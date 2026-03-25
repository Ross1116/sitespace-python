"""
Asset taxonomy routes.

GET  /api/asset-types              — list all (or active-only) asset types
GET  /api/asset-types/selectable   — active + user-selectable (for dropdowns)
GET  /api/asset-types/{code}       — single asset type by code
POST /api/asset-types              — create a new asset type (admin)
PATCH /api/asset-types/{code}      — update an existing asset type (admin)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import require_role
from ...models.user import User
from ...schemas.enums import UserRole
from ...schemas.asset_type import (
    AssetTypeBriefResponse,
    AssetTypeCreate,
    AssetTypeListResponse,
    AssetTypeResponse,
    AssetTypeUpdate,
)
from ...crud import asset_type as crud

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/asset-types", tags=["Asset Types"])


@router.get("", response_model=AssetTypeListResponse)
def list_asset_types(
    active_only: bool = Query(False, description="Return only active types"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    """List all asset types in the taxonomy."""
    types = crud.get_all(db, active_only=active_only)
    return AssetTypeListResponse(
        asset_types=[AssetTypeResponse.model_validate(t) for t in types],
        total=len(types),
    )


@router.get("/selectable", response_model=list[AssetTypeBriefResponse])
def list_selectable_types(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    """Return active, user-selectable types for UI dropdowns."""
    types = crud.get_selectable(db)
    return [AssetTypeBriefResponse.model_validate(t) for t in types]


@router.get("/{code}", response_model=AssetTypeResponse)
def get_asset_type(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    """Get a single asset type by code."""
    db_obj = crud.get_by_code(db, code)
    if not db_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset type '{code}' not found",
        )
    return AssetTypeResponse.model_validate(db_obj)


@router.post("", response_model=AssetTypeResponse, status_code=status.HTTP_201_CREATED)
def create_asset_type(
    body: AssetTypeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN])),
):
    """Add a new asset type to the taxonomy (admin only)."""
    if crud.get_by_code(db, body.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Asset type '{body.code}' already exists",
        )
    if body.parent_code and not crud.get_by_code(db, body.parent_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Parent type '{body.parent_code}' does not exist",
        )
    try:
        db_obj = crud.create(db, body)
        return AssetTypeResponse.model_validate(db_obj)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conflict creating asset type '{body.code}'",
        ) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to create asset type '%s'", body.code)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create asset type",
        ) from exc


@router.patch("/{code}", response_model=AssetTypeResponse)
def update_asset_type(
    code: str,
    body: AssetTypeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN])),
):
    """Update an existing asset type (admin only)."""
    db_obj = crud.get_by_code(db, code)
    if not db_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset type '{code}' not found",
        )
    if body.parent_code and body.parent_code == code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset type cannot be its own parent",
        )
    if body.parent_code and not crud.get_by_code(db, body.parent_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Parent type '{body.parent_code}' does not exist",
        )
    try:
        updated = crud.update(db, db_obj, body)
        return AssetTypeResponse.model_validate(updated)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Asset type '{code}' conflicts with an existing type",
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to update asset type '%s'", code)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update asset type",
        ) from exc
