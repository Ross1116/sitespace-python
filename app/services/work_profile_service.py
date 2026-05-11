"""
Work profile service — Stage 5.

Manages deterministic work-profile context extraction, cache maturity,
fallback/default estimation, AI proposal validation, and activity-level
materialization for construction programme activities.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.constants import (
    AI_WORK_PROFILE_MAX_CONCURRENT,
    AI_WORK_PROFILE_MAX_TOKENS,
    WORK_PROFILE_ACTUAL_ERROR_FRACTION,
    WORK_PROFILE_AI_ERROR_FRACTION,
    WORK_PROFILE_BASE_TOKENS,
    WORK_PROFILE_CONTEXT_VERSION,
    WORK_PROFILE_CORRECTION_MIN_SAMPLES,
    WORK_PROFILE_CORRECTION_RATE_THRESHOLD,
    WORK_PROFILE_CV_CONFIRMED,
    WORK_PROFILE_CV_TRUSTED,
    WORK_PROFILE_INFERENCE_VERSION,
    WORK_PROFILE_MAX_TOKENS_CAP,
    WORK_PROFILE_MAX_NEW_CONTEXTS_PER_UPLOAD,
    WORK_PROFILE_MIN_HOURS,
    WORK_PROFILE_NORM_DIST_SUM_TOLERANCE,
    WORK_PROFILE_OPERATIONAL_UNIT,
    WORK_PROFILE_TOKENS_PER_DAY,
)
from ..models.asset import Asset
from ..models.programme import ActivityAssetMapping, ProgrammeActivity, ProgrammeUpload
from ..models.work_profile import (
    ActivityWorkProfile,
    AssetUsageActual,
    ContextExpansionSignal,
    ItemContextProfile,
    ItemKnowledgeBase,
    WorkProfileAILog,
)
from .ai_service import (
    AIExecutionContext,
    _call_api,
    _get_async_client,
    _parse_json_response,
    _resolve_ai_execution_context,
    build_ai_usage,
    coerce_ai_usage,
    sum_ai_costs,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Maturity tier constants
# ---------------------------------------------------------------------------

MATURITY_TENTATIVE = "tentative"
MATURITY_CONFIRMED = "confirmed"
MATURITY_TRUSTED_BASELINE = "trusted_baseline"
MATURITY_MANUAL = "manual"


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class WorkProfileRuntimeState:
    allow_ai: bool = True
    operator_override: bool = False
    ai_tokens_used: int = 0
    ai_cost_usd: Decimal | None = None
    ai_attempts: int = 0
    validation_failures: int = 0
    degraded_reasons: list[str] = field(default_factory=list)


@dataclass
class WorkProfilePreflight:
    compressed_context: dict
    context_hash: str
    max_hours_per_day: float
    cached: Optional[ItemContextProfile] = None
    tier: Optional[str] = None
    trusted_baseline: Optional[float] = None
    global_knowledge: Optional[ItemKnowledgeBase] = None


@dataclass
class WorkProfileAIOutcome:
    proposal: Optional[dict[str, object]] = None
    attempted_ai: bool = False
    ai_attempts: int = 0
    ai_tokens_used: int = 0
    ai_cost_usd: Decimal | None = None
    validation_failures: int = 0
    request_json: dict[str, object] = field(default_factory=dict)
    response_json: Optional[dict[str, object]] = None
    validation_errors: Optional[list[str]] = None
    fallback_used: bool = False
    retry_count: int = 0
    log_input_tokens_used: Optional[int] = None
    log_output_tokens_used: Optional[int] = None
    log_tokens_used: Optional[int] = None
    log_cost_usd: Decimal | None = None
    latency_ms: Optional[int] = None


@dataclass
class PreparedManualWorkProfile:
    total_hours: float
    distribution: list[float]
    normalized_distribution: list[float]
    max_hours_per_day: float


# ---------------------------------------------------------------------------
# 1. Context builder
# ---------------------------------------------------------------------------

# Phase detection — keywords mapped to canonical phase labels
_PHASE_KEYWORDS: dict[str, list[str]] = {
    "structure":  ["reo", "rebar", "concrete", "pour", "formwork", "slab", "column",
                   "core", "wall", "structural", "structure", "superstructure"],
    "facade":     ["facade", "curtain wall", "cladding", "glazing", "window",
                   "external wall", "envelope"],
    "services":   ["mep", "hvac", "mechanical", "electrical", "plumbing", "fire",
                   "services", "lift", "escalator"],
    "fitout":     ["fitout", "fit-out", "fit out", "joinery", "ceiling", "partition",
                   "floor finish", "tiling", "painting", "FF&E"],
    "external":   ["external", "landscaping", "civil", "pavement", "car park",
                   "road", "drainage", "retaining"],
    "prelims":    ["mobilisation", "mobilization", "demobilisation", "demolition",
                   "hoarding", "scaffold", "crane erect", "site establishment",
                   "preliminar"],
}

# Spatial type detection
_SPATIAL_KEYWORDS: dict[str, list[str]] = {
    "level":    ["level", "lvl", "floor", "storey", "story", "basement", "b1", "b2",
                 "ground", "mezzanine", "roof"],
    "zone":     ["zone", "sector", "area", "wing", "block", "phase", "package"],
    "room":     ["room", "apartment", "unit", "suite", "bathroom", "kitchen",
                 "office", "lobby"],
    "building": ["building", "tower", "block", "structure"],
}

# Area type detection
_AREA_KEYWORDS: dict[str, list[str]] = {
    "basement": ["basement", "carpark", "car park", "underground", "b1", "b2", "b3"],
    "roof":     ["roof", "rooftop", "plant room"],
    "podium":   ["podium"],
    "external": ["external", "landscaping", "civil", "pavement", "road", "yard"],
}

# Work type detection (extension field)
_WORK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "slab":       ["slab", "pour", "deck", "topping"],
    "column":     ["column", "pier", "pillar", "post"],
    "wall":       ["wall", "shear wall", "core wall", "blade"],
    "core":       ["core", "stair", "lift shaft", "shaft"],
    "facade":     ["facade", "curtain wall", "cladding", "glazing"],
    "services":   ["mep", "hvac", "mechanical", "electrical", "plumbing", "fire",
                   "services"],
    "fitout":     ["fitout", "fit-out", "joinery", "ceiling", "partition"],
    "inspection": ["inspection", "hold point", "witness point", "itp", "test"],
}


def _keyword_match(text: str, keyword_map: dict[str, list[str]]) -> str:
    """Return the first matching key or 'unknown'."""
    def _normalize_match_text(value: str) -> str:
        # Remove ampersands before punctuation stripping so tokens like FF&E
        # normalize to "ffe" instead of splitting into "ff e".
        normalized = value.lower().replace("&", "")
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    normalized_text = _normalize_match_text(text)
    if not normalized_text:
        return "unknown"
    padded_text = f" {normalized_text} "

    for label, keywords in keyword_map.items():
        for kw in keywords:
            normalized_kw = _normalize_match_text(kw)
            if normalized_kw and f" {normalized_kw} " in padded_text:
                return label
    return "unknown"


def build_compressed_context(
    activity_name: str,
    level_name: Optional[str] = None,
    zone_name: Optional[str] = None,
) -> dict:
    """
    Extract a fixed-schema compressed context from available activity metadata.

    Schema (base):
      phase        — construction phase the activity belongs to
      spatial_type — most specific spatial descriptor present
      area_type    — internal | external | roof | basement | podium | unknown

    Extension fields (approved, bounded enum):
      work_type    — specific work type inferred from activity name

    Unknown context tokens are not included in the compressed context because
    including free-text values would explode the cache key space.
    """
    combined = " ".join(filter(None, [activity_name, level_name, zone_name]))

    phase = _keyword_match(combined, _PHASE_KEYWORDS)

    # Spatial type: prefer the most specific signal available
    if zone_name and zone_name.strip():
        spatial_type = "zone"
    elif level_name and level_name.strip():
        spatial_type = "level"
    else:
        spatial_type = _keyword_match(combined, _SPATIAL_KEYWORDS)

    area_type = _keyword_match(combined, _AREA_KEYWORDS)
    if area_type == "unknown":
        area_type = "internal"  # default for construction activities

    work_type = _keyword_match(activity_name, _WORK_TYPE_KEYWORDS)

    return {
        "phase": phase,
        "spatial_type": spatial_type,
        "area_type": area_type,
        "work_type": work_type,
    }


# ---------------------------------------------------------------------------
# 2. Deterministic context key
# ---------------------------------------------------------------------------

def build_context_key(
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    compressed_context: dict,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> str:
    """
    Return a 64-character hex SHA-256 digest that uniquely identifies a
    (item, asset_type, duration, context, versions) tuple.

    sort_keys=True guarantees the same JSON regardless of dict insertion order.
    """
    payload = {
        "item_id": str(item_id),
        "asset_type": asset_type,
        "duration_days": int(duration_days),
        "compressed_context": compressed_context,
        "context_version": context_version,
        "inference_version": inference_version,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 2.5 Global knowledge helpers
# ---------------------------------------------------------------------------

def duration_bucket_for_days(duration_days: int) -> int:
    duration_days = max(int(duration_days or 0), 1)
    if duration_days <= 5:
        return duration_days
    if duration_days <= 10:
        return 7
    if duration_days <= 20:
        return 14
    return 28


def _project_has_asset_type(
    db: Session,
    project_id: uuid.UUID,
    asset_type: str,
) -> bool:
    return (
        db.query(Asset.id)
        .filter(
            Asset.project_id == project_id,
            Asset.canonical_type == asset_type,
        )
        .first()
        is not None
    )


def _resample_normalized_distribution(
    normalized_distribution: list[float],
    target_length: int,
) -> list[float]:
    if target_length <= 0:
        return []
    if not normalized_distribution:
        return _uniform_normalized(target_length)

    source = derive_normalized_distribution([max(0.0, float(value)) for value in normalized_distribution])
    source_length = len(source)
    if source_length == target_length:
        return source

    # Piecewise-linear bucket overlap so we preserve total mass while changing resolution.
    result = [0.0] * target_length
    for target_idx in range(target_length):
        target_start = target_idx / target_length
        target_end = (target_idx + 1) / target_length
        accumulated = 0.0
        for source_idx, source_value in enumerate(source):
            source_start = source_idx / source_length
            source_end = (source_idx + 1) / source_length
            overlap = max(0.0, min(target_end, source_end) - max(target_start, source_start))
            if overlap > 0:
                accumulated += source_value * (overlap * source_length)
        result[target_idx] = accumulated
    return derive_normalized_distribution(result)


def _coefficient_of_variation(posterior_mean: float, posterior_precision: float) -> float:
    return (1.0 / math.sqrt(float(posterior_precision))) / float(posterior_mean)


def _global_confidence_tier(
    *,
    posterior_mean: float,
    posterior_precision: float,
    source_project_count: int,
    sample_count: int,
    correction_count: int = 0,
) -> str | None:
    if posterior_mean <= 0 or posterior_precision <= 0:
        return None
    cv = _coefficient_of_variation(posterior_mean, posterior_precision)
    base_tier: str | None = None
    if sample_count >= 10 and source_project_count >= 3 and cv < WORK_PROFILE_CV_TRUSTED:
        base_tier = "high"
    elif sample_count >= 3 and source_project_count >= 1 and cv < WORK_PROFILE_CV_CONFIRMED:
        base_tier = "medium"
    if base_tier is None:
        return None

    if (
        sample_count >= WORK_PROFILE_CORRECTION_MIN_SAMPLES
        and correction_count > 0
        and correction_count / sample_count > WORK_PROFILE_CORRECTION_RATE_THRESHOLD
    ):
        return "medium"

    return base_tier


# ---------------------------------------------------------------------------
# 3. Maturity tier evaluation
# ---------------------------------------------------------------------------

def work_profile_maturity(profile: ItemContextProfile) -> str:
    """
    Evaluate the maturity tier of a cached work profile.

    MANUAL           — source='manual'; AI is never called regardless of precision.
    TRUSTED_BASELINE — cv < 0.10; use posterior_mean directly, skip AI.
    CONFIRMED        — 0.10 <= cv < 0.20; call AI with posterior as hint, update posterior.
    TENTATIVE        — cv >= 0.20 or no posterior; call AI fresh, update posterior.

    Correction rate override: if correction_count / sample_count > 0.20 and
    sample_count >= 3, treat as TENTATIVE even if precision is high (quietly wrong).
    """
    if profile.source == "manual":
        return MATURITY_MANUAL

    pm = profile.posterior_mean
    pp = profile.posterior_precision
    if pm is None or pp is None or float(pm) <= 0 or float(pp) <= 0:
        return MATURITY_TENTATIVE

    # Correction rate override
    sc = int(profile.sample_count or 0)
    cc = int(profile.correction_count or 0)
    if sc >= WORK_PROFILE_CORRECTION_MIN_SAMPLES and cc / sc > WORK_PROFILE_CORRECTION_RATE_THRESHOLD:
        return MATURITY_TENTATIVE

    cv = (1.0 / math.sqrt(float(pp))) / float(pm)
    if cv < WORK_PROFILE_CV_TRUSTED:
        return MATURITY_TRUSTED_BASELINE
    if cv < WORK_PROFILE_CV_CONFIRMED:
        return MATURITY_CONFIRMED
    return MATURITY_TENTATIVE


# ---------------------------------------------------------------------------
# 4. Bayesian posterior update
# ---------------------------------------------------------------------------

def _obs_precision(value: float, source: str) -> float:
    """
    Compute observation precision (τ = 1/σ²) for a new total_hours observation.

    AI estimates:   σ = value × 0.20  → obs_precision = 1 / (0.20v)²
    Actual hours:   σ = value × 0.05  → obs_precision = 1 / (0.05v)²
    Manual:         treated as near-infinite precision (σ → 0)
    """
    if value <= 0:
        return 0.0
    if source == "actual":
        sigma = value * WORK_PROFILE_ACTUAL_ERROR_FRACTION
    elif source == "manual":
        # Manual truth overrides everything; use current precision × 1000
        # (caller is expected to handle manual separately, but be safe here)
        sigma = value * 0.001
    else:  # ai, learned, default
        sigma = value * WORK_PROFILE_AI_ERROR_FRACTION
    return 1.0 / (sigma ** 2) if sigma > 0 else 0.0


def bayesian_update(
    current_mean: float,
    current_precision: float,
    obs_value: float,
    obs_precision: float,
) -> tuple[float, float]:
    """
    Normal-Normal conjugate update.

    new_precision = τ_current + τ_obs
    new_mean      = (τ_current × μ_current + τ_obs × x_obs) / new_precision
    """
    new_precision = current_precision + obs_precision
    if new_precision <= 0:
        return current_mean, current_precision
    new_mean = (current_precision * current_mean + obs_precision * obs_value) / new_precision
    return new_mean, new_precision


def _initial_posterior(total_hours: float, confidence: float) -> tuple[float, float]:
    """
    Set the initial prior for a new (first-encounter) AI-generated profile.

    σ_prior = total_hours × (1 − confidence)
    posterior_mean = total_hours
    posterior_precision = 1 / σ_prior²

    If total_hours is zero (asset_type='none'), posterior is None (not updated).
    """
    if total_hours <= 0:
        return total_hours, 0.0
    sigma = total_hours * (1.0 - float(confidence))
    if sigma <= 0:
        sigma = total_hours * 0.01  # fallback when confidence >= 1.0: assume 1% uncertainty
    return total_hours, 1.0 / (sigma ** 2)


# ---------------------------------------------------------------------------
# 5. Hours finalization — bounds, baseline, quantization
# ---------------------------------------------------------------------------

def quantize_hours(hours: float) -> float:
    """Round to the nearest 0.5-hour operational unit (conventional round-half-up)."""
    # math.floor(x + 0.5) avoids Python's banker's rounding (round-half-to-even)
    # so that 10.25 → 10.5, not 10.0.
    return math.floor(hours / WORK_PROFILE_OPERATIONAL_UNIT + 0.5) * WORK_PROFILE_OPERATIONAL_UNIT


def active_context_profile_query(db: Session, *, include_invalidated: bool = False):
    """Return the shared query for reusable local profiles that are still active."""
    query = db.query(ItemContextProfile)
    if include_invalidated:
        return query
    return query.filter(ItemContextProfile.invalidated_at.is_(None))


def _assert_profile_mutable(profile: ItemContextProfile) -> None:
    if getattr(profile, "invalidated_at", None) is not None:
        raise ValueError(f"Context profile {getattr(profile, 'id', 'unknown')} is invalidated and immutable")


def invalidate_context_profile(
    profile: ItemContextProfile,
    *,
    reason: str,
    superseded_by_profile_id: uuid.UUID | None = None,
) -> ItemContextProfile:
    """Soft-invalidate a reusable cache entry without deleting historical evidence."""
    if getattr(profile, "invalidated_at", None) is not None:
        return profile
    profile.invalidated_at = datetime.now(timezone.utc)
    profile.invalidation_reason = reason
    profile.superseded_by_profile_id = superseded_by_profile_id
    return profile


def invalidate_profiles_for_item_asset_type(
    db: Session,
    *,
    item_id: uuid.UUID,
    old_asset_type: str,
    new_asset_type: str | None = None,
    reason: str,
) -> int:
    """Invalidate active reusable profiles for an item's superseded asset type."""
    if not old_asset_type or old_asset_type == new_asset_type:
        return 0

    rows = (
        active_context_profile_query(db)
        .filter(
            ItemContextProfile.item_id == item_id,
            ItemContextProfile.asset_type == old_asset_type,
            ItemContextProfile.project_id.isnot(None),
        )
        .all()
    )
    for row in rows:
        invalidate_context_profile(row, reason=reason)

    affected_buckets = {
        (
            duration_bucket_for_days(int(row.duration_days or 0)),
            int(row.context_version or WORK_PROFILE_CONTEXT_VERSION),
            int(row.inference_version or WORK_PROFILE_INFERENCE_VERSION),
        )
        for row in rows
    }
    db.flush()
    for duration_bucket, context_version, inference_version in sorted(affected_buckets):
        rebuild_global_knowledge_entry(
            db,
            item_id=item_id,
            asset_type=old_asset_type,
            duration_bucket=duration_bucket,
            context_version=context_version,
            inference_version=inference_version,
        )
        if new_asset_type:
            rebuild_global_knowledge_entry(
                db,
                item_id=item_id,
                asset_type=new_asset_type,
                duration_bucket=duration_bucket,
                context_version=context_version,
                inference_version=inference_version,
            )
    return len(rows)


