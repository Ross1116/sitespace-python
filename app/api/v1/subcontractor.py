# app/api/v1/endpoints/subcontractor.py
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List, Union, Any
from uuid import UUID
from datetime import date, datetime, timedelta

from ...core.database import get_db
from ...core.security import get_current_active_user, create_password_reset_token, verify_password
from ...core.email import send_subcontractor_invite_email 
from ...crud import subcontractor as subcontractor_crud
from ...models.user import User
# We handle the Subcontractor model via CRUD, but might need it for typing if available
# from ...models.subcontractor import Subcontractor 

from ...schemas.subcontractor import (
    SubcontractorCreate,
    SubcontractorUpdate,
    SubcontractorPasswordUpdate,
    SubcontractorResponse,
    SubcontractorDetailResponse,
    SubcontractorListResponse,
    ProjectAssignmentResponse
)
from ...schemas.base import MessageResponse

router = APIRouter(prefix="/subcontractors", tags=["Subcontractors"])

# ========================================================
# HELPER FUNCTIONS
# ========================================================

async def verify_manager_access(
    subcontractor_id: UUID,
    db: Session,
    current_user: User
) -> bool:
    """Verify that the current user can access this subcontractor"""
    
    if current_user.role == "admin":
        return True
    
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
    
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions"
    )

# ========================================================
# LIST & SEARCH ROUTES
# ========================================================

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
    user_role = getattr(current_user, "role", None)

    if user_role == "admin":
        result = subcontractor_crud.get_all_subcontractors(
            db,
            skip=skip,
            limit=limit,
            is_active=is_active,
            trade_specialty=trade_specialty
        )
    elif user_role == "manager":
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
        # This handles Subcontractors (who have no role attribute) gracefully
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
    If email already exists, it will attempt to assign the existing subcontractor to the project (if provided).
    """
    # Import locally to avoid circular dependencies
    from ...models.site_project import SiteProject 

    # Check for existing email
    existing_subcontractor = subcontractor_crud.get_subcontractor_by_email(
        db, email=subcontractor_data.email
    )
    
    if existing_subcontractor:
        # If they exist AND we have a project_id, just link them!
        if subcontractor_data.project_id:
            # (Optional: Check if manager owns this project for security)
            if current_user.role == "manager":
                project = db.query(SiteProject).filter(
                    SiteProject.id == subcontractor_data.project_id,
                    SiteProject.managers.any(id=current_user.id)
                ).first()
                if not project:
                    raise HTTPException(status_code=403, detail="Cannot assign to this project")

            # Perform the assignment
            subcontractor_crud.assign_subcontractor_to_project(
                db, existing_subcontractor.id, subcontractor_data.project_id
            )
            return existing_subcontractor
        else:
            raise HTTPException(status_code=400, detail="Email already registered")

    # Create new subcontractor
    new_subcontractor = subcontractor_crud.create_subcontractor(db, subcontractor_data)

    # Immediately assign to project if ID is present
    if subcontractor_data.project_id:
        if current_user.role == "manager":
            # Verify manager owns the project
            project = db.query(SiteProject).filter(
                SiteProject.id == subcontractor_data.project_id,
                SiteProject.managers.any(id=current_user.id)
            ).first()
            
            if project:
                subcontractor_crud.assign_subcontractor_to_project(
                    db, new_subcontractor.id, subcontractor_data.project_id
                )
        elif current_user.role == "admin":
            subcontractor_crud.assign_subcontractor_to_project(
                db, new_subcontractor.id, subcontractor_data.project_id
            )
    
    return new_subcontractor

# ========================================================
# SELF-UPDATE ROUTES (MUST BE BEFORE PARAMETERIZED ROUTES)
# ========================================================

@router.put("/me", response_model=SubcontractorResponse)
def update_subcontractor_me(
    subcontractor_update: SubcontractorUpdate,
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_active_user)
):
    """
    Update logged-in subcontractor's own profile.
    """
    # 1. Verify the user is a subcontractor
    # We check for a property specific to subcontractors (like 'trade_specialty')
    if not hasattr(current_user, "trade_specialty"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only subcontractors can access this endpoint"
        )
    
    # 2. Security: Prevent self-deactivation via this endpoint
    # We manually exclude 'is_active' from the update data
    update_data = subcontractor_update.model_dump(exclude={'is_active'}, exclude_unset=True)
    
    # 3. Check email uniqueness
    if "email" in update_data and update_data["email"] != current_user.email:
        existing = subcontractor_crud.get_subcontractor_by_email(db, update_data["email"])
        if existing and existing.id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

    # 4. Re-construct the update model (safe version) and update
    safe_update = SubcontractorUpdate(**update_data)
    
    updated_sub = subcontractor_crud.update_subcontractor(
        db,
        current_user.id,
        safe_update
    )
    
    return updated_sub

# ========================================================
# SPECIFIC SUBCONTRACTOR ROUTES (/{subcontractor_id})
# ========================================================

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
    Update a subcontractor's information (Manager/Admin access).
    """
    # Permission check
    if current_user.role not in ["manager", "admin"]:
        # If the user is trying to update themselves but didn't use /me, allow it if ID matches
        # Assuming current_user has an 'id' attribute
        if getattr(current_user, 'id', None) != subcontractor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
    
    # Check email uniqueness
    if subcontractor_update.email:
        existing = subcontractor_crud.get_subcontractor_by_email(db, subcontractor_update.email)
        if existing and existing.id != subcontractor_id:
            raise HTTPException(status_code=400, detail="Email already registered")

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
    
    return updated_subcontractor

