# crud/slot_booking.py
import warnings
from typing import Optional, List, Dict, Any, Set, Tuple, Union
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from sqlalchemy import and_, or_, func, case
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.sql.elements import ColumnElement
from collections import defaultdict

from ..core.constants import BOOKING_MIN_SLOT_DURATION_MINUTES
from ..models.lookahead import Notification
from ..models.programme import (
    ActivityAssetMapping,
    ActivityBookingGroup,
    ProgrammeActivity,
    ProgrammeUpload,
)
from ..models.slot_booking import SlotBooking
from ..schemas.enums import AssetStatus, BookingAuditAction, BookingStatus, UserRole
from ..models.user import User
from ..models.subcontractor import Subcontractor
from ..models.asset import Asset
from ..models.site_project import SiteProject
from ..schemas.slot_booking import (
    BookingCreate,
    BookingUpdate,
    BookingFilterParams,
    BookingConflictCheck,
    BookingConflictResponse,
    BookingCalendarView,
    BookingStatistics,
    BulkBookingCreate,
    BookingDetailResponse,
    BookingResponse,
    BulkRescheduleApplyResponse,
    BulkRescheduleBookingSnapshot,
    BulkRescheduleIssue,
    BulkRescheduleItemResult,
    BulkRescheduleRequest,
    BulkRescheduleSummary,
    BulkRescheduleValidationResponse,
)
from app.crud.booking_audit import log_booking_audit, build_changes_dict
from .asset import sync_maintenance_status
from ..services.metadata_confidence_service import asset_is_planning_ready
from ..services.lookahead_engine import build_eligible_activity_mapping_filters
from ..services.programme_upload_service import is_upload_readable
from ..services.programme_upload_service import get_active_programme_upload
from ..services.project_calendar_service import (
    get_project_calendar_days,
    resolve_project_holiday_region,
)


# ---------------------------------------------------------------------------
# Helpers shared across conflict-checking, auto-deny, and competing count
# ---------------------------------------------------------------------------


