"""
Programme upload and management routes.

POST /api/programmes/upload    — receive CSV/XLSX, return 202, run pipeline in background
GET  /api/programmes/{project_id}             — list versions for project
GET  /api/programmes/{upload_id}/status       — poll processing status
GET  /api/programmes/{upload_id}/activities   — activity tree
GET  /api/programmes/{upload_id}/diff         — diff vs previous version
"""

from __future__ import annotations

import uuid
import logging
from typing import Any
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import require_role
from ...models.programme import ActivityAssetMapping, AISuggestionLog, ProgrammeActivity, ProgrammeUpload
from ...models.site_project import SiteProject
from ...models.stored_file import StoredFile
from ...models.user import User
from ...schemas.programme import ActivityMappingResponse, MappingCorrectionRequest
from ...schemas.enums import UserRole
from ...utils.storage import storage
from ...services.process_programme import process_programme

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/programmes", tags=["Programmes"])

ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/csv",
    "text/plain",  # some CSV uploads come through as text/plain
}
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}


def _normalize_role(role: Any) -> str:
    if hasattr(role, "value"):
        return str(role.value).strip().lower()
    return str(role).strip().lower()


def _check_project_access(project_id: UUID, current_user: User, db: Session) -> SiteProject:
    project = db.query(SiteProject).filter(SiteProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    role = _normalize_role(getattr(current_user, "role", ""))
    is_manager = role in ("manager", "admin")
    is_project_manager = any(str(m.id) == str(current_user.id) for m in project.managers)

    if not is_manager and not is_project_manager:
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

@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_programme(
    background_tasks: BackgroundTasks,
    project_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> dict[str, Any]:
    """
    Accept a CSV or XLSX programme file. Returns 202 immediately.
    Processing (AI structure detection → activity import) runs in background.
    Poll GET /api/programmes/{upload_id}/status for completion.
    """
    _check_project_access(project_id, current_user, db)

    # Validate file extension
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Upload a CSV, XLSX, or XLSM file.",
        )

    # Read and store via storage backend (same pattern as site_plans)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        storage_path = await storage.save(file_bytes, filename)
    except Exception as exc:
        logger.error("Failed to save programme file: %s", exc)
        raise HTTPException(status_code=500, detail="File storage failed.")

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
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Concurrent upload detected. Please retry.",
        )

    upload_id = str(upload.id)

    # Enqueue background pipeline — returns immediately
    background_tasks.add_task(process_programme, upload_id)

    logger.info(
        "Programme upload queued: upload_id=%s project=%s user=%s file=%s",
        upload_id, project_id, current_user.id, filename,
    )

    return {
        "upload_id": upload_id,
        "status": "processing",
        "message": "Programme upload accepted. Poll /status for progress.",
    }


# ---------------------------------------------------------------------------
# GET /api/programmes/{upload_id}/status
# ---------------------------------------------------------------------------

@router.get("/{upload_id}/status")
def get_upload_status(
    upload_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> dict[str, Any]:
    """Poll processing status. Returns committed once the pipeline completes."""
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    _check_project_access(upload.project_id, current_user, db)

    return {
        "upload_id": str(upload.id),
        "status": upload.status,
        "completeness_score": round((upload.completeness_score or 0.0) * 100),  # 0–100 for display
        "completeness_notes": upload.completeness_notes,
        "version_number": upload.version_number,
        "file_name": upload.file_name,
        "created_at": upload.created_at.isoformat() if upload.created_at else None,
    }


# ---------------------------------------------------------------------------
# GET /api/programmes/{project_id}  — list versions
# ---------------------------------------------------------------------------

@router.get("/{project_id}")
def list_programme_versions(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> list[dict[str, Any]]:
    """List all programme versions for a project, newest first."""
    _check_project_access(project_id, current_user, db)

    uploads = (
        db.query(ProgrammeUpload)
        .filter(ProgrammeUpload.project_id == project_id)
        .order_by(ProgrammeUpload.version_number.desc())
        .all()
    )

    return [
        {
            "upload_id": str(u.id),
            "version_number": u.version_number,
            "file_name": u.file_name,
            "status": u.status,
            "completeness_score": round((u.completeness_score or 0.0) * 100),
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in uploads
    ]


# ---------------------------------------------------------------------------
# GET /api/programmes/{upload_id}/activities
# ---------------------------------------------------------------------------

@router.get("/{upload_id}/activities")
def get_activities(
    upload_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> list[dict[str, Any]]:
    """Return all activities for a programme version, ordered by sort_order."""
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.status != "committed":
        raise HTTPException(status_code=409, detail="Programme is still processing.")

    _check_project_access(upload.project_id, current_user, db)

    activities = (
        db.query(ProgrammeActivity)
        .filter(ProgrammeActivity.programme_upload_id == upload_id)
        .order_by(ProgrammeActivity.sort_order)
        .all()
    )

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

@router.get("/{upload_id}/diff")
def get_programme_diff(
    upload_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> dict[str, Any]:
    """
    Diff this version against the previous version for the same project.
    Returns activity count delta and date shift summary.
    Anomaly flags (if any) are stored on lookahead_snapshots — not here.
    """
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.status != "committed":
        raise HTTPException(status_code=409, detail="Programme is still processing.")

    _check_project_access(upload.project_id, current_user, db)

    # Find previous version
    previous = (
        db.query(ProgrammeUpload)
        .filter(
            ProgrammeUpload.project_id == upload.project_id,
            ProgrammeUpload.version_number == upload.version_number - 1,
            ProgrammeUpload.status == "committed",
        )
        .first()
    )

    current_count = (
        db.query(ProgrammeActivity)
        .filter(ProgrammeActivity.programme_upload_id == upload_id)
        .count()
    )

    if not previous:
        return {
            "upload_id": str(upload_id),
            "version_number": upload.version_number,
            "previous_version": None,
            "activity_count": current_count,
            "activity_delta": None,
            "summary": "No previous version to compare.",
        }

    previous_count = (
        db.query(ProgrammeActivity)
        .filter(ProgrammeActivity.programme_upload_id == previous.id)
        .count()
    )
    delta = current_count - previous_count

    return {
        "upload_id": str(upload_id),
        "version_number": upload.version_number,
        "previous_version": upload.version_number - 1,
        "activity_count": current_count,
        "previous_activity_count": previous_count,
        "activity_delta": delta,
        "summary": f"{'+' if delta >= 0 else ''}{delta} activities vs previous version.",
    }


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

@router.patch("/mappings/{mapping_id}")
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
