import logging

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Asset type bootstrap fallbacks
# ---------------------------------------------------------------------------

# Used when the DB taxonomy cannot be read yet. Code with a DB session should
# prefer get_active_asset_types(db) so runtime behavior follows the stored
# taxonomy rather than this bootstrap fallback.
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
    "none",  # milestone/summary rows intentionally produce no managed demand
})


# Bootstrap max-hours values used only when the asset_types table is unavailable.
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
# Lookahead demand thresholds
# ---------------------------------------------------------------------------

# Standard working hours contributed by one asset across one working day.
DEMAND_HOURS_PER_DAY: int = 8

# Weekly demand bands. These keep up to 2 standard days in "low", 2-4 days in
# "medium", and one full single-asset week in "high" before "critical" starts.
DEMAND_LEVEL_LOW_MAX: int = 16
DEMAND_LEVEL_MEDIUM_MAX: int = 32
DEMAND_LEVEL_HIGH_MAX: int = 64


# ---------------------------------------------------------------------------
# Lookahead anomaly thresholds
# ---------------------------------------------------------------------------

# Week-over-week demand must change by more than 150% before it is treated as
# anomalous. Doubling alone is common on construction programmes.
ANOMALY_DEMAND_SPIKE_THRESHOLD: float = 1.5

# Flag when at least half of committed activity-to-asset mappings changed.
ANOMALY_MAPPING_CHANGE_THRESHOLD: float = 0.5

# Flag when the programme activity count changes by more than 30%.
ANOMALY_ACTIVITY_DELTA_THRESHOLD: float = 0.3


# ---------------------------------------------------------------------------
# Classification confidence thresholds
# ---------------------------------------------------------------------------

# Reuse classification directly after two clean confirmations across uploads.
CLASSIFICATION_CONFIRMED_MIN_CONFIRMATIONS: int = 2

# Treat classification as a stable baseline after five clean confirmations.
CLASSIFICATION_STABLE_MIN_CONFIRMATIONS: int = 5


# ---------------------------------------------------------------------------
# AI structure-detection limits
# ---------------------------------------------------------------------------

# Sample enough rows to identify headers, date columns, and row-shape patterns
# without paying to send an entire upload.
AI_STRUCTURE_DETECTION_SAMPLE_SIZE: int = 50

# Leave enough room for strict JSON plus header/date reasoning in a small model.
AI_STRUCTURE_DETECTION_MAX_TOKENS: int = 2048


# ---------------------------------------------------------------------------
# AI classification limits
# ---------------------------------------------------------------------------

# Keep batch size modest so a single oddball activity cannot blow out the whole
# request payload or make one retry too expensive.
AI_CLASSIFICATION_BATCH_SIZE: int = 40

# Sized for ~40 rows plus instructions and JSON output with comfortable headroom.
AI_CLASSIFICATION_BATCH_MAX_TOKENS: int = 6144

# Only parallelize once the upload is large enough to justify the extra cost and
# concurrent rate-limit pressure.
AI_CLASSIFICATION_PARALLEL_THRESHOLD: int = 80

# Conservative launch default: enough parallelism to keep uploads moving without
# spiking provider errors or spend.
AI_CLASSIFICATION_MAX_CONCURRENT_BATCHES: int = 3

# Shared provider throttle applied at the actual API call layer. This keeps
# structure detection, classification, and work-profile inference from all
# bursting at once even when their local batchers are active.
AI_PROVIDER_MAX_CONCURRENT_REQUESTS: int = 2

# Stagger launches slightly so retries and large uploads do not hammer the
# provider in a single burst.
AI_PROVIDER_MIN_REQUEST_SPACING_SECONDS: float = 0.35

# Give the outer timeout a little headroom over the coroutine timeout so the
# inner timeout is what usually fires first.
AI_STANDALONE_TIMEOUT_BUFFER_SECONDS: int = 3


# ---------------------------------------------------------------------------
# Work-profile inference limits
# ---------------------------------------------------------------------------

# Bump when context extraction logic changes in a way that changes cache semantics.
WORK_PROFILE_CONTEXT_VERSION: int = 1

# Bump when prompt/model/policy behavior changes in a way that changes cache semantics.
WORK_PROFILE_INFERENCE_VERSION: int = 2

