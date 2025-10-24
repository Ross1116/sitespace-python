from typing import Optional, List, Dict, Any
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Path, Body
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.user import User
from app.models.slot_booking import SlotBooking, BookingStatus
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
    BookingConflictResponse
)
from app.schemas.base import MessageResponse
from app.schemas.enums import UserRole
from app.crud import slot_booking as booking_crud
from app.crud import site_project as project_crud

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
    user: User,
    require_owner: bool = False
) -> None:
    """Check if user has access to the booking"""
    
    # Admins always have access
    if user.role == UserRole.ADMIN:
        return
    
    # Check if user is the booking owner (manager)
    if booking.manager_id == user.id:
        return
    
    # Check if user is a project manager for this booking's project
    if booking.project_id:
        if project_crud.is_project_manager(db, booking.project_id, user.id):
            if not require_owner:
                return
    
    # If require_owner is True, only the booking creator can access
    if require_owner and booking.manager_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the booking owner can perform this action"
        )
    
    # No access granted
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You don't have access to this booking"
    )


def validate_booking_times(start_time: str, end_time: str) -> None:
    """Validate that start_time is before end_time"""
    try:
        start = datetime.strptime(start_time, "%H:%M").time()
        end = datetime.strptime(end_time, "%H:%M").time()
        
        if start >= end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Start time must be before end time"
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid time format. Use HH:MM format"
        )


# ==================== Main Endpoints ====================

@router.post("/", response_model=BookingDetailResponse, status_code=status.HTTP_201_CREATED)
def create_booking(
    booking_data: BookingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Create a new booking.
    
    - Validates time slots and booking date
    - Checks for conflicts with existing bookings
    - Creates booking with PENDING status
    """
    try:
        # Validate booking times
        validate_booking_times(booking_data.start_time, booking_data.end_time)
        
        # Validate booking date is not in the past
        if booking_data.booking_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot create bookings for past dates"
            )
        
        # Check for conflicts before creating
        conflict_check = BookingConflictCheck(
            asset_id=booking_data.asset_id,
            booking_date=booking_data.booking_date,
            start_time=booking_data.start_time,
            end_time=booking_data.end_time
        )
        
        conflicts = booking_crud.check_booking_conflicts(db, conflict_check)
        if conflicts.has_conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Booking conflicts with {len(conflicts.conflicting_bookings)} existing reservation(s)"
            )
        
        # Verify user has access to the project if specified
        if booking_data.project_id:
            if not project_crud.has_project_access(db, booking_data.project_id, current_user.id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this project"
                )
        
        # Create the booking
        booking = booking_crud.create_booking(db, booking_data, current_user.id)
        return booking_crud.get_booking_detail(db, booking.id)
        
    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid booking data. Please check asset and project IDs"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create booking: {str(e)}"
        )


@router.post("/bulk", response_model=List[BookingDetailResponse], status_code=status.HTTP_201_CREATED)
def create_bulk_bookings(
    bulk_data: BulkBookingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> List[BookingDetailResponse]:
    """
    Create multiple bookings at once.
    
    - Creates bookings for multiple assets and/or dates
    - Validates all bookings before creating any
    - Returns list of created bookings
    """
    try:
        # Validate all bookings first
        for booking_date in bulk_data.booking_dates:
            if booking_date < date.today():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot create bookings for past dates"
                )
        
        # Validate times
        for booking in bulk_data.bookings:
            validate_booking_times(booking.start_time, booking.end_time)
        
        # Check project access if specified
        if bulk_data.project_id:
            if not project_crud.has_project_access(db, bulk_data.project_id, current_user.id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this project"
                )
        
        # Create all bookings
        bookings = booking_crud.create_bulk_bookings(db, bulk_data, current_user.id)
        
        # Return detailed response for each booking
        return [booking_crud.get_booking_detail(db, b.id) for b in bookings]
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create bulk bookings: {str(e)}"
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
    current_user: User = Depends(get_current_active_user)
) -> BookingListResponse:
    """
    Get list of bookings with optional filters.
    
    - Supports pagination
    - Multiple filter options
    - Returns detailed booking information
    """
    try:
        # Validate date range
        validate_date_range(date_from, date_to)
        
        # Build filter parameters
        filter_params = BookingFilterParams(
            project_id=project_id,
            manager_id=manager_id,
            subcontractor_id=subcontractor_id,
            asset_id=asset_id,
            status=booking_status,
            date_from=date_from,
            date_to=date_to
        )
        
        # For non-admin users, filter to show only accessible bookings
        if current_user.role != UserRole.ADMIN:
            # Show user's own bookings and bookings from their projects
            filter_params.user_id = current_user.id
        
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve bookings: {str(e)}"
        )


@router.get("/calendar", response_model=List[BookingCalendarView])
def get_calendar_view(
    date_from: date = Query(..., description="Start date for calendar view"),
    date_to: date = Query(..., description="End date for calendar view"),
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    asset_id: Optional[UUID] = Query(None, description="Filter by asset ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> List[BookingCalendarView]:
    """
    Get bookings in calendar view format.
    
    - Groups bookings by date
    - Useful for calendar UI components
    """
    try:
        # Validate date range
        validate_date_range(date_from, date_to)
        
        # Limit date range to prevent excessive data retrieval
        max_days = 90
        delta = (date_to - date_from).days
        if delta > max_days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Date range cannot exceed {max_days} days"
            )
        
        # Check project access if filtering by project
        if project_id:
            if not project_crud.has_project_access(db, project_id, current_user.id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this project"
                )
        
        calendar_data = booking_crud.get_calendar_view(
            db,
            date_from=date_from,
            date_to=date_to,
            project_id=project_id,
            asset_id=asset_id,
            user_id=current_user.id if current_user.role != UserRole.ADMIN else None
        )
        
        return calendar_data
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve calendar view: {str(e)}"
        )


@router.get("/statistics", response_model=BookingStatistics)
def get_booking_statistics(
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    date_from: Optional[date] = Query(None, description="Statistics from date"),
    date_to: Optional[date] = Query(None, description="Statistics to date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> BookingStatistics:
    """
    Get booking statistics and analytics.
    
    - Overall booking counts by status
    - Utilization rates
    - Trends and insights
    """
    try:
        # Validate date range
        validate_date_range(date_from, date_to)
        
        # Check project access if filtering by project
        if project_id:
            if not project_crud.has_project_access(db, project_id, current_user.id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this project"
                )
        
        stats = booking_crud.get_booking_statistics(
            db,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            user_id=current_user.id if current_user.role != UserRole.ADMIN else None
        )
        
        return stats
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve statistics: {str(e)}"
        )


@router.get("/{booking_id}", response_model=BookingDetailResponse)
def get_booking(
    booking_id: UUID = Path(..., description="Booking ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Get detailed information about a specific booking.
    
    - Includes related project, manager, subcontractor, and asset details
    """
    try:
        booking = booking_crud.get_booking(db, booking_id)
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        # Check access
        check_booking_access(db, booking, current_user)
        
        return booking_crud.get_booking_detail(db, booking_id)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve booking: {str(e)}"
        )