class BookingValidationError(ValueError):
    """Domain validation error for booking flows."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


def _ensure_asset_planning_ready(asset: Asset) -> None:
    if not asset_is_planning_ready(asset):
        raise BookingValidationError(
            f"Asset '{asset.name}' must have a confirmed or inferred canonical type before it can be booked."
        )

def _load_project_with_members(db: Session, project_id: UUID) -> Optional[SiteProject]:
    return (
        db.query(SiteProject)
        .options(joinedload(SiteProject.managers), joinedload(SiteProject.subcontractors))
        .filter(SiteProject.id == project_id)
        .first()
    )


def _ids_match(entity: object | None, expected_id: UUID) -> bool:
    entity_id = getattr(entity, "id", getattr(entity, "pk", None))
    return str(entity_id) == str(expected_id)


def _resolve_project_with_members(
    db: Session,
    project_id: UUID,
    project: Optional[SiteProject] = None,
) -> Optional[SiteProject]:
    if (
        project is not None
        and _ids_match(project, project_id)
        and hasattr(project, "managers")
        and hasattr(project, "subcontractors")
    ):
        return project
    return _load_project_with_members(db, project_id)


def _load_booking_asset(db: Session, asset_id: UUID) -> Optional[Asset]:
    return db.query(Asset).filter(Asset.id == asset_id).first()


def _resolve_booking_asset(
    db: Session,
    asset_id: UUID,
    asset: Optional[Asset] = None,
) -> Optional[Asset]:
    if asset is not None:
        asset_identity = getattr(asset, "id", getattr(asset, "pk", None))
        if asset_identity is not None and str(asset_identity) == str(asset_id):
            return asset
    return _load_booking_asset(db, asset_id)

def _overlapping_time_filter(start_time: Union[time, ColumnElement], end_time: Union[time, ColumnElement]) -> ColumnElement:
    """Return an OR clause matching any time overlap with the given window."""
    return or_(
        and_(
            SlotBooking.start_time <= start_time,
            SlotBooking.end_time > start_time
        ),
        and_(
            SlotBooking.start_time < end_time,
            SlotBooking.end_time >= end_time
        ),
        and_(
            SlotBooking.start_time >= start_time,
            SlotBooking.end_time <= end_time
        )
    )


_ACTIVE_STATUSES = [BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED]


def _start_of_booking_week(booking_date: date) -> date:
    return booking_date - timedelta(days=booking_date.weekday())


def _normalize_selected_week_start(selected_week_start: Optional[date]) -> Optional[date]:
    if selected_week_start is None:
        return None
    return selected_week_start - timedelta(days=selected_week_start.weekday())


def _normalize_asset_type_value(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _get_activity_expected_asset_type(
    db: Session,
    programme_activity_id: UUID | None = None,
    activity_asset_mapping_id: UUID | None = None,
) -> tuple[ProgrammeActivity, ProgrammeUpload, ActivityAssetMapping, str]:
    if activity_asset_mapping_id is not None:
        mapping = (
            db.query(ActivityAssetMapping)
            .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
            .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
            .filter(
                ActivityAssetMapping.id == activity_asset_mapping_id,
                *build_eligible_activity_mapping_filters(),
                ActivityAssetMapping.is_active.is_(True),
            )
            .first()
        )
        if mapping is None or not mapping.asset_type:
            raise BookingValidationError("Asset requirement is not available for booking")
        if programme_activity_id is not None and programme_activity_id != mapping.programme_activity_id:
            raise BookingValidationError("Activity asset requirement does not belong to the supplied programme activity")
        programme_activity_id = mapping.programme_activity_id
    elif programme_activity_id is None:
        raise BookingValidationError("activity_asset_mapping_id is required for activity-linked bookings")

    activity = (
        db.query(ProgrammeActivity)
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .filter(ProgrammeActivity.id == programme_activity_id)
        .first()
    )
    if activity is None:
        raise BookingValidationError("Programme activity not found")

    upload = (
        db.query(ProgrammeUpload)
        .filter(ProgrammeUpload.id == activity.programme_upload_id)
        .first()
    )
    if upload is None or not is_upload_readable(upload):
        raise BookingValidationError("Programme activity is not available for booking")

    if activity_asset_mapping_id is None:
        eligible_mappings = (
            db.query(ActivityAssetMapping)
            .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
            .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
            .filter(
                ActivityAssetMapping.programme_activity_id == programme_activity_id,
                *build_eligible_activity_mapping_filters(),
                ActivityAssetMapping.is_active.is_(True),
            )
            .order_by(
                case((ActivityAssetMapping.asset_role == "lead", 0), else_=1),
                ActivityAssetMapping.manually_corrected.desc(),
                ActivityAssetMapping.corrected_at.desc().nulls_last(),
                ActivityAssetMapping.created_at.desc(),
            )
            .all()
        )
        lead_mappings = [m for m in eligible_mappings if (m.asset_role or "lead") == "lead"]
        candidates = lead_mappings or eligible_mappings
        if len(candidates) != 1:
            raise BookingValidationError("Multiple asset requirements exist; choose an activity_asset_mapping_id")
        mapping = candidates[0]

    if mapping is None or not mapping.asset_type:
        raise BookingValidationError("Programme activity does not have a resolved asset type")

    return activity, upload, mapping, _normalize_asset_type_value(mapping.asset_type) or ""


def _get_or_create_activity_booking_group(
    db: Session,
    *,
    project_id: UUID,
    programme_activity_id: UUID | None,
    activity_asset_mapping_id: UUID | None = None,
    selected_week_start: Optional[date],
    created_by_id: UUID,
) -> ActivityBookingGroup:
    activity, upload, mapping, expected_asset_type = _get_activity_expected_asset_type(
        db,
        programme_activity_id=programme_activity_id,
        activity_asset_mapping_id=activity_asset_mapping_id,
    )
    if upload.project_id != project_id:
        raise BookingValidationError("Programme activity does not belong to this project")

    booking_group = (
        db.query(ActivityBookingGroup)
        .filter(ActivityBookingGroup.activity_asset_mapping_id == mapping.id)
        .first()
    )
    if booking_group is None:
        booking_group = ActivityBookingGroup(
            project_id=project_id,
            programme_activity_id=activity.id,
            activity_asset_mapping_id=mapping.id,
            expected_asset_type=expected_asset_type,
            selected_week_start=_normalize_selected_week_start(selected_week_start),
            origin_source="lookahead_week_row" if selected_week_start else "activity_row",
            created_by=created_by_id,
        )
        db.add(booking_group)
        db.flush()
    else:
        booking_group.project_id = project_id
        booking_group.programme_activity_id = activity.id
        booking_group.activity_asset_mapping_id = mapping.id
        booking_group.expected_asset_type = expected_asset_type
        if booking_group.selected_week_start is None and selected_week_start is not None:
            booking_group.selected_week_start = _normalize_selected_week_start(selected_week_start)
        db.flush()

    return booking_group


def _mark_booking_group_modified_if_needed(
    booking_group: ActivityBookingGroup | None,
    *,
    booking: SlotBooking,
    previous_date: date,
    previous_start_time: time,
    previous_end_time: time,
    previous_subcontractor_id: UUID | None,
    previous_asset_type: str | None,
    current_asset_type: str | None,
) -> None:
    if booking_group is None or booking_group.is_modified:
        return

    expected_asset_type = _normalize_asset_type_value(booking_group.expected_asset_type)
    date_changed = booking.booking_date != previous_date
    time_changed = booking.start_time != previous_start_time or booking.end_time != previous_end_time
    subcontractor_changed = booking.subcontractor_id != previous_subcontractor_id
    normalized_previous_asset_type = _normalize_asset_type_value(previous_asset_type)
    normalized_current_asset_type = _normalize_asset_type_value(current_asset_type)
    type_drift = bool(
        normalized_current_asset_type
        and expected_asset_type
        and normalized_current_asset_type != expected_asset_type
    )
    previous_type_drift = bool(
        normalized_previous_asset_type
        and expected_asset_type
        and normalized_previous_asset_type != expected_asset_type
    )

    if date_changed or time_changed or subcontractor_changed or (type_drift and not previous_type_drift):
        booking_group.is_modified = True


def _mark_matching_lookahead_notifications_acted(
    db: Session,
    booking: SlotBooking,
    *,
    asset: Asset | None = None,
) -> List[UUID]:
    """Mark unresolved lookahead notifications as acted when a booking fulfills them."""
    if booking.status != BookingStatus.CONFIRMED or booking.subcontractor_id is None:
        return []

    asset_identity = getattr(asset, "id", getattr(asset, "pk", None)) if asset is not None else None
    resolved_asset = asset if asset is not None and asset_identity is None else _resolve_booking_asset(
        db,
        booking.asset_id,
        asset=asset,
    )
    if resolved_asset is None or not asset_is_planning_ready(resolved_asset):
        return []

    asset_type = _normalize_asset_type_value(resolved_asset.canonical_type)
    if not asset_type:
        return []

    notifications = (
        db.query(Notification)
        .filter(
            Notification.project_id == booking.project_id,
            Notification.sub_id == booking.subcontractor_id,
            Notification.asset_type == asset_type,
            Notification.week_start == _start_of_booking_week(booking.booking_date),
            Notification.trigger_type == "lookahead",
            Notification.status.in_(["pending", "sent"]),
        )
        .all()
    )

    if not notifications:
        return []

    now = datetime.now(timezone.utc)
    for notification in notifications:
        notification.status = "acted"
        notification.acted_at = now
        notification.booking_id = booking.id

    return [notification.id for notification in notifications]


# ---------------------------------------------------------------------------
# Response builder helpers
# ---------------------------------------------------------------------------

def _build_booking_detail_response(
    booking: SlotBooking,
    competing_pending_count: int = 0,
) -> BookingDetailResponse:
    """Build a BookingDetailResponse from a fully-loaded SlotBooking ORM object.

    The booking must have project, manager, subcontractor, and asset
    relationships already loaded (e.g. via joinedload) before calling this.
    ``competing_pending_count`` is only relevant for the single-booking detail
    view and defaults to 0 for list/calendar contexts.
    """
    booking_group = getattr(booking, "booking_group", None)
    group_activity = getattr(booking_group, "activity", None) if booking_group else None
    return BookingDetailResponse(
        id=booking.id,
        project_id=booking.project_id,
        manager_id=booking.manager_id,
        subcontractor_id=booking.subcontractor_id,
        asset_id=booking.asset_id,
        booking_group_id=booking_group.id if booking_group else None,
        programme_activity_id=group_activity.id if group_activity else None,
        activity_asset_mapping_id=getattr(booking_group, "activity_asset_mapping_id", None) if booking_group else None,
        programme_activity_name=group_activity.name if group_activity else None,
        expected_asset_type=booking_group.expected_asset_type if booking_group else None,
        is_modified=bool(booking_group.is_modified) if booking_group else False,
        booking_date=booking.booking_date,
        start_time=booking.start_time,
        end_time=booking.end_time,
        status=booking.status,
        source=booking.source,
        purpose=booking.purpose,
        notes=booking.notes,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
        competing_pending_count=competing_pending_count,
        project={
            "id": booking.project.id,
            "name": booking.project.name,
            "location": booking.project.location,
            "status": booking.project.status,
        } if booking.project else None,
        manager={
            "id": booking.manager.id,
            "email": booking.manager.email,
            "first_name": booking.manager.first_name,
            "last_name": booking.manager.last_name,
            "role": booking.manager.role,
            "full_name": f"{booking.manager.first_name} {booking.manager.last_name}",
        } if booking.manager else None,
        subcontractor={
            "id": booking.subcontractor.id,
            "email": booking.subcontractor.email,
            "first_name": booking.subcontractor.first_name,
            "last_name": booking.subcontractor.last_name,
            "company_name": booking.subcontractor.company_name,
            "trade_specialty": booking.subcontractor.trade_specialty,
        } if booking.subcontractor else None,
        asset={
            "id": booking.asset.id,
            "asset_code": booking.asset.asset_code,
            "name": booking.asset.name,
            "type": booking.asset.type,
            "status": booking.asset.status.value if booking.asset.status else None,
            "pending_booking_capacity": booking.asset.pending_booking_capacity,
        } if booking.asset else None,
    )


# ---------------------------------------------------------------------------
# Actor resolution helper
# ---------------------------------------------------------------------------

def _resolve_booking_actor(
    db: Session,
    actor_id: UUID,
    actor_role: UserRole,
    provided_manager_id: Optional[UUID],
    provided_subcontractor_id: Optional[UUID],
    project_id: UUID,
    project: Optional[SiteProject] = None,
) -> Tuple[UUID, Optional[UUID], BookingStatus]:
    """Resolve manager_id, subcontractor_id, and initial booking status from the actor's role.

    Returns ``(manager_id, subcontractor_id, booking_status)``.

    Raises ``BookingValidationError`` for any invalid/missing entity reference.
    """
    project_with_members = _resolve_project_with_members(
        db,
        project_id,
        project=project,
    )

    if not project_with_members:
        raise BookingValidationError(f"Project with id {project_id} not found")

    if actor_role in [UserRole.ADMIN, UserRole.MANAGER]:
        manager_id = provided_manager_id or actor_id
        subcontractor_id = provided_subcontractor_id
        booking_status = BookingStatus.CONFIRMED

        if not any(str(m.id) == str(manager_id) for m in project_with_members.managers):
            raise BookingValidationError(f"Manager {manager_id} is not a member of project {project_id}")

        if subcontractor_id:
            if not any(str(s.id) == str(subcontractor_id) for s in project_with_members.subcontractors):
                raise BookingValidationError(f"Subcontractor {subcontractor_id} is not assigned to project {project_id}")

    elif actor_role == UserRole.SUBCONTRACTOR:
        subcontractor_id = actor_id

        if provided_subcontractor_id and provided_subcontractor_id != actor_id:
            raise BookingValidationError("Subcontractors can only create bookings for themselves")

        if not any(str(s.id) == str(subcontractor_id) for s in project_with_members.subcontractors):
            raise BookingValidationError(f"Subcontractor {subcontractor_id} is not assigned to project {project_id}")

        if provided_manager_id:
            manager_id = provided_manager_id
            if not any(str(m.id) == str(manager_id) for m in project_with_members.managers):
                raise BookingValidationError(f"Manager {manager_id} is not a member of project {project_id}")
        else:
            if not project_with_members.managers:
                raise BookingValidationError("No managers found for this project")
            manager_id = project_with_members.managers[0].id

        booking_status = BookingStatus.PENDING
    else:
        raise BookingValidationError(f"Invalid user role: {actor_role}")

    return manager_id, subcontractor_id, booking_status


# ---------------------------------------------------------------------------
# Statistics filter helper
# ---------------------------------------------------------------------------

def _apply_booking_stats_filters(
    query: Any,
    project_id: Optional[UUID],
    user_id: Optional[UUID],
    date_from: Optional[date],
    date_to: Optional[date],
) -> Any:
    """Apply the standard 4-field statistics filters to any SlotBooking query."""
    if project_id:
        query = query.filter(SlotBooking.project_id == project_id)
    if user_id:
        query = query.filter(SlotBooking.manager_id == user_id)
    if date_from:
        query = query.filter(SlotBooking.booking_date >= date_from)
    if date_to:
        query = query.filter(SlotBooking.booking_date <= date_to)
    return query


# ---------------------------------------------------------------------------

def _auto_deny_competing_pending_bookings(
    db: Session,
    booking: SlotBooking,
    actor_id: UUID,
    actor_role: UserRole,
    comment: str = "Auto-denied: another booking on this slot was confirmed",
    treat_as_confirmed: bool = False,
) -> List[UUID]:
    """Auto-deny PENDING bookings overlapping a confirmed booking's slot."""
    if not treat_as_confirmed and booking.status != BookingStatus.CONFIRMED:
        return []

    competing = (
        db.query(SlotBooking)
        .filter(
            SlotBooking.asset_id == booking.asset_id,
            SlotBooking.booking_date == booking.booking_date,
            _overlapping_time_filter(booking.start_time, booking.end_time),
            SlotBooking.status == BookingStatus.PENDING,
            SlotBooking.id != booking.id,
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    for comp in competing:
        comp.status = BookingStatus.DENIED
        comp.updated_at = now
        log_booking_audit(
            db,
            actor_id=actor_id,
            actor_role=actor_role,
            action=BookingAuditAction.DENIED,
            booking_id=comp.id,
            from_status=BookingStatus.PENDING,
            to_status=BookingStatus.DENIED,
            comment=comment,
        )

    return [comp.id for comp in competing]


# ---------------------------------------------------------------------------

def create_booking(
    db: Session,
    booking_data: BookingCreate,
    created_by_id: UUID,
    created_by_role: UserRole,
    comment: Optional[str] = None,
    source: Optional[str] = None,
    booking_group_id: Optional[UUID] = None,
    project: Optional[SiteProject] = None,
    asset: Optional[Asset] = None,
) -> SlotBooking:
    """
    Create a new booking in the database with role-based status.
    """
    
    # Validate that all referenced entities exist
    project = _resolve_project_with_members(
        db,
        booking_data.project_id,
        project=project,
    )
    if not project:
        raise BookingValidationError(f"Project with id {booking_data.project_id} not found")
    
    asset = _resolve_booking_asset(
        db,
        booking_data.asset_id,
        asset=asset,
    )
    if not asset:
        raise BookingValidationError(f"Asset with id {booking_data.asset_id} not found")
    _ensure_asset_planning_ready(asset)

    sync_maintenance_status(db, asset)

    # Block permanently unavailable statuses
    if asset.status in (AssetStatus.MAINTENANCE, AssetStatus.RETIRED):
        raise BookingValidationError(f"Asset is not available (status: {asset.status.value})")

    # Block bookings during scheduled maintenance windows
    if asset.maintenance_start_date and asset.maintenance_end_date:
        if asset.maintenance_start_date <= booking_data.booking_date <= asset.maintenance_end_date:
            raise BookingValidationError(
                f"Asset is under scheduled maintenance from "
                f"{asset.maintenance_start_date} to {asset.maintenance_end_date}"
            )

    # Determine manager_id, subcontractor_id, and status based on role
    manager_id, subcontractor_id, booking_status = _resolve_booking_actor(
        db=db,
        actor_id=created_by_id,
        actor_role=created_by_role,
        provided_manager_id=booking_data.manager_id,
        provided_subcontractor_id=booking_data.subcontractor_id,
        project_id=booking_data.project_id,
        project=project,
    )

    booking_group: ActivityBookingGroup | None = None
    if booking_group_id is not None:
        booking_group = (
            db.query(ActivityBookingGroup)
            .filter(ActivityBookingGroup.id == booking_group_id)
            .first()
        )
        if booking_group is None:
            raise BookingValidationError("Activity booking group not found")
        if booking_group.project_id != booking_data.project_id:
            raise BookingValidationError("Activity booking group does not belong to this project")
    elif (
        getattr(booking_data, "programme_activity_id", None) is not None
        or getattr(booking_data, "activity_asset_mapping_id", None) is not None
    ):
        booking_group = _get_or_create_activity_booking_group(
            db,
            project_id=booking_data.project_id,
            programme_activity_id=getattr(booking_data, "programme_activity_id", None),
            activity_asset_mapping_id=getattr(booking_data, "activity_asset_mapping_id", None),
            selected_week_start=booking_data.selected_week_start,
            created_by_id=created_by_id,
        )

    # Create the booking
    db_booking = SlotBooking(
        project_id=booking_data.project_id,
        manager_id=manager_id,
        subcontractor_id=subcontractor_id,
        asset_id=booking_data.asset_id,
        booking_group_id=booking_group.id if booking_group else None,
        booking_date=booking_data.booking_date,
        start_time=booking_data.start_time,
        end_time=booking_data.end_time,
        purpose=booking_data.purpose,
        notes=booking_data.notes,
        status=booking_status,
        source=(source or "manual").strip().lower(),
    )

    db.add(db_booking)
    db.flush()  # Flush to get the ID for audit logging

    # Log the booking creation in audit trail
    log_booking_audit(
        db,
        actor_id=created_by_id,
        actor_role=created_by_role,
        action=BookingAuditAction.CREATED,
        booking_id=db_booking.id,
        to_status=db_booking.status,
        comment=comment
    )

    if (
        booking_group is not None
        and booking_group.expected_asset_type
        and _normalize_asset_type_value(asset.canonical_type)
        != _normalize_asset_type_value(booking_group.expected_asset_type)
    ):
        booking_group.is_modified = True

    # If this booking is confirmed on creation (manager/admin), auto-deny
    # overlapping pending requests for the same slot.
    _auto_deny_competing_pending_bookings(
        db,
        booking=db_booking,
        actor_id=created_by_id,
        actor_role=created_by_role,
    )
    _mark_matching_lookahead_notifications_acted(db, db_booking, asset=asset)

    db.commit()
    db.refresh(db_booking)
    
    return db_booking

def create_bulk_bookings(
    db: Session,
    bulk_data: BulkBookingCreate,
    created_by_id: UUID,
    created_by_role: UserRole,
    comment: Optional[str] = None,
    project: Optional[SiteProject] = None,
) -> List[SlotBooking]:
    """
    Create multiple bookings at once with role-based status.
    """
    bookings = []
    booking_requests = []
    failed_bookings = []
    
    # Validate base entities exist
    project = _resolve_project_with_members(
        db,
        bulk_data.project_id,
        project=project,
    )
    if not project:
        raise BookingValidationError(f"Project with id {bulk_data.project_id} not found")
    
    # Determine manager_id, subcontractor_id, and status based on role
    manager_id, subcontractor_id, booking_status = _resolve_booking_actor(
        db=db,
        actor_id=created_by_id,
        actor_role=created_by_role,
        provided_manager_id=bulk_data.manager_id,
        provided_subcontractor_id=bulk_data.subcontractor_id,
        project_id=bulk_data.project_id,
        project=project,
    )

    booking_group: ActivityBookingGroup | None = None
    if (
        getattr(bulk_data, "programme_activity_id", None) is not None
        or getattr(bulk_data, "activity_asset_mapping_id", None) is not None
    ):
        booking_group = _get_or_create_activity_booking_group(
            db,
            project_id=bulk_data.project_id,
            programme_activity_id=getattr(bulk_data, "programme_activity_id", None),
            activity_asset_mapping_id=getattr(bulk_data, "activity_asset_mapping_id", None),
            selected_week_start=bulk_data.selected_week_start,
            created_by_id=created_by_id,
        )
    
    try:
        # Resolve maintenance status for all assets upfront (before any bookings are flushed)
        for aid in set(bulk_data.asset_ids):
            a = db.query(Asset).filter(Asset.id == aid).first()
            if a:
                sync_maintenance_status(db, a)

        seen_booking_pairs: Set[Tuple[UUID, date]] = set()

        for asset_id in bulk_data.asset_ids:
            asset = db.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                failed_bookings.append({"asset_id": asset_id, "reason": "Asset not found"})
                continue
            if not asset_is_planning_ready(asset):
                failed_bookings.append({
                    "asset_id": asset_id,
                    "reason": f"Asset '{asset.name}' must have a confirmed or inferred canonical type before it can be booked",
                })
                continue

            if asset.status in (AssetStatus.MAINTENANCE, AssetStatus.RETIRED):
                failed_bookings.append({"asset_id": asset_id, "reason": f"Asset status is {asset.status.value}"})
                continue

            for booking_date in bulk_data.booking_dates:
                # Check maintenance date window
                if asset.maintenance_start_date and asset.maintenance_end_date:
                    if asset.maintenance_start_date <= booking_date <= asset.maintenance_end_date:
                        failed_bookings.append({
                            "asset_id": asset_id,
                            "date": booking_date,
                            "reason": f"Asset is under scheduled maintenance from {asset.maintenance_start_date} to {asset.maintenance_end_date}"
                        })
                        continue

                pair_key = (asset_id, booking_date)
                if pair_key in seen_booking_pairs:
                    failed_bookings.append({
                        "asset_id": asset_id,
                        "date": booking_date,
                        "reason": "Duplicate asset/date pair in bulk booking payload",
                    })
                    continue

                conflict_check = BookingConflictCheck(
                    asset_id=asset_id,
                    booking_date=booking_date,
                    start_time=bulk_data.start_time,
                    end_time=bulk_data.end_time
                )

                conflicts = check_booking_conflicts(db, conflict_check)
                if conflicts.has_confirmed_conflict:
                    failed_bookings.append({
                        "asset_id": asset_id,
                        "date": booking_date,
                        "reason": "A confirmed booking already exists"
                    })
                    continue
                if booking_status == BookingStatus.PENDING and not conflicts.can_request:
                    failed_bookings.append({
                        "asset_id": asset_id,
                        "date": booking_date,
                        "reason": "Pending capacity reached"
                    })
                    continue

                seen_booking_pairs.add(pair_key)
                booking_requests.append((asset_id, booking_date))

        if failed_bookings:
            db.rollback()
            raise BookingValidationError(
                "Bulk booking validation failed",
                details=failed_bookings,
            )

        for asset_id, booking_date in booking_requests:
            asset = db.query(Asset).filter(Asset.id == asset_id).first()
            db_booking = SlotBooking(
                project_id=bulk_data.project_id,
                manager_id=manager_id,
                subcontractor_id=subcontractor_id,
                asset_id=asset_id,
                booking_group_id=booking_group.id if booking_group else None,
                booking_date=booking_date,
                start_time=bulk_data.start_time,
                end_time=bulk_data.end_time,
                purpose=bulk_data.purpose,
                notes=bulk_data.notes,
                status=booking_status,
                source="manual",
            )

            db.add(db_booking)
            db.flush()

            log_booking_audit(
                db,
                actor_id=created_by_id,
                actor_role=created_by_role,
                action=BookingAuditAction.CREATED,
                booking_id=db_booking.id,
                to_status=db_booking.status,
                comment=comment
            )

            if (
                booking_group is not None
                and booking_group.expected_asset_type
                and _normalize_asset_type_value(asset.canonical_type)
                != _normalize_asset_type_value(booking_group.expected_asset_type)
            ):
                booking_group.is_modified = True

            # If this booking is confirmed on creation (manager/admin),
            # auto-deny overlapping pending requests for the same slot.
            _auto_deny_competing_pending_bookings(
                db,
                booking=db_booking,
                actor_id=created_by_id,
                actor_role=created_by_role,
            )
            _mark_matching_lookahead_notifications_acted(db, db_booking, asset=asset)

            bookings.append(db_booking)

        if bookings:
            db.commit()
            for booking in bookings:
                db.refresh(booking)

    except Exception:
        db.rollback()
        raise

    return bookings


def _snapshot_booking(booking: SlotBooking) -> BulkRescheduleBookingSnapshot:
    return BulkRescheduleBookingSnapshot(
        booking_id=booking.id,
        project_id=booking.project_id,
        asset_id=booking.asset_id,
        subcontractor_id=booking.subcontractor_id,
        booking_date=booking.booking_date,
        start_time=booking.start_time,
        end_time=booking.end_time,
        status=booking.status,
    )


def _bulk_issue(code: str, message: str, field: Optional[str] = None) -> BulkRescheduleIssue:
    return BulkRescheduleIssue(code=code, message=message, field=field)


def _status_value(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _is_maintenance_blocked_for_date(asset: Asset, target_date: date) -> Optional[str]:
    status_value = _status_value(getattr(asset, "status", None))
    maint_start = getattr(asset, "maintenance_start_date", None)
    maint_end = getattr(asset, "maintenance_end_date", None)

    if maint_start and maint_end:
        if maint_start <= target_date <= maint_end:
            return f"Asset is under scheduled maintenance from {maint_start} to {maint_end}"
        return None

    if status_value == AssetStatus.MAINTENANCE.value:
        return "Asset is in maintenance without a schedulable maintenance window"

    return None


def _is_working_weekday(target_date: date, work_days_per_week: int) -> bool:
    if work_days_per_week < 1 or work_days_per_week > 7:
        work_days_per_week = 5
    return target_date.weekday() < work_days_per_week


def _resolve_upload_work_days_per_week(upload: object | None) -> tuple[int, str]:
    raw_value = getattr(upload, "work_days_per_week", None)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 5, "default"
    if 1 <= value <= 7:
        return value, "programme_upload"
    return 5, "default"


def _resolve_booking_work_days_per_week(
    booking: SlotBooking,
    active_upload: ProgrammeUpload | None,
) -> tuple[int, str]:
    booking_group = getattr(booking, "booking_group", None)
    activity = getattr(booking_group, "activity", None) if booking_group else None
    upload = getattr(activity, "upload", None) if activity else None
    value, source = _resolve_upload_work_days_per_week(upload)
    if source != "default":
        return value, "linked_activity_upload"

    value, source = _resolve_upload_work_days_per_week(active_upload)
    if source != "default":
        return value, "active_project_upload"

    return 5, "default"


def _project_work_hours(project: SiteProject) -> tuple[time, time]:
    start_time = getattr(project, "default_work_start_time", None) or time(8, 0)
    end_time = getattr(project, "default_work_end_time", None) or time(16, 0)
    return start_time, end_time


def _is_inside_project_work_hours(
    start_time: time,
    end_time: time,
    project: SiteProject,
) -> bool:
    work_start, work_end = _project_work_hours(project)
    if work_end <= work_start:
        return True
    return start_time >= work_start and end_time <= work_end


def _target_windows_overlap(left: BulkRescheduleBookingSnapshot, right: BulkRescheduleBookingSnapshot) -> bool:
    return (
        left.asset_id == right.asset_id
        and left.booking_date == right.booking_date
        and left.start_time < right.end_time
        and left.end_time > right.start_time
    )


def _build_conflict_booking_response(booking: SlotBooking) -> BookingResponse:
    return BookingResponse(
        id=booking.id,
        project_id=booking.project_id,
        manager_id=booking.manager_id,
        subcontractor_id=booking.subcontractor_id,
        asset_id=booking.asset_id,
        booking_date=booking.booking_date,
        start_time=booking.start_time,
        end_time=booking.end_time,
        status=booking.status,
        purpose=booking.purpose,
        notes=booking.notes,
        source=booking.source,
        booking_group_id=booking.booking_group_id,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


def validate_bulk_reschedule(
    db: Session,
    payload: BulkRescheduleRequest,
    *,
    actor_id: UUID,
    actor_role: UserRole,
    lock_rows: bool = False,
) -> BulkRescheduleValidationResponse:
    project = _load_project_with_members(db, payload.project_id)
    item_results = {
        item.booking_id: BulkRescheduleItemResult(booking_id=item.booking_id)
        for item in payload.items
    }

    if project is None:
        for result in item_results.values():
            result.errors.append(_bulk_issue("project_not_found", f"Project with id {payload.project_id} not found"))
        return _summarize_bulk_reschedule_results(item_results.values())

    if payload.allow_non_working_days and actor_role not in {UserRole.MANAGER, UserRole.ADMIN}:
        for result in item_results.values():
            result.errors.append(
                _bulk_issue(
                    "non_working_override_forbidden",
                    "Only managers and admins can allow non-working day reschedules",
                    "allow_non_working_days",
                )
            )

    if payload.allow_outside_working_hours and actor_role not in {UserRole.MANAGER, UserRole.ADMIN}:
        for result in item_results.values():
            result.errors.append(
                _bulk_issue(
                    "working_hours_override_forbidden",
                    "Only managers and admins can allow outside-working-hours reschedules",
                    "allow_outside_working_hours",
                )
            )

    booking_ids = [item.booking_id for item in payload.items]
    booking_group_loader = (
        selectinload(SlotBooking.booking_group)
        .selectinload(ActivityBookingGroup.activity)
        .selectinload(ProgrammeActivity.upload)
        if lock_rows
        else joinedload(SlotBooking.booking_group)
        .joinedload(ActivityBookingGroup.activity)
        .joinedload(ProgrammeActivity.upload)
    )
    booking_query = (
        db.query(SlotBooking)
        .options(booking_group_loader)
        .filter(SlotBooking.id.in_(booking_ids))
    )
    if lock_rows:
        booking_query = booking_query.with_for_update(of=SlotBooking)
    bookings = {booking.id: booking for booking in booking_query.all()}
    active_upload = get_active_programme_upload(payload.project_id, db)

    target_asset_ids = {
        item.asset_id
        for item in payload.items
        if item.asset_id is not None
    }
    target_asset_ids.update(
        booking.asset_id
        for booking in bookings.values()
        if booking.id in item_results
    )
    assets = {
        asset.id: asset
        for asset in db.query(Asset).filter(Asset.id.in_(target_asset_ids)).all()
    } if target_asset_ids else {}

    target_subcontractor_ids = {
        item.subcontractor_id
        for item in payload.items
        if item.subcontractor_id is not None
    }
    subcontractors = {
        sub.id: sub
        for sub in db.query(Subcontractor).filter(Subcontractor.id.in_(target_subcontractor_ids)).all()
    } if target_subcontractor_ids else {}

    target_dates = [item.booking_date for item in payload.items]
    min_date = min(target_dates)
    max_date = max(target_dates)
    non_working_days = {
        day.calendar_date: day
        for day in get_project_calendar_days(
            db,
            project,
            date_from=min_date,
            date_to=max_date,
            include_regional=True,
        )
    }
    _, holiday_region, holiday_region_source = resolve_project_holiday_region(project)

    target_snapshots: Dict[UUID, BulkRescheduleBookingSnapshot] = {}
    previous_asset_types: Dict[UUID, Optional[str]] = {}
    current_asset_types: Dict[UUID, Optional[str]] = {}
    reschedulable_statuses = {BookingStatus.PENDING, BookingStatus.CONFIRMED}

    for item in payload.items:
        result = item_results[item.booking_id]
        booking = bookings.get(item.booking_id)
        if booking is None:
            result.errors.append(_bulk_issue("booking_not_found", "Booking not found", "booking_id"))
            continue

        result.original = _snapshot_booking(booking)
        work_days_per_week, work_days_source = _resolve_booking_work_days_per_week(booking, active_upload)
        result.work_days_per_week = work_days_per_week
        result.work_days_source = work_days_source
        result.holiday_region_code = holiday_region
        result.holiday_region_source = holiday_region_source

        if booking.project_id != payload.project_id:
            result.errors.append(_bulk_issue("project_mismatch", "Booking does not belong to this project", "project_id"))

        if actor_role == UserRole.SUBCONTRACTOR and booking.subcontractor_id != actor_id:
            result.errors.append(_bulk_issue("forbidden", "Subcontractors can only reschedule their own bookings"))

        if booking.status not in reschedulable_statuses:
            result.errors.append(
                _bulk_issue(
                    "status_not_reschedulable",
                    f"Only pending and confirmed bookings can be rescheduled; current status is {booking.status.value}",
                    "status",
                )
            )

        if item.end_time <= item.start_time:
            result.errors.append(_bulk_issue("invalid_time_range", "Start time must be before end time"))

        if not _is_inside_project_work_hours(item.start_time, item.end_time, project):
            work_start, work_end = _project_work_hours(project)
            working_hours_issue = _bulk_issue(
                "outside_working_hours",
                f"Target time is outside project working hours {work_start.strftime('%H:%M')} to {work_end.strftime('%H:%M')}",
                "start_time",
            )
            if payload.allow_outside_working_hours and actor_role in {UserRole.MANAGER, UserRole.ADMIN}:
                result.warnings.append(working_hours_issue)
            else:
                result.errors.append(working_hours_issue)

        target_asset_id = item.asset_id or booking.asset_id
        target_subcontractor_id = item.subcontractor_id if item.subcontractor_id is not None else booking.subcontractor_id
        if actor_role == UserRole.SUBCONTRACTOR and target_subcontractor_id != actor_id:
            result.errors.append(
                _bulk_issue(
                    "subcontractor_reassignment_forbidden",
                    "Subcontractors can only keep bookings assigned to themselves",
                    "subcontractor_id",
                )
            )
        target = BulkRescheduleBookingSnapshot(
            booking_id=booking.id,
            project_id=booking.project_id,
            asset_id=target_asset_id,
            subcontractor_id=target_subcontractor_id,
            booking_date=item.booking_date,
            start_time=item.start_time,
            end_time=item.end_time,
            status=booking.status,
        )
        result.target = target
        target_snapshots[booking.id] = target

        previous_asset = assets.get(booking.asset_id)
        previous_asset_types[booking.id] = (
            _normalize_asset_type_value(previous_asset.canonical_type)
            if previous_asset is not None
            else None
        )

        target_asset = assets.get(target_asset_id)
        if target_asset is None:
            result.errors.append(_bulk_issue("asset_not_found", "Target asset not found", "asset_id"))
        else:
            current_asset_types[booking.id] = _normalize_asset_type_value(target_asset.canonical_type)
            sync_maintenance_status(db, target_asset)
            if target_asset.project_id != payload.project_id:
                result.errors.append(_bulk_issue("asset_project_mismatch", "Target asset does not belong to this project", "asset_id"))
            if not asset_is_planning_ready(target_asset):
                result.errors.append(
                    _bulk_issue(
                        "asset_not_planning_ready",
                        f"Asset '{target_asset.name}' must have a confirmed or inferred canonical type before it can be booked",
                        "asset_id",
                    )
                )
            if _status_value(target_asset.status) == AssetStatus.RETIRED.value:
                result.errors.append(_bulk_issue("asset_retired", "Target asset is retired", "asset_id"))
            maintenance_reason = _is_maintenance_blocked_for_date(target_asset, item.booking_date)
            if maintenance_reason:
                result.errors.append(_bulk_issue("asset_maintenance", maintenance_reason, "booking_date"))

        if item.subcontractor_id is not None:
            subcontractor = subcontractors.get(item.subcontractor_id)
            if subcontractor is None:
                result.errors.append(_bulk_issue("subcontractor_not_found", "Target subcontractor not found", "subcontractor_id"))
            elif not any(str(s.id) == str(item.subcontractor_id) for s in project.subcontractors):
                result.errors.append(
                    _bulk_issue(
                        "subcontractor_project_mismatch",
                        "Target subcontractor is not assigned to this project",
                        "subcontractor_id",
                    )
                )

        weekday_issue = None
        if not _is_working_weekday(item.booking_date, work_days_per_week):
            weekday_issue = _bulk_issue(
                "outside_work_days",
                f"Target date is outside the programme-derived {work_days_per_week}-day working week",
                "booking_date",
            )
        calendar_day = non_working_days.get(item.booking_date)
        calendar_issue = None
        if calendar_day is not None:
            if getattr(calendar_day, "kind", "") == "rdo":
                result.warnings.append(
                    _bulk_issue(
                        "project_rdo_day",
                        f"Target date is a rostered day off (RDO): {calendar_day.label}",
                        "booking_date",
                    )
                )
            else:
                calendar_issue = _bulk_issue(
                    "project_non_working_day",
                    f"Target date is marked non-working: {calendar_day.label}",
                    "booking_date",
                )
        for issue in (weekday_issue, calendar_issue):
            if issue is None:
                continue
            if payload.allow_non_working_days and actor_role in {UserRole.MANAGER, UserRole.ADMIN}:
                result.warnings.append(issue)
            else:
                result.errors.append(issue)

    valid_targets = {
        booking_id: target
        for booking_id, target in target_snapshots.items()
        if not item_results[booking_id].errors
    }
    selected_ids = set(booking_ids)

    if valid_targets:
        for left_id, left in valid_targets.items():
            for right_id, right in valid_targets.items():
                if str(left_id) >= str(right_id):
                    continue
                if not _target_windows_overlap(left, right):
                    continue
                left_active = left.status in _ACTIVE_STATUSES
                right_active = right.status in _ACTIVE_STATUSES
                if left_active or right_active:
                    item_results[left_id].errors.append(
                        _bulk_issue("batch_conflict", "Target slot overlaps another selected booking in this batch")
                    )
                    item_results[right_id].errors.append(
                        _bulk_issue("batch_conflict", "Target slot overlaps another selected booking in this batch")
                    )

        for booking_id, target in valid_targets.items():
            result = item_results[booking_id]
            overlap_filter = [
                SlotBooking.asset_id == target.asset_id,
                SlotBooking.booking_date == target.booking_date,
                _overlapping_time_filter(target.start_time, target.end_time),
                SlotBooking.id.notin_(selected_ids),
            ]
            external_active = (
                db.query(SlotBooking)
                .filter(*overlap_filter, SlotBooking.status.in_(_ACTIVE_STATUSES))
                .all()
            )
            if external_active:
                result.conflicts.extend(_build_conflict_booking_response(conflict) for conflict in external_active)
                result.errors.append(_bulk_issue("confirmed_conflict", "Target slot conflicts with an active booking"))

            if target.status == BookingStatus.PENDING and not external_active:
                external_pending_count = (
                    db.query(func.count(SlotBooking.id))
                    .filter(*overlap_filter, SlotBooking.status == BookingStatus.PENDING)
                    .scalar()
                ) or 0
                batch_pending_count = sum(
                    1
                    for other_id, other_target in valid_targets.items()
                    if other_id != booking_id
                    and other_target.status == BookingStatus.PENDING
                    and _target_windows_overlap(target, other_target)
                )
                asset = assets.get(target.asset_id)
                capacity = asset.pending_booking_capacity if asset else 5
                if external_pending_count + batch_pending_count >= capacity:
                    result.errors.append(
                        _bulk_issue(
                            "pending_capacity_reached",
                            "Limit reached for this time slot. Choose another slot or contact the manager.",
                        )
                    )

    return _summarize_bulk_reschedule_results(item_results.values())


def _summarize_bulk_reschedule_results(
    results: Union[List[BulkRescheduleItemResult], Any],
) -> BulkRescheduleValidationResponse:
    result_list = list(results)
    invalid = sum(1 for result in result_list if result.errors)
    warning_count = sum(len(result.warnings) for result in result_list)
    return BulkRescheduleValidationResponse(
        can_apply=invalid == 0,
        summary=BulkRescheduleSummary(
            total=len(result_list),
            valid=len(result_list) - invalid,
            invalid=invalid,
            warnings=warning_count,
        ),
        items=result_list,
    )


def apply_bulk_reschedule(
    db: Session,
    payload: BulkRescheduleRequest,
    *,
    actor_id: UUID,
    actor_role: UserRole,
) -> BulkRescheduleApplyResponse:
    validation = validate_bulk_reschedule(
        db,
        payload,
        actor_id=actor_id,
        actor_role=actor_role,
        lock_rows=True,
    )
    if not validation.can_apply:
        raise BookingValidationError("Bulk reschedule validation failed", details=validation.model_dump(mode="json"))

    bookings_by_id = {
        booking.id: booking
        for booking in (
            db.query(SlotBooking)
            .options(joinedload(SlotBooking.booking_group))
            .filter(SlotBooking.id.in_([item.booking_id for item in payload.items]))
            .all()
        )
    }
    target_by_id = {item.booking_id: item for item in payload.items}
    updated_bookings: List[SlotBooking] = []

    try:
        for booking_id, item in target_by_id.items():
            booking = bookings_by_id[booking_id]
            old_values = {
                "booking_date": booking.booking_date,
                "start_time": booking.start_time,
                "end_time": booking.end_time,
                "purpose": booking.purpose,
                "notes": booking.notes,
                "asset_id": booking.asset_id,
                "subcontractor_id": booking.subcontractor_id,
                "status": booking.status,
            }

            previous_asset = db.query(Asset).filter(Asset.id == booking.asset_id).first()
            previous_asset_type = (
                _normalize_asset_type_value(previous_asset.canonical_type)
                if previous_asset
                else None
            )

            booking.booking_date = item.booking_date
            booking.start_time = item.start_time
            booking.end_time = item.end_time
            if item.asset_id is not None:
                booking.asset_id = item.asset_id
            if item.subcontractor_id is not None:
                booking.subcontractor_id = item.subcontractor_id
            booking.updated_at = datetime.now(timezone.utc)

            new_values = {
                "booking_date": booking.booking_date,
                "start_time": booking.start_time,
                "end_time": booking.end_time,
                "purpose": booking.purpose,
                "notes": booking.notes,
                "asset_id": booking.asset_id,
                "subcontractor_id": booking.subcontractor_id,
                "status": booking.status,
            }
            changes = build_changes_dict(old_values, new_values)
            log_booking_audit(
                db,
                actor_id=actor_id,
                actor_role=actor_role,
                action=BookingAuditAction.RESCHEDULED,
                booking_id=booking.id,
                changes=changes,
                comment=payload.comment,
            )

            current_asset = db.query(Asset).filter(Asset.id == booking.asset_id).first()
            current_asset_type = (
                _normalize_asset_type_value(current_asset.canonical_type)
                if current_asset
                else None
            )
            _mark_booking_group_modified_if_needed(
                booking.booking_group,
                booking=booking,
                previous_date=old_values["booking_date"],
                previous_start_time=old_values["start_time"],
                previous_end_time=old_values["end_time"],
                previous_subcontractor_id=old_values["subcontractor_id"],
                previous_asset_type=previous_asset_type,
                current_asset_type=current_asset_type,
            )

            _auto_deny_competing_pending_bookings(
                db,
                booking=booking,
                actor_id=actor_id,
                actor_role=actor_role,
            )
            if booking.status == BookingStatus.CONFIRMED:
                _mark_matching_lookahead_notifications_acted(db, booking, asset=current_asset)
            updated_bookings.append(booking)

        db.commit()
        for booking in updated_bookings:
            db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    post_validation = validate_bulk_reschedule(
        db,
        payload,
        actor_id=actor_id,
        actor_role=actor_role,
        lock_rows=False,
    )
    return BulkRescheduleApplyResponse(
        validation=post_validation,
        bookings=[get_booking_detail(db, booking.id) for booking in updated_bookings],
    )


def get_booking(db: Session, booking_id: UUID) -> Optional[SlotBooking]:
    return db.query(SlotBooking).filter(SlotBooking.id == booking_id).first()

def get_booking_with_details(db: Session, booking_id: UUID) -> Optional[SlotBooking]:
    return db.query(SlotBooking).options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.manager),
        joinedload(SlotBooking.subcontractor),
        joinedload(SlotBooking.asset),
        joinedload(SlotBooking.booking_group).joinedload(ActivityBookingGroup.activity),
    ).filter(SlotBooking.id == booking_id).first()

def _get_competing_pending_count(db: Session, booking: SlotBooking) -> int:
    """Count other PENDING bookings overlapping the same slot (excludes self)."""
    if booking.status != BookingStatus.PENDING:
        return 0
    return (
        db.query(func.count(SlotBooking.id))
        .filter(
            SlotBooking.asset_id == booking.asset_id,
            SlotBooking.booking_date == booking.booking_date,
            _overlapping_time_filter(booking.start_time, booking.end_time),
            SlotBooking.status == BookingStatus.PENDING,
            SlotBooking.id != booking.id,
        )
        .scalar()
    ) or 0


def get_booking_detail(db: Session, booking_id: UUID) -> Optional[BookingDetailResponse]:
    booking = get_booking_with_details(db, booking_id)

    if not booking:
        return None

    competing = _get_competing_pending_count(db, booking)

    return _build_booking_detail_response(booking, competing_pending_count=competing)

def get_bookings(
    db: Session,
    filter_params: Optional[BookingFilterParams] = None,
    skip: int = 0,
    limit: int = 100
) -> Tuple[List[BookingDetailResponse], int]:
    """Get list of bookings with optional filters"""
    query = db.query(SlotBooking).options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.manager),
        joinedload(SlotBooking.subcontractor),
        joinedload(SlotBooking.asset),
        joinedload(SlotBooking.booking_group).joinedload(ActivityBookingGroup.activity),
    )
    
    # Apply filters
    if filter_params:
        if filter_params.project_id:
            query = query.filter(SlotBooking.project_id == filter_params.project_id)
        
        if filter_params.manager_id:
            query = query.filter(SlotBooking.manager_id == filter_params.manager_id)
        
        if filter_params.subcontractor_id:
            query = query.filter(SlotBooking.subcontractor_id == filter_params.subcontractor_id)
        
        if filter_params.asset_id:
            query = query.filter(SlotBooking.asset_id == filter_params.asset_id)
        
        if filter_params.status:
            status_val = filter_params.status
            if isinstance(status_val, str):
                try:
                    status_val = BookingStatus(status_val.lower())
                except ValueError as exc:
                    raise ValueError(f"Invalid booking status: {status_val}") from exc
            query = query.filter(SlotBooking.status == status_val)
        
        if filter_params.date_from:
            query = query.filter(SlotBooking.booking_date >= filter_params.date_from)
        
        if filter_params.date_to:
            query = query.filter(SlotBooking.booking_date <= filter_params.date_to)
    
    # Get total count
    total = query.count()
    
    # Apply pagination and get results
    bookings = query.order_by(
        SlotBooking.booking_date.desc(),
        SlotBooking.start_time.desc()
    ).offset(skip).limit(limit).all()
    
    # Convert to detailed responses
    booking_responses = [_build_booking_detail_response(b) for b in bookings]

    return booking_responses, total

