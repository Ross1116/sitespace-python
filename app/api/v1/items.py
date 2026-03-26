"""
Item identity routes.

GET  /api/items                              — search / list items
POST /api/items/merge                        — manually merge two items (admin)
GET  /api/items/{item_id}/classification     — active classification for an item
POST /api/items/{item_id}/classification     — manually override classification (admin)
GET  /api/items/{item_id}/classification/history — classification audit trail
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ...core.constants import (
    ITEM_PAGE_DEFAULT,
    ITEM_PAGE_MAX,
    CLASSIFICATION_HISTORY_PAGE_DEFAULT,
    CLASSIFICATION_HISTORY_PAGE_MAX,
)
from ...core.database import get_db
from ...core.security import require_role
from ...models.item_identity import Item, ItemClassificationEvent
from ...models.user import User
from ...schemas.enums import UserRole
from ...schemas.item_identity import (
    ItemClassificationEventResponse,
    ItemClassificationOverrideRequest,
    ItemClassificationResponse,
    ItemMergeRequest,
    ItemMergeResponse,
    ItemResponse,
)
from ...services.classification_service import (
    apply_manual_classification,
    get_active_classification,
    maturity_tier,
)
from ...services.identity_service import MergeError, merge_items

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/items", tags=["Items"])


@router.get("", response_model=list[ItemResponse])
def list_items(
    search: str | None = Query(None, description="Filter by display_name (case-insensitive substring)"),
    identity_status: str | None = Query(None, description="Filter by identity_status ('active' or 'merged')"),
    limit: int = Query(ITEM_PAGE_DEFAULT, ge=1, le=ITEM_PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    q = db.query(Item)
    if search:
        q = q.filter(Item.display_name.ilike(f"%{search}%"))
    if identity_status:
        q = q.filter(Item.identity_status == identity_status)
    items = q.order_by(Item.display_name, Item.id).offset(offset).limit(limit).all()
    return [ItemResponse(
        id=i.id,
        display_name=i.display_name,
        identity_status=i.identity_status,
        merged_into_item_id=i.merged_into_item_id,
    ) for i in items]


@router.post("/merge", response_model=ItemMergeResponse, status_code=status.HTTP_200_OK)
def merge_items_endpoint(
    body: ItemMergeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN])),
):
    """
    Merge source_item_id (loser) into target_item_id (survivor).

    - Source is marked merged; all runtime lookups redirect to target.
    - Historical activity rows are NOT repointed immediately; the runtime
      redirect in the demand engine and classification queries handles this.
    - An audit event is recorded in item_identity_events.
    """
    try:
        survivor = merge_items(
            db=db,
            source_item_id=body.source_item_id,
            target_item_id=body.target_item_id,
            performed_by_user_id=current_user.id,
        )
        db.commit()
    except MergeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("Unexpected error during item merge source=%s target=%s", body.source_item_id, body.target_item_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Merge failed")

    return ItemMergeResponse(
        survivor_item_id=survivor.id,
        merged_item_id=body.source_item_id,
        message=f"Item {body.source_item_id} merged into {survivor.id}",
    )


# ---------------------------------------------------------------------------
# Classification endpoints
# ---------------------------------------------------------------------------

def _classification_response(cls) -> ItemClassificationResponse:
    return ItemClassificationResponse(
        id=cls.id,
        item_id=cls.item_id,
        asset_type=cls.asset_type,
        confidence=cls.confidence,
        source=cls.source,
        is_active=cls.is_active,
        confirmation_count=cls.confirmation_count,
        correction_count=cls.correction_count,
        maturity_tier=maturity_tier(cls),
        created_at=cls.created_at,
        updated_at=cls.updated_at,
    )


@router.get("/{item_id}/classification", response_model=ItemClassificationResponse)
def get_item_classification(
    item_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    """Return the active classification for an item."""
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item {item_id} not found")
    cls = get_active_classification(db, item_id)
    if not cls:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active classification for this item")
    return _classification_response(cls)


@router.post("/{item_id}/classification", response_model=ItemClassificationResponse)
def override_item_classification(
    item_id: UUID,
    body: ItemClassificationOverrideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN])),
):
    """Manually set (or override) the classification for an item. Result is PERMANENT."""
    try:
        cls = apply_manual_classification(
            db=db,
            item_id=item_id,
            asset_type=body.asset_type,
            performed_by_user_id=current_user.id,
        )
        db.commit()
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        db.rollback()
        logger.exception("Failed to apply manual classification for item %s", item_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Classification update failed")
    return _classification_response(cls)


@router.get("/{item_id}/classification/history", response_model=list[ItemClassificationEventResponse])
def get_classification_history(
    item_id: UUID,
    limit: int = Query(CLASSIFICATION_HISTORY_PAGE_DEFAULT, ge=1, le=CLASSIFICATION_HISTORY_PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    """Return the full classification audit trail for an item, newest first."""
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item {item_id} not found")
    events = (
        db.query(ItemClassificationEvent)
        .filter(ItemClassificationEvent.item_id == item_id)
        .order_by(ItemClassificationEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        ItemClassificationEventResponse(
            id=e.id,
            item_id=e.item_id,
            classification_id=e.classification_id,
            event_type=e.event_type,
            old_asset_type=e.old_asset_type,
            new_asset_type=e.new_asset_type,
            triggered_by_upload_id=e.triggered_by_upload_id,
            performed_by_user_id=e.performed_by_user_id,
            details_json=e.details_json,
            created_at=e.created_at,
        )
        for e in events
    ]
