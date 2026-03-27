# app/api/v1/endpoints/subcontractor.py
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List, Union, Any
from uuid import UUID
from datetime import date, datetime, timedelta, timezone

from ...core.database import get_db
from ...core.security import get_current_active_user, create_password_reset_token, require_manager_or_admin, verify_password
from ...core.email import send_subcontractor_invite_email 
from ...crud import subcontractor as subcontractor_crud
from ...models.subcontractor import Subcontractor
from ...models.user import User
from ...schemas.subcontractor import (
    SubcontractorCreate,
    SubcontractorUpdate,
    SubcontractorPasswordUpdate,
    SubcontractorResponse,
    SubcontractorDetailResponse,
    SubcontractorListResponse,
    ProjectAssignmentResponse,
    ManagerSubcontractorStatsResponse,
    BookingCountsByStatusResponse,
    SubcontractorAvailabilityResponse,
    SubcontractorBookingSummary,
    SubcontractorProjectSummary,
)
from ...core.constants import (
    SUBCONTRACTOR_PAGE_DEFAULT,
    SUBCONTRACTOR_PAGE_MAX,
    UPCOMING_BOOKINGS_DEFAULT_DAYS_AHEAD,
    UPCOMING_BOOKINGS_MAX_DAYS_AHEAD,
)
from ...schemas.base import MessageResponse
from ...schemas.enums import BookingStatus, ProjectStatus, TradeResolutionStatus, UserRole

router = APIRouter(prefix="/subcontractors", tags=["Subcontractors"])


def _validated_subcontractor_response(subcontractor: Any) -> SubcontractorResponse:
    if isinstance(subcontractor, dict):
        payload = dict(subcontractor)
        if payload.get("trade_resolution_status") is None:
            payload["trade_resolution_status"] = TradeResolutionStatus.UNKNOWN.value
        return SubcontractorResponse.model_validate(payload)

    if getattr(subcontractor, "trade_resolution_status", None) is None:
        setattr(subcontractor, "trade_resolution_status", TradeResolutionStatus.UNKNOWN.value)
    return SubcontractorResponse.model_validate(subcontractor)


# ========================================================
# LIST & SEARCH ROUTES
# ========================================================

