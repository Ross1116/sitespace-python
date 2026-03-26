# crud/slot_booking.py
import warnings
from typing import Optional, List, Dict, Any, Tuple, Union
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from sqlalchemy import and_, or_, func, case
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.elements import ColumnElement
from collections import defaultdict

from ..core.constants import BOOKING_MIN_SLOT_DURATION_MINUTES
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
    BookingResponse
)
from app.crud.booking_audit import log_booking_audit, build_changes_dict
from .asset import sync_maintenance_status
from ..services.metadata_confidence_service import asset_is_planning_ready


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
    return BookingDetailResponse(
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
) -> Tuple[UUID, Optional[UUID], BookingStatus]:
    """Resolve manager_id, subcontractor_id, and initial booking status from the actor's role.

    Returns ``(manager_id, subcontractor_id, booking_status)``.

    Raises ``BookingValidationError`` for any invalid/missing entity reference.
    """
    if actor_role in [UserRole.ADMIN, UserRole.MANAGER]:
        manager_id = provided_manager_id or actor_id
        subcontractor_id = provided_subcontractor_id
        booking_status = BookingStatus.CONFIRMED

        manager = db.query(User).filter(User.id == manager_id).first()
        if not manager:
            raise BookingValidationError(f"Manager with id {manager_id} not found")

        project = (
            db.query(SiteProject)
            .options(joinedload(SiteProject.managers), joinedload(SiteProject.subcontractors))
            .filter(SiteProject.id == project_id)
            .first()
        )
        if not project:
            raise BookingValidationError(f"Project with id {project_id} not found")
        if not any(str(m.id) == str(manager_id) for m in project.managers):
            raise BookingValidationError(f"Manager {manager_id} is not a member of project {project_id}")

        if subcontractor_id:
            subcontractor = db.query(Subcontractor).filter(Subcontractor.id == subcontractor_id).first()
            if not subcontractor:
                raise BookingValidationError(f"Subcontractor with id {subcontractor_id} not found")
            if not any(str(s.id) == str(subcontractor_id) for s in project.subcontractors):
                raise BookingValidationError(f"Subcontractor {subcontractor_id} is not assigned to project {project_id}")

    elif actor_role == UserRole.SUBCONTRACTOR:
        subcontractor_id = actor_id

        if provided_subcontractor_id and provided_subcontractor_id != actor_id:
            raise BookingValidationError("Subcontractors can only create bookings for themselves")

        subcontractor = db.query(Subcontractor).filter(Subcontractor.id == subcontractor_id).first()
        if not subcontractor:
            raise BookingValidationError(f"Subcontractor with id {subcontractor_id} not found")

        project_with_members = (
            db.query(SiteProject)
            .options(joinedload(SiteProject.managers), joinedload(SiteProject.subcontractors))
            .filter(SiteProject.id == project_id)
            .first()
        )
        if not project_with_members:
            raise BookingValidationError(f"Project with id {project_id} not found")
        if not any(str(s.id) == str(subcontractor_id) for s in project_with_members.subcontractors):
            raise BookingValidationError(f"Subcontractor {subcontractor_id} is not assigned to project {project_id}")

        if provided_manager_id:
            manager_id = provided_manager_id
            manager = db.query(User).filter(User.id == manager_id).first()
            if not manager:
                raise BookingValidationError(f"Manager with id {manager_id} not found")
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
    comment: Optional[str] = None
) -> SlotBooking:
    """
    Create a new booking in the database with role-based status.
    """
    
    # Validate that all referenced entities exist
    project = db.query(SiteProject).filter(SiteProject.id == booking_data.project_id).first()
    if not project:
        raise BookingValidationError(f"Project with id {booking_data.project_id} not found")
    
    asset = db.query(Asset).filter(Asset.id == booking_data.asset_id).first()
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
    )

    # Create the booking
    db_booking = SlotBooking(
        project_id=booking_data.project_id,
        manager_id=manager_id,
        subcontractor_id=subcontractor_id,
        asset_id=booking_data.asset_id,
        booking_date=booking_data.booking_date,
        start_time=booking_data.start_time,
        end_time=booking_data.end_time,
        purpose=booking_data.purpose,
        notes=booking_data.notes,
        status=booking_status
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

    # If this booking is confirmed on creation (manager/admin), auto-deny
    # overlapping pending requests for the same slot.
    _auto_deny_competing_pending_bookings(
        db,
        booking=db_booking,
        actor_id=created_by_id,
        actor_role=created_by_role,
    )
    
    db.commit()
    db.refresh(db_booking)
    
    return db_booking

def create_bulk_bookings(
    db: Session,
    bulk_data: BulkBookingCreate,
    created_by_id: UUID,
    created_by_role: UserRole,
    comment: Optional[str] = None
) -> List[SlotBooking]:
    """
    Create multiple bookings at once with role-based status.
    """
    bookings = []
    booking_requests = []
    failed_bookings = []
    
    # Validate base entities exist
    project = db.query(SiteProject).filter(SiteProject.id == bulk_data.project_id).first()
    if not project:
        raise ValueError(f"Project with id {bulk_data.project_id} not found")
    
    # Determine manager_id, subcontractor_id, and status based on role
    manager_id, subcontractor_id, booking_status = _resolve_booking_actor(
        db=db,
        actor_id=created_by_id,
        actor_role=created_by_role,
        provided_manager_id=bulk_data.manager_id,
        provided_subcontractor_id=bulk_data.subcontractor_id,
        project_id=bulk_data.project_id,
    )
    
    try:
        # Resolve maintenance status for all assets upfront (before any bookings are flushed)
        for aid in set(bulk_data.asset_ids):
            a = db.query(Asset).filter(Asset.id == aid).first()
            if a:
                sync_maintenance_status(db, a)

        seen_booking_pairs: set[tuple[UUID, date]] = set()

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
            db_booking = SlotBooking(
                project_id=bulk_data.project_id,
                manager_id=manager_id,
                subcontractor_id=subcontractor_id,
                asset_id=asset_id,
                booking_date=booking_date,
                start_time=bulk_data.start_time,
                end_time=bulk_data.end_time,
                purpose=bulk_data.purpose,
                notes=bulk_data.notes,
                status=booking_status
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

            # If this booking is confirmed on creation (manager/admin),
            # auto-deny overlapping pending requests for the same slot.
            _auto_deny_competing_pending_bookings(
                db,
                booking=db_booking,
                actor_id=created_by_id,
                actor_role=created_by_role,
            )

            bookings.append(db_booking)

        if bookings:
            db.commit()
            for booking in bookings:
                db.refresh(booking)

    except Exception:
        db.rollback()
        raise

    return bookings

def get_booking(db: Session, booking_id: UUID) -> Optional[SlotBooking]:
    return db.query(SlotBooking).filter(SlotBooking.id == booking_id).first()

def get_booking_with_details(db: Session, booking_id: UUID) -> Optional[SlotBooking]:
    return db.query(SlotBooking).options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.manager),
        joinedload(SlotBooking.subcontractor),
        joinedload(SlotBooking.asset)
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
        joinedload(SlotBooking.asset)
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
    target_status = update_data.get('status', booking.status)
    requires_slot_validation = (
        target_asset_id != booking.asset_id
        or target_date != booking.booking_date
        or target_start_time != booking.start_time
        or target_end_time != booking.end_time
        or target_status != booking.status
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
    if new_status == BookingStatus.CONFIRMED and old_status == BookingStatus.PENDING:
        active_conflict = next(
            (
                row for row in locked_slot_rows
                if row.id != booking_id and row.status in _ACTIVE_STATUSES
            ),
            None,
        )
        if active_conflict:
            raise ValueError(
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
    conflict_check: BookingConflictCheck
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
    asset = db.query(Asset).filter(Asset.id == conflict_check.asset_id).first()
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
        joinedload(SlotBooking.asset)
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
        joinedload(SlotBooking.asset)
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
