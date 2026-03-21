"""
Programme upload and management routes.

POST /api/programmes/upload    — receive CSV/XLSX/XLSM, return 202, run pipeline in background
GET  /api/programmes/{project_id}             — list versions for project
GET  /api/programmes/{upload_id}/status       — poll processing status
GET  /api/programmes/{upload_id}/activities   — activity tree
GET  /api/programmes/{upload_id}/diff         — diff vs previous version
"""

from __future__ import annotations

import asyncio
import uuid
import logging
from typing import Any
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import normalize_role, require_role
from ...crud.site_project import check_sub_project_access
from ...models.programme import ActivityAssetMapping, AISuggestionLog, ProgrammeActivity, ProgrammeUpload
from ...models.site_project import SiteProject
from ...models.stored_file import StoredFile
from ...models.subcontractor import Subcontractor
from ...models.user import User
from ...schemas.programme import (
    ActivityMappingResponse,
    MappingCorrectionRequest,
    ProgrammeActivityItem,
    ProgrammeDiff,
    ProgrammeUploadAccepted,
    ProgrammeUploadStatus,
    ProgrammeVersionSummary,
)
from ...schemas.enums import UserRole
from ...utils.storage import storage
from ...services.process_programme import preflight_validate, process_programme

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/programmes", tags=["Programmes"])

ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/csv",
    "text/plain",  # some CSV uploads come through as text/plain
    "application/pdf",
}
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xlsm", ".pdf"}