@router.put("/{subcontractor_id}/password", response_model=MessageResponse)
def update_subcontractor_password(
    subcontractor_id: UUID,
    password_data: SubcontractorPasswordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update a subcontractor's password.
    """
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )

    # Only Admin, Manager, or the Subcontractor themselves can change the password
    can_update = False
    if getattr(current_user, 'role', None) in ["manager", "admin"]:
        can_update = True
    elif getattr(current_user, 'id', None) == subcontractor_id:
        can_update = True
        
    if not can_update:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to update this password"
        )

    # Verify Old Password
    if not verify_password(password_data.current_password, subcontractor.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect current password"
        )

    # Update Password
    subcontractor_crud.update_password(db, subcontractor_id, password_data.new_password)

    return MessageResponse(message="Password updated successfully", success=True)

@router.delete("/{subcontractor_id}", response_model=MessageResponse)
def delete_subcontractor(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Soft delete a subcontractor (sets is_active to False).
    """
    if getattr(current_user, 'role', None) not in ["manager", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and admins can delete subcontractors"
        )
    
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

@router.post("/{subcontractor_id}/send-welcome-email", response_model=MessageResponse)
def send_welcome_email_endpoint(
    subcontractor_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Send welcome email with password setup link to new subcontractor.
    """
    if getattr(current_user, 'role', None) not in ["manager", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and admins can send welcome emails"
        )
    
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # 1. Generate password reset token
    reset_token = create_password_reset_token(subcontractor.email)
    
    # 2. Send email in background
    background_tasks.add_task(
        send_subcontractor_invite_email,
        to_email=subcontractor.email,
        user_name=subcontractor.first_name,
        reset_token=reset_token
    )
    
    return MessageResponse(
        message="Welcome email sent successfully",
        success=True
    )
    
@router.post("/{subcontractor_id}/activate", response_model=MessageResponse)
def activate_subcontractor(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Reactivate a deactivated subcontractor.
    """
    if getattr(current_user, 'role', None) not in ["manager", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and admins can activate subcontractors"
        )
    
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    if subcontractor.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subcontractor is already active"
        )
    
    # Reactivate
    subcontractor_crud.activate_subcontractor(db, subcontractor_id)
    
    return MessageResponse(
        message="Subcontractor activated successfully",
        success=True
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
    """
    if getattr(current_user, 'role', None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can permanently delete subcontractors"
        )
    
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permanent deletion requires confirmation"
        )
    
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
            message="Subcontractor permanently deleted",
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
    
    projects = []
    # Use slice on the relationship
    for project in subcontractor.assigned_projects[skip:skip + limit]:
        if is_active is not None and (project.status == "active") != is_active:
            continue
            
        projects.append(ProjectAssignmentResponse(
            project_id=project.id,
            project_name=project.name,
            project_location=project.location,
            assigned_date=project.created_at.date(),
            hourly_rate=None,
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
    Get all bookings for a subcontractor with optional filters.
    """
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    # Filter bookings via Python list comprehension (or move to CRUD for efficiency)
    bookings = subcontractor.bookings
    
    if project_id:
        bookings = [b for b in bookings if b.project_id == project_id]
    
    if start_date:
        bookings = [b for b in bookings if b.booking_date >= start_date]
    
    if end_date:
        bookings = [b for b in bookings if b.booking_date <= end_date]
    
    if status:
        bookings = [b for b in bookings if b.status == status]
    
    # Sort by date descending
    bookings.sort(key=lambda x: (x.booking_date, x.start_time), reverse=True)
    
    # Paginate
    bookings = bookings[skip:skip + limit]
    
    return [
        {
            "id": b.id,
            "project_id": b.project_id,
            "project_name": b.project.name if b.project else None,
            "asset_id": b.asset_id,
            "asset_name": b.asset.name if b.asset else None,
            "booking_date": b.booking_date,
            "start_time": b.start_time,
            "end_time": b.end_time,
            "status": b.status,
            "notes": b.notes,
            "created_at": b.created_at,
            "updated_at": b.updated_at
        } for b in bookings
    ]

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
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    today = datetime.now().date()
    end_date = today + timedelta(days=days_ahead)
    
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
    
    for status_name in statuses:
        counts[status_name] = subcontractor_crud.count_subcontractor_bookings_by_status(
            db, 
            subcontractor_id, 
            status_name
        )
    
    return {
        "subcontractor_id": subcontractor_id,
        "booking_counts": counts,
        "total": sum(counts.values())
    }

@router.get("/{subcontractor_id}/availability", response_model=dict)
def check_subcontractor_availability_detail(
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
    
    existing_bookings = []
    for booking in subcontractor.bookings:
        if booking.booking_date == check_date and booking.status != "cancelled":
            existing_bookings.append({
                "booking_id": booking.id,
                "project_name": booking.project.name if booking.project else None,
                "start_time": str(booking.start_time),
                "end_time": str(booking.end_time),
                "status": booking.status
            })
    
    is_available = True
    conflicts = []
    
    if start_time and end_time and existing_bookings:
        for booking in existing_bookings:
            # Simplified time check (assuming string comparison works for HH:MM)
            b_start = booking["start_time"]
            b_end = booking["end_time"]
            
            if (b_start <= start_time < b_end or
                b_start < end_time <= b_end or
                (start_time <= b_start and end_time >= b_end)):
                is_available = False
                conflicts.append(booking)
    elif existing_bookings and not (start_time and end_time):
        # If bookings exist but no specific time requested, return them but don't mark whole day busy
        pass 
    
    return {
        "subcontractor_id": subcontractor_id,
        "date": check_date,
        "is_available": is_available,
        "existing_bookings": existing_bookings,
        "conflicts": conflicts
    }
    
@router.post("/{subcontractor_id}/projects/{project_id}", response_model=MessageResponse)
def assign_subcontractor_to_project_endpoint(
    subcontractor_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Assign a subcontractor to a specific project.
    Only Managers and Admins can perform this action.
    """
    from ...models.site_project import SiteProject 
    
    if getattr(current_user, 'role', None) not in ["manager", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and admins can assign subcontractors"
        )

    # Optional: specific manager check
    if getattr(current_user, 'role', None) == "manager":
        # Check if manager owns this project
        project = db.query(SiteProject).filter(
            SiteProject.id == project_id,
            SiteProject.managers.any(id=current_user.id)
        ).first()
        if not project:
             raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a manager of this project"
            )

    success = subcontractor_crud.assign_subcontractor_to_project(db, subcontractor_id, project_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor or Project not found"
        )
        
    return MessageResponse(message="Subcontractor assigned to project successfully", success=True)

@router.delete("/{subcontractor_id}/projects/{project_id}", response_model=MessageResponse)
def remove_subcontractor_from_project_endpoint(
    subcontractor_id: UUID,
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Remove a subcontractor from a project.
    """
    if getattr(current_user, 'role', None) not in ["manager", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions"
        )

    success = subcontractor_crud.remove_subcontractor_from_project(db, subcontractor_id, project_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment not found"
        )
        
    return MessageResponse(message="Subcontractor removed from project successfully", success=True)