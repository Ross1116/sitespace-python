"""
Work profile service — Stage 5.

Manages the work profile cache for construction programme activities.

Architecture:
  1. Context builder      — fixed-schema semantic context extraction from hierarchy
  2. Context key          — deterministic SHA-256 cache key per (item, asset, duration, context)
  3. Cache lookup         — project-local item_context_profiles (global tier deferred to Stage 10)
  4. Maturity evaluation  — TENTATIVE / CONFIRMED / TRUSTED_BASELINE / MANUAL
  5. Bayesian update      — Normal-Normal conjugate posterior update on each cache encounter
  6. Hours finalization   — manual > trusted baseline > bounded AI proposal
  7. Hours quantization   — round to 0.5-hour operational unit
  8. Stage B validation   — per-day distribution bucket cap (must not exceed max_hours_per_day)
  9. Stage D validation   — final profile invariants
  10. Cache write          — persist / update item_context_profiles with full evidence tracking
  11. Profile write        — persist activity_work_profiles

AI generation (cache misses, TENTATIVE, CONFIRMED hints) is in the second half
of Stage 5 and wired in via generate_work_profile_ai().  Until that function is
implemented, resolve_work_profile() falls back to a default pattern.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..core.constants import (
    DEFAULT_MAX_HOURS_PER_DAY,
    WORK_PROFILE_ACTUAL_ERROR_FRACTION,
    WORK_PROFILE_AI_ERROR_FRACTION,
    WORK_PROFILE_CONTEXT_VERSION,
    WORK_PROFILE_CORRECTION_MIN_SAMPLES,
    WORK_PROFILE_CORRECTION_RATE_THRESHOLD,
    WORK_PROFILE_CV_CONFIRMED,
    WORK_PROFILE_CV_TRUSTED,
    WORK_PROFILE_INFERENCE_VERSION,
    WORK_PROFILE_MIN_HOURS,
    WORK_PROFILE_NORM_DIST_SUM_TOLERANCE,
    WORK_PROFILE_OPERATIONAL_UNIT,
)
from ..models.work_profile import ActivityWorkProfile, ItemContextProfile

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
    lowered = text.lower()
    for label, keywords in keyword_map.items():
        for kw in keywords:
            if kw in lowered:
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


def _find_trusted_baseline(
    db: Session,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
) -> Optional[float]:
    """
    Return the median total_hours across trusted historical cache entries for
    (item_id, asset_type, duration_days), or None if no baseline exists.

    Manual rows establish a baseline immediately.
    Learned / AI rows require NOT low_confidence_flag and sample_count >= 3.
    """
    manual_rows = (
        db.query(ItemContextProfile)
        .filter(
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
        db.query(ItemContextProfile)
        .filter(
            ItemContextProfile.item_id == item_id,
            ItemContextProfile.asset_type == asset_type,
            ItemContextProfile.duration_days == duration_days,
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
) -> list[float]:
    """
    Derive raw distribution from finalized total_hours × normalized_distribution.

    Returns a list of floats that sums to total_hours (within floating-point
    tolerance).  Each value is rounded to 4 decimal places.
    """
    if total_hours <= 0:
        return [0.0] * len(normalized_distribution)
    return [round(w * total_hours, 4) for w in normalized_distribution]


def derive_normalized_distribution(distribution: list[float]) -> list[float]:
    """Convert raw distribution to normalized form (sums to 1.0 or all zeros)."""
    total = sum(distribution)
    if total <= 0:
        return [0.0] * len(distribution)
    return [round(v / total, 6) for v in distribution]


def _base_context_only(compressed_context: dict) -> dict:
    """Return the fixed-schema base context used for reduced-context fallback."""
    return {
        "phase": compressed_context.get("phase", "unknown"),
        "spatial_type": compressed_context.get("spatial_type", "unknown"),
        "area_type": compressed_context.get("area_type", "unknown"),
    }


def _lookup_cache_with_reduced_context(
    db: Session,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    compressed_context: dict,
    context_version: int = WORK_PROFILE_CONTEXT_VERSION,
    inference_version: int = WORK_PROFILE_INFERENCE_VERSION,
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
    share = remaining / len(uncapped_indices)
    for i in uncapped_indices:
        adjusted[i] = round(min(share, max_hours_per_day), 4)

    if abs(sum(adjusted) - total_hours) > 0.01:
        per_bucket = min(round(total_hours / n, 4), max_hours_per_day)
        return [per_bucket] * n, True

    return adjusted, False


# ---------------------------------------------------------------------------
# 9. Default / fallback profile builder
# ---------------------------------------------------------------------------

_FRONT_LOADED_WEIGHTS: dict[int, list[float]] = {
    1: [1.0],
    2: [0.6, 0.4],
    3: [0.4, 0.35, 0.25],
    4: [0.35, 0.30, 0.20, 0.15],
    5: [0.30, 0.25, 0.20, 0.15, 0.10],
}


def build_default_profile(
    asset_type: str,
    duration_days: int,
    max_hours_per_day: float,
) -> tuple[float, list[float], list[float]]:
    """
    Build a minimal fallback work profile when AI is unavailable or disabled.

    Returns (total_hours, distribution, normalized_distribution).

    For 'none': all zeros.
    For others: total_hours = 0.5 × max_hours_per_day × duration_days (half utilisation),
    with a front-loaded normalized distribution for short tasks or uniform for longer ones.
    """
    if asset_type == "none":
        zeros = [0.0] * max(duration_days, 1)
        return 0.0, zeros, zeros

    # Half average utilisation as a conservative default
    raw_total = 0.5 * max_hours_per_day * duration_days
    total_hours = quantize_hours(raw_total)

    if duration_days in _FRONT_LOADED_WEIGHTS:
        norm_dist = _FRONT_LOADED_WEIGHTS[duration_days]
    else:
        norm_dist = _uniform_normalized(duration_days)

    distribution = derive_distribution(norm_dist, total_hours)
    return total_hours, distribution, norm_dist


# ---------------------------------------------------------------------------
# 10. Cache lookup
# ---------------------------------------------------------------------------

def lookup_cache(
    db: Session,
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
        db.query(ItemContextProfile)
        .filter(
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
    db.add(profile)
    db.flush()
    return profile


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


# ---------------------------------------------------------------------------
# 12. ActivityWorkProfile writer
# ---------------------------------------------------------------------------

def _write_activity_profile(
    db: Session,
    activity_id: uuid.UUID,
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
    awp = ActivityWorkProfile(
        activity_id=activity_id,
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
    return awp


# ---------------------------------------------------------------------------
# 13. Main entry point (first-half: cache + default fallback, no AI yet)
# ---------------------------------------------------------------------------

def resolve_work_profile(
    db: Session,
    activity_id: uuid.UUID,
    item_id: uuid.UUID,
    asset_type: str,
    duration_days: int,
    activity_name: str,
    level_name: Optional[str] = None,
    zone_name: Optional[str] = None,
    row_confidence: Optional[str] = None,
) -> ActivityWorkProfile:
    """
    Resolve and persist a work profile for one programme activity.

    Flow:
      1. Build compressed context and deterministic context key.
      2. Look up item_context_profiles (exact cache key).
      3. Cache HIT:
           - evaluate maturity tier
           - MANUAL / TRUSTED_BASELINE → use posterior_mean (or stored total_hours)
           - CONFIRMED / TENTATIVE → use posterior_mean if available, else stored value
             (AI generation is wired in Stage 5 second half; for now treat as trusted)
           - update evidence counters on the cache entry
      4. Cache MISS:
           - check for trusted baseline across other contexts for same (item, asset, duration)
           - if baseline: use it; write new cache entry with source='learned'
           - else: build default profile; write cache entry with source='default'
      5. Apply low_confidence_flag if row_confidence='low'.
      6. Run Stage D validation; log any failures.
      7. Write activity_work_profiles.

    AI generation (cache misses, TENTATIVE hits) is added in the second half
    of Stage 5.  Until then, cache misses fall back to the default profile.
    """
    if duration_days <= 0:
        duration_days = 1  # guard: must be positive per DB constraint

    # ── Step 1: context ──────────────────────────────────────────────────────
    compressed = build_compressed_context(activity_name, level_name, zone_name)
    context_hash = build_context_key(
        item_id, asset_type, duration_days, compressed,
        WORK_PROFILE_CONTEXT_VERSION, WORK_PROFILE_INFERENCE_VERSION,
    )

    # Look up max_hours_per_day for bounds enforcement
    from ..core.constants import get_max_hours_for_type
    max_h_per_day = get_max_hours_for_type(db, asset_type)

    low_flag = row_confidence == "low"
    context_profile_id: Optional[uuid.UUID] = None

    # ── Step 2: cache lookup ─────────────────────────────────────────────────
    cached, context_hash = _lookup_cache_with_reduced_context(
        db, item_id, asset_type, duration_days, compressed,
        WORK_PROFILE_CONTEXT_VERSION, WORK_PROFILE_INFERENCE_VERSION,
    )

    if cached is not None:
        # ── Step 3: cache HIT ────────────────────────────────────────────────
        tier = work_profile_maturity(cached)
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
            # CONFIRMED / TENTATIVE — AI will refine in second half; use best available
            operative_hours = (
                float(cached.posterior_mean)
                if cached.posterior_mean is not None
                else float(cached.total_hours)
            )

        # Derive distribution scaled to operative_hours
        norm_dist = list(cached.normalized_distribution_json)
        distribution = derive_distribution(norm_dist, operative_hours)
        final_hours = quantize_hours(operative_hours)
        # Re-derive to match quantized total exactly
        distribution = derive_distribution(norm_dist, final_hours)

        _update_cache_on_hit(db, cached)
        context_profile_id = cached.id
        activity_source = _activity_profile_source_for_cache(cached.source)
        confidence = float(cached.confidence)
        low_flag = low_flag or cached.low_confidence_flag

    else:
        # ── Step 4: cache MISS ───────────────────────────────────────────────
        logger.debug(
            "Item %s asset=%s dur=%d: cache MISS",
            item_id, asset_type, duration_days,
        )

        # Check for trusted baseline from other contexts (same item/asset/duration)
        baseline = _find_trusted_baseline(db, item_id, asset_type, duration_days)

        if baseline is not None:
            final_hours = quantize_hours(baseline)
            norm_dist = _uniform_normalized(duration_days)  # shape: uniform (best we have)
            distribution = derive_distribution(norm_dist, final_hours)
            confidence = 0.6   # moderate confidence for inherited baseline
            new_cache = _write_cache_entry(
                db, item_id, asset_type, duration_days, context_hash,
                final_hours, distribution, norm_dist, confidence,
                source="learned", low_confidence_flag=low_flag,
            )
            context_profile_id = new_cache.id
            activity_source = "cache"
            logger.debug(
                "Item %s asset=%s dur=%d: trusted baseline %.2fh",
                item_id, asset_type, duration_days, final_hours,
            )
        else:
            # Full cache miss — default profile (AI generation added in second half)
            final_hours, distribution, norm_dist = build_default_profile(
                asset_type, duration_days, max_h_per_day,
            )
            confidence = 0.3
            activity_source = "default"
            low_flag = True   # default profiles are always low confidence
            new_cache = _write_cache_entry(
                db, item_id, asset_type, duration_days, context_hash,
                final_hours, distribution, norm_dist, confidence,
                source="default", low_confidence_flag=True,
            )
            context_profile_id = new_cache.id
            logger.debug(
                "Item %s asset=%s dur=%d: default fallback %.2fh",
                item_id, asset_type, duration_days, final_hours,
            )

    # ── Step 5: Stage D validation ───────────────────────────────────────────
    result = validate_stage_d(
        final_hours, distribution, norm_dist,
        asset_type, duration_days, max_h_per_day,
    )
    if not result.valid:
        logger.warning(
            "Stage D validation failed for item=%s asset=%s dur=%d: %s",
            item_id, asset_type, duration_days, "; ".join(result.errors),
        )

    # ── Step 6: write activity_work_profiles ─────────────────────────────────
    return _write_activity_profile(
        db,
        activity_id=activity_id,
        item_id=item_id,
        asset_type=asset_type,
        duration_days=duration_days,
        total_hours=final_hours,
        distribution=distribution,
        normalized_distribution=norm_dist,
        confidence=confidence,
        source=activity_source,
        context_hash=context_hash,
        context_profile_id=context_profile_id,
        low_confidence_flag=low_flag,
    )
