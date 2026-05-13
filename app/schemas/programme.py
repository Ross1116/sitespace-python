from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .base import BaseSchema
from .slot_booking import BookingDetailResponse


class ProgrammeUploadAccepted(BaseSchema):
    """Immediate 202 response after a programme file is accepted for processing."""

    upload_id: UUID
    status: str
    message: str


class UploadLifecycleBase(BaseSchema):
    processing_outcome: Optional[str] = None
    is_active_version: bool = False
    is_terminal_success: bool = False
    has_warnings: bool = False


class ProgrammeUploadStatus(UploadLifecycleBase):
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
    # Queue diagnostics (operational, not business lifecycle)
    queue_state: Optional[str] = None
    processing_attempts: Optional[int] = None
    retry_after: Optional[str] = None
    last_error_code: Optional[str] = None


class ProgrammeVersionSummary(UploadLifecycleBase):
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

    asset_type: str | None = Field(default=None, min_length=1, max_length=50)
    asset_role: str | None = Field(default=None, min_length=1, max_length=20)
    profile_shape: str | None = Field(default=None, min_length=1, max_length=50)
    requirement_source: str | None = Field(default=None, min_length=1, max_length=20)
    manual_total_hours: float | None = Field(default=None, ge=0)
    manual_normalized_distribution: list[float] | None = None

    @field_validator("asset_type", mode="before")
    @classmethod
    def normalize_and_validate_asset_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = str(v).strip().lower()
        return normalized

    @field_validator("asset_role", mode="before")
    @classmethod
    def normalize_asset_role(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = str(v).strip().lower()
        if normalized not in {"lead", "support", "incidental"}:
            raise ValueError("asset_role must be one of: lead, support, incidental")
        return normalized

    @field_validator("profile_shape", mode="before")
    @classmethod
    def normalize_profile_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = str(v).strip().lower()
        allowed = {"single_day", "flat", "front_loaded", "back_loaded", "bell", "inverse_bell", "staged"}
        if normalized not in allowed:
            raise ValueError("Invalid profile_shape")
        return normalized

    @field_validator("requirement_source", mode="before")
    @classmethod
    def normalize_requirement_source(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = str(v).strip().lower()
        if normalized not in {"ai", "keyword", "manual", "imported_gold"}:
            raise ValueError("requirement_source must be one of: ai, keyword, manual, imported_gold")
        return normalized

    @field_validator("manual_normalized_distribution")
    @classmethod
    def validate_manual_distribution(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return None
        if not v:
            raise ValueError("manual_normalized_distribution cannot be empty")
        normalized = [float(value) for value in v]
        if any(value < 0 for value in normalized):
            raise ValueError("manual_normalized_distribution cannot contain negative values")
        total = sum(normalized)
        if total > 0 and abs(total - 1.0) > 1e-6:
            raise ValueError("manual_normalized_distribution must sum to 1.0 when non-zero")
        return normalized

    @model_validator(mode="after")
    def validate_payload(self) -> "MappingCorrectionRequest":
        if (
            self.asset_type is None
            and self.asset_role is None
            and self.profile_shape is None
            and self.requirement_source is None
            and self.manual_total_hours is None
            and self.manual_normalized_distribution is None
        ):
            raise ValueError("At least one correction must be supplied")

        if (self.manual_total_hours is None) != (self.manual_normalized_distribution is None):
            raise ValueError(
                "manual_total_hours and manual_normalized_distribution must be supplied together"
            )

        if self.manual_total_hours is not None and self.manual_normalized_distribution is not None:
            distribution_total = sum(self.manual_normalized_distribution)
            if self.manual_total_hours == 0 and distribution_total > 1e-6:
                raise ValueError(
                    "manual_normalized_distribution must be all zeros when manual_total_hours is zero"
                )
            if self.manual_total_hours > 0 and distribution_total <= 1e-6:
                raise ValueError(
                    "manual_normalized_distribution must contain positive weights when manual_total_hours is positive"
                )

        return self


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
    asset_role: str | None = None
    estimated_total_hours: float | None = None
    profile_shape: str | None = None
    label_confidence: float | None = None
    requirement_source: str | None = None
    is_active: bool = True
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
    activity_asset_mapping_id: UUID | None = None
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
    activity_asset_mapping_id: UUID | None = None
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
    activity_asset_mapping_id: UUID
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
