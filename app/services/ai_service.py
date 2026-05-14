"""
AI service — structure detection and asset classification.

Public interface:
  detect_structure(rows)  -> StructureResult
  classify_assets(activities) -> ClassificationResult
  suggest_subcontractor_asset_types(subcontractors) -> list[SubcontractorAssetSuggestion]
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Coroutine

import threading

import anthropic
try:
    import openai as _openai_module
except ImportError:
    _openai_module = None  # type: ignore[assignment]

from ..core.config import settings
from ..core.constants import (
    ALLOWED_ASSET_TYPES,
    AI_CLASSIFICATION_BATCH_MAX_TOKENS,
    AI_CLASSIFICATION_BATCH_SIZE,
    AI_CLASSIFICATION_MAX_CONCURRENT_BATCHES,
    AI_CLASSIFICATION_PARALLEL_THRESHOLD,
    AI_PROVIDER_MAX_CONCURRENT_REQUESTS,
    AI_PROVIDER_MIN_REQUEST_SPACING_SECONDS,
    AI_STANDALONE_TIMEOUT_BUFFER_SECONDS,
    AI_STRUCTURE_DETECTION_MAX_TOKENS,
    AI_STRUCTURE_DETECTION_SAMPLE_SIZE,
)

logger = logging.getLogger(__name__)
_USD_COST_QUANTUM = Decimal("0.000001")

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_AI_PROVIDER_REQUEST_SEMAPHORE = threading.BoundedSemaphore(
    max(1, AI_PROVIDER_MAX_CONCURRENT_REQUESTS)
)
_AI_PROVIDER_PACING_LOCK = threading.Lock()
_AI_PROVIDER_NEXT_REQUEST_AT = 0.0

# P6 and common programme day-step prefix stripped before name dedup.
# Matches: "Day 7 - ", "Day 14 – ", "Day 7-" etc.
# Character class explicitly covers hyphen, en-dash (U+2013), and em-dash (U+2014).
_DEDUP_PREFIX_RE = re.compile(r"^(?:day\s+\d+\s*[-\u2013\u2014]\s*)+", re.IGNORECASE)
_VALID_CLASSIFICATION_CONFIDENCES = frozenset({"low", "medium", "high"})
_VALID_ASSET_ROLES = frozenset({"lead", "support", "incidental"})
_VALID_PROFILE_SHAPES = frozenset(
    {
        "single_day",
        "flat",
        "front_loaded",
        "back_loaded",
        "bell",
        "inverse_bell",
        "staged",
    }
)


def _normalize_for_dedup(name: str) -> str:
    """Lowercase, strip P6 day-step prefix, collapse whitespace."""
    norm = _DEDUP_PREFIX_RE.sub("", name.strip().lower()).strip()
    return re.sub(r"\s{2,}", " ", norm)


def _normalize_classification_confidence(confidence: Any) -> str:
    """
    Accept only explicit high/medium/low confidence tokens.

    Unexpected or empty values degrade to low so downstream auto-commit logic
    never treats malformed AI responses as planning-ready.
    """
    token = str(confidence or "").strip().lower()
    if token in _VALID_CLASSIFICATION_CONFIDENCES:
        return token
    return "low"


def _confidence_tier_from_score(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return _normalize_classification_confidence(score)
    if value >= 0.75:
        return "high"
    if value >= 0.40:
        return "medium"
    return "low"


def _label_confidence_from_any(value: Any, fallback_tier: str = "medium") -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return {"high": 0.85, "medium": 0.55, "low": 0.25}.get(fallback_tier, 0.55)
    return max(0.0, min(1.0, score))


def _normalize_asset_role(role: Any) -> str:
    token = str(role or "").strip().lower()
    if token in _VALID_ASSET_ROLES:
        return token
    return "lead"


def _normalize_profile_shape(shape: Any) -> str | None:
    token = str(shape or "").strip().lower()
    return token if token in _VALID_PROFILE_SHAPES else None


def _coerce_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


# ---------------------------------------------------------------------------
# Stage 1 — row typing and row confidence helpers
# ---------------------------------------------------------------------------

# Milestone names often end with a parenthetical date or are a single short
# phrase with no duration.  We detect milestones structurally (zero duration
# + non-summary) rather than by name pattern to avoid false positives.

def classify_row_kind(
    *,
    is_summary: bool,
    start: str | None,
    finish: str | None,
) -> str:
    """
    Return 'summary', 'milestone', or 'task'.

    Rules (in priority order):
      1. is_summary=True → 'summary'
      2. Both dates present and start == finish (zero-duration row) → 'milestone'
      3. Everything else → 'task'
    """
    if is_summary:
        return "summary"
    if start and finish and start == finish:
        return "milestone"
    return "task"


def score_row_confidence(
    *,
    name: str,
    start: str | None,
    finish: str | None,
    activity_kind: str,
) -> str:
    """
    Return 'high', 'medium', or 'low' based on data completeness.

    Rules:
      - 'high':   name non-empty + both dates present (or milestone with at least one date)
      - 'medium': name non-empty + exactly one date present
      - 'low':    name missing/whitespace-only, OR both dates absent on a task/summary row
    """
    clean_name = name.strip()
    has_start = bool(start)
    has_finish = bool(finish)

    if not clean_name:
        return "low"

    if activity_kind == "milestone":
        # Milestones legitimately have start == finish; one date is fine.
        return "high" if (has_start or has_finish) else "medium"

    if has_start and has_finish:
        return "high"

    if has_start or has_finish:
        return "medium"

    # task/summary row with no dates at all
    return "low"


# ---------------------------------------------------------------------------
# Shared pct_complete parser
# ---------------------------------------------------------------------------

def parse_pct_raw(raw: object) -> int | None:
    """
    Parse a raw pct_complete value to an integer in 0–100.

    Detection rules (in order):
      - Token ends with '%'     → treat the numeric part as a percentage directly
      - Parsed float ≤ 1.0      → decimal fraction; multiply by 100 (e.g. 0.75 → 75)
      - Otherwise               → treat as a percentage already (e.g. 75 → 75)

    Returns None when the value is absent or cannot be parsed.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    has_pct_sign = s.endswith("%")
    s_clean = s.rstrip("%").strip()
    if not s_clean:
        return None
    try:
        val = float(s_clean)
    except (ValueError, TypeError):
        return None
    if has_pct_sign:
        pct = val
    elif val <= 1.0:
        pct = val * 100
    else:
        pct = val
    return max(0, min(100, int(round(pct))))


# ---------------------------------------------------------------------------
# Canonical asset type normalizer.
# Maps raw asset.type strings entered in the UI (mixed-case, varied phrasing)
# to the canonical ALLOWED_ASSET_TYPES values.  Used in two places:
#   1. _build_classification_prompt — so project-aware valid_types stays
#      compatible with the classifier and lookahead pipeline.
#   2. lookahead_engine — so booked-hours bucketing matches demand bucketing.
# ---------------------------------------------------------------------------
_CANONICAL_TYPE_KEYWORDS: list[tuple[str, str]] = [
    # (substring to match in lowercased raw type, canonical type)
    # Order: more specific first to avoid early short-circuit on "crane" in "tower crane"
    ("tower crane",       "crane"),
    ("mobile crane",      "crane"),
    ("luffing crane",     "crane"),
    ("crawler crane",     "crane"),
    ("pick-and-carry",    "crane"),
    ("pick and carry",    "crane"),
    ("crane",             "crane"),
    ("builder's hoist",   "hoist"),
    ("builders hoist",    "hoist"),
    ("personnel hoist",   "hoist"),
    ("materials hoist",   "hoist"),
    ("material hoist",    "hoist"),
    ("construction lift", "hoist"),
    ("hoist",             "hoist"),
    ("elevated work platform", "ewp"),
    ("scissor lift",      "ewp"),
    ("boom lift",         "ewp"),
    ("knuckle boom",      "ewp"),
    ("knuckle lift",      "ewp"),
    ("cherry picker",     "ewp"),
    ("man lift",          "ewp"),
    ("ewp",               "ewp"),
    ("loading bay",       "loading_bay"),
    ("unloading bay",     "loading_bay"),
    ("loading zone",      "loading_bay"),
    ("loading_bay",       "loading_bay"),
    ("boom pump",         "concrete_pump"),
    ("line pump",         "concrete_pump"),
    ("concrete pump",     "concrete_pump"),
    ("concrete_pump",     "concrete_pump"),
    ("kibble",            "concrete_pump"),
    ("mini excavator",    "excavator"),
    ("backhoe",           "excavator"),
    ("excavator",         "excavator"),
    ("digger",            "excavator"),
    ("rough terrain forklift", "forklift"),
    ("telehandler",           "telehandler"),
    ("telescopic handler",    "telehandler"),
    ("reach forklift",        "telehandler"),
    ("telescopic forklift",   "telehandler"),
    ("forklift",              "forklift"),
    ("plate compactor",   "compactor"),
    ("vibrating",         "compactor"),
    ("compactor",         "compactor"),
    ("roller",            "compactor"),
]


def normalize_asset_type(raw_type: str) -> str | None:
    """
    Map a raw asset.type string to a canonical ALLOWED_ASSET_TYPES value.

    Returns the canonical string (e.g. "crane") or None if no mapping is found
    (meaning the asset type is not a bookable construction plant — e.g. "Storage Area").
    None signals that this asset should not contribute to valid_types.
    """
    key = (raw_type or "").strip().lower()
    if not key:
        return None
    for substring, canonical in _CANONICAL_TYPE_KEYWORDS:
        if substring in key:
            return canonical
    return None


