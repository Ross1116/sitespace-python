from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from ...core.database import get_db
from ...core.security import get_current_active_user
from ...crud.site_project import (
    create_site_project, get_site_projects_by_contractor_key, get_site_project,
    update_site_project, delete_site_project, get_all_site_projects
)
from ...models.user import User
from ...schemas.site_project import (
    SiteProjectCreate, SiteProjectUpdate, SiteProjectResponse, SiteProjectListResponse
)
from ...schemas.base import MessageResponse
import json

router = APIRouter(prefix="/SiteProject", tags=["Site Project"])

@router.post("/saveSiteProject", response_model=SiteProjectResponse)
def save_site_project(
    site_project: SiteProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a new site project
    """
    try:
        created_project = create_site_project(db, site_project)
        
        # Convert the created project to response format with proper JSON handling
        project_data = {
            'id': created_project.id,
            'contractor_key': created_project.contractor_key,
            'email_id': created_project.email_id,
            'contractor_project_id': created_project.contractor_project_id,
            'contractor_name': created_project.contractor_name,
            'contractor_company': created_project.contractor_company,
            'contractor_trade': created_project.contractor_trade,
            'contractor_email': created_project.contractor_email,
            'contractor_phone': created_project.contractor_phone,
            'created_by': created_project.created_by,
            'created_at': created_project.created_at,
            'updated_at': created_project.updated_at
        }
        
        # Handle contractor_project conversion from JSON string to list
        if created_project.contractor_project:
            try:
                project_data['contractor_project'] = json.loads(created_project.contractor_project)
            except (json.JSONDecodeError, TypeError):
                project_data['contractor_project'] = []
        else:
            project_data['contractor_project'] = []
        
        return SiteProjectResponse(**project_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save site project: {str(e)}"
        )

@router.get("/getSiteProjectList", response_model=SiteProjectListResponse)
def get_site_project_list(
    contractor_key: Optional[str] = Query(None, description="Filter by contractor key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get list of site projects, optionally filtered by contractor key
    """
    try:
        if contractor_key:
            projects = get_site_projects_by_contractor_key(db, contractor_key)
        else:
            projects = get_all_site_projects(db)
        
        # Convert contractor_project from JSON strings to lists
        project_responses = []
        for project in projects:
            project_dict = project.__dict__.copy()
            if project_dict.get('contractor_project') and isinstance(project_dict['contractor_project'], str):
                try:
                    project_dict['contractor_project'] = json.loads(project_dict['contractor_project'])
                except (json.JSONDecodeError, TypeError):
                    project_dict['contractor_project'] = []
            project_responses.append(SiteProjectResponse(**project_dict))
        
        return SiteProjectListResponse(
            success=True,
            message="Site projects retrieved successfully",
            data=project_responses
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve site projects: {str(e)}"
        )

@router.put("/updateSiteProject/{project_id}", response_model=SiteProjectResponse)
def update_site_project_endpoint(
    project_id: int,
    site_project_update: SiteProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update an existing site project
    """
    try:
        updated_project = update_site_project(db, project_id, site_project_update)
        if not updated_project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Site project not found"
            )
        
        # Convert the updated project to response format with proper JSON handling
        project_data = {
            'id': updated_project.id,
            'contractor_key': updated_project.contractor_key,
            'email_id': updated_project.email_id,
            'contractor_project_id': updated_project.contractor_project_id,
            'contractor_name': updated_project.contractor_name,
            'contractor_company': updated_project.contractor_company,
            'contractor_trade': updated_project.contractor_trade,
            'contractor_email': updated_project.contractor_email,
            'contractor_phone': updated_project.contractor_phone,
            'created_by': updated_project.created_by,
            'created_at': updated_project.created_at,
            'updated_at': updated_project.updated_at
        }
        
        # Handle contractor_project conversion from JSON string to list
        if updated_project.contractor_project:
            try:
                project_data['contractor_project'] = json.loads(updated_project.contractor_project)
            except (json.JSONDecodeError, TypeError):
                project_data['contractor_project'] = []
        else:
            project_data['contractor_project'] = []
        
        return SiteProjectResponse(**project_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update site project: {str(e)}"
        )

@router.delete("/deleteSiteProject/{project_id}", response_model=MessageResponse)
def delete_site_project_endpoint(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete a site project
    """
    try:
        success = delete_site_project(db, project_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Site project not found"
            )
        return MessageResponse(message="Site project deleted successfully!")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete site project: {str(e)}"
        )

@router.get("/getSiteProjectDetails/{project_id}", response_model=SiteProjectResponse)
def get_site_project_details(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get details of a specific site project
    """
    try:
        project = get_site_project(db, project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Site project not found"
            )
        return project
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve site project details: {str(e)}"
        )
