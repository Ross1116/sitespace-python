from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from ...core.database import get_db
from ...core.security import get_current_active_user
from ...crud.subcontractor import (
    create_subcontractor, get_subcontractor_by_email, get_subcontractor,
    update_subcontractor, delete_subcontractor, get_all_subcontractors
)
from ...models.user import User
from ...schemas.subcontractor import (
    SubcontractorCreate, SubcontractorUpdate, SubcontractorResponse, SubcontractorListResponse
)
from ...schemas.base import MessageResponse

router = APIRouter(prefix="/Subcontractor", tags=["Subcontractor"])

@router.post("/saveSubcontractor", response_model=MessageResponse)
def save_subcontractor(
    subcontractor: SubcontractorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a new subcontractor
    """
    try:
        # Check if email already exists
        if subcontractor.email_id and get_subcontractor_by_email(db, subcontractor.email_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        created_subcontractor = create_subcontractor(db, subcontractor)
        return MessageResponse(message="Subcontractor saved successfully!")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save subcontractor: {str(e)}"
        )

@router.get("/getSubcontractorList", response_model=SubcontractorListResponse)
def get_subcontractor_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get list of all subcontractors
    """
    try:
        subcontractors = get_all_subcontractors(db)
        
        return SubcontractorListResponse(
            success=True,
            message="Subcontractors retrieved successfully",
            data=subcontractors
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve subcontractors: {str(e)}"
        )

@router.put("/updateSubcontractor/{subcontractor_id}", response_model=MessageResponse)
def update_subcontractor_endpoint(
    subcontractor_id: int,
    subcontractor_update: SubcontractorUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update an existing subcontractor
    """
    try:
        updated_subcontractor = update_subcontractor(db, subcontractor_id, subcontractor_update)
        if not updated_subcontractor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subcontractor not found"
            )
        return MessageResponse(message="Subcontractor updated successfully!")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update subcontractor: {str(e)}"
        )

@router.delete("/deleteSubcontractor/{subcontractor_id}", response_model=MessageResponse)
def delete_subcontractor_endpoint(
    subcontractor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete a subcontractor
    """
    try:
        success = delete_subcontractor(db, subcontractor_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subcontractor not found"
            )
        return MessageResponse(message="Subcontractor deleted successfully!")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete subcontractor: {str(e)}"
        )

@router.get("/getSubcontractorDetails/{subcontractor_id}", response_model=SubcontractorResponse)
def get_subcontractor_details(
    subcontractor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get details of a specific subcontractor
    """
    try:
        subcontractor = get_subcontractor(db, subcontractor_id)
        if not subcontractor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subcontractor not found"
            )
        return subcontractor
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve subcontractor details: {str(e)}"
        )