# ---------------------------------------------------------------------------
# Trade specialty → asset type heuristic map.
# Used to suggest which assets a subcontractor is likely to need based on
# their registered trade_specialty field.
# ---------------------------------------------------------------------------
TRADE_TO_ASSET_TYPES: dict[str, list[str]] = {
    "structural": ["crane", "hoist"],
    "steel erector": ["crane", "hoist"],
    "steel": ["crane", "hoist"],
    "rigger": ["crane", "hoist"],
    "crane operator": ["crane"],
    "hoist operator": ["hoist"],
    "concreter": ["concrete_pump"],
    "concrete": ["concrete_pump", "crane"],
    "formwork": ["crane", "ewp"],
    "precast": ["crane", "hoist"],
    "scaffolding": ["ewp"],
    "scaffolder": ["ewp"],
    "electrician": ["ewp", "loading_bay"],
    "electrical": ["ewp", "loading_bay"],
    "plumber": ["ewp", "loading_bay"],
    "plumbing": ["ewp", "loading_bay"],
    "hvac": ["ewp", "crane"],
    "mechanical": ["ewp", "crane"],
    "glazier": ["crane", "ewp"],
    "facade": ["crane", "ewp"],
    "cladding": ["crane", "ewp"],
    "curtain wall": ["crane", "ewp"],
    "excavation": ["excavator"],
    "earthworks": ["excavator", "compactor"],
    "civil": ["excavator", "compactor"],
    "demolition": ["excavator", "crane"],
    "landscaping": ["compactor", "excavator"],
    "carpenter": ["forklift", "loading_bay"],
    "joinery": ["forklift", "loading_bay"],
    "fit-out": ["forklift", "loading_bay", "ewp"],
    "fitout": ["forklift", "loading_bay", "ewp"],
    "painting": ["ewp"],
    "roofing": ["crane", "ewp"],
    "waterproofing": ["ewp"],
    "tiling": ["loading_bay"],
    "flooring": ["forklift", "loading_bay"],
    "general": ["loading_bay", "forklift"],
}


# ---------------------------------------------------------------------------
# Return type contracts
# ---------------------------------------------------------------------------

@dataclass
class ActivityItem:
    """Single activity as returned by detect_structure()."""
    id: str                          # temp row identifier used to build parent_id refs
    name: str
    start: str | None                # ISO date string "YYYY-MM-DD" or None
    finish: str | None
    parent_id: str | None
    is_summary: bool
    level_name: str | None
    zone_name: str | None
    # Stage 1 correctness fields (may be None when not available in source file)
    pct_complete: int | None = None  # 0–100 extracted from source file
    activity_kind: str | None = None # 'summary' | 'task' | 'milestone'
    row_confidence: str | None = None  # 'high' | 'medium' | 'low'
    duration_days: int | None = None


@dataclass
class StructureResult:
    """
    Output of detect_structure().

    column_mapping keys: name, start_date, end_date, duration, wbs_code,
                         resource, level_indicator, or "unknown"
    completeness_score: int 0–100 (converted to float 0.0–1.0 on DB write)
    """
    column_mapping: dict[str, str]
    activities: list[ActivityItem]
    completeness_score: int          # 0–100
    missing_fields: list[str]
    notes: str
    ai_tokens_used: int = 0
    ai_cost_usd: Decimal | None = None


@dataclass
class AssetRequirementItem:
    """Single asset requirement result within ClassificationResult."""
    activity_id: str                 # UUID string of programme_activity.id
    asset_type: str                  # must be in ALLOWED_ASSET_TYPES
    confidence: str                  # "high" | "medium" | "low"
    source: str                      # "ai" | "keyword" | "manual"
    label_confidence: float | None = None
    asset_role: str | None = None    # "lead" | "support" | "incidental"
    estimated_total_hours: float | None = None
    profile_shape: str | None = None
    reasoning: str | None = None


ClassificationItem = AssetRequirementItem


@dataclass(init=False)
class ClassificationResult:
    """
    Output of classify_assets().

    requirements: one row per asset requirement.  Low confidence requirements
                  are real rows, but the writer stores them uncommitted.
    skipped:      no-demand/unresolved activity ids. These create no demand rows.
    fallback_used:   True when AI was unavailable and keyword-only fallback ran
    """
    requirements: list[AssetRequirementItem]
    skipped: list[str]
    batch_tokens_used: int = 0
    batch_cost_usd: Decimal | None = None
    fallback_used: bool = False

    def __init__(
        self,
        requirements: list[AssetRequirementItem] | None = None,
        skipped: list[str] | None = None,
        batch_tokens_used: int = 0,
        batch_cost_usd: Decimal | None = None,
        fallback_used: bool = False,
        classifications: list[AssetRequirementItem] | None = None,
    ) -> None:
        self.requirements = list(requirements if requirements is not None else classifications or [])
        self.skipped = list(skipped or [])
        self.batch_tokens_used = batch_tokens_used
        self.batch_cost_usd = batch_cost_usd
        self.fallback_used = fallback_used

    @property
    def classifications(self) -> list[AssetRequirementItem]:
        """Compatibility alias while call sites move to requirements."""
        return self.requirements

    @classifications.setter
    def classifications(self, value: list[AssetRequirementItem]) -> None:
        self.requirements = value