def update_booking(
    db: Session,
    booking_id: UUID,
    booking_update: BookingUpdate,
    updated_by_id: UUID,
    updated_by_role: UserRole,
    comment: Optional[str] = None
) -> Optional[SlotBooking]:
    booking = get_booking(db, booking_id)
    if not booking:
        return None
    
    # Capture old values for audit
    old_values = {
        'booking_date': booking.booking_date,
        'start_time': booking.start_time,
        'end_time': booking.end_time,
        'purpose': booking.purpose,
        'notes': booking.notes,
        'asset_id': booking.asset_id,
        'subcontractor_id': booking.subcontractor_id,
        'status': booking.status
    }
    
    update_data = booking_update.dict(exclude_unset=True)
    
    if 'project_id' in update_data:
        project = db.query(SiteProject).filter(SiteProject.id == update_data['project_id']).first()
        if not project:
            raise ValueError(f"Project with id {update_data['project_id']} not found")
    
    if 'manager_id' in update_data:
        manager = db.query(User).filter(User.id == update_data['manager_id']).first()
        if not manager:
            raise ValueError(f"Manager with id {update_data['manager_id']} not found")
    
    if 'subcontractor_id' in update_data:
        subcontractor = db.query(Subcontractor).filter(Subcontractor.id == update_data['subcontractor_id']).first()
        if not subcontractor:
            raise ValueError(f"Subcontractor with id {update_data['subcontractor_id']} not found")
        project = db.query(SiteProject).filter(SiteProject.id == booking.project_id).first()
        if project and not any(str(s.id) == str(subcontractor.id) for s in project.subcontractors):
            raise BookingValidationError(
                f"Subcontractor {update_data['subcontractor_id']} is not assigned to project {booking.project_id}"
            )
    
    if 'status' in update_data and isinstance(update_data['status'], str):
        raw_status = update_data['status']
        try:
            update_data['status'] = BookingStatus(raw_status.lower())
        except ValueError as exc:
            raise ValueError(f"Invalid booking status: {raw_status}") from exc

    target_asset_id = update_data.get('asset_id', booking.asset_id)
    target_date = update_data.get('booking_date', booking.booking_date)
    target_start_time = update_data.get('start_time', booking.start_time)
    target_end_time = update_data.get('end_time', booking.end_time)
    previous_asset = db.query(Asset).filter(Asset.id == booking.asset_id).first()
    previous_asset_type = (
        _normalize_asset_type_value(previous_asset.canonical_type)
        if previous_asset
        else None
    )
    target_status = update_data.get('status', booking.status)
    status_changed = target_status != booking.status
    other_changes = (
        target_asset_id != booking.asset_id
        or target_date != booking.booking_date
        or target_start_time != booking.start_time
        or target_end_time != booking.end_time
    )
    requires_slot_validation = other_changes or (
        status_changed
        and target_status not in {BookingStatus.CANCELLED, BookingStatus.DENIED, BookingStatus.COMPLETED}
    )

    if requires_slot_validation:
        asset = db.query(Asset).filter(Asset.id == target_asset_id).first()
        if not asset:
            raise BookingValidationError(f"Asset with id {target_asset_id} not found")
        sync_maintenance_status(db, asset)
        _ensure_asset_planning_ready(asset)
        if asset.status in (AssetStatus.MAINTENANCE, AssetStatus.RETIRED):
            raise BookingValidationError(f"Asset is not available (status: {asset.status.value})")

        if asset.maintenance_start_date and asset.maintenance_end_date:
            if asset.maintenance_start_date <= target_date <= asset.maintenance_end_date:
                raise BookingValidationError(
                    f"Asset is under scheduled maintenance from "
                    f"{asset.maintenance_start_date} to {asset.maintenance_end_date}"
                )

        conflict_check = BookingConflictCheck(
            asset_id=asset.id,
            booking_date=target_date,
            start_time=target_start_time,
            end_time=target_end_time,
            exclude_booking_id=booking_id,
        )
        conflicts = check_booking_conflicts(db, conflict_check)
        if conflicts.has_confirmed_conflict:
            raise BookingValidationError("Updated booking would conflict with a confirmed reservation")

        if target_status == BookingStatus.PENDING and not conflicts.can_request:
            raise BookingValidationError(
                "Limit reached for this time slot. Choose another slot or contact the manager."
            )
    
    for field, value in update_data.items():
        setattr(booking, field, value)
    
    booking.updated_at = datetime.now(timezone.utc)
    
    # Build changes dict for audit
    new_values = {
        'booking_date': booking.booking_date,
        'start_time': booking.start_time,
        'end_time': booking.end_time,
        'purpose': booking.purpose,
        'notes': booking.notes,
        'asset_id': booking.asset_id,
        'subcontractor_id': booking.subcontractor_id,
        'status': booking.status
    }
    
    changes = build_changes_dict(old_values, new_values)
    
    # Determine action type
    action = BookingAuditAction.UPDATED
    if any(key in update_data for key in ['booking_date', 'start_time', 'end_time']):
        action = BookingAuditAction.RESCHEDULED
    
    # Log the update
    log_booking_audit(
        db,
        actor_id=updated_by_id,
        actor_role=updated_by_role,
        action=action,
        booking_id=booking_id,
        from_status=old_values['status'] if 'status' in update_data else None,
        to_status=booking.status if 'status' in update_data else None,
        changes=changes,
        comment=comment
    )

    if old_values["status"] != BookingStatus.CONFIRMED and booking.status == BookingStatus.CONFIRMED:
        _mark_matching_lookahead_notifications_acted(db, booking)

    booking_group = None
    if booking.booking_group_id:
        booking_group = (
            db.query(ActivityBookingGroup)
            .filter(ActivityBookingGroup.id == booking.booking_group_id)
            .first()
        )
    current_asset = db.query(Asset).filter(Asset.id == booking.asset_id).first()
    current_asset_type = (
        _normalize_asset_type_value(current_asset.canonical_type)
        if current_asset
        else None
    )
    _mark_booking_group_modified_if_needed(
        booking_group,
        booking=booking,
        previous_date=old_values["booking_date"],
        previous_start_time=old_values["start_time"],
        previous_end_time=old_values["end_time"],
        previous_subcontractor_id=old_values["subcontractor_id"],
        previous_asset_type=previous_asset_type,
        current_asset_type=current_asset_type,
    )
    
    db.commit()
    db.refresh(booking)
    
    return booking

