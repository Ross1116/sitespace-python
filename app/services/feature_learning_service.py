"""
Stage 11 — Feature Learning.

Phase A: passive data collection (record_feature_observation, record_actuals_shape).
Phase B: computation and application (compute_feature_effects, get_feature_adjustments,
         apply_feature_adjustments_to_hours, evaluate_context_expansion,
         batch_compute_all_feature_effects, nightly_feature_learning_job).
"""
from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.constants import (
    FEATURE_LEARNING_CONFIDENCE_FLOOR,
    FEATURE_LEARNING_EXPANSION_CV_THRESHOLD,
    FEATURE_LEARNING_EXPANSION_MIN_OBS,
    FEATURE_LEARNING_MAX_ADJUSTMENT,
    FEATURE_LEARNING_MIN_OBSERVATIONS,
    WORK_PROFILE_CONTEXT_VERSION,
    WORK_PROFILE_INFERENCE_VERSION,
    WORK_PROFILE_NORM_DIST_SUM_TOLERANCE,
)
from app.models.work_profile import (
    ActivityWorkProfile,
    ContextExpansionSignal,
    ContextFeatureEffect,
    ContextFeatureObservation,
    ItemContextProfile,
)

_FEATURE_NAMES = ("phase", "spatial_type", "area_type", "work_type")
_CTX_COLUMNS = {
    "phase": "ctx_phase",
    "spatial_type": "ctx_spatial_type",
    "area_type": "ctx_area_type",
    "work_type": "ctx_work_type",
}


