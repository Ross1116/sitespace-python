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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..core.config import settings

logger = logging.getLogger(__name__)

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
})


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
    """
    classifications: list[ClassificationItem]
    skipped: list[str]
    batch_tokens_used: int = 0


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


async def classify_assets(activities: list[dict[str, Any]]) -> ClassificationResult:
    """
    Classify a batch of activities by asset type.
    Returns high + medium in classifications[], low-confidence in skipped[].

    Falls back to keyword-only classification if AI is disabled or fails.
    Never raises.
    """
    if not settings.AI_ENABLED:
        logger.info("AI_ENABLED=false — using keyword fallback for classification")
        return _classify_assets_fallback(activities)

    try:
        return await _classify_assets_real(activities)
    except Exception as exc:
        logger.warning("AI classification failed (%s) — falling back to keyword", exc)
        return _classify_assets_fallback(activities)


# ---------------------------------------------------------------------------
# Stub implementations — AI Dev replaces these with real LLM calls
# ---------------------------------------------------------------------------

async def _detect_structure_real(rows: list[dict[str, Any]]) -> StructureResult:
    """
    TODO (AI Dev): implement real LLM call.
    - Use settings.AI_API_KEY, settings.AI_MODEL, settings.AI_PROVIDER
    - Hard timeout: settings.AI_TIMEOUT_STRUCTURE seconds
    - Load prompt from app/services/prompts/structure_detection.txt
    - Validate JSON schema — raise ValueError if shape doesn't match contract
    - Return StructureResult matching Section 2.1
    """
    # STUB: returns hardcoded fixture so orchestrator can be tested end-to-end
    logger.warning("_detect_structure_real is a stub — returning fixture data")
    return StructureResult(
        column_mapping={
            "name": "Task Name",
            "start_date": "Start",
            "end_date": "Finish",
            "duration": "Duration",
            "wbs_code": "WBS",
        },
        activities=[
            ActivityItem(
                id="row-1",
                name="Lift column cages L4",
                start="2026-04-07",
                finish="2026-04-09",
                parent_id=None,
                is_summary=False,
                level_name="Level 4",
                zone_name="Zone A",
            ),
            ActivityItem(
                id="row-2",
                name="Jump the Hoist L4",
                start="2026-04-10",
                finish="2026-04-10",
                parent_id=None,
                is_summary=False,
                level_name="Level 4",
                zone_name=None,
            ),
        ],
        completeness_score=95,
        missing_fields=[],
        notes="STUB — fixture data. AI Dev: replace with real LLM call.",
    )


async def _classify_assets_real(activities: list[dict[str, Any]]) -> ClassificationResult:
    """
    TODO (AI Dev): implement real LLM call.
    - Batch 100 activities per call; run parallel calls for 500+
    - Hard timeout: settings.AI_TIMEOUT_CLASSIFY seconds per batch
    - Load prompt from app/services/prompts/asset_classification.txt
    - Keyword boost layer applied before/after LLM call
    - Confidence tiers: high = AI + keyword agree, medium = AI alone, low = uncertain
    - Only return values from ALLOWED_ASSET_TYPES
    - Validate output shape — raise ValueError if malformed
    """
    logger.warning("_classify_assets_real is a stub — returning keyword-only results")
    return _classify_assets_fallback(activities)


# ---------------------------------------------------------------------------
# Deterministic fallbacks — always produce a usable result
# ---------------------------------------------------------------------------

# Keywords that map directly to asset types (keyword boost layer)
_KEYWORD_MAP: dict[str, str] = {
    "crane": "crane",
    "lift": "crane",
    "precast": "crane",
    "pre-cast": "crane",
    "column cage": "crane",
    "column cages": "crane",
    "hoist": "hoist",
    "loading bay": "loading_bay",
    "loading_bay": "loading_bay",
    "ewp": "ewp",
    "elevated work platform": "ewp",
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
    import re
    from datetime import datetime

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
        re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),   # dd/mm/yyyy
        re.compile(r"^\d{1,2}/\d{1,2}/\d{2}$"),    # dd/mm/yy
        re.compile(r"^\d{4}-\d{2}-\d{2}$"),         # yyyy-mm-dd
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
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(s, fmt)
                if "%y" in fmt:
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


def _classify_assets_fallback(activities: list[dict[str, Any]]) -> ClassificationResult:
    """
    Keyword-only classification fallback.
    Matches activity names against _KEYWORD_MAP. No AI call.
    Unmatched activities go to skipped[].
    """
    classifications: list[ClassificationItem] = []
    skipped: list[str] = []

    for activity in activities:
        activity_id = str(activity.get("id", ""))
        name = str(activity.get("name", "")).lower()

        matched_type: str | None = None
        for keyword, asset_type in sorted(_KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
            if keyword in name:
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
    )