def update_booking_status(
    db: Session,
    booking_id: UUID,
    new_status: BookingStatus,
    updated_by_id: UUID,
    updated_by_role: UserRole,
    comment: Optional[str] = None
) -> Tuple[Optional[SlotBooking], List[UUID]]:
    """Update a booking's status. Returns (booking, auto_denied_ids).

    When confirming a PENDING booking:
    1. Guard against an existing active booking on the same slot.
    2. Auto-deny all other PENDING bookings on the same slot.
    """
    booking = (
        db.query(SlotBooking)
        .filter(SlotBooking.id == booking_id)
        .with_for_update()
        .first()
    )
    if not booking:
        return None, []

    auto_denied_ids: List[UUID] = []

    booking_asset_id = booking.asset_id
    booking_date = booking.booking_date
    booking_start_time = booking.start_time
    booking_end_time = booking.end_time

    # Lock all overlapping rows for this slot in a deterministic order so
    # conflict check + status transition are atomic and race-safe.
    locked_slot_rows = (
        db.query(SlotBooking)
        .filter(
            SlotBooking.asset_id == booking_asset_id,
            SlotBooking.booking_date == booking_date,
            _overlapping_time_filter(booking_start_time, booking_end_time),
        )
        .order_by(SlotBooking.id)
        .with_for_update()
        .all()
    )

    old_status = booking.status

    # --- Confirmation guard + auto-deny (atomic on locked rows) ---
    if new_status == BookingStatus.CONFIRMED and old_status != BookingStatus.CONFIRMED:
        asset = db.query(Asset).filter(Asset.id == booking_asset_id).first()
        if not asset:
            raise BookingValidationError(f"Asset with id {booking_asset_id} not found")
        _ensure_asset_planning_ready(asset)

    if new_status == BookingStatus.CONFIRMED and old_status == BookingStatus.PENDING:
        active_conflict = next(
            (
                row for row in locked_slot_rows
                if row.id != booking_id and row.status in _ACTIVE_STATUSES
            ),
            None,
        )
        if active_conflict:
            raise BookingValidationError(
                "Cannot confirm: a confirmed booking already exists for this time slot"
            )

        now = datetime.now(timezone.utc)
        for row in locked_slot_rows:
            if row.id == booking_id or row.status != BookingStatus.PENDING:
                continue

            row.status = BookingStatus.DENIED
            row.updated_at = now
            auto_denied_ids.append(row.id)
            log_booking_audit(
                db,
                actor_id=updated_by_id,
                actor_role=updated_by_role,
                action=BookingAuditAction.DENIED,
                booking_id=row.id,
                from_status=BookingStatus.PENDING,
                to_status=BookingStatus.DENIED,
                comment="Auto-denied: another booking on this slot was confirmed",
            )

    booking.status = new_status
    booking.updated_at = datetime.now(timezone.utc)

    # Determine action based on status transition
    action = BookingAuditAction.UPDATED
    if new_status == BookingStatus.CONFIRMED and old_status == BookingStatus.PENDING:
        action = BookingAuditAction.APPROVED
    elif new_status == BookingStatus.DENIED:
        action = BookingAuditAction.DENIED
    elif new_status == BookingStatus.CANCELLED:
        action = BookingAuditAction.CANCELLED

    # Log the status change
    log_booking_audit(
        db,
        actor_id=updated_by_id,
        actor_role=updated_by_role,
        action=action,
        booking_id=booking_id,
        from_status=old_status,
        to_status=new_status,
        comment=comment
    )

    if old_status != BookingStatus.CONFIRMED and booking.status == BookingStatus.CONFIRMED:
        _mark_matching_lookahead_notifications_acted(db, booking)

    db.commit()
    db.refresh(booking)

    return booking, auto_denied_ids

