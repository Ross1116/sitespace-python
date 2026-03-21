# crud/subcontractor.py
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, date, time, timedelta, timezone

from ..models.subcontractor import Subcontractor
from ..models.site_project import SiteProject
from ..models.slot_booking import SlotBooking
from ..models.user import User
from ..schemas.subcontractor import SubcontractorCreate, SubcontractorUpdate

from ..core.security import get_password_hash, normalize_email as _normalize_email, verify_password
from ..schemas.enums import BookingStatus, ProjectStatus


def create_subcontractor(db: Session, subcontractor_data: SubcontractorCreate) -> Subcontractor:
    """Create a new subcontractor"""
    # Hash the password
    hashed_password = get_password_hash(subcontractor_data.password)
    
    trade_value = None
    if subcontractor_data.trade_specialty:
        if hasattr(subcontractor_data.trade_specialty, 'value'):
            trade_value = subcontractor_data.trade_specialty.value
        else:
            trade_value = str(subcontractor_data.trade_specialty)
    
    # Create subcontractor instance
    db_subcontractor = Subcontractor(
        email=_normalize_email(subcontractor_data.email),
        password_hash=hashed_password,
        first_name=subcontractor_data.first_name,
        last_name=subcontractor_data.last_name,
        company_name=subcontractor_data.company_name,
        trade_specialty=trade_value,
        phone=subcontractor_data.phone
    )
    
    db.add(db_subcontractor)
    db.commit()
    db.refresh(db_subcontractor)
    return db_subcontractor

def get_subcontractor(db: Session, subcontractor_id: UUID) -> Optional[Subcontractor]:
    """Get a subcontractor by ID"""
    return db.query(Subcontractor).filter(Subcontractor.id == subcontractor_id).first()

def get_subcontractor_with_details(db: Session, subcontractor_id: UUID) -> Optional[Subcontractor]:
    """Get subcontractor with all relationships loaded"""
    return db.query(Subcontractor)\
        .options(
            joinedload(Subcontractor.bookings),
            joinedload(Subcontractor.assigned_projects)
        )\
        .filter(Subcontractor.id == subcontractor_id)\
        .first()

def get_subcontractor_by_email(db: Session, email: str) -> Optional[Subcontractor]:
    """Get a subcontractor by email"""
    normalized_email = _normalize_email(email)
    return db.query(Subcontractor).filter(
        func.lower(Subcontractor.email) == normalized_email
    ).first()

def authenticate_subcontractor(db: Session, email: str, password: str) -> Optional[Subcontractor]:
    """
    Authenticate a subcontractor by email and password.
    Returns the subcontractor object if credentials are valid, None otherwise.
    """
    subcontractor = get_subcontractor_by_email(db, email)
    if not subcontractor:
        return None
    if not verify_password(password, subcontractor.password_hash):
        return None
    return subcontractor

def get_all_subcontractors(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    is_active: Optional[bool] = None,
    trade_specialty: Optional[str] = None
) -> dict:
    """Get all subcontractors with pagination and filters"""
    query = db.query(Subcontractor)
    
    # Apply filters
    if is_active is not None:
        query = query.filter(Subcontractor.is_active == is_active)
    
    if trade_specialty:
        query = query.filter(Subcontractor.trade_specialty == trade_specialty)
    
    # Get total count
    total = query.count()
    
    # Apply pagination and order by creation date
    subcontractors = query.order_by(Subcontractor.created_at.desc())\
        .offset(skip).limit(limit).all()
    
    return {
        "subcontractors": subcontractors,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < total
    }

