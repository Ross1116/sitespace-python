from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
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


class ItemAliasCreateRequest(BaseSchema):
    """Body for adding a manual alias to an item (ADMIN only)."""
    alias: str


class ItemAliasResponse(BaseSchema):
    id: UUID
    item_id: UUID
    alias_normalised_name: str
    normalizer_version: int
    alias_type: str
    confidence: str
    source: str
    created_at: datetime
    updated_at: datetime


class ItemClassificationResponse(BaseSchema):
    """Active classification for an item, including computed maturity tier."""
    id: UUID
    item_id: UUID
    asset_type: str
    confidence: str
    source: str
    is_active: bool
    confirmation_count: int
    correction_count: int
    maturity_tier: str
    created_at: datetime
    updated_at: datetime


class ItemClassificationOverrideRequest(BaseSchema):
    """Body for manually setting the classification of an item (ADMIN only)."""
    asset_type: str


class ItemClassificationEventResponse(BaseSchema):
    """One entry from the classification audit trail."""
    id: UUID
    item_id: Optional[UUID] = None
    classification_id: Optional[UUID] = None
    event_type: str
    old_asset_type: Optional[str] = None
    new_asset_type: Optional[str] = None
    triggered_by_upload_id: Optional[UUID] = None
    performed_by_user_id: Optional[UUID] = None
    details_json: Optional[Dict[str, Any]] = None
    created_at: datetime