def delete_booking(
    db: Session,
    booking_id: UUID,
    deleted_by_id: UUID,
    deleted_by_role: UserRole,
    comment: Optional[str] = None,
    hard_delete: bool = False,
    ) -> bool:
    """Soft-cancel a booking.

    ``hard_delete`` is accepted for backwards-compatibility but is always
    ignored — bookings are never hard-deleted to preserve audit history.
    """
    if hard_delete:
        warnings.warn(
            "hard_delete=True passed to delete_booking but hard delete is disabled; "
            "the booking will be soft-cancelled instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    booking = get_booking(db, booking_id)
    if not booking:
        return False

    old_status = booking.status

    _terminal = {BookingStatus.DENIED, BookingStatus.COMPLETED, BookingStatus.CANCELLED}
    if old_status in _terminal:
        return False

    # Soft delete (cancel) — hard delete is disabled to preserve audit history
    booking.status = BookingStatus.CANCELLED
    booking.updated_at = datetime.now(timezone.utc)

    log_booking_audit(
        db,
        actor_id=deleted_by_id,
        actor_role=deleted_by_role,
        action=BookingAuditAction.CANCELLED,
        booking_id=booking_id,
        from_status=old_status,
        to_status=BookingStatus.CANCELLED,
        comment=comment
    )
    
    db.commit()
    return True

def check_booking_conflicts(
    db: Session,
    conflict_check: BookingConflictCheck,
    asset: Optional[Asset] = None,
) -> BookingConflictResponse:
    """Check for conflicts on a slot. Returns enriched response with
    confirmed-conflict flag, pending count, capacity, and can_request."""

    base_filters = [
        SlotBooking.asset_id == conflict_check.asset_id,
        SlotBooking.booking_date == conflict_check.booking_date,
        _overlapping_time_filter(conflict_check.start_time, conflict_check.end_time),
    ]

    if conflict_check.exclude_booking_id:
        base_filters.append(SlotBooking.id != conflict_check.exclude_booking_id)

    # --- confirmed / active conflicts ---
    confirmed_bookings = (
        db.query(SlotBooking)
        .filter(*base_filters, SlotBooking.status.in_(_ACTIVE_STATUSES))
        .all()
    )

    # --- pending count ---
    pending_count = (
        db.query(func.count(SlotBooking.id))
        .filter(*base_filters, SlotBooking.status == BookingStatus.PENDING)
        .scalar()
    ) or 0

    # --- asset capacity ---
    asset = _resolve_booking_asset(
        db,
        conflict_check.asset_id,
        asset=asset,
    )
    capacity = asset.pending_booking_capacity if asset else 5

    has_confirmed = len(confirmed_bookings) > 0
    can_request = (not has_confirmed) and (pending_count < capacity)

    # Build response-compatible dicts for confirmed conflicts only
    conflicts = []
    for booking in confirmed_bookings:
        conflicts.append({
            "id": booking.id,
            "start_time": booking.start_time,
            "end_time": booking.end_time,
            "status": booking.status,
            "subcontractor_id": booking.subcontractor_id,
            "manager_id": booking.manager_id,
            "booking_date": booking.booking_date,
            "project_id": booking.project_id,
            "asset_id": booking.asset_id,
            "created_at": booking.created_at,
            "updated_at": booking.updated_at,
            "purpose": booking.purpose,
            "notes": booking.notes
        })

    return BookingConflictResponse(
        has_conflict=has_confirmed,
        has_confirmed_conflict=has_confirmed,
        pending_count=pending_count,
        pending_capacity=capacity,
        can_request=can_request,
        conflicting_bookings=conflicts,
        conflict_count=len(confirmed_bookings)
    )

def get_booking_statistics(
    db: Session,
    project_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    user_id: Optional[UUID] = None
) -> BookingStatistics:
    _filters = dict(project_id=project_id, user_id=user_id, date_from=date_from, date_to=date_to)

    status_counts_query = _apply_booking_stats_filters(
        db.query(SlotBooking.status, func.count(SlotBooking.id).label("count")),
        **_filters,
    )
    status_counts = status_counts_query.group_by(SlotBooking.status).all()

    status_dict = {status.value: count for status, count in status_counts}
    total_bookings = sum(status_dict.values())

    # Busiest Day
    busiest_day_query = _apply_booking_stats_filters(
        db.query(SlotBooking.booking_date, func.count(SlotBooking.id).label("count")),
        **_filters,
    )
    busiest_day_result = busiest_day_query.group_by(
        SlotBooking.booking_date
    ).order_by(
        func.count(SlotBooking.id).desc()
    ).first()

    # Most Booked Asset
    most_booked_asset_query = _apply_booking_stats_filters(
        db.query(Asset, func.count(SlotBooking.id).label("count")).join(
            SlotBooking, SlotBooking.asset_id == Asset.id
        ),
        **_filters,
    )
    
    most_booked_asset_result = most_booked_asset_query.group_by(
        Asset.id
    ).order_by(
        func.count(SlotBooking.id).desc()
    ).first()
    
    most_booked_asset = None
    if most_booked_asset_result:
        asset, count = most_booked_asset_result
        most_booked_asset = {
            "id": asset.id,
            "name": asset.name,
            "asset_code": asset.asset_code,
            "type": asset.type,
            "booking_count": count
        }
    
    utilized_bookings = (
        status_dict.get(BookingStatus.CONFIRMED.value, 0) +
        status_dict.get(BookingStatus.COMPLETED.value, 0) +
        status_dict.get(BookingStatus.IN_PROGRESS.value, 0)
    )
    utilization_rate = (utilized_bookings / total_bookings * 100) if total_bookings > 0 else 0
    
    return BookingStatistics(
        total_bookings=total_bookings,
        pending_bookings=status_dict.get(BookingStatus.PENDING.value, 0),
        confirmed_bookings=status_dict.get(BookingStatus.CONFIRMED.value, 0),
        in_progress_bookings=status_dict.get(BookingStatus.IN_PROGRESS.value, 0),
        completed_bookings=status_dict.get(BookingStatus.COMPLETED.value, 0),
        cancelled_bookings=status_dict.get(BookingStatus.CANCELLED.value, 0),
        utilization_rate=round(utilization_rate, 2),
        busiest_day=busiest_day_result[0] if busiest_day_result else None,
        busiest_day_count=busiest_day_result[1] if busiest_day_result else 0,
        most_booked_asset=most_booked_asset,
        period={
            "date_from": date_from,
            "date_to": date_to
        }
    )

def get_user_upcoming_bookings(
    db: Session,
    user_id: UUID,
    user_role: Optional[UserRole] = None,
    limit: int = 10
) -> List[BookingDetailResponse]:
    """Get upcoming bookings for a specific user (manager or subcontractor)"""
    today = date.today()
    current_time = datetime.now(timezone.utc).time()

    # Determine filter based on user role
    if user_role == UserRole.SUBCONTRACTOR:
        user_filter = SlotBooking.subcontractor_id == user_id
    else:
        user_filter = SlotBooking.manager_id == user_id

    bookings = db.query(SlotBooking).options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.manager),
        joinedload(SlotBooking.subcontractor),
        joinedload(SlotBooking.asset),
        joinedload(SlotBooking.booking_group).joinedload(ActivityBookingGroup.activity),
    ).filter(
        user_filter,
        SlotBooking.status.notin_([BookingStatus.CANCELLED, BookingStatus.COMPLETED]),
        or_(
            SlotBooking.booking_date > today,
            and_(
                SlotBooking.booking_date == today,
                SlotBooking.start_time >= current_time
            )
        )
    ).order_by(
        SlotBooking.booking_date,
        SlotBooking.start_time
    ).limit(limit).all()
    
    booking_responses = [_build_booking_detail_response(b) for b in bookings]

    return booking_responses

