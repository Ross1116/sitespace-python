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
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import normalize_role, require_role
from ...crud import asset as asset_crud
from ...crud import slot_booking as booking_crud
from ...crud.site_project import check_sub_project_access
from ...models.asset import Asset
from ...models.programme import (
    ActivityAssetMapping,
    ActivityBookingGroup,
    AISuggestionLog,
    ProgrammeActivity,
    ProgrammeUpload,
)
from ...models.site_project import SiteProject
from ...models.slot_booking import SlotBooking
from ...models.stored_file import StoredFile
from ...models.subcontractor import Subcontractor
from ...models.user import User
from ...models.work_profile import ActivityWorkProfile
from ...schemas.asset import AssetAvailabilityCheck
from ...schemas.programme import (
    ActivityBookingContextAssetCandidate,
    ActivityBookingGroupSummary,
    ActivityMappingResponse,
    LinkedBookingGroupSummary,
    MappingCorrectionRequest,
    ProgrammeActivityItem,
    ProgrammeActivityBookingContextResponse,
    ProgrammeActivitySuggestedBookingDate,
    ProgrammeDiff,
    ProgrammeUploadAccepted,
    ProgrammeUploadStatus,
    ProgrammeVersionSummary,
)
from ...schemas.enums import BookingStatus, UserRole
from ...utils.storage import storage
from ...utils.programme_notes import normalize_programme_completeness_notes
from ...services.metadata_confidence_service import asset_is_planning_ready
from ...services.lookahead_engine import resolve_activity_distribution
from ...services.process_programme import preflight_validate, process_programme
from ...services.programme_upload_service import (
    get_active_programme_upload,
    get_previous_successful_programme_upload,
    get_upload_status as get_normalized_upload_status,
    is_upload_processing,
    is_upload_readable,
    is_upload_terminal_success,
    upload_has_warnings,
)
from ...services.correction_service import (
    MappingCorrectionValidationError,
    apply_mapping_correction,
    load_mapping_correction_context,
)

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
    item_id: UUID | None,
) -> ActivityMappingResponse:
    return ActivityMappingResponse(
        id=mapping.id,
        programme_activity_id=mapping.programme_activity_id,
        item_id=item_id,
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


def _normalize_completeness_notes(notes: dict | None) -> dict:
    return normalize_programme_completeness_notes(notes)


def _serialize_programme_upload(
    upload: ProgrammeUpload,
    *,
    active_upload_id: UUID | None = None,
    include_notes: bool = False,
) -> dict[str, object]:
    normalized_status = get_normalized_upload_status(upload)
    payload: dict[str, object] = {
        "upload_id": upload.id,
        "status": normalized_status,
        "processing_outcome": upload.processing_outcome,
        "is_active_version": bool(active_upload_id and str(upload.id) == str(active_upload_id)),
        "is_terminal_success": is_upload_terminal_success(upload),
        "has_warnings": upload_has_warnings(upload),
        "completeness_score": (
            None if upload.completeness_score is None else round(upload.completeness_score * 100)
        ),
        "version_number": upload.version_number,
        "file_name": upload.file_name,
        "ai_tokens_used": upload.ai_tokens_used,
        "ai_cost_usd": None if upload.ai_cost_usd is None else float(upload.ai_cost_usd),
        "created_at": upload.created_at.isoformat() if upload.created_at else None,
    }
    if include_notes:
        payload["completeness_notes"] = _normalize_completeness_notes(upload.completeness_notes)
    return payload


def _require_readable_upload(upload: ProgrammeUpload) -> None:
    if is_upload_readable(upload):
        return
    if is_upload_processing(upload):
        raise HTTPException(status_code=409, detail="Programme is still processing.")
    raise HTTPException(status_code=409, detail="Programme processing did not complete successfully.")


def _normalize_week_start(selected_week_start: date | None) -> date | None:
    if selected_week_start is None:
        return None
    return selected_week_start - timedelta(days=selected_week_start.weekday())


def _resolve_work_days_per_week(upload: ProgrammeUpload) -> int:
    raw_wdpw = upload.work_days_per_week
    if raw_wdpw and 1 <= raw_wdpw <= 7:
        return raw_wdpw
    return 5


def _iter_working_dates(start_date: date | None, end_date: date | None, work_days_per_week: int) -> list[date]:
    if start_date is None or end_date is None:
        return []
    span_start = min(start_date, end_date)
    span_end = max(start_date, end_date)
    days: list[date] = []
    current = span_start
    while current <= span_end:
        if current.weekday() < work_days_per_week:
            days.append(current)
        current += timedelta(days=1)
    return days


def _hours_between_times(start_time: time | None, end_time: time | None) -> float:
    if start_time is None or end_time is None:
        return 0.0
    start_dt = datetime.combine(date.min, start_time)
    end_dt = datetime.combine(date.min, end_time)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return max((end_dt - start_dt).total_seconds() / 3600.0, 0.0)


def _is_active_linked_booking(booking) -> bool:
    status = getattr(booking, "status", None)
    if status is None:
        return False
    if hasattr(status, "value"):
        status_value = str(status.value).strip().lower()
    else:
        status_value = str(status).strip().lower()
    return status_value not in {
        BookingStatus.CANCELLED.value,
        BookingStatus.DENIED.value,
    }


def _isoformat_datetime(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    return None


def _build_suggested_booking_dates(
    *,
    effective_week_start: date | None,
    distribution_result: dict[str, object] | None,
    linked_bookings: list,
    default_start_time: str,
    default_end_time: str,
) -> list[ProgrammeActivitySuggestedBookingDate]:
    if effective_week_start is None or distribution_result is None:
        return []

    week_end = effective_week_start + timedelta(days=6)
    booked_hours_by_date: dict[str, float] = {}
    for booking in linked_bookings:
        if not _is_active_linked_booking(booking):
            continue
        booking_date = getattr(booking, "booking_date", None)
        if booking_date is None:
            continue
        booking_date_key = booking_date.isoformat() if hasattr(booking_date, "isoformat") else str(booking_date)
        booked_hours_by_date[booking_date_key] = round(
            booked_hours_by_date.get(booking_date_key, 0.0)
            + _hours_between_times(getattr(booking, "start_time", None), getattr(booking, "end_time", None)),
            4,
        )

    suggestions: list[ProgrammeActivitySuggestedBookingDate] = []
    for working_date, demand_hours in zip(
        distribution_result["work_dates"],
        distribution_result["distribution"],
        strict=True,
    ):
        if not isinstance(working_date, date):
            continue
        if not (effective_week_start <= working_date <= week_end):
            continue

        date_key = working_date.isoformat()
        demand_value = round(float(demand_hours), 4)
        booked_value = round(booked_hours_by_date.get(date_key, 0.0), 4)
        gap_value = round(max(demand_value - booked_value, 0.0), 4)
        suggestions.append(
            ProgrammeActivitySuggestedBookingDate(
                date=date_key,
                start_time=default_start_time,
                end_time=default_end_time,
                hours=gap_value,
                demand_hours=demand_value,
                booked_hours=booked_value,
                gap_hours=gap_value,
            )
        )

    return suggestions


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
            deleted = storage.delete(stored_file.storage_path)
            if deleted is False:
                logger.error(
                    "Blob deletion returned False (orphaned) after IntegrityError path=%s",
                    stored_file.storage_path,
                )
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
            deleted = storage.delete(stored_file.storage_path)
            if deleted is False:
                logger.error(
                    "Blob deletion returned False (orphaned) after OperationalError path=%s",
                    stored_file.storage_path,
                )
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
            deleted = storage.delete(stored_file.storage_path)
            if deleted is False:
                logger.error(
                    "Blob deletion returned False (orphaned) after DB error path=%s",
                    stored_file.storage_path,
                )
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
        upload_id=upload.id,
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
    active_upload = get_active_programme_upload(upload.project_id, db)

    return ProgrammeUploadStatus(
        **_serialize_programme_upload(
            upload,
            active_upload_id=getattr(active_upload, "id", None),
            include_notes=True,
        )
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
    active_upload = get_active_programme_upload(project_id, db)

    return [
        ProgrammeVersionSummary(
            **_serialize_programme_upload(u, active_upload_id=getattr(active_upload, "id", None))
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
    _require_readable_upload(upload)

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
        subcontractor = db.query(Subcontractor).filter(Subcontractor.id == subcontractor_id).first()
        if not subcontractor:
            raise HTTPException(status_code=404, detail="Subcontractor not found")
        if not check_sub_project_access(db, subcontractor, project):
            raise HTTPException(status_code=403, detail="You are not assigned to this project")

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
        ProgrammeActivityItem(
            id=str(a.id),
            parent_id=str(a.parent_id) if a.parent_id else None,
            name=a.name,
            start_date=a.start_date.isoformat() if a.start_date else None,
            end_date=a.end_date.isoformat() if a.end_date else None,
            duration_days=a.duration_days,
            level_name=a.level_name,
            zone_name=a.zone_name,
            is_summary=a.is_summary,
            wbs_code=a.wbs_code,
            sort_order=a.sort_order,
            import_flags=a.import_flags or [],
            pct_complete=a.pct_complete,
            activity_kind=a.activity_kind,
            row_confidence=a.row_confidence,
            item_id=a.item_id,
        )
        for a in activities
    ]


# ---------------------------------------------------------------------------
# GET /api/programmes/activities/{activity_id}/booking-context
# ---------------------------------------------------------------------------

@router.get(
    "/activities/{activity_id}/booking-context",
    response_model=ProgrammeActivityBookingContextResponse,
)
def get_activity_booking_context(
    activity_id: UUID,
    selected_week_start: date | None = None,
    db: Session = Depends(get_db),
    current_entity: User | Subcontractor = Depends(
        require_role([UserRole.MANAGER, UserRole.ADMIN, UserRole.SUBCONTRACTOR])
    ),
) -> ProgrammeActivityBookingContextResponse:
    activity = db.query(ProgrammeActivity).filter(ProgrammeActivity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == activity.programme_upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    _require_readable_upload(upload)

    role = normalize_role(getattr(current_entity, "role", "subcontractor"))
    is_subcontractor = role == UserRole.SUBCONTRACTOR.value or isinstance(current_entity, Subcontractor)

    if is_subcontractor:
        project = db.query(SiteProject).filter(SiteProject.id == upload.project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        subcontractor = db.query(Subcontractor).filter(Subcontractor.id == current_entity.id).first()
        if not subcontractor or not check_sub_project_access(db, subcontractor, project):
            raise HTTPException(status_code=403, detail="You are not assigned to this project")

        has_activity_access = (
            db.query(ActivityAssetMapping.id)
            .filter(
                ActivityAssetMapping.programme_activity_id == activity_id,
                ActivityAssetMapping.subcontractor_id == current_entity.id,
            )
            .first()
            is not None
        )
        if not has_activity_access:
            raise HTTPException(status_code=403, detail="You can only view your own assigned activities")
    else:
        _check_project_access(upload.project_id, current_entity, db)

    mapping = (
        db.query(ActivityAssetMapping)
        .filter(
            ActivityAssetMapping.programme_activity_id == activity_id,
            ActivityAssetMapping.asset_type.isnot(None),
        )
        .order_by(
            ActivityAssetMapping.manually_corrected.desc(),
            ActivityAssetMapping.corrected_at.desc().nulls_last(),
            ActivityAssetMapping.created_at.desc(),
        )
        .first()
    )
    if mapping is None or not mapping.asset_type:
        raise HTTPException(status_code=409, detail="Activity does not have a resolved asset type.")

    profile = (
        db.query(ActivityWorkProfile)
        .filter(ActivityWorkProfile.activity_id == activity_id)
        .one_or_none()
    )

    requested_week_start = _normalize_week_start(selected_week_start)
    booking_group = (
        db.query(ActivityBookingGroup)
        .filter(ActivityBookingGroup.programme_activity_id == activity_id)
        .first()
    )

    working_dates = _iter_working_dates(
        activity.start_date,
        activity.end_date,
        _resolve_work_days_per_week(upload),
    )
    default_week_start = (
        requested_week_start
        or _normalize_week_start(booking_group.selected_week_start if booking_group else None)
        or _normalize_week_start(activity.start_date)
    )

    if default_week_start is not None:
        week_end = default_week_start + timedelta(days=6)
        suggested_work_dates = [
            working_date
            for working_date in working_dates
            if default_week_start <= working_date <= week_end
        ]
        default_date = suggested_work_dates[0] if suggested_work_dates else activity.start_date
    else:
        suggested_work_dates = []
        default_date = activity.start_date

    linked_booking_ids = []
    if booking_group is not None:
        linked_booking_ids = [
            row[0]
            for row in (
                db.query(SlotBooking.id)
                .filter(SlotBooking.booking_group_id == booking_group.id)
                .order_by(SlotBooking.booking_date.asc(), SlotBooking.start_time.asc())
                .all()
            )
        ]

    linked_bookings = [
        booking_crud.get_booking_detail(db, booking_id)
        for booking_id in linked_booking_ids
    ]
    linked_bookings = [booking for booking in linked_bookings if booking is not None]

    distribution_result = resolve_activity_distribution(
        db,
        mapping=mapping,
        activity=activity,
        upload=upload,
        profile=profile,
    )

    default_start_time = "08:00"
    default_end_time = "16:00"
    suggested_bulk_dates = _build_suggested_booking_dates(
        effective_week_start=default_week_start,
        distribution_result=distribution_result,
        linked_bookings=linked_bookings,
        default_start_time=default_start_time,
        default_end_time=default_end_time,
    )

    total_booked_hours = round(
        sum(
            _hours_between_times(
                getattr(booking, "start_time", None),
                getattr(booking, "end_time", None),
            )
            for booking in linked_bookings
            if _is_active_linked_booking(booking)
        ),
        2,
    )
    last_booking_at = max(
        (
            value
            for booking in linked_bookings
            for value in (
                _isoformat_datetime(getattr(booking, "updated_at", None)),
                _isoformat_datetime(getattr(booking, "created_at", None)),
            )
            if value
        ),
        default=None,
    )

    candidate_assets_query = (
        db.query(Asset)
        .filter(
            Asset.project_id == upload.project_id,
            func.lower(func.coalesce(Asset.canonical_type, "")) == str(mapping.asset_type).strip().lower(),
        )
        .order_by(Asset.name.asc())
    )
    candidate_assets: list[ActivityBookingContextAssetCandidate] = []
    for asset in candidate_assets_query.all():
        if default_date is not None:
            availability = asset_crud.check_asset_availability(
                db,
                AssetAvailabilityCheck(
                    asset_id=asset.id,
                    date=default_date,
                    start_time="08:00",
                    end_time="16:00",
                ),
            )
            is_available = bool(availability.is_available)
            availability_reason = availability.reason
        else:
            is_available = False
            availability_reason = "No default date available"

        candidate_assets.append(
            ActivityBookingContextAssetCandidate(
                id=asset.id,
                asset_code=asset.asset_code,
                name=asset.name,
                type=asset.type,
                canonical_type=asset.canonical_type,
                status=asset.status.value if asset.status else "unknown",
                planning_ready=asset_is_planning_ready(asset),
                is_available=is_available,
                availability_reason=availability_reason,
            )
        )

    candidate_assets.sort(key=lambda asset: (not asset.is_available, asset.name.lower(), str(asset.id)))

    return ProgrammeActivityBookingContextResponse(
        activity_id=activity.id,
        programme_upload_id=upload.id,
        project_id=upload.project_id,
        activity_name=activity.name,
        start_date=activity.start_date.isoformat() if activity.start_date else None,
        end_date=activity.end_date.isoformat() if activity.end_date else None,
        level_name=activity.level_name,
        zone_name=activity.zone_name,
        expected_asset_type=str(mapping.asset_type).strip().lower(),
        selected_week_start=default_week_start.isoformat() if default_week_start else None,
        default_week_start=default_week_start.isoformat() if default_week_start else None,
        default_date=default_date.isoformat() if default_date else None,
        default_booking_date=default_date.isoformat() if default_date else None,
        default_start_time=default_start_time,
        default_end_time=default_end_time,
        suggested_bulk_dates=suggested_bulk_dates,
        booking_group=(
            ActivityBookingGroupSummary(
                id=booking_group.id,
                programme_activity_id=booking_group.programme_activity_id,
                expected_asset_type=booking_group.expected_asset_type,
                selected_week_start=(
                    booking_group.selected_week_start.isoformat()
                    if booking_group.selected_week_start
                    else None
                ),
                origin_source=booking_group.origin_source,
                is_modified=booking_group.is_modified,
                linked_booking_count=len(linked_booking_ids),
            )
            if booking_group
            else None
        ),
        linked_booking_group=(
            LinkedBookingGroupSummary(
                booking_group_id=booking_group.id,
                programme_activity_id=booking_group.programme_activity_id,
                expected_asset_type=booking_group.expected_asset_type,
                selected_week_start=(
                    booking_group.selected_week_start.isoformat()
                    if booking_group.selected_week_start
                    else None
                ),
                origin_source=booking_group.origin_source,
                is_modified=booking_group.is_modified,
                booking_count=len(linked_booking_ids),
                total_booked_hours=total_booked_hours,
                last_booking_at=last_booking_at,
            )
            if booking_group
            else None
        ),
        linked_bookings=linked_bookings,
        candidate_assets=candidate_assets,
    )


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
    _require_readable_upload(upload)

    _check_project_access(upload.project_id, current_user, db)

    # Find previous version
    previous = get_previous_successful_programme_upload(upload.project_id, upload.version_number, db)

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

    if is_upload_processing(upload):
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
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Blob deletion failed for upload {upload_id}; DB record not removed.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Blob deletion error for upload {upload_id}; DB record not removed.",
            ) from exc

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
    _require_readable_upload(upload)

    _check_project_access(upload.project_id, current_user, db)

    rows = (
        db.query(ActivityAssetMapping, ProgrammeActivity.name, ProgrammeActivity.item_id)
        .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
        .filter(ProgrammeActivity.programme_upload_id == upload_id)
        .order_by(ProgrammeActivity.sort_order.asc().nulls_last(), ActivityAssetMapping.created_at.asc())
        .all()
    )
    return [_serialize_mapping(mapping, activity_name, item_id) for mapping, activity_name, item_id in rows]


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
    _require_readable_upload(upload)

    _check_project_access(upload.project_id, current_user, db)

    rows = (
        db.query(ActivityAssetMapping, ProgrammeActivity.name, ProgrammeActivity.item_id)
        .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
        .filter(
            ProgrammeActivity.programme_upload_id == upload_id,
            ActivityAssetMapping.confidence == "low",
            ActivityAssetMapping.manually_corrected.is_(False),
        )
        .order_by(ProgrammeActivity.sort_order.asc().nulls_last(), ActivityAssetMapping.created_at.asc())
        .all()
    )
    return [_serialize_mapping(mapping, activity_name, item_id) for mapping, activity_name, item_id in rows]


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
    try:
        context = load_mapping_correction_context(db, mapping_id)
        if context is None:
            raise HTTPException(status_code=404, detail="Mapping not found")

        _check_project_access(context.upload.project_id, current_user, db)

        result = apply_mapping_correction(
            db,
            context=context,
            corrected_by_user_id=current_user.id,
            asset_type=payload.asset_type,
            manual_total_hours=payload.manual_total_hours,
            manual_normalized_distribution=payload.manual_normalized_distribution,
        )
        db.commit()
        db.refresh(result.context.mapping)
    except HTTPException:
        raise
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MappingCorrectionValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to apply Stage 8 mapping correction for mapping %s", mapping_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to apply mapping correction",
        ) from exc

    return _serialize_mapping(
        result.context.mapping,
        result.context.activity.name,
        result.context.activity.item_id,
    )