@dataclass(frozen=True)
class AIUsage:
    """Token and cost accounting for a single upstream AI call."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal | None = None


@dataclass
class AIExecutionContext:
    """Per-upload AI execution state shared across detection, classification, and profiling."""

    suppress_ai: bool = False
    quota_exhausted: bool = False
    quota_error_count: int = 0

    def mark_quota_exhausted(self) -> None:
        self.quota_exhausted = True
        self.suppress_ai = True
        self.quota_error_count += 1


def _configured_token_price(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def estimate_ai_cost_usd(
    input_tokens: int,
    output_tokens: int,
) -> Decimal | None:
    """Return USD cost for one AI call when pricing has been configured."""
    input_rate = _configured_token_price(settings.AI_INPUT_COST_PER_MILLION_USD)
    output_rate = _configured_token_price(settings.AI_OUTPUT_COST_PER_MILLION_USD)
    if input_rate is None or output_rate is None:
        return None

    total_cost = (
        (Decimal(max(0, input_tokens)) * input_rate)
        + (Decimal(max(0, output_tokens)) * output_rate)
    ) / Decimal("1000000")
    return total_cost.quantize(_USD_COST_QUANTUM, rounding=ROUND_HALF_UP)


def build_ai_usage(
    input_tokens: int,
    output_tokens: int,
) -> AIUsage:
    """Build a normalized AI usage object from provider token counters."""
    safe_input = max(0, int(input_tokens or 0))
    safe_output = max(0, int(output_tokens or 0))
    return AIUsage(
        input_tokens=safe_input,
        output_tokens=safe_output,
        total_tokens=safe_input + safe_output,
        cost_usd=estimate_ai_cost_usd(safe_input, safe_output),
    )


def coerce_ai_usage(value: AIUsage | int | None) -> AIUsage:
    """Normalize legacy total-token mocks to the structured AIUsage shape."""
    if isinstance(value, AIUsage):
        return value
    return build_ai_usage(int(value or 0), 0)


def sum_ai_costs(*values: Decimal | None) -> Decimal | None:
    """Add nullable Decimal costs, preserving None when every value is unknown."""
    known = [value for value in values if value is not None]
    if not known:
        return None
    return sum(known, Decimal("0")).quantize(_USD_COST_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass
class SubcontractorAssetSuggestion:
    """
    Suggested asset types for a subcontractor based on their trade_specialty.
    Used by the lookahead planning feature to pre-assign likely asset needs.
    """
    subcontractor_id: str
    trade_specialty: str
    suggested_asset_types: list[str]


_CURRENT_AI_EXECUTION_CONTEXT: ContextVar[AIExecutionContext | None] = ContextVar(
    "sitespace_ai_execution_context",
    default=None,
)


def get_current_ai_execution_context() -> AIExecutionContext | None:
    return _CURRENT_AI_EXECUTION_CONTEXT.get()


def _resolve_ai_execution_context(
    execution_context: AIExecutionContext | None = None,
) -> AIExecutionContext | None:
    return execution_context or get_current_ai_execution_context()


@contextmanager
def bind_ai_execution_context(execution_context: AIExecutionContext):
    """Bind a shared execution context for all AI calls made in this upload flow."""
    token = _CURRENT_AI_EXECUTION_CONTEXT.set(execution_context)
    try:
        yield execution_context
    finally:
        _CURRENT_AI_EXECUTION_CONTEXT.reset(token)


# ---------------------------------------------------------------------------
# Public interface — called by process_programme.py orchestrator
# ---------------------------------------------------------------------------

async def detect_structure(
    rows: list[dict[str, Any]],
    *,
    execution_context: AIExecutionContext | None = None,
) -> StructureResult:
    """
    Analyse the first 50–100 rows of a programme file and return:
      - column_mapping: header → semantic field name
      - activities:     parsed activity tree
      - completeness_score: int 0–100

    Falls back to regex heuristics if AI is disabled or fails.
    Never raises — always returns a StructureResult (possibly degraded).
    """
    execution_context = _resolve_ai_execution_context(execution_context)

    if not settings.AI_ENABLED:
        logger.info("AI_ENABLED=false — using regex fallback for structure detection")
        return _detect_structure_fallback(rows)
    if execution_context is not None and execution_context.suppress_ai:
        logger.info("AI suppressed — using regex fallback for structure detection")
        return _detect_structure_fallback(rows)

    try:
        return await _detect_structure_real(rows, execution_context=execution_context)
    except Exception as exc:
        logger.warning("AI structure detection failed (%s) — falling back to regex", exc)
        return _detect_structure_fallback(rows)


async def classify_assets(
    activities: list[dict[str, Any]],
    project_assets: list[dict[str, Any]] | None = None,
    *,
    execution_context: AIExecutionContext | None = None,
) -> ClassificationResult:
    """
    Classify a batch of activities by asset type.
    Returns high + medium in classifications[], low-confidence in skipped[].

    project_assets: list of {"name": str, "type": str, "code": str} dicts for
    assets actually registered on this project.  When provided the AI prompt
    and keyword pre-screen are scoped to those assets rather than the generic
    hardcoded list.

    Falls back to keyword-only classification if AI is disabled or fails.
    Never raises.
    """
    execution_context = _resolve_ai_execution_context(execution_context)

    if not settings.AI_ENABLED:
        logger.info("AI_ENABLED=false — using keyword fallback for classification")
        return _classify_assets_fallback(activities, project_assets=project_assets)
    if execution_context is not None and execution_context.suppress_ai:
        logger.info("AI suppressed — using keyword fallback for classification")
        return _classify_assets_fallback(activities, project_assets=project_assets)

    try:
        return await _classify_assets_real(
            activities,
            project_assets=project_assets,
            execution_context=execution_context,
        )
    except Exception as exc:
        logger.warning("AI classification failed (%s) — falling back to keyword", exc)
        return _classify_assets_fallback(activities, project_assets=project_assets)


def classify_item_standalone(
    activity_name: str,
    valid_types: frozenset[str],
) -> tuple[str, str] | None:
    """
    Classify a single activity name against the active asset type taxonomy.

    Used by the classification service when neither an active classification nor
    a keyword match exists for an item.  Runs _classify_batch with a synthetic
    single-item list in a dedicated thread so it is safe to call from within a
    running async context (asyncio.run() cannot be used there).

    Returns (asset_type, confidence) for high/medium results, or None on
    failure / low-confidence / AI disabled.  Never raises.
    """
    execution_context = get_current_ai_execution_context()
    if not settings.AI_ENABLED:
        return None
    if execution_context is not None and execution_context.suppress_ai:
        return None

    fake_id = str(uuid.uuid4())
    batch = [{"id": fake_id, "name": activity_name}]
    # Build the system prompt from the caller-supplied taxonomy so the prompt
    # and the post-filter below agree on which types are valid.
    _base = _load_prompt("asset_classification.txt")
    if "{{ASSET_TYPES_BLOCK}}" not in _base:
        _base = _base + "\n\n" + _DEFAULT_ASSET_TYPES_BLOCK
    _types_block = (
        "ALLOWED ASSET TYPES (use ONLY these exact strings):\n"
        + "\n".join(f"  - {t}" for t in sorted(valid_types))
    )
    system_prompt = _base.replace("{{ASSET_TYPES_BLOCK}}", _types_block)

    # Run the async helper in a dedicated thread with its own event loop so this
    # sync function is safe to call from within a running async context (e.g. the
    # process_programme pipeline).  asyncio.run() cannot be called when a loop is
    # already running, so we isolate it completely.
    # A fresh client is created inside the thread because the module-level
    # AsyncAnthropic singleton has an httpx connection pool tied to the main event
    # loop and is not safe to reuse from a different loop.
    import concurrent.futures

    def _run_in_thread() -> tuple[str, str] | None:
        if not settings.AI_API_KEY:
            return None
        provider = settings.AI_PROVIDER.lower()
        if provider == "openai":
            if _openai_module is None:
                return None
            thread_client = _openai_module.AsyncOpenAI(api_key=settings.AI_API_KEY, max_retries=0)
        else:
            thread_client = anthropic.AsyncAnthropic(api_key=settings.AI_API_KEY, max_retries=0)

        async def _run() -> tuple[str, str] | None:
            # Use the client as an async context manager so its connection pool is
            # closed before the event loop is torn down.
            async with thread_client:
                try:
                    raw = await _classify_batch(
                        batch,
                        system_prompt,
                        thread_client,
                        execution_context=execution_context,
                    )
                except Exception as exc:
                    logger.warning("classify_item_standalone AI call failed: %s", exc)
                    return None
                for item in raw.get("requirements") or raw.get("classifications") or []:
                    if str(item.get("activity_id")) == fake_id:
                        asset_type = str(item.get("asset_type") or "").strip().lower()
                        confidence = _confidence_tier_from_score(item.get("confidence"))
                        if asset_type in valid_types and confidence in {"high", "medium"}:
                            return asset_type, confidence
                return None

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_run_in_thread)
        return future.result(
            timeout=float(settings.AI_TIMEOUT_CLASSIFY) + AI_STANDALONE_TIMEOUT_BUFFER_SECONDS
        )
    except concurrent.futures.TimeoutError:
        logger.warning(
            "classify_item_standalone timed out after %ss",
            float(settings.AI_TIMEOUT_CLASSIFY) + AI_STANDALONE_TIMEOUT_BUFFER_SECONDS,
        )
        return None
    except Exception as exc:
        logger.warning("classify_item_standalone failed: %s", exc)
        return None
    finally:
        # shutdown(wait=False) avoids blocking the caller while the background
        # thread unwinds; the thread itself will clean up when it finishes.
        executor.shutdown(wait=False)


def suggest_subcontractor_asset_types(
    subcontractors: list[dict[str, str]],
) -> list[SubcontractorAssetSuggestion]:
    """
    Given a list of subcontractors with their trade_specialty, return the
    asset types each one is likely to need based on known trade-to-asset heuristics.

    This is the core of the lookahead planning AI feature: using the subcontractor's
    registered trade type to predict which bookable assets they will need.

    Args:
        subcontractors: list of dicts with 'id' and 'trade_specialty' keys.

    Returns:
        list of SubcontractorAssetSuggestion, one per subcontractor.
    """
    suggestions: list[SubcontractorAssetSuggestion] = []
    for sub in subcontractors:
        sub_id = str(sub.get("id", ""))
        specialty = str(sub.get("trade_specialty") or "").lower().strip()
        asset_types = _lookup_trade_asset_types(specialty)
        suggestions.append(SubcontractorAssetSuggestion(
            subcontractor_id=sub_id,
            trade_specialty=specialty,
            suggested_asset_types=asset_types,
        ))
    return suggestions


# ---------------------------------------------------------------------------
# AI implementation helpers
# ---------------------------------------------------------------------------

def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts/ directory."""
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


# Module-level singleton clients — created once, reused across all calls.
# Creating a new AsyncOpenAI/AsyncAnthropic per call spawns a new httpx
# connection pool each time; when many parallel batches complete they all race
# to close their pools concurrently, producing TCPTransport closed errors and
# unnecessary 429s from opening too many simultaneous connections.
_async_client: anthropic.AsyncAnthropic | Any | None = None
_async_client_lock = threading.Lock()


def _get_async_client() -> anthropic.AsyncAnthropic | Any:
    """Return the module-level singleton async client for the configured AI provider."""
    global _async_client
    if _async_client is not None:
        return _async_client

    with _async_client_lock:
        # Double-check after acquiring lock
        if _async_client is not None:
            return _async_client

        if not settings.AI_API_KEY:
            raise ValueError("AI_API_KEY is not configured")

        provider = settings.AI_PROVIDER.lower()
        if provider == "openai":
            if _openai_module is None:
                raise ImportError("openai package is not installed — add 'openai' to requirements.txt")
            _async_client = _openai_module.AsyncOpenAI(
                api_key=settings.AI_API_KEY,
                max_retries=3,  # built-in exponential back-off for 429/5xx
            )
        else:
            _async_client = anthropic.AsyncAnthropic(
                api_key=settings.AI_API_KEY,
                max_retries=3,
            )

        logger.info("AI async client created (provider=%s)", provider)
        return _async_client


def _is_openai_client(client: Any) -> bool:
    return _openai_module is not None and isinstance(client, _openai_module.AsyncOpenAI)


def _reserve_provider_start_delay() -> float:
    """Reserve the next provider start time and return required delay in seconds."""
    global _AI_PROVIDER_NEXT_REQUEST_AT

    spacing = max(0.0, AI_PROVIDER_MIN_REQUEST_SPACING_SECONDS)
    if spacing <= 0:
        return 0.0

    with _AI_PROVIDER_PACING_LOCK:
        now = time.monotonic()
        start_at = max(now, _AI_PROVIDER_NEXT_REQUEST_AT)
        _AI_PROVIDER_NEXT_REQUEST_AT = start_at + spacing
        return max(0.0, start_at - now)


async def _acquire_provider_request_slot() -> None:
    """
    Apply process-wide back-pressure before hitting the upstream AI provider.

    Uses a thread-safe semaphore so standalone per-thread event loops are
    throttled together with the main async pipeline.
    """
    permit_acquired = False
    try:
        await asyncio.to_thread(_AI_PROVIDER_REQUEST_SEMAPHORE.acquire)
        permit_acquired = True
        delay = _reserve_provider_start_delay()
        if delay > 0:
            logger.debug("AI provider pacing delay %.3fs", delay)
            await asyncio.sleep(delay)
    except BaseException:
        if permit_acquired:
            _AI_PROVIDER_REQUEST_SEMAPHORE.release()
        raise