def get_calendar_view(
    db: Session,
    date_from: date,
    date_to: date,
    project_id: Optional[UUID] = None,
    asset_id: Optional[UUID] = None,
    manager_id: Optional[UUID] = None,
    subcontractor_id: Optional[UUID] = None
) -> List[BookingCalendarView]:
    query = db.query(SlotBooking).options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.manager),
        joinedload(SlotBooking.subcontractor),
        joinedload(SlotBooking.asset),
        joinedload(SlotBooking.booking_group).joinedload(ActivityBookingGroup.activity),
    )
    
    query = query.filter(
        SlotBooking.booking_date >= date_from,
        SlotBooking.booking_date <= date_to
    )
    
    if project_id:
        query = query.filter(SlotBooking.project_id == project_id)
    
    if asset_id:
        query = query.filter(SlotBooking.asset_id == asset_id)
    
    if manager_id:
        query = query.filter(SlotBooking.manager_id == manager_id)
        
    if subcontractor_id:
        query = query.filter(SlotBooking.subcontractor_id == subcontractor_id)
    
    bookings = query.order_by(
        SlotBooking.booking_date,
        SlotBooking.start_time
    ).all()
    
    bookings_by_date = defaultdict(list)
    for booking in bookings:
        bookings_by_date[booking.booking_date].append(_build_booking_detail_response(booking))
    
    calendar_view = []
    current_date = date_from
    while current_date <= date_to:
        day_bookings = bookings_by_date.get(current_date, [])
        calendar_view.append(BookingCalendarView(
            date=current_date,
            bookings=day_bookings
        ))
        current_date += timedelta(days=1)
    
    return calendar_view

