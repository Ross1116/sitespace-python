# crud/slot_booking.py
from typing import Optional, List, Dict, Any, Tuple
from datetime import date, datetime, time, timedelta
from uuid import UUID
from sqlalchemy import and_, or_, func, case
from sqlalchemy.orm import Session, joinedload
from collections import defaultdict

from ..models.slot_booking import SlotBooking, BookingStatus
from ..models.user import User
from ..models.subcontractor import Subcontractor
from ..models.asset import Asset, AssetStatus
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

def create_booking(
    db: Session,
    booking_data: BookingCreate,
    created_by_id: UUID
) -> SlotBooking:
    """Create a new booking in the database"""
    
    # Validate that all referenced entities exist
    project = db.query(SiteProject).filter(SiteProject.id == booking_data.project_id).first()
    if not project:
        raise ValueError(f"Project with id {booking_data.project_id} not found")
    
    manager = db.query(User).filter(User.id == booking_data.manager_id).first()
    if not manager:
        raise ValueError(f"Manager with id {booking_data.manager_id} not found")
    
    subcontractor = db.query(Subcontractor).filter(Subcontractor.id == booking_data.subcontractor_id).first()
    if not subcontractor:
        raise ValueError(f"Subcontractor with id {booking_data.subcontractor_id} not found")
    
    asset = db.query(Asset).filter(Asset.id == booking_data.asset_id).first()
    if not asset:
        raise ValueError(f"Asset with id {booking_data.asset_id} not found")
    
    # Check if asset is available
    if asset.status != AssetStatus.AVAILABLE:
        raise ValueError(f"Asset is not available (status: {asset.status})")
    
    # Create the booking
    db_booking = SlotBooking(
        project_id=booking_data.project_id,
        manager_id=booking_data.manager_id or created_by_id,  # Use current user as manager if not specified
        subcontractor_id=booking_data.subcontractor_id,
        asset_id=booking_data.asset_id,
        booking_date=booking_data.booking_date,
        start_time=booking_data.start_time,
        end_time=booking_data.end_time,
        purpose=booking_data.purpose,
        notes=booking_data.notes,
        status=BookingStatus.PENDING
    )
    
    db.add(db_booking)
    db.commit()
    db.refresh(db_booking)
    
    return db_booking

def create_bulk_bookings(
    db: Session,
    bulk_data: BulkBookingCreate,
    created_by_id: UUID
) -> List[SlotBooking]:
    """Create multiple bookings at once"""
    bookings = []
    failed_bookings = []
    
    # Validate base entities exist
    project = db.query(SiteProject).filter(SiteProject.id == bulk_data.project_id).first()
    if not project:
        raise ValueError(f"Project with id {bulk_data.project_id} not found")
    
    manager = db.query(User).filter(User.id == bulk_data.manager_id).first()
    if not manager:
        raise ValueError(f"Manager with id {bulk_data.manager_id} not found")
    
    subcontractor = db.query(Subcontractor).filter(Subcontractor.id == bulk_data.subcontractor_id).first()
    if not subcontractor:
        raise ValueError(f"Subcontractor with id {bulk_data.subcontractor_id} not found")
    
    for asset_id in bulk_data.asset_ids:
        # Validate asset exists and is available
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            failed_bookings.append({"asset_id": asset_id, "reason": "Asset not found"})
            continue
        
        if asset.status != AssetStatus.AVAILABLE:
            failed_bookings.append({"asset_id": asset_id, "reason": f"Asset status is {asset.status}"})
            continue
        
        for booking_date in bulk_data.booking_dates:
            # Check for conflicts for each combination
            conflict_check = BookingConflictCheck(
                asset_id=asset_id,
                booking_date=booking_date,
                start_time=bulk_data.start_time,
                end_time=bulk_data.end_time
            )
            
            conflicts = check_booking_conflicts(db, conflict_check)
            if conflicts.has_conflict:
                failed_bookings.append({
                    "asset_id": asset_id,
                    "date": booking_date,
                    "reason": "Scheduling conflict"
                })
                continue
            
            db_booking = SlotBooking(
                project_id=bulk_data.project_id,
                manager_id=bulk_data.manager_id or created_by_id,
                subcontractor_id=bulk_data.subcontractor_id,
                asset_id=asset_id,
                booking_date=booking_date,
                start_time=bulk_data.start_time,
                end_time=bulk_data.end_time,
                purpose=bulk_data.purpose,
                notes=bulk_data.notes,
                status=BookingStatus.PENDING
            )
            
            db.add(db_booking)
            bookings.append(db_booking)
    
    if bookings:
        db.commit()
        # Refresh all bookings
        for booking in bookings:
            db.refresh(booking)
    
    # You might want to return failed_bookings info as well
    return bookings

