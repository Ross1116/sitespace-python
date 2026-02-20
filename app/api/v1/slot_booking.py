from typing import Optional, List, Dict, Any, Union
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Path, Body
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.core.security import get_current_active_user, get_user_role, get_entity_id
from app.models.subcontractor import Subcontractor
from app.models.user import User
from app.models.slot_booking import SlotBooking
from app.schemas.slot_booking import (
    BookingCreate,
    BookingUpdate,
    BookingResponse,
    BookingDetailResponse,
    BookingListResponse,
    BookingFilterParams,
    BookingCalendarView,
    BookingStatistics,
    BulkBookingCreate,
    BookingConflictCheck,
    BookingConflictResponse,
    BookingStatusUpdate,
    BookingDeleteRequest,
    BookingDuplicateRequest
)
from app.schemas.base import MessageResponse
from app.schemas.enums import BookingStatus, UserRole
from app.crud import slot_booking as booking_crud
from app.crud import site_project as project_crud
from app.core.email import notify_booking_change

router = APIRouter(prefix="/bookings", tags=["Bookings"])


# ==================== Helper Functions ====================

def validate_date_range(date_from: Optional[date], date_to: Optional[date]) -> None:
    """Validate that date_from is not after date_to"""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Start date cannot be after end date"
        )


def check_booking_access(
    db: Session,
    booking: SlotBooking,
    entity: Union[User, Subcontractor],
    require_owner: bool = False
) -> None:
    """Check if user/subcontractor has access to the booking"""
    
    user_role = get_user_role(entity)
    user_id = get_entity_id(entity)
    
    # 1. Admins always have access
    if user_role == UserRole.ADMIN:
        return
    
    # 2. Check Direct Ownership (Creator)
    if booking.manager_id == user_id:
        return
        
    if user_role == UserRole.SUBCONTRACTOR and booking.subcontractor_id == user_id:
        return

    # 3. Check Project Manager Access (Managers only)
    if booking.project_id and user_role == UserRole.MANAGER:
        if project_crud.is_project_manager(db, booking.project_id, user_id):
            return
    
    # 4. If we get here, we haven't matched any access rights
    if require_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the booking owner or project manager can perform this action"
        )
    
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You don't have access to this booking"
    )


def validate_booking_times(start_time, end_time) -> None:
    """Validate that start_time is before end_time"""
    from datetime import time as time_type
    
    # Handle both string and time objects
    if isinstance(start_time, str):
        # Handle different time formats
        if 'T' in start_time or 'Z' in start_time:
            start_time = start_time.replace('Z', '').replace('T', '')
            if '.' in start_time:
                start_time = start_time.split('.')[0]
        
        if len(start_time.split(':')) == 3:
            start = datetime.strptime(start_time, "%H:%M:%S").time()
        else:
            start = datetime.strptime(start_time, "%H:%M").time()
    elif isinstance(start_time, time_type):
        start = start_time
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid start_time type: {type(start_time)}"
        )
    
    if isinstance(end_time, str):
        if 'T' in end_time or 'Z' in end_time:
            end_time = end_time.replace('Z', '').replace('T', '')
            if '.' in end_time:
                end_time = end_time.split('.')[0]
        
        if len(end_time.split(':')) == 3:
            end = datetime.strptime(end_time, "%H:%M:%S").time()
        else:
            end = datetime.strptime(end_time, "%H:%M").time()
    elif isinstance(end_time, time_type):
        end = end_time
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid end_time type: {type(end_time)}"
        )
    
    if start >= end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Start time must be before end time"
        )


# ==================== Main Endpoints ====================