@router.put("/{booking_id}", response_model=BookingDetailResponse)
def update_booking(
    booking_id: UUID = Path(..., description="Booking ID"),
    booking_update: BookingUpdate = Body(..., description="Booking update data"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Update an existing booking.
    
    - Partial updates supported
    - Validates new time slots if changed
    - Checks for conflicts with updated details
    """
    try:
        # Get existing booking
        existing = booking_crud.get_booking(db, booking_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        # Check access (only owner or admin can update)
        check_booking_access(db, existing, current_user, require_owner=True)
        
        # Validate new times if provided
        if booking_update.start_time and booking_update.end_time:
            validate_booking_times(booking_update.start_time, booking_update.end_time)
        elif booking_update.start_time or booking_update.end_time:
            # If only one time is being updated, validate against existing
            start = booking_update.start_time or existing.start_time
            end = booking_update.end_time or existing.end_time
            validate_booking_times(start, end)
        
        # Validate booking date is not in the past
        new_date = booking_update.booking_date or existing.booking_date
        if new_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update booking to a past date"
            )
        
        # Check if time/date changes would create conflicts
        if any([booking_update.booking_date, booking_update.start_time, booking_update.end_time]):
            conflict_check = BookingConflictCheck(
                asset_id=existing.asset_id,
                booking_date=booking_update.booking_date or existing.booking_date,
                start_time=booking_update.start_time or existing.start_time,
                end_time=booking_update.end_time or existing.end_time,
                exclude_booking_id=booking_id
            )
            
            conflicts = booking_crud.check_booking_conflicts(db, conflict_check)
            if conflicts.has_conflict:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Updated booking would conflict with {len(conflicts.conflicting_bookings)} existing reservation(s)"
                )
        
        # Update the booking
        updated_booking = booking_crud.update_booking(db, booking_id, booking_update)
        return booking_crud.get_booking_detail(db, updated_booking.id)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update booking: {str(e)}"
        )


@router.patch("/{booking_id}/status", response_model=BookingDetailResponse)
def update_booking_status(
    booking_id: UUID = Path(..., description="Booking ID"),
    new_status: BookingStatus = Query(..., description="New booking status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Update only the status of a booking.
    
    - Quick status updates
    - Useful for confirming, cancelling, or completing bookings
    """
    try:
        # Get existing booking
        booking = booking_crud.get_booking(db, booking_id)
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        # Check access
        check_booking_access(db, booking, current_user)
        
        # Validate status transition
        if booking.status == BookingStatus.COMPLETED and new_status != BookingStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change status of a completed booking"
            )
        
        if booking.status == BookingStatus.CANCELLED and new_status != BookingStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot reactivate a cancelled booking"
            )
        
        # Update status
        updated_booking = booking_crud.update_booking_status(db, booking_id, new_status)
        return booking_crud.get_booking_detail(db, updated_booking.id)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update booking status: {str(e)}"
        )