def get_booking(db: Session, booking_id: UUID) -> Optional[SlotBooking]:
    """Get a single booking by ID"""
    return db.query(SlotBooking).filter(SlotBooking.id == booking_id).first()

def get_booking_with_details(db: Session, booking_id: UUID) -> Optional[SlotBooking]:
    """Get booking with all relationships loaded"""
    return db.query(SlotBooking).options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.manager),
        joinedload(SlotBooking.subcontractor),
        joinedload(SlotBooking.asset)
    ).filter(SlotBooking.id == booking_id).first()

def get_booking_detail(db: Session, booking_id: UUID) -> Optional[BookingDetailResponse]:
    """Get detailed booking information with related entities"""
    booking = get_booking_with_details(db, booking_id)
    
    if not booking:
        return None
    
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
        project={
            "id": booking.project.id,
            "name": booking.project.name,
            "location": booking.project.location,
            "status": booking.project.status
        } if booking.project else None,
        manager={
            "id": booking.manager.id,
            "email": booking.manager.email,
            "first_name": booking.manager.first_name,
            "last_name": booking.manager.last_name,
            "role": booking.manager.role,  # ADD THIS LINE
            "full_name": f"{booking.manager.first_name} {booking.manager.last_name}"
        } if booking.manager else None,
        subcontractor={
            "id": booking.subcontractor.id,
            "email": booking.subcontractor.email,
            "first_name": booking.subcontractor.first_name,
            "last_name": booking.subcontractor.last_name,
            "company_name": booking.subcontractor.company_name,
            "trade_specialty": booking.subcontractor.trade_specialty
        } if booking.subcontractor else None,
        asset={
            "id": booking.asset.id,
            "asset_code": booking.asset.asset_code,
            "name": booking.asset.name,
            "type": booking.asset.type,
            "status": booking.asset.status.value if booking.asset.status else None
        } if booking.asset else None
    )

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
            query = query.filter(SlotBooking.status == filter_params.status)
        
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
    booking_responses = []
    for booking in bookings:
        booking_responses.append(BookingDetailResponse(
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
            project={
                "id": booking.project.id,
                "name": booking.project.name,
                "location": booking.project.location,
                "status": booking.project.status
            } if booking.project else None,
            manager={
                "id": booking.manager.id,
                "email": booking.manager.email,
                "first_name": booking.manager.first_name,
                "last_name": booking.manager.last_name,
                "role": booking.manager.role,
                "full_name": f"{booking.manager.first_name} {booking.manager.last_name}"
            } if booking.manager else None,
            subcontractor={
                "id": booking.subcontractor.id,
                "email": booking.subcontractor.email,
                "first_name": booking.subcontractor.first_name,
                "last_name": booking.subcontractor.last_name,
                "company_name": booking.subcontractor.company_name,
                "trade_specialty": booking.subcontractor.trade_specialty
            } if booking.subcontractor else None,
            asset={
                "id": booking.asset.id,
                "asset_code": booking.asset.asset_code,
                "name": booking.asset.name,
                "type": booking.asset.type,
                "status": booking.asset.status.value if booking.asset.status else None
            } if booking.asset else None
        ))
    
    return booking_responses, total