def _find_trusted_baseline(
    db: Session,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> Optional[float]:
    """
    Return the median total_hours across trusted historical cache entries for
    (item_id, asset_type, duration_days), or None if no baseline exists.

    Manual rows establish a baseline immediately.
    Learned / AI rows require NOT low_confidence_flag and
    sample_count >= WORK_PROFILE_CORRECTION_MIN_SAMPLES for each qualifying row.
    In addition, this function requires at least
    WORK_PROFILE_CORRECTION_MIN_SAMPLES qualifying learned / AI rows before it
    will return a baseline, so the current rule is a dual threshold.
    """
    manual_rows = (
        active_context_profile_query(db)
        .filter(
            ItemContextProfile.project_id == project_id,
            ItemContextProfile.item_id == item_id,
            ItemContextProfile.asset_type == asset_type,
            ItemContextProfile.duration_days == duration_days,
            ItemContextProfile.source == "manual",
        )
        .all()
    )
    if manual_rows:
        values = [
            float(r.posterior_mean) if r.posterior_mean is not None else float(r.total_hours)
            for r in manual_rows
        ]
        if values:
            values.sort()
            mid = len(values) // 2
            if len(values) % 2 == 0:
                return (values[mid - 1] + values[mid]) / 2.0
            return values[mid]

    rows = (
        active_context_profile_query(db)
        .filter(
            ItemContextProfile.project_id == project_id,
            ItemContextProfile.item_id == item_id,
            ItemContextProfile.asset_type == asset_type,
            ItemContextProfile.duration_days == duration_days,
            ItemContextProfile.context_version == context_version,
            ItemContextProfile.inference_version == inference_version,
            ItemContextProfile.source.in_(["learned", "ai"]),
            ItemContextProfile.low_confidence_flag.is_(False),
            ItemContextProfile.sample_count >= WORK_PROFILE_CORRECTION_MIN_SAMPLES,
        )
        .all()
    )
    if not rows:
        return None
    # Require at least 3 distinct context entries to form a reliable baseline.
    if len(rows) < WORK_PROFILE_CORRECTION_MIN_SAMPLES:
        return None

    # Use posterior_mean when available (more accurate than stored initial value)
    values = [
        float(r.posterior_mean) if r.posterior_mean is not None else float(r.total_hours)
        for r in rows
    ]
    if not values:
        return None
    values.sort()
    mid = len(values) // 2
    if len(values) % 2 == 0:
        return (values[mid - 1] + values[mid]) / 2.0
    return values[mid]


def finalize_total_hours(
    ai_proposal: float,
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
    trusted_baseline: Optional[float] = None,
    manual_truth: Optional[float] = None,
) -> float:
    """
    Apply the V1 total-hours finalization policy (§10.7):

      if manual_truth exists → use manual_truth (quantized)
      elif trusted_baseline exists → use trusted_baseline (quantized)
      else → bound ai_proposal and quantize

    For asset_type='none', always returns 0.0.
    """
    if asset_type == "none":
        return 0.0

    if manual_truth is not None:
        return quantize_hours(manual_truth)

    if trusted_baseline is not None:
        return quantize_hours(trusted_baseline)

    # Bound the AI proposal
    max_hours = max_hours_per_day * duration_days
    bounded = max(WORK_PROFILE_MIN_HOURS, min(float(ai_proposal), max_hours))
    return quantize_hours(bounded)


# ---------------------------------------------------------------------------
# 6. Distribution derivation and normalization
# ---------------------------------------------------------------------------

def derive_distribution(
    normalized_distribution: list[float],
    total_hours: float,
    max_hours_per_day: Optional[float] = None,
) -> list[float]:
    """
    Derive raw distribution from finalized total_hours × normalized_distribution.

    Returns a list of floats that sums to total_hours (within floating-point
    tolerance).  Each value is rounded to 4 decimal places.
    """
    if total_hours <= 0:
        return [0.0] * len(normalized_distribution)
    if not normalized_distribution:
        return []

    weights = derive_normalized_distribution(normalized_distribution)
    total_units = round(total_hours / WORK_PROFILE_OPERATIONAL_UNIT)
    cap_units = None
    if max_hours_per_day is not None:
        cap_units = round(max_hours_per_day / WORK_PROFILE_OPERATIONAL_UNIT)

    ideal_units = [w * total_units for w in weights]
    allocated = [math.floor(value) for value in ideal_units]
    if cap_units is not None:
        allocated = [min(value, cap_units) for value in allocated]

    remaining = total_units - sum(allocated)
    while remaining > 0:
        candidates = [
            (ideal_units[i] - allocated[i], -i, i)
            for i in range(len(allocated))
            if cap_units is None or allocated[i] < cap_units
        ]
        if not candidates:
            break
        _, _, best_idx = max(candidates)
        allocated[best_idx] += 1
        remaining -= 1

    return [round(units * WORK_PROFILE_OPERATIONAL_UNIT, 4) for units in allocated]


def apportion_daily_hours(
    normalized_distribution: list[float],
    total_hours: float,
    *,
    max_hours_per_day: Optional[float] = None,
) -> list[float]:
    """Public exact-unit apportionment helper used by planning/demand surfaces.

    The allocation is deterministic in 0.5-hour units using largest remainder
    with earlier-day tie-breaking via derive_distribution().
    """
    return derive_distribution(
        normalized_distribution,
        total_hours,
        max_hours_per_day=max_hours_per_day,
    )


def derive_normalized_distribution(distribution: list[float]) -> list[float]:
    """Convert raw distribution to normalized form (sums to 1.0 or all zeros)."""
    total = sum(distribution)
    if total <= 0:
        return [0.0] * len(distribution)

    normalized = [v / total for v in distribution]
    rounded = [round(v, 6) for v in normalized[:-1]]
    last_value = max(0.0, round(1.0 - sum(rounded), 6))
    return rounded + [last_value]


def _base_context_only(compressed_context: dict) -> dict:
    """Return the fixed-schema base context used for reduced-context fallback."""
    return {
        "phase": compressed_context.get("phase", "unknown"),
        "spatial_type": compressed_context.get("spatial_type", "unknown"),
        "area_type": compressed_context.get("area_type", "unknown"),
    }


def _context_expansion_signature(base_context: dict) -> str:
    return (
        f"phase={base_context.get('phase', 'unknown')};"
        f"spatial_type={base_context.get('spatial_type', 'unknown')};"
        f"area_type={base_context.get('area_type', 'unknown')}"
    )


def _lookup_cache_with_reduced_context(
    db: Session,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    compressed_context: dict,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
    skip_signal_query: bool = False,
) -> tuple[Optional[ItemContextProfile], str]:
    """
    Look up cache entries using the deterministic fallback order:
    1. full compressed context
    2. base compressed context only (drop work_type)
    """
    exact_hash = build_context_key(
        item_id,
        asset_type,
        duration_days,
        compressed_context,
        context_version,
        inference_version,
    )
    cached = lookup_cache(
        db,
        project_id,
        item_id,
        asset_type,
        duration_days,
        exact_hash,
        context_version,
        inference_version,
    )
    if cached is not None:
        return cached, exact_hash

    base_context = _base_context_only(compressed_context)
    if base_context == compressed_context:
        return None, exact_hash

    promoted_signal = None
    if not skip_signal_query:
        promoted_signal = (
            db.query(ContextExpansionSignal.id)
            .filter(
                ContextExpansionSignal.item_id == item_id,
                ContextExpansionSignal.asset_type == asset_type,
                ContextExpansionSignal.context_signature == _context_expansion_signature(base_context),
                ContextExpansionSignal.context_version == context_version,
                ContextExpansionSignal.inference_version == inference_version,
                ContextExpansionSignal.promoted.is_(True),
            )
            .first()
        )
    if promoted_signal is not None:
        return None, exact_hash

    base_hash = build_context_key(
        item_id,
        asset_type,
        duration_days,
        base_context,
        context_version,
        inference_version,
    )
    cached = lookup_cache(
        db,
        project_id,
        item_id,
        asset_type,
        duration_days,
        base_hash,
        context_version,
        inference_version,
    )
    if cached is not None:
        logger.debug(
            "Item %s asset=%s dur=%d: reduced-context cache HIT (dropped work_type)",
            item_id,
            asset_type,
            duration_days,
        )
        return cached, base_hash

    return None, exact_hash


def _uniform_normalized(duration_days: int) -> list[float]:
    """Return a uniform normalized distribution over duration_days.

    The last element is adjusted so the rounded values sum to exactly 1.0.
    """
    if duration_days <= 0:
        return []
    unit = round(1.0 / duration_days, 6)
    weights = [unit] * duration_days
    weights[-1] = round(1.0 - sum(weights[:-1]), 6)
    return weights


def _coerce_manual_distribution(
    *,
    duration_days: int,
    normalized_distribution: list[float] | None,
    distribution: list[float] | None,
) -> list[float]:
    if normalized_distribution is not None and len(normalized_distribution) == duration_days:
        return derive_normalized_distribution([max(0.0, float(value)) for value in normalized_distribution])
    if distribution is not None and len(distribution) == duration_days:
        return derive_normalized_distribution([max(0.0, float(value)) for value in distribution])
    return _uniform_normalized(duration_days)


# ---------------------------------------------------------------------------
# 7. Validation
# ---------------------------------------------------------------------------

def validate_stage_b(
    distribution: list[float],
    asset_type: str,
    max_hours_per_day: float,
    duration_days: int,
) -> ValidationResult:
    """
    Stage B — work-profile AI proposal validation (§13.7).

    Checks:
    - distribution length matches duration_days
    - all buckets >= 0
    - for 'none': all buckets must be zero
    - for non-'none': no bucket exceeds max_hours_per_day
    """
    errors: list[str] = []

    if len(distribution) != duration_days:
        errors.append(
            f"distribution length {len(distribution)} != duration_days {duration_days}"
        )
        return ValidationResult(valid=False, errors=errors)

    if asset_type == "none":
        if any(b != 0 for b in distribution):
            errors.append("asset_type='none' requires all distribution buckets to be zero")
    else:
        for i, bucket in enumerate(distribution):
            if bucket < 0:
                errors.append(f"distribution[{i}]={bucket} is negative")
            elif bucket > max_hours_per_day:
                errors.append(
                    f"distribution[{i}]={bucket} exceeds "
                    f"max_hours_per_day={max_hours_per_day} for '{asset_type}'"
                )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_stage_d(
    total_hours: float,
    distribution: list[float],
    normalized_distribution: list[float],
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
) -> ValidationResult:
    """
    Stage D — final profile invariants (§13.7).

    Checks:
    - total_hours >= 0 and is representable in operational unit (0.5h)
    - for 'none': total_hours == 0 and all distributions zero
    - distribution sums to total_hours within tolerance
    - all distribution values >= 0
    - total_hours within allowed bounds
    - normalized_distribution sums to 1.0 (when total_hours > 0)
    - asset_type consistency already enforced by caller
    """
    errors: list[str] = []

    if total_hours < 0:
        errors.append(f"total_hours={total_hours} is negative")

    # Representable in operational unit
    remainder = abs(total_hours - quantize_hours(total_hours))
    if remainder > 1e-9:
        errors.append(
            f"total_hours={total_hours} is not representable in "
            f"{WORK_PROFILE_OPERATIONAL_UNIT}-hour increments"
        )

    if asset_type == "none":
        if total_hours != 0:
            errors.append(f"asset_type='none' requires total_hours=0, got {total_hours}")
        if any(b != 0 for b in distribution):
            errors.append("asset_type='none' requires all distribution buckets to be zero")
        if any(b != 0 for b in normalized_distribution):
            errors.append(
                "asset_type='none' requires all normalized_distribution buckets to be zero"
            )
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    # Non-'none' bounds
    max_hours = max_hours_per_day * duration_days
    if total_hours > max_hours:
        errors.append(
            f"total_hours={total_hours} exceeds max bound "
            f"{max_hours} ({max_hours_per_day} × {duration_days} days)"
        )

    # Distribution sum
    if len(distribution) != duration_days:
        errors.append(
            f"distribution length {len(distribution)} != duration_days {duration_days}"
        )

    dist_sum = sum(distribution)
    if abs(dist_sum - total_hours) > 0.01:
        errors.append(
            f"sum(distribution)={dist_sum:.4f} differs from total_hours={total_hours}"
        )

    if len(normalized_distribution) != duration_days:
        errors.append(
            "normalized_distribution length "
            f"{len(normalized_distribution)} != duration_days {duration_days}"
        )

    # All non-negative
    neg = [i for i, v in enumerate(distribution) if v < 0]
    if neg:
        errors.append(f"distribution has negative buckets at indices {neg}")

    neg_norm = [i for i, v in enumerate(normalized_distribution) if v < 0]
    if neg_norm:
        errors.append(
            f"normalized_distribution has negative buckets at indices {neg_norm}"
        )

    over_cap = [i for i, v in enumerate(distribution) if v > max_hours_per_day]
    if over_cap:
        errors.append(
            "distribution exceeds max_hours_per_day at indices "
            f"{over_cap}"
        )

    # Normalized sum
    if total_hours > 0:
        norm_sum = sum(normalized_distribution)
        if abs(norm_sum - 1.0) > WORK_PROFILE_NORM_DIST_SUM_TOLERANCE:
            errors.append(
                f"sum(normalized_distribution)={norm_sum:.8f} is not within "
                f"{WORK_PROFILE_NORM_DIST_SUM_TOLERANCE} of 1.0"
            )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# 8. Stage B cap redistribution
# ---------------------------------------------------------------------------

def redistribute_capped_distribution(
    distribution: list[float],
    max_hours_per_day: float,
    total_hours: float,
) -> tuple[list[float], bool]:
    """
    Redistribute excess hours from buckets that exceed max_hours_per_day to
    uncapped buckets. Returns (adjusted_distribution, fallback_triggered).
    """
    n = len(distribution)
    adjusted = [min(b, max_hours_per_day) for b in distribution]

    if abs(sum(adjusted) - total_hours) <= 0.01:
        return adjusted, False

    uncapped_indices = [i for i, v in enumerate(adjusted) if v < max_hours_per_day]
    if not uncapped_indices:
        per_bucket = min(round(total_hours / n, 4), max_hours_per_day)
        return [per_bucket] * n, True

    uncapped_set = set(uncapped_indices)
    capped_sum = sum(v for i, v in enumerate(adjusted) if i not in uncapped_set)
    remaining = total_hours - capped_sum
    current_uncapped_sum = sum(adjusted[i] for i in uncapped_indices)
    extra = remaining - current_uncapped_sum
    if extra > 0:
        per_add = extra / len(uncapped_indices)
        for i in uncapped_indices:
            adjusted[i] = round(min(adjusted[i] + per_add, max_hours_per_day), 4)

    if abs(sum(adjusted) - total_hours) > 0.01:
        per_bucket = min(round(total_hours / n, 4), max_hours_per_day)
        return [per_bucket] * n, True

    return adjusted, False


# ---------------------------------------------------------------------------
# 9. Default / fallback profile builder
# ---------------------------------------------------------------------------

_FALLBACK_FAMILY_SHORT_WEIGHTS: dict[str, dict[int, list[float]]] = {
    "steady": {
        1: [1.0],
        2: [0.50, 0.50],
        3: [0.34, 0.33, 0.33],
        4: [0.26, 0.25, 0.25, 0.24],
        5: [0.21, 0.20, 0.20, 0.20, 0.19],
    },
    "steady_front": {
        1: [1.0],
        2: [0.53, 0.47],
        3: [0.38, 0.33, 0.29],
        4: [0.30, 0.27, 0.23, 0.20],
        5: [0.25, 0.22, 0.20, 0.18, 0.15],
    },
    "front_loaded": {
        1: [1.0],
        2: [0.60, 0.40],
        3: [0.45, 0.35, 0.20],
        4: [0.35, 0.30, 0.20, 0.15],
        5: [0.30, 0.25, 0.20, 0.15, 0.10],
    },
    "center_peak": {
        1: [1.0],
        2: [0.45, 0.55],
        3: [0.25, 0.50, 0.25],
        4: [0.18, 0.32, 0.30, 0.20],
        5: [0.12, 0.22, 0.34, 0.20, 0.12],
    },
    "event_peak": {
        1: [1.0],
        2: [0.35, 0.65],
        3: [0.15, 0.70, 0.15],
        4: [0.10, 0.30, 0.45, 0.15],
        5: [0.08, 0.17, 0.45, 0.20, 0.10],
    },
    "back_loaded": {
        1: [1.0],
        2: [0.40, 0.60],
        3: [0.20, 0.35, 0.45],
        4: [0.15, 0.20, 0.30, 0.35],
        5: [0.10, 0.15, 0.20, 0.25, 0.30],
    },
}

_ASSET_FALLBACK_PRIORS: dict[str, dict[str, object]] = {
    "crane": {
        "utilisation_fraction": 0.50,
        "shape_family": "front_loaded",
        "guidance": "Crane demand is usually lift-intensive and concentrated around install windows.",
    },
    "hoist": {
        "utilisation_fraction": 0.50,
        "shape_family": "steady",
        "guidance": "Hoist demand behaves like steady logistics support over active days.",
    },
    "loading_bay": {
        "utilisation_fraction": 0.60,
        "shape_family": "steady",
        "guidance": "Loading-bay demand is logistics-led and typically spread across the active window.",
    },
    "ewp": {
        "utilisation_fraction": 0.45,
        "shape_family": "center_peak",
        "guidance": "EWP demand usually builds into the main access and installation window.",
    },
    "concrete_pump": {
        "utilisation_fraction": 0.25,
        "shape_family": "event_peak",
        "guidance": "Concrete-pump demand should cluster around pour days rather than remain uniform.",
    },
    "excavator": {
        "utilisation_fraction": 0.65,
        "shape_family": "steady_front",
        "guidance": "Excavator demand is typically continuous while the workfront is open.",
    },
    "forklift": {
        "utilisation_fraction": 0.45,
        "shape_family": "steady",
        "guidance": "Forklift demand is moderate, logistics-driven, and usually fairly even.",
    },
    "telehandler": {
        "utilisation_fraction": 0.45,
        "shape_family": "steady_front",
        "guidance": "Telehandler demand is moderate and often slightly front-loaded during lift windows.",
    },
    "compactor": {
        "utilisation_fraction": 0.40,
        "shape_family": "back_loaded",
        "guidance": "Compactor demand often lands later after preparation or placement is complete.",
    },
    "other": {
        "utilisation_fraction": 0.35,
        "shape_family": "steady",
        "guidance": "Unknown plant should default to a conservative, moderately even profile.",
    },
}


def _linear_weight_profile(duration_days: int, start: float, end: float) -> list[float]:
    if duration_days <= 0:
        return []
    if duration_days == 1:
        return [1.0]
    step = (end - start) / (duration_days - 1)
    raw = [start + (step * i) for i in range(duration_days)]
    return derive_normalized_distribution(raw)


def _peaked_weight_profile(
    duration_days: int,
    *,
    base: float,
    amplitude: float,
) -> list[float]:
    if duration_days <= 0:
        return []
    if duration_days == 1:
        return [1.0]
    peak_idx = duration_days // 2
    sigma = max(duration_days / 5.0, 0.9)
    raw = []
    for idx in range(duration_days):
        distance = (idx - peak_idx) / sigma
        raw.append(base + amplitude * math.exp(-0.5 * distance * distance))
    return derive_normalized_distribution(raw)


def _fallback_shape_weights(shape_family: str, duration_days: int) -> list[float]:
    short = _FALLBACK_FAMILY_SHORT_WEIGHTS.get(shape_family, {})
    if duration_days in short:
        return derive_normalized_distribution(short[duration_days])

    if shape_family == "steady":
        return _uniform_normalized(duration_days)
    if shape_family == "steady_front":
        return _linear_weight_profile(duration_days, 1.15, 0.85)
    if shape_family == "front_loaded":
        return _linear_weight_profile(duration_days, 1.55, 0.45)
    if shape_family == "back_loaded":
        return _linear_weight_profile(duration_days, 0.45, 1.55)
    if shape_family == "center_peak":
        return _peaked_weight_profile(duration_days, base=0.75, amplitude=1.15)
    if shape_family == "event_peak":
        return _peaked_weight_profile(duration_days, base=0.25, amplitude=2.00)
    return _uniform_normalized(duration_days)


def _profile_shape_to_fallback_family(profile_shape: str | None) -> str | None:
    token = str(profile_shape or "").strip().lower()
    if token == "single_day":
        return "event_peak"
    if token == "flat":
        return "steady"
    if token == "front_loaded":
        return "front_loaded"
    if token == "back_loaded":
        return "back_loaded"
    if token == "bell":
        return "center_peak"
    if token == "inverse_bell":
        return "steady_front"
    if token == "staged":
        return "event_peak"
    return None


def _build_default_profile_prior(
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
    compressed_context: Optional[dict] = None,
) -> dict[str, object]:
    duration_days = max(int(duration_days or 0), 1)
    compressed_context = compressed_context or {}
    phase = str(compressed_context.get("phase") or "unknown")
    spatial_type = str(compressed_context.get("spatial_type") or "unknown")
    area_type = str(compressed_context.get("area_type") or "unknown")
    work_type = str(compressed_context.get("work_type") or "unknown")

    if asset_type == "none":
        zeros = [0.0] * duration_days
        return {
            "asset_type": asset_type,
            "shape_family": "zero",
            "utilisation_fraction": 0.0,
            "default_total_hours": 0.0,
            "normalized_distribution": zeros,
            "guidance": "Milestone and non-productive rows carry no bookable asset demand.",
        }

    base_prior = dict(_ASSET_FALLBACK_PRIORS.get(asset_type, _ASSET_FALLBACK_PRIORS["other"]))
    utilisation = float(base_prior["utilisation_fraction"])
    shape_family = str(base_prior["shape_family"])
    guidance_parts = [str(base_prior["guidance"])]
    inspection_like = work_type == "inspection"

    if inspection_like:
        utilisation = min(utilisation, 0.15)
        shape_family = "steady"
        guidance_parts.append("Inspection and test rows usually consume only light plant time.")

    if not inspection_like:
        if asset_type == "crane":
            if phase == "structure":
                utilisation += 0.10
                shape_family = "front_loaded"
                guidance_parts.append("Structural crane work usually peaks during heavy lift and install windows.")
            elif phase == "facade":
                utilisation += 0.05
                shape_family = "center_peak"
                guidance_parts.append("Facade lift work usually ramps into the main install window.")
            if work_type in {"slab", "column", "wall", "core", "facade"}:
                utilisation += 0.05
        elif asset_type == "hoist":
            if phase in {"fitout", "services"}:
                utilisation += 0.05
                guidance_parts.append("Fitout and services hoist demand is usually steady day-to-day logistics.")
            elif phase == "structure":
                utilisation -= 0.05
        elif asset_type == "loading_bay":
            if phase in {"fitout", "services", "external", "prelims"}:
                utilisation += 0.05
        elif asset_type == "ewp":
            if phase in {"facade", "services", "external"} or work_type in {"facade", "services"}:
                utilisation += 0.05
                shape_family = "center_peak"
        elif asset_type == "concrete_pump":
            shape_family = "event_peak"
            if phase == "structure":
                utilisation += 0.05
            if work_type in {"slab", "column", "wall", "core"}:
                utilisation += 0.05
            guidance_parts.append("Pump work should stay concentrated around pour days, not spread evenly.")
        elif asset_type == "excavator":
            if phase in {"external", "prelims"} or area_type == "external":
                utilisation += 0.05
                shape_family = "steady_front"
        elif asset_type == "forklift":
            if phase in {"fitout", "services", "prelims"}:
                utilisation += 0.05
        elif asset_type == "telehandler":
            if phase in {"structure", "external"}:
                utilisation += 0.05
                shape_family = "steady_front"
        elif asset_type == "compactor":
            shape_family = "back_loaded"
            if phase == "external" or area_type == "external":
                utilisation += 0.10
                guidance_parts.append("Compaction usually lands later in the activity once material is placed.")
        elif asset_type == "other":
            if phase in {"structure", "services", "external"}:
                utilisation += 0.05

        if area_type == "roof" and asset_type in {"crane", "ewp"}:
            utilisation += 0.05
    if spatial_type == "room" and asset_type in {"loading_bay", "hoist"}:
        utilisation -= 0.05
        guidance_parts.append("Room-scale work usually uses logistics assets less continuously.")

    utilisation = max(0.15, min(utilisation, 0.75))
    total_hours = quantize_hours(utilisation * max_hours_per_day * duration_days)
    if total_hours <= 0:
        total_hours = WORK_PROFILE_MIN_HOURS
    normalized_distribution = _fallback_shape_weights(shape_family, duration_days)
    guidance = " ".join(dict.fromkeys(guidance_parts))

    return {
        "asset_type": asset_type,
        "shape_family": shape_family,
        "utilisation_fraction": round(utilisation, 2),
        "default_total_hours": total_hours,
        "normalized_distribution": normalized_distribution,
        "guidance": guidance,
    }


def build_default_profile(
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
    compressed_context: Optional[dict] = None,
) -> tuple[float, list[float], list[float]]:
    """
    Build a conservative but construction-realistic fallback work profile.

    Returns (total_hours, distribution, normalized_distribution).

    The fallback is asset-specific and context-aware:
      - logistics assets default to steadier spreads
      - event assets (for example concrete pumps) cluster around peak days
      - structural lift assets tend to front-load into install windows
    """
    prior = _build_default_profile_prior(
        asset_type,
        duration_days,
        max_hours_per_day,
        compressed_context=compressed_context,
    )
    total_hours = float(prior["default_total_hours"])
    norm_dist = list(prior["normalized_distribution"])
    distribution = derive_distribution(norm_dist, total_hours, max_hours_per_day=max_hours_per_day)
    return total_hours, distribution, norm_dist


# ---------------------------------------------------------------------------
# 10. Cache lookup
# ---------------------------------------------------------------------------

def lookup_cache(
    db: Session,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    context_hash: str,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> Optional[ItemContextProfile]:
    """
    Look up an exact cache entry for the given deterministic context key.

    Returns None on a cache miss.  The caller should then check for a trusted
    baseline via _find_trusted_baseline() before calling AI.
    """
    return (
        active_context_profile_query(db)
        .filter(
            ItemContextProfile.project_id == project_id,
            ItemContextProfile.item_id == item_id,
            ItemContextProfile.asset_type == asset_type,
            ItemContextProfile.duration_days == duration_days,
            ItemContextProfile.context_version == context_version,
            ItemContextProfile.inference_version == inference_version,
            ItemContextProfile.context_hash == context_hash,
        )
        .first()
    )


# ---------------------------------------------------------------------------
# 11. Cache write / update
# ---------------------------------------------------------------------------

def _write_cache_entry(
    db: Session,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    context_hash: str,
    total_hours: float,
    distribution: list[float],
    normalized_distribution: list[float],
    confidence: float,
    source: str,
    low_confidence_flag: bool = False,
    sync_global: bool = True,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> ItemContextProfile:
    """
    Insert a new cache entry with initial Bayesian posterior.

    For 'default' source entries: evidence fields are left at zero (they don't
    accumulate evidence — they just prevent repeated failure loops).
    """
    if source == "default" or total_hours <= 0:
        pm, pp = None, None
        obs_count = 0
        ev_weight = 0.0
        sc = 0
    else:
        pm, pp = _initial_posterior(total_hours, confidence)
        obs_count = 1
        ev_weight = float(confidence)
        sc = 1

    profile = ItemContextProfile(
        project_id=project_id,
        item_id=item_id,
        asset_type=asset_type,
        duration_days=duration_days,
        context_version=context_version,
        inference_version=inference_version,
        context_hash=context_hash,
        total_hours=total_hours,
        distribution_json=distribution,
        normalized_distribution_json=normalized_distribution,
        confidence=confidence,
        source=source,
        low_confidence_flag=low_confidence_flag,
        observation_count=obs_count,
        evidence_weight=ev_weight,
        posterior_mean=pm,
        posterior_precision=pp,
        sample_count=sc,
        correction_count=0,
        actuals_count=0,
    )
    savepoint = db.begin_nested()
    db.add(profile)
    try:
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = (
            active_context_profile_query(db)
            .filter(
                ItemContextProfile.project_id == project_id,
                ItemContextProfile.item_id == item_id,
                ItemContextProfile.asset_type == asset_type,
                ItemContextProfile.duration_days == duration_days,
                ItemContextProfile.context_version == context_version,
                ItemContextProfile.inference_version == inference_version,
                ItemContextProfile.context_hash == context_hash,
            )
            .one_or_none()
        )
        if existing is None:
            raise
        return existing
    if sync_global and source != "default":
        rebuild_global_knowledge_entry(
            db,
            item_id=item_id,
            asset_type=asset_type,
            duration_bucket=duration_bucket_for_days(duration_days),
            context_version=context_version,
            inference_version=inference_version,
        )
    return profile


def _overwrite_cache_entry(
    profile: ItemContextProfile,
    *,
    total_hours: float,
    distribution: list[float],
    normalized_distribution: list[float],
    confidence: float,
    source: str,
    low_confidence_flag: bool,
) -> ItemContextProfile:
    _assert_profile_mutable(profile)
    profile.total_hours = total_hours
    profile.distribution_json = distribution
    profile.normalized_distribution_json = normalized_distribution
    profile.confidence = confidence
    profile.source = source
    profile.low_confidence_flag = low_confidence_flag
    return profile


def _upsert_cache_from_external_observation(
    db: Session,
    *,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    context_hash: str,
    total_hours: float,
    distribution: list[float],
    normalized_distribution: list[float],
    confidence: float,
    source: str,
    low_confidence_flag: bool,
    sync_global: bool = True,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> ItemContextProfile:
    existing = lookup_cache(
        db,
        project_id,
        item_id,
        asset_type,
        duration_days,
        context_hash,
        context_version,
        inference_version,
    )
    if existing is None:
        return _write_cache_entry(
            db,
            project_id,
            item_id,
            asset_type,
            duration_days,
            context_hash,
            total_hours,
            distribution,
            normalized_distribution,
            confidence,
            source,
            low_confidence_flag,
            sync_global=sync_global,
            context_version=context_version,
            inference_version=inference_version,
        )

    if existing.source == "manual":
        return existing

    if existing.source == "default":
        _overwrite_cache_entry(
            existing,
            total_hours=total_hours,
            distribution=distribution,
            normalized_distribution=normalized_distribution,
            confidence=confidence,
            source=source,
            low_confidence_flag=low_confidence_flag,
        )
        if total_hours > 0:
            pm, pp = _initial_posterior(total_hours, confidence)
            existing.posterior_mean = pm
            existing.posterior_precision = pp
            existing.observation_count = 1
            existing.evidence_weight = float(confidence)
            existing.sample_count = 1
        db.flush()
        if sync_global:
            rebuild_global_knowledge_entry(
                db,
                item_id=item_id,
                asset_type=asset_type,
                duration_bucket=duration_bucket_for_days(duration_days),
                context_version=context_version,
                inference_version=inference_version,
            )
        return existing

    _overwrite_cache_entry(
        existing,
        total_hours=total_hours,
        distribution=distribution,
        normalized_distribution=normalized_distribution,
        confidence=confidence,
        source=source,
        low_confidence_flag=low_confidence_flag,
    )
    existing.observation_count = int(existing.observation_count or 0) + 1
    existing.sample_count = int(existing.sample_count or 0) + 1
    existing.evidence_weight = float(existing.evidence_weight or 0) + float(confidence)

    obs_prec = _obs_precision(total_hours, source)
    if total_hours > 0 and obs_prec > 0:
        pm = float(existing.posterior_mean or 0)
        pp = float(existing.posterior_precision or 0)
        if pm <= 0 or pp <= 0:
            pm, pp = _initial_posterior(total_hours, confidence)
        else:
            pm, pp = bayesian_update(pm, pp, total_hours, obs_prec)
        existing.posterior_mean = pm
        existing.posterior_precision = pp

    db.flush()
    if sync_global:
        rebuild_global_knowledge_entry(
            db,
            item_id=item_id,
            asset_type=asset_type,
            duration_bucket=duration_bucket_for_days(duration_days),
            context_version=context_version,
            inference_version=inference_version,
        )
    return existing


def _preflight_work_profile_resolution(
    db: Session,
    *,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    activity_name: str,
    level_name: Optional[str],
    zone_name: Optional[str],
) -> WorkProfilePreflight:
    from ..core.constants import get_max_hours_for_type

    compressed = build_compressed_context(activity_name, level_name, zone_name)
    cached, context_hash = _lookup_cache_with_reduced_context(
        db,
        project_id,
        item_id,
        asset_type,
        duration_days,
        compressed,
        WORK_PROFILE_CONTEXT_VERSION,
        WORK_PROFILE_INFERENCE_VERSION,
    )
    tier = work_profile_maturity(cached) if cached is not None else None
    baseline = None
    global_knowledge = None
    if cached is None:
        baseline = _find_trusted_baseline(db, project_id, item_id, asset_type, duration_days)
        if baseline is None:
            global_knowledge = get_global_knowledge_entry(
                db,
                project_id=project_id,
                item_id=item_id,
                asset_type=asset_type,
                duration_days=duration_days,
                context_version=WORK_PROFILE_CONTEXT_VERSION,
                inference_version=WORK_PROFILE_INFERENCE_VERSION,
            )

    return WorkProfilePreflight(
        compressed_context=compressed,
        context_hash=context_hash,
        max_hours_per_day=get_max_hours_for_type(db, asset_type),
        cached=cached,
        tier=tier,
        trusted_baseline=baseline,
        global_knowledge=global_knowledge,
    )


def _preflight_needs_ai(preflight: WorkProfilePreflight) -> tuple[bool, Optional[dict[str, float]]]:
    if preflight.cached is not None:
        if preflight.tier in (MATURITY_CONFIRMED, MATURITY_TENTATIVE):
            hint = (
                _posterior_hint_payload(preflight.cached)
                if preflight.tier == MATURITY_CONFIRMED
                else None
            )
            return True, hint
        return False, None
    if preflight.trusted_baseline is not None:
        return False, None
    if preflight.global_knowledge is not None:
        if preflight.global_knowledge.confidence_tier == "medium":
            return True, {
                "posterior_mean": float(preflight.global_knowledge.posterior_mean),
                "posterior_precision": float(preflight.global_knowledge.posterior_precision),
                "sample_count": float(preflight.global_knowledge.sample_count or 0),
                "confidence": 0.8,
            }
        return False, None
    return True, None


def _update_cache_on_hit(
    db: Session,
    profile: ItemContextProfile,
) -> None:
    """
    Increment the reuse counter on a cache hit.

    Bayesian posterior fields are only updated when fresh external evidence
    arrives, not when a cached estimate is reused.
    """
    if profile.source in ("default", "manual"):
        return

    profile.observation_count = int(profile.observation_count or 0) + 1


def _activity_profile_source_for_cache(profile_source: str) -> str:
    """Map cache-entry source to the materialized activity_work_profiles source."""
    if profile_source == "manual":
        return "manual"
    if profile_source == "default":
        return "default"
    return "cache"


def _append_runtime_reason(runtime: Optional[WorkProfileRuntimeState], reason: str) -> None:
    if runtime is None:
        return
    if reason not in runtime.degraded_reasons:
        runtime.degraded_reasons.append(reason)


@lru_cache(maxsize=1)
def _work_profile_prompt_text() -> str:
    prompt_path = Path(__file__).parent / "prompts" / "work_profile_generation.txt"
    return prompt_path.read_text(encoding="utf-8").strip()


def _work_profile_response_max_tokens(duration_days: int) -> int:
    """
    Size the completion budget for one normalized bucket per activity day.

    The original fixed 768-token budget is enough for short activities but it
    truncates long responses once the model has to emit dozens or hundreds of
    numeric buckets. Keep the historical floor for short rows, then scale
    linearly with duration while capping spend for extremely long activities.
    """
    duration_days = max(1, int(duration_days or 1))
    estimated_tokens = WORK_PROFILE_BASE_TOKENS + (duration_days * WORK_PROFILE_TOKENS_PER_DAY)
    return max(AI_WORK_PROFILE_MAX_TOKENS, min(WORK_PROFILE_MAX_TOKENS_CAP, estimated_tokens))


def _posterior_hint_payload(profile: Optional[ItemContextProfile]) -> Optional[dict[str, float]]:
    if profile is None or profile.posterior_mean is None or profile.posterior_precision is None:
        return None
    if float(profile.posterior_mean) <= 0 or float(profile.posterior_precision) <= 0:
        return None
    return {
        "posterior_mean": float(profile.posterior_mean),
        "posterior_precision": float(profile.posterior_precision),
        "sample_count": float(profile.sample_count or 0),
        "confidence": float(profile.confidence or 0),
    }


def _stabilize_ai_confidence(
    raw_confidence: float,
    *,
    row_confidence: Optional[str],
    compressed_context: dict,
    posterior_hint: Optional[dict[str, float]],
) -> float:
    confidence = min(1.0, max(0.0, raw_confidence))
    unknown_count = sum(1 for value in compressed_context.values() if value == "unknown")

    if row_confidence == "low":
        confidence = min(confidence, 0.55)
    elif unknown_count >= 3 and posterior_hint is None:
        confidence = min(confidence, 0.60)
    elif unknown_count >= 2 and posterior_hint is None:
        confidence = min(confidence, 0.70)

    return round(confidence, 3)


def _write_work_profile_ai_log(
    db: Session,
    *,
    activity_id: uuid.UUID,
    item_id: uuid.UUID,
    context_hash: str,
    request_json: dict[str, object],
    response_json: Optional[dict[str, object]],
    validation_errors: Optional[list[str]],
    fallback_used: bool,
    retry_count: int,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    tokens_used: Optional[int],
    cost_usd: Decimal | None,
    latency_ms: Optional[int],
    model_name: str,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> WorkProfileAILog:
    log_row = WorkProfileAILog(
        activity_id=activity_id,
        item_id=item_id,
        context_hash=context_hash,
        inference_version=inference_version,
        model_name=model_name,
        request_json=request_json,
        response_json=response_json,
        validation_errors_json=validation_errors,
        fallback_used=fallback_used,
        retry_count=retry_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tokens_used=tokens_used,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )
    db.add(log_row)
    db.flush()
    return log_row


async def generate_work_profile_ai(
    *,
    activity_name: str,
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
    level_name: Optional[str] = None,
    zone_name: Optional[str] = None,
    row_confidence: Optional[str] = None,
    compressed_context: Optional[dict] = None,
    posterior_hint: Optional[dict[str, float]] = None,
    repair_errors: Optional[list[str]] = None,
    execution_context: AIExecutionContext | None = None,
) -> Optional[dict[str, object]]:
    execution_context = _resolve_ai_execution_context(execution_context)
    if duration_days <= 0:
        duration_days = 1

    compressed_context = compressed_context or build_compressed_context(
        activity_name,
        level_name=level_name,
        zone_name=zone_name,
    )
    deterministic_prior = _build_default_profile_prior(
        asset_type,
        duration_days,
        max_hours_per_day,
        compressed_context=compressed_context,
    )
    request_payload: dict[str, object] = {
        "activity_name": activity_name,
        "asset_type": asset_type,
        "duration_days": duration_days,
        "max_hours_per_day": max_hours_per_day,
        "level_name": level_name,
        "zone_name": zone_name,
        "row_confidence": row_confidence,
        "compressed_context": compressed_context,
        "deterministic_prior": deterministic_prior,
    }
    if posterior_hint is not None:
        request_payload["posterior_hint"] = posterior_hint
    if repair_errors:
        request_payload["repair_errors"] = repair_errors

    if asset_type == "none":
        usage = build_ai_usage(0, 0)
        return {
            "total_hours": 0.0,
            "normalized_distribution": [0.0] * duration_days,
            "confidence": 1.0,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "tokens_used": usage.total_tokens,
            "cost_usd": usage.cost_usd,
            "latency_ms": 0,
            "request_json": request_payload,
            "response_json": {
                "total_hours": 0.0,
                "normalized_distribution": [0.0] * duration_days,
                "confidence": 1.0,
            },
        }

    if not settings.AI_ENABLED or not settings.AI_API_KEY:
        return None
    if execution_context is not None and execution_context.suppress_ai:
        return None

    system_prompt = _work_profile_prompt_text()
    if repair_errors:
        system_prompt = (
            f"{system_prompt}\n\n"
            "Previous output failed validation. Repair the result strictly against these errors:\n"
            f"{json.dumps(repair_errors, sort_keys=True)}"
        )
    user_message = json.dumps(request_payload, sort_keys=True)
    response_max_tokens = _work_profile_response_max_tokens(duration_days)

    try:
        started = time.perf_counter()
        client = _get_async_client()
        text, usage = await _call_api(
            client,
            system_prompt,
            user_message,
            max_tokens=response_max_tokens,
            timeout=float(settings.AI_TIMEOUT_WORK_PROFILE),
            execution_context=execution_context,
        )
        usage = coerce_ai_usage(usage)
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = _parse_json_response(text)
    except Exception as exc:
        logger.warning("Work-profile AI generation failed: %s", exc)
        return None

    try:
        total_hours = float(data["total_hours"])
        raw_distribution = data["normalized_distribution"]
        if not isinstance(raw_distribution, list) or len(raw_distribution) != duration_days:
            raise ValueError(
                "normalized_distribution must be a list matching duration_days"
            )
        weights = [max(0.0, float(value)) for value in raw_distribution]
        normalized_distribution = (
            derive_normalized_distribution(weights)
            if sum(weights) > 0
            else [0.0] * duration_days
        )
        confidence = _stabilize_ai_confidence(
            float(data.get("confidence", 0.5)),
            row_confidence=row_confidence,
            compressed_context=compressed_context,
            posterior_hint=posterior_hint,
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Work-profile AI response was invalid: %s", exc)
        return None

    return {
        "total_hours": total_hours,
        "normalized_distribution": normalized_distribution,
        "confidence": confidence,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "tokens_used": usage.total_tokens,
        "cost_usd": usage.cost_usd,
        "latency_ms": latency_ms,
        "request_json": request_payload,
        "response_json": data,
    }


def _validate_ai_proposal(
    proposal: dict[str, object],
    *,
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
    trusted_baseline: Optional[float],
    manual_truth: Optional[float] = None,
) -> tuple[Optional[dict[str, object]], list[str]]:
    try:
        ai_total = float(proposal["total_hours"])
        normalized_distribution = [
            float(value) for value in list(proposal["normalized_distribution"])
        ]
        confidence = min(1.0, max(0.0, float(proposal.get("confidence", 0.5))))
    except (KeyError, TypeError, ValueError) as exc:
        return None, [f"invalid_ai_payload: {exc}"]

    final_hours = finalize_total_hours(
        ai_total,
        asset_type,
        duration_days,
        max_hours_per_day,
        trusted_baseline=trusted_baseline,
        manual_truth=manual_truth,
    )
    distribution = derive_distribution(
        normalized_distribution,
        final_hours,
        max_hours_per_day=max_hours_per_day,
    )

    stage_b = validate_stage_b(distribution, asset_type, max_hours_per_day, duration_days)
    stage_d = validate_stage_d(
        final_hours,
        distribution,
        normalized_distribution,
        asset_type,
        duration_days,
        max_hours_per_day,
    )
    errors = list(stage_b.errors) + [e for e in stage_d.errors if e not in stage_b.errors]
    if errors:
        return None, errors

    return {
        "final_hours": final_hours,
        "distribution": distribution,
        "normalized_distribution": normalized_distribution,
        "confidence": confidence,
        "raw_total_hours": ai_total,
        "input_tokens": int(proposal.get("input_tokens", 0) or 0),
        "output_tokens": int(proposal.get("output_tokens", 0) or 0),
        "tokens_used": int(proposal.get("tokens_used", 0) or 0),
        "cost_usd": proposal.get("cost_usd"),
        "latency_ms": proposal.get("latency_ms"),
        "request_json": proposal.get("request_json"),
        "response_json": proposal.get("response_json"),
    }, []


async def _request_validated_ai_proposal(
    db: Session,
    *,
    activity_id: uuid.UUID,
    item_id: uuid.UUID,
    context_hash: str,
    activity_name: str,
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
    compressed_context: dict,
    level_name: Optional[str],
    zone_name: Optional[str],
    row_confidence: Optional[str],
    posterior_hint: Optional[dict[str, float]],
    trusted_baseline: Optional[float],
    runtime: Optional[WorkProfileRuntimeState],
    execution_context: AIExecutionContext | None = None,
) -> Optional[dict[str, object]]:
    last_errors: list[str] = []
    last_request: dict[str, object] | None = None
    last_response: dict[str, object] | None = None
    last_input_tokens: int | None = None
    last_output_tokens: int | None = None
    last_tokens: int | None = None
    last_cost_usd: Decimal | None = None
    last_latency: int | None = None

    for attempt in range(2):
        proposal = await generate_work_profile_ai(
            activity_name=activity_name,
            asset_type=asset_type,
            duration_days=duration_days,
            max_hours_per_day=max_hours_per_day,
            level_name=level_name,
            zone_name=zone_name,
            row_confidence=row_confidence,
            compressed_context=compressed_context,
            posterior_hint=posterior_hint,
            repair_errors=last_errors or None,
            execution_context=execution_context,
        )
        if proposal is None:
            last_errors = ["ai_unavailable_or_invalid_response"]
            continue

        if runtime is not None:
            runtime.ai_attempts += 1
            runtime.ai_tokens_used += int(proposal.get("tokens_used", 0) or 0)
            runtime.ai_cost_usd = sum_ai_costs(runtime.ai_cost_usd, proposal.get("cost_usd"))

        last_request = proposal.get("request_json") if isinstance(proposal.get("request_json"), dict) else {}
        last_response = proposal.get("response_json") if isinstance(proposal.get("response_json"), dict) else {}
        last_input_tokens = int(proposal.get("input_tokens", 0) or 0)
        last_output_tokens = int(proposal.get("output_tokens", 0) or 0)
        last_tokens = int(proposal.get("tokens_used", 0) or 0)
        last_cost_usd = proposal.get("cost_usd")
        last_latency = int(proposal.get("latency_ms", 0) or 0)

        validated, errors = _validate_ai_proposal(
            proposal,
            asset_type=asset_type,
            duration_days=duration_days,
            max_hours_per_day=max_hours_per_day,
            trusted_baseline=trusted_baseline,
        )
        if validated is not None:
            _write_work_profile_ai_log(
                db,
                activity_id=activity_id,
                item_id=item_id,
                context_hash=context_hash,
                request_json=last_request or {},
                response_json=last_response,
                validation_errors=None,
                fallback_used=False,
                retry_count=attempt,
                input_tokens=last_input_tokens,
                output_tokens=last_output_tokens,
                tokens_used=last_tokens,
                cost_usd=last_cost_usd,
                latency_ms=last_latency,
                model_name=settings.AI_MODEL,
            )
            return validated
        last_errors = errors

    if runtime is not None:
        runtime.validation_failures += 1
    _write_work_profile_ai_log(
        db,
        activity_id=activity_id,
        item_id=item_id,
        context_hash=context_hash,
        request_json=last_request or {},
        response_json=last_response,
        validation_errors=last_errors or ["ai_fallback_used"],
        fallback_used=True,
        retry_count=1 if last_errors else 0,
        input_tokens=last_input_tokens,
        output_tokens=last_output_tokens,
        tokens_used=last_tokens,
        cost_usd=last_cost_usd,
        latency_ms=last_latency,
        model_name=settings.AI_MODEL,
    )
    return None


def _request_validated_ai_proposal_sync(**kwargs: object) -> Optional[dict[str, object]]:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        raise RuntimeError(
            "resolve_work_profile cannot use _request_validated_ai_proposal_sync while an event loop is running. "
            "From async code, call _request_validated_ai_proposal directly and pass the result into "
            "resolve_work_profile via precomputed_ai_proposal, or run the sync wrapper from a separate thread/event loop."
        )
    return asyncio.run(_request_validated_ai_proposal(**kwargs))


async def _precompute_validated_ai_proposal(
    *,
    activity_name: str,
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
    compressed_context: dict,
    level_name: Optional[str],
    zone_name: Optional[str],
    row_confidence: Optional[str],
    posterior_hint: Optional[dict[str, float]],
    trusted_baseline: Optional[float],
    execution_context: AIExecutionContext | None = None,
) -> WorkProfileAIOutcome:
    last_errors: list[str] = []
    last_request: dict[str, object] = {}
    last_response: dict[str, object] | None = None
    last_input_tokens: int | None = None
    last_output_tokens: int | None = None
    last_tokens: int | None = None
    last_cost_usd: Decimal | None = None
    last_latency: int | None = None
    total_tokens = 0
    total_cost_usd: Decimal | None = None
    ai_attempts = 0

    for attempt in range(2):
        ai_attempts += 1
        proposal = await generate_work_profile_ai(
            activity_name=activity_name,
            asset_type=asset_type,
            duration_days=duration_days,
            max_hours_per_day=max_hours_per_day,
            level_name=level_name,
            zone_name=zone_name,
            row_confidence=row_confidence,
            compressed_context=compressed_context,
            posterior_hint=posterior_hint,
            repair_errors=last_errors or None,
            execution_context=execution_context,
        )
        if proposal is None:
            last_errors = ["ai_unavailable_or_invalid_response"]
            continue

        current_tokens = int(proposal.get("tokens_used", 0) or 0)
        total_tokens += current_tokens
        total_cost_usd = sum_ai_costs(total_cost_usd, proposal.get("cost_usd"))
        last_request = proposal.get("request_json") if isinstance(proposal.get("request_json"), dict) else {}
        last_response = proposal.get("response_json") if isinstance(proposal.get("response_json"), dict) else {}
        last_input_tokens = int(proposal.get("input_tokens", 0) or 0)
        last_output_tokens = int(proposal.get("output_tokens", 0) or 0)
        last_tokens = current_tokens
        last_cost_usd = proposal.get("cost_usd")
        last_latency = int(proposal.get("latency_ms", 0) or 0)

        validated, errors = _validate_ai_proposal(
            proposal,
            asset_type=asset_type,
            duration_days=duration_days,
            max_hours_per_day=max_hours_per_day,
            trusted_baseline=trusted_baseline,
        )
        if validated is not None:
            return WorkProfileAIOutcome(
                proposal=validated,
                attempted_ai=True,
                ai_attempts=ai_attempts,
                ai_tokens_used=total_tokens,
                ai_cost_usd=total_cost_usd,
                validation_failures=0,
                request_json=last_request,
                response_json=last_response,
                validation_errors=None,
                fallback_used=False,
                retry_count=attempt,
                log_input_tokens_used=last_input_tokens,
                log_output_tokens_used=last_output_tokens,
                log_tokens_used=last_tokens,
                log_cost_usd=last_cost_usd,
                latency_ms=last_latency,
            )
        last_errors = errors

    return WorkProfileAIOutcome(
        proposal=None,
        attempted_ai=True,
        ai_attempts=ai_attempts,
        ai_tokens_used=total_tokens,
        ai_cost_usd=total_cost_usd,
        validation_failures=1,
        request_json=last_request,
        response_json=last_response,
        validation_errors=last_errors or ["ai_fallback_used"],
        fallback_used=True,
        retry_count=1 if last_errors else 0,
        log_input_tokens_used=last_input_tokens,
        log_output_tokens_used=last_output_tokens,
        log_tokens_used=last_tokens,
        log_cost_usd=last_cost_usd,
        latency_ms=last_latency,
    )


# ---------------------------------------------------------------------------
# 12. ActivityWorkProfile writer
# ---------------------------------------------------------------------------

def _write_activity_profile(
    db: Session,
    activity_id: uuid.UUID,
    activity_asset_mapping_id: uuid.UUID | None,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    total_hours: float,
    distribution: list[float],
    normalized_distribution: list[float],
    confidence: float,
    source: str,
    context_hash: str,
    context_profile_id: Optional[uuid.UUID] = None,
    low_confidence_flag: bool = False,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> ActivityWorkProfile:
    awp = (
        db.query(ActivityWorkProfile)
        .filter(
            ActivityWorkProfile.activity_asset_mapping_id == activity_asset_mapping_id
            if activity_asset_mapping_id is not None
            else ActivityWorkProfile.activity_id == activity_id
        )
        .one_or_none()
    )
    if awp is None:
        savepoint = db.begin_nested()
        try:
            awp = ActivityWorkProfile(
                activity_id=activity_id,
                activity_asset_mapping_id=activity_asset_mapping_id,
                item_id=item_id,
                asset_type=asset_type,
                duration_days=duration_days,
                context_version=context_version,
                inference_version=inference_version,
                total_hours=total_hours,
                distribution_json=distribution,
                normalized_distribution_json=normalized_distribution,
                confidence=confidence,
                low_confidence_flag=low_confidence_flag,
                source=source,
                context_hash=context_hash,
                context_profile_id=context_profile_id,
            )
            db.add(awp)
            db.flush()
            savepoint.commit()
            return awp
        except IntegrityError:
            savepoint.rollback()
            awp = (
                db.query(ActivityWorkProfile)
                .filter(
                    ActivityWorkProfile.activity_asset_mapping_id == activity_asset_mapping_id
                    if activity_asset_mapping_id is not None
                    else ActivityWorkProfile.activity_id == activity_id
                )
                .one_or_none()
            )
            if awp is None:
                raise

    awp.item_id = item_id
    awp.activity_asset_mapping_id = activity_asset_mapping_id
    awp.asset_type = asset_type
    awp.duration_days = duration_days
    awp.context_version = context_version
    awp.inference_version = inference_version
    awp.total_hours = total_hours
    awp.distribution_json = distribution
    awp.normalized_distribution_json = normalized_distribution
    awp.confidence = confidence
    awp.low_confidence_flag = low_confidence_flag
    awp.source = source
    awp.context_hash = context_hash
    awp.context_profile_id = context_profile_id
    db.flush()
    return awp


def prepare_manual_work_profile(
    *,
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
    manual_total_hours: float | None = None,
    manual_normalized_distribution: list[float] | None = None,
    existing_total_hours: float | None = None,
    existing_distribution: list[float] | None = None,
    existing_normalized_distribution: list[float] | None = None,
) -> PreparedManualWorkProfile:
    duration_days = max(1, int(duration_days or 1))

    if asset_type == "none":
        return PreparedManualWorkProfile(
            total_hours=0.0,
            distribution=[0.0] * duration_days,
            normalized_distribution=[0.0] * duration_days,
            max_hours_per_day=max_hours_per_day,
        )

    if manual_total_hours is not None:
        seed_total_hours = float(manual_total_hours)
    elif existing_total_hours is not None:
        seed_total_hours = float(existing_total_hours)
    else:
        raise ValueError("Manual work-profile preparation requires manual input or an existing profile")

    if (
        manual_total_hours is None
        and existing_total_hours is None
        and manual_normalized_distribution is None
        and existing_normalized_distribution is None
        and existing_distribution is None
    ):
        raise ValueError("Manual work-profile preparation requires manual input or an existing profile")

    normalized_distribution = _coerce_manual_distribution(
        duration_days=duration_days,
        normalized_distribution=(
            manual_normalized_distribution
            if manual_normalized_distribution is not None
            else existing_normalized_distribution
        ),
        distribution=existing_distribution,
    )

    bounded_seed_total_hours = max(
        0.0,
        min(float(seed_total_hours), max_hours_per_day * duration_days),
    )

    final_hours = finalize_total_hours(
        bounded_seed_total_hours,
        asset_type,
        duration_days,
        max_hours_per_day,
        manual_truth=bounded_seed_total_hours,
    )
    if final_hours <= 0:
        normalized_distribution = [0.0] * duration_days

    distribution = derive_distribution(
        normalized_distribution,
        final_hours,
        max_hours_per_day=max_hours_per_day,
    )
    validation = validate_stage_d(
        final_hours,
        distribution,
        normalized_distribution,
        asset_type,
        duration_days,
        max_hours_per_day,
    )
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))

    return PreparedManualWorkProfile(
        total_hours=final_hours,
        distribution=distribution,
        normalized_distribution=normalized_distribution,
        max_hours_per_day=max_hours_per_day,
    )


def _apply_manual_cache_values(
    profile: ItemContextProfile,
    *,
    total_hours: float,
    distribution: list[float],
    normalized_distribution: list[float],
) -> ItemContextProfile:
    _assert_profile_mutable(profile)
    previous_observation_count = int(profile.observation_count or 0)
    previous_evidence_weight = float(profile.evidence_weight or 0)
    previous_sample_count = int(profile.sample_count or 0)

    profile.total_hours = total_hours
    profile.distribution_json = distribution
    profile.normalized_distribution_json = normalized_distribution
    profile.confidence = 1.0
    profile.source = "manual"
    profile.low_confidence_flag = False
    profile.observation_count = max(previous_observation_count, 0) + 1
    profile.evidence_weight = round(previous_evidence_weight + 1.0, 4)
    profile.sample_count = max(previous_sample_count, 0) + 1
    profile.posterior_mean = total_hours if total_hours > 0 else None
    profile.posterior_precision = (
        _obs_precision(total_hours, "manual") if total_hours > 0 else None
    )
    return profile


def upsert_manual_context_profile(
    db: Session,
    *,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    compressed_context: dict,
    total_hours: float,
    distribution: list[float],
    normalized_distribution: list[float],
    context_hash: str | None = None,
    sync_global: bool = True,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> ItemContextProfile:
    context_hash = context_hash or build_context_key(
        item_id,
        asset_type,
        duration_days,
        compressed_context,
        context_version,
        inference_version,
    )
    existing = lookup_cache(
        db,
        project_id,
        item_id,
        asset_type,
        duration_days,
        context_hash,
        context_version,
        inference_version,
    )

    if existing is None:
        savepoint = db.begin_nested()
        try:
            existing = ItemContextProfile(
                project_id=project_id,
                item_id=item_id,
                asset_type=asset_type,
                duration_days=duration_days,
                context_version=context_version,
                inference_version=inference_version,
                context_hash=context_hash,
                total_hours=total_hours,
                distribution_json=distribution,
                normalized_distribution_json=normalized_distribution,
                confidence=1.0,
                source="manual",
                low_confidence_flag=False,
                observation_count=0,
                evidence_weight=0,
                posterior_mean=None,
                posterior_precision=None,
                sample_count=0,
                correction_count=0,
                actuals_count=int(0),
            )
            db.add(existing)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = lookup_cache(
                db,
                project_id,
                item_id,
                asset_type,
                duration_days,
                context_hash,
                context_version,
                inference_version,
            )
            if existing is None:
                raise

    _apply_manual_cache_values(
        existing,
        total_hours=total_hours,
        distribution=distribution,
        normalized_distribution=normalized_distribution,
    )
    db.flush()
    if sync_global:
        rebuild_global_knowledge_entry(
            db,
            item_id=item_id,
            asset_type=asset_type,
            duration_bucket=duration_bucket_for_days(duration_days),
            context_version=context_version,
            inference_version=inference_version,
        )
    return existing


def write_manual_activity_profile(
    db: Session,
    *,
    activity_id: uuid.UUID,
    activity_asset_mapping_id: uuid.UUID | None = None,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    total_hours: float,
    distribution: list[float],
    normalized_distribution: list[float],
    context_hash: str,
    context_profile_id: uuid.UUID | None = None,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> ActivityWorkProfile:
    return _write_activity_profile(
        db,
        activity_id=activity_id,
        activity_asset_mapping_id=activity_asset_mapping_id,
        item_id=item_id,
        asset_type=asset_type,
        duration_days=duration_days,
        total_hours=total_hours,
        distribution=distribution,
        normalized_distribution=normalized_distribution,
        confidence=1.0,
        source="manual",
        context_hash=context_hash,
        context_profile_id=context_profile_id,
        low_confidence_flag=False,
        context_version=context_version,
        inference_version=inference_version,
    )


_CONTEXT_PROFILE_SOURCE_PRIORITY: dict[str, int] = {
    "manual": 4,
    "learned": 3,
    "ai": 2,
    "default": 1,
}


def _normalise_compressed_context(compressed_context: dict | None) -> dict[str, str] | None:
    if not isinstance(compressed_context, dict):
        return None
    return {
        "phase": str(compressed_context.get("phase") or "unknown"),
        "spatial_type": str(compressed_context.get("spatial_type") or "unknown"),
        "area_type": str(compressed_context.get("area_type") or "unknown"),
        "work_type": str(compressed_context.get("work_type") or "unknown"),
    }


def _resolve_context_profile_compressed_context(
    db: Session,
    profile: ItemContextProfile,
) -> dict[str, str] | None:
    explicit_context = _normalise_compressed_context(getattr(profile, "compressed_context", None))
    if explicit_context is not None:
        return explicit_context

    activity_row = (
        db.query(ProgrammeActivity)
        .join(ActivityWorkProfile, ActivityWorkProfile.activity_id == ProgrammeActivity.id)
        .filter(ActivityWorkProfile.context_profile_id == profile.id)
        .order_by(ActivityWorkProfile.created_at.desc())
        .first()
    )
    if activity_row is not None:
        return build_compressed_context(
            activity_row.name or "",
            activity_row.level_name,
            activity_row.zone_name,
        )

    ai_log = (
        db.query(WorkProfileAILog)
        .filter(
            WorkProfileAILog.item_id == profile.item_id,
            WorkProfileAILog.context_hash == profile.context_hash,
            WorkProfileAILog.inference_version == profile.inference_version,
        )
        .order_by(WorkProfileAILog.created_at.desc())
        .first()
    )
    if ai_log is not None:
        request_json = ai_log.request_json if isinstance(ai_log.request_json, dict) else {}
        return _normalise_compressed_context(request_json.get("compressed_context"))

    return None


def _context_profile_merge_key(
    db: Session,
    profile: ItemContextProfile,
) -> tuple[str, str, int, int, int, str, str, str, str]:
    compressed_context = _resolve_context_profile_compressed_context(db, profile)
    if compressed_context is not None:
        return (
            str(profile.project_id),
            str(profile.asset_type),
            int(profile.duration_days or 0),
            int(profile.context_version or 0),
            int(profile.inference_version or 0),
            compressed_context["phase"],
            compressed_context["spatial_type"],
            compressed_context["area_type"],
            compressed_context["work_type"],
        )
    return (
        str(profile.project_id),
        str(profile.asset_type),
        int(profile.duration_days or 0),
        int(profile.context_version or 0),
        int(profile.inference_version or 0),
        "__hash__",
        str(profile.context_hash or ""),
        "",
        "",
    )


def _context_profile_rank(profile: ItemContextProfile) -> tuple[int, float, float, int, datetime, str]:
    updated_at = profile.updated_at or profile.created_at or datetime.min.replace(tzinfo=timezone.utc)
    return (
        _CONTEXT_PROFILE_SOURCE_PRIORITY.get(str(profile.source or ""), 0),
        float(profile.confidence or 0),
        float(profile.evidence_weight or 0),
        int(profile.observation_count or 0),
        updated_at,
        str(profile.id),
    )


def _copy_context_profile_payload(
    target: ItemContextProfile,
    source: ItemContextProfile,
) -> ItemContextProfile:
    _assert_profile_mutable(target)
    target.asset_type = source.asset_type
    target.duration_days = source.duration_days
    target.context_version = source.context_version
    target.inference_version = source.inference_version
    target.total_hours = source.total_hours
    target.distribution_json = list(source.distribution_json or [])
    target.normalized_distribution_json = list(source.normalized_distribution_json or [])
    target.confidence = source.confidence
    target.source = source.source
    target.low_confidence_flag = bool(source.low_confidence_flag)
    target.posterior_mean = source.posterior_mean
    target.posterior_precision = source.posterior_precision
    return target


def _merge_context_profile_counters(
    winner: ItemContextProfile,
    loser: ItemContextProfile,
) -> ItemContextProfile:
    _assert_profile_mutable(winner)
    original_winner_actuals_count = int(winner.actuals_count or 0)
    winner.observation_count = int(winner.observation_count or 0) + int(loser.observation_count or 0)
    winner.evidence_weight = float(winner.evidence_weight or 0) + float(loser.evidence_weight or 0)
    winner.sample_count = int(winner.sample_count or 0) + int(loser.sample_count or 0)
    winner.correction_count = int(winner.correction_count or 0) + int(loser.correction_count or 0)
    winner.actuals_count = int(winner.actuals_count or 0) + int(loser.actuals_count or 0)
    # Combined counters invalidate the previous posterior unless we recompute it from the merged evidence.
    winner.posterior_mean = None
    winner.posterior_precision = None
    if loser.actuals_median is not None and (
        winner.actuals_median is None or int(loser.actuals_count or 0) > original_winner_actuals_count
    ):
        winner.actuals_median = loser.actuals_median
    return winner


def _rebuild_context_profile_hash(
    db: Session,
    profile: ItemContextProfile,
    item_id: uuid.UUID,
) -> str:
    compressed_context = _resolve_context_profile_compressed_context(db, profile)
    if compressed_context is None:
        raise RuntimeError(
            f"Unable to reconstruct compressed context for context profile {getattr(profile, 'id', 'unknown')}"
        )
    return build_context_key(
        item_id,
        str(profile.asset_type),
        int(profile.duration_days or 0),
        compressed_context,
        context_version=int(profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
        inference_version=int(profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
    )


def reconcile_context_profiles_on_merge(
    db: Session,
    source_item_id: uuid.UUID,
    target_item_id: uuid.UUID,
) -> None:
    source_profiles = (
        active_context_profile_query(db)
        .filter(ItemContextProfile.item_id == source_item_id)
        .with_for_update()
        .all()
    )
    if not source_profiles:
        return

    target_profiles = (
        active_context_profile_query(db)
        .filter(ItemContextProfile.item_id == target_item_id)
        .with_for_update()
        .all()
    )
    target_by_key = {
        _context_profile_merge_key(db, profile): profile for profile in target_profiles
    }

    for source_profile in source_profiles:
        key = _context_profile_merge_key(db, source_profile)
        target_profile = target_by_key.get(key)
        if target_profile is None:
            source_profile.item_id = target_item_id
            source_profile.context_hash = _rebuild_context_profile_hash(db, source_profile, target_item_id)
            target_by_key[_context_profile_merge_key(db, source_profile)] = source_profile
            continue

        if _context_profile_rank(source_profile) > _context_profile_rank(target_profile):
            _copy_context_profile_payload(target_profile, source_profile)
            target_profile.context_hash = _rebuild_context_profile_hash(db, source_profile, target_item_id)
        else:
            target_profile.context_hash = _rebuild_context_profile_hash(db, target_profile, target_item_id)
        _merge_context_profile_counters(target_profile, source_profile)
        (
            db.query(ActivityWorkProfile)
            .filter(ActivityWorkProfile.context_profile_id == source_profile.id)
            .update({"context_profile_id": target_profile.id}, synchronize_session=False)
        )
        invalidate_context_profile(
            source_profile,
            reason="item_merge_conflict",
            superseded_by_profile_id=target_profile.id,
        )

    db.flush()


# ---------------------------------------------------------------------------
# 12.5 Stage 10 global knowledge + actuals
# ---------------------------------------------------------------------------

def _has_valid_posterior(profile: ItemContextProfile) -> bool:
    return bool(
        profile.posterior_mean is not None
        and profile.posterior_precision is not None
        and float(profile.posterior_mean) > 0
        and float(profile.posterior_precision) > 0
    )


def _eligible_local_profiles_for_global_entry(
    db: Session,
    *,
    item_id: uuid.UUID,
    asset_type: str,
    duration_bucket: int,
    context_version: int,
    inference_version: int,
) -> list[ItemContextProfile]:
    rows = (
        active_context_profile_query(db)
        .filter(
            ItemContextProfile.item_id == item_id,
            ItemContextProfile.asset_type == asset_type,
            ItemContextProfile.context_version == context_version,
            ItemContextProfile.inference_version == inference_version,
            ItemContextProfile.project_id.isnot(None),
            ItemContextProfile.source.in_(["ai", "learned"]),
            ItemContextProfile.low_confidence_flag.is_(False),
            ItemContextProfile.sample_count >= WORK_PROFILE_CORRECTION_MIN_SAMPLES,
        )
        .all()
    )
    return [
        row
        for row in rows
        if duration_bucket_for_days(int(row.duration_days or 0)) == duration_bucket
        and _has_valid_posterior(row)
        and str(row.asset_type) not in {"other", "none"}
    ]


def get_global_knowledge_entry(
    db: Session,
    *,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
    ) -> ItemKnowledgeBase | None:
    if asset_type in {"other", "none"}:
        return None
    if not _project_has_asset_type(db, project_id, asset_type):
        return None
    duration_bucket = duration_bucket_for_days(duration_days)
    return (
        _query_item_knowledge_entry(
            db,
            item_id=item_id,
            asset_type=asset_type,
            duration_bucket=duration_bucket,
            context_version=context_version,
            inference_version=inference_version,
        )
    )


def _query_item_knowledge_entry(
    db: Session,
    *,
    item_id: uuid.UUID,
    asset_type: str,
    duration_bucket: int,
    context_version: int,
    inference_version: int,
) -> ItemKnowledgeBase | None:
    return (
        db.query(ItemKnowledgeBase)
        .filter(
            ItemKnowledgeBase.item_id == item_id,
            ItemKnowledgeBase.asset_type == asset_type,
            ItemKnowledgeBase.duration_bucket == duration_bucket,
            ItemKnowledgeBase.context_version == context_version,
            ItemKnowledgeBase.inference_version == inference_version,
        )
        .one_or_none()
    )


def _apply_item_knowledge_payload(
    knowledge: ItemKnowledgeBase,
    *,
    normalized_shape: list[float],
    confidence_tier: str,
    posterior_mean: float,
    posterior_precision: float,
    source_project_count: int,
    sample_count: int,
    correction_count: int,
    actuals_shape_json: list[float] | None = None,
    actuals_shape_weight: float = 0.0,
) -> ItemKnowledgeBase:
    knowledge.posterior_mean = posterior_mean
    knowledge.posterior_precision = posterior_precision
    knowledge.source_project_count = source_project_count
    knowledge.sample_count = sample_count
    knowledge.correction_count = correction_count
    knowledge.normalized_shape_json = normalized_shape
    knowledge.confidence_tier = confidence_tier
    knowledge.actuals_shape_json = actuals_shape_json
    knowledge.actuals_shape_weight = round(actuals_shape_weight, 4)
    return knowledge


def rebuild_global_knowledge_entry(
    db: Session,
    *,
    item_id: uuid.UUID,
    asset_type: str,
    duration_bucket: int,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> ItemKnowledgeBase | None:
    existing = _query_item_knowledge_entry(
        db,
        item_id=item_id,
        asset_type=asset_type,
        duration_bucket=duration_bucket,
        context_version=context_version,
        inference_version=inference_version,
    )

    rows = _eligible_local_profiles_for_global_entry(
        db,
        item_id=item_id,
        asset_type=asset_type,
        duration_bucket=duration_bucket,
        context_version=context_version,
        inference_version=inference_version,
    )
    if not rows:
        if existing is not None:
            db.delete(existing)
            db.flush()
        return None

    combined_mean: float | None = None
    combined_precision: float | None = None
    sample_count = 0
    correction_count = 0
    contributing_projects: set[uuid.UUID] = set()
    representative_days = duration_bucket
    shape_accumulator = [0.0] * representative_days
    shape_weight_total = 0.0
    actuals_shape_accumulator = [0.0] * representative_days
    actuals_shape_weight_total = 0.0

    for row in rows:
        row_mean = float(row.posterior_mean)
        row_precision = float(row.posterior_precision)
        if combined_mean is None or combined_precision is None:
            combined_mean = row_mean
            combined_precision = row_precision
        else:
            combined_mean, combined_precision = bayesian_update(
                combined_mean,
                combined_precision,
                row_mean,
                row_precision,
            )
        sample_count += int(row.sample_count or 0)
        correction_count += int(row.correction_count or 0)
        contributing_projects.add(row.project_id)

        weight = float(max(int(row.sample_count or 0), 1))
        resampled_shape = _resample_normalized_distribution(
            list(row.normalized_distribution_json or []),
            representative_days,
        )
        for idx, value in enumerate(resampled_shape):
            shape_accumulator[idx] += value * weight
        shape_weight_total += weight

        # Stage 11 — aggregate actuals shape
        row_actuals_shape = getattr(row, "actuals_shape_json", None)
        row_actuals_count = int(getattr(row, "actuals_count", 0) or 0)
        if row_actuals_shape and row_actuals_count > 0:
            resampled_actuals = _resample_normalized_distribution(
                list(row_actuals_shape), representative_days
            )
            for idx, v in enumerate(resampled_actuals):
                actuals_shape_accumulator[idx] += v * row_actuals_count
            actuals_shape_weight_total += row_actuals_count

    if (
        combined_mean is None
        or combined_precision is None
        or combined_mean <= 0
        or combined_precision <= 0
    ):
        if existing is not None:
            db.delete(existing)
            db.flush()
        return None

    confidence_tier = _global_confidence_tier(
        posterior_mean=combined_mean,
        posterior_precision=combined_precision,
        source_project_count=len(contributing_projects),
        sample_count=sample_count,
        correction_count=correction_count,
    )
    if confidence_tier is None:
        if existing is not None:
            db.delete(existing)
            db.flush()
        return None

    normalized_shape = derive_normalized_distribution(
        [
            (value / shape_weight_total) if shape_weight_total > 0 else 0.0
            for value in shape_accumulator
        ]
    )

    # Stage 11 — actuals-informed shape
    actuals_shape: list[float] | None = None
    if actuals_shape_weight_total > 0:
        actuals_shape = derive_normalized_distribution(
            [v / actuals_shape_weight_total for v in actuals_shape_accumulator]
        )

    knowledge = existing
    if knowledge is None:
        savepoint = db.begin_nested()
        try:
            knowledge = ItemKnowledgeBase(
                item_id=item_id,
                asset_type=asset_type,
                duration_bucket=duration_bucket,
                context_version=context_version,
                inference_version=inference_version,
                normalized_shape_json=normalized_shape,
                confidence_tier=confidence_tier,
                posterior_mean=combined_mean,
                posterior_precision=combined_precision,
                source_project_count=len(contributing_projects),
                sample_count=sample_count,
                correction_count=correction_count,
                actuals_shape_json=actuals_shape,
                actuals_shape_weight=round(actuals_shape_weight_total, 4),
            )
            db.add(knowledge)
            db.flush()
            savepoint.commit()
            return knowledge
        except IntegrityError:
            savepoint.rollback()
            knowledge = _query_item_knowledge_entry(
                db,
                item_id=item_id,
                asset_type=asset_type,
                duration_bucket=duration_bucket,
                context_version=context_version,
                inference_version=inference_version,
            )
            if knowledge is None:
                raise

    _apply_item_knowledge_payload(
        knowledge,
        normalized_shape=normalized_shape,
        confidence_tier=confidence_tier,
        posterior_mean=combined_mean,
        posterior_precision=combined_precision,
        source_project_count=len(contributing_projects),
        sample_count=sample_count,
        correction_count=correction_count,
        actuals_shape_json=actuals_shape,
        actuals_shape_weight=actuals_shape_weight_total,
    )

    db.flush()
    return knowledge


def rebuild_global_knowledge_for_item(
    db: Session,
    item_id: uuid.UUID,
) -> None:
    existing_keys = {
        (
            row.asset_type,
            int(row.duration_bucket or 0),
            int(row.context_version or 0),
            int(row.inference_version or 0),
        )
        for row in db.query(ItemKnowledgeBase)
        .filter(ItemKnowledgeBase.item_id == item_id)
        .all()
    }
    local_keys = {
        (
            row.asset_type,
            duration_bucket_for_days(int(row.duration_days or 0)),
            int(row.context_version or 0),
            int(row.inference_version or 0),
        )
        for row in active_context_profile_query(db)
        .filter(
            ItemContextProfile.item_id == item_id,
            ItemContextProfile.project_id.isnot(None),
        )
        .all()
    }
    for asset_type, duration_bucket, context_version, inference_version in sorted(existing_keys | local_keys):
        rebuild_global_knowledge_entry(
            db,
            item_id=item_id,
            asset_type=asset_type,
            duration_bucket=duration_bucket,
            context_version=context_version,
            inference_version=inference_version,
        )


def rebuild_all_global_knowledge(db: Session) -> None:
    item_ids = {
        row[0]
        for row in active_context_profile_query(db)
        .with_entities(ItemContextProfile.item_id)
        .filter(ItemContextProfile.project_id.isnot(None))
        .distinct()
        .all()
    }
    for item_id in sorted(item_ids, key=str):
        rebuild_global_knowledge_for_item(db, item_id)


def _seed_local_cache_from_global_knowledge(
    db: Session,
    *,
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    context_hash: str,
    max_hours_per_day: float,
    compressed_context: dict,
    global_knowledge: ItemKnowledgeBase,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
) -> ItemContextProfile:
    existing = lookup_cache(
        db,
        project_id,
        item_id,
        asset_type,
        duration_days,
        context_hash,
        context_version,
        inference_version,
    )
    if existing is not None:
        return existing

    learned_shape = list(getattr(global_knowledge, "normalized_shape_json", []) or [])
    if learned_shape:
        normalized_distribution = _resample_normalized_distribution(
            learned_shape,
            duration_days,
        )
    else:
        normalized_distribution = list(
            _build_default_profile_prior(
                asset_type,
                duration_days,
                max_hours_per_day,
                compressed_context=compressed_context,
            )["normalized_distribution"]
        )
    total_hours = quantize_hours(float(global_knowledge.posterior_mean))
    distribution = derive_distribution(
        normalized_distribution,
        total_hours,
        max_hours_per_day=max_hours_per_day,
    )
    seeded = ItemContextProfile(
        project_id=project_id,
        item_id=item_id,
        asset_type=asset_type,
        duration_days=duration_days,
        context_version=context_version,
        inference_version=inference_version,
        context_hash=context_hash,
        total_hours=total_hours,
        distribution_json=distribution,
        normalized_distribution_json=normalized_distribution,
        confidence=0.9,
        source="learned",
        low_confidence_flag=False,
        observation_count=0,
        evidence_weight=0,
        posterior_mean=float(global_knowledge.posterior_mean),
        posterior_precision=float(global_knowledge.posterior_precision),
        sample_count=0,
        correction_count=0,
        actuals_count=0,
    )
    savepoint = db.begin_nested()
    db.add(seeded)
    try:
        db.flush()
        savepoint.commit()
        return seeded
    except IntegrityError:
        savepoint.rollback()
        existing = lookup_cache(
            db,
            project_id,
            item_id,
            asset_type,
            duration_days,
            context_hash,
            context_version,
            inference_version,
        )
        if existing is None:
            raise
        return existing

def _median_of_values(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _replace_first_value(values: list[float], old_value: float, new_value: float) -> list[float]:
    updated = list(values)
    for index, value in enumerate(updated):
        if value == old_value:
            updated[index] = new_value
            return updated
    updated.append(new_value)
    return updated


def _reverse_actual_observations_from_posterior(
    context_profile: ItemContextProfile,
    previous_actual_values: list[float],
) -> tuple[float | None, float | None]:
    if not _has_valid_posterior(context_profile):
        return None, None

    precision = float(context_profile.posterior_precision)
    weighted_sum = float(context_profile.posterior_mean) * precision
    for actual_value in previous_actual_values:
        obs_precision = _obs_precision(float(actual_value), "actual")
        precision -= obs_precision
        weighted_sum -= obs_precision * float(actual_value)

    if precision <= 0:
        return None, None

    mean = weighted_sum / precision
    if mean <= 0:
        return None, None
    return mean, precision


def _apply_actual_observations_to_posterior(
    base_mean: float | None,
    base_precision: float | None,
    actual_values: list[float],
) -> tuple[float | None, float | None]:
    ordered_values = [float(value) for value in actual_values]
    if not ordered_values:
        return base_mean, base_precision

    if base_mean is not None and base_precision is not None and base_precision > 0:
        mean = float(base_mean)
        precision = float(base_precision)
        start_index = 0
    else:
        mean, precision = _initial_posterior(float(ordered_values[0]), 0.95)
        start_index = 1

    for value in ordered_values[start_index:]:
        mean, precision = bayesian_update(
            mean,
            precision,
            float(value),
            _obs_precision(float(value), "actual"),
        )
    return mean, precision


def record_actual_hours(
    db: Session,
    *,
    activity_work_profile_id: uuid.UUID,
    actual_hours_used: float,
    source: str = "system",
    recorded_by_user_id: uuid.UUID | None = None,
    booking_group_id: uuid.UUID | None = None,
    booking_id: uuid.UUID | None = None,
) -> AssetUsageActual:
    profile = (
        db.query(ActivityWorkProfile)
        .filter(ActivityWorkProfile.id == activity_work_profile_id)
        .one_or_none()
    )
    if profile is None:
        raise LookupError(f"Activity work profile {activity_work_profile_id} not found")
    if actual_hours_used < 0:
        raise ValueError("actual_hours_used must be non-negative")

    actual = (
        db.query(AssetUsageActual)
        .filter(AssetUsageActual.activity_work_profile_id == activity_work_profile_id)
        .one_or_none()
    )
    created_actual = actual is None
    previous_actual_hours = (
        float(actual.actual_hours_used)
        if actual is not None and actual.actual_hours_used is not None
        else None
    )
    previous_source = str(actual.source) if actual is not None and actual.source is not None else None
    if actual is None:
        actual = AssetUsageActual(
            activity_work_profile_id=activity_work_profile_id,
            actual_hours_used=actual_hours_used,
            source=source,
            recorded_by_user_id=recorded_by_user_id,
            booking_group_id=booking_group_id,
            booking_id=booking_id,
        )
        db.add(actual)
    else:
        actual.actual_hours_used = actual_hours_used
        actual.source = source
        actual.recorded_by_user_id = recorded_by_user_id
        actual.booking_group_id = booking_group_id
        actual.booking_id = booking_id

    context_profile = None
    previous_context_actual_values: list[float] = []
    if profile.context_profile_id is not None:
        context_profile = (
            db.query(ItemContextProfile)
            .filter(ItemContextProfile.id == profile.context_profile_id)
            .one_or_none()
        )
        if context_profile is not None:
            previous_context_actual_values = [
                float(row[0])
                for row in db.query(AssetUsageActual.actual_hours_used)
                .join(
                    ActivityWorkProfile,
                    ActivityWorkProfile.id == AssetUsageActual.activity_work_profile_id,
                )
                .filter(ActivityWorkProfile.context_profile_id == context_profile.id)
                .all()
            ]

    db.flush()

    if context_profile is not None:
        try:
            _assert_profile_mutable(context_profile)
        except ValueError:
            logger.info(
                "Skipping context-profile learning updates for invalidated profile %s while preserving actual hours",
                getattr(context_profile, "id", None),
            )
            context_profile = None

    if context_profile is not None:
        if created_actual:
            next_context_actual_values = previous_context_actual_values + [float(actual_hours_used)]
        elif previous_actual_hours == float(actual_hours_used) and previous_source == source:
            next_context_actual_values = previous_context_actual_values
        else:
            next_context_actual_values = _replace_first_value(
                previous_context_actual_values,
                float(previous_actual_hours or 0.0),
                float(actual_hours_used),
            )

        context_profile.actuals_count = len(next_context_actual_values)
        context_profile.actuals_median = _median_of_values(next_context_actual_values)
        if context_profile.source != "manual" and (
            created_actual
            or previous_actual_hours != float(actual_hours_used)
            or previous_source != source
        ):
            base_sample_count = max(
                int(context_profile.sample_count or 0) - len(previous_context_actual_values),
                0,
            )
            base_evidence_weight = max(
                float(context_profile.evidence_weight or 0) - float(len(previous_context_actual_values)),
                0.0,
            )
            base_mean, base_precision = _reverse_actual_observations_from_posterior(
                context_profile,
                previous_context_actual_values,
            )
            mean, precision = _apply_actual_observations_to_posterior(
                base_mean,
                base_precision,
                next_context_actual_values,
            )
            context_profile.sample_count = base_sample_count + len(next_context_actual_values)
            context_profile.evidence_weight = round(
                base_evidence_weight + float(len(next_context_actual_values)),
                4,
            )
            context_profile.posterior_mean = mean
            context_profile.posterior_precision = precision

            rebuild_global_knowledge_entry(
                db,
                item_id=context_profile.item_id,
                asset_type=str(context_profile.asset_type),
                duration_bucket=duration_bucket_for_days(int(context_profile.duration_days or 0)),
                context_version=int(context_profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
                inference_version=int(context_profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
            )

    # Stage 11 — record feature observation for learning (append-only, no behavioural change)
    if context_profile is not None:
        _compressed = _resolve_context_profile_compressed_context(db, context_profile)
        if _compressed is not None:
            from app.services.feature_learning_service import record_feature_observation
            record_feature_observation(
                db,
                context_profile=context_profile,
                activity_work_profile=profile,
                actual_hours=float(actual_hours_used),
                compressed_context=_compressed,
                project_id=context_profile.project_id,
            )
    return actual


def backfill_project_local_context_profiles(
    db: Session,
    *,
    delete_unreferenced_legacy: bool = False,
) -> dict[str, int]:
    rows = (
        db.query(ActivityWorkProfile, ProgrammeActivity, ProgrammeUpload, ItemContextProfile)
        .join(ProgrammeActivity, ProgrammeActivity.id == ActivityWorkProfile.activity_id)
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .outerjoin(ItemContextProfile, ItemContextProfile.id == ActivityWorkProfile.context_profile_id)
        .order_by(ActivityWorkProfile.created_at.asc(), ActivityWorkProfile.id.asc())
        .all()
    )

    errors: list[str] = []
    repointed = 0
    reused = 0
    seeded = 0
    local_learning_changed = False

    for activity_profile, activity, upload, linked_profile in rows:
        if upload.project_id is None:
            errors.append(f"activity_work_profile:{activity_profile.id}:missing_project_id")
            continue

        resolved_item_id = (
            getattr(linked_profile, "item_id", None)
            or getattr(activity_profile, "item_id", None)
            or getattr(activity, "item_id", None)
        )
        if resolved_item_id is None:
            errors.append(f"activity_work_profile:{activity_profile.id}:missing_item_id")
            continue

        compressed_context = build_compressed_context(
            activity.name or "",
            level_name=activity.level_name,
            zone_name=activity.zone_name,
        )
        duration_days = max(int(activity_profile.duration_days or 0), 1)
        context_hash = str(activity_profile.context_hash or build_context_key(
            resolved_item_id,
            str(activity_profile.asset_type),
            duration_days,
            compressed_context,
            context_version=int(activity_profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
            inference_version=int(activity_profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
        ))
        confidence = float(activity_profile.confidence or 0)
        linked_source = str(getattr(linked_profile, "source", "") or "")
        activity_source = str(activity_profile.source or "")

        if linked_source in {"manual", "default", "learned", "ai"}:
            effective_source = linked_source
        elif activity_source == "manual":
            effective_source = "manual"
        elif activity_source == "default":
            effective_source = "default"
        elif activity_source == "ai":
            effective_source = "ai"
        else:
            effective_source = "learned"

        local_profile = lookup_cache(
            db,
            upload.project_id,
            resolved_item_id,
            str(activity_profile.asset_type),
            duration_days,
            context_hash,
            int(activity_profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
            int(activity_profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
        )
        if local_profile is not None and activity_profile.context_profile_id == local_profile.id:
            continue

        if effective_source == "manual":
            local_profile = upsert_manual_context_profile(
                db,
                project_id=upload.project_id,
                item_id=resolved_item_id,
                asset_type=str(activity_profile.asset_type),
                duration_days=duration_days,
                compressed_context=compressed_context,
                total_hours=float(activity_profile.total_hours or 0),
                distribution=list(activity_profile.distribution_json or []),
                normalized_distribution=list(activity_profile.normalized_distribution_json or []),
                context_hash=context_hash,
                sync_global=False,
                context_version=int(activity_profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
                inference_version=int(activity_profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
            )
            seeded += 1
            local_learning_changed = True
        elif local_profile is None and effective_source == "default":
            local_profile = _write_cache_entry(
                db,
                upload.project_id,
                resolved_item_id,
                str(activity_profile.asset_type),
                duration_days,
                context_hash,
                float(activity_profile.total_hours or 0),
                list(activity_profile.distribution_json or []),
                list(activity_profile.normalized_distribution_json or []),
                confidence,
                source="default",
                low_confidence_flag=bool(activity_profile.low_confidence_flag),
                sync_global=False,
                context_version=int(activity_profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
                inference_version=int(activity_profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
            )
            seeded += 1
            local_learning_changed = True
        elif local_profile is None and activity_source == "cache":
            local_profile = _write_cache_entry(
                db,
                upload.project_id,
                resolved_item_id,
                str(activity_profile.asset_type),
                duration_days,
                context_hash,
                float(activity_profile.total_hours or 0),
                list(activity_profile.distribution_json or []),
                list(activity_profile.normalized_distribution_json or []),
                confidence,
                source=effective_source,
                low_confidence_flag=bool(activity_profile.low_confidence_flag),
                sync_global=False,
                context_version=int(activity_profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
                inference_version=int(activity_profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
            )
            seeded += 1
            local_learning_changed = True
        elif activity_source == "cache":
            _update_cache_on_hit(db, local_profile)
            db.flush()
            reused += 1
            local_learning_changed = True
        else:
            local_profile = _upsert_cache_from_external_observation(
                db,
                project_id=upload.project_id,
                item_id=resolved_item_id,
                asset_type=str(activity_profile.asset_type),
                duration_days=duration_days,
                context_hash=context_hash,
                total_hours=float(activity_profile.total_hours or 0),
                distribution=list(activity_profile.distribution_json or []),
                normalized_distribution=list(activity_profile.normalized_distribution_json or []),
                confidence=confidence,
                source=effective_source,
                low_confidence_flag=bool(activity_profile.low_confidence_flag),
                sync_global=False,
                context_version=int(activity_profile.context_version or WORK_PROFILE_CONTEXT_VERSION),
                inference_version=int(activity_profile.inference_version or WORK_PROFILE_INFERENCE_VERSION),
            )
            seeded += 1
            local_learning_changed = True

        if local_profile is None:
            errors.append(f"activity_work_profile:{activity_profile.id}:unable_to_materialize_local_profile")
            continue

        if activity_profile.context_profile_id != local_profile.id:
            activity_profile.context_profile_id = local_profile.id
            repointed += 1

    if errors:
        raise RuntimeError(
            "Stage 10 local-cache backfill aborted due to unresolved provenance: "
            + "; ".join(errors[:20])
        )

    if local_learning_changed:
        rebuild_all_global_knowledge(db)

    deleted_legacy = 0
    if delete_unreferenced_legacy:
        referenced_ids = {
            row[0]
            for row in db.query(ActivityWorkProfile.context_profile_id)
            .filter(ActivityWorkProfile.context_profile_id.isnot(None))
            .all()
        }
        legacy_rows = (
            db.query(ItemContextProfile)
            .filter(ItemContextProfile.project_id.is_(None))
            .all()
        )
        for legacy_row in legacy_rows:
            if legacy_row.id not in referenced_ids:
                db.delete(legacy_row)
                deleted_legacy += 1

    db.flush()
    return {
        "processed_activity_profiles": len(rows),
        "repointed_activity_profiles": repointed,
        "reused_local_hits": reused,
        "materialized_local_profiles": seeded,
        "deleted_unreferenced_legacy_profiles": deleted_legacy,
    }


# ---------------------------------------------------------------------------
# 13. Main entry point (first-half: cache + default fallback, no AI yet)
# ---------------------------------------------------------------------------

def resolve_work_profile(
    db: Session,
    project_id: uuid.UUID,
    activity_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    activity_name: str,
    activity_asset_mapping_id: uuid.UUID | None = None,
    asset_role: str | None = None,
    seed_total_hours: float | None = None,
    seed_profile_shape: str | None = None,
    level_name: Optional[str] = None,
    zone_name: Optional[str] = None,
    row_confidence: Optional[str] = None,
    allow_ai: bool = True,
    degraded_mode: bool = False,
    runtime: Optional[WorkProfileRuntimeState] = None,
    preflight: Optional[WorkProfilePreflight] = None,
    precomputed_ai_proposal: Optional[dict[str, object]] = None,
    execution_context: AIExecutionContext | None = None,
) -> ActivityWorkProfile:
    """
    Resolve and persist a work profile for one programme activity.

    Flow:
      1. Build compressed context and deterministic context key.
      2. Look up item_context_profiles using exact, then reduced-context fallback.
      3. Cache HIT:
           - evaluate maturity tier
           - MANUAL / TRUSTED_BASELINE → use posterior_mean (or stored total_hours)
           - CONFIRMED / TENTATIVE → call AI when allowed, otherwise reuse cache
           - update evidence counters on the cache entry
      4. Cache MISS:
           - check for trusted baseline across other contexts for same (item, asset, duration)
           - if baseline: use it with a deterministic prior shape; write source='learned'
           - else: call AI when allowed, or fall back to an asset-specific default profile
      5. Apply low_confidence_flag if row_confidence='low'.
      6. Run Stage D validation; log any failures.
      7. Write activity_work_profiles.
    """
    if duration_days <= 0:
        duration_days = 1

    # ── Step 1: context ──────────────────────────────────────────────────────
    preflight = preflight or _preflight_work_profile_resolution(
        db,
        project_id=project_id,
        item_id=item_id,
        asset_type=asset_type,
        duration_days=duration_days,
        activity_name=activity_name,
        level_name=level_name,
        zone_name=zone_name,
    )

    low_flag = row_confidence == "low"
    context_profile_id: Optional[uuid.UUID] = None

    # ── Step 2: cache lookup ─────────────────────────────────────────────────
    cached = preflight.cached

    if cached is not None:
        # ── Step 3: cache HIT ────────────────────────────────────────────────
        tier = preflight.tier or work_profile_maturity(cached)
        logger.debug(
            "Item %s asset=%s dur=%d: cache HIT (maturity=%s)",
            item_id, asset_type, duration_days, tier,
        )

        # Determine operative total_hours
        if tier in (MATURITY_MANUAL, MATURITY_TRUSTED_BASELINE):
            operative_hours = (
                float(cached.posterior_mean)
                if cached.posterior_mean is not None
                else float(cached.total_hours)
            )
        else:
            # CONFIRMED / TENTATIVE start from the best cached estimate, then AI may refine it.
            operative_hours = (
                float(cached.posterior_mean)
                if cached.posterior_mean is not None
                else float(cached.total_hours)
            )

        norm_dist = list(cached.normalized_distribution_json)
        final_hours = quantize_hours(operative_hours)
        distribution = derive_distribution(
            norm_dist,
            final_hours,
            max_hours_per_day=preflight.max_hours_per_day,
        )

        _update_cache_on_hit(db, cached)
        context_profile_id = cached.id
        activity_source = _activity_profile_source_for_cache(cached.source)
        confidence = float(cached.confidence)
        low_flag = low_flag or bool(cached.low_confidence_flag)

        if tier in (MATURITY_CONFIRMED, MATURITY_TENTATIVE):
            ai_payload = precomputed_ai_proposal
            if ai_payload is None and allow_ai and not degraded_mode:
                hint = _posterior_hint_payload(cached) if tier == MATURITY_CONFIRMED else None
                ai_payload = _request_validated_ai_proposal_sync(
                    db=db,
                    activity_id=activity_id,
                    item_id=item_id,
                    context_hash=preflight.context_hash,
                    activity_name=activity_name,
                    asset_type=asset_type,
                    duration_days=duration_days,
                    max_hours_per_day=preflight.max_hours_per_day,
                    compressed_context=preflight.compressed_context,
                    level_name=level_name,
                    zone_name=zone_name,
                    row_confidence=row_confidence,
                    posterior_hint=hint,
                    trusted_baseline=None,
                    runtime=runtime,
                    execution_context=execution_context,
                )

            if ai_payload is not None:
                final_hours = float(ai_payload["final_hours"])
                distribution = list(ai_payload["distribution"])
                norm_dist = list(ai_payload["normalized_distribution"])
                confidence = float(ai_payload["confidence"])
                updated_cache = _upsert_cache_from_external_observation(
                    db,
                    project_id=project_id,
                    item_id=item_id,
                    asset_type=asset_type,
                    duration_days=duration_days,
                    context_hash=preflight.context_hash,
                    total_hours=final_hours,
                    distribution=distribution,
                    normalized_distribution=norm_dist,
                    confidence=confidence,
                    source="ai",
                    low_confidence_flag=low_flag,
                )
                context_profile_id = updated_cache.id
                activity_source = "ai"
            elif degraded_mode:
                low_flag = True
                _append_runtime_reason(runtime, "work_profile_ai_suppressed")

    else:
        # ── Step 4: cache MISS ───────────────────────────────────────────────
        logger.debug(
            "Item %s asset=%s dur=%d: cache MISS",
            item_id, asset_type, duration_days,
        )

        baseline = preflight.trusted_baseline
        global_knowledge = preflight.global_knowledge

        if baseline is not None:
            final_hours = quantize_hours(baseline)
            _, _, norm_dist = build_default_profile(
                asset_type,
                duration_days,
                preflight.max_hours_per_day,
                compressed_context=preflight.compressed_context,
            )
            distribution = derive_distribution(
                norm_dist,
                final_hours,
                max_hours_per_day=preflight.max_hours_per_day,
            )
            confidence = 0.6   # moderate confidence for inherited baseline
            new_cache = _upsert_cache_from_external_observation(
                db,
                project_id=project_id,
                item_id=item_id,
                asset_type=asset_type,
                duration_days=duration_days,
                context_hash=preflight.context_hash,
                total_hours=final_hours,
                distribution=distribution,
                normalized_distribution=norm_dist,
                confidence=confidence,
                source="learned",
                low_confidence_flag=low_flag,
            )
            context_profile_id = new_cache.id
            activity_source = "cache"
            logger.debug(
                "Item %s asset=%s dur=%d: trusted baseline %.2fh",
                item_id, asset_type, duration_days, final_hours,
            )
        elif global_knowledge is not None and global_knowledge.confidence_tier == "high":
            seeded_cache = _seed_local_cache_from_global_knowledge(
                db,
                project_id=project_id,
                item_id=item_id,
                asset_type=asset_type,
                duration_days=duration_days,
                context_hash=preflight.context_hash,
                max_hours_per_day=preflight.max_hours_per_day,
                compressed_context=preflight.compressed_context,
                global_knowledge=global_knowledge,
            )
            final_hours = float(seeded_cache.total_hours)
            distribution = list(seeded_cache.distribution_json or [])
            norm_dist = list(seeded_cache.normalized_distribution_json or [])
            confidence = float(seeded_cache.confidence or 0.9)
            context_profile_id = seeded_cache.id
            activity_source = _activity_profile_source_for_cache(str(seeded_cache.source))
            low_flag = low_flag or bool(getattr(seeded_cache, "low_confidence_flag", False))
            logger.debug(
                "Item %s asset=%s dur=%d: global knowledge HIT (tier=%s)",
                item_id,
                asset_type,
                duration_days,
                global_knowledge.confidence_tier,
            )
        else:
            # Full cache miss — try AI first when allowed, otherwise fall back to a deterministic default.
            ai_payload = precomputed_ai_proposal
            global_hint = None
            if global_knowledge is not None and global_knowledge.confidence_tier == "medium":
                global_hint = {
                    "posterior_mean": float(global_knowledge.posterior_mean),
                    "posterior_precision": float(global_knowledge.posterior_precision),
                    "sample_count": float(global_knowledge.sample_count or 0),
                    "confidence": 0.8,
                }
            if ai_payload is None and allow_ai and not degraded_mode:
                ai_payload = _request_validated_ai_proposal_sync(
                    db=db,
                    activity_id=activity_id,
                    item_id=item_id,
                    context_hash=preflight.context_hash,
                    activity_name=activity_name,
                    asset_type=asset_type,
                    duration_days=duration_days,
                    max_hours_per_day=preflight.max_hours_per_day,
                    compressed_context=preflight.compressed_context,
                    level_name=level_name,
                    zone_name=zone_name,
                    row_confidence=row_confidence,
                    posterior_hint=global_hint,
                    trusted_baseline=None,
                    runtime=runtime,
                    execution_context=execution_context,
                )

            if ai_payload is not None:
                final_hours = float(ai_payload["final_hours"])
                distribution = list(ai_payload["distribution"])
                norm_dist = list(ai_payload["normalized_distribution"])
                confidence = float(ai_payload["confidence"])
                updated_cache = _upsert_cache_from_external_observation(
                    db,
                    project_id=project_id,
                    item_id=item_id,
                    asset_type=asset_type,
                    duration_days=duration_days,
                    context_hash=preflight.context_hash,
                    total_hours=final_hours,
                    distribution=distribution,
                    normalized_distribution=norm_dist,
                    confidence=confidence,
                    source="ai",
                    low_confidence_flag=low_flag,
                )
                context_profile_id = updated_cache.id
                activity_source = "ai"
            else:
                final_hours, distribution, norm_dist = build_default_profile(
                    asset_type,
                    duration_days,
                    preflight.max_hours_per_day,
                    compressed_context=preflight.compressed_context,
                )
                confidence = 0.3
                activity_source = "default"
                low_flag = True
                new_cache = _write_cache_entry(
                    db,
                    project_id,
                    item_id,
                    asset_type,
                    duration_days,
                    preflight.context_hash,
                    final_hours,
                    distribution,
                    norm_dist,
                    confidence,
                    source="default",
                    low_confidence_flag=True,
                )
                context_profile_id = new_cache.id
                if degraded_mode:
                    _append_runtime_reason(runtime, "work_profile_ai_suppressed")
                logger.debug(
                    "Item %s asset=%s dur=%d: default fallback %.2fh",
                    item_id, asset_type, duration_days, final_hours,
                )

    # ── Step 4b: Stage 11 feature adjustments + actuals shape blending ──────
    from app.services.feature_learning_service import (
        apply_feature_adjustments_to_hours,
        get_feature_adjustments,
    )
    _adjustments = get_feature_adjustments(
        db,
        item_id=item_id,
        asset_type=asset_type,
        duration_bucket=duration_bucket_for_days(duration_days),
        compressed_context=preflight.compressed_context,
    )
    if _adjustments:
        final_hours = quantize_hours(
            apply_feature_adjustments_to_hours(float(final_hours), _adjustments)
        )
        distribution = derive_distribution(
            norm_dist, final_hours, max_hours_per_day=preflight.max_hours_per_day
        )

    # Shape blending: blend AI shape with actuals shape when sufficient data exists
    if cached is not None:
        _actuals_shape = getattr(cached, "actuals_shape_json", None)
        _actuals_count = int(getattr(cached, "actuals_count", 0) or 0)
        if _actuals_shape and _actuals_count >= 3 and len(_actuals_shape) == len(norm_dist):
            alpha = min(_actuals_count / 10.0, 0.9)
            blended = [
                alpha * _actuals_shape[i] + (1.0 - alpha) * norm_dist[i]
                for i in range(len(norm_dist))
            ]
            norm_dist = derive_normalized_distribution(blended)
            distribution = derive_distribution(
                norm_dist, final_hours, max_hours_per_day=preflight.max_hours_per_day
            )

    # ── Step 5: Stage D validation ───────────────────────────────────────────
    shape_family = _profile_shape_to_fallback_family(seed_profile_shape)
    if shape_family:
        norm_dist = _fallback_shape_weights(shape_family, duration_days)
        distribution = derive_distribution(
            norm_dist,
            final_hours,
            max_hours_per_day=preflight.max_hours_per_day,
        )

    if seed_total_hours is not None:
        seeded_hours = max(0.0, float(seed_total_hours))
        seeded_hours = min(seeded_hours, preflight.max_hours_per_day * duration_days)
        final_hours = quantize_hours(seeded_hours)
        distribution = derive_distribution(
            norm_dist,
            final_hours,
            max_hours_per_day=preflight.max_hours_per_day,
        )
        confidence = max(float(confidence), 0.75)
        activity_source = "manual" if activity_source == "default" else activity_source
        low_flag = False
    elif str(asset_role or "").lower() in {"support", "incidental"} and activity_source == "default":
        role_factor = 0.45 if str(asset_role or "").lower() == "support" else 0.20
        final_hours = quantize_hours(float(final_hours) * role_factor)
        distribution = derive_distribution(
            norm_dist,
            final_hours,
            max_hours_per_day=preflight.max_hours_per_day,
        )

    result = validate_stage_d(
        final_hours, distribution, norm_dist,
        asset_type, duration_days, preflight.max_hours_per_day,
    )
    if not result.valid:
        logger.warning(
            "Stage D validation failed for item=%s asset=%s dur=%d: %s",
            item_id, asset_type, duration_days, "; ".join(result.errors),
        )
        low_flag = True
        if runtime is not None:
            runtime.validation_failures += 1

    # ── Step 6: write activity_work_profiles ─────────────────────────────────
    return _write_activity_profile(
        db,
        activity_id=activity_id,
        activity_asset_mapping_id=activity_asset_mapping_id,
        item_id=item_id,
        asset_type=asset_type,
        duration_days=duration_days,
        total_hours=final_hours,
        distribution=distribution,
        normalized_distribution=norm_dist,
        confidence=confidence,
        source=activity_source,
        context_hash=preflight.context_hash,
        context_profile_id=context_profile_id,
        low_confidence_flag=low_flag,
    )


async def materialize_work_profiles_for_upload(
    db: Session,
    upload: ProgrammeUpload,
    *,
    operator_override: bool = False,
    execution_context: AIExecutionContext | None = None,
) -> WorkProfileRuntimeState:
    execution_context = _resolve_ai_execution_context(execution_context)
    runtime = WorkProfileRuntimeState(
        allow_ai=not bool(execution_context and execution_context.suppress_ai),
        operator_override=operator_override,
    )
    if execution_context is not None and execution_context.suppress_ai:
        _append_runtime_reason(runtime, "work_profile_ai_suppressed")

    rows = (
        db.query(ProgrammeActivity, ActivityAssetMapping)
        .join(ActivityAssetMapping, ActivityAssetMapping.programme_activity_id == ProgrammeActivity.id)
        .filter(
            ProgrammeActivity.programme_upload_id == upload.id,
            ActivityAssetMapping.asset_type.isnot(None),
            ActivityAssetMapping.asset_type != "none",
            ActivityAssetMapping.is_active.is_(True),
            ActivityAssetMapping.auto_committed.is_(True),
        )
        .all()
    )

    grouped: dict[str, list[tuple[ProgrammeActivity, ActivityAssetMapping, WorkProfilePreflight]]] = defaultdict(list)
    ai_needed_keys: set[str] = set()
    for activity, mapping in rows:
        if not activity.item_id:
            _append_runtime_reason(runtime, "missing_item_identity")
            continue
        preflight = _preflight_work_profile_resolution(
            db,
            project_id=upload.project_id,
            item_id=activity.item_id,
            asset_type=mapping.asset_type,
            duration_days=max(int(activity.duration_days or 0), 1),
            activity_name=activity.name,
            level_name=activity.level_name,
            zone_name=activity.zone_name,
        )
        grouped[preflight.context_hash].append((activity, mapping, preflight))
        needs_ai, _ = _preflight_needs_ai(preflight)
        if needs_ai:
            ai_needed_keys.add(preflight.context_hash)

    logger.info(
        "Work-profile materialization starting: upload=%s mapped_rows=%d context_groups=%d ai_candidates=%d",
        upload.id,
        len(rows),
        len(grouped),
        len(ai_needed_keys),
    )

    if len(ai_needed_keys) > WORK_PROFILE_MAX_NEW_CONTEXTS_PER_UPLOAD and not operator_override:
        runtime.allow_ai = False
        _append_runtime_reason(runtime, "max_new_contexts_exceeded")

    grouped_items = list(grouped.items())
    max_batch_size = max(1, AI_WORK_PROFILE_MAX_CONCURRENT)
    processed_groups = 0
    processed_ai_candidates = 0

    for batch_start in range(0, len(grouped_items), max_batch_size):
        batch = grouped_items[batch_start : batch_start + max_batch_size]
        representative_outcomes: dict[str, WorkProfileAIOutcome] = {}
        precompute_inputs: list[tuple[str, ProgrammeActivity, ActivityAssetMapping, WorkProfilePreflight, Optional[dict[str, float]]]] = []

        if runtime.allow_ai:
            for context_hash, group in batch:
                representative_activity, representative_mapping, representative_preflight = group[0]
                needs_ai, hint = _preflight_needs_ai(representative_preflight)
                if needs_ai:
                    precompute_inputs.append(
                        (
                            context_hash,
                            representative_activity,
                            representative_mapping,
                            representative_preflight,
                            hint,
                        )
                    )

        if precompute_inputs:
            if execution_context is not None:
                outcome_list = []
                for (
                    _context_hash,
                    representative_activity,
                    representative_mapping,
                    representative_preflight,
                    hint,
                ) in precompute_inputs:
                    if execution_context.suppress_ai:
                        runtime.allow_ai = False
                        _append_runtime_reason(runtime, "work_profile_ai_suppressed")
                        break
                    outcome = await _precompute_validated_ai_proposal(
                        activity_name=representative_activity.name,
                        asset_type=representative_mapping.asset_type,
                        duration_days=max(int(representative_activity.duration_days or 0), 1),
                        max_hours_per_day=representative_preflight.max_hours_per_day,
                        compressed_context=representative_preflight.compressed_context,
                        level_name=representative_activity.level_name,
                        zone_name=representative_activity.zone_name,
                        row_confidence=representative_activity.row_confidence,
                        posterior_hint=hint,
                        trusted_baseline=None,
                        execution_context=execution_context,
                    )
                    outcome_list.append(outcome)
            else:
                outcome_list = await asyncio.gather(
                    *[
                        _precompute_validated_ai_proposal(
                            activity_name=representative_activity.name,
                            asset_type=representative_mapping.asset_type,
                            duration_days=max(int(representative_activity.duration_days or 0), 1),
                            max_hours_per_day=representative_preflight.max_hours_per_day,
                            compressed_context=representative_preflight.compressed_context,
                            level_name=representative_activity.level_name,
                            zone_name=representative_activity.zone_name,
                            row_confidence=representative_activity.row_confidence,
                            posterior_hint=hint,
                            trusted_baseline=None,
                            execution_context=None,
                        )
                        for (
                            _context_hash,
                            representative_activity,
                            representative_mapping,
                            representative_preflight,
                            hint,
                        ) in precompute_inputs
                    ]
                )

            processed_precompute_inputs = precompute_inputs[: len(outcome_list)]

            for (
                context_hash,
                representative_activity,
                _representative_mapping,
                _representative_preflight,
                _hint,
            ), outcome in zip(processed_precompute_inputs, outcome_list, strict=True):
                representative_outcomes[context_hash] = outcome
                processed_ai_candidates += 1
                runtime.ai_attempts += outcome.ai_attempts
                runtime.ai_tokens_used += outcome.ai_tokens_used
                runtime.ai_cost_usd = sum_ai_costs(runtime.ai_cost_usd, outcome.ai_cost_usd)
                runtime.validation_failures += outcome.validation_failures
                if execution_context is not None and execution_context.suppress_ai:
                    runtime.allow_ai = False
                    _append_runtime_reason(runtime, "work_profile_ai_suppressed")

                _write_work_profile_ai_log(
                    db,
                    activity_id=representative_activity.id,
                    item_id=representative_activity.item_id,
                    context_hash=context_hash,
                    request_json=outcome.request_json,
                    response_json=outcome.response_json,
                    validation_errors=outcome.validation_errors,
                    fallback_used=outcome.fallback_used,
                    retry_count=outcome.retry_count,
                    input_tokens=outcome.log_input_tokens_used,
                    output_tokens=outcome.log_output_tokens_used,
                    tokens_used=outcome.log_tokens_used,
                    cost_usd=outcome.log_cost_usd,
                    latency_ms=outcome.latency_ms,
                    model_name=settings.AI_MODEL,
                )

            if (
                runtime.ai_attempts > 0
                and runtime.validation_failures / max(runtime.ai_attempts, 1)
                > WORK_PROFILE_CORRECTION_RATE_THRESHOLD
                and not operator_override
            ):
                runtime.allow_ai = False
                _append_runtime_reason(runtime, "validation_failure_rate_exceeded")

        for context_hash, group in batch:
            representative_activity, representative_mapping, representative_preflight = group[0]
            representative_outcome = representative_outcomes.get(context_hash)
            representative_attempted_ai = representative_outcome is not None and representative_outcome.attempted_ai
            representative_ai_payload = representative_outcome.proposal if representative_outcome is not None else None
            representative_degraded = (not runtime.allow_ai) or (
                representative_attempted_ai and representative_ai_payload is None
            )

            resolve_work_profile(
                db,
                project_id=upload.project_id,
                activity_id=representative_activity.id,
                activity_asset_mapping_id=representative_mapping.id,
                item_id=representative_activity.item_id,
                asset_type=representative_mapping.asset_type,
                duration_days=max(int(representative_activity.duration_days or 0), 1),
                activity_name=representative_activity.name,
                asset_role=representative_mapping.asset_role,
                seed_total_hours=float(representative_mapping.estimated_total_hours) if representative_mapping.estimated_total_hours is not None else None,
                seed_profile_shape=representative_mapping.profile_shape,
                level_name=representative_activity.level_name,
                zone_name=representative_activity.zone_name,
                row_confidence=representative_activity.row_confidence,
                allow_ai=runtime.allow_ai and not representative_attempted_ai,
                degraded_mode=representative_degraded,
                runtime=runtime,
                preflight=representative_preflight,
                precomputed_ai_proposal=representative_ai_payload,
                execution_context=execution_context,
            )

            for sub_activity, sub_mapping, sub_preflight in group[1:]:
                sub_preflight = _preflight_work_profile_resolution(
                    db,
                    project_id=upload.project_id,
                    item_id=sub_activity.item_id,
                    asset_type=sub_mapping.asset_type,
                    duration_days=max(int(sub_activity.duration_days or 0), 1),
                    activity_name=sub_activity.name,
                    level_name=sub_activity.level_name,
                    zone_name=sub_activity.zone_name,
                )
                resolve_work_profile(
                    db,
                    project_id=upload.project_id,
                    activity_id=sub_activity.id,
                    activity_asset_mapping_id=sub_mapping.id,
                    item_id=sub_activity.item_id,
                    asset_type=sub_mapping.asset_type,
                    duration_days=max(int(sub_activity.duration_days or 0), 1),
                    activity_name=sub_activity.name,
                    asset_role=sub_mapping.asset_role,
                    seed_total_hours=float(sub_mapping.estimated_total_hours) if sub_mapping.estimated_total_hours is not None else None,
                    seed_profile_shape=sub_mapping.profile_shape,
                    level_name=sub_activity.level_name,
                    zone_name=sub_activity.zone_name,
                    row_confidence=sub_activity.row_confidence,
                    allow_ai=False,
                    degraded_mode=representative_degraded,
                    runtime=runtime,
                    preflight=sub_preflight,
                    precomputed_ai_proposal=None,
                    execution_context=execution_context,
                )

        processed_groups += len(batch)
        logger.info(
            "Work-profile materialization progress: upload=%s groups=%d/%d ai_contexts=%d/%d allow_ai=%s",
            upload.id,
            processed_groups,
            len(grouped_items),
            processed_ai_candidates,
            len(ai_needed_keys),
            runtime.allow_ai,
        )

    return runtime
