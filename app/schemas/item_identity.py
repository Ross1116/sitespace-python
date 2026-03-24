from __future__ import annotations

from typing import Optional
from uuid import UUID

from .base import BaseSchema


class ItemResponse(BaseSchema):
    id: UUID
    display_name: str
    identity_status: str
    merged_into_item_id: Optional[UUID] = None


class ItemMergeRequest(BaseSchema):
    """Merge source_item_id (loser) into target_item_id (survivor)."""
    source_item_id: UUID
    target_item_id: UUID


class ItemMergeResponse(BaseSchema):
    """Result of a successful merge."""
    survivor_item_id: UUID
    merged_item_id: UUID
    message: str