def record_feature_observation(
    db: Session,
    *,
    context_profile: ItemContextProfile,
    activity_work_profile: ActivityWorkProfile,
    actual_hours: float,
    compressed_context: dict[str, str],
    project_id: uuid.UUID,
) -> ContextFeatureObservation:
    """
    Record one actual-vs-predicted comparison for feature learning.

    Called from ``record_actual_hours()`` and ``apply_mapping_correction()``
    after the posterior has already been updated.  The insertion is
    append-only; it does not modify any existing row.
    """
    predicted = float(activity_work_profile.total_hours or 0)
    residual = actual_hours - predicted
    relative_error: Optional[float] = (residual / predicted) if predicted != 0 else None

    # Defensive: fall back to "unknown" for any missing context field
    ctx_phase = str(compressed_context.get("phase") or "unknown")
    ctx_spatial_type = str(compressed_context.get("spatial_type") or "unknown")
    ctx_area_type = str(compressed_context.get("area_type") or "unknown")
    ctx_work_type = str(compressed_context.get("work_type") or "unknown")

    from app.services.work_profile_service import duration_bucket_for_days  # local import avoids circular

    obs = ContextFeatureObservation(
        item_id=context_profile.item_id,
        asset_type=str(context_profile.asset_type),
        duration_bucket=duration_bucket_for_days(int(context_profile.duration_days or 1)),
        ctx_phase=ctx_phase,
        ctx_spatial_type=ctx_spatial_type,
        ctx_area_type=ctx_area_type,
        ctx_work_type=ctx_work_type,
        predicted_hours=round(predicted, 4),
        actual_hours=round(actual_hours, 4),
        residual=round(residual, 4),
        relative_error=round(relative_error, 6) if relative_error is not None else None,
        context_profile_id=context_profile.id,
        activity_work_profile_id=activity_work_profile.id,
        project_id=project_id,
        context_version=int(context_profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
        inference_version=int(context_profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
    )
    db.add(obs)
    return obs


def record_actuals_shape(
    db: Session,
    *,
    context_profile: ItemContextProfile,
    actual_daily_hours: list[float],
) -> None:
    """
    Update ``context_profile.actuals_shape_json`` with a running weighted
    average of the observed normalized daily distribution.

    ``actual_daily_hours`` must be a per-day breakdown (one entry per
    duration_days).  When only a total is available, do not call this
    function — shape learning requires daily granularity.

    The weight for each new observation is 1 (equal weight), so:
        new_shape = (old_shape * (n - 1) + new_obs) / n
    where n = actuals_count after the update.
    """
    if not actual_daily_hours or all(h == 0 for h in actual_daily_hours):
        return

    total = sum(actual_daily_hours)
    if total <= 0:
        return

    normalized = [h / total for h in actual_daily_hours]

    n = int(context_profile.actuals_count or 0)
    if n <= 0:
        # First observation — store directly
        context_profile.actuals_shape_json = normalized
        return

    existing = context_profile.actuals_shape_json
    if not existing or len(existing) != len(normalized):
        # Length mismatch (duration change) or corrupt — reset
        context_profile.actuals_shape_json = normalized
        return

    blended = [
        (existing[i] * (n - 1) + normalized[i]) / n
        for i in range(len(normalized))
    ]

    # Re-normalise to guard against floating-point drift
    blend_sum = sum(blended)
    if blend_sum > WORK_PROFILE_NORM_DIST_SUM_TOLERANCE:
        blended = [v / blend_sum for v in blended]

    context_profile.actuals_shape_json = blended


# ---------------------------------------------------------------------------
# Phase B — computation functions
# ---------------------------------------------------------------------------

def apply_feature_adjustments_to_hours(
    base_hours: float,
    adjustments: dict[str, float],
) -> float:
    """
    Apply learned feature adjustments to a base hours estimate.

    Each adjustment is a signed fractional shift:
        adjusted = base * product(1 + adj for adj in adjustments.values())

    The total shift is clamped to ±FEATURE_LEARNING_MAX_ADJUSTMENT so that
    learned features cannot dominate over the Bayesian prior.

    Pure function — no database access.
    """
    if not adjustments or base_hours <= 0:
        return base_hours

    multiplier = 1.0
    for adj in adjustments.values():
        multiplier *= 1.0 + float(adj)

    adjusted = base_hours * multiplier
    lower = base_hours * (1.0 - FEATURE_LEARNING_MAX_ADJUSTMENT)
    upper = base_hours * (1.0 + FEATURE_LEARNING_MAX_ADJUSTMENT)
    return max(lower, min(adjusted, upper))


def compute_feature_effects(
    db: Session,
    *,
    item_id: uuid.UUID,
    asset_type: str,
    duration_bucket: int,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
    min_observations: int = FEATURE_LEARNING_MIN_OBSERVATIONS,
) -> int:
    """
    Compute and upsert feature effect rows for one (item, asset_type, duration_bucket).

    For each of the four context fields, groups observations by field value
    and computes mean_residual, variance, learned_weight, confidence, and
    effective_weight.  Rows are upserted into context_feature_effects.

    Returns the number of effect rows upserted.
    """
    rows = (
        db.query(ContextFeatureObservation)
        .filter(
            ContextFeatureObservation.item_id == item_id,
            ContextFeatureObservation.asset_type == asset_type,
            ContextFeatureObservation.duration_bucket == duration_bucket,
            ContextFeatureObservation.context_version == context_version,
            ContextFeatureObservation.inference_version == inference_version,
        )
        .all()
    )
    if not rows:
        return 0

    all_predicted = [float(r.predicted_hours) for r in rows]
    global_mean = statistics.mean(all_predicted) if all_predicted else 0.0
    if global_mean <= 0:
        return 0

    upserted = 0
    for feature_name in _FEATURE_NAMES:
        col_attr = _CTX_COLUMNS[feature_name]
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            groups[str(getattr(row, col_attr))].append(float(row.residual))

        for feature_value, residuals in groups.items():
            n = len(residuals)
            if n < min_observations:
                continue

            mean_residual = statistics.mean(residuals)
            variance = statistics.variance(residuals) if n > 1 else 0.0
            effect_magnitude = abs(mean_residual) / global_mean
            learned_weight = mean_residual / global_mean
            confidence = 1.0 - 1.0 / math.sqrt(n)
            effective_weight = learned_weight * confidence

            # Query-then-upsert (matches existing codebase style)
            existing = (
                db.query(ContextFeatureEffect)
                .filter(
                    ContextFeatureEffect.item_id == item_id,
                    ContextFeatureEffect.asset_type == asset_type,
                    ContextFeatureEffect.duration_bucket == duration_bucket,
                    ContextFeatureEffect.feature_name == feature_name,
                    ContextFeatureEffect.feature_value == feature_value,
                    ContextFeatureEffect.context_version == context_version,
                    ContextFeatureEffect.inference_version == inference_version,
                )
                .one_or_none()
            )
            if existing is None:
                effect = ContextFeatureEffect(
                    item_id=item_id,
                    asset_type=asset_type,
                    duration_bucket=duration_bucket,
                    feature_name=feature_name,
                    feature_value=feature_value,
                    context_version=context_version,
                    inference_version=inference_version,
                    observation_count=n,
                    mean_residual=round(mean_residual, 4),
                    variance_of_residual=round(variance, 6),
                    effect_magnitude=round(effect_magnitude, 6),
                    learned_weight=round(learned_weight, 6),
                    confidence=round(confidence, 4),
                    effective_weight=round(effective_weight, 6),
                )
                db.add(effect)
            else:
                existing.observation_count = n
                existing.mean_residual = round(mean_residual, 4)
                existing.variance_of_residual = round(variance, 6)
                existing.effect_magnitude = round(effect_magnitude, 6)
                existing.learned_weight = round(learned_weight, 6)
                existing.confidence = round(confidence, 4)
                existing.effective_weight = round(effective_weight, 6)
            upserted += 1

    return upserted


def get_feature_adjustments(
    db: Session,
    *,
    item_id: uuid.UUID,
    asset_type: str,
    duration_bucket: int,
    compressed_context: dict[str, str],
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> dict[str, float]:
    """
    Return learned feature adjustments for the given compressed context.

    Only includes effects where confidence >= FEATURE_LEARNING_CONFIDENCE_FLOOR
    and the feature_value matches the current compressed_context.

    Returns an empty dict (no-op) when no effects exist.
    """
    effects = (
        db.query(ContextFeatureEffect)
        .filter(
            ContextFeatureEffect.item_id == item_id,
            ContextFeatureEffect.asset_type == asset_type,
            ContextFeatureEffect.duration_bucket == duration_bucket,
            ContextFeatureEffect.context_version == context_version,
            ContextFeatureEffect.inference_version == inference_version,
            ContextFeatureEffect.confidence >= FEATURE_LEARNING_CONFIDENCE_FLOOR,
        )
        .all()
    )
    if not effects:
        return {}

    adjustments: dict[str, float] = {}
    for effect in effects:
        ctx_value = compressed_context.get(str(effect.feature_name))
        if ctx_value is not None and ctx_value == str(effect.feature_value):
            adjustments[str(effect.feature_name)] = float(effect.effective_weight)

    return adjustments


def evaluate_context_expansion(
    db: Session,
    *,
    item_id: uuid.UUID,
    asset_type: str,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> int:
    """
    Detect base-context signatures that produce high variance and evaluate
    whether work_type explains the divergence.

    Upserts into context_expansion_signals (promoted=False always — human approval required).
    Returns number of signals upserted.
    """
    rows = (
        db.query(ContextFeatureObservation)
        .filter(
            ContextFeatureObservation.item_id == item_id,
            ContextFeatureObservation.asset_type == asset_type,
            ContextFeatureObservation.context_version == context_version,
            ContextFeatureObservation.inference_version == inference_version,
        )
        .all()
    )
    if not rows:
        return 0

    # Group by base signature (excludes work_type)
    base_groups: dict[str, list] = defaultdict(list)
    for row in rows:
        sig = (
            f"phase={row.ctx_phase};"
            f"spatial_type={row.ctx_spatial_type};"
            f"area_type={row.ctx_area_type}"
        )
        base_groups[sig].append(row)

    upserted = 0
    for context_signature, group_rows in base_groups.items():
        n = len(group_rows)
        if n < FEATURE_LEARNING_EXPANSION_MIN_OBS:
            continue

        actual_hours = [float(r.actual_hours) for r in group_rows]
        mean_actual = statistics.mean(actual_hours)
        if mean_actual <= 0:
            continue
        std_actual = statistics.stdev(actual_hours) if n > 1 else 0.0
        parent_cv = std_actual / mean_actual

        if parent_cv <= FEATURE_LEARNING_EXPANSION_CV_THRESHOLD:
            continue

        # Sub-group by work_type
        subgroups: dict[str, list[float]] = defaultdict(list)
        for row in group_rows:
            subgroups[row.ctx_work_type].append(float(row.actual_hours))

        sub_cvs = []
        for sub_vals in subgroups.values():
            if len(sub_vals) < 2:
                continue
            sub_mean = statistics.mean(sub_vals)
            if sub_mean <= 0:
                continue
            sub_cv = statistics.stdev(sub_vals) / sub_mean
            sub_cvs.append(sub_cv)

        if not sub_cvs:
            continue

        avg_subgroup_cv = statistics.mean(sub_cvs)
        if avg_subgroup_cv >= 0.7 * parent_cv:
            continue  # work_type doesn't explain the variance

        expansion_score = 1.0 - (avg_subgroup_cv / parent_cv)
        expansion_score = max(0.0, min(expansion_score, 1.0))

        existing = (
            db.query(ContextExpansionSignal)
            .filter(
                ContextExpansionSignal.item_id == item_id,
                ContextExpansionSignal.asset_type == asset_type,
                ContextExpansionSignal.context_signature == context_signature,
                ContextExpansionSignal.context_version == context_version,
                ContextExpansionSignal.inference_version == inference_version,
            )
            .one_or_none()
        )
        if existing is None:
            signal = ContextExpansionSignal(
                item_id=item_id,
                asset_type=asset_type,
                context_signature=context_signature,
                context_version=context_version,
                inference_version=inference_version,
                observation_count=n,
                mean_cv=round(parent_cv, 6),
                expansion_candidate_field="work_type",
                expansion_score=round(expansion_score, 6),
                promoted=False,
            )
            db.add(signal)
        else:
            existing.observation_count = n
            existing.mean_cv = round(parent_cv, 6)
            existing.expansion_score = round(expansion_score, 6)
        upserted += 1

    return upserted


def batch_compute_all_feature_effects(
    db: Session,
    *,
    min_observations: int = FEATURE_LEARNING_MIN_OBSERVATIONS,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> dict[str, int]:
    """
    Recompute feature effects for all (item_id, asset_type, duration_bucket) tuples
    that have observations.

    Idempotent — safe to run nightly.  Returns a summary dict.
    """
    from sqlalchemy import distinct as sa_distinct

    tuples = (
        db.query(
            ContextFeatureObservation.item_id,
            ContextFeatureObservation.asset_type,
            ContextFeatureObservation.duration_bucket,
        )
        .filter(
            ContextFeatureObservation.context_version == context_version,
            ContextFeatureObservation.inference_version == inference_version,
        )
        .distinct()
        .all()
    )

    computed = 0
    skipped = 0
    effects_upserted = 0
    expansion_signals = 0
    expansion_evaluated: set[tuple] = set()

    for item_id, asset_type, duration_bucket in tuples:
        n = compute_feature_effects(
            db,
            item_id=item_id,
            asset_type=asset_type,
            duration_bucket=duration_bucket,
            context_version=context_version,
            inference_version=inference_version,
            min_observations=min_observations,
        )
        if n > 0:
            computed += 1
            effects_upserted += n
        else:
            skipped += 1

        # evaluate_context_expansion once per (item, asset_type)
        expansion_key = (item_id, asset_type)
        if expansion_key not in expansion_evaluated:
            expansion_evaluated.add(expansion_key)
            expansion_signals += evaluate_context_expansion(
                db,
                item_id=item_id,
                asset_type=asset_type,
                context_version=context_version,
                inference_version=inference_version,
            )

    db.flush()
    return {
        "computed": computed,
        "skipped": skipped,
        "effects_upserted": effects_upserted,
        "expansion_signals": expansion_signals,
    }


def nightly_feature_learning_job() -> dict[str, int]:
    """
    Nightly batch entry point — recomputes all feature effects and expansion signals.

    Called from nightly_tick.py.  Creates its own session.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        result = batch_compute_all_feature_effects(db)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_feature_effects(
    db: Session,
    *,
    item_id: uuid.UUID,
    asset_type: str | None = None,
    duration_bucket: int | None = None,
) -> list[ContextFeatureEffect]:
    query = db.query(ContextFeatureEffect).filter(ContextFeatureEffect.item_id == item_id)
    if asset_type:
        query = query.filter(ContextFeatureEffect.asset_type == asset_type)
    if duration_bucket is not None:
        query = query.filter(ContextFeatureEffect.duration_bucket == duration_bucket)
    return (
        query.order_by(
            ContextFeatureEffect.asset_type.asc(),
            ContextFeatureEffect.duration_bucket.asc(),
            ContextFeatureEffect.feature_name.asc(),
            ContextFeatureEffect.feature_value.asc(),
        )
        .all()
    )


def list_context_expansion_signals(
    db: Session,
    *,
    item_id: uuid.UUID,
    asset_type: str | None = None,
) -> list[ContextExpansionSignal]:
    query = db.query(ContextExpansionSignal).filter(ContextExpansionSignal.item_id == item_id)
    if asset_type:
        query = query.filter(ContextExpansionSignal.asset_type == asset_type)
    return (
        query.order_by(
            ContextExpansionSignal.asset_type.asc(),
            ContextExpansionSignal.expansion_score.desc(),
            ContextExpansionSignal.context_signature.asc(),
        )
        .all()
    )


def set_context_expansion_signal_promoted(
    db: Session,
    *,
    signal_id: uuid.UUID,
    promoted: bool,
) -> ContextExpansionSignal:
    signal = db.query(ContextExpansionSignal).filter(ContextExpansionSignal.id == signal_id).one_or_none()
    if signal is None:
        raise LookupError(f"Context expansion signal {signal_id} not found")
    signal.promoted = promoted
    signal.promoted_at = datetime.now(timezone.utc) if promoted else None
    db.flush()
    return signal
