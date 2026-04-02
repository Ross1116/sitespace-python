"""
Stage 11 — Feature Learning (Phase A: data collection).

This module is intentionally thin.  Phase A records observations of
actual-vs-predicted hours so that Phase B (not yet built) can later compute
which compressed-context fields explain variance.

No behavioural changes are made here: every function is append-only or a
simple in-place column update on an existing row.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.constants import (
    WORK_PROFILE_CONTEXT_VERSION,
    WORK_PROFILE_INFERENCE_VERSION,
    WORK_PROFILE_NORM_DIST_SUM_TOLERANCE,
)
from app.models.work_profile import (
    ActivityWorkProfile,
    ContextFeatureObservation,
    ItemContextProfile,
)


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
