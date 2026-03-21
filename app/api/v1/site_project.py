from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import date
import logging

from ...core.database import get_db
from ...core.security import get_current_user, get_current_active_user, require_manager_or_admin
from ...models.user import User
from ...models.site_project import SiteProject
from ...models.subcontractor import Subcontractor
from ...schemas.site_project import (
    SiteProjectCreate,
    SiteProjectUpdate,
    SiteProjectFilters,
    SiteProjectResponse,
    SiteProjectDetailResponse,
    SiteProjectListResponse,
    ProjectManagerCreate,
    ProjectSubcontractorCreate,
    ProjectSubcontractorUpdate,
    ProjectStatisticsResponse
)
from ...schemas.base import MessageResponse
from ...schemas.enums import UserRole, ProjectStatus
from ...schemas.subcontractor import SubcontractorResponse
from ...crud import site_project as project_crud
from ...crud import subcontractor as subcontractor_crud
from ...crud import user as user_crud

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Projects"])


# ==================== Helper Functions ====================

def validate_managers_exist(db: Session, manager_ids: List[UUID]) -> None:
    """Validate that all manager IDs exist and are active"""
    for manager_id in manager_ids:
        manager = user_crud.get_user(db, user_id=manager_id)
        if not manager:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Manager with ID {manager_id} not found"
            )
        if not manager.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Manager {manager.email} is not active"
            )
        if manager.role not in (UserRole.MANAGER.value, UserRole.ADMIN.value, UserRole.TV.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User {manager.email} is not eligible to be assigned to a project"
            )


def validate_subcontractors_exist(db: Session, subcontractor_ids: List[UUID]) -> None:
    """Validate that all subcontractor IDs exist and are active"""
    for subcontractor_id in subcontractor_ids:
        subcontractor = subcontractor_crud.get_subcontractor(db, subcontractor_id)
        if not subcontractor:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Subcontractor with ID {subcontractor_id} not found"
            )
        if not subcontractor.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Subcontractor {subcontractor.email} is not active"
            )


def check_project_access(
    db: Session,
    project_id: UUID,
    user: User,
    require_manager: bool = False,
    require_lead: bool = False
) -> None:
    """Check if user has required access level to project"""
    
    # Admins always have access
    if user.role == UserRole.ADMIN.value:
        return
    
    # Check if user has project access
    if not project_crud.has_project_access(db, project_id=project_id, user_id=user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this project"
        )
    
    # Check if manager access is required
    if require_manager and not project_crud.is_project_manager(db, project_id=project_id, user_id=user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project managers can perform this action"
        )
    
    # Check if lead manager access is required
    if require_lead and not project_crud.is_lead_project_manager(db, project_id=project_id, user_id=user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only lead project managers can perform this action"
        )


# ==================== Main Endpoints ====================

@router.post("/", response_model=SiteProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    project_data: SiteProjectCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> SiteProjectResponse:
    """Create a new project"""
    
    try:
        require_manager_or_admin(current_user)

        # Validate managers exist if provided
        if project_data.manager_ids:
            validate_managers_exist(db, project_data.manager_ids)
        
        # Validate subcontractors exist if provided
        if project_data.subcontractor_ids:
            validate_subcontractors_exist(db, project_data.subcontractor_ids)
        
        # Create project
        project = project_crud.create_project(
            db,
            project_data=project_data,
            manager_ids=project_data.manager_ids,
            subcontractor_ids=project_data.subcontractor_ids
        )
        
        # Add current user as a lead manager if no managers specified
        # and current user is a manager/admin
        if not project_data.manager_ids and current_user.role in (UserRole.MANAGER.value, UserRole.ADMIN.value):
            project_crud.add_manager_to_project(
                db,
                project_id=project.id,
                manager_id=current_user.id,
                is_lead=True
            )
            db.refresh(project)
        
        return project
        
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to create project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create project"
        ) from exc