def update_subcontractor(
    db: Session, 
    subcontractor_id: UUID, 
    subcontractor_update: SubcontractorUpdate
) -> Optional[Subcontractor]:
    """Update a subcontractor"""
    db_subcontractor = get_subcontractor(db, subcontractor_id)
    
    if not db_subcontractor:
        return None
    
    # Update only provided fields
    update_data = subcontractor_update.dict(exclude_unset=True)
    
    for field, value in update_data.items():
        if field == "trade_specialty" and value:
            value = value.value if hasattr(value, 'value') else value
        if field == "email" and value:
            value = _normalize_email(value)
        setattr(db_subcontractor, field, value)
    
    db_subcontractor.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(db_subcontractor)
    return db_subcontractor

def update_password(db: Session, subcontractor_id: UUID, password: str) -> bool:
    """
    Update a subcontractor's password.
    Hashes the new password before saving.
    """
    db_subcontractor = get_subcontractor(db, subcontractor_id)
    if not db_subcontractor:
        return False
        
    hashed_password = get_password_hash(password)
    db_subcontractor.password_hash = hashed_password
    db_subcontractor.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(db_subcontractor)
    return True

def delete_subcontractor(db: Session, subcontractor_id: UUID) -> bool:
    """Delete a subcontractor (soft delete by setting is_active to False)"""
    db_subcontractor = get_subcontractor(db, subcontractor_id)
    
    if not db_subcontractor:
        return False
    
    # Soft delete
    db_subcontractor.is_active = False
    db_subcontractor.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    return True

def activate_subcontractor(db: Session, subcontractor_id: UUID) -> bool:
    """Activate a subcontractor"""
    db_subcontractor = get_subcontractor(db, subcontractor_id)
    
    if not db_subcontractor:
        return False
    
    db_subcontractor.is_active = True
    db_subcontractor.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    return True

def hard_delete_subcontractor(db: Session, subcontractor_id: UUID) -> bool:
    """Permanently delete a subcontractor"""
    db_subcontractor = get_subcontractor(db, subcontractor_id)
    
    if not db_subcontractor:
        return False
    
    db.delete(db_subcontractor)
    db.commit()
    return True

def search_subcontractors(
    db: Session,
    search_term: Optional[str] = None,
    trade_specialty: Optional[str] = None,
    is_active: Optional[bool] = True,
    skip: int = 0,
    limit: int = 100
) -> dict:
    """Search subcontractors by various criteria"""
    query = db.query(Subcontractor)
    
    if search_term:
        search_filter = or_(
            Subcontractor.first_name.ilike(f"%{search_term}%"),
            Subcontractor.last_name.ilike(f"%{search_term}%"),
            Subcontractor.company_name.ilike(f"%{search_term}%"),
            Subcontractor.email.ilike(f"%{search_term}%")
        )
        query = query.filter(search_filter)
    
    if trade_specialty:
        query = query.filter(Subcontractor.trade_specialty == trade_specialty)
    
    if is_active is not None:
        query = query.filter(Subcontractor.is_active == is_active)
    
    total = query.count()
    subcontractors = query.order_by(Subcontractor.created_at.desc())\
        .offset(skip).limit(limit).all()
    
    return {
        "subcontractors": subcontractors,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < total
    }

# ========================================================
# Relationship and Logic Functions
# ========================================================