def get_asset_availability(
    db: Session,
    asset_id: UUID,
    check_date: date,
    start_time: Optional[time] = None,
    end_time: Optional[time] = None
) -> List[Dict[str, Any]]:
    bookings = db.query(SlotBooking).filter(
        SlotBooking.asset_id == asset_id,
        SlotBooking.booking_date == check_date,
        SlotBooking.status.notin_([BookingStatus.CANCELLED])
    ).order_by(SlotBooking.start_time).all()
    
    available_slots = []
    work_start = start_time or time(8, 0)
    work_end = end_time or time(18, 0)
    
    if not bookings:
        duration = _calculate_minutes_between(work_start, work_end)
        if duration >= BOOKING_MIN_SLOT_DURATION_MINUTES:
            available_slots.append({
                'start_time': work_start,
                'end_time': work_end,
                'duration_minutes': duration
            })
    else:
        current_time = work_start
        
        for booking in bookings:
            if booking.start_time > current_time:
                duration = _calculate_minutes_between(current_time, booking.start_time)
                if duration >= BOOKING_MIN_SLOT_DURATION_MINUTES:
                    available_slots.append({
                        'start_time': current_time,
                        'end_time': booking.start_time,
                        'duration_minutes': duration
                    })
            current_time = max(current_time, booking.end_time)
        
        if current_time < work_end:
            duration = _calculate_minutes_between(current_time, work_end)
            if duration >= BOOKING_MIN_SLOT_DURATION_MINUTES:
                available_slots.append({
                    'start_time': current_time,
                    'end_time': work_end,
                    'duration_minutes': duration
                })
    
    return available_slots

