from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID
from datetime import date

from ...core.database import get_db
from ...core.security import get_current_active_user
from ...crud import subcontractor as subcontractor_crud
from ...models.user import User
from ...schemas.subcontractor import (
    SubcontractorCreate,
    SubcontractorUpdate,
    SubcontractorResponse,
    SubcontractorDetailResponse,
    SubcontractorListResponse,
    ProjectAssignmentResponse
)
from ...schemas.base import MessageResponse

router = APIRouter(prefix="/subcontractors", tags=["Subcontractors"])

# ===== HELPER FUNCTIONS =====

async def verify_manager_access(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> bool:
    """Verify that the current user can access this subcontractor"""
    
    # Admins can access all
    if current_user.role == "admin":
        return True
    
    # Managers can only access subcontractors on their projects
    if current_user.role == "manager":
        has_access = subcontractor_crud.check_manager_can_access_subcontractor(
            db,
            manager_id=current_user.id,
            subcontractor_id=subcontractor_id
        )
        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this subcontractor"
            )
        return True
    
    # Other roles don't have access
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions"
    )

# ===== STATIC ROUTES FIRST (NO PARAMETERS) =====

@router.get("/my-subcontractors", response_model=SubcontractorListResponse)
def get_my_subcontractors(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    trade_specialty: Optional[str] = Query(None, description="Filter by trade specialty"),
    project_id: Optional[UUID] = Query(None, description="Filter by specific project"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get subcontractors for the current manager's projects.
    Admins see all subcontractors, managers see only their project subcontractors.
    """
    if current_user.role == "admin":
        # Admins get all subcontractors
        result = subcontractor_crud.get_all_subcontractors(
            db,
            skip=skip,
            limit=limit,
            is_active=is_active,
            trade_specialty=trade_specialty
        )
    elif current_user.role == "manager":
        # Managers get only their subcontractors
        result = subcontractor_crud.get_subcontractors_for_manager(
            db,
            manager_id=current_user.id,
            skip=skip,
            limit=limit,
            is_active=is_active,
            trade_specialty=trade_specialty,
            project_id=project_id
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and admins can access this endpoint"
        )
    
    return SubcontractorListResponse(
        subcontractors=[
            SubcontractorResponse(
                id=s.id,
                email=s.email,
                first_name=s.first_name,
                last_name=s.last_name,
                company_name=s.company_name,
                trade_specialty=s.trade_specialty,
                phone=s.phone,
                is_active=s.is_active,
                created_at=s.created_at,
                updated_at=s.updated_at
            ) for s in result["subcontractors"]
        ],
        total=result["total"],
        skip=result["skip"],
        limit=result["limit"],
        has_more=result["has_more"]
    )

@router.get("/manager-stats", response_model=dict)
def get_manager_subcontractor_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get statistics about subcontractors under the current manager.
    """
    if current_user.role not in ["manager", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and admins can access this endpoint"
        )
    
    stats = subcontractor_crud.get_manager_statistics(db, current_user.id)
    
    # Don't return the full subcontractor list in stats, just counts
    stats.pop("subcontractor_list", None)
    
    return stats

@router.get("/search", response_model=SubcontractorListResponse)
def search_subcontractors(
    search_term: Optional[str] = Query(None, description="Search in name, company, email"),
    trade_specialty: Optional[str] = Query(None, description="Filter by trade specialty"),
    is_active: Optional[bool] = Query(True, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Search subcontractors by name, company, email, or trade specialty.
    """
    result = subcontractor_crud.search_subcontractors(
        db,
        search_term=search_term,
        trade_specialty=trade_specialty,
        is_active=is_active,
        skip=skip,
        limit=limit
    )
    
    return SubcontractorListResponse(
        subcontractors=[
            SubcontractorResponse(
                id=s.id,
                email=s.email,
                first_name=s.first_name,
                last_name=s.last_name,
                company_name=s.company_name,
                trade_specialty=s.trade_specialty,
                phone=s.phone,
                is_active=s.is_active,
                created_at=s.created_at,
                updated_at=s.updated_at
            ) for s in result["subcontractors"]
        ],
        total=result["total"],
        skip=result["skip"],
        limit=result["limit"],
        has_more=result["has_more"]
    )

@router.get("/available", response_model=List[SubcontractorResponse])
def get_available_subcontractors(
    check_date: date = Query(..., description="Date to check availability"),
    trade_specialty: Optional[str] = Query(None, description="Filter by trade specialty"),
    start_time: Optional[str] = Query(None, description="Start time (HH:MM format)"),
    end_time: Optional[str] = Query(None, description="End time (HH:MM format)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all available subcontractors for a specific date and time.
    Useful for scheduling and finding replacements.
    """
    available = subcontractor_crud.get_available_subcontractors_for_date(
        db,
        check_date=check_date,
        trade_specialty=trade_specialty,
        start_time=start_time,
        end_time=end_time
    )
    
    return [
        SubcontractorResponse(
            id=s.id,
            email=s.email,
            first_name=s.first_name,
            last_name=s.last_name,
            company_name=s.company_name,
            trade_specialty=s.trade_specialty,
            phone=s.phone,
            is_active=s.is_active,
            created_at=s.created_at,
            updated_at=s.updated_at
        ) for s in available
    ]

# ===== STATIC ROUTES WITH SPECIFIC PARAMETERS =====

@router.get("/by-trade/{trade_specialty}", response_model=List[SubcontractorResponse])
def get_subcontractors_by_trade(
    trade_specialty: str,
    is_active: bool = Query(True, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all subcontractors with a specific trade specialty.
    """
    subcontractors = subcontractor_crud.get_subcontractors_by_trade(
        db,
        trade_specialty=trade_specialty,
        is_active=is_active,
        skip=skip,
        limit=limit
    )
    
    return [
        SubcontractorResponse(
            id=s.id,
            email=s.email,
            first_name=s.first_name,
            last_name=s.last_name,
            company_name=s.company_name,
            trade_specialty=s.trade_specialty,
            phone=s.phone,
            is_active=s.is_active,
            created_at=s.created_at,
            updated_at=s.updated_at
        ) for s in subcontractors
    ]

# ===== ROOT ENDPOINTS (/ PATH) =====

@router.get("/", response_model=SubcontractorListResponse)
def get_all_subcontractors(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    trade_specialty: Optional[str] = Query(None, description="Filter by trade specialty"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all subcontractors with pagination and optional filters.
    """
    result = subcontractor_crud.get_all_subcontractors(
        db,
        skip=skip,
        limit=limit,
        is_active=is_active,
        trade_specialty=trade_specialty
    )
    
    return SubcontractorListResponse(
        subcontractors=[
            SubcontractorResponse(
                id=s.id,
                email=s.email,
                first_name=s.first_name,
                last_name=s.last_name,
                company_name=s.company_name,
                trade_specialty=s.trade_specialty,
                phone=s.phone,
                is_active=s.is_active,
                created_at=s.created_at,
                updated_at=s.updated_at
            ) for s in result["subcontractors"]
        ],
        total=result["total"],
        skip=result["skip"],
        limit=result["limit"],
        has_more=result["has_more"]
    )

@router.post("/", response_model=SubcontractorResponse, status_code=status.HTTP_201_CREATED)
def create_subcontractor(
    subcontractor_data: SubcontractorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a new subcontractor.
    
    Required fields:
    - email: Unique email address
    - password: Password for the subcontractor account
    - first_name: First name
    - last_name: Last name
    - company_name: Company name
    - trade_specialty: Trade specialty (enum)
    - phone: Contact phone number
    """
    # Check if email already exists
    existing_subcontractor = subcontractor_crud.get_subcontractor_by_email(
        db, 
        email=subcontractor_data.email
    )
    
    if existing_subcontractor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create the subcontractor
    try:
        new_subcontractor = subcontractor_crud.create_subcontractor(
            db, 
            subcontractor_data
        )
        
        return SubcontractorResponse(
            id=new_subcontractor.id,
            email=new_subcontractor.email,
            first_name=new_subcontractor.first_name,
            last_name=new_subcontractor.last_name,
            company_name=new_subcontractor.company_name,
            trade_specialty=new_subcontractor.trade_specialty,
            phone=new_subcontractor.phone,
            is_active=new_subcontractor.is_active,
            created_at=new_subcontractor.created_at,
            updated_at=new_subcontractor.updated_at
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating subcontractor: {str(e)}"
        )

# ===== PARAMETERIZED ROUTES (/{subcontractor_id}) - MUST BE LAST =====

@router.get("/{subcontractor_id}", response_model=SubcontractorDetailResponse)
def get_subcontractor(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get detailed information about a specific subcontractor.
    """
    subcontractor = subcontractor_crud.get_subcontractor_with_details(
        db, 
        subcontractor_id
    )
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # Format the response with relationships
    return SubcontractorDetailResponse(
        id=subcontractor.id,
        email=subcontractor.email,
        first_name=subcontractor.first_name,
        last_name=subcontractor.last_name,
        company_name=subcontractor.company_name,
        trade_specialty=subcontractor.trade_specialty,
        phone=subcontractor.phone,
        is_active=subcontractor.is_active,
        created_at=subcontractor.created_at,
        updated_at=subcontractor.updated_at,
        total_projects=len(subcontractor.assigned_projects),
        active_projects=sum(1 for p in subcontractor.assigned_projects 
                          if p.status == "active" or p.status is None),
        total_bookings=len(subcontractor.bookings),
        upcoming_bookings=sum(1 for b in subcontractor.bookings 
                             if b.status == "confirmed" and b.booking_date >= date.today())
    )

@router.put("/{subcontractor_id}", response_model=SubcontractorResponse)
def update_subcontractor(
    subcontractor_id: UUID,
    subcontractor_update: SubcontractorUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update a subcontractor's information.
    """
    updated_subcontractor = subcontractor_crud.update_subcontractor(
        db,
        subcontractor_id,
        subcontractor_update
    )
    
    if not updated_subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    return SubcontractorResponse(
        id=updated_subcontractor.id,
        email=updated_subcontractor.email,
        first_name=updated_subcontractor.first_name,
        last_name=updated_subcontractor.last_name,
        company_name=updated_subcontractor.company_name,
        trade_specialty=updated_subcontractor.trade_specialty,
        phone=updated_subcontractor.phone,
        is_active=updated_subcontractor.is_active,
        created_at=updated_subcontractor.created_at,
        updated_at=updated_subcontractor.updated_at
    )

@router.delete("/{subcontractor_id}", response_model=MessageResponse)
def delete_subcontractor(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Soft delete a subcontractor (sets is_active to False).
    """
    success = subcontractor_crud.delete_subcontractor(db, subcontractor_id)
    
    if success:
        return MessageResponse(
            message="Subcontractor deactivated successfully",
            success=True
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )

@router.delete("/{subcontractor_id}/permanent", response_model=MessageResponse)
def permanently_delete_subcontractor(
    subcontractor_id: UUID,
    confirm: bool = Query(False, description="Confirm permanent deletion"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Permanently delete a subcontractor from the database.
    This action cannot be undone. Requires confirmation.
    
    Note: This should typically be restricted to admin users only.
    """
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permanent deletion requires confirmation"
        )
    
    # You might want to add additional permission check here
    # if not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Only administrators can permanently delete subcontractors"
    #     )
    
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # Check if subcontractor has active bookings
    active_bookings = subcontractor_crud.count_subcontractor_bookings_by_status(
        db, 
        subcontractor_id, 
        "confirmed"
    )
    
    if active_bookings > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete subcontractor with {active_bookings} active bookings"
        )
    
    success = subcontractor_crud.hard_delete_subcontractor(db, subcontractor_id)
    
    if success:
        return MessageResponse(
            message=f"Subcontractor permanently deleted",
            success=True
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete subcontractor"
        )

@router.get("/{subcontractor_id}/projects", response_model=List[ProjectAssignmentResponse])
def get_subcontractor_projects(
    subcontractor_id: UUID,
    is_active: Optional[bool] = Query(None, description="Filter by active project status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all projects assigned to a subcontractor.
    """
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # Get projects through the relationship
    projects = []
    for project in subcontractor.assigned_projects[skip:skip + limit]:
        # Filter by active status if specified
        if is_active is not None and (project.status == "active") != is_active:
            continue
            
        projects.append(ProjectAssignmentResponse(
            project_id=project.id,
            project_name=project.name,
            project_location=project.location,
            assigned_date=project.created_at.date(),  # Or you might have a specific assigned_date
            hourly_rate=None,  # This would need to come from the association table if tracked
            is_active=project.status == "active"
        ))
    
    return projects

@router.get("/{subcontractor_id}/projects/current", response_model=List[dict])
def get_current_projects(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get only current active projects for a subcontractor.
    """
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    current_projects = subcontractor_crud.get_subcontractor_current_projects(
        db, 
        subcontractor_id
    )
    
    return [
        {
            "project_id": p.id,
            "project_name": p.name,
            "project_location": p.location,
            "project_status": p.status,
            "start_date": p.start_date,
            "end_date": p.end_date,
            "description": p.description
        } for p in current_projects
    ]

@router.get("/{subcontractor_id}/bookings", response_model=List[dict])
def get_subcontractor_bookings(
    subcontractor_id: UUID,
    project_id: Optional[UUID] = Query(None, description="Filter by project"),
    start_date: Optional[date] = Query(None, description="Filter bookings from this date"),
    end_date: Optional[date] = Query(None, description="Filter bookings until this date"),
    status: Optional[str] = Query(None, description="Filter by booking status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all slot bookings for a subcontractor.
    """
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # Build query for bookings
    bookings_query = db.query(subcontractor.bookings)
    
    # Apply filters
    if project_id:
        bookings_query = bookings_query.filter_by(project_id=project_id)
    
    if start_date:
        bookings_query = bookings_query.filter(
            subcontractor.bookings.booking_date >= start_date
        )
    
    if end_date:
        bookings_query = bookings_query.filter(
            subcontractor.bookings.booking_date <= end_date
        )
    
    if status:
        bookings_query = bookings_query.filter_by(status=status)
    
    # Get paginated results
    total = bookings_query.count()
    bookings = bookings_query.offset(skip).limit(limit).all()
    
    # Format response
    booking_list = []
    for booking in bookings:
        booking_list.append({
            "id": booking.id,
            "project_id": booking.project_id,
            "project_name": booking.project.name if booking.project else None,
            "asset_id": booking.asset_id,
            "asset_name": booking.asset.name if booking.asset else None,
            "slot_id": booking.slot_id,
            "booking_date": booking.booking_date,
            "start_time": booking.start_time,
            "end_time": booking.end_time,
            "status": booking.status,
            "notes": booking.notes,
            "created_at": booking.created_at,
            "updated_at": booking.updated_at
        })
    
    return booking_list

@router.get("/{subcontractor_id}/bookings/upcoming", response_model=List[dict])
def get_upcoming_bookings(
    subcontractor_id: UUID,
    days_ahead: int = Query(7, ge=1, le=90, description="Number of days to look ahead"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get upcoming bookings for a subcontractor in the next N days.
    """
    from datetime import datetime, timedelta
    
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # Calculate date range
    today = datetime.now().date()
    end_date = today + timedelta(days=days_ahead)
    
    # Get upcoming bookings
    upcoming_bookings = []
    for booking in subcontractor.bookings:
        if today <= booking.booking_date <= end_date and booking.status != "cancelled":
            upcoming_bookings.append({
                "id": booking.id,
                "project_id": booking.project_id,
                "project_name": booking.project.name if booking.project else None,
                "asset_id": booking.asset_id,
                "asset_name": booking.asset.name if booking.asset else None,
                "booking_date": booking.booking_date,
                "start_time": booking.start_time,
                "end_time": booking.end_time,
                "status": booking.status,
                "notes": booking.notes
            })
    
    # Sort by date and time
    upcoming_bookings.sort(key=lambda x: (x["booking_date"], x["start_time"]))
    
    return upcoming_bookings

@router.get("/{subcontractor_id}/bookings/count-by-status", response_model=dict)
def get_booking_counts(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get count of bookings grouped by status for a subcontractor.
    """
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    statuses = ["pending", "confirmed", "completed", "cancelled"]
    counts = {}
    
    for status in statuses:
        counts[status] = subcontractor_crud.count_subcontractor_bookings_by_status(
            db, 
            subcontractor_id, 
            status
        )
    
    return {
        "subcontractor_id": subcontractor_id,
        "booking_counts": counts,
        "total": sum(counts.values())
    }

@router.get("/{subcontractor_id}/availability", response_model=dict)
def check_subcontractor_availability(
    subcontractor_id: UUID,
    check_date: date = Query(..., description="Date to check availability"),
    start_time: Optional[str] = Query(None, description="Start time (HH:MM format)"),
    end_time: Optional[str] = Query(None, description="End time (HH:MM format)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Check if a subcontractor is available on a specific date/time.
    """
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # Check for existing bookings on that date
    existing_bookings = []
    for booking in subcontractor.bookings:
        if booking.booking_date == check_date and booking.status != "cancelled":
            existing_bookings.append({
                "booking_id": booking.id,
                "project_name": booking.project.name if booking.project else None,
                "start_time": booking.start_time,
                "end_time": booking.end_time,
                "status": booking.status
            })
    
    # Check for conflicts if time range provided
    is_available = True
    conflicts = []
    
    if start_time and end_time and existing_bookings:
        for booking in existing_bookings:
            # Check for time overlap
            if (booking["start_time"] <= start_time < booking["end_time"] or
                booking["start_time"] < end_time <= booking["end_time"] or
                (start_time <= booking["start_time"] and end_time >= booking["end_time"])):
                is_available = False
                conflicts.append(booking)
    elif existing_bookings:
        # If no specific time provided, consider unavailable if any bookings exist
        is_available = False
        conflicts = existing_bookings
    
    return {
        "subcontractor_id": subcontractor_id,
        "date": check_date,
        "is_available": is_available,
        "existing_bookings": existing_bookings,
        "conflicts": conflicts
    }

@router.get("/{subcontractor_id}/statistics", response_model=dict)
def get_subcontractor_statistics(
    subcontractor_id: UUID,
    start_date: Optional[date] = Query(None, description="Start date for statistics"),
    end_date: Optional[date] = Query(None, description="End date for statistics"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get statistics for a subcontractor (bookings, projects, etc.).
    """
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # Calculate statistics
    total_projects = len(subcontractor.assigned_projects)
    active_projects = sum(1 for p in subcontractor.assigned_projects if p.status == "active")
    
    # Filter bookings by date range if provided
    bookings = subcontractor.bookings
    if start_date:
        bookings = [b for b in bookings if b.booking_date >= start_date]
    if end_date:
        bookings = [b for b in bookings if b.booking_date <= end_date]
    
    total_bookings = len(bookings)
    completed_bookings = sum(1 for b in bookings if b.status == "completed")
    cancelled_bookings = sum(1 for b in bookings if b.status == "cancelled")
    upcoming_bookings = sum(1 for b in bookings if b.status == "confirmed" and b.booking_date >= date.today())
    
    # Calculate booking hours (if you track this)
    total_hours = 0  # You'd need to calculate based on start_time and end_time
    
    return {
        "subcontractor_id": subcontractor_id,
        "subcontractor_name": f"{subcontractor.first_name} {subcontractor.last_name}",
        "company_name": subcontractor.company_name,
        "trade_specialty": subcontractor.trade_specialty,
        "statistics": {
            "projects": {
                "total": total_projects,
                "active": active_projects,
                "completed": total_projects - active_projects
            },
            "bookings": {
                "total": total_bookings,
                "completed": completed_bookings,
                "cancelled": cancelled_bookings,
                "upcoming": upcoming_bookings
            },
            "period": {
                "start_date": start_date,
                "end_date": end_date
            }
        }
    }