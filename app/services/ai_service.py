"""
AI service — structure detection and asset classification.

BE Dev: this file defines the interface contract. The stub implementations
        return hardcoded fixture data so the orchestrator can be built and
        tested independently.

AI Dev: replace _detect_structure_real() and _classify_assets_real() with
        real LLM calls. Do not change the function signatures or return shapes —
        those are the agreed interface contract (team-split doc Sections 2.1 + 2.2).

Contract:
  detect_structure(rows)  -> StructureResult   (Section 2.1)
  classify_assets(activities) -> ClassificationResult  (Section 2.2)
  suggest_subcontractor_asset_types(subcontractors) -> list[SubcontractorAssetSuggestion]
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import threading

import anthropic
try:
    import openai as _openai_module
except ImportError:
    _openai_module = None  # type: ignore[assignment]

from ..core.config import settings

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# P6 and common programme day-step prefix stripped before name dedup.
# Matches: "Day 7 - ", "Day 14 – ", "Day 7-" etc.
# Character class explicitly covers hyphen, en-dash (U+2013), and em-dash (U+2014).
_DEDUP_PREFIX_RE = re.compile(r"^(?:day\s+\d+\s*[-\u2013\u2014]\s*)+", re.IGNORECASE)


def _normalize_for_dedup(name: str) -> str:
    """Lowercase, strip P6 day-step prefix, collapse whitespace."""
    norm = _DEDUP_PREFIX_RE.sub("", name.lower()).strip()
    return re.sub(r"\s{2,}", " ", norm)


# ---------------------------------------------------------------------------
# Allowed asset_type values — Section 2.3 canonical list.
# BE validates on DB write; AI Dev uses only these values in prompts.
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
    ("boom pump",         "concrete_pump"),
    ("line pump",         "concrete_pump"),
    ("concrete pump",     "concrete_pump"),
    ("kibble",            "concrete_pump"),
    ("mini excavator",    "excavator"),
    ("backhoe",           "excavator"),
    ("excavator",         "excavator"),
    ("digger",            "excavator"),
    ("rough terrain forklift", "forklift"),
    ("forklift",          "forklift"),
    ("telehandler",       "telehandler"),
    ("telescopic handler", "telehandler"),
    ("reach forklift",    "telehandler"),
    ("telescopic forklift", "telehandler"),
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


@dataclass
class StructureResult:
    """
    Return shape for detect_structure() — Section 2.1.

    column_mapping keys: name, start_date, end_date, duration, wbs_code,
                         resource, level_indicator, or "unknown"
    completeness_score: int 0–100 (BE converts to float 0.0–1.0 on DB write)
    """
    column_mapping: dict[str, str]
    activities: list[ActivityItem]
    completeness_score: int          # 0–100
    missing_fields: list[str]
    notes: str


@dataclass
class ClassificationItem:
    """Single classification result within ClassificationResult."""
    activity_id: str                 # UUID string of programme_activity.id
    asset_type: str                  # must be in ALLOWED_ASSET_TYPES
    confidence: str                  # "high" | "medium" | "low"
    source: str                      # "ai" | "keyword_boost"
    reasoning: str | None = None


@dataclass
class ClassificationResult:
    """
    Return shape for classify_assets() — Section 2.2.

    classifications: high + medium confidence items (auto-committed by BE)
    skipped:         activity_id strings for low-confidence items (not committed)
    fallback_used:   True when AI was unavailable and keyword-only fallback ran
    """
    classifications: list[ClassificationItem]
    skipped: list[str]
    batch_tokens_used: int = 0
    fallback_used: bool = False


@dataclass
class SubcontractorAssetSuggestion:
    """
    Suggested asset types for a subcontractor based on their trade_specialty.
    Used by the lookahead planning feature to pre-assign likely asset needs.
    """
    subcontractor_id: str
    trade_specialty: str
    suggested_asset_types: list[str]


# ---------------------------------------------------------------------------
# Public interface — called by process_programme.py orchestrator
# ---------------------------------------------------------------------------

async def detect_structure(rows: list[dict[str, Any]]) -> StructureResult:
    """
    Analyse the first 50–100 rows of a programme file and return:
      - column_mapping: header → semantic field name
      - activities:     parsed activity tree
      - completeness_score: int 0–100

    Falls back to regex heuristics if AI is disabled or fails.
    Never raises — always returns a StructureResult (possibly degraded).
    """
    if not settings.AI_ENABLED:
        logger.info("AI_ENABLED=false — using regex fallback for structure detection")
        return _detect_structure_fallback(rows)

    try:
        return await _detect_structure_real(rows)
    except Exception as exc:
        logger.warning("AI structure detection failed (%s) — falling back to regex", exc)
        return _detect_structure_fallback(rows)


async def classify_assets(
    activities: list[dict[str, Any]],
    project_assets: list[dict[str, Any]] | None = None,
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
    if not settings.AI_ENABLED:
        logger.info("AI_ENABLED=false — using keyword fallback for classification")
        return _classify_assets_fallback(activities, project_assets=project_assets)

    try:
        return await _classify_assets_real(activities, project_assets=project_assets)
    except Exception as exc:
        logger.warning("AI classification failed (%s) — falling back to keyword", exc)
        return _classify_assets_fallback(activities, project_assets=project_assets)


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


async def _call_api(
    client: Any,
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    timeout: float,
) -> tuple[str, int]:
    """
    Unified async call — returns (text_content, tokens_used) regardless of provider.

    Quota/billing errors (OpenAI insufficient_quota, Anthropic credit exhausted) are
    re-raised immediately as RuntimeError so the SDK's built-in retry loop doesn't
    waste minutes retrying a permanent billing error.
    """
    try:
        if _is_openai_client(client):
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.AI_MODEL,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                ),
                timeout=timeout,
            )
            content = response.choices[0].message.content if response.choices else None
            usage = getattr(response, "usage", None)
            tokens = (getattr(usage, "prompt_tokens", None) or 0) + (getattr(usage, "completion_tokens", None) or 0)
        else:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=settings.AI_MODEL,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                ),
                timeout=timeout,
            )
            content = response.content[0].text if response.content else None
            usage = getattr(response, "usage", None)
            tokens = (getattr(usage, "input_tokens", None) or 0) + (getattr(usage, "output_tokens", None) or 0)
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
            logger.error("AI provider quota/billing limit reached — disabling AI for this request: %s", exc)
            raise RuntimeError(f"AI quota exhausted: {exc}") from exc
        raise

    if not content:
        raise ValueError("Empty or malformed API response")
    return content, tokens


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
    """
    name_col = column_mapping.get("name")
    start_col = column_mapping.get("start_date")
    end_col = column_mapping.get("end_date")
    id_col = column_mapping.get("id") or column_mapping.get("wbs_code")
    parent_col = column_mapping.get("parent_id")
    summary_col = column_mapping.get("is_summary")
    level_col = column_mapping.get("level_name") or column_mapping.get("level_indicator")
    zone_col = column_mapping.get("zone_name")

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

        activities.append(ActivityItem(
            id=activity_id,
            name=name,
            start=str(start_raw).strip() if start_raw is not None else None,
            finish=str(end_raw).strip() if end_raw is not None else None,
            parent_id=str(parent_raw).strip() if parent_raw is not None else None,
            is_summary=is_summary,
            level_name=str(level_raw).strip() if level_raw is not None else None,
            zone_name=str(zone_raw).strip() if zone_raw is not None else None,
        ))

    return activities


