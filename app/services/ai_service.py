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
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

from ..core.config import settings

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

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
    """
    classifications: list[ClassificationItem]
    skipped: list[str]
    batch_tokens_used: int = 0


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


_MS_PROJECT_ROW_RE = re.compile(
    r"^(\d+)\s+(.+?)\s+(\d+)%\s+(\d+)\s+days?\s+"
    r"(\w+\s+\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(\w+\s+\d{1,2}/\d{1,2}/\d{2,4})\s*$"
)
_DAY_PREFIX_RE = re.compile(r"^[A-Za-z]{3}\s+")


def parse_ms_project_pdf(pdf_bytes: bytes) -> tuple[list[dict[str, Any]], str | None]:
    """
    Parse MS Project / P6 PDF Gantt exports using pypdf text extraction + regex.

    These PDFs have a structured left-hand activity table with columns:
        ID | Name | % Complete | Duration | Start | Finish
    followed by a Gantt chart on the right whose extracted text is noise.

    The regex naturally ignores all Gantt noise — no pdfplumber or Claude needed.
    This is the fast path for the common Australian construction programme format.

    Date format handled: "Mon 18/11/24" → stripped to "18/11/24" (DD/MM/YY).
    Summary rows detected by: all-uppercase meaningful words OR "Zone X" pattern.

    Returns (rows, error). Empty rows (not an error) if format doesn't match —
    the caller should then try pdfplumber or Claude Vision.
    """
    try:
        import pypdf as _pypdf
    except ImportError:
        return [], None

    try:
        reader = _pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        return [], str(exc)

    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue

        for line in text.splitlines():
            line = line.strip()
            m = _MS_PROJECT_ROW_RE.match(line)
            if not m:
                continue

            task_id, name, pct_str, dur_str, start_raw, finish_raw = m.groups()

            if task_id in seen_ids:
                continue
            seen_ids.add(task_id)

            # Strip "Mon ", "Tue " etc. day-of-week prefix from date strings
            start_clean = _DAY_PREFIX_RE.sub("", start_raw).strip()
            finish_clean = _DAY_PREFIX_RE.sub("", finish_raw).strip()

            # Summary detection:
            # (a) all meaningful words (len≥2) are uppercase → "SUPERSTRUCTURE",
            #     "LEVEL 1 ~ 2,200m2 CARPARK" (the lone "m" in m2 is ignored)
            # (b) "Zone A" / "Zone B" section-header pattern
            words = re.sub(r"[^a-zA-Z\s]", " ", name).split()
            meaningful = [w for w in words if len(w) >= 2]
            is_summary = (
                bool(meaningful) and all(w == w.upper() for w in meaningful)
            ) or bool(re.match(r"^Zone\s+\w+$", name.strip(), re.IGNORECASE))

            rows.append({
                "ID": task_id,
                "Name": name.strip(),
                "% Complete": f"{pct_str}%",
                "Duration": f"{dur_str} days",
                "Start": start_clean,
                "Finish": finish_clean,
                "Is Summary": "Yes" if is_summary else "No",
                "Is Milestone": "Yes" if dur_str == "0" else "No",
            })

    if rows:
        logger.info(
            "MS Project PDF parser: extracted %d rows from %d pages",
            len(rows),
            len(reader.pages),
        )
    return rows, None


async def extract_pdf_activities(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """
    Extract activity rows from a PDF programme file using Claude Vision.

    Called when pdfplumber table extraction returns insufficient data — typically
    for PDFs with complex Gantt chart layouts or image-heavy formatting.

    Pipeline:
    - Renders each PDF page to PNG at 1.5× scale using pypdfium2
    - Sends all page images to Claude with the pdf_extraction.txt prompt
    - Claude reads the visuals and returns structured JSON rows
    - Returns list of row dicts compatible with the CSV/XLSX detect_structure pipeline

    Hard timeout: 120 seconds (PDF vision is slower than text classification).
    Raises ValueError if Claude cannot find any activity rows.
    """
    import base64
    import io

    import pypdfium2

    client = _get_async_client()
    system_prompt = _load_prompt("pdf_extraction.txt")

    doc = pypdfium2.PdfDocument(pdf_bytes)
    # Cap at 8 pages — beyond that we risk token limits; most PMP tables fit in first 8
    n_pages = min(len(doc), 8)

    content_parts: list[dict[str, Any]] = []
    for i in range(n_pages):
        page = doc[i]
        # 1.5× scale: readable for Claude, ~half the token cost of 3×
        bitmap = page.render(scale=1.5)
        pil_img = bitmap.to_pil()
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        img_b64 = base64.standard_b64encode(buf.getvalue()).decode()
        content_parts.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img_b64,
            },
        })

    doc.close()

    content_parts.append({
        "type": "text",
        "text": (
            f"This is a {n_pages}-page construction programme schedule PDF. "
            "Extract all activity/task rows into structured JSON. "
            "Return ONLY valid JSON."
        ),
    })

    response = await asyncio.wait_for(
        client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": content_parts}],
        ),
        timeout=120.0,
    )

    data = _parse_json_response(response.content[0].text)
    rows: list[dict] = [r for r in (data.get("rows") or []) if isinstance(r, dict)]

    if not rows:
        raise ValueError(
            "Claude PDF vision returned no activity rows. "
            f"Notes: {data.get('notes', 'none')}"
        )

    logger.info(
        "PDF vision extraction: %d rows extracted from %d pages. Notes: %s",
        len(rows),
        n_pages,
        data.get("notes"),
    )
    return rows


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


def _get_async_client() -> anthropic.AsyncAnthropic:
    """Create a configured Anthropic async client. Raises if API key missing."""
    if not settings.AI_API_KEY:
        raise ValueError("AI_API_KEY is not configured — cannot make Claude API calls")
    return anthropic.AsyncAnthropic(api_key=settings.AI_API_KEY)


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

    # Last resort: find the largest JSON object in the response
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group())
        except json.JSONDecodeError:
            pass

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

    response = await asyncio.wait_for(
        client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ),
        timeout=float(settings.AI_TIMEOUT_STRUCTURE),
    )

    data = _parse_json_response(response.content[0].text)

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


async def _classify_batch(
    batch: list[dict[str, Any]],
    system_prompt: str,
    client: anthropic.AsyncAnthropic,
) -> dict[str, Any]:
    """
    Send a single batch of up to 100 activities to Claude for classification.
    Returns dict with 'classifications', 'skipped', and 'tokens_used'.
    Hard timeout: settings.AI_TIMEOUT_CLASSIFY seconds.
    """
    user_message = (
        f"Classify these {len(batch)} construction programme activities. "
        "Return ONLY valid JSON.\n\n"
        f"ACTIVITIES:\n{json.dumps(batch, ensure_ascii=False)}"
    )

    response = await asyncio.wait_for(
        client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ),
        timeout=float(settings.AI_TIMEOUT_CLASSIFY),
    )

    data = _parse_json_response(response.content[0].text)
    tokens_used = (
        (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)
    )

    return {
        "classifications": list(data.get("classifications") or []),
        "skipped": list(data.get("skipped") or []),
        "tokens_used": tokens_used,
    }


async def _classify_assets_real(activities: list[dict[str, Any]]) -> ClassificationResult:
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
    system_prompt = _load_prompt("asset_classification.txt")

    # Step 1: Keyword pre-screening
    keyword_matched: dict[str, str] = {}   # activity_id → asset_type
    ai_candidates: list[dict[str, Any]] = []

    for act in activities:
        act_id = str(act.get("id", ""))
        name_lower = str(act.get("name", "")).lower()

        matched_type: str | None = None
        for keyword, asset_type in sorted(_KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
            if keyword in name_lower:
                matched_type = asset_type
                break

        if matched_type:
            keyword_matched[act_id] = matched_type
        else:
            ai_candidates.append(act)

    logger.info(
        "Classification pre-screen: %d keyword-matched, %d for AI (%d total)",
        len(keyword_matched),
        len(ai_candidates),
        len(activities),
    )

    # Step 2: Batch remaining through Claude
    BATCH_SIZE = 100
    all_ai_results: dict[str, dict[str, Any]] = {}  # activity_id → result item
    total_tokens = 0

    if ai_candidates:
        batches = [
            ai_candidates[i:i + BATCH_SIZE]
            for i in range(0, len(ai_candidates), BATCH_SIZE)
        ]
        batch_tasks = [_classify_batch(batch, system_prompt, client) for batch in batches]

        # Parallel for large volumes, sequential otherwise
        if len(ai_candidates) >= 500:
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
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
            # Low-confidence items returned by Claude in skipped[]
            for skipped_id in result.get("skipped", []):
                sid = str(skipped_id)
                if sid and sid not in all_ai_results:
                    all_ai_results[sid] = {
                        "activity_id": sid,
                        "asset_type": None,
                        "confidence": "low",
                    }
            total_tokens += result.get("tokens_used", 0)

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

            if ai_type == keyword_type and ai_type in ALLOWED_ASSET_TYPES:
                # Both agree — highest confidence
                classifications.append(ClassificationItem(
                    activity_id=act_id,
                    asset_type=keyword_type,
                    confidence="high",
                    source="keyword_boost",
                    reasoning=ai_result.get("reasoning"),
                ))
            elif ai_type and ai_type in ALLOWED_ASSET_TYPES and ai_type != "none":
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

            if not ai_type or ai_type == "none" or ai_type not in ALLOWED_ASSET_TYPES:
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

    # Substring: specialty contains a key or a key contains specialty
    for key, types in TRADE_TO_ASSET_TYPES.items():
        if key in specialty or specialty in key:
            return list(types)

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
    # ── Crane ────────────────────────────────────────────────────────────────
    "crane": "crane",
    "precast": "crane",          # precast concrete panels always need a crane
    "pre-cast": "crane",
    "column cage": "crane",      # column cage reo/formwork lifts
    "column cages": "crane",
    "lift column": "crane",      # "Lift column cages", "Lift column formwork"
    "lift bd false work": "crane",   # "Lift BD false work" — explicit lift, not inspect
    "lift bd reo": "crane",
    "lift bd panels": "crane",
    "install bd false work": "crane",
    "install bd panels": "crane",
    "lift bd": "crane",          # "Lift BD reo / screw in bars" etc
    "lift precast": "crane",
    "install precast": "crane",
    "lift reo": "crane",
    "lift screw": "crane",
    "column formwork install": "crane",
    "install formwork": "crane",
    # NOTE: "formwork" / "false work" alone is intentionally NOT a keyword because
    # "BD false work inspection and sign off" should NOT match — AI handles that.
    "bd panels": "crane",        # Bubbledeck panel installs (large precast units)
    "bubbledeck": "crane",       # Bubbledeck = precast concrete deck system
    # ── Hoist ────────────────────────────────────────────────────────────────
    "hoist": "hoist",
    "jump the hoist": "hoist",   # MS Project specific phrasing
    "materials hoist": "hoist",
    "personnel hoist": "hoist",
    "platform hoist": "hoist",
    # ── Loading bay ──────────────────────────────────────────────────────────
    "loading bay": "loading_bay",
    "loading_bay": "loading_bay",
    "unloading": "loading_bay",
    "delivery": "loading_bay",   # material deliveries (reo delivery etc.)
    "reo delivery": "loading_bay",
    # ── EWP (Elevated Work Platform / scissor lift / boom lift) ──────────────
    "ewp": "ewp",
    "elevated work platform": "ewp",
    "scissor lift": "ewp",
    "boom lift": "ewp",
    "scaffold": "ewp",           # scaffold erect/dismantle needs EWP
    "scaffolding": "ewp",
    "install scaffold": "ewp",
    "handrail": "ewp",           # "Install scaffold handrail to live deck"
    "hvac": "ewp",
    "ductwork": "ewp",
    "electrical conduit": "ewp",
    "rough-in": "ewp",
    "rough in": "ewp",
    "ceiling": "ewp",
    "suspended ceiling": "ewp",
    "cladding": "ewp",
    "external cladding": "ewp",
    "waterproofing": "ewp",
    "painting": "ewp",
    # ── Concrete pump ────────────────────────────────────────────────────────
    "concrete pump": "concrete_pump",
    "concrete_pump": "concrete_pump",
    "concrete pour": "concrete_pump",
    "slab pour": "concrete_pump",
    "pour concrete": "concrete_pump",
    "pour slab": "concrete_pump",
    "column pour": "concrete_pump",  # "Ground floor column pour"
    "pour columns": "concrete_pump",
    "pour beam": "concrete_pump",
    "concrete pour floor slab": "concrete_pump",
    "slab and columns": "concrete_pump",
    "floor slab": "concrete_pump",
    "ramp infill": "concrete_pump",
    "levelling pour": "concrete_pump",
    "pour 1": "concrete_pump",   # "Slab pour, pour 1" / "Slab pour, pour 2"
    "pour 2": "concrete_pump",
    # ── Excavator ────────────────────────────────────────────────────────────
    "excavat": "excavator",      # excavation / excavating / excavator
    "bulk earthwork": "excavator",
    "earthwork": "excavator",
    "earthworks": "excavator",
    "demolish": "excavator",
    "demolition": "excavator",
    # NOTE: "strip slab" intentionally excluded — "Strip slab edge and set downs - tidy up"
    # is formwork clean-up (no asset), not excavation. Let AI handle it.
    "site clearance": "excavator",
    "site establishment": "excavator",
    "cut and fill": "excavator",
    "trenching": "excavator",
    "footing": "excavator",
    "footing excavation": "excavator",
    "piling": "excavator",
    # ── Forklift ─────────────────────────────────────────────────────────────
    "forklift": "forklift",
    "pallet": "forklift",
    "stack": "forklift",
    # ── Telehandler ──────────────────────────────────────────────────────────
    "telehandler": "telehandler",
    "telescopic": "telehandler",
    # ── Compactor ────────────────────────────────────────────────────────────
    "compactor": "compactor",
    "compaction": "compactor",
    "roller": "compactor",
    "vibrating plate": "compactor",
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

    # Detect is_summary and id columns by exact or fuzzy header name match
    _summary_col = next(
        (h for h in headers if h.lower().replace(" ", "_") in ("is_summary", "summary")),
        None,
    )
    _milestone_col = next(
        (h for h in headers if h.lower().replace(" ", "_") in ("is_milestone", "milestone")),
        None,
    )
    _id_col = next(
        (h for h in headers if h.upper() in ("ID", "TASK ID", "ACTIVITY ID", "WBS")),
        None,
    )
    if _summary_col:
        column_mapping["is_summary"] = _summary_col
    if _milestone_col:
        column_mapping["is_milestone"] = _milestone_col
    if _id_col:
        column_mapping["id"] = _id_col

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

    def _is_truthy(val: Any) -> bool:
        """Return True for Yes/True/1/true values used in summary/milestone flags."""
        return str(val).strip().lower() in ("yes", "true", "1", "y")

    activities: list[ActivityItem] = []

    for i, row in enumerate(rows):
        name = str(row.get(column_mapping.get("name", ""), "")).strip()
        if not name:
            continue

        start = _parse_date(row.get(column_mapping.get("start_date", ""), ""))
        finish = _parse_date(row.get(column_mapping.get("end_date", ""), ""))

        is_summary = False
        if _summary_col:
            is_summary = _is_truthy(row.get(_summary_col, ""))
        if not is_summary and _milestone_col:
            is_summary = _is_truthy(row.get(_milestone_col, ""))

        row_id = str(row.get(_id_col, "")).strip() if _id_col else f"row-{i}"
        if not row_id:
            row_id = f"row-{i}"

        activities.append(ActivityItem(
            id=row_id,
            name=name,
            start=start,
            finish=finish,
            parent_id=None,   # flat — no hierarchy in fallback
            is_summary=is_summary,
            level_name=None,
            zone_name=None,
        ))

    imported = len(activities)
    total = len(rows)
    # Bonus points for detecting the is_summary and id columns (max 90 for fallback)
    base = int((imported / total) * 75) if total else 0
    bonus = (5 if _summary_col else 0) + (5 if _id_col else 0) + (5 if _milestone_col else 0)
    score = min(90, base + bonus)

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