def _release_provider_request_slot() -> None:
    _AI_PROVIDER_REQUEST_SEMAPHORE.release()


async def _call_api(
    client: Any,
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    timeout: float,
    execution_context: AIExecutionContext | None = None,
) -> tuple[str, AIUsage]:
    """
    Unified async call — returns (text_content, usage) regardless of provider.

    Quota/billing errors (OpenAI insufficient_quota, Anthropic credit exhausted) are
    re-raised immediately as RuntimeError so the SDK's built-in retry loop doesn't
    waste minutes retrying a permanent billing error.
    """
    execution_context = _resolve_ai_execution_context(execution_context)
    if execution_context is not None and execution_context.suppress_ai:
        raise RuntimeError("AI suppressed for this upload")

    await _acquire_provider_request_slot()
    execution_context = _resolve_ai_execution_context(execution_context)
    try:
        if execution_context is not None and execution_context.suppress_ai:
            raise RuntimeError("AI suppressed for this upload")
        try:
            if _is_openai_client(client):
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=settings.AI_MODEL,
                        max_tokens=max_tokens,
                        temperature=0,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                    ),
                    timeout=timeout,
                )
                content = response.choices[0].message.content if response.choices else None
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "prompt_tokens", None) or 0
                output_tokens = getattr(usage, "completion_tokens", None) or 0
            else:
                response = await asyncio.wait_for(
                    client.messages.create(
                        model=settings.AI_MODEL,
                        max_tokens=max_tokens,
                        temperature=0,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_message}],
                    ),
                    timeout=timeout,
                )
                content = response.content[0].text if response.content else None
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "input_tokens", None) or 0
                output_tokens = getattr(usage, "output_tokens", None) or 0
        except Exception as exc:
            # Permanent billing/quota errors — skip retries and fail fast.
            # OpenAI:    RateLimitError with code="insufficient_quota"
            # Anthropic: APIStatusError with status 400 + "credit balance is too low"
            err_code = getattr(exc, "code", None) or ""
            err_body = str(getattr(exc, "message", "") or exc)
            is_quota = (
                err_code == "insufficient_quota"
                or "insufficient_quota" in err_body
                or "credit balance is too low" in err_body
                or "exceeded your current quota" in err_body
            )
            if is_quota:
                if execution_context is not None:
                    execution_context.mark_quota_exhausted()
                logger.error("AI provider quota/billing limit reached — disabling AI for this request: %s", exc)
                raise RuntimeError(f"AI quota exhausted: {exc}") from exc
            raise
    finally:
        _release_provider_request_slot()

    if not content:
        raise ValueError("Empty or malformed API response")
    return content, build_ai_usage(input_tokens, output_tokens)


def _parse_json_response(text: str) -> dict[str, Any]:
    """
    Robustly extract and parse JSON from a Claude response.
    Handles: bare JSON, markdown code fences, JSON embedded in prose.
    """
    text = text.strip()

    # Fast path: pure JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Markdown code fence: ```json {...} ```
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Last resort: balanced-brace extraction to handle nested objects correctly
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"Could not extract valid JSON from response: {text[:300]!r}")


def _build_activities_from_rows(
    rows: list[dict[str, Any]],
    column_mapping: dict[str, str],
) -> list[ActivityItem]:
    """
    Build ActivityItem list from raw rows using the detected column_mapping.
    Handles id, parent_id, is_summary, level_name, zone_name when mapped.
    Populates Stage 1 correctness fields: activity_kind, row_confidence.
    """
    name_col = column_mapping.get("name")
    start_col = column_mapping.get("start_date")
    end_col = column_mapping.get("end_date")
    id_col = column_mapping.get("id") or column_mapping.get("wbs_code")
    parent_col = column_mapping.get("parent_id")
    summary_col = column_mapping.get("is_summary")
    level_col = column_mapping.get("level_name") or column_mapping.get("level_indicator")
    zone_col = column_mapping.get("zone_name")
    pct_col = column_mapping.get("pct_complete")

    activities: list[ActivityItem] = []

    for i, row in enumerate(rows):
        name_raw = row.get(name_col) if name_col else None
        name = str(name_raw).strip() if name_raw is not None else ""
        if not name:
            continue

        id_raw = row.get(id_col) if id_col else None
        activity_id = str(id_raw).strip() if id_raw is not None else f"row-{i}"

        start_raw = row.get(start_col) if start_col else None
        end_raw = row.get(end_col) if end_col else None
        parent_raw = row.get(parent_col) if parent_col else None
        level_raw = row.get(level_col) if level_col else None
        zone_raw = row.get(zone_col) if zone_col else None

        is_summary = False
        if summary_col:
            summary_raw = row.get(summary_col)
            if summary_raw is not None:
                is_summary = str(summary_raw).strip().lower() in {"1", "true", "yes", "y", "t"}

        start_str = str(start_raw).strip() if start_raw is not None else None
        finish_str = str(end_raw).strip() if end_raw is not None else None

        pct_complete = parse_pct_raw(row.get(pct_col) if pct_col else None)

        activity_kind = classify_row_kind(
            is_summary=is_summary,
            start=start_str,
            finish=finish_str,
        )
        row_confidence = score_row_confidence(
            name=name,
            start=start_str,
            finish=finish_str,
            activity_kind=activity_kind,
        )

        activities.append(ActivityItem(
            id=activity_id,
            name=name,
            start=start_str,
            finish=finish_str,
            parent_id=str(parent_raw).strip() if parent_raw is not None else None,
            is_summary=is_summary,
            level_name=str(level_raw).strip() if level_raw is not None else None,
            zone_name=str(zone_raw).strip() if zone_raw is not None else None,
            pct_complete=pct_complete,
            activity_kind=activity_kind,
            row_confidence=row_confidence,
        ))

    return activities


# ---------------------------------------------------------------------------
# Real AI implementations — Claude API calls
# ---------------------------------------------------------------------------

async def _detect_structure_real(
    rows: list[dict[str, Any]],
    *,
    execution_context: AIExecutionContext | None = None,
) -> StructureResult:
    """
    Call Claude to detect column structure of a programme file.
    Uses structure_detection.txt system prompt.
    Hard timeout: settings.AI_TIMEOUT_STRUCTURE seconds.
    """
    client = _get_async_client()
    system_prompt = _load_prompt("structure_detection.txt")

    sample = rows[:AI_STRUCTURE_DETECTION_SAMPLE_SIZE]
    user_message = (
        f"Here are the first {len(sample)} rows from a construction programme file. "
        "Identify the column structure. Return ONLY valid JSON.\n\n"
        f"ROWS:\n{json.dumps(sample, default=str)}"
    )

    text, usage = await _call_api(
        client,
        system_prompt,
        user_message,
        max_tokens=AI_STRUCTURE_DETECTION_MAX_TOKENS,
        timeout=float(settings.AI_TIMEOUT_STRUCTURE),
        execution_context=execution_context,
    )
    try:
        data = _parse_json_response(text)

        # Extract mapping, dropping null values
        raw_mapping = data.get("column_mapping") or {}
        column_mapping = {
            k: str(v).strip()
            for k, v in raw_mapping.items()
            if v is not None and str(v).strip()
        }

        activities = _build_activities_from_rows(rows, column_mapping)

        completeness_score = max(0, min(100, int(data.get("completeness_score") or 50)))
        missing_fields: list[str] = list(data.get("missing_fields") or [])

        notes_parts: list[str] = []
        if data.get("notes"):
            notes_parts.append(str(data["notes"]))
        if data.get("detected_tool") and str(data["detected_tool"]).lower() != "unknown":
            notes_parts.append(f"Detected: {data['detected_tool']}")
        if data.get("date_format_hint"):
            notes_parts.append(f"Date format: {data['date_format_hint']}")
        notes = " | ".join(notes_parts) if notes_parts else "AI structure detection complete."

        logger.info(
            "Structure detection: completeness=%d, activities=%d, mapping_keys=%s",
            completeness_score,
            len(activities),
            list(column_mapping.keys()),
        )

        return StructureResult(
            column_mapping=column_mapping,
            activities=activities,
            completeness_score=completeness_score,
            missing_fields=missing_fields,
            notes=notes,
            ai_tokens_used=usage.total_tokens,
            ai_cost_usd=usage.cost_usd,
        )
    except Exception as exc:
        logger.warning("AI structure response processing failed; using regex fallback: %s", exc)
        fallback = _detect_structure_fallback(rows)
        fallback.notes = f"{fallback.notes} AI structure response invalid; regex fallback used."
        fallback.ai_tokens_used = usage.total_tokens
        fallback.ai_cost_usd = usage.cost_usd
        return fallback


