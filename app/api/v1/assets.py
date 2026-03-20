from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Union
from uuid import UUID
from datetime import date
import logging

from app.models.subcontractor import Subcontractor
from ...core.database import get_db
from ...core.security import get_current_active_user, get_user_role, get_entity_id
from ...crud import asset as asset_crud
from ...crud import site_project as project_crud 
from ...models.user import User
from ...schemas.asset import (
    AssetCreate, AssetUpdate, AssetTransfer,
    AssetResponse, AssetDetailResponse, AssetBriefResponse, AssetListResponse,
    AssetAvailabilityCheck, AssetAvailabilityResponse,
    AssetStatusChangeImpactResponse
)
from ...schemas.base import MessageResponse
from ...schemas.enums import AssetStatus, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["Asset Management"])

def check_asset_view_access(db: Session, project_id: UUID, entity: Union[User, Subcontractor]):
    """Helper to check if entity can view assets of a project"""
    user_role = get_user_role(entity)
    user_id = get_entity_id(entity)

    if user_role == UserRole.ADMIN:
        return True
    
    if user_role == UserRole.SUBCONTRACTOR:
        if not project_crud.is_subcontractor_assigned(db, project_id, user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not assigned to this project"
            )
        return True
        
    # Managers
    if not project_crud.has_project_access(db, project_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this project"
        )
    return True


@router.post("/", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
def create_asset(
    asset: AssetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a new asset"""
    try:
        # Check if asset_code already exists
        if asset_crud.get_asset_by_code(db, asset.asset_code):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Asset with code {asset.asset_code} already exists"
            )
        
        db_asset = asset_crud.create_asset(db, asset, current_user.id)
        return AssetResponse.model_validate(db_asset)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to create asset")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create asset"
        ) from exc

@router.get("/", response_model=AssetListResponse)
def list_assets(
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    asset_status: Optional[AssetStatus] = Query(None, description="Filter by status"),
    asset_type: Optional[str] = Query(None, description="Filter by asset type"),
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=100, description="Number of items to return"),
    db: Session = Depends(get_db),
    # Change Dependency to accept Subcontractors
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
):
    """Get paginated list of assets with optional filters"""
    try:
        # 1. Security Check
        if project_id:
            check_asset_view_access(db, project_id, current_entity)
        else:
            # If no project_id provided, restrict non-admins
            role = get_user_role(current_entity)
            if role != UserRole.ADMIN:
                return AssetListResponse(assets=[], total=0, skip=skip, limit=limit, has_more=False) 

        assets, total = asset_crud.get_assets_paginated(
            db=db,
            project_id=project_id,
            status=asset_status,
            asset_type=asset_type,
            skip=skip,
            limit=limit
        )
        
        return AssetListResponse(
            assets=[AssetResponse.model_validate(asset) for asset in assets],
            total=total,
            skip=skip,
            limit=limit,
            has_more=(skip + limit) < total
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list assets")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving assets"
        ) from exc

@router.get("/brief", response_model=List[AssetBriefResponse])
def list_assets_brief(
    project_id: UUID = Query(..., description="Project ID"),
    asset_status: Optional[AssetStatus] = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
):
    """Get brief list of assets for dropdowns/selectors"""
    try:
        # Security Check
        check_asset_view_access(db, project_id, current_entity)

        assets = asset_crud.get_assets_brief(db, project_id, asset_status)
        return [AssetBriefResponse.model_validate(asset) for asset in assets]
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list assets brief")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving assets"
        ) from exc

@router.get("/{asset_id}", response_model=AssetDetailResponse)
def get_asset_detail(
    asset_id: UUID,
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
):
    """Get detailed asset information"""
    try:
        # 1. Fetch Asset to find its Project ID
        asset = asset_crud.get_asset(db, asset_id)
        if not asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found"
            )
            
        # 2. Security Check using the asset's project_id
        check_asset_view_access(db, asset.project_id, current_entity)

        # 3. Get Full Details
        asset_detail = asset_crud.get_asset_detail(db, asset_id)
        return asset_detail
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to retrieve asset details")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving asset details"
        ) from exc

@router.get("/code/{asset_code}", response_model=AssetResponse)
def get_asset_by_code(
    asset_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get asset by asset code"""
    try:
        # Not project-scoped; TV users must use project-scoped endpoints.
        if get_user_role(current_user) == UserRole.TV:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="TV users cannot access this endpoint"
            )
        db_asset = asset_crud.get_asset_by_code(db, asset_code)
        if not db_asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Asset with code {asset_code} not found"
            )
        return AssetResponse.model_validate(db_asset)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to retrieve asset by code")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving asset"
        ) from exc

@router.put("/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: UUID,
    asset_update: AssetUpdate,
    confirm_booking_denials: bool = Query(
        False,
        description="Set true to confirm and apply auto-denial of impacted bookings",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update an existing asset"""
    try:
        asset = asset_crud.get_asset(db, asset_id)
        if not asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found"
            )

        check_asset_view_access(db, asset.project_id, current_user)

        try:
            actor_role = (
                current_user.role
                if isinstance(current_user.role, UserRole)
                else UserRole(current_user.role)
            )
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid user role",
            ) from err

        updated_asset = asset_crud.update_asset(
            db,
            asset_id,
            asset_update,
            current_user.id,
            actor_role=actor_role,
            confirm_booking_denials=confirm_booking_denials,
        )
        return AssetResponse.model_validate(updated_asset)
    except asset_crud.AssetStatusChangeConfirmationRequired as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=e.payload.model_dump(mode="json")
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to update asset")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating asset"
        ) from exc


@router.post("/{asset_id}/status-impact", response_model=AssetStatusChangeImpactResponse)
def preview_asset_status_change_impact(
    asset_id: UUID,
    asset_update: AssetUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Preview bookings that would be auto-denied by this asset status update."""
    try:
        asset = asset_crud.get_asset(db, asset_id)
        if not asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found"
            )

        check_asset_view_access(db, asset.project_id, current_user)

        impact = asset_crud.preview_asset_status_change_impact(db, asset_id, asset_update)
        if not impact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found"
            )
        return impact
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to preview asset status change impact")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error previewing status impact"
        ) from exc

@router.post("/{asset_id}/transfer", response_model=AssetResponse)
def transfer_asset(
    asset_id: UUID,
    transfer: AssetTransfer,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Transfer asset to another project"""
    try:
        transferred_asset = asset_crud.transfer_asset(
            db, asset_id, transfer, current_user.id
        )
        if not transferred_asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found"
            )
        return AssetResponse.model_validate(transferred_asset)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to transfer asset")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error transferring asset"
        ) from exc

@router.post("/check-availability", response_model=AssetAvailabilityResponse)
def check_asset_availability(
    availability_check: AssetAvailabilityCheck,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Check if an asset is available for a specific time slot"""
    try:
        availability = asset_crud.check_asset_availability(db, availability_check)
        return availability
    except Exception as exc:
        logger.exception("Failed to check asset availability")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error checking availability"
        ) from exc

@router.delete("/{asset_id}", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def delete_asset(
    asset_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> MessageResponse:
    """Delete an asset (soft delete if asset has bookings)"""
    try:
        success = asset_crud.delete_asset(db, asset_id, current_user.id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found"
            )
        return MessageResponse(message="Asset deleted successfully")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete asset")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting asset"
        ) from exc