from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field, field_validator

from .base import BaseSchema
from ..core.constants import ALLOWED_ASSET_TYPES
from .slot_booking import BookingDetailResponse


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
    completeness_notes: Optional[dict] = None
    version_number: Optional[int] = None
    file_name: Optional[str] = None
    ai_tokens_used: Optional[int] = None
    ai_cost_usd: Optional[float] = None
    created_at: Optional[str] = None


class ProgrammeVersionSummary(BaseSchema):
    """Summary of a single programme version in a list."""

    upload_id: UUID
    version_number: Optional[int] = None
    file_name: Optional[str] = None
    status: Optional[str] = None
    completeness_score: Optional[float] = None
    ai_tokens_used: Optional[int] = None
    ai_cost_usd: Optional[float] = None
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
    # Stage 1 correctness fields
    pct_complete: int | None = None
    activity_kind: str | None = None   # 'summary' | 'task' | 'milestone'
    row_confidence: str | None = None  # 'high' | 'medium' | 'low'
    # Stage 2 identity fields
    item_id: UUID | None = None


class ActivityMappingResponse(BaseSchema):
    """Response schema for programme activity asset mappings."""

    id: UUID
    programme_activity_id: UUID
    item_id: UUID | None = None
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


class ActivityBookingGroupSummary(BaseSchema):
    """Summary of the primary booking group linked to a programme activity."""

    id: UUID
    programme_activity_id: UUID
    expected_asset_type: str
    selected_week_start: str | None = None
    origin_source: str
    is_modified: bool
    linked_booking_count: int = 0


class ActivityBookingContextAssetCandidate(BaseSchema):
    """Real project asset candidate for activity-linked booking."""

    id: UUID
    asset_code: str
    name: str
    type: str | None = None
    canonical_type: str | None = None
    status: str
    planning_ready: bool
    is_available: bool
    availability_reason: str | None = None


class ProgrammeActivitySuggestedBookingDate(BaseSchema):
    """A booking-ready per-date suggestion for an activity-linked week."""

    date: str
    start_time: str | None = None
    end_time: str | None = None
    hours: float | None = None
    demand_hours: float | None = None
    booked_hours: float | None = None
    gap_hours: float | None = None


class LinkedBookingGroupSummary(BaseSchema):
    """Frontend-friendly summary of the linked booking group for this activity."""

    booking_group_id: UUID
    programme_activity_id: UUID
    expected_asset_type: str
    selected_week_start: str | None = None
    origin_source: str
    is_modified: bool
    booking_count: int = 0
    total_booked_hours: float = 0.0
    last_booking_at: str | None = None


class ProgrammeActivityBookingContextResponse(BaseSchema):
    """Prefill payload for booking from a programme activity."""

    activity_id: UUID
    programme_upload_id: UUID
    project_id: UUID
    activity_name: str
    start_date: str | None = None
    end_date: str | None = None
    level_name: str | None = None
    zone_name: str | None = None
    expected_asset_type: str
    selected_week_start: str | None = None
    default_week_start: str | None = None
    default_date: str | None = None
    default_booking_date: str | None = None
    default_start_time: str
    default_end_time: str
    suggested_bulk_dates: list[ProgrammeActivitySuggestedBookingDate] = Field(default_factory=list)
    booking_group: ActivityBookingGroupSummary | None = None
    linked_booking_group: LinkedBookingGroupSummary | None = None
    linked_bookings: list[BookingDetailResponse] = Field(default_factory=list)
    candidate_assets: list[ActivityBookingContextAssetCandidate] = Field(default_factory=list)