def _extract_partial_classifications(text: str) -> list[dict[str, Any]]:
    """
    Last-resort extraction for truncated classification responses.

    Supports both legacy classification rows and the multi-asset requirement
    shape with numeric confidence and optional source.
    """
    object_pattern = re.compile(
        r'\{[^{}]*(?:"activity_id"[^{}]*"asset_type"|"asset_type"[^{}]*"activity_id")[^{}]*\}',
        re.DOTALL,
    )

    def _extract_string(obj: str, field: str) -> str | None:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*"(?P<value>[^"]+)"', obj)
        return match.group("value") if match else None

    def _extract_scalar(obj: str, field: str) -> str | float | None:
        match = re.search(
            rf'"{re.escape(field)}"\s*:\s*(?:"(?P<quoted>[^"]+)"|(?P<number>-?\d+(?:\.\d+)?))',
            obj,
        )
        if not match:
            return None
        if match.group("quoted") is not None:
            return match.group("quoted")
        return float(match.group("number"))

    partial: list[dict[str, Any]] = []
    for match in object_pattern.finditer(text):
        obj = match.group(0)
        activity_id = _extract_string(obj, "activity_id")
        asset_type = _extract_string(obj, "asset_type")
        if not activity_id or not asset_type:
            continue
        row: dict[str, Any] = {
            "activity_id": activity_id,
            "asset_type": asset_type,
            "source": _extract_string(obj, "source") or "ai",
        }
        confidence = _extract_scalar(obj, "label_confidence")
        if confidence is None:
            confidence = _extract_scalar(obj, "confidence")
        if confidence is not None:
            row["confidence"] = confidence
        for field in ("asset_role", "role", "profile_shape", "reasoning"):
            value = _extract_string(obj, field)
            if value is not None:
                row[field] = value
        hours = _extract_scalar(obj, "estimated_total_hours")
        if hours is None:
            hours = _extract_scalar(obj, "estimated_hours")
        if hours is not None:
            row["estimated_total_hours"] = hours
        partial.append(row)
    return partial


async def _classify_batch(
    batch: list[dict[str, Any]],
    system_prompt: str,
    client: Any,
    *,
    execution_context: AIExecutionContext | None = None,
) -> dict[str, Any]:
    """
    Send a single batch of up to 50 activities to the configured AI provider for classification.
    Returns dict with 'classifications', 'skipped', and 'tokens_used'.
    Hard timeout: settings.AI_TIMEOUT_CLASSIFY seconds.
    """
    user_message = (
        f"Classify these {len(batch)} construction programme activities. "
        "Return ONLY valid JSON.\n\n"
        f"ACTIVITIES:\n{json.dumps(batch, ensure_ascii=False)}"
    )

    text, usage = await _call_api(
        client,
        system_prompt,
        user_message,
        max_tokens=AI_CLASSIFICATION_BATCH_MAX_TOKENS,
        timeout=float(settings.AI_TIMEOUT_CLASSIFY),
        execution_context=execution_context,
    )
    try:
        data = _parse_json_response(text)
        requirements: list[dict[str, Any]] = []
        for item in data.get("requirements") or []:
            if isinstance(item, dict):
                requirements.append(item)
        for item in data.get("asset_requirements") or []:
            if isinstance(item, dict) and item.get("activity_id"):
                requirements.append(item)
        for item in data.get("classifications") or []:
            if isinstance(item, dict):
                requirements.append(item)
        return {
            "requirements": requirements,
            "classifications": requirements,
            "skipped": list(data.get("skipped") or []),
            "tokens_used": usage.total_tokens,
            "cost_usd": usage.cost_usd,
        }
    except ValueError:
        # Response was truncated or malformed — attempt field-by-field rescue
        partial = _extract_partial_classifications(text)
        if partial:
            logger.warning(
                "Batch response truncated — rescued %d/%d classifications via partial extraction",
                len(partial),
                len(batch),
            )
            return {
                "requirements": partial,
                "classifications": partial,
                "skipped": [],
                "tokens_used": usage.total_tokens,
                "cost_usd": usage.cost_usd,
            }
        raise


_DEFAULT_ASSET_TYPES_BLOCK = (
    "ALLOWED ASSET TYPES (use ONLY these exact strings):\n"
    "  - crane          → Tower crane, mobile crane, luffing crane, pick-and-carry\n"
    "  - hoist          → Builder's hoist, personnel/materials hoist, construction lift\n"
    "  - loading_bay    → Dedicated loading bay / unloading zone at building perimeter\n"
    "  - ewp            → Elevated work platform — scissor lift, boom lift, knuckle boom\n"
    "  - concrete_pump  → Boom pump, line pump, concrete kibble\n"
    "  - excavator      → Excavator, backhoe, mini excavator\n"
    "  - forklift       → Forklift, rough terrain forklift (dedicated forklift only)\n"
    "  - telehandler    → Telehandler, reach forklift, telescopic handler\n"
    "  - compactor      → Plate compactor, roller, vibrating compactor\n"
    "  - other          → Any asset need that doesn't fit above\n"
    "  - none           → Activity genuinely requires no bookable site asset"
)


def _build_classification_prompt(
    project_assets: list[dict[str, Any]] | None,
) -> tuple[str, frozenset[str]]:
    """
    Build the system prompt and valid-type set for asset classification.

    When project_assets are provided the {{ASSET_TYPES_BLOCK}} placeholder in
    the base prompt is replaced with the project's registered assets so the AI
    classifies against what is actually on site.

    Returns (system_prompt, valid_types_frozenset).
    """
    base_prompt = _load_prompt("asset_classification.txt")

    if "{{ASSET_TYPES_BLOCK}}" not in base_prompt:
        logger.warning(
            "asset_classification.txt is missing {{ASSET_TYPES_BLOCK}} placeholder — "
            "falling back to default asset types block"
        )
        base_prompt = base_prompt + "\n\n" + _DEFAULT_ASSET_TYPES_BLOCK

    if not project_assets:
        return base_prompt.replace("{{ASSET_TYPES_BLOCK}}", _DEFAULT_ASSET_TYPES_BLOCK), ALLOWED_ASSET_TYPES

    # Normalise: deduplicate by (name, type) and map each asset's type to a
    # canonical ALLOWED_ASSET_TYPES value.  Assets whose type doesn't map to a
    # canonical type (e.g. "Storage Area") are listed for context but excluded
    # from valid_types so the classifier cannot emit them — they are not
    # bookable plant and would be silently filtered by the lookahead anyway.
    seen: set[tuple[str, str]] = set()
    asset_lines: list[str] = []
    valid_types: set[str] = set()

    for a in project_assets:
        raw_name = str(a.get("name") or "").strip()
        raw_type = str(a.get("type") or "").strip()
        code = str(a.get("code") or "").strip()
        if not raw_name:
            continue
        key = (raw_name.lower(), raw_type.lower())
        if key in seen:
            continue
        seen.add(key)

        # Prefer pre-computed canonical_type (Stage 3) when available.
        canonical = str(a.get("canonical_type") or "").strip() or None
        if not canonical:
            canonical = normalize_asset_type(raw_type)
        if canonical is None:
            # Type is generic (e.g. "EQUIPMENT") — fall back to the asset name.
            # Covers cases like Forklift/EQUIPMENT or Excavator/EQUIPMENT.
            canonical = normalize_asset_type(raw_name)
        if canonical and canonical != "none":
            valid_types.add(canonical)

        description = str(a.get("description") or "").strip()
        label = f"- {raw_name}"
        if raw_type:
            label += f" (type: {raw_type})"
        if canonical:
            label += f" -> {canonical}"
        if code:
            label += f" [code: {code}]"
        if description:
            label += f": {description}"
        asset_lines.append(label)

    # If no project assets mapped to canonical types, fall back to the full
    # default set so classification still produces useful output.
    if not valid_types:
        logger.warning(
            "No project assets mapped to canonical types — falling back to default asset types"
        )
        return base_prompt.replace("{{ASSET_TYPES_BLOCK}}", _DEFAULT_ASSET_TYPES_BLOCK), ALLOWED_ASSET_TYPES

    # Always keep "none" and "other" available.
    valid_types.update({"none", "other"})
    valid_types_frozen = frozenset(valid_types)

    # Build the canonical type lines for types actually present on this project.
    _TYPE_DESCRIPTIONS: dict[str, str] = {
        "crane":         "crane          → Tower crane, mobile crane, luffing crane, pick-and-carry",
        "hoist":         "hoist          → Builder's hoist, personnel/materials hoist, construction lift",
        "loading_bay":   "loading_bay    → Dedicated loading bay / unloading zone at building perimeter",
        "ewp":           "ewp            → Elevated work platform — scissor lift, boom lift, knuckle boom",
        "concrete_pump": "concrete_pump  → Boom pump, line pump, concrete kibble",
        "excavator":     "excavator      → Excavator, backhoe, mini excavator",
        "forklift":      "forklift       → Forklift, rough terrain forklift",
        "telehandler":   "telehandler    → Telehandler, reach forklift, telescopic handler",
        "compactor":     "compactor      → Plate compactor, roller, vibrating compactor",
        "other":         "other          → Any asset need that doesn't fit the types above",
        "none":          "none           → Activity genuinely requires no bookable site asset",
    }
    local_descriptions: dict[str, str] = {}
    for a in project_assets:
        canonical = str(a.get("canonical_type") or "").strip()
        if canonical and canonical not in _TYPE_DESCRIPTIONS:
            label = str(a.get("type") or a.get("name") or canonical).strip()
            description = str(a.get("description") or "").strip()
            local_descriptions[canonical] = (
                f"{canonical:<14} -> {label}: {description}" if description else f"{canonical:<14} -> {label}"
            )

    type_lines = "\n".join(
        f"  - {_TYPE_DESCRIPTIONS.get(t, local_descriptions.get(t, t))}"
        for t in sorted(valid_types)
        if t in _TYPE_DESCRIPTIONS or t in local_descriptions
    )

    asset_block = (
        "THIS PROJECT'S REGISTERED ASSETS (for context — use to guide classification):\n"
        + "\n".join(asset_lines)
        + "\n\n"
        "ALLOWED ASSET TYPES for this project (use ONLY these exact strings in \"asset_type\"):\n"
        + type_lines
        + "\n\n"
        "Classify each activity into one of the canonical types above based on what "
        "the work actually requires. Use the registered asset list to understand what "
        "is physically on site — only classify to a type if the project has that asset.\n"
        "Use \"none\" for milestones/summaries or activities that need no bookable asset.\n"
        "Use \"other\" only if the activity clearly needs physical plant not covered above."
    )

    prompt = base_prompt.replace("{{ASSET_TYPES_BLOCK}}", asset_block)

    logger.debug(
        "Built dynamic classification prompt with %d project assets, %d valid types",
        len(asset_lines),
        len(valid_types_frozen),
    )

    return prompt, valid_types_frozen