@router.get("/my-subcontractors", response_model=SubcontractorListResponse)
def get_my_subcontractors(
    skip: int = Query(0, ge=0),
    limit: int = Query(SUBCONTRACTOR_PAGE_DEFAULT, ge=1, le=SUBCONTRACTOR_PAGE_MAX),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    trade_specialty: Optional[str] = Query(None, description="Filter by trade specialty"),
    trade_resolution_status: Optional[TradeResolutionStatus] = Query(None, description="Filter by trade resolution status"),
    planning_ready: Optional[bool] = Query(None, description="Filter by planning readiness"),
    project_id: Optional[UUID] = Query(None, description="Filter by specific project"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get subcontractors for the current manager's projects.
    Admins see all subcontractors, managers see only their project subcontractors.
    """
    user_role = getattr(current_user, "role", None)

    if user_role == UserRole.ADMIN:
        result = subcontractor_crud.get_all_subcontractors(
            db,
            skip=skip,
            limit=limit,
            is_active=is_active,
            trade_specialty=trade_specialty,
            trade_resolution_status=trade_resolution_status.value if trade_resolution_status else None,
            planning_ready=planning_ready,
        )
    elif user_role == UserRole.MANAGER:
        result = subcontractor_crud.get_subcontractors_for_manager(
            db,
            manager_id=current_user.id,
            skip=skip,
            limit=limit,
            is_active=is_active,
            trade_specialty=trade_specialty,
            trade_resolution_status=trade_resolution_status.value if trade_resolution_status else None,
            planning_ready=planning_ready,
            project_id=project_id
        )
    else:
        # This handles Subcontractors (who have no role attribute) gracefully
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and admins can access this endpoint"
        )
    
    return SubcontractorListResponse(
        subcontractors=[_validated_subcontractor_response(s) for s in result["subcontractors"]],
        total=result["total"],
        skip=result["skip"],
        limit=result["limit"],
        has_more=result["has_more"]
    )

@router.get("/manager-stats", response_model=ManagerSubcontractorStatsResponse)
def get_manager_subcontractor_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get statistics about subcontractors under the current manager.
    """
    if current_user.role not in [UserRole.MANAGER, UserRole.ADMIN]:
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
    trade_resolution_status: Optional[TradeResolutionStatus] = Query(None, description="Filter by trade resolution status"),
    planning_ready: Optional[bool] = Query(None, description="Filter by planning readiness"),
    is_active: Optional[bool] = Query(True, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(SUBCONTRACTOR_PAGE_DEFAULT, ge=1, le=SUBCONTRACTOR_PAGE_MAX),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Search subcontractors by name, company, email, or trade specialty.
    """
    require_manager_or_admin(current_user)

    result = subcontractor_crud.search_subcontractors(
        db,
        search_term=search_term,
        trade_specialty=trade_specialty,
        trade_resolution_status=trade_resolution_status.value if trade_resolution_status else None,
        planning_ready=planning_ready,
        is_active=is_active,
        skip=skip,
        limit=limit
    )
    
    return SubcontractorListResponse(
        subcontractors=[_validated_subcontractor_response(s) for s in result["subcontractors"]],
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
    require_manager_or_admin(current_user)

    available = subcontractor_crud.get_available_subcontractors_for_date(
        db,
        check_date=check_date,
        trade_specialty=trade_specialty,
        start_time=start_time,
        end_time=end_time
    )
    
    return [_validated_subcontractor_response(s) for s in available]

@router.get("/by-trade/{trade_specialty}", response_model=List[SubcontractorResponse])
def get_subcontractors_by_trade(
    trade_specialty: str,
    is_active: bool = Query(True, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(SUBCONTRACTOR_PAGE_DEFAULT, ge=1, le=SUBCONTRACTOR_PAGE_MAX),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all subcontractors with a specific trade specialty.
    """
    require_manager_or_admin(current_user)

    subcontractors = subcontractor_crud.get_subcontractors_by_trade(
        db,
        trade_specialty=trade_specialty,
        is_active=is_active,
        skip=skip,
        limit=limit
    )
    
    return [_validated_subcontractor_response(s) for s in subcontractors]

@router.get("/", response_model=SubcontractorListResponse)
def get_all_subcontractors(
    skip: int = Query(0, ge=0),
    limit: int = Query(SUBCONTRACTOR_PAGE_DEFAULT, ge=1, le=SUBCONTRACTOR_PAGE_MAX),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    trade_specialty: Optional[str] = Query(None, description="Filter by trade specialty"),
    trade_resolution_status: Optional[TradeResolutionStatus] = Query(None, description="Filter by trade resolution status"),
    planning_ready: Optional[bool] = Query(None, description="Filter by planning readiness"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all subcontractors with pagination and optional filters.
    """
    require_manager_or_admin(current_user)

    result = subcontractor_crud.get_all_subcontractors(
        db,
        skip=skip,
        limit=limit,
        is_active=is_active,
        trade_specialty=trade_specialty,
        trade_resolution_status=trade_resolution_status.value if trade_resolution_status else None,
        planning_ready=planning_ready,
    )
    
    return SubcontractorListResponse(
        subcontractors=[_validated_subcontractor_response(s) for s in result["subcontractors"]],
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
    """
    # Import locally to avoid circular dependencies
    from ...models.site_project import SiteProject 

    # Check for existing email
    existing_subcontractor = subcontractor_crud.get_subcontractor_by_email(
        db, email=subcontractor_data.email
    )
    
    if existing_subcontractor:
        if subcontractor_data.project_id:
            if current_user.role == UserRole.MANAGER:
                project = db.query(SiteProject).filter(
                    SiteProject.id == subcontractor_data.project_id,
                    SiteProject.managers.any(id=current_user.id)
                ).first()
                if not project:
                    raise HTTPException(status_code=403, detail="Cannot assign to this project")

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
        if current_user.role == UserRole.MANAGER:
            project = db.query(SiteProject).filter(
                SiteProject.id == subcontractor_data.project_id,
                SiteProject.managers.any(id=current_user.id)
            ).first()
            
            if project:
                subcontractor_crud.assign_subcontractor_to_project(
                    db, new_subcontractor.id, subcontractor_data.project_id
                )
        elif current_user.role == UserRole.ADMIN:
            subcontractor_crud.assign_subcontractor_to_project(
                db, new_subcontractor.id, subcontractor_data.project_id
            )
    
    return new_subcontractor

@router.put("/me", response_model=SubcontractorResponse)
def update_subcontractor_me(
    subcontractor_update: SubcontractorUpdate,
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_active_user)
):
    if not hasattr(current_user, "trade_specialty"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only subcontractors can access this endpoint"
        )
    
    update_data = subcontractor_update.model_dump(exclude={'is_active'}, exclude_unset=True)
    
    if "email" in update_data and update_data["email"] != current_user.email:
        existing = subcontractor_crud.get_subcontractor_by_email(db, update_data["email"])
        if existing and existing.id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

    safe_update = SubcontractorUpdate(**update_data)
    
    updated_sub = subcontractor_crud.update_subcontractor(
        db,
        current_user.id,
        safe_update
    )
    
    return updated_sub

@router.get("/{subcontractor_id}", response_model=SubcontractorDetailResponse)
def get_subcontractor(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: Union[User, Subcontractor] = Depends(get_current_active_user)
):
    subcontractor = subcontractor_crud.get_subcontractor_with_details(
        db, 
        subcontractor_id
    )
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )

    current_role = getattr(current_user, "role", None)
    current_role_value = current_role.value if hasattr(current_role, "value") else str(current_role or "").lower()
    current_subcontractor_id = getattr(current_user, "subcontractor_id", None)
    is_self = (
        isinstance(current_user, Subcontractor)
        and current_user.id == subcontractor.id
    ) or current_subcontractor_id == subcontractor.id
    is_admin = isinstance(current_user, User) and current_role_value == UserRole.ADMIN.value
    has_manager_access = (
        isinstance(current_user, User)
        and current_role_value == UserRole.MANAGER.value
        and subcontractor_crud.check_manager_can_access_subcontractor(
        db,
        current_user.id,
        subcontractor.id,
        )
    )

    if not (is_self or is_admin or has_manager_access):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    current_assignments = [
        ProjectAssignmentResponse(
            project_id=project.id,
            project_name=project.name,
            project_location=project.location,
            assigned_date=project.created_at.date(),
            hourly_rate=None,
            is_active=project.status == ProjectStatus.ACTIVE.value or project.status is None,
        )
        for project in subcontractor.assigned_projects
        if project.status == ProjectStatus.ACTIVE.value or project.status is None
    ]
    
    return SubcontractorDetailResponse(
        id=subcontractor.id,
        email=subcontractor.email,
        first_name=subcontractor.first_name,
        last_name=subcontractor.last_name,
        company_name=subcontractor.company_name,
        trade_specialty=subcontractor.trade_specialty,
        suggested_trade_specialty=subcontractor.suggested_trade_specialty,
        trade_resolution_status=subcontractor.trade_resolution_status or TradeResolutionStatus.UNKNOWN.value,
        trade_inference_source=subcontractor.trade_inference_source,
        trade_inference_confidence=subcontractor.trade_inference_confidence,
        planning_ready=subcontractor.planning_ready,
        phone=subcontractor.phone,
        is_active=subcontractor.is_active,
        created_at=subcontractor.created_at,
        updated_at=subcontractor.updated_at,
        active_projects_count=len(current_assignments),
        total_bookings=len(subcontractor.bookings),
        current_assignments=current_assignments,
    )

@router.put("/{subcontractor_id}", response_model=SubcontractorResponse)
def update_subcontractor(
    subcontractor_id: UUID,
    subcontractor_update: SubcontractorUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.MANAGER, UserRole.ADMIN]:
        if getattr(current_user, 'id', None) != subcontractor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
    
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
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )

    can_update = False
    if getattr(current_user, 'role', None) in [UserRole.MANAGER, UserRole.ADMIN]:
        can_update = True
    elif getattr(current_user, 'id', None) == subcontractor_id:
        can_update = True
        
    if not can_update:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to update this password"
        )

    if not verify_password(password_data.current_password, subcontractor.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect current password"
        )

    subcontractor_crud.update_password(db, subcontractor_id, password_data.new_password)

    return MessageResponse(message="Password updated successfully", success=True)

@router.delete("/{subcontractor_id}", response_model=MessageResponse)
def delete_subcontractor(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if getattr(current_user, 'role', None) not in [UserRole.MANAGER, UserRole.ADMIN]:
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
    if getattr(current_user, 'role', None) not in [UserRole.MANAGER, UserRole.ADMIN]:
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
    
    reset_token = create_password_reset_token(subcontractor.email)
    
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
    if getattr(current_user, 'role', None) not in [UserRole.MANAGER, UserRole.ADMIN]:
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
    if getattr(current_user, 'role', None) != UserRole.ADMIN:
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
    
    active_bookings = subcontractor_crud.count_subcontractor_bookings_by_status(
        db,
        subcontractor_id,
        BookingStatus.CONFIRMED
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
    limit: int = Query(SUBCONTRACTOR_PAGE_DEFAULT, ge=1, le=SUBCONTRACTOR_PAGE_MAX),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    projects = []
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

@router.get("/{subcontractor_id}/projects/current", response_model=List[SubcontractorProjectSummary])
def get_current_projects(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
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

@router.get("/{subcontractor_id}/bookings", response_model=List[SubcontractorBookingSummary])
def get_subcontractor_bookings(
    subcontractor_id: UUID,
    project_id: Optional[UUID] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(SUBCONTRACTOR_PAGE_DEFAULT, ge=1, le=SUBCONTRACTOR_PAGE_MAX),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # Check if sub exists
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    if not subcontractor:
        raise HTTPException(status_code=404, detail="Subcontractor not found")

    # USE THE CRUD FUNCTION - It filters in SQL
    result = subcontractor_crud.get_subcontractor_bookings(
        db,
        subcontractor_id=subcontractor_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        status=status,
        skip=skip,
        limit=limit
    )

    bookings = result["bookings"]
    
    # Map to response format
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

@router.get("/{subcontractor_id}/bookings/upcoming", response_model=List[SubcontractorBookingSummary])
def get_upcoming_bookings(
    subcontractor_id: UUID,
    days_ahead: int = Query(UPCOMING_BOOKINGS_DEFAULT_DAYS_AHEAD, ge=1, le=UPCOMING_BOOKINGS_MAX_DAYS_AHEAD, description="Number of days to look ahead"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    today = datetime.now(timezone.utc).date()
    end_date = today + timedelta(days=days_ahead)
    
    upcoming_bookings = []
    for booking in subcontractor.bookings:
        if today <= booking.booking_date <= end_date and booking.status != BookingStatus.CANCELLED:
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

@router.get("/{subcontractor_id}/bookings/count-by-status", response_model=BookingCountsByStatusResponse)
def get_booking_counts(
    subcontractor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    statuses = [BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.COMPLETED, BookingStatus.CANCELLED]
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

@router.get("/{subcontractor_id}/availability", response_model=SubcontractorAvailabilityResponse)
def check_subcontractor_availability_detail(
    subcontractor_id: UUID,
    check_date: date = Query(..., description="Date to check availability"),
    start_time: Optional[str] = Query(None, description="Start time (HH:MM format)"),
    end_time: Optional[str] = Query(None, description="End time (HH:MM format)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
    
    if not subcontractor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor not found"
        )
    
    existing_bookings = []
    for booking in subcontractor.bookings:
        if booking.booking_date == check_date and booking.status != BookingStatus.CANCELLED:
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
        req_start = datetime.combine(check_date, datetime.strptime(start_time, "%H:%M").time())
        req_end = datetime.combine(check_date, datetime.strptime(end_time, "%H:%M").time())
        if req_end <= req_start:
            req_end += timedelta(days=1)
        for booking in existing_bookings:
            b_start = datetime.combine(check_date, datetime.strptime(booking["start_time"], "%H:%M:%S").time())
            b_end = datetime.combine(check_date, datetime.strptime(booking["end_time"], "%H:%M:%S").time())
            if b_end <= b_start:
                b_end += timedelta(days=1)

            if b_start < req_end and req_start < b_end:
                is_available = False
                conflicts.append(booking)
    
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
    from ...models.site_project import SiteProject 
    
    if getattr(current_user, 'role', None) not in [UserRole.MANAGER, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers and admins can assign subcontractors"
        )

    if getattr(current_user, 'role', None) == UserRole.MANAGER:
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
    if getattr(current_user, 'role', None) not in [UserRole.MANAGER, UserRole.ADMIN]:
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
