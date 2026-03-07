from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from .base import BaseSchema

ALLOWED_ASSET_TYPES = {
    "crane",
    "hoist",
    "loading_bay",
    "ewp",
    "concrete_pump",
    "excavator",
    "forklift",
    "telehandler",
    "compactor",
    "other",
}


class MappingCorrectionRequest(BaseSchema):
    """Request body for PM correction of an activity mapping."""

    asset_type: str = Field(..., min_length=1, max_length=50)

    @field_validator("asset_type", mode="before")
    @classmethod
    def normalize_and_validate_asset_type(cls, v: str) -> str:
        normalized = str(v).strip().lower()
        if normalized not in ALLOWED_ASSET_TYPES:
            raise ValueError(
                "Invalid asset_type. Allowed values: " + ", ".join(sorted(ALLOWED_ASSET_TYPES))
            )
        return normalized


class ActivityMappingResponse(BaseSchema):
    """Response schema for programme activity asset mappings."""

    id: UUID
    programme_activity_id: UUID
    activity_name: str | None = None
    asset_type: str | None = None
    confidence: str
    source: str
    auto_committed: bool
    manually_corrected: bool
    corrected_by: UUID | None = None
    corrected_at: datetime | None = None
    subcontractor_id: UUID | None = None
    created_at: datetime | None = None