async def _classify_assets_real(
    activities: list[dict[str, Any]],
    project_assets: list[dict[str, Any]] | None = None,
    *,
    execution_context: AIExecutionContext | None = None,
) -> ClassificationResult:
    if not activities:
        return ClassificationResult(requirements=[], skipped=[], batch_tokens_used=0)

    execution_context = _resolve_ai_execution_context(execution_context)
    client = _get_async_client()
    system_prompt, valid_types = _build_classification_prompt(project_assets)

    deterministic_by_activity: dict[str, list[AssetRequirementItem]] = {}
    for act in activities:
        act_id = str(act.get("id", ""))
        candidates = _deterministic_asset_requirement_candidates(
            act,
            valid_types=valid_types if project_assets else None,
        )
        deterministic_by_activity[act_id] = candidates
        if candidates:
            act["deterministic_asset_candidates"] = [
                {
                    "asset_type": item.asset_type,
                    "confidence": item.label_confidence,
                    "role": item.asset_role,
                    "profile_shape": item.profile_shape,
                }
                for item in candidates
            ]

    norm_to_rep: dict[str, str] = {}
    rep_to_ids: dict[str, list[str]] = {}
    deduped_candidates: list[dict[str, Any]] = []
    for act in activities:
        act_id = str(act.get("id", ""))
        norm = _normalize_for_dedup(str(act.get("name", "")))
        if norm in norm_to_rep:
            rep_to_ids[norm_to_rep[norm]].append(act_id)
        else:
            norm_to_rep[norm] = act_id
            rep_to_ids[act_id] = [act_id]
            deduped_candidates.append(act)

    logger.info(
        "Multi-asset classification: %d deterministic requirements, %d unique AI activities (%d total)",
        sum(len(items) for items in deterministic_by_activity.values()),
        len(deduped_candidates),
        len(activities),
    )

    BATCH_SIZE = AI_CLASSIFICATION_BATCH_SIZE
    all_ai_results: dict[str, list[dict[str, Any]]] = {}
    total_tokens = 0
    total_cost_usd: Decimal | None = None

    batches = [
        deduped_candidates[i:i + BATCH_SIZE]
        for i in range(0, len(deduped_candidates), BATCH_SIZE)
    ]
    batch_results: list[Any] = []
    if execution_context is None or not execution_context.suppress_ai:
        batch_tasks = [
            _classify_batch(batch, system_prompt, client, execution_context=execution_context)
            for batch in batches
        ]
        if len(deduped_candidates) > AI_CLASSIFICATION_PARALLEL_THRESHOLD:
            sem = asyncio.Semaphore(AI_CLASSIFICATION_MAX_CONCURRENT_BATCHES)

            async def _bounded(task: Coroutine[Any, Any, Any]) -> Any:
                async with sem:
                    return await task

            batch_results = await asyncio.gather(
                *[_bounded(task) for task in batch_tasks],
                return_exceptions=True,
            )
        else:
            for task in batch_tasks:
                try:
                    batch_results.append(await task)
                except Exception as exc:
                    logger.warning("Batch classification task failed: %s", exc, exc_info=True)
                    batch_results.append(exc)

    for result in batch_results:
        if isinstance(result, Exception):
            logger.warning("Batch result error (skipping batch): %s", result)
            continue
        for item in result.get("requirements", []):
            act_id = str(item.get("activity_id", ""))
            if act_id:
                all_ai_results.setdefault(act_id, []).append(item)
        for skipped_id in result.get("skipped", []):
            sid = str(skipped_id)
            if sid and sid not in all_ai_results:
                all_ai_results[sid] = []
        total_tokens += result.get("tokens_used", 0)
        total_cost_usd = sum_ai_costs(total_cost_usd, result.get("cost_usd"))

    expanded: dict[str, list[dict[str, Any]]] = {}
    for rep_id, dup_ids in rep_to_ids.items():
        rep_result = all_ai_results.get(rep_id)
        if rep_result is not None:
            for aid in dup_ids:
                expanded[aid] = [{**item, "activity_id": aid} for item in rep_result]
    # Every AI candidate should be represented by rep_to_ids; preserve any
    # unexpected leftovers rather than silently discarding future response shapes.
    for act_id, result_items in all_ai_results.items():
        expanded.setdefault(act_id, result_items)
    all_ai_results = expanded

    requirements: list[AssetRequirementItem] = []
    skipped: list[str] = []

    for act in activities:
        act_id = str(act.get("id", ""))
        merged: dict[str, AssetRequirementItem] = {
            item.asset_type: item for item in deterministic_by_activity.get(act_id, [])
        }

        for raw in all_ai_results.get(act_id, []):
            asset_type = str(raw.get("asset_type") or "").strip().lower()
            if asset_type in {"", "none", "other"} or asset_type not in valid_types:
                continue
            raw_score = raw.get("label_confidence", raw.get("confidence"))
            tier = _confidence_tier_from_score(raw_score)
            score = _label_confidence_from_any(raw_score, tier)
            candidate = AssetRequirementItem(
                activity_id=act_id,
                asset_type=asset_type,
                confidence=tier,
                source=str(raw.get("source") or "ai"),
                label_confidence=score,
                asset_role=_normalize_asset_role(raw.get("asset_role", raw.get("role"))),
                estimated_total_hours=_coerce_optional_float(
                    raw.get("estimated_total_hours", raw.get("estimated_hours"))
                ),
                profile_shape=_normalize_profile_shape(raw.get("profile_shape")),
                reasoning=raw.get("reasoning"),
            )
            existing = merged.get(asset_type)
            if existing is not None:
                if existing.estimated_total_hours is None and candidate.estimated_total_hours is not None:
                    existing.estimated_total_hours = candidate.estimated_total_hours
                if not existing.profile_shape and candidate.profile_shape:
                    existing.profile_shape = candidate.profile_shape
            if existing is None or (candidate.label_confidence or 0.0) >= (existing.label_confidence or 0.0):
                if existing and existing.estimated_total_hours is not None and candidate.estimated_total_hours is None:
                    candidate.estimated_total_hours = existing.estimated_total_hours
                if existing and existing.profile_shape and not candidate.profile_shape:
                    candidate.profile_shape = existing.profile_shape
                merged[asset_type] = candidate

        if not merged:
            skipped.append(act_id)
            continue
        requirements.extend(merged.values())

    logger.info(
        "Multi-asset classification complete: %d requirements, %d no-demand/skipped, %d tokens used",
        len(requirements),
        len(skipped),
        total_tokens,
    )
    return ClassificationResult(
        requirements=requirements,
        skipped=skipped,
        batch_tokens_used=total_tokens,
        batch_cost_usd=total_cost_usd,
        fallback_used=False,
    )

# ---------------------------------------------------------------------------
# Subcontractor asset suggestion helper
# ---------------------------------------------------------------------------

def _lookup_trade_asset_types(specialty: str) -> list[str]:
    """
    Map a trade specialty string to suggested asset types.

    Tries (in order): exact match → substring match → word-level partial match.
    Returns ["other"] if no match found.
    """
    if not specialty:
        return ["other"]

    # Exact match
    if specialty in TRADE_TO_ASSET_TYPES:
        return list(TRADE_TO_ASSET_TYPES[specialty])

    # Substring: specialty contains a key or a key contains specialty.
    # Pick the longest (most specific) matching key to avoid wrong matches
    # when similar keys exist (e.g. "crane" vs "tower crane").
    substring_matches = [
        (key, types)
        for key, types in TRADE_TO_ASSET_TYPES.items()
        if key in specialty or specialty in key
    ]
    if substring_matches:
        _best_key, best_types = max(substring_matches, key=lambda kv: len(kv[0]))
        return list(best_types)

    # Word-level: any shared word
    specialty_words = set(specialty.split())
    for key, types in TRADE_TO_ASSET_TYPES.items():
        if specialty_words & set(key.split()):
            return list(types)

    return ["other"]


# ---------------------------------------------------------------------------
# Deterministic fallbacks — always produce a usable result
# ---------------------------------------------------------------------------