def get_subcontractor_schedule(
    db: Session,
    subcontractor_id: UUID,
    date_from: date,
    date_to: date
) -> List[SlotBooking]:
    return db.query(SlotBooking).filter(
        SlotBooking.subcontractor_id == subcontractor_id,
        SlotBooking.booking_date >= date_from,
        SlotBooking.booking_date <= date_to,
        SlotBooking.status != BookingStatus.CANCELLED
    ).order_by(
        SlotBooking.booking_date,
        SlotBooking.start_time
    ).all()

def get_project_bookings_summary(
    db: Session,
    project_id: UUID
) -> Dict[str, Any]:
    bookings = db.query(SlotBooking).filter(
        SlotBooking.project_id == project_id
    ).all()
    
    total = len(bookings)
    by_status = {}
    by_asset = {}
    by_subcontractor = {}
    
    for booking in bookings:
        status_key = booking.status.value if booking.status else 'unknown'
        by_status[status_key] = by_status.get(status_key, 0) + 1
        
        if booking.asset_id:
            by_asset[str(booking.asset_id)] = by_asset.get(str(booking.asset_id), 0) + 1
        
        if booking.subcontractor_id:
            by_subcontractor[str(booking.subcontractor_id)] = by_subcontractor.get(str(booking.subcontractor_id), 0) + 1
    
    return {
        "total_bookings": total,
        "by_status": by_status,
        "by_asset": by_asset,
        "by_subcontractor": by_subcontractor,
        "active_bookings": by_status.get(BookingStatus.CONFIRMED.value, 0) + by_status.get(BookingStatus.IN_PROGRESS.value, 0),
        "completed_bookings": by_status.get(BookingStatus.COMPLETED.value, 0),
        "cancelled_bookings": by_status.get(BookingStatus.CANCELLED.value, 0)
    }

def cancel_expired_bookings(db: Session) -> int:
    now = datetime.now(timezone.utc)
    today = now.date()
    current_time = now.time()
    
    expired_bookings = db.query(SlotBooking).filter(
        SlotBooking.status == BookingStatus.PENDING,
        or_(
            SlotBooking.booking_date < today,
            and_(
                SlotBooking.booking_date == today,
                SlotBooking.start_time < current_time
            )
        )
    ).all()
    
    count = 0
    for booking in expired_bookings:
        booking.status = BookingStatus.CANCELLED
        booking.notes = (booking.notes or '') + '\n[Auto-cancelled: Booking time passed]'
        booking.updated_at = now
        count += 1
    
    if count > 0:
        db.commit()
    
    return count

def _calculate_minutes_between(start_time: time, end_time: time) -> int:
    start_delta = timedelta(hours=start_time.hour, minutes=start_time.minute)
    end_delta = timedelta(hours=end_time.hour, minutes=end_time.minute)
    return int((end_delta - start_delta).total_seconds() / 60)
