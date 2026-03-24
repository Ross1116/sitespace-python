"""
Item identity routes.

GET  /api/items              — search / list items
POST /api/items/merge        — manually merge two items (admin)
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import require_role
from ...models.item_identity import Item
from ...models.user import User
from ...schemas.enums import UserRole
from ...schemas.item_identity import ItemMergeRequest, ItemMergeResponse, ItemResponse
from ...services.identity_service import MergeError, merge_items

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/items", tags=["Items"])


@router.get("", response_model=list[ItemResponse])
def list_items(
    search: str | None = Query(None, description="Filter by display_name (case-insensitive substring)"),
    identity_status: str | None = Query(None, description="Filter by identity_status ('active' or 'merged')"),
    limit: int = Query(50, ge=1, le=200),
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