# Keywords that map directly to asset types (keyword boost layer).
#
# Lookup is sorted longest-key-first, so more specific phrases always win over
# shorter substrings (e.g. "jump the hoist" → crane beats "hoist" → hoist).
#
# Crane additions verified against ARC Bowden Overall Programme V36.1 (PDF):
#   RC superstructure programmes write work-package names, not plant names.
#   The tower crane is implicit in ~400 activities that never say "crane".
_KEYWORD_MAP: dict[str, str] = {
    # ── Crane ──────────────────────────────────────────────────────────────────
    "crane": "crane",
    # "lift" alone removed — too ambiguous ("scissor lift", "boom lift" are ewp)

    # Precast panels — TC lifts every panel to each floor
    "precast": "crane",
    "pre-cast": "crane",

    # Column reinforcement cages — TC lifts cages to deck level
    "column cage": "crane",
    "column cages": "crane",

    # Bubbledeck false-work, panels and installation — all TC-lifted
    # (note: "Install BD reo" / "Install (BD) reo" are manual reo-fixing → other,
    #  so we match the specific panel/false-work phrases, not a blanket "install bd")
    "bubbledeck installation": "crane",   # e.g. "Bubbledeck installation ZONE (A)"
    "bubbledeck install":      "crane",   # e.g. "Commence/Continue/Complete Bubbledeck install"
    "bubbledeck false work":   "crane",   # e.g. "Bubbledeck false work" (L1 form)
    "install bd panels":       "crane",   # e.g. "Install BD panels"
    "install bd false":        "crane",   # e.g. "Install BD false work"
    "lift bd":                 "crane",   # e.g. "Lift BD false work", "Lift BD reo"

    # Column formwork — TC lifts heavy steel forms to each floor
    "lift column":             "crane",   # e.g. "Lift column formwork", "Lift column cages"

    # Screw-in / threaded bars — TC lifts bundles of bars
    "lift screw":              "crane",   # e.g. "Lift screw in bars"

    # TC repositions stair-form and raises construction hoist to next level
    # (longer keys checked first → these override the plain "hoist" entry below)
    "jump the stretcher":      "crane",   # e.g. "Jump the stretcher stairs"
    "jump the hoist":          "crane",   # e.g. "Jump the Hoist"
    "hoist off site":          "crane",   # e.g. "Hoist off site following Builders Lift Ready"

    # TC lifts shoring props between floors
    "recycle props to upper":  "crane",   # e.g. "Recycle props to upper levels"

    # TC installs the permanent builder's hoist level by level
    "install builder's lift":  "crane",   # e.g. "Install Builder's Lift @ 4d/ level"

    # External canopy steel — installed with TC ("use of TC" in activity name)
    "install canopy steel":    "crane",   # e.g. "Install canopy steel with use of TC"

    # ── Hoist ──────────────────────────────────────────────────────────────────
    "hoist": "hoist",

    # ── Loading bay ────────────────────────────────────────────────────────────
    "loading bay":  "loading_bay",
    "loading_bay":  "loading_bay",

    # ── EWP ────────────────────────────────────────────────────────────────────
    "ewp":                    "ewp",
    "elevated work platform": "ewp",
    "scissor lift":           "ewp",
    "boom lift":              "ewp",
    "man lift":               "ewp",
    "knuckle lift":           "ewp",

    # ── Concrete pump ──────────────────────────────────────────────────────────
    "concrete pump":  "concrete_pump",
    "concrete_pump":  "concrete_pump",
    "slab pour":      "concrete_pump",   # e.g. "Slab pour, pour 1"
    "concrete pour":  "concrete_pump",   # e.g. "Concrete pour floor slab Zone [A]"
    "pour concrete":  "concrete_pump",   # e.g. "Pour concrete columns" (reversed word order)
    "pour columns":   "concrete_pump",   # e.g. "Day 3 - Pour columns to pour 1"
    "column pour":    "concrete_pump",   # e.g. "Ground floor column pour"

    # ── Excavator ──────────────────────────────────────────────────────────────
    "excavator":   "excavator",
    "dig footings": "excavator",         # e.g. "Dig footings / piles for steel canopy columns/posts"

    # ── Forklift ───────────────────────────────────────────────────────────────
    "forklift": "forklift",

    # ── Telehandler ────────────────────────────────────────────────────────────
    "telehandler": "telehandler",

    # ── Compactor ──────────────────────────────────────────────────────────────
    "compactor": "compactor",
}

# Generalized deterministic keyword map used by all no-credit fallbacks.
# This intentionally favors broad, reusable construction patterns over
# project-specific literals so it remains stable across uploads.
_KEYWORD_MAP.update({
    "tower crane": "crane",
    "mobile crane": "crane",
    "crawler crane": "crane",
    "luffing crane": "crane",
    "pick and carry": "crane",
    "pre cast": "crane",
    "precast": "crane",
    "column cages": "crane",
    "column cage": "crane",
    "crane": "crane",
    "builders hoist": "hoist",
    "builder hoist": "hoist",
    "builders lift": "hoist",
    "builder lift": "hoist",
    "personnel hoist": "hoist",
    "materials hoist": "hoist",
    "material hoist": "hoist",
    "construction lift": "hoist",
    "hoist": "hoist",
    "loading zone": "loading_bay",
    "loading bay": "loading_bay",
    "unloading bay": "loading_bay",
    "elevated work platform": "ewp",
    "scissor lift": "ewp",
    "boom lift": "ewp",
    "knuckle boom": "ewp",
    "knuckle lift": "ewp",
    "cherry picker": "ewp",
    "man lift": "ewp",
    "ewp": "ewp",
    "concrete pump": "concrete_pump",
    "boom pump": "concrete_pump",
    "line pump": "concrete_pump",
    "concrete pour": "concrete_pump",
    "slab pour": "concrete_pump",
    "column pour": "concrete_pump",
    "pour concrete": "concrete_pump",
    "pour columns": "concrete_pump",
    "kibble": "concrete_pump",
    "mini excavator": "excavator",
    "dig footings": "excavator",
    "earthworks": "excavator",
    "excavation": "excavator",
    "excavate": "excavator",
    "trenching": "excavator",
    "excavator": "excavator",
    "backhoe": "excavator",
    "digger": "excavator",
    "telescopic handler": "telehandler",
    "reach forklift": "telehandler",
    "telehandler": "telehandler",
    "forklift": "forklift",
    "plate compactor": "compactor",
    "smooth drum roller": "compactor",
    "compactor": "compactor",
    "roller": "compactor",
})


def _normalize_for_keyword_match(name: str) -> str:
    normalized = _normalize_for_dedup(name)
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


_OBVIOUS_NO_ASSET_PHASE_HEADERS: set[str] = {
    "superstructure",
    "substructure",
    "early works",
    "external works",
    "internal works",
    "civil works",
    "preliminaries",
    "preliminary works",
    "fitout",
    "fit out",
    "finishes",
    "structure",
}

_OBVIOUS_NO_ASSET_ACTION_HINTS: tuple[str, ...] = (
    "install",
    "pour",
    "lift",
    "erect",
    "excavate",
    "excavation",
    "dig",
    "fix",
    "fixing",
    "formwork",
    "setout",
    "set out",
    "setup",
    "set up",
    "deliver",
    "delivery",
    "remove",
    "removal",
    "pump",
    "place",
    "jump",
    "recycle",
    "inspect",
    "inspection",
    "test",
    "testing",
    "paint",
    "erection",
    "rough in",
    "fit off",
    "commission",
)

_OBVIOUS_ASSET_HINTS: tuple[str, ...] = (
    "crane",
    "hoist",
    "loading bay",
    "loading zone",
    "ewp",
    "scissor lift",
    "boom lift",
    "concrete pump",
    "boom pump",
    "line pump",
    "excavator",
    "forklift",
    "telehandler",
    "compactor",
)


def looks_like_non_demand_heading(activity_name: str) -> bool:
    """
    Return True for obvious phase/zone/level headings that should resolve to
    asset_type='none' rather than entering review as unresolved demand rows.

    This is intentionally conservative: explicit asset references or obvious
    action verbs always win over the heading heuristic.
    """
    normalized_name = _normalize_for_keyword_match(activity_name)
    if not normalized_name:
        return False

    padded_name = f" {normalized_name} "
    if any(f" {hint} " in padded_name for hint in _OBVIOUS_ASSET_HINTS):
        return False
    if any(f" {hint} " in padded_name for hint in _OBVIOUS_NO_ASSET_ACTION_HINTS):
        return False

    if normalized_name in _OBVIOUS_NO_ASSET_PHASE_HEADERS:
        return True

    if re.fullmatch(r"zone [a-z0-9][a-z0-9 /-]*", normalized_name):
        return True

    if re.fullmatch(
        r"(?:level|floor|basement|podium|roof) [a-z0-9][a-z0-9 m~.,()/+-]*",
        normalized_name,
    ):
        return True

    if normalized_name.startswith("construction ") and " works" in padded_name:
        return True

    return False


def keyword_classify_activity_name(
    activity_name: str,
    *,
    valid_types: frozenset[str] | None = None,
) -> str | None:
    normalized_name = _normalize_for_keyword_match(activity_name)
    if not normalized_name:
        return None

    padded_name = f" {normalized_name} "
    for keyword, asset_type in sorted(
        _KEYWORD_MAP.items(),
        key=lambda kv: len(_normalize_for_keyword_match(kv[0])),
        reverse=True,
    ):
        normalized_keyword = _normalize_for_keyword_match(keyword)
        if not normalized_keyword or f" {normalized_keyword} " not in padded_name:
            continue
        if valid_types and asset_type not in valid_types:
            continue
        return asset_type

    if (valid_types is None or "none" in valid_types) and looks_like_non_demand_heading(activity_name):
        return "none"
    return None