@router.post("/", response_model=BookingDetailResponse, status_code=status.HTTP_201_CREATED)
def create_booking(
    booking_data: BookingCreate,
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Create a new booking.
    
    **Role-based booking:**
    - **Managers/Admins**: Bookings are automatically CONFIRMED
    - **Subcontractors**: Bookings are PENDING (require manager approval)
    
    - Validates time slots and booking date
    - Checks for conflicts with existing bookings
    - Optional comment for audit trail
    """
    try:
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)
        
        # Validate booking times
        validate_booking_times(booking_data.start_time, booking_data.end_time)
        
        # Validate booking date is not in the past
        if booking_data.booking_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot create bookings for past dates"
            )
        
        # Set status based on role
        if user_role in [UserRole.MANAGER, UserRole.ADMIN]:
            booking_data.status = BookingStatus.CONFIRMED
        else:
            booking_data.status = BookingStatus.PENDING
        
        # Role-specific validations
        if user_role == UserRole.SUBCONTRACTOR:
            if booking_data.subcontractor_id and booking_data.subcontractor_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Subcontractors can only create bookings for themselves"
                )
            
            from app.crud import subcontractor as subcontractor_crud
            if not subcontractor_crud.is_subcontractor_assigned(
                db, 
                booking_data.project_id, 
                user_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not assigned to this project"
                )
        
        elif user_role in [UserRole.MANAGER, UserRole.ADMIN]:
            if booking_data.project_id:
                if user_role != UserRole.ADMIN:
                    if not project_crud.has_project_access(db, booking_data.project_id, user_id):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="You don't have access to this project"
                        )
            
            if booking_data.subcontractor_id:
                from app.crud import subcontractor as subcontractor_crud
                if not subcontractor_crud.is_subcontractor_assigned(
                    db, 
                    booking_data.project_id, 
                    booking_data.subcontractor_id
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Selected subcontractor is not assigned to this project"
                    )
        
        # Check for conflicts before creating
        conflict_check = BookingConflictCheck(
            asset_id=booking_data.asset_id,
            booking_date=booking_data.booking_date,
            start_time=booking_data.start_time,
            end_time=booking_data.end_time
        )

        conflicts = booking_crud.check_booking_conflicts(db, conflict_check)

        # Confirmed conflict blocks everyone
        if conflicts.has_confirmed_conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A confirmed booking already exists for this time slot"
            )

        # Subcontractors must also pass pending capacity check
        if user_role == UserRole.SUBCONTRACTOR and not conflicts.can_request:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Limit reached for this time slot. Choose another slot or contact the manager."
            )

        # Create the booking with audit logging
        booking = booking_crud.create_booking(
            db,
            booking_data,
            created_by_id=user_id,
            created_by_role=user_role,
            comment=booking_data.comment  # User-provided comment for audit
        )

        notify_booking_change(db, booking.id, "created", user_id)

        return booking_crud.get_booking_detail(db, booking.id)
        
    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid booking data. Please check asset and project IDs"
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid booking data"
        )
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create booking"
        )


@router.post("/bulk", response_model=List[BookingDetailResponse], status_code=status.HTTP_201_CREATED)
def create_bulk_bookings(
    bulk_data: BulkBookingCreate,
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> List[BookingDetailResponse]:
    """
    Create multiple bookings at once.
    
    - Creates bookings for multiple assets and/or dates
    - Validates all bookings before creating any
    - Role-based status: CONFIRMED for managers, PENDING for subcontractors
    - Optional comment applies to all created bookings
    - Returns list of created bookings
    """
    try:
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)
        
        # Validate all bookings first
        for booking_date in bulk_data.booking_dates:
            if booking_date < date.today():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot create bookings for past date: {booking_date}"
                )
        
        # Validate times
        validate_booking_times(bulk_data.start_time, bulk_data.end_time)
        
        # Role-specific validations
        if user_role == UserRole.SUBCONTRACTOR:
            if bulk_data.subcontractor_id and bulk_data.subcontractor_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Subcontractors can only create bookings for themselves"
                )
            
            from app.crud import subcontractor as subcontractor_crud
            if not subcontractor_crud.is_subcontractor_assigned(
                db, 
                bulk_data.project_id, 
                user_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not assigned to this project"
                )
        
        elif user_role in [UserRole.MANAGER, UserRole.ADMIN]:
            if bulk_data.project_id:
                if user_role != UserRole.ADMIN:
                    if not project_crud.has_project_access(db, bulk_data.project_id, user_id):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="You don't have access to this project"
                        )
            
            if bulk_data.subcontractor_id:
                from app.crud import subcontractor as subcontractor_crud
                if not subcontractor_crud.is_subcontractor_assigned(
                    db, 
                    bulk_data.project_id, 
                    bulk_data.subcontractor_id
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Selected subcontractor is not assigned to this project"
                    )
        
        # Check for conflicts for all combinations
        for asset_id in bulk_data.asset_ids:
            for booking_date in bulk_data.booking_dates:
                conflict_check = BookingConflictCheck(
                    asset_id=asset_id,
                    booking_date=booking_date,
                    start_time=bulk_data.start_time,
                    end_time=bulk_data.end_time
                )

                conflicts = booking_crud.check_booking_conflicts(db, conflict_check)
                if conflicts.has_confirmed_conflict:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"A confirmed booking already exists for asset on {booking_date}"
                    )
                if user_role == UserRole.SUBCONTRACTOR and not conflicts.can_request:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Limit reached for asset on {booking_date}. Choose another slot or contact the manager."
                    )
        
        # Create all bookings with audit logging
        bookings = booking_crud.create_bulk_bookings(
            db,
            bulk_data,
            created_by_id=user_id,
            created_by_role=user_role,
            comment=bulk_data.comment  # User-provided comment for audit
        )

        for b in bookings:
            notify_booking_change(db, b.id, "created", user_id)

        return [booking_crud.get_booking_detail(db, b.id) for b in bookings]
        
    except HTTPException:
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid bulk booking request"
        )
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create bulk bookings"
        )


@router.get("/", response_model=BookingListResponse)
def get_bookings(
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Number of items to return"),
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    manager_id: Optional[UUID] = Query(None, description="Filter by manager ID"),
    subcontractor_id: Optional[UUID] = Query(None, description="Filter by subcontractor ID"),
    asset_id: Optional[UUID] = Query(None, description="Filter by asset ID"),
    booking_status: Optional[BookingStatus] = Query(None, description="Filter by booking status"),
    date_from: Optional[date] = Query(None, description="Filter from date"),
    date_to: Optional[date] = Query(None, description="Filter to date"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingListResponse:
    """
    Get list of bookings with optional filters.
    
    **Role-based filtering:**
    - **Admins**: See all bookings
    - **Managers**: See bookings for their projects
    - **Subcontractors**: See only their own bookings
    """
    try:
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)
        
        validate_date_range(date_from, date_to)
        
        filter_params = BookingFilterParams(
            project_id=project_id,
            manager_id=manager_id,
            subcontractor_id=subcontractor_id,
            asset_id=asset_id,
            status=booking_status,
            date_from=date_from,
            date_to=date_to
        )
        
        # Apply role-based filters
        if user_role == UserRole.SUBCONTRACTOR:
            filter_params.subcontractor_id = user_id
        elif user_role == UserRole.MANAGER:
            if not manager_id and not project_id:
                filter_params.manager_id = user_id
        
        bookings, total = booking_crud.get_bookings(
            db, 
            filter_params=filter_params,
            skip=skip,
            limit=limit
        )
        
        return BookingListResponse(
            bookings=bookings,
            total=total,
            skip=skip,
            limit=limit,
            has_more=(skip + limit) < total
        )
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve bookings"
        )


@router.get("/calendar", response_model=List[BookingCalendarView])
def get_calendar_view(
    date_from: date = Query(..., description="Start date for calendar view"),
    date_to: date = Query(..., description="End date for calendar view"),
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    asset_id: Optional[UUID] = Query(None, description="Filter by asset ID"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
):
    """
    Get bookings in calendar view format.
    """
    try:
        validate_date_range(date_from, date_to)
        
        max_days = 90
        delta = (date_to - date_from).days
        if delta > max_days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Date range cannot exceed {max_days} days"
            )
        
        user_id = get_entity_id(current_entity)
        user_role = get_user_role(current_entity)

        if project_id:
            has_access = False
            if user_role == UserRole.ADMIN:
                has_access = True
            elif user_role == UserRole.SUBCONTRACTOR:
                from app.crud import subcontractor as subcontractor_crud
                has_access = subcontractor_crud.is_subcontractor_assigned(db, project_id, user_id)
            else:
                has_access = project_crud.has_project_access(db, project_id, user_id)

            if not has_access:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this project"
                )
        
        filter_manager_id = None
        filter_subcontractor_id = None

        if not project_id:
            if user_role == UserRole.MANAGER:
                filter_manager_id = user_id
            elif user_role == UserRole.SUBCONTRACTOR:
                filter_subcontractor_id = user_id

        calendar_data = booking_crud.get_calendar_view(
            db,
            date_from=date_from,
            date_to=date_to,
            project_id=project_id,
            asset_id=asset_id,
            manager_id=filter_manager_id,
            subcontractor_id=filter_subcontractor_id
        )
        
        return calendar_data
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve calendar view"
        )


@router.get("/statistics", response_model=BookingStatistics)
def get_booking_statistics(
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    date_from: Optional[date] = Query(None, description="Statistics from date"),
    date_to: Optional[date] = Query(None, description="Statistics to date"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingStatistics:
    """
    Get booking statistics and analytics.
    """
    try:
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)
        
        validate_date_range(date_from, date_to)
        
        if project_id:
            if user_role != UserRole.ADMIN:
                if not project_crud.has_project_access(db, project_id, user_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You don't have access to this project"
                    )
        
        stats = booking_crud.get_booking_statistics(
            db,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            user_id=user_id if user_role != UserRole.ADMIN else None
        )
        
        return stats
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve statistics"
        )


@router.get("/my/upcoming", response_model=List[BookingDetailResponse])
def get_my_upcoming_bookings(
    limit: int = Query(10, ge=1, le=100, description="Maximum number of bookings to return"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> List[BookingDetailResponse]:
    """
    Get current user's upcoming bookings.
    """
    try:
        user_id = get_entity_id(current_entity)
        user_role = get_user_role(current_entity)
        
        bookings = booking_crud.get_user_upcoming_bookings(
            db, 
            user_id=user_id,
            user_role=user_role,
            limit=limit
        )
        
        return bookings
        
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve upcoming bookings"
        )


@router.get("/{booking_id}", response_model=BookingDetailResponse)
def get_booking(
    booking_id: UUID = Path(..., description="Booking ID"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Get detailed information about a specific booking.
    """
    try:
        booking = booking_crud.get_booking(db, booking_id)
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        check_booking_access(db, booking, current_entity)
        
        return booking_crud.get_booking_detail(db, booking_id)
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve booking"
        )


