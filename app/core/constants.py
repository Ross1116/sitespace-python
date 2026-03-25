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
    "loading_bay":   10.0,
    "ewp":           16.0,
    "concrete_pump": 10.0,
    "excavator":     16.0,
    "forklift":      16.0,
    "telehandler":   16.0,
    "compactor":     16.0,
    "other":         16.0,
    "none":           0.0,
}


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
