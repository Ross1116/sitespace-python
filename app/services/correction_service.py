from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import uuid
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.constants import get_max_hours_for_type
from ..models.item_identity import Item, ItemClassification
from ..models.programme import (
    AISuggestionLog,
    ActivityAssetMapping,
    ProgrammeActivity,
    ProgrammeUpload,
)
from ..models.work_profile import ActivityWorkProfile, ItemContextProfile
from .classification_service import apply_manual_classification, get_active_classification
from .identity_service import follow_item_redirect
from .work_profile_service import (
    build_compressed_context,
    build_context_key,
    duration_bucket_for_days,
    prepare_manual_work_profile,
    rebuild_global_knowledge_entry,
    upsert_manual_context_profile,
    write_manual_activity_profile,
)


class MappingCorrectionValidationError(ValueError):
    """Raised when a Stage 8 correction payload cannot be applied safely."""


@dataclass
class MappingCorrectionContext:
    mapping: ActivityAssetMapping
    activity: ProgrammeActivity
    upload: ProgrammeUpload
    activity_profile: ActivityWorkProfile | None = None
    context_profile: ItemContextProfile | None = None
    suggestion_log: AISuggestionLog | None = None
    canonical_item: Item | None = None


@dataclass
class MappingCorrectionResult:
    context: MappingCorrectionContext
    classification: ItemClassification | None = None
    context_profile: ItemContextProfile | None = None
    activity_profile: ActivityWorkProfile | None = None
    suggestion_log: AISuggestionLog | None = None


def _resolve_canonical_item(db: Session, item_id: uuid.UUID | None) -> Item | None:
    if item_id is None:
        return None
    item = db.get(Item, item_id)
    if item is None:
        return None
    return follow_item_redirect(db, item)


def _find_relevant_suggestion_log(
    db: Session,
    *,
    activity_id: uuid.UUID,
    upload_id: uuid.UUID,
) -> AISuggestionLog | None:
    return (
        db.query(AISuggestionLog)
        .filter(
            AISuggestionLog.activity_id == activity_id,
            or_(AISuggestionLog.upload_id == upload_id, AISuggestionLog.upload_id.is_(None)),
            AISuggestionLog.accepted.is_(True),
        )
        .order_by(AISuggestionLog.created_at.desc(), AISuggestionLog.id.desc())
        .first()
    )


def load_mapping_correction_context(
    db: Session,
    mapping_id: uuid.UUID,
) -> MappingCorrectionContext | None:
    mapping = db.query(ActivityAssetMapping).filter(ActivityAssetMapping.id == mapping_id).first()
    if mapping is None:
        return None

    activity = db.query(ProgrammeActivity).filter(ProgrammeActivity.id == mapping.programme_activity_id).first()
    if activity is None:
        raise LookupError("Mapped activity not found")

    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == activity.programme_upload_id).first()
    if upload is None:
        raise LookupError("Upload not found")

    activity_profile = (
        db.query(ActivityWorkProfile)
        .filter(ActivityWorkProfile.activity_id == activity.id)
        .one_or_none()
    )
    context_profile = None
    if activity_profile is not None and activity_profile.context_profile_id is not None:
        context_profile = (
            db.query(ItemContextProfile)
            .filter(ItemContextProfile.id == activity_profile.context_profile_id)
            .one_or_none()
        )
    suggestion_log = _find_relevant_suggestion_log(
        db,
        activity_id=activity.id,
        upload_id=upload.id,
    )
    canonical_item = _resolve_canonical_item(db, activity.item_id)

    return MappingCorrectionContext(
        mapping=mapping,
        activity=activity,
        upload=upload,
        activity_profile=activity_profile,
        context_profile=context_profile,
        suggestion_log=suggestion_log,
        canonical_item=canonical_item,
    )


def _apply_suggestion_audit(
    db: Session,
    *,
    context: MappingCorrectionContext,
    corrected_asset_type: str,
    previous_asset_type: str | None,
) -> AISuggestionLog:
    suggestion_log = context.suggestion_log
    if suggestion_log is not None:
        suggestion_log.accepted = False
        suggestion_log.correction = corrected_asset_type
        if suggestion_log.upload_id is None:
            suggestion_log.upload_id = context.upload.id
        if suggestion_log.suggested_asset_type is None:
            suggestion_log.suggested_asset_type = previous_asset_type
        return suggestion_log

    fallback = AISuggestionLog(
        id=uuid.uuid4(),
        activity_id=context.activity.id,
        upload_id=context.upload.id,
        suggested_asset_type=previous_asset_type,
        confidence=context.mapping.confidence,
        accepted=False,
        correction=corrected_asset_type,
        source="manual",
        pipeline_stage="manual_correction",
    )
    db.add(fallback)
    db.flush()
    return fallback