@router.put("/{booking_id}", response_model=BookingDetailResponse)
def update_booking(
    booking_id: UUID = Path(..., description="Booking ID"),
    booking_update: BookingUpdate = Body(..., description="Booking update data"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Update an existing booking.
    
    - Partial updates supported
    - Validates new time slots if changed
    - Checks for conflicts with updated details
    - Optional comment for audit trail
    """
    try:
        existing = booking_crud.get_booking(db, booking_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        check_booking_access(db, existing, current_entity, require_owner=True)
        
        # Validate new times if provided
        if booking_update.start_time and booking_update.end_time:
            validate_booking_times(booking_update.start_time, booking_update.end_time)
        elif booking_update.start_time or booking_update.end_time:
            start = booking_update.start_time or existing.start_time
            end = booking_update.end_time or existing.end_time
            validate_booking_times(start, end)
        
        # Validate booking date
        new_date = booking_update.booking_date or existing.booking_date
        if new_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update booking to a past date"
            )
        
        # Check for conflicts
        if any([booking_update.booking_date, booking_update.start_time, booking_update.end_time]):
            conflict_check = BookingConflictCheck(
                asset_id=existing.asset_id,
                booking_date=booking_update.booking_date or existing.booking_date,
                start_time=booking_update.start_time or existing.start_time,
                end_time=booking_update.end_time or existing.end_time,
                exclude_booking_id=booking_id
            )
            
            conflicts = booking_crud.check_booking_conflicts(db, conflict_check)
            if conflicts.has_confirmed_conflict:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Updated booking would conflict with a confirmed reservation"
                )
        
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)
        
        # Update with audit logging
        updated_booking = booking_crud.update_booking(
            db,
            booking_id,
            booking_update,
            updated_by_id=user_id,
            updated_by_role=user_role,
            comment=booking_update.comment  # User-provided comment for audit
        )

        # Determine if this was a reschedule or general update
        update_fields = booking_update.model_dump(exclude_unset=True)
        action = "rescheduled" if any(
            k in update_fields for k in ("booking_date", "start_time", "end_time")
        ) else "updated"
        notify_booking_change(db, updated_booking.id, action, user_id)

        return booking_crud.get_booking_detail(db, updated_booking.id)
        
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update booking"
        )


@router.patch("/{booking_id}/status", response_model=BookingDetailResponse)
def update_booking_status(
    booking_id: UUID = Path(..., description="Booking ID"),
    status_update: BookingStatusUpdate = Body(..., description="New status and optional comment"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Update only the status of a booking.
    
    - Quick status updates
    - Provide optional comment explaining the status change
    - Comment will be recorded in audit trail
    """
    try:
        booking = booking_crud.get_booking(db, booking_id)
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        check_booking_access(db, booking, current_entity)
        
        old_status = booking.status

        # Validate status transition
        if booking.status == BookingStatus.COMPLETED and status_update.status != BookingStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change status of a completed booking"
            )
        
        if booking.status == BookingStatus.CANCELLED and status_update.status != BookingStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot reactivate a cancelled booking"
            )
        
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)
        
        # Update status with audit logging
        updated_booking, auto_denied_ids = booking_crud.update_booking_status(
            db,
            booking_id,
            new_status=status_update.status,
            updated_by_id=user_id,
            updated_by_role=user_role,
            comment=status_update.comment  # User-provided comment for audit
        )

        # Map status transition to notification action
        if status_update.status == BookingStatus.CONFIRMED and old_status == BookingStatus.PENDING:
            notif_action = "approved"
        elif status_update.status == BookingStatus.DENIED:
            notif_action = "denied"
        elif status_update.status == BookingStatus.CANCELLED:
            notif_action = "cancelled"
        else:
            notif_action = "updated"
        notify_booking_change(db, updated_booking.id, notif_action, user_id)

        # Send denial notifications to auto-denied subcontractors
        for denied_id in auto_denied_ids:
            notify_booking_change(db, denied_id, "denied", user_id)

        return booking_crud.get_booking_detail(db, updated_booking.id)
        
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update booking status"
        )


