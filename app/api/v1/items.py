"""
Item identity routes.

GET  /api/items                              — search / list items
GET  /api/items/review/other                 — list active items classified as `other`
POST /api/items/merge                        — manually merge two items (admin)
GET  /api/items/{item_id}/statistics         — learning / usage statistics for an item
GET  /api/items/{item_id}/classification     — active classification for an item
POST /api/items/{item_id}/classification     — manually override classification (admin)
GET  /api/items/{item_id}/classification/history — classification audit trail
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
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
    ContextExpansionPromoteRequest,
    ContextExpansionSignalResponse,
    ContextFeatureEffectResponse,
    ItemAliasCreateRequest,
    ItemAliasResponse,
    ItemClassificationEventResponse,
    ItemClassificationOverrideRequest,
    ItemClassificationResponse,
    ItemClassificationSummary,
    ItemKnowledgeEntrySummary,
    ItemMergeRequest,
    ItemMergeResponse,
    ItemOtherReviewResponse,
    ItemOtherReviewSummaryResponse,
    ItemMergeSuggestionResponse,
    ItemRequirementEvaluationRequest,
    ItemRequirementEvaluationResponse,
    ItemRequirementSetResponse,
    ItemRequirementSetUpsertRequest,
    ItemResponse,
    ItemStatisticsResponse,
)
from ...services.classification_service import (
    apply_manual_classification,
    get_active_classification,
    maturity_tier,
)
from ...services.identity_service import AliasConflictError, MergeError, add_manual_alias, merge_items
from ...services.item_learning_service import (
    get_item_statistics,
    list_other_review_items,
    suggest_merge_candidates,
    summarize_other_review_items,
)
from ...services.feature_learning_service import (
    list_context_expansion_signals,
    list_feature_effects,
    set_context_expansion_signal_promoted,
)
from ...services.item_requirements_service import (
    evaluate_assets_against_requirements,
    get_active_item_requirement_set,
    replace_item_requirement_set,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/items", tags=["Items"])


def _get_item_or_404(db: Session, item_id: UUID) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item {item_id} not found")
    return item


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


@router.get("/review/other", response_model=list[ItemOtherReviewResponse])
def review_other_items(
    limit: int = Query(ITEM_PAGE_DEFAULT, ge=1, le=ITEM_PAGE_MAX),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    rows = list_other_review_items(db, limit=limit, offset=offset)
    return [ItemOtherReviewResponse(**row) for row in rows]


@router.get("/review/other/summary", response_model=ItemOtherReviewSummaryResponse)
def review_other_items_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    payload = summarize_other_review_items(db)
    return ItemOtherReviewSummaryResponse(
        total_items=payload["total_items"],
        total_occurrences=payload["total_occurrences"],
        distinct_project_count=payload["distinct_project_count"],
        recent_upload_occurrences=payload["recent_upload_occurrences"],
        top_items=[ItemOtherReviewResponse(**row) for row in payload["top_items"]],
    )


@router.get("/{item_id}/statistics", response_model=ItemStatisticsResponse)
def get_item_statistics_endpoint(
    item_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    try:
        stats = get_item_statistics(db, item_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    classification = stats.active_classification
    return ItemStatisticsResponse(
        item_id=stats.item.id,
        display_name=stats.item.display_name,
        alias_count=stats.alias_count,
        occurrence_count=stats.occurrence_count,
        distinct_project_count=stats.distinct_project_count,
        actuals_count=stats.actuals_count,
        actual_hours_total=stats.actual_hours_total,
        last_seen_at=stats.last_seen_at,
        active_classification=(
            ItemClassificationSummary(
                asset_type=classification.asset_type,
                source=classification.source,
                confidence=classification.confidence,
                maturity_tier=maturity_tier(classification),
            )
            if classification is not None
            else None
        ),
        local_profile_counts_by_source=stats.local_profile_counts_by_source,
        local_profile_counts_by_maturity=stats.local_profile_counts_by_maturity,
        global_knowledge_counts_by_tier=stats.global_knowledge_counts_by_tier,
        global_knowledge_entries=[
            ItemKnowledgeEntrySummary(
                asset_type=row.asset_type,
                duration_bucket=row.duration_bucket,
                confidence_tier=row.confidence_tier,
                source_project_count=row.source_project_count,
                sample_count=row.sample_count,
                correction_count=row.correction_count,
                posterior_mean=float(row.posterior_mean),
                last_updated_at=row.last_updated_at,
            )
            for row in stats.global_knowledge_entries
        ],
    )


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


@router.get("/{item_id}/merge-suggestions", response_model=list[ItemMergeSuggestionResponse])
def get_merge_suggestions(
    item_id: UUID,
    limit: int = Query(10, ge=1, le=25),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    try:
        rows = suggest_merge_candidates(db, item_id, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [ItemMergeSuggestionResponse(**row) for row in rows]


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


def _alias_response(alias) -> ItemAliasResponse:
    return ItemAliasResponse(
        id=alias.id,
        item_id=alias.item_id,
        alias_normalised_name=alias.alias_normalised_name,
        normalizer_version=alias.normalizer_version,
        alias_type=alias.alias_type,
        confidence=alias.confidence,
        source=alias.source,
        created_at=alias.created_at,
        updated_at=alias.updated_at,
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to apply manual classification for item %s", item_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Classification update failed") from exc
    return _classification_response(cls)


@router.post("/{item_id}/aliases", response_model=ItemAliasResponse)
def add_item_alias(
    item_id: UUID,
    body: ItemAliasCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN])),
):
    """Add a manual alias to an active canonical item."""
    try:
        alias = add_manual_alias(
            db=db,
            item_id=item_id,
            raw_alias=body.alias,
            performed_by_user_id=current_user.id,
        )
        db.commit()
        db.refresh(alias)
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AliasConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to add manual alias for item %s", item_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Alias update failed",
        ) from exc

    return _alias_response(alias)


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
        .order_by(ItemClassificationEvent.created_at.desc(), ItemClassificationEvent.id.desc())
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


@router.get("/{item_id}/feature-effects", response_model=list[ContextFeatureEffectResponse])
def get_item_feature_effects(
    item_id: UUID,
    asset_type: str | None = Query(None),
    duration_bucket: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    rows = list_feature_effects(db, item_id=item_id, asset_type=asset_type, duration_bucket=duration_bucket)
    return [
        ContextFeatureEffectResponse(
            id=row.id,
            asset_type=row.asset_type,
            duration_bucket=row.duration_bucket,
            feature_name=row.feature_name,
            feature_value=row.feature_value,
            observation_count=row.observation_count,
            mean_residual=float(row.mean_residual),
            confidence=float(row.confidence),
            effective_weight=float(row.effective_weight),
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get("/{item_id}/expansion-signals", response_model=list[ContextExpansionSignalResponse])
def get_item_expansion_signals(
    item_id: UUID,
    asset_type: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    rows = list_context_expansion_signals(db, item_id=item_id, asset_type=asset_type)
    return [
        ContextExpansionSignalResponse(
            id=row.id,
            asset_type=row.asset_type,
            context_signature=row.context_signature,
            observation_count=row.observation_count,
            mean_cv=float(row.mean_cv),
            expansion_candidate_field=row.expansion_candidate_field,
            expansion_score=float(row.expansion_score),
            promoted=bool(row.promoted),
            promoted_at=row.promoted_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.post("/expansion-signals/{signal_id}/promote", response_model=ContextExpansionSignalResponse)
def promote_expansion_signal(
    signal_id: UUID,
    body: ContextExpansionPromoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN])),
):
    try:
        row = set_context_expansion_signal_promoted(db, signal_id=signal_id, promoted=body.promoted)
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ContextExpansionSignalResponse(
        id=row.id,
        asset_type=row.asset_type,
        context_signature=row.context_signature,
        observation_count=row.observation_count,
        mean_cv=float(row.mean_cv),
        expansion_candidate_field=row.expansion_candidate_field,
        expansion_score=float(row.expansion_score),
        promoted=bool(row.promoted),
        promoted_at=row.promoted_at,
        updated_at=row.updated_at,
    )


@router.get("/{item_id}/requirements", response_model=ItemRequirementSetResponse | None)
def get_item_requirements(
    item_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    _get_item_or_404(db, item_id)
    row = get_active_item_requirement_set(db, item_id)
    if row is None:
        return None
    return ItemRequirementSetResponse(
        id=row.id,
        item_id=row.item_id,
        version=row.version,
        is_active=row.is_active,
        rules_json=row.rules_json,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.put("/{item_id}/requirements", response_model=ItemRequirementSetResponse)
def replace_requirements(
    item_id: UUID,
    body: ItemRequirementSetUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN])),
):
    _get_item_or_404(db, item_id)
    try:
        row = replace_item_requirement_set(
            db,
            item_id=item_id,
            rules=body.rules_json,
            notes=body.notes,
            created_by_user_id=current_user.id,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Requirement version conflict. Please retry.",
        ) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to replace requirement set for item %s", item_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Requirement update failed",
        ) from exc
    return ItemRequirementSetResponse(
        id=row.id,
        item_id=row.item_id,
        version=row.version,
        is_active=row.is_active,
        rules_json=row.rules_json,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/{item_id}/requirements/evaluate", response_model=ItemRequirementEvaluationResponse)
def evaluate_item_requirements(
    item_id: UUID,
    body: ItemRequirementEvaluationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    _get_item_or_404(db, item_id)
    payload = evaluate_assets_against_requirements(
        db,
        item_id=item_id,
        project_id=body.project_id,
        asset_ids=body.asset_ids,
    )
    return ItemRequirementEvaluationResponse(**payload)