def apply_mapping_correction(
    db: Session,
    *,
    context: MappingCorrectionContext,
    corrected_by_user_id: uuid.UUID,
    asset_type: str | None = None,
    manual_total_hours: float | None = None,
    manual_normalized_distribution: list[float] | None = None,
) -> MappingCorrectionResult:
    corrected_asset_type = asset_type or context.mapping.asset_type
    if not corrected_asset_type:
        raise MappingCorrectionValidationError(
            "asset_type is required when the current mapping has no asset_type"
        )

    duration_days = max(1, int(context.activity.duration_days or 1))
    if manual_normalized_distribution is not None and len(manual_normalized_distribution) != duration_days:
        raise MappingCorrectionValidationError(
            "manual_normalized_distribution must match the activity duration"
        )

    previous_asset_type = context.mapping.asset_type
    context.mapping.asset_type = corrected_asset_type
    context.mapping.source = "manual"
    context.mapping.manually_corrected = True
    context.mapping.corrected_by = corrected_by_user_id
    context.mapping.corrected_at = datetime.now(timezone.utc)
    context.mapping.auto_committed = False

    suggestion_log = _apply_suggestion_audit(
        db,
        context=context,
        corrected_asset_type=corrected_asset_type,
        previous_asset_type=previous_asset_type,
    )

    classification: ItemClassification | None = None
    memory_item = context.canonical_item
    if memory_item is not None:
        active_classification = get_active_classification(db, memory_item.id)
        if (
            active_classification is None
            or active_classification.asset_type != corrected_asset_type
            or active_classification.source != "manual"
        ):
            classification = apply_manual_classification(
                db,
                memory_item.id,
                corrected_asset_type,
                corrected_by_user_id,
            )

    profile_item_id = (
        memory_item.id
        if memory_item is not None
        else getattr(context.activity_profile, "item_id", None)
    )
    should_materialize_profile = (
        (manual_total_hours is not None and manual_normalized_distribution is not None)
        or context.activity_profile is not None
    )
    if should_materialize_profile and profile_item_id is None:
        raise MappingCorrectionValidationError(
            "Manual work-profile correction requires an item identity"
        )

    context_profile: ItemContextProfile | None = None
    activity_profile: ActivityWorkProfile | None = None
    old_global_key: tuple[uuid.UUID, str, int, int, int] | None = None
    if should_materialize_profile and profile_item_id is not None:
        prepared = prepare_manual_work_profile(
            asset_type=corrected_asset_type,
            duration_days=duration_days,
            max_hours_per_day=get_max_hours_for_type(db, corrected_asset_type),
            manual_total_hours=manual_total_hours,
            manual_normalized_distribution=manual_normalized_distribution,
            existing_total_hours=(
                float(context.activity_profile.total_hours)
                if context.activity_profile is not None and context.activity_profile.total_hours is not None
                else None
            ),
            existing_distribution=(
                list(context.activity_profile.distribution_json or [])
                if context.activity_profile is not None
                else None
            ),
            existing_normalized_distribution=(
                list(context.activity_profile.normalized_distribution_json or [])
                if context.activity_profile is not None
                else None
            ),
        )
        compressed_context = build_compressed_context(
            context.activity.name or "",
            level_name=context.activity.level_name,
            zone_name=context.activity.zone_name,
        )
        context_hash = build_context_key(
            profile_item_id,
            corrected_asset_type,
            duration_days,
            compressed_context,
        )

        previous_context_profile = context.context_profile
        if previous_context_profile is not None and previous_context_profile.source != "manual":
            previous_distribution = (
                list(context.activity_profile.normalized_distribution_json or [])
                if context.activity_profile is not None
                else []
            )
            existing_hours = (
                float(context.activity_profile.total_hours)
                if context.activity_profile is not None and context.activity_profile.total_hours is not None
                else None
            )
            profile_changed = (
                corrected_asset_type != str(previous_context_profile.asset_type)
                or (
                    existing_hours is None
                    or not math.isclose(existing_hours, prepared.total_hours, rel_tol=1e-9, abs_tol=1e-6)
                )
                or previous_distribution != prepared.normalized_distribution
            )
            if profile_changed:
                previous_context_profile.correction_count = int(previous_context_profile.correction_count or 0) + 1
                old_global_key = (
                    previous_context_profile.item_id,
                    str(previous_context_profile.asset_type),
                    duration_bucket_for_days(int(previous_context_profile.duration_days or 0)),
                    int(previous_context_profile.context_version or 0),
                    int(previous_context_profile.inference_version or 0),
                )

        if memory_item is not None:
            context_profile = upsert_manual_context_profile(
                db,
                project_id=context.upload.project_id,
                item_id=memory_item.id,
                asset_type=corrected_asset_type,
                duration_days=duration_days,
                compressed_context=compressed_context,
                context_hash=context_hash,
                total_hours=prepared.total_hours,
                distribution=prepared.distribution,
                normalized_distribution=prepared.normalized_distribution,
            )

        activity_profile = write_manual_activity_profile(
            db,
            activity_id=context.activity.id,
            item_id=profile_item_id,
            asset_type=corrected_asset_type,
            duration_days=duration_days,
            total_hours=prepared.total_hours,
            distribution=prepared.distribution,
            normalized_distribution=prepared.normalized_distribution,
            context_hash=context_hash,
            context_profile_id=context_profile.id if context_profile is not None else None,
        )

    db.flush()
    if old_global_key is not None:
        rebuild_global_knowledge_entry(
            db,
            item_id=old_global_key[0],
            asset_type=old_global_key[1],
            duration_bucket=old_global_key[2],
            context_version=old_global_key[3],
            inference_version=old_global_key[4],
        )
    return MappingCorrectionResult(
        context=context,
        classification=classification,
        context_profile=context_profile,
        activity_profile=activity_profile,
        suggestion_log=suggestion_log,
    )