def update_booking(
    db: Session,
    booking_id: UUID,
    booking_update: BookingUpdate
) -> Optional[SlotBooking]:
    """Update an existing booking"""
    booking = get_booking(db, booking_id)
    if not booking:
        return None
    
    # Update fields if provided
    update_data = booking_update.dict(exclude_unset=True)
    
    # Validate new references if being updated
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
    
    if 'asset_id' in update_data:
        asset = db.query(Asset).filter(Asset.id == update_data['asset_id']).first()
        if not asset:
            raise ValueError(f"Asset with id {update_data['asset_id']} not found")
    
    for field, value in update_data.items():
        setattr(booking, field, value)
    
    booking.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(booking)
    
    return booking

def update_booking_status(
    db: Session,
    booking_id: UUID,
    status: BookingStatus
) -> Optional[SlotBooking]:
    """Update only the status of a booking"""
    booking = get_booking(db, booking_id)
    if not booking:
        return None
    
    booking.status = status
    booking.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(booking)
    
    return booking

def delete_booking(db: Session, booking_id: UUID, soft_delete: bool = True) -> bool:
    """Delete a booking (soft or hard delete)"""
    booking = get_booking(db, booking_id)
    if not booking:
        return False
    
    if soft_delete:
        # Soft delete - set status to cancelled
        booking.status = BookingStatus.CANCELLED
        booking.updated_at = datetime.utcnow()
    else:
        # Hard delete
        db.delete(booking)
    
    db.commit()
    return True

def check_booking_conflicts(
    db: Session,
    conflict_check: BookingConflictCheck
) -> BookingConflictResponse:
    """Check if a proposed booking would conflict with existing bookings"""
    query = db.query(SlotBooking).filter(
        SlotBooking.asset_id == conflict_check.asset_id,
        SlotBooking.booking_date == conflict_check.booking_date,
        SlotBooking.status.notin_([BookingStatus.CANCELLED])
    )
    
    # Exclude specific booking if updating
    if hasattr(conflict_check, 'exclude_booking_id') and conflict_check.exclude_booking_id:
        query = query.filter(SlotBooking.id != conflict_check.exclude_booking_id)
    
    # Check for time overlap
    query = query.filter(
        or_(
            # New booking starts during existing booking
            and_(
                SlotBooking.start_time <= conflict_check.start_time,
                SlotBooking.end_time > conflict_check.start_time
            ),
            # New booking ends during existing booking
            and_(
                SlotBooking.start_time < conflict_check.end_time,
                SlotBooking.end_time >= conflict_check.end_time
            ),
            # New booking completely contains existing booking
            and_(
                SlotBooking.start_time >= conflict_check.start_time,
                SlotBooking.end_time <= conflict_check.end_time
            )
        )
    )
    
    conflicting_bookings = query.all()
    
    conflicts = []
    for booking in conflicting_bookings:
        conflicts.append({
            "id": booking.id,
            "start_time": booking.start_time,
            "end_time": booking.end_time,
            "status": booking.status,
            "subcontractor_id": booking.subcontractor_id,
            "manager_id": booking.manager_id,
            # Add the missing required fields
            "booking_date": booking.booking_date,
            "project_id": booking.project_id,
            "asset_id": booking.asset_id,
            "created_at": booking.created_at,
            # Optional fields that might be expected
            "updated_at": booking.updated_at,
            "purpose": booking.purpose,
            "notes": booking.notes
        })
    
    return BookingConflictResponse(
        has_conflict=len(conflicting_bookings) > 0,
        conflicting_bookings=conflicts,
        conflict_count=len(conflicting_bookings)
    )