# ---------------------------------------------------------------------------
# Real AI implementations — Claude API calls
# ---------------------------------------------------------------------------

async def _detect_structure_real(rows: list[dict[str, Any]]) -> StructureResult:
    """
    Call Claude to detect column structure of a programme file.
    Uses structure_detection.txt system prompt.
    Hard timeout: settings.AI_TIMEOUT_STRUCTURE seconds.
    """
    client = _get_async_client()
    system_prompt = _load_prompt("structure_detection.txt")

    # 50 rows is sufficient to reliably identify headers and date patterns
    sample = rows[:50]
    user_message = (
        f"Here are the first {len(sample)} rows from a construction programme file. "
        "Identify the column structure. Return ONLY valid JSON.\n\n"
        f"ROWS:\n{json.dumps(sample, default=str)}"
    )

    text, _tokens = await _call_api(client, system_prompt, user_message, max_tokens=2048, timeout=float(settings.AI_TIMEOUT_STRUCTURE))
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
    )


def _extract_partial_classifications(text: str) -> list[dict[str, Any]]:
    """
    Last-resort extraction: pull out any syntactically complete classification
    objects from a truncated response.  Matches objects that have all four
    required fields (activity_id, asset_type, confidence, source) in any order.
    """
    pattern = re.compile(
        r'\{\s*'
        r'"activity_id"\s*:\s*"(?P<activity_id>[^"]+)"\s*,\s*'
        r'"asset_type"\s*:\s*"(?P<asset_type>[^"]+)"\s*,\s*'
        r'"confidence"\s*:\s*"(?P<confidence>[^"]+)"\s*,\s*'
        r'"source"\s*:\s*"(?P<source>[^"]+)"\s*'
        r'\}',
        re.DOTALL,
    )
    return [m.groupdict() for m in pattern.finditer(text)]


