from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field, field_validator

from .base import BaseSchema
from ..services.ai_service import ALLOWED_ASSET_TYPES


class ProgrammeUploadAccepted(BaseSchema):
    """Immediate 202 response after a programme file is accepted for processing."""

    upload_id: UUID
    status: str
    message: str


class ProgrammeUploadStatus(BaseSchema):
    """Processing status for a programme upload."""

    upload_id: UUID
    status: str
    completeness_score: Optional[float] = None
    completeness_notes: Optional[str] = None
    version_number: Optional[int] = None
    file_name: Optional[str] = None
    created_at: Optional[str] = None


class ProgrammeVersionSummary(BaseSchema):
    """Summary of a single programme version in a list."""

    upload_id: UUID
    version_number: Optional[int] = None
    file_name: Optional[str] = None
    status: Optional[str] = None
    completeness_score: Optional[float] = None
    created_at: Optional[str] = None


class ProgrammeDiff(BaseSchema):
    """Diff between two programme versions."""

    upload_id: UUID
    version_number: Optional[int] = None
    previous_version: Optional[int] = None
    activity_count: int
    previous_activity_count: Optional[int] = None
    activity_delta: Optional[int] = None
    summary: str


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


class ProgrammeActivityItem(BaseSchema):
    """Single activity row returned by the activities list endpoint."""

    id: str
    parent_id: str | None = None
    name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    duration_days: int | None = None
    level_name: str | None = None
    zone_name: str | None = None
    is_summary: bool | None = None
    wbs_code: str | None = None
    sort_order: int | None = None
    import_flags: list[str] = Field(default_factory=list)


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