_MULTI_ASSET_PHRASE_RULES: tuple[tuple[tuple[str, ...], tuple[tuple[str, str, float, str | None], ...]], ...] = (
    (("bubbledeck", "false work"), (("crane", "lead", 0.85, "staged"), ("telehandler", "support", 0.55, "front_loaded"), ("forklift", "incidental", 0.45, "front_loaded"))),
    (("bubbledeck", "install"), (("crane", "lead", 0.85, "staged"), ("telehandler", "support", 0.50, "front_loaded"))),
    (("jump", "hoist"), (("crane", "lead", 0.90, "single_day"),)),
    (("jump", "stretcher"), (("crane", "lead", 0.90, "single_day"),)),
    (("strip", "column"), (("ewp", "lead", 0.80, "flat"),)),
    (("slab edge strip",), (("ewp", "lead", 0.80, "flat"),)),
    (("set down",), (("ewp", "lead", 0.75, "flat"),)),
    (("ground floor", "strip columns"), (("ewp", "lead", 0.82, "flat"),)),
    (("concrete pour",), (("concrete_pump", "lead", 0.88, "single_day"),)),
    (("slab pour",), (("concrete_pump", "lead", 0.88, "single_day"),)),
    (("column pour",), (("concrete_pump", "lead", 0.88, "single_day"), ("ewp", "support", 0.50, "single_day"))),
    (("delivery",), (("loading_bay", "lead", 0.70, "front_loaded"), ("forklift", "support", 0.50, "front_loaded"))),
    (("deliver",), (("loading_bay", "lead", 0.70, "front_loaded"), ("forklift", "support", 0.50, "front_loaded"))),
)


def _deterministic_asset_requirement_candidates(
    activity: dict[str, Any],
    *,
    valid_types: frozenset[str] | None = None,
) -> list[AssetRequirementItem]:
    activity_id = str(activity.get("id", ""))
    name = str(activity.get("name", ""))
    normalized_name = _normalize_for_keyword_match(name)
    if not activity_id or not normalized_name:
        return []

    if looks_like_non_demand_heading(name):
        return []

    candidates: dict[str, AssetRequirementItem] = {}

    def _add(asset_type: str, role: str, score: float, shape: str | None = None) -> None:
        if asset_type in {"none", "other"}:
            return
        if valid_types and asset_type not in valid_types:
            return
        existing = candidates.get(asset_type)
        if existing and (existing.label_confidence or 0.0) >= score:
            return
        tier = _confidence_tier_from_score(score)
        candidates[asset_type] = AssetRequirementItem(
            activity_id=activity_id,
            asset_type=asset_type,
            confidence=tier,
            source="keyword",
            label_confidence=score,
            asset_role=role,
            profile_shape=shape,
        )

    primary = keyword_classify_activity_name(name, valid_types=valid_types)
    if primary and primary not in {"none", "other"}:
        _add(primary, "lead", 0.78)

    for phrase_parts, requirements in _MULTI_ASSET_PHRASE_RULES:
        if all(part in normalized_name for part in phrase_parts):
            for asset_type, role, score, shape in requirements:
                _add(asset_type, role, score, shape)

    return sorted(
        candidates.values(),
        key=lambda item: (item.asset_role != "lead", -(item.label_confidence or 0.0), item.asset_type),
    )


def _detect_structure_fallback(rows: list[dict[str, Any]]) -> StructureResult:
    """
    Regex/heuristic fallback when AI is unavailable.
    Detects date columns by parsing sample values. Uses first string column
    with the most unique values as the name column.
    Returns a flat (no hierarchy) activity list — always commits something.
    """
    if not rows:
        return StructureResult(
            column_mapping={},
            activities=[],
            completeness_score=0,
            missing_fields=["name", "start_date", "end_date"],
            notes="Empty file — no rows to parse.",
        )

    headers = list(rows[0].keys())

    # Detect date columns via regex on first 10 non-empty values
    date_patterns = [
        re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),                      # dd/mm/yyyy
        re.compile(r"^\d{1,2}/\d{1,2}/\d{2}$"),                      # dd/mm/yy
        re.compile(r"^\d{4}-\d{2}-\d{2}$"),                          # yyyy-mm-dd
        re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}$"),                # dd-Mon-yy / dd-Mon-yyyy (P6 PDF)
        re.compile(r"^\d{1,2} [A-Za-z]{3} \d{4}$"),                  # dd Mon yyyy
    ]

    def _looks_like_date(col: str) -> bool:
        samples = [str(r[col]) for r in rows[:10] if r.get(col)]
        return any(p.match(s) for s in samples for p in date_patterns)

    date_cols = [h for h in headers if _looks_like_date(h)]

    # Name column: prefer headers containing "name" (case-insensitive);
    # exclude headers that look like IDs or numeric fields (contain "id", "%", "complete", "duration").
    # Tiebreak: most unique values.
    def _unique_count(col: str) -> int:
        return len({str(r.get(col, "")) for r in rows if r.get(col)})

    def _name_col_score(col: str) -> tuple[int, int]:
        lower = col.lower()
        preference = 1 if "name" in lower else 0
        penalty = -1 if any(x in lower for x in ("id", "%", "complete", "duration", "code")) else 0
        return (preference + penalty, _unique_count(col))

    def _looks_like_pct(col: str) -> bool:
        """True when the header name suggests a % complete field and sample values are numeric."""
        lower = col.lower()
        if not any(k in lower for k in ("pct", "percent", "complete", "%")):
            return False
        samples = [str(r[col]).rstrip("%").strip() for r in rows[:10] if col in r and r[col] is not None]
        return any(_float_ok(s) for s in samples)

    def _float_ok(s: str) -> bool:
        try:
            float(s)
            return True
        except (ValueError, TypeError):
            return False

    string_cols = [h for h in headers if not _looks_like_date(h)]
    name_col = max(string_cols, key=_name_col_score) if string_cols else (headers[0] if headers else None)

    # Detect pct_complete column (exclude whatever was chosen as name_col).
    pct_col = next(
        (h for h in string_cols if h != name_col and _looks_like_pct(h)),
        None,
    )

    column_mapping: dict[str, str] = {}
    missing: list[str] = []

    if name_col:
        column_mapping["name"] = name_col
    else:
        missing.append("name")

    if pct_col:
        column_mapping["pct_complete"] = pct_col

    if len(date_cols) >= 2:
        column_mapping["start_date"] = date_cols[0]
        column_mapping["end_date"] = date_cols[1]
    elif len(date_cols) == 1:
        column_mapping["start_date"] = date_cols[0]
        missing.append("end_date")
    else:
        missing += ["start_date", "end_date"]

    def _parse_date(val: Any) -> str | None:
        if not val:
            return None
        s = str(val).strip()
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y"):
            try:
                parsed = datetime.strptime(s, fmt)
                return parsed.date().isoformat()
            except ValueError:
                pass
        return None

    activities: list[ActivityItem] = []

    for i, row in enumerate(rows):
        name = str(row.get(column_mapping.get("name", ""), "")).strip()
        if not name:
            continue

        start = _parse_date(row.get(column_mapping.get("start_date", ""), ""))
        finish = _parse_date(row.get(column_mapping.get("end_date", ""), ""))

        pct_complete = parse_pct_raw(row.get(pct_col) if pct_col else None)

        activity_kind = classify_row_kind(is_summary=False, start=start, finish=finish)
        row_confidence = score_row_confidence(
            name=name, start=start, finish=finish, activity_kind=activity_kind
        )
        activities.append(ActivityItem(
            id=f"row-{i}",
            name=name,
            start=start,
            finish=finish,
            parent_id=None,   # flat — no hierarchy in fallback
            is_summary=False,
            level_name=None,
            zone_name=None,
            pct_complete=pct_complete,
            activity_kind=activity_kind,
            row_confidence=row_confidence,
        ))

    rows_with_dates = sum(1 for a in activities if a.start and a.finish)
    total = len(rows)
    score = int((rows_with_dates / total) * 80) if total else 0  # cap at 80 for fallback

    notes_parts = ["Regex fallback — AI unavailable."]
    if missing:
        notes_parts.append(f"Missing columns: {', '.join(missing)}.")

    return StructureResult(
        column_mapping=column_mapping,
        activities=activities,
        completeness_score=score,
        missing_fields=missing,
        notes=" ".join(notes_parts),
    )


def _classify_assets_fallback(
    activities: list[dict[str, Any]],
    project_assets: list[dict[str, Any]] | None = None,
) -> ClassificationResult:
    """
    Keyword-only classification fallback.
    Matches activity names against _KEYWORD_MAP. No AI call.
    When project_assets are provided, only accepts keyword hits for types
    that exist on the project (same scoping as the AI path).
    Unmatched activities go to skipped[].
    """
    # Build valid_types from project assets (mirrors _build_classification_prompt logic)
    valid_types: frozenset[str] | None = None
    if project_assets:
        vt: set[str] = set()
        for a in project_assets:
            canonical = str(a.get("canonical_type") or "").strip() or None
            if canonical is None:
                canonical = normalize_asset_type(str(a.get("type") or ""))
            if canonical is None:
                canonical = normalize_asset_type(str(a.get("name") or ""))
            if canonical and canonical != "none":
                vt.add(canonical)
        if vt:
            vt.update({"none", "other"})
            valid_types = frozenset(vt)

    requirements: list[AssetRequirementItem] = []
    skipped: list[str] = []

    for activity in activities:
        activity_id = str(activity.get("id", ""))
        candidates = _deterministic_asset_requirement_candidates(activity, valid_types=valid_types)

        if candidates:
            requirements.extend(candidates)
        else:
            skipped.append(activity_id)

    return ClassificationResult(
        requirements=requirements,
        skipped=skipped,
        batch_tokens_used=0,
        fallback_used=True,
    )