def get_booking_statistics(
    db: Session,
    project_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    user_id: Optional[UUID] = None 
) -> BookingStatistics:
    """Calculate booking statistics"""
    query = db.query(SlotBooking)
    
    if project_id:
        query = query.filter(SlotBooking.project_id == project_id)
        
    if user_id:
        status_counts = status_counts.filter(SlotBooking.manager_id == user_id)
        
    if date_from:
        query = query.filter(SlotBooking.booking_date >= date_from)
    
    if date_to:
        query = query.filter(SlotBooking.booking_date <= date_to)
    
    # Get counts by status
    status_counts = db.query(
        SlotBooking.status,
        func.count(SlotBooking.id).label('count')
    )
    
    if project_id:
        status_counts = status_counts.filter(SlotBooking.project_id == project_id)
    if user_id:
        busiest_day_query = busiest_day_query.filter(SlotBooking.manager_id == user_id)
    if date_from:
        status_counts = status_counts.filter(SlotBooking.booking_date >= date_from)
    if date_to:
        status_counts = status_counts.filter(SlotBooking.booking_date <= date_to)
    
    status_counts = status_counts.group_by(SlotBooking.status).all()
    
    status_dict = {status.value: count for status, count in status_counts}
    
    # Get total bookings
    total_bookings = sum(status_dict.values())
    
    # Get busiest day
    busiest_day_query = db.query(
        SlotBooking.booking_date,
        func.count(SlotBooking.id).label('count')
    )
    
    if project_id:
        busiest_day_query = busiest_day_query.filter(SlotBooking.project_id == project_id)
    if user_id:
        most_booked_asset_query = most_booked_asset_query.filter(SlotBooking.manager_id == user_id)
    if date_from:
        busiest_day_query = busiest_day_query.filter(SlotBooking.booking_date >= date_from)
    if date_to:
        busiest_day_query = busiest_day_query.filter(SlotBooking.booking_date <= date_to)
    
    busiest_day_result = busiest_day_query.group_by(
        SlotBooking.booking_date
    ).order_by(
        func.count(SlotBooking.id).desc()
    ).first()
    
    # Get most booked asset
    most_booked_asset_query = db.query(
        Asset,
        func.count(SlotBooking.id).label('count')
    ).join(
        SlotBooking, SlotBooking.asset_id == Asset.id
    )
    
    if project_id:
        most_booked_asset_query = most_booked_asset_query.filter(SlotBooking.project_id == project_id)
    if date_from:
        most_booked_asset_query = most_booked_asset_query.filter(SlotBooking.booking_date >= date_from)
    if date_to:
        most_booked_asset_query = most_booked_asset_query.filter(SlotBooking.booking_date <= date_to)
    
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
    
    # Calculate utilization rate
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
    limit: int = 10
) -> List[BookingDetailResponse]:
    """Get upcoming bookings for a specific user (as manager)"""
    today = date.today()
    current_time = datetime.now().time()
    
    bookings = db.query(SlotBooking).options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.manager),
        joinedload(SlotBooking.subcontractor),
        joinedload(SlotBooking.asset)
    ).filter(
        SlotBooking.manager_id == user_id,
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
    
    booking_responses = []
    for booking in bookings:
        booking_responses.append(BookingDetailResponse(
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
            project={
                "id": booking.project.id,
                "name": booking.project.name,
                "location": booking.project.location,
                "status": booking.project.status
            } if booking.project else None,
            manager={
                "id": booking.manager.id,
                "email": booking.manager.email,
                "first_name": booking.manager.first_name,
                "last_name": booking.manager.last_name,
                "role": booking.manager.role,
                "full_name": f"{booking.manager.first_name} {booking.manager.last_name}"
            } if booking.manager else None,
            subcontractor={
                "id": booking.subcontractor.id,
                "email": booking.subcontractor.email,
                "first_name": booking.subcontractor.first_name,
                "last_name": booking.subcontractor.last_name,
                "company_name": booking.subcontractor.company_name,
                "trade_specialty": booking.subcontractor.trade_specialty
            } if booking.subcontractor else None,
            asset={
                "id": booking.asset.id,
                "asset_code": booking.asset.asset_code,
                "name": booking.asset.name,
                "type": booking.asset.type,
                "status": booking.asset.status.value if booking.asset.status else None
            } if booking.asset else None
        ))
    
    return booking_responses

def get_calendar_view(
    db: Session,
    date_from: date,
    date_to: date,
    project_id: Optional[UUID] = None,
    asset_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None
) -> List[BookingCalendarView]:
    """Get bookings in calendar view format"""
    from collections import defaultdict
    
    query = db.query(SlotBooking).options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.manager),
        joinedload(SlotBooking.subcontractor),
        joinedload(SlotBooking.asset)
    )
    
    # Apply filters
    query = query.filter(
        SlotBooking.booking_date >= date_from,
        SlotBooking.booking_date <= date_to
    )
    
    if project_id:
        query = query.filter(SlotBooking.project_id == project_id)
    
    if asset_id:
        query = query.filter(SlotBooking.asset_id == asset_id)
    
    if user_id:
        query = query.filter(SlotBooking.manager_id == user_id)
    
    # Get bookings and group by date
    bookings = query.order_by(
        SlotBooking.booking_date,
        SlotBooking.start_time
    ).all()
    
    # Group bookings by date
    bookings_by_date = defaultdict(list)
    for booking in bookings:
        booking_response = BookingResponse(
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
            updated_at=booking.updated_at
        )
        bookings_by_date[booking.booking_date].append(booking_response)
    
    # Create calendar view objects
    calendar_view = []
    current_date = date_from
    while current_date <= date_to:
        day_bookings = bookings_by_date.get(current_date, [])
        calendar_view.append(BookingCalendarView(
            date=current_date,
            bookings=day_bookings,
            total_bookings=len(day_bookings)
        ))
        current_date += timedelta(days=1)
    
    return calendar_view

