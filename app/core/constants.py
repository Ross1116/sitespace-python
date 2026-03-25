import logging

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bootstrap set — used as fallback when no DB session is available.
# The canonical source of truth is the asset_types table (Stage 3).
# Code that has a DB session should call get_active_asset_types(db) instead.
# ---------------------------------------------------------------------------
ALLOWED_ASSET_TYPES: frozenset[str] = frozenset({
    "crane",
    "hoist",
    "loading_bay",
    "ewp",
    "concrete_pump",
    "excavator",
    "forklift",
    "telehandler",
    "compactor",
    "other",
    "none",  # milestone/summary rows — classifier returns this; merge logic treats it as skip
})


# Default max_hours_per_day per asset type code (bootstrap fallback).
# Canonical values live in the asset_types table.
DEFAULT_MAX_HOURS_PER_DAY: dict[str, float] = {
    "crane":         10.0,
    "hoist":         10.0,
    "loading_bay":   12.0,
    "ewp":           12.0,
    "concrete_pump": 10.0,
    "excavator":     16.0,
    "forklift":      16.0,
    "telehandler":   16.0,
    "compactor":     10.0,
    "other":         16.0,
    "none":           0.0,
}


# ---------------------------------------------------------------------------
# Lookahead / demand-engine thresholds
# ---------------------------------------------------------------------------

# Hours per working day used when spreading activity demand across weeks.
# Aligns with the standard 8-hour day under most Australian construction EBAs.
DEMAND_HOURS_PER_DAY: int = 8

# Weekly demand-level bucket boundaries (hours per asset type per week).
# Demand is computed as working_days × DEMAND_HOURS_PER_DAY, so a single
# asset running a full 5-day week contributes 40 h.  Thresholds are set so
# that normal full-week single-asset utilisation lands in "high", not
# "critical".  "Critical" implies multi-asset demand or a scheduling conflict.
DEMAND_LEVEL_LOW_MAX: int = 16     # < 16 h  → low   (≤ 2 standard days)
DEMAND_LEVEL_MEDIUM_MAX: int = 32  # < 32 h  → medium (2–4 days)
DEMAND_LEVEL_HIGH_MAX: int = 48    # < 48 h  → high   (4–6 days, 1 asset limit)
                                   # ≥ 48 h  → critical (multi-asset territory)

# Anomaly-detection thresholds used in _compute_anomaly_flags().
# Demand spike: flag when week-over-week change for a bucket exceeds this
# fraction.  1.5 = 150% change (2.5× demand); doubling alone is common on
# pour weeks and should not trigger an alert.
ANOMALY_DEMAND_SPIKE_THRESHOLD: float = 1.5

# Mapping-change ratio: flag when this fraction of activity-asset mappings
# differ between consecutive uploads.  0.5 = half the schedule rewired.
ANOMALY_MAPPING_CHANGE_THRESHOLD: float = 0.5

# Activity-count delta ratio: flag when the programme changes in size by more
# than this fraction.  0.3 = 30% addition or removal of activities.
ANOMALY_ACTIVITY_DELTA_THRESHOLD: float = 0.3


# ---------------------------------------------------------------------------
# Classification maturity thresholds (Stage 4)
# ---------------------------------------------------------------------------

# Minimum number of upload confirmations (with zero corrections) before a
# classification graduates from TENTATIVE to CONFIRMED.  At this tier the AI
# re-check is skipped — the classification is considered reliable enough to
# use without validation.  2 = seen on two separate uploads with no disagreement.
CLASSIFICATION_CONFIRMED_MIN_CONFIRMATIONS: int = 2

# Minimum confirmations for the STABLE tier.  At STABLE the classification is
# treated as a trusted baseline — AI never re-checks it.  5 = seen on five
# separate uploads with no corrections.
CLASSIFICATION_STABLE_MIN_CONFIRMATIONS: int = 5


def get_active_asset_types(db: object) -> frozenset[str]:
    """Load active asset type codes from the DB taxonomy.

    Falls back to the bootstrap ALLOWED_ASSET_TYPES if the asset_types
    table doesn't exist yet (pre-migration) or the query fails.

    Parameters
    ----------
    db : sqlalchemy.orm.Session
        An active database session.
    """
    try:
        from ..crud.asset_type import get_active_codes
        return get_active_codes(db)  # type: ignore[arg-type]
    except Exception as exc:
        try:
            db.rollback()  # type: ignore[union-attr]
        except Exception:
            pass
        _logger.warning("Falling back to bootstrap ALLOWED_ASSET_TYPES: %s", exc)
        return ALLOWED_ASSET_TYPES


def get_max_hours_for_type(db: object, code: str) -> float:
    """Return max_hours_per_day for *code* from the DB taxonomy.

    Falls back to DEFAULT_MAX_HOURS_PER_DAY if the DB lookup fails.
    """
    try:
        from ..crud.asset_type import get_max_hours
        val = get_max_hours(db, code)  # type: ignore[arg-type]
        if val is not None:
            return val
    except Exception as exc:
        try:
            db.rollback()  # type: ignore[union-attr]
        except Exception:
            pass
        _logger.warning(
            "Falling back to DEFAULT_MAX_HOURS_PER_DAY for '%s': %s", code, exc,
        )
    return DEFAULT_MAX_HOURS_PER_DAY.get(code, 16.0)