def get_subcontractor_projects(
    db: Session,
    subcontractor_id: UUID,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Get all projects assigned to a subcontractor"""
    subcontractor = get_subcontractor_with_details(db, subcontractor_id)
    
    if not subcontractor:
        return []
    
    projects = subcontractor.assigned_projects
    
    # Filter by active status if specified
    if is_active is not None:
        if is_active:
            projects = [p for p in projects if p.status == ProjectStatus.ACTIVE.value or p.status is None]
        else:
            projects = [p for p in projects if p.status != ProjectStatus.ACTIVE.value and p.status is not None]
    
    # Apply pagination
    return projects[skip:skip + limit]

def get_subcontractor_bookings(
    db: Session,
    subcontractor_id: UUID,
    project_id: Optional[UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> Dict[str, Any]:
    """Get all bookings for a subcontractor with filters"""
    query = db.query(SlotBooking).filter(SlotBooking.subcontractor_id == subcontractor_id)
    
    # Apply filters
    if project_id:
        query = query.filter(SlotBooking.project_id == project_id)
    
    if start_date:
        query = query.filter(SlotBooking.booking_date >= start_date)
    
    if end_date:
        query = query.filter(SlotBooking.booking_date <= end_date)
    
    if status:
        query = query.filter(SlotBooking.status == status)
    
    # Get total count
    total = query.count()
    
    # Get bookings with relationships loaded
    bookings = query.options(
        joinedload(SlotBooking.project),
        joinedload(SlotBooking.asset)
    ).order_by(SlotBooking.booking_date.desc(), SlotBooking.start_time.desc())\
        .offset(skip).limit(limit).all()
    
    return {
        "bookings": bookings,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < total
    }

def get_upcoming_bookings(
    db: Session,
    subcontractor_id: UUID,
    days_ahead: int = 7
) -> List[SlotBooking]:
    """Get upcoming bookings for a subcontractor in the next N days"""
    today = date.today()
    end_date = today + timedelta(days=days_ahead)
    
    bookings = db.query(SlotBooking)\
        .filter(
            and_(
                SlotBooking.subcontractor_id == subcontractor_id,
                SlotBooking.booking_date >= today,
                SlotBooking.booking_date <= end_date,
                SlotBooking.status != BookingStatus.CANCELLED
            )
        )\
        .options(
            joinedload(SlotBooking.project),
            joinedload(SlotBooking.asset)
        )\
        .order_by(SlotBooking.booking_date.asc(), SlotBooking.start_time.asc())\
        .all()

    return bookings

def check_subcontractor_availability(
    db: Session,
    subcontractor_id: UUID,
    check_date: date,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> Dict[str, Any]:
    """Check if a subcontractor is available on a specific date/time"""

    # Convert string times to time objects
    if isinstance(start_time, str):
        start_time = time.fromisoformat(start_time)
    if isinstance(end_time, str):
        end_time = time.fromisoformat(end_time)

    # Get existing bookings around that date to catch overnight spans
    date_window = [
        check_date - timedelta(days=1),
        check_date,
        check_date + timedelta(days=1),
    ]
    existing_bookings_query = db.query(SlotBooking)\
        .filter(
            and_(
                SlotBooking.subcontractor_id == subcontractor_id,
                SlotBooking.booking_date.in_(date_window),
                SlotBooking.status != BookingStatus.CANCELLED
            )
        )\
        .options(joinedload(SlotBooking.project))
    
    existing_bookings = existing_bookings_query.all()
    
    # Format existing bookings
    bookings_list = []
    conflicts = []
    is_available = True
    
    for booking in existing_bookings:
        booking_info = {
            "booking_id": booking.id,
            "project_name": booking.project.name if booking.project else None,
            "start_time": booking.start_time,
            "end_time": booking.end_time,
            "status": booking.status
        }
        bookings_list.append(booking_info)
        
        # Check for conflicts if time range provided
        if start_time and end_time:
            # Normalize requested range to datetime to handle overnight spans
            req_start = datetime.combine(check_date, start_time)
            req_end = datetime.combine(check_date, end_time)
            if req_end <= req_start:
                req_end += timedelta(days=1)

            # Normalize existing booking range based on its booking date
            bk_start = datetime.combine(booking.booking_date, booking.start_time)
            bk_end = datetime.combine(booking.booking_date, booking.end_time)
            if bk_end <= bk_start:
                bk_end += timedelta(days=1)

            # Two intervals overlap iff each starts before the other ends
            if bk_start < req_end and req_start < bk_end:
                is_available = False
                conflicts.append(booking_info)
    
    # If no specific time provided and bookings exist, consider unavailable
    if not start_time and not end_time and existing_bookings:
        is_available = False
        conflicts = bookings_list
    
    return {
        "subcontractor_id": subcontractor_id,
        "date": check_date,
        "is_available": is_available,
        "existing_bookings": bookings_list,
        "conflicts": conflicts
    }

def get_subcontractor_statistics(
    db: Session,
    subcontractor_id: UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> Dict[str, Any]:
    """Get statistics for a subcontractor"""
    
    subcontractor = get_subcontractor_with_details(db, subcontractor_id)
    
    if not subcontractor:
        return {}
    
    # Project statistics
    total_projects = len(subcontractor.assigned_projects)
    active_projects = sum(
        1 for p in subcontractor.assigned_projects
        if p.status == ProjectStatus.ACTIVE.value or p.status is None
    )
    completed_projects = sum(
        1 for p in subcontractor.assigned_projects
        if p.status == ProjectStatus.COMPLETED.value
    )
    
    # Booking statistics with date filtering
    bookings_query = db.query(SlotBooking).filter(
        SlotBooking.subcontractor_id == subcontractor_id
    )
    
    if start_date:
        bookings_query = bookings_query.filter(SlotBooking.booking_date >= start_date)
    if end_date:
        bookings_query = bookings_query.filter(SlotBooking.booking_date <= end_date)
    
    bookings = bookings_query.all()
    
    total_bookings = len(bookings)
    completed_bookings = sum(1 for b in bookings if b.status == BookingStatus.COMPLETED)
    cancelled_bookings = sum(1 for b in bookings if b.status == BookingStatus.CANCELLED)
    confirmed_bookings = sum(1 for b in bookings if b.status == BookingStatus.CONFIRMED)
    upcoming_bookings = sum(
        1 for b in bookings
        if b.status == BookingStatus.CONFIRMED and b.booking_date >= date.today()
    )

    # Calculate total hours from Time columns
    total_hours = 0.0
    for booking in bookings:
        if booking.start_time and booking.end_time and booking.status != BookingStatus.CANCELLED:
            start_dt = datetime.combine(date.today(), booking.start_time)
            end_dt = datetime.combine(date.today(), booking.end_time)
            # Handle overnight bookings (e.g., 22:00 → 02:00)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            hours = (end_dt - start_dt).total_seconds() / 3600
            if hours > 0:
                total_hours += hours
    
    return {
        "subcontractor_id": subcontractor_id,
        "subcontractor_name": f"{subcontractor.first_name} {subcontractor.last_name}",
        "company_name": subcontractor.company_name,
        "trade_specialty": subcontractor.trade_specialty,
        "is_active": subcontractor.is_active,
        "statistics": {
            "projects": {
                "total": total_projects,
                "active": active_projects,
                "completed": completed_projects
            },
            "bookings": {
                "total": total_bookings,
                "completed": completed_bookings,
                "cancelled": cancelled_bookings,
                "confirmed": confirmed_bookings,
                "upcoming": upcoming_bookings,
                "total_hours": round(total_hours, 2)
            },
            "period": {
                "start_date": start_date,
                "end_date": end_date
            }
        }
    }

def get_subcontractors_by_trade(
    db: Session,
    trade_specialty: str,
    is_active: bool = True,
    skip: int = 0,
    limit: int = 100
) -> List[Subcontractor]:
    """Get all subcontractors with a specific trade specialty"""
    query = db.query(Subcontractor).filter(
        Subcontractor.trade_specialty == trade_specialty
    )
    
    if is_active is not None:
        query = query.filter(Subcontractor.is_active == is_active)
    
    return query.order_by(Subcontractor.company_name.asc())\
        .offset(skip).limit(limit).all()

def get_available_subcontractors_for_date(
    db: Session,
    check_date: date,
    trade_specialty: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> List[Subcontractor]:
    """Get all available subcontractors for a specific date/time"""

    # Convert string times to time objects
    if isinstance(start_time, str):
        start_time = time.fromisoformat(start_time)
    if isinstance(end_time, str):
        end_time = time.fromisoformat(end_time)

    # Start with all active subcontractors
    query = db.query(Subcontractor).filter(Subcontractor.is_active == True)

    # Filter by trade if specified
    if trade_specialty:
        query = query.filter(Subcontractor.trade_specialty == trade_specialty)

    if start_time and end_time:
        req_start = datetime.combine(check_date, start_time)
        req_end = datetime.combine(check_date, end_time)
        if req_end <= req_start:
            req_end += timedelta(days=1)

        date_window = [
            check_date - timedelta(days=1),
            check_date,
            check_date + timedelta(days=1),
        ]

        bookings = db.query(SlotBooking).filter(
            SlotBooking.booking_date.in_(date_window),
            SlotBooking.status != BookingStatus.CANCELLED,
            SlotBooking.subcontractor_id.isnot(None),
        ).all()

        busy_ids = set()
        for booking in bookings:
            bk_start = datetime.combine(booking.booking_date, booking.start_time)
            bk_end = datetime.combine(booking.booking_date, booking.end_time)
            if bk_end <= bk_start:
                bk_end += timedelta(days=1)

            if bk_start < req_end and req_start < bk_end:
                busy_ids.add(booking.subcontractor_id)

        if busy_ids:
            query = query.filter(Subcontractor.id.notin_(busy_ids))
    else:
        busy_sub_ids = db.query(SlotBooking.subcontractor_id).filter(
            SlotBooking.booking_date == check_date,
            SlotBooking.status != BookingStatus.CANCELLED,
            SlotBooking.subcontractor_id.isnot(None)
        ).distinct().subquery()

        # Exclude busy subcontractors
        query = query.filter(Subcontractor.id.notin_(db.query(busy_sub_ids)))

    return query.all()

def get_subcontractor_current_projects(
    db: Session,
    subcontractor_id: UUID
) -> List[SiteProject]:
    """Get current active projects for a subcontractor"""
    subcontractor = get_subcontractor_with_details(db, subcontractor_id)
    
    if not subcontractor:
        return []
    
    # Return only active projects
    return [
        p for p in subcontractor.assigned_projects
        if p.status == ProjectStatus.ACTIVE.value or p.status is None
    ]

def count_subcontractor_bookings_by_status(
    db: Session,
    subcontractor_id: UUID,
    status: BookingStatus
) -> int:
    """Count bookings for a subcontractor by status"""
    return db.query(SlotBooking).filter(
        and_(
            SlotBooking.subcontractor_id == subcontractor_id,
            SlotBooking.status == status
        )
    ).count()

# ========================================================
# Manager Relationship Functions
# ========================================================

def get_subcontractors_for_manager(
    db: Session,
    manager_id: UUID,
    skip: int = 0,
    limit: int = 100,
    is_active: Optional[bool] = None,
    trade_specialty: Optional[str] = None,
    project_id: Optional[UUID] = None
) -> dict:
    """Get all subcontractors working on projects managed by a specific manager"""
    
    # First, verify the user is a manager
    manager = db.query(User).filter(
        User.id == manager_id,
        User.role.in_(["manager", "admin"])
    ).first()
    
    if not manager:
        return {
            "subcontractors": [],
            "total": 0,
            "skip": skip,
            "limit": limit,
            "has_more": False
        }
    
    # Build the query for subcontractors through projects
    query = db.query(Subcontractor).distinct()
    
    # Join through the association tables
    query = query.join(
        Subcontractor.assigned_projects
    ).join(
        SiteProject.managers
    ).filter(
        User.id == manager_id
    )
    
    # Filter by specific project if provided
    if project_id:
        query = query.filter(SiteProject.id == project_id)
    
    # Apply other filters
    if is_active is not None:
        query = query.filter(Subcontractor.is_active == is_active)
    
    if trade_specialty:
        query = query.filter(Subcontractor.trade_specialty == trade_specialty)
    
    total = query.count()
    subcontractors = query.order_by(Subcontractor.company_name.asc())\
        .offset(skip).limit(limit).all()
    
    return {
        "subcontractors": subcontractors,
        "total": total,
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < total
    }

def get_managers_for_subcontractor(
    db: Session,
    subcontractor_id: UUID
) -> List[Dict[str, Any]]:
    """Get all managers who oversee projects this subcontractor works on"""
    
    # Get managers through the project relationships
    managers_query = db.query(User, SiteProject).distinct(User.id)\
        .join(
            SiteProject.managers
        ).join(
            SiteProject.subcontractors
        ).filter(
            Subcontractor.id == subcontractor_id
        )
    
    results = []
    for manager, project in managers_query.all():
        results.append({
            "manager": manager,
            "project": project
        })
    
    return results

def check_manager_can_access_subcontractor(
    db: Session,
    manager_id: UUID,
    subcontractor_id: UUID
) -> bool:
    """Check if a manager has access to a specific subcontractor through shared projects"""
    
    # Check if they share any projects
    shared_projects = db.query(SiteProject).join(
        SiteProject.managers
    ).join(
        SiteProject.subcontractors
    ).filter(
        User.id == manager_id,
        Subcontractor.id == subcontractor_id
    ).count()
    
    return shared_projects > 0

def get_subcontractor_projects_by_manager(
    db: Session,
    subcontractor_id: UUID,
    manager_id: UUID
) -> List[SiteProject]:
    """Get projects where both the manager and subcontractor are assigned"""
    
    projects = db.query(SiteProject).join(
        SiteProject.managers
    ).join(
        SiteProject.subcontractors
    ).filter(
        User.id == manager_id,
        Subcontractor.id == subcontractor_id
    ).all()
    
    return projects

def get_manager_statistics(
    db: Session,
    manager_id: UUID
) -> Dict[str, Any]:
    """Get statistics about subcontractors under a manager"""
    
    # Get unique subcontractors
    subcontractors = db.query(Subcontractor).distinct().join(
        Subcontractor.assigned_projects
    ).join(
        SiteProject.managers
    ).filter(
        User.id == manager_id
    ).all()
    
    # Group by trade specialty
    trade_counts = {}
    for sub in subcontractors:
        trade = sub.trade_specialty or "Not Specified"
        trade_counts[trade] = trade_counts.get(trade, 0) + 1
    
    # Get active vs inactive
    active_count = sum(1 for s in subcontractors if s.is_active)
    inactive_count = len(subcontractors) - active_count
    
    return {
        "total_subcontractors": len(subcontractors),
        "active_subcontractors": active_count,
        "inactive_subcontractors": inactive_count,
        "by_trade": trade_counts,
        "subcontractor_list": subcontractors
    }


def assign_subcontractor_to_project(db: Session, subcontractor_id: UUID, project_id: UUID) -> bool:
    """
    Assign a subcontractor to a specific project.
    """
    subcontractor = get_subcontractor(db, subcontractor_id)
    # We need to query SiteProject. Ensure you import SiteProject model at the top
    project = db.query(SiteProject).filter(SiteProject.id == project_id).first()
    
    if not subcontractor or not project:
        return False
    
    # Check if already assigned
    if project in subcontractor.assigned_projects:
        return True
        
    subcontractor.assigned_projects.append(project)
    db.commit()
    return True

def remove_subcontractor_from_project(db: Session, subcontractor_id: UUID, project_id: UUID) -> bool:
    """
    Remove a subcontractor from a specific project.
    """
    subcontractor = get_subcontractor(db, subcontractor_id)
    project = db.query(SiteProject).filter(SiteProject.id == project_id).first()
    
    if not subcontractor or not project:
        return False
        
    if project in subcontractor.assigned_projects:
        subcontractor.assigned_projects.remove(project)
        db.commit()
        return True
        
    return False