def _check_project_access(project_id: UUID, current_user: User, db: Session) -> SiteProject:
    project = db.query(SiteProject).filter(SiteProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    role = normalize_role(getattr(current_user, "role", ""))
    is_admin = role == UserRole.ADMIN.value
    is_project_manager = any(str(m.id) == str(current_user.id) for m in project.managers)

    if not is_admin and not is_project_manager:
        raise HTTPException(status_code=403, detail="You don't have access to this project")

    return project


def _serialize_mapping(
    mapping: ActivityAssetMapping,
    activity_name: str | None,
) -> ActivityMappingResponse:
    return ActivityMappingResponse(
        id=mapping.id,
        programme_activity_id=mapping.programme_activity_id,
        activity_name=activity_name,
        asset_type=mapping.asset_type,
        confidence=mapping.confidence,
        source=mapping.source,
        auto_committed=mapping.auto_committed,
        manually_corrected=mapping.manually_corrected,
        corrected_by=mapping.corrected_by,
        corrected_at=mapping.corrected_at,
        subcontractor_id=mapping.subcontractor_id,
        created_at=mapping.created_at,
    )


# ---------------------------------------------------------------------------
# POST /api/programmes/upload
# ---------------------------------------------------------------------------

@router.post("/upload", status_code=status.HTTP_202_ACCEPTED, response_model=ProgrammeUploadAccepted)
async def upload_programme(
    background_tasks: BackgroundTasks,
    project_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> ProgrammeUploadAccepted:
    """
    Accept a CSV, XLSX, XLSM, or PDF programme file. Returns 202 immediately.
    Processing (AI structure detection → activity import) runs in background.
    Poll GET /api/programmes/{upload_id}/status for completion.
    """
    # Validate file extension and run preflight before acquiring a DB connection.
    # This way a bad/unsupported file fails fast without holding a pool connection
    # during the (potentially slow) parse step.
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Upload a CSV, XLSX, XLSM, or PDF file.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    loop = asyncio.get_event_loop()
    parse_error = await loop.run_in_executor(None, preflight_validate, file_bytes, filename)
    if parse_error:
        raise HTTPException(status_code=422, detail=f"File cannot be parsed: {parse_error}")

    # File is valid — now check project access (acquires DB connection).
    _check_project_access(project_id, current_user, db)

    try:
        storage_path = await storage.save(file_bytes, filename)
    except Exception as exc:
        logger.exception("Failed to save programme file")
        raise HTTPException(status_code=500, detail="File storage failed.") from exc

    # Create StoredFile record
    stored_file = StoredFile(
        id=uuid.uuid4(),
        original_filename=filename,
        storage_backend=storage.BACKEND_NAME,
        storage_path=storage_path,
        content_type=file.content_type or "application/octet-stream",
        file_size=len(file_bytes),
        uploaded_by_id=current_user.id,
    )
    try:
        db.add(stored_file)
        db.flush()

        # Lock project row to serialize per-project version allocation.
        db.query(SiteProject).filter(SiteProject.id == project_id).with_for_update().first()
        latest_version = (
            db.query(func.max(ProgrammeUpload.version_number))
            .filter(ProgrammeUpload.project_id == project_id)
            .scalar()
        )
        version_number = int(latest_version or 0) + 1

        # Create ProgrammeUpload record (status=processing)
        upload = ProgrammeUpload(
            id=uuid.uuid4(),
            project_id=project_id,
            uploaded_by=current_user.id,
            file_id=stored_file.id,
            file_name=filename,
            version_number=version_number,
            status="processing",
        )
        db.add(upload)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        try:
            storage.delete(stored_file.storage_path)
        except Exception as delete_exc:
            logger.error(
                "Failed to delete orphaned programme blob after IntegrityError path=%s err=%s",
                stored_file.storage_path,
                delete_exc,
            )
        raise HTTPException(
            status_code=409,
            detail="Concurrent upload detected. Please retry.",
        ) from exc
    except OperationalError as exc:
        # Transient DB disconnect (e.g. stale connection after slow preflight).
        # pool_pre_ping can't protect an already-checked-out connection.
        db.rollback()
        try:
            storage.delete(stored_file.storage_path)
        except Exception as delete_exc:
            logger.error(
                "Failed to delete orphaned programme blob after OperationalError path=%s err=%s",
                stored_file.storage_path,
                delete_exc,
            )
        logger.error("DB operational error persisting upload metadata: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Database temporarily unavailable. Please retry the upload.",
        ) from exc
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(stored_file.storage_path)
        except Exception as delete_exc:
            logger.error(
                "Failed to delete orphaned programme blob after DB error path=%s err=%s",
                stored_file.storage_path,
                delete_exc,
            )
        raise HTTPException(status_code=500, detail="Failed to persist upload metadata.") from exc

    upload_id = str(upload.id)

    # Enqueue background pipeline — returns immediately
    background_tasks.add_task(process_programme, upload_id)

    logger.info(
        "Programme upload queued: upload_id=%s project=%s user=%s file=%s",
        upload_id, project_id, current_user.id, filename,
    )

    return ProgrammeUploadAccepted(
        upload_id=upload_id,
        status="processing",
        message="Programme upload accepted. Poll /status for progress.",
    )


# ---------------------------------------------------------------------------
# GET /api/programmes/{upload_id}/status
# ---------------------------------------------------------------------------

@router.get("/{upload_id}/status", response_model=ProgrammeUploadStatus)
def get_upload_status(
    upload_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> ProgrammeUploadStatus:
    """Poll processing status. Returns committed once the pipeline completes."""
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    _check_project_access(upload.project_id, current_user, db)

    return ProgrammeUploadStatus(
        upload_id=upload.id,
        status=upload.status,
        completeness_score=round((upload.completeness_score or 0.0) * 100),
        completeness_notes=upload.completeness_notes,
        version_number=upload.version_number,
        file_name=upload.file_name,
        created_at=upload.created_at.isoformat() if upload.created_at else None,
    )


# ---------------------------------------------------------------------------
# GET /api/programmes/{project_id}  — list versions
# ---------------------------------------------------------------------------

@router.get("/{project_id}", response_model=list[ProgrammeVersionSummary])
def list_programme_versions(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> list[ProgrammeVersionSummary]:
    """List all programme versions for a project, newest first."""
    _check_project_access(project_id, current_user, db)

    uploads = (
        db.query(ProgrammeUpload)
        .filter(ProgrammeUpload.project_id == project_id)
        .order_by(ProgrammeUpload.version_number.desc())
        .all()
    )

    return [
        ProgrammeVersionSummary(
            upload_id=u.id,
            version_number=u.version_number,
            file_name=u.file_name,
            status=u.status,
            completeness_score=round((u.completeness_score or 0.0) * 100),
            created_at=u.created_at.isoformat() if u.created_at else None,
        )
        for u in uploads
    ]


# ---------------------------------------------------------------------------
# GET /api/programmes/{upload_id}/activities
# ---------------------------------------------------------------------------

@router.get("/{upload_id}/activities", response_model=list[ProgrammeActivityItem])
def get_activities(
    upload_id: UUID,
    subcontractor_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_entity: User | Subcontractor = Depends(
        require_role([UserRole.MANAGER, UserRole.ADMIN, UserRole.SUBCONTRACTOR])
    ),
) -> list[ProgrammeActivityItem]:
    """
    Return activities for a programme version.

    - Managers/admins: full activity list, or filtered by subcontractor_id when provided.
    - Subcontractors: must provide subcontractor_id matching their own identity.
    """
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.status != "committed":
        if upload.status in {"degraded", "failed"}:
            raise HTTPException(status_code=409, detail="Programme processing did not complete successfully.")
        raise HTTPException(status_code=409, detail="Programme is still processing.")

    role = normalize_role(getattr(current_entity, "role", "subcontractor"))
    is_subcontractor = role == UserRole.SUBCONTRACTOR.value or isinstance(current_entity, Subcontractor)

    if is_subcontractor:
        if subcontractor_id is None:
            raise HTTPException(status_code=400, detail="subcontractor_id is required for subcontractor access")

        if str(current_entity.id) != str(subcontractor_id):
            raise HTTPException(status_code=403, detail="You can only view your own assigned activities")

        project = db.query(SiteProject).filter(SiteProject.id == upload.project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        check_sub_project_access(db, current_entity, project)

        activities = (
            db.query(ProgrammeActivity)
            .join(ActivityAssetMapping, ActivityAssetMapping.programme_activity_id == ProgrammeActivity.id)
            .filter(
                ProgrammeActivity.programme_upload_id == upload_id,
                ActivityAssetMapping.subcontractor_id == subcontractor_id,
            )
            .distinct()
            .order_by(ProgrammeActivity.sort_order)
            .all()
        )
    else:
        _check_project_access(upload.project_id, current_entity, db)

        activities_query = db.query(ProgrammeActivity).filter(ProgrammeActivity.programme_upload_id == upload_id)
        if subcontractor_id is not None:
            activities_query = (
                activities_query
                .join(ActivityAssetMapping, ActivityAssetMapping.programme_activity_id == ProgrammeActivity.id)
                .filter(ActivityAssetMapping.subcontractor_id == subcontractor_id)
                .distinct()
            )

        activities = activities_query.order_by(ProgrammeActivity.sort_order).all()

    return [
        {
            "id": str(a.id),
            "parent_id": str(a.parent_id) if a.parent_id else None,
            "name": a.name,
            "start_date": a.start_date.isoformat() if a.start_date else None,
            "end_date": a.end_date.isoformat() if a.end_date else None,
            "duration_days": a.duration_days,
            "level_name": a.level_name,
            "zone_name": a.zone_name,
            "is_summary": a.is_summary,
            "wbs_code": a.wbs_code,
            "sort_order": a.sort_order,
            "import_flags": a.import_flags or [],
        }
        for a in activities
    ]


# ---------------------------------------------------------------------------
# GET /api/programmes/{upload_id}/diff
# ---------------------------------------------------------------------------

@router.get("/{upload_id}/diff", response_model=ProgrammeDiff)
def get_programme_diff(
    upload_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> ProgrammeDiff:
    """
    Diff this version against the previous version for the same project.
    Returns activity count delta and date shift summary.
    Anomaly flags (if any) are stored on lookahead_snapshots — not here.
    """
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.status != "committed":
        if upload.status in {"degraded", "failed"}:
            raise HTTPException(status_code=409, detail="Programme processing did not complete successfully.")
        raise HTTPException(status_code=409, detail="Programme is still processing.")

    _check_project_access(upload.project_id, current_user, db)

    # Find previous version
    previous = (
        db.query(ProgrammeUpload)
        .filter(
            ProgrammeUpload.project_id == upload.project_id,
            ProgrammeUpload.version_number < upload.version_number,
            ProgrammeUpload.status == "committed",
        )
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )

    current_count = (
        db.query(ProgrammeActivity)
        .filter(ProgrammeActivity.programme_upload_id == upload_id)
        .count()
    )

    if not previous:
        return ProgrammeDiff(
            upload_id=upload_id,
            version_number=upload.version_number,
            previous_version=None,
            activity_count=current_count,
            activity_delta=None,
            summary="No previous version to compare.",
        )

    previous_count = (
        db.query(ProgrammeActivity)
        .filter(ProgrammeActivity.programme_upload_id == previous.id)
        .count()
    )
    delta = current_count - previous_count

    return ProgrammeDiff(
        upload_id=upload_id,
        version_number=upload.version_number,
        previous_version=previous.version_number,
        activity_count=current_count,
        previous_activity_count=previous_count,
        activity_delta=delta,
        summary=f"{'+' if delta >= 0 else ''}{delta} activities vs previous version.",
    )


# ---------------------------------------------------------------------------
# DELETE /api/programmes/{upload_id}
# ---------------------------------------------------------------------------

@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_programme_upload(
    upload_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> None:
    """
    Hard-delete a programme upload and all cascaded data (activities, mappings,
    ai_suggestion_logs, lookahead_snapshots).
    Returns 409 if the upload is still processing.
    """
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    _check_project_access(upload.project_id, current_user, db)

    if upload.status == "processing":
        raise HTTPException(
            status_code=409,
            detail="Cannot delete an upload that is still processing.",
        )

    stored_file = upload.file
    storage_path = stored_file.storage_path if stored_file else None

    # Attempt blob deletion before committing the DB removal so an orphaned
    # blob is never silently left behind after a successful DB commit.
    if storage_path:
        try:
            deleted = storage.delete(storage_path)
            if deleted is False:
                logger.warning(
                    "Blob deletion returned False for upload %s at path %s",
                    upload_id,
                    storage_path,
                )
        except Exception:
            logger.warning(
                "Could not delete blob for upload %s at path %s — proceeding with DB removal",
                upload_id,
                storage_path,
            )

    db.delete(upload)
    db.flush()
    if stored_file:
        db.delete(stored_file)
    db.commit()


# ---------------------------------------------------------------------------
# GET /api/programmes/{upload_id}/mappings
# ---------------------------------------------------------------------------

@router.get("/{upload_id}/mappings", response_model=list[ActivityMappingResponse])
def get_activity_mappings(
    upload_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> list[ActivityMappingResponse]:
    """Return all activity mappings for a programme upload."""
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    _check_project_access(upload.project_id, current_user, db)

    rows = (
        db.query(ActivityAssetMapping, ProgrammeActivity.name)
        .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
        .filter(ProgrammeActivity.programme_upload_id == upload_id)
        .order_by(ProgrammeActivity.sort_order.asc().nulls_last(), ActivityAssetMapping.created_at.asc())
        .all()
    )
    return [_serialize_mapping(mapping, activity_name) for mapping, activity_name in rows]


# ---------------------------------------------------------------------------
# GET /api/programmes/{upload_id}/mappings/unclassified
# ---------------------------------------------------------------------------

@router.get("/{upload_id}/mappings/unclassified", response_model=list[ActivityMappingResponse])
def get_unclassified_activity_mappings(
    upload_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> list[ActivityMappingResponse]:
    """Return low-confidence or unresolved mapping rows for UI badge counts and triage."""
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    _check_project_access(upload.project_id, current_user, db)

    rows = (
        db.query(ActivityAssetMapping, ProgrammeActivity.name)
        .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
        .filter(
            ProgrammeActivity.programme_upload_id == upload_id,
            ActivityAssetMapping.confidence == "low",
            ActivityAssetMapping.manually_corrected.is_(False),
        )
        .order_by(ProgrammeActivity.sort_order.asc().nulls_last(), ActivityAssetMapping.created_at.asc())
        .all()
    )
    return [_serialize_mapping(mapping, activity_name) for mapping, activity_name in rows]


# ---------------------------------------------------------------------------
# PATCH /api/programmes/mappings/{mapping_id}
# ---------------------------------------------------------------------------

@router.patch("/mappings/{mapping_id}", response_model=ActivityMappingResponse)
def correct_activity_mapping(
    mapping_id: UUID,
    payload: MappingCorrectionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> ActivityMappingResponse:
    """
    Apply a PM correction to an activity mapping.

    Expected JSON body:
      {"asset_type": "telehandler"}
    """
    new_asset_type = payload.asset_type

    mapping = db.query(ActivityAssetMapping).filter(ActivityAssetMapping.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")

    activity = db.query(ProgrammeActivity).filter(ProgrammeActivity.id == mapping.programme_activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Mapped activity not found")

    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == activity.programme_upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    _check_project_access(upload.project_id, current_user, db)

    previous_suggestion = mapping.asset_type

    mapping.asset_type = new_asset_type
    mapping.source = "manual"
    mapping.manually_corrected = True
    mapping.corrected_by = current_user.id
    mapping.corrected_at = datetime.now(timezone.utc)
    mapping.auto_committed = False

    correction_log = AISuggestionLog(
        id=uuid.uuid4(),
        activity_id=mapping.programme_activity_id,
        suggested_asset_type=previous_suggestion,
        confidence=mapping.confidence,
        accepted=False,
        correction=new_asset_type,
    )
    db.add(correction_log)
    db.commit()
    db.refresh(mapping)

    return _serialize_mapping(mapping, activity.name)
