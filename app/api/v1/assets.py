from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List
from ...core.database import get_db
from ...core.security import get_current_active_user
from ...crud.asset import (
    get_asset, get_assets_by_project, create_asset, update_asset, delete_asset
)
from ...models.user import User
from ...schemas.asset import AssetCreate, AssetUpdate, AssetResponse, AssetListResponse
from ...schemas.base import BaseResponse

router = APIRouter(prefix="/Asset", tags=["Asset Management"])

@router.post("/saveAsset", response_model=AssetResponse)
def save_asset(
    asset: AssetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Save a new asset
    """
    try:
        db_asset = create_asset(db, asset)
        return AssetResponse.model_validate(db_asset)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save asset: {str(e)}"
        )

@router.get("/getAssetList", response_model=AssetListResponse)
def get_asset_list(
    project_id: int = Query(..., description="Project name to filter assets"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get list of assets by project
    """
    try:
        assets = get_assets_by_project(db, project_id)
        asset_responses = [AssetResponse.from_orm(asset) for asset in assets]
        return AssetListResponse(
            success=True,
            data=asset_responses,
            message="Assets retrieved successfully"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving assets: {str(e)}"
        )

@router.post("/updateAsset", response_model=BaseResponse)
def update_asset_endpoint(
    asset_update: AssetUpdate,
    asset_id: int = Query(..., description="Asset ID to update"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update an existing asset
    """
    try:
        updated_asset = update_asset(db, asset_id, asset_update)
        if not updated_asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found"
            )
        
        return BaseResponse(
            success=True,
            message="Asset updated successfully",
            data=AssetResponse.from_orm(updated_asset)
        )
    except HTTPException:
        raise
    except Exception as e:
        return BaseResponse(
            success=False,
            message=f"Error updating asset: {str(e)}"
        )

@router.get("/editAssetdetails", response_model=BaseResponse)
def get_asset_details(
    current_user_id: str = Query(..., description="Current user ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get asset details for editing
    """
    try:
        # This would need to be implemented based on your business logic
        # For now, returning a placeholder response
        return BaseResponse(
            success=True,
            message="Asset details retrieved successfully",
            data={"user_id": current_user_id}
        )
    except Exception as e:
        return BaseResponse(
            success=False,
            message=f"Error retrieving asset details: {str(e)}"
        )

@router.delete("/deleteAsset/{asset_id}", response_model=BaseResponse)
def delete_asset_endpoint(
    asset_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete an asset
    """
    try:
        success = delete_asset(db, asset_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found"
            )
        
        return BaseResponse(
            success=True,
            message="Asset deleted successfully"
        )
    except HTTPException:
        raise
    except Exception as e:
        return BaseResponse(
            success=False,
            message=f"Error deleting asset: {str(e)}"
        )