async def _classify_batch(
    batch: list[dict[str, Any]],
    system_prompt: str,
    client: Any,
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

    # Each activity produces ~30 output tokens (UUID + asset_type + confidence + source).
    # 50-activity batches → ~1,500 tokens + JSON overhead; 8192 gives a safe 5× headroom.
    text, tokens_used = await _call_api(client, system_prompt, user_message, max_tokens=8192, timeout=float(settings.AI_TIMEOUT_CLASSIFY))
    try:
        data = _parse_json_response(text)
        return {
            "classifications": list(data.get("classifications") or []),
            "skipped": list(data.get("skipped") or []),
            "tokens_used": tokens_used,
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
            return {"classifications": partial, "skipped": [], "tokens_used": tokens_used}
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

        canonical = normalize_asset_type(raw_type)
        if canonical is None:
            # Type is generic (e.g. "EQUIPMENT") — fall back to the asset name.
            # Covers cases like Forklift/EQUIPMENT or Excavator/EQUIPMENT.
            canonical = normalize_asset_type(raw_name)
        if canonical and canonical != "none":
            valid_types.add(canonical)

        label = f"- {raw_name}"
        if raw_type:
            label += f" (type: {raw_type})"
        if code:
            label += f" [code: {code}]"
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
    type_lines = "\n".join(
        f"  - {_TYPE_DESCRIPTIONS[t]}"
        for t in sorted(valid_types)
        if t in _TYPE_DESCRIPTIONS
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
) -> ClassificationResult:
    """
    Classify activities via keyword pre-screening + Claude batched calls.

    Pipeline:
    1. Pre-screen with _KEYWORD_MAP (definite direct keyword hits)
    2. Send remaining activities to Claude in batches of 100
       — parallel for 500+ activities, sequential otherwise
    3. Merge results:
       - keyword + AI agree on same type → confidence "high"
       - keyword match, AI diverges with low confidence → keyword wins (medium)
       - keyword match, AI diverges with medium/high → AI wins
       - AI only → use AI confidence/type as-is
       - neither matched → skipped[]
    """
    if not activities:
        return ClassificationResult(classifications=[], skipped=[], batch_tokens_used=0)

    client = _get_async_client()
    system_prompt, valid_types = _build_classification_prompt(project_assets)

    # Step 1: Keyword pre-screening
    # When project assets are provided, restrict keyword hits to types that
    # actually exist in this project (normalised).
    keyword_matched: dict[str, str] = {}   # activity_id → asset_type
    ai_candidates: list[dict[str, Any]] = []

    for act in activities:
        act_id = str(act.get("id", ""))
        name_lower = str(act.get("name", "")).lower()

        matched_type: str | None = None
        for keyword, asset_type in sorted(_KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
            if keyword in name_lower:
                # Only accept keyword hit if that type exists on this project
                if project_assets and asset_type not in valid_types:
                    continue
                matched_type = asset_type
                break

        if matched_type:
            keyword_matched[act_id] = matched_type
        else:
            ai_candidates.append(act)

    # Dedup AI candidates by normalised name: send one representative per unique
    # name to the AI, then fan the result back to all activities sharing that name.
    # Eliminates redundant calls for P6 day-step repetitions like
    # "Day 7 - Commence Bubbledeck install" / "Day 8 - Continue Bubbledeck install".
    norm_to_rep: dict[str, str] = {}       # normalised_name → representative act_id
    rep_to_ids: dict[str, list[str]] = {}  # rep_id → all act_ids with same norm name
    deduped_candidates: list[dict[str, Any]] = []

    for act in ai_candidates:
        act_id = str(act.get("id", ""))
        norm = _normalize_for_dedup(str(act.get("name", "")))
        if norm in norm_to_rep:
            rep_to_ids[norm_to_rep[norm]].append(act_id)
        else:
            norm_to_rep[norm] = act_id
            rep_to_ids[act_id] = [act_id]
            deduped_candidates.append(act)

    logger.info(
        "Classification pre-screen: %d keyword-matched, %d AI candidates → %d unique (%d total)",
        len(keyword_matched),
        len(ai_candidates),
        len(deduped_candidates),
        len(activities),
    )

    # Step 2: Batch remaining through AI
    # 50 activities × ~30 output tokens each = ~1,500 tokens per batch, well
    # within the 8192 max_tokens ceiling even when all activities need a skipped[].
    BATCH_SIZE = 50
    all_ai_results: dict[str, dict[str, Any]] = {}  # activity_id → result item
    total_tokens = 0

    if deduped_candidates:
        batches = [
            deduped_candidates[i:i + BATCH_SIZE]
            for i in range(0, len(deduped_candidates), BATCH_SIZE)
        ]
        batch_tasks = [_classify_batch(batch, system_prompt, client) for batch in batches]

        # Parallel for large volumes (bounded concurrency), sequential otherwise.
        # The semaphore prevents multiplying long per-batch timeouts into
        # unbounded in-flight requests when many batches are fanned out at once.
        # Threshold: >2 batches (>100 unique candidates) goes parallel.
        if len(deduped_candidates) > 100:
            _sem = asyncio.Semaphore(4)

            async def _bounded(task: asyncio.Task) -> Any:
                async with _sem:
                    return await task

            batch_results = await asyncio.gather(
                *[_bounded(t) for t in batch_tasks],
                return_exceptions=True,
            )
        else:
            batch_results = []
            for task in batch_tasks:
                try:
                    batch_results.append(await task)
                except Exception as exc:
                    logger.warning("Batch classification task failed: %s", exc)
                    batch_results.append(exc)

        for result in batch_results:
            if isinstance(result, Exception):
                logger.warning("Batch result error (skipping batch): %s", result)
                continue
            for item in result.get("classifications", []):
                act_id = str(item.get("activity_id", ""))
                if act_id:
                    all_ai_results[act_id] = item
            # Low-confidence items returned in skipped[]
            for skipped_id in result.get("skipped", []):
                sid = str(skipped_id)
                if sid and sid not in all_ai_results:
                    all_ai_results[sid] = {
                        "activity_id": sid,
                        "asset_type": None,
                        "confidence": "low",
                    }
            total_tokens += result.get("tokens_used", 0)

        # Fan-out: copy representative result to all duplicate activities
        expanded: dict[str, dict[str, Any]] = {}
        for rep_id, dup_ids in rep_to_ids.items():
            rep_result = all_ai_results.get(rep_id)
            if rep_result is not None:
                for aid in dup_ids:
                    expanded[aid] = rep_result
        all_ai_results = expanded

    # Step 3: Merge keyword + AI results
    classifications: list[ClassificationItem] = []
    skipped: list[str] = []
    processed_ids: set[str] = set()

    for act in activities:
        act_id = str(act.get("id", ""))
        if act_id in processed_ids:
            continue
        processed_ids.add(act_id)

        keyword_type = keyword_matched.get(act_id)
        ai_result = all_ai_results.get(act_id)

        if keyword_type and ai_result:
            ai_type = ai_result.get("asset_type")
            ai_confidence = str(ai_result.get("confidence") or "medium").lower()

            if ai_type == keyword_type and ai_type in valid_types:
                # Both agree — highest confidence
                classifications.append(ClassificationItem(
                    activity_id=act_id,
                    asset_type=keyword_type,
                    confidence="high",
                    source="keyword_boost",
                    reasoning=ai_result.get("reasoning"),
                ))
            elif ai_type and ai_type in valid_types and ai_type != "none":
                if ai_confidence == "low":
                    # Low-confidence AI disagreement: trust the keyword
                    classifications.append(ClassificationItem(
                        activity_id=act_id,
                        asset_type=keyword_type,
                        confidence="medium",
                        source="keyword_boost",
                    ))
                else:
                    # AI is confident and disagrees — AI wins
                    classifications.append(ClassificationItem(
                        activity_id=act_id,
                        asset_type=ai_type,
                        confidence=ai_confidence,
                        source="ai",
                        reasoning=ai_result.get("reasoning"),
                    ))
            else:
                # AI returned none/invalid — fall back to keyword
                classifications.append(ClassificationItem(
                    activity_id=act_id,
                    asset_type=keyword_type,
                    confidence="medium",
                    source="keyword_boost",
                ))

        elif keyword_type:
            # Keyword match only (AI did not process this activity)
            classifications.append(ClassificationItem(
                activity_id=act_id,
                asset_type=keyword_type,
                confidence="medium",
                source="keyword_boost",
            ))

        elif ai_result:
            ai_type = ai_result.get("asset_type")
            ai_confidence = str(ai_result.get("confidence") or "medium").lower()

            if not ai_type or ai_type == "none" or ai_type not in valid_types:
                skipped.append(act_id)
            elif ai_confidence == "low":
                skipped.append(act_id)
            else:
                classifications.append(ClassificationItem(
                    activity_id=act_id,
                    asset_type=ai_type,
                    confidence=ai_confidence,
                    source=str(ai_result.get("source") or "ai"),
                    reasoning=ai_result.get("reasoning"),
                ))

        else:
            skipped.append(act_id)

    logger.info(
        "Classification complete: %d classified, %d skipped, %d tokens used",
        len(classifications),
        len(skipped),
        total_tokens,
    )

    return ClassificationResult(
        classifications=classifications,
        skipped=skipped,
        batch_tokens_used=total_tokens,
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
        best_key, best_types = max(substring_matches, key=lambda kv: len(kv[0]))
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

# Keywords that map directly to asset types (keyword boost layer)
_KEYWORD_MAP: dict[str, str] = {
    "crane": "crane",
    # "lift" removed — too ambiguous ("scissor lift", "boom lift" are ewp, not crane)
    "precast": "crane",
    "pre-cast": "crane",
    "column cage": "crane",
    "column cages": "crane",
    "hoist": "hoist",
    "loading bay": "loading_bay",
    "loading_bay": "loading_bay",
    "ewp": "ewp",
    "elevated work platform": "ewp",
    "scissor lift": "ewp",
    "boom lift": "ewp",
    "man lift": "ewp",
    "knuckle lift": "ewp",
    "concrete pump": "concrete_pump",
    "concrete_pump": "concrete_pump",
    "slab pour": "concrete_pump",
    "concrete pour": "concrete_pump",
    "excavator": "excavator",
    "forklift": "forklift",
    "telehandler": "telehandler",
    "compactor": "compactor",
}


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

    string_cols = [h for h in headers if not _looks_like_date(h)]
    name_col = max(string_cols, key=_name_col_score) if string_cols else (headers[0] if headers else None)

    column_mapping: dict[str, str] = {}
    missing: list[str] = []

    if name_col:
        column_mapping["name"] = name_col
    else:
        missing.append("name")

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
                if "%y" in fmt and "%Y" not in fmt:
                    yy = parsed.year % 100
                    parsed = parsed.replace(year=1900 + yy if yy >= 69 else 2000 + yy)
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
        activities.append(ActivityItem(
            id=f"row-{i}",
            name=name,
            start=start,
            finish=finish,
            parent_id=None,   # flat — no hierarchy in fallback
            is_summary=False,
            level_name=None,
            zone_name=None,
        ))

    imported = len(activities)
    total = len(rows)
    score = int((imported / total) * 80) if total else 0  # cap at 80 for fallback

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
            canonical = normalize_asset_type(str(a.get("type") or ""))
            if canonical is None:
                canonical = normalize_asset_type(str(a.get("name") or ""))
            if canonical and canonical != "none":
                vt.add(canonical)
        if vt:
            vt.update({"none", "other"})
            valid_types = frozenset(vt)

    classifications: list[ClassificationItem] = []
    skipped: list[str] = []

    for activity in activities:
        activity_id = str(activity.get("id", ""))
        name = str(activity.get("name", "")).lower()

        matched_type: str | None = None
        for keyword, asset_type in sorted(_KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
            if keyword in name:
                if valid_types and asset_type not in valid_types:
                    continue
                matched_type = asset_type
                break

        if matched_type:
            classifications.append(ClassificationItem(
                activity_id=activity_id,
                asset_type=matched_type,
                confidence="medium",   # keyword-only = medium (no AI corroboration)
                source="keyword_boost",
            ))
        else:
            skipped.append(activity_id)

    return ClassificationResult(
        classifications=classifications,
        skipped=skipped,
        batch_tokens_used=0,
        fallback_used=True,
    )