@router.delete("/{booking_id}", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def delete_booking(
    booking_id: UUID = Path(..., description="Booking ID"),
    delete_request: BookingDeleteRequest = Body(..., description="Delete options and optional reason"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> MessageResponse:
    """
    Delete a booking.
    
    - Soft delete (sets status to CANCELLED) by default
    - Hard delete permanently removes the booking
    - Owners can Hard Delete ONLY if status is CANCELLED or DENIED
    - Admins can Hard Delete anything
    - Provide optional comment explaining the deletion
    """
    try:
        booking = booking_crud.get_booking(db, booking_id)
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)

        # Hard delete permission logic
        if delete_request.hard_delete:
            if user_role == UserRole.ADMIN:
                pass  # Admins always allowed
            elif booking.manager_id == user_id or booking.subcontractor_id == user_id:
                # Owners can only hard delete if booking is already "dead"
                if booking.status not in [BookingStatus.CANCELLED, BookingStatus.DENIED]:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You can only permanently delete bookings that are Cancelled or Denied"
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only administrators or the booking owner can permanently delete this booking"
                )
        
        check_booking_access(db, booking, current_entity)
        
        # Delete with audit logging
        success = booking_crud.delete_booking(
            db,
            booking_id,
            deleted_by_id=user_id,
            deleted_by_role=user_role,
            hard_delete=delete_request.hard_delete,
            comment=delete_request.comment  # User-provided reason for audit
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete booking"
            )
        
        # Notify after successful soft-delete only
        if not delete_request.hard_delete:
            notify_booking_change(db, booking_id, "cancelled", user_id)

        action = "permanently deleted" if delete_request.hard_delete else "cancelled"
        return MessageResponse(
            success=True,
            message=f"Booking {action} successfully"
        )
        
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete booking"
        )