@router.get("/", response_model=SiteProjectListResponse)
def list_projects(
    name: Optional[str] = Query(None, description="Filter by project name (partial match)"),
    location: Optional[str] = Query(None, description="Filter by location (partial match)"),
    project_status: Optional[ProjectStatus] = Query(None, description="Filter by status"),
    start_date_from: Optional[date] = Query(None, description="Filter by start date (from)"),
    start_date_to: Optional[date] = Query(None, description="Filter by start date (to)"),
    my_projects: bool = Query(False, description="Show only my projects"),
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Number of items to return"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> SiteProjectListResponse:
    """List projects with optional filters"""
    
    try:
        raw_role = getattr(current_user, "role", None)
        role_norm = raw_role.strip().lower() if isinstance(raw_role, str) else raw_role

        # TV users can only list their assigned projects (used by TV mode on login)
        if role_norm == UserRole.TV.value:
            if not my_projects:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="TV users can only list assigned projects"
                )
        else:
            require_manager_or_admin(current_user)

        filters = SiteProjectFilters(
            name=name,
            location=location,
            status=project_status,
            start_date_from=start_date_from,
            start_date_to=start_date_to,
            user_id=current_user.id if (my_projects or role_norm == UserRole.TV.value) else None,
        )

        # Get paginated projects
        projects = project_crud.get_projects(
            db,
            filters=filters,
            skip=skip,
            limit=limit
        )
        
        total = project_crud.count_projects(db, filters=filters)
        
        return SiteProjectListResponse(
            projects=projects,
            total=total,
            skip=skip,
            limit=limit,
            has_more=(skip + limit) < total
        )
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to retrieve projects")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve projects"
        ) from exc


@router.get("/{project_id}", response_model=SiteProjectDetailResponse)
def get_project(
    project_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> SiteProjectDetailResponse:
    """Get detailed project information"""
    
    try:
        project = project_crud.get_project_with_details(db, project_id=project_id)
        
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        # Check if user has access to this project
        check_project_access(db, project_id, current_user)
        
        return project
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to retrieve project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve project"
        ) from exc


@router.patch("/{project_id}", response_model=SiteProjectResponse)
def update_project(
    project_id: UUID,
    update_data: SiteProjectUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> SiteProjectResponse:
    """Update project details"""
    
    try:
        project = project_crud.get_project(db, project_id=project_id)
        
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        # Check if user has manager access
        check_project_access(db, project_id, current_user, require_manager=True)
        
        # Update project
        project = project_crud.update_project(
            db,
            project=project,
            update_data=update_data
        )
        
        return project
        
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to update project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update project"
        ) from exc


@router.delete("/{project_id}", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def delete_project(
    project_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    """Archive project (admin or lead manager only)."""

    try:
        project = project_crud.get_project(db, project_id=project_id)

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        # Check if user is admin or lead manager
        if current_user.role != UserRole.ADMIN.value:
            check_project_access(db, project_id, current_user, require_lead=True)

        # Archive project (non-destructive)
        project_crud.archive_project(db, project=project)
        return MessageResponse(message="Project archived successfully")

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to archive project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to archive project"
        ) from exc


# ==================== Manager Management Endpoints ====================

@router.post("/{project_id}/managers", response_model=MessageResponse)
def add_manager(
    project_id: UUID,
    manager_data: ProjectManagerCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    """Add manager to project"""
    
    try:
        project = project_crud.get_project(db, project_id=project_id)
        
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        # Check if current user has manager access
        check_project_access(db, project_id, current_user, require_manager=True)
        
        # Validate new manager exists and is eligible
        validate_managers_exist(db, [manager_data.manager_id])
        
        # Check if manager is already assigned
        if project_crud.has_project_access(db, project_id=project_id, user_id=manager_data.manager_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is already assigned to this project"
            )
        
        # Add manager
        added = project_crud.add_manager_to_project(
            db,
            project_id=project_id,
            manager_id=manager_data.manager_id,
            is_lead=manager_data.is_lead_manager
        )
        if not added:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project or manager not found"
            )

        return MessageResponse(message="Manager added successfully")
        
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to add manager to project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add manager"
        ) from exc


@router.delete("/{project_id}/managers/{manager_id}", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def remove_manager(
    project_id: UUID,
    manager_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    """Remove manager from project"""
    
    try:
        project = project_crud.get_project(db, project_id=project_id)
        
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        # Check permissions (need to be lead manager or admin)
        if current_user.role != UserRole.ADMIN.value:
            check_project_access(db, project_id, current_user, require_lead=True)
        
        # Prevent removing last manager
        if project_crud.count_project_managers(db, project_id=project_id) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last manager from project"
            )
        
        # Prevent self-removal if you're the only lead
        if manager_id == current_user.id:
            lead_count = project_crud.count_lead_managers(db, project_id=project_id)
            if lead_count <= 1 and project_crud.is_lead_project_manager(db, project_id, current_user.id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot remove yourself as the only lead manager"
                )
        
        # Remove manager
        project_crud.remove_manager_from_project(db, project_id=project_id, manager_id=manager_id)
        
        return MessageResponse(message="Manager removed successfully")
        
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to remove manager from project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove manager"
        ) from exc


