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
    # If I am the Manager who made it OR the Subcontractor who made it, I have full access
    if booking.manager_id == user_id:
        return
        
    if user_role == UserRole.SUBCONTRACTOR and booking.subcontractor_id == user_id:
        return

    # 3. Check Project Manager Access (Managers only)
    # Project Managers should be able to edit/delete any booking on their project
    if booking.project_id and user_role == UserRole.MANAGER:
        if project_crud.is_project_manager(db, booking.project_id, user_id):
            return
    
    # 4. If we get here, we haven't matched any access rights
    if require_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the booking owner or project manager can perform this action"
        )
    
    # If just reading (require_owner=False), but we didn't match the above checks,
    # we still deny access (e.g., a Subcontractor trying to view another Sub's booking)
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
            # ISO format like "08:56:03.503Z"
            start_time = start_time.replace('Z', '').replace('T', '')
            if '.' in start_time:
                start_time = start_time.split('.')[0]  # Remove milliseconds
        
        # Parse time string
        if len(start_time.split(':')) == 3:
            # Format: HH:MM:SS
            start = datetime.strptime(start_time, "%H:%M:%S").time()
        else:
            # Format: HH:MM
            start = datetime.strptime(start_time, "%H:%M").time()
    elif isinstance(start_time, time_type):
        start = start_time
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid start_time type: {type(start_time)}"
        )
    
    if isinstance(end_time, str):
        # Handle different time formats
        if 'T' in end_time or 'Z' in end_time:
            # ISO format like "10:56:03.503Z"
            end_time = end_time.replace('Z', '').replace('T', '')
            if '.' in end_time:
                end_time = end_time.split('.')[0]  # Remove milliseconds
        
        # Parse time string
        if len(end_time.split(':')) == 3:
            # Format: HH:MM:SS
            end = datetime.strptime(end_time, "%H:%M:%S").time()
        else:
            # Format: HH:MM
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
    """
    try:
        # Get role and ID from entity (works for both User and Subcontractor)
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
        if user_role in [UserRole.MANAGER, UserRole.ADMIN]:
            booking_data.status = BookingStatus.CONFIRMED
        else:
            booking_data.status = BookingStatus.PENDING
        
        # Role-specific validations
        if user_role == UserRole.SUBCONTRACTOR:
            # Subcontractors can only book for themselves
            if booking_data.subcontractor_id and booking_data.subcontractor_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Subcontractors can only create bookings for themselves"
                )
            
            # Check if subcontractor is assigned to the project
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
            # Verify manager has access to the project
            if booking_data.project_id:
                if user_role != UserRole.ADMIN:  # Admins have access to all projects
                    if not project_crud.has_project_access(db, booking_data.project_id, user_id):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="You don't have access to this project"
                        )
            
            # Verify subcontractor is assigned to the project (only if subcontractor is specified)
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
        if conflicts.has_conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Booking conflicts with {len(conflicts.conflicting_bookings)} existing reservation(s)"
            )
        
        # Create the booking with role-based status
        booking = booking_crud.create_booking(
            db, 
            booking_data, 
            user_id,
            user_role  # Pass the determined role
        )
        
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
            detail=str(e)
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
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> List[BookingDetailResponse]:
    """
    Create multiple bookings at once.
    
    - Creates bookings for multiple assets and/or dates
    - Validates all bookings before creating any
    - Role-based status: CONFIRMED for managers, PENDING for subcontractors
    - Returns list of created bookings
    """
    try:
        # Get role and ID from entity (works for both User and Subcontractor)
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
            # Subcontractor must book for themselves
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
            # Check project access
            if bulk_data.project_id:
                if user_role != UserRole.ADMIN:
                    if not project_crud.has_project_access(db, bulk_data.project_id, user_id):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="You don't have access to this project"
                        )
            
            # Verify subcontractor is assigned to project (only if specified)
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
                if conflicts.has_conflict:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Conflict found for asset on {booking_date}"
                    )
        
        # Create all bookings with role-based status
        bookings = booking_crud.create_bulk_bookings(
            db, 
            bulk_data, 
            user_id,      
            user_role     
        )
        
        # Return detailed response for each booking
        return [booking_crud.get_booking_detail(db, b.id) for b in bookings]
        
    except HTTPException:
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
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
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingListResponse:
    """
    Get list of bookings with optional filters.
    
    **Role-based filtering:**
    - **Admins**: See all bookings
    - **Managers**: See bookings for their projects
    - **Subcontractors**: See only their own bookings
    
    - Supports pagination
    - Multiple filter options
    - Returns detailed booking information
    """
    try:
        user_role = get_user_role(current_entity)
        user_id = get_entity_id(current_entity)
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
        
        # Apply role-based filters
        if user_role == UserRole.SUBCONTRACTOR:
            # Subcontractors can only see their own bookings
            filter_params.subcontractor_id = user_id
        elif user_role == UserRole.MANAGER:
            # Managers see bookings they created or for their projects
            if not manager_id and not project_id:
                filter_params.manager_id = user_id
        # Admins see everything (no additional filter)
        
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

        # 1. Access Control Check
        if project_id:
            has_access = False
            if user_role == UserRole.ADMIN:
                has_access = True
            elif user_role == UserRole.SUBCONTRACTOR:
                has_access = project_crud.is_subcontractor_assigned(db, project_id, user_id)
            else:
                has_access = project_crud.has_project_access(db, project_id, user_id)

            if not has_access:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this project"
                )
        
        # 2. Determine Filters
        filter_manager_id = None
        filter_subcontractor_id = None

        if not project_id:
            if user_role == UserRole.MANAGER:
                filter_manager_id = user_id
            elif user_role == UserRole.SUBCONTRACTOR:
                filter_subcontractor_id = user_id

        # 3. Call CRUD
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
    current_user: Union[User, Subcontractor] = Depends(get_current_active_user)
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
        
        # ✅ FIX: Get role safely using the helper function
        user_role = get_user_role(current_user)

        # Check access
        if hard_delete and user_role != UserRole.ADMIN:
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> List[BookingDetailResponse]:
    """
    Get current user's upcoming bookings.
    
    - Shows bookings where user is the manager
    - Ordered by date and time
    """
    try:
        bookings = booking_crud.get_user_upcoming_bookings(
            db, 
            user_id=current_user.id,
            limit=limit
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
    current_user: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingDetailResponse:
    """
    Duplicate an existing booking for a different date.
    """
    try:
        # Get original booking
        original = booking_crud.get_booking(db, booking_id)
        if not original:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Booking not found"
            )
        
        # ✅ FIX: Get role and ID safely
        user_role = get_user_role(current_user)
        user_id = get_entity_id(current_user)
        
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
            manager_id=original.manager_id if user_role in [UserRole.ADMIN, UserRole.MANAGER] else None,
            subcontractor_id=original.subcontractor_id,
            asset_id=original.asset_id,
            booking_date=new_date,
            start_time=original.start_time,
            end_time=original.end_time,
            purpose=original.purpose,
            notes=f"Duplicated from booking {booking_id}. {original.notes or ''}"
        )

        if user_role in [UserRole.MANAGER, UserRole.ADMIN]:
            duplicate_data.status = BookingStatus.CONFIRMED
        else:
            duplicate_data.status = BookingStatus.PENDING
        
        # Create with role-based status
        new_booking = booking_crud.create_booking(
            db, 
            duplicate_data, 
            user_id,
            user_role
        )
        
        return booking_crud.get_booking_detail(db, new_booking.id)
        
    except HTTPException:
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to duplicate booking: {str(e)}"
        )