# Additional helper functions

def get_asset_availability(
    db: Session,
    asset_id: UUID,
    check_date: date,
    start_time: Optional[time] = None,
    end_time: Optional[time] = None
) -> List[Dict[str, Any]]:
    """Get availability slots for an asset on a specific date"""
    # Get all bookings for the asset on the given date
    bookings = db.query(SlotBooking).filter(
        SlotBooking.asset_id == asset_id,
        SlotBooking.booking_date == check_date,
        SlotBooking.status.notin_([BookingStatus.CANCELLED])
    ).order_by(SlotBooking.start_time).all()
    
    # Calculate available time slots
    available_slots = []
    work_start = start_time or time(8, 0)  # Default work start
    work_end = end_time or time(18, 0)  # Default work end
    
    if not bookings:
        # Entire day is available
        available_slots.append({
            'start_time': work_start,
            'end_time': work_end,
            'duration_minutes': _calculate_minutes_between(work_start, work_end)
        })
    else:
        current_time = work_start
        
        for booking in bookings:
            if booking.start_time > current_time:
                # There's a gap before this booking
                duration = _calculate_minutes_between(current_time, booking.start_time)
                if duration >= 30:  # Only show slots of 30 minutes or more
                    available_slots.append({
                        'start_time': current_time,
                        'end_time': booking.start_time,
                        'duration_minutes': duration
                    })
            current_time = max(current_time, booking.end_time)
        
        # Check if there's time after the last booking
        if current_time < work_end:
            duration = _calculate_minutes_between(current_time, work_end)
            if duration >= 30:
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
    """Get all bookings for a subcontractor in a date range"""
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
    """Get summary of bookings for a project"""
    # Get all bookings for the project
    bookings = db.query(SlotBooking).filter(
        SlotBooking.project_id == project_id
    ).all()
    
    # Calculate summary
    total = len(bookings)
    by_status = {}
    by_asset = {}
    by_subcontractor = {}
    
    for booking in bookings:
        # Count by status
        status_key = booking.status.value if booking.status else 'unknown'
        by_status[status_key] = by_status.get(status_key, 0) + 1
        
        # Count by asset
        if booking.asset_id:
            by_asset[str(booking.asset_id)] = by_asset.get(str(booking.asset_id), 0) + 1
        
        # Count by subcontractor
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
    """Cancel pending bookings that have passed their date/time"""
    now = datetime.now()
    today = now.date()
    current_time = now.time()
    
    # Find expired pending bookings
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
    """Calculate minutes between two time objects"""
    start_delta = timedelta(hours=start_time.hour, minutes=start_time.minute)
    end_delta = timedelta(hours=end_time.hour, minutes=end_time.minute)
    return int((end_delta - start_delta).total_seconds() / 60)