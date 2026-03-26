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
# Demand is computed as working_days x DEMAND_HOURS_PER_DAY, so a single
# asset running a full 5-day week contributes 40 h.  Thresholds are set so
# that normal full-week single-asset utilisation lands in "high", not
# "critical".  "Critical" implies multi-asset demand or a scheduling conflict.
DEMAND_LEVEL_LOW_MAX: int = 16     # < 16 h  -> low   (<= 2 standard days)
DEMAND_LEVEL_MEDIUM_MAX: int = 32  # < 32 h  -> medium (2-4 days)
DEMAND_LEVEL_HIGH_MAX: int = 48    # < 48 h  -> high   (4-6 days, 1 asset limit)
                                   # >= 48 h  -> critical (multi-asset territory)

# Anomaly-detection thresholds used in _compute_anomaly_flags().
# Demand spike: flag when week-over-week change for a bucket exceeds this
# fraction.  1.5 = 150% change (2.5x demand); doubling alone is common on
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


# ---------------------------------------------------------------------------
# AI service — processing parameters
# ---------------------------------------------------------------------------

# Number of rows sampled from an uploaded file for structure detection.
# 50 rows is sufficient to identify column headers and date patterns reliably.
AI_STRUCTURE_DETECTION_SAMPLE_SIZE: int = 50

# max_tokens budget for the structure detection API call.
AI_STRUCTURE_DETECTION_MAX_TOKENS: int = 2048

# max_tokens budget for a single activity classification batch call.
# 50 activities × ~30 output tokens = ~1,500 tokens + JSON overhead;
# 8192 gives ~5× headroom.
AI_CLASSIFICATION_BATCH_MAX_TOKENS: int = 8192

# Number of activities sent to the AI in a single classification batch call.
AI_CLASSIFICATION_BATCH_SIZE: int = 50

# Unique-candidate count above which classification batches are fanned out in
# parallel rather than run sequentially.
AI_CLASSIFICATION_PARALLEL_THRESHOLD: int = 100

# Maximum number of in-flight classification batch calls when running in parallel.
AI_CLASSIFICATION_MAX_CONCURRENT_BATCHES: int = 4

# Extra seconds added to AI_TIMEOUT_CLASSIFY for the ThreadPoolExecutor timeout
# in classify_item_standalone, to allow the coroutine's own timeout to fire first.
AI_STANDALONE_TIMEOUT_BUFFER_SECONDS: int = 2


# ---------------------------------------------------------------------------
# Booking rules
# ---------------------------------------------------------------------------

# Minimum duration (minutes) for a gap to be reported as an available booking slot.
BOOKING_MIN_SLOT_DURATION_MINUTES: int = 30

# Maximum date-range span (days) accepted by calendar / report query endpoints.
BOOKING_CALENDAR_MAX_DAYS: int = 90


# ---------------------------------------------------------------------------
# File handling
# ---------------------------------------------------------------------------

# Maximum accepted upload size in bytes (20 MB).
MAX_FILE_UPLOAD_SIZE_BYTES: int = 20 * 1024 * 1024

# HTTP Cache-Control max-age for served file previews and images (seconds).
FILE_CACHE_MAX_AGE_SECONDS: int = 3600

# PDF → PNG render scale for the low-resolution preview endpoint.
PDF_PREVIEW_SCALE: float = 1.5

# PDF → PNG render scale for the full-resolution image endpoint.
PDF_IMAGE_SCALE: float = 3.0


# ---------------------------------------------------------------------------
# API pagination defaults
# ---------------------------------------------------------------------------

# Default and maximum page sizes for list endpoints.
# Naming: <RESOURCE>_PAGE_<DEFAULT|MAX>

ASSET_PAGE_DEFAULT: int = 100
ASSET_PAGE_MAX: int = 100

BOOKING_PAGE_DEFAULT: int = 100
BOOKING_PAGE_MAX: int = 1000

BOOKING_AUDIT_PAGE_DEFAULT: int = 50
BOOKING_AUDIT_PAGE_MAX: int = 200

UPCOMING_BOOKINGS_PAGE_DEFAULT: int = 10
UPCOMING_BOOKINGS_PAGE_MAX: int = 100

ITEM_PAGE_DEFAULT: int = 50
ITEM_PAGE_MAX: int = 200

CLASSIFICATION_HISTORY_PAGE_DEFAULT: int = 50
CLASSIFICATION_HISTORY_PAGE_MAX: int = 200

PROJECT_PAGE_DEFAULT: int = 100
PROJECT_PAGE_MAX: int = 1000

SUBCONTRACTOR_PAGE_DEFAULT: int = 100
SUBCONTRACTOR_PAGE_MAX: int = 1000

# Default look-ahead window (days) for upcoming-bookings queries.
UPCOMING_BOOKINGS_DEFAULT_DAYS_AHEAD: int = 7
UPCOMING_BOOKINGS_MAX_DAYS_AHEAD: int = 90


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