@router.delete("/{booking_id}", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def delete_booking(
    booking_id: UUID = Path(..., description="Booking ID"),
    hard_delete: bool = Query(False, description="Permanently delete the booking"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> MessageResponse:
    """
    Delete a booking.
    
    - Soft delete (sets status to CANCELLED) by default
    - Hard delete permanently removes the booking (admin only)
    """
    try:
        # Get booking
        booking = booking_crud.get_booking(db, booking_id)
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        # Check access
        if hard_delete and current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can permanently delete bookings"
            )
        
        check_booking_access(db, booking, current_user, require_owner=True)
        
        # Delete booking
        success = booking_crud.delete_booking(db, booking_id, hard_delete=hard_delete)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete booking"
            )
        
        action = "permanently deleted" if hard_delete else "cancelled"
        return MessageResponse(
            success=True,
            message=f"Booking {action} successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete booking: {str(e)}"
        )


@router.post("/check-conflicts", response_model=BookingConflictResponse)
def check_conflicts(
    conflict_check: BookingConflictCheck,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> BookingConflictResponse:
    """
    Check if a proposed booking would conflict with existing bookings.
    
    - Useful for validation before booking creation
    - Returns conflicting bookings if any exist
    """
    try:
        # Validate times
        validate_booking_times(conflict_check.start_time, conflict_check.end_time)
        
        # Validate booking date
        if conflict_check.booking_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot check conflicts for past dates"
            )
        
        conflicts = booking_crud.check_booking_conflicts(db, conflict_check)
        return conflicts
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check conflicts: {str(e)}"
        )


@router.get("/my/upcoming", response_model=List[BookingDetailResponse])
def get_my_upcoming_bookings(
    limit: int = Query(10, ge=1, le=100, description="Maximum number of bookings to return"),
    include_projects: bool = Query(False, description="Include bookings from projects I manage"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> List[BookingDetailResponse]:
    """
    Get current user's upcoming bookings.
    
    - Shows bookings where user is the manager
    - Optionally includes bookings from user's projects
    - Ordered by date and time
    """
    try:
        bookings = booking_crud.get_user_upcoming_bookings(
            db, 
            user_id=current_user.id,
            limit=limit,
            include_projects=include_projects
        )
        
        return bookings
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve upcoming bookings: {str(e)}"
        )


@router.post("/{booking_id}/duplicate", response_model=BookingDetailResponse, status_code=status.HTTP_201_CREATED)
def duplicate_booking(
    booking_id: UUID = Path(..., description="Booking ID to duplicate"),
    new_date: date = Body(..., description="Date for the duplicated booking"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Duplicate an existing booking for a different date.
    
    - Copies all booking details except the date
    - Checks for conflicts on the new date
    """
    try:
        # Get original booking
        original = booking_crud.get_booking(db, booking_id)
        if not original:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        # Check access
        check_booking_access(db, original, current_user)
        
        # Validate new date
        if new_date < date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot create bookings for past dates"
            )
        
        # Check for conflicts on new date
        conflict_check = BookingConflictCheck(
            asset_id=original.asset_id,
            booking_date=new_date,
            start_time=original.start_time,
            end_time=original.end_time
        )
        
        conflicts = booking_crud.check_booking_conflicts(db, conflict_check)
        if conflicts.has_conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Booking conflicts with existing reservations on {new_date}"
            )
        
        # Create duplicate booking
        duplicate_data = BookingCreate(
            project_id=original.project_id,
            subcontractor_id=original.subcontractor_id,
            asset_id=original.asset_id,
            booking_date=new_date,
            start_time=original.start_time,
            end_time=original.end_time,
            notes=f"Duplicated from booking {booking_id}"
        )
        
        new_booking = booking_crud.create_booking(db, duplicate_data, current_user.id)
        return booking_crud.get_booking_detail(db, new_booking.id)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to duplicate booking: {str(e)}"
        )