@router.post("/check-conflicts", response_model=BookingConflictResponse)
def check_conflicts(
    conflict_check: BookingConflictCheck,
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingConflictResponse:
    """
    Check if a proposed booking would conflict with existing bookings.
    """
    try:
        validate_booking_times(conflict_check.start_time, conflict_check.end_time)
        
        if conflict_check.booking_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot check conflicts for past dates"
            )
        
        conflicts = booking_crud.check_booking_conflicts(db, conflict_check)
        return conflicts
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check conflicts"
        )


@router.post("/{booking_id}/duplicate", response_model=BookingDetailResponse, status_code=status.HTTP_201_CREATED)
def duplicate_booking(
    booking_id: UUID = Path(..., description="Booking ID to duplicate"),
    duplicate_request: BookingDuplicateRequest = Body(..., description="Duplicate booking options"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Duplicate an existing booking for a different date.
    
    - Optional comment for audit trail
    """
    try:
        original = booking_crud.get_booking(db, booking_id)
        if not original:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)
        
        check_booking_access(db, original, current_entity)
        
        # Validate new date (already validated in schema, but double-check)
        if duplicate_request.new_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot create bookings for past dates"
            )
        
        # Check for conflicts on new date
        conflict_check = BookingConflictCheck(
            asset_id=original.asset_id,
            booking_date=duplicate_request.new_date,
            start_time=original.start_time,
            end_time=original.end_time
        )
        
        conflicts = booking_crud.check_booking_conflicts(db, conflict_check)
        if conflicts.has_confirmed_conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A confirmed booking already exists on {duplicate_request.new_date}"
            )
        if user_role == UserRole.SUBCONTRACTOR and not conflicts.can_request:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Limit reached for this time slot. Choose another slot or contact the manager."
            )

        # Create duplicate booking data
        duplicate_data = BookingCreate(
            project_id=original.project_id,
            manager_id=original.manager_id if user_role in [UserRole.ADMIN, UserRole.MANAGER] else None,
            subcontractor_id=original.subcontractor_id,
            asset_id=original.asset_id,
            booking_date=duplicate_request.new_date,
            start_time=original.start_time,
            end_time=original.end_time,
            purpose=original.purpose,
            notes=f"Duplicated from booking {booking_id}. {original.notes or ''}".strip()
        )

        # Set status based on role
        if user_role in [UserRole.MANAGER, UserRole.ADMIN]:
            duplicate_data.status = BookingStatus.CONFIRMED
        else:
            duplicate_data.status = BookingStatus.PENDING
        
        # Create with audit logging
        new_booking = booking_crud.create_booking(
            db,
            duplicate_data,
            created_by_id=user_id,
            created_by_role=user_role,
            comment=duplicate_request.comment  # User-provided comment for audit
        )

        notify_booking_change(db, new_booking.id, "created", user_id)

        return booking_crud.get_booking_detail(db, new_booking.id)
        
    except HTTPException:
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid duplicate booking request"
        )
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to duplicate booking"
        )