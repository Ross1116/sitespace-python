from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import Field, model_validator

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
    project_id: Optional[UUID] = None
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
    project_id: Optional[UUID] = None


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


class ItemClassificationSummary(BaseSchema):
    asset_type: str
    source: str
    confidence: str
    maturity_tier: str


class ItemKnowledgeEntrySummary(BaseSchema):
    asset_type: str
    duration_bucket: int
    confidence_tier: str
    source_project_count: int
    sample_count: int
    correction_count: int
    posterior_mean: float
    last_updated_at: datetime


class ItemStatisticsResponse(BaseSchema):
    item_id: UUID
    display_name: str
    alias_count: int
    occurrence_count: int
    distinct_project_count: int
    actuals_count: int
    actual_hours_total: float
    last_seen_at: Optional[datetime] = None
    active_classification: Optional[ItemClassificationSummary] = None
    local_profile_counts_by_source: Dict[str, int]
    local_profile_counts_by_maturity: Dict[str, int]
    global_knowledge_counts_by_tier: Dict[str, int]
    global_knowledge_entries: list[ItemKnowledgeEntrySummary]


class ItemOtherReviewResponse(BaseSchema):
    item_id: UUID
    display_name: str
    occurrence_count: int
    distinct_project_count: int
    last_seen_at: Optional[datetime] = None
    classification_source: str
    classification_confidence: str
    classification_maturity_tier: str


class ItemOtherReviewSummaryResponse(BaseSchema):
    total_items: int
    total_occurrences: int
    distinct_project_count: int
    recent_upload_occurrences: int
    top_items: list[ItemOtherReviewResponse]


class ItemMergeSuggestionResponse(BaseSchema):
    item_id: UUID
    display_name: str
    score: float
    overlapping_tokens: list[str]
    candidate_asset_type: Optional[str] = None
    occurrence_count: int
    distinct_project_count: int
    last_seen_at: Optional[datetime] = None


class ContextFeatureEffectResponse(BaseSchema):
    id: UUID
    asset_type: str
    duration_bucket: int
    feature_name: str
    feature_value: str
    observation_count: int
    mean_residual: float
    confidence: float
    effective_weight: float
    updated_at: datetime


class ContextExpansionSignalResponse(BaseSchema):
    id: UUID
    asset_type: str
    context_signature: str
    observation_count: int
    mean_cv: float
    expansion_candidate_field: str
    expansion_score: float
    promoted: bool
    promoted_at: Optional[datetime] = None
    updated_at: datetime


class ContextExpansionPromoteRequest(BaseSchema):
    promoted: bool = True


class ItemRequirementSetResponse(BaseSchema):
    id: UUID
    item_id: UUID
    version: int
    is_active: bool
    rules_json: Dict[str, Any]
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ItemRequirementSetUpsertRequest(BaseSchema):
    rules_json: Dict[str, Any]
    notes: Optional[str] = None


class ItemRequirementEvaluationRequest(BaseSchema):
    project_id: Optional[UUID] = None
    asset_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.project_id is None and not self.asset_ids:
            raise ValueError("project_id or asset_ids must be provided")
        return self


class ItemRequirementAssetEvaluation(BaseSchema):
    asset_id: UUID
    asset_code: str
    asset_name: str
    asset_type: str
    matches: bool
    failures: list[str]
    preferences: list[str]
    planning_attributes: Dict[str, Any]


class ItemRequirementEvaluationResponse(BaseSchema):
    item_id: UUID
    requirements: Dict[str, Any]
    evaluations: list[ItemRequirementAssetEvaluation]