# Cap new unique contexts per upload so cold-start uploads do not explode cost
# or fragment the cache too aggressively.
WORK_PROFILE_MAX_NEW_CONTEXTS_PER_UPLOAD: int = 150

# Conservative parallelism for work-profile AI calls during early production use.
AI_WORK_PROFILE_MAX_CONCURRENT: int = 3

# Enough room for bounded JSON output describing total hours and normalized shape.
AI_WORK_PROFILE_MAX_TOKENS: int = 768

# Dynamic work-profile response sizing. Long-duration activities need one
# normalized bucket per day, so the completion budget scales with duration while
# staying bounded for very large activities.
WORK_PROFILE_BASE_TOKENS: int = 320
WORK_PROFILE_TOKENS_PER_DAY: int = 8
WORK_PROFILE_MAX_TOKENS_CAP: int = 4096


# ---------------------------------------------------------------------------
# Work-profile validation and maturity thresholds
# ---------------------------------------------------------------------------

# Final stored total_hours values must align to this scheduling unit.
WORK_PROFILE_OPERATIONAL_UNIT: float = 0.5

# Prevent tiny non-zero profiles that create phantom demand.
WORK_PROFILE_MIN_HOURS: float = 0.5

# Bayesian maturity thresholds. Lower coefficient of variation means the cache
# estimate is trusted enough to reuse without another AI call.
WORK_PROFILE_CV_CONFIRMED: float = 0.20
WORK_PROFILE_CV_TRUSTED: float = 0.10

# If corrections exceed 20% once enough evidence exists, treat the profile as
# insufficiently reliable regardless of posterior precision.
WORK_PROFILE_CORRECTION_RATE_THRESHOLD: float = 0.20
WORK_PROFILE_CORRECTION_MIN_SAMPLES: int = 3

# Observation uncertainty assumptions. Actuals remain much more trustworthy
# than AI estimates, so they move the posterior far more aggressively.
WORK_PROFILE_AI_ERROR_FRACTION: float = 0.20
WORK_PROFILE_ACTUAL_ERROR_FRACTION: float = 0.05

# Normalized distributions must be effectively exact before they are stored.
WORK_PROFILE_NORM_DIST_SUM_TOLERANCE: float = 1e-6


# ---------------------------------------------------------------------------
# Booking and calendar limits
# ---------------------------------------------------------------------------

# Ignore unusably short booking gaps when exposing available slots.
BOOKING_MIN_SLOT_DURATION_MINUTES: int = 30

# Bound expensive date-range queries on booking and calendar endpoints.
BOOKING_CALENDAR_MAX_DAYS: int = 90

# Default forward window for upcoming-bookings style queries.
UPCOMING_BOOKINGS_DEFAULT_DAYS_AHEAD: int = 7
UPCOMING_BOOKINGS_MAX_DAYS_AHEAD: int = 90


# ---------------------------------------------------------------------------
# File and preview limits
# ---------------------------------------------------------------------------

# Large enough for typical project uploads without inviting oversized binaries.
MAX_FILE_UPLOAD_SIZE_BYTES: int = 20 * 1024 * 1024

# Browser cache lifetime for served previews and derived images.
FILE_CACHE_MAX_AGE_SECONDS: int = 3600

# Preview uses a lighter render; full image endpoint prefers readability.
PDF_PREVIEW_SCALE: float = 1.5
PDF_IMAGE_SCALE: float = 3.0


# ---------------------------------------------------------------------------
# API pagination defaults
# ---------------------------------------------------------------------------

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


def get_active_asset_types(db: object) -> frozenset[str]:
    """Load active asset type codes from the DB taxonomy.

    Falls back to the bootstrap ALLOWED_ASSET_TYPES if the asset_types
    table doesn't exist yet or the query fails.

    Parameters
    ----------
    db : sqlalchemy.orm.Session
        An active database session.
    """
    try:
        from ..crud.asset_type import get_active_codes
        return get_active_codes(db)  # type: ignore[arg-type]
    except Exception as exc:
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
        _logger.warning(
            "Falling back to DEFAULT_MAX_HOURS_PER_DAY for '%s': %s", code, exc,
        )
    return DEFAULT_MAX_HOURS_PER_DAY.get(code, 16.0)