# ==================== Subcontractor Management Endpoints ====================

@router.post("/{project_id}/subcontractors", response_model=MessageResponse)
def add_subcontractor(
    project_id: UUID,
    subcontractor_data: ProjectSubcontractorCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    """Add subcontractor to project"""
    
    try:
        project = project_crud.get_project(db, project_id=project_id)
        
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        # Check if user has manager access
        check_project_access(db, project_id, current_user, require_manager=True)
        
        # Verify subcontractor exists and is active
        subcontractor = subcontractor_crud.get_subcontractor(
            db, subcontractor_data.subcontractor_id
        )
        if not subcontractor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subcontractor not found"
            )
        
        if not subcontractor.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subcontractor is not active"
            )
        
        # Check if subcontractor is already assigned
        if project_crud.is_subcontractor_assigned(db, project_id, subcontractor_data.subcontractor_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subcontractor is already assigned to this project"
            )
        
        # Add subcontractor
        project_crud.add_subcontractor_to_project(
            db,
            project_id=project_id,
            subcontractor_id=subcontractor_data.subcontractor_id,
        )
        
        return MessageResponse(message="Subcontractor added successfully")
        
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to add subcontractor to project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add subcontractor"
        ) from exc


@router.patch("/{project_id}/subcontractors/{subcontractor_id}", response_model=MessageResponse)
def update_subcontractor(
    project_id: UUID,
    subcontractor_id: UUID,
    update_data: ProjectSubcontractorUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    """Update subcontractor details in project"""
    
    try:
        # Check permissions
        check_project_access(db, project_id, current_user, require_manager=True)
        
        # Verify subcontractor is assigned to project
        if not project_crud.is_subcontractor_assigned(db, project_id, subcontractor_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subcontractor is not assigned to this project"
            )
        
        # Update subcontractor
        project_crud.update_project_subcontractor(
            db,
            project_id=project_id,
            subcontractor_id=subcontractor_id,
            update_data=update_data
        )
        
        return MessageResponse(message="Subcontractor updated successfully")
        
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to update subcontractor in project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update subcontractor"
        ) from exc


@router.delete("/{project_id}/subcontractors/{subcontractor_id}", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def remove_subcontractor(
    project_id: UUID,
    subcontractor_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    """Remove subcontractor from project"""
    
    try:
        # Check permissions
        check_project_access(db, project_id, current_user, require_manager=True)
        
        # Verify subcontractor is assigned to project
        if not project_crud.is_subcontractor_assigned(db, project_id, subcontractor_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subcontractor is not assigned to this project"
            )
        
        # Remove subcontractor
        project_crud.remove_subcontractor_from_project(
            db,
            project_id=project_id,
            subcontractor_id=subcontractor_id
        )
        
        return MessageResponse(message="Subcontractor removed successfully")
        
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to remove subcontractor from project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove subcontractor"
        ) from exc


@router.get("/{project_id}/available-subcontractors", response_model=List[SubcontractorResponse])
def get_available_subcontractors(
    project_id: UUID,
    trade_specialty: Optional[str] = Query(None, description="Filter by trade specialty"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> List[SubcontractorResponse]:
    """Get list of subcontractors not yet assigned to this project"""
    
    try:
        # Check if user has access to project
        check_project_access(db, project_id, current_user)
        
        available_subcontractors = project_crud.get_available_subcontractors(
            db,
            project_id=project_id,
            trade_specialty=trade_specialty
        )
        
        return available_subcontractors
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to retrieve available subcontractors")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve available subcontractors"
        ) from exc


@router.get("/{project_id}/statistics", response_model=ProjectStatisticsResponse)
def get_project_statistics(
    project_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> ProjectStatisticsResponse:
    """Get project statistics and summary"""
    
    try:
        # Check if user has access to project
        check_project_access(db, project_id, current_user)
        
        stats = project_crud.get_project_statistics(db, project_id=project_id)
        
        if not stats:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        return stats
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to retrieve project statistics")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve project statistics"
        ) from exc
