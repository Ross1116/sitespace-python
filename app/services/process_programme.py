"""
Background orchestrator for programme upload processing.

Flow:
    1. Read uploaded file (CSV or XLSX/XLSM)
  2. Call ai_service.detect_structure() on first 100 rows
     (falls back to regex if AI unavailable/fails — always produces a result)
  3. Convert completeness_score int → float, write to programme_uploads
  4. Bulk-insert programme_activities (parents before children)
  5. Call ai_service.classify_assets() on all inserted activities
     - high + medium → activity_asset_mappings with auto_committed=True
     - low → activity_asset_mappings with auto_committed=False, asset_type=None
     - all suggestions logged to ai_suggestion_logs
  6. Set programme_uploads.status = "committed"

Called from POST /api/programmes/upload via FastAPI BackgroundTasks.
Never raises to the caller — all exceptions are caught, logged, and result
in a degraded commit rather than a silent failure.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib
import io
import logging
import threading
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..core.database import SessionLocal
from ..models.programme import ActivityAssetMapping, AISuggestionLog, ProgrammeActivity, ProgrammeUpload
from ..utils.storage import storage
from .ai_service import ALLOWED_ASSET_TYPES, ActivityItem, ClassificationResult, StructureResult, classify_assets, detect_structure

logger = logging.getLogger(__name__)
SAFE_FAILURE_REASON = "processing_failed"

# Header hash cache: sha256(headers_tuple) -> cached structure metadata
# Reused across uploads with identical column headers — skips AI entirely.
_header_cache: dict[str, dict[str, Any]] = {}
_header_cache_lock = threading.Lock()


def process_programme(upload_id: str) -> None:
    """
    Entry point called by BackgroundTasks.
    Opens its own DB session — never shares with the request session.
    """
    db = SessionLocal()
    try:
        asyncio.run(_run(upload_id, db))
    except Exception:
        logger.exception("Unhandled error in process_programme for upload %s", upload_id)
        try:
            db.rollback()
        except Exception:
            logger.exception("Rollback failed in process_programme for upload %s", upload_id)
        _mark_failed_as_committed(upload_id, db, SAFE_FAILURE_REASON)
    finally:
        db.close()


async def _run(upload_id: str, db: Session) -> None:
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        logger.error("process_programme: upload %s not found", upload_id)
        return

    # 1. Read file bytes from storage
    stored_file = upload.file
    try:
        file_bytes = storage.read(stored_file.storage_path)
    except Exception:
        logger.exception("Could not read file for upload %s", upload_id)
        _commit_degraded(upload, db, completeness_score=0.0, notes=["file_read_error"])
        return

    # 2. Parse rows from CSV or XLSX/XLSM
    rows, parse_error = _parse_file(file_bytes, upload.file_name)
    if parse_error or not rows:
        logger.warning("Could not parse file for upload %s: %s", upload_id, parse_error)
        _commit_degraded(upload, db, completeness_score=0.0, notes=["parse_error"])
        return

    sample = rows[:100]

    # 3. Header hash cache — skip AI if same headers seen before
    header_hash = _compute_header_hash(sample)
    with _header_cache_lock:
        cached = _header_cache.get(header_hash)

    if cached:
        logger.info("Header hash cache hit for upload %s — reusing cached structure metadata", upload_id)
        structure = _build_structure_from_cached_mapping(sample, cached)
    else:
        structure = await detect_structure(sample)
        with _header_cache_lock:
            _header_cache[header_hash] = {
                "column_mapping": dict(structure.column_mapping),
                # Persist hierarchy-related mapping keys so cache hits can preserve
                # parent/summary/level/zone metadata when those columns exist.
                "hierarchy_mapping": {
                    key: structure.column_mapping[key]
                    for key in ("parent_id", "is_summary", "level_name", "zone_name")
                    if key in structure.column_mapping
                },
            }

    # 4. completeness_score: AI returns int 0–100, store as float 0.0–1.0
    completeness_float = max(0.0, min(1.0, structure.completeness_score / 100.0))

    # 5. Persist column_mapping + score on programme_uploads
    upload.column_mapping = structure.column_mapping
    upload.completeness_score = completeness_float
    upload.completeness_notes = {
        "missing_fields": structure.missing_fields,
        "notes": structure.notes,
    }
    db.flush()

    # 6. Build activity rows from AI output + remaining rows
    #    AI analyses first 100 rows and returns the parsed activities array.
    #    For files > 100 rows, apply column_mapping deterministically to the rest.
    ai_activities = structure.activities
    extra_rows = rows[100:] if len(rows) > 100 else []
    extra_activities = _apply_mapping(extra_rows, structure.column_mapping, start_index=len(ai_activities))

    all_activity_items = ai_activities + extra_activities

    # 7. Bulk-insert programme_activities
    #    Insert parents before children to satisfy the deferred FK constraint.
    #    The deferred FK means the constraint is checked at commit time, not per-row,
    #    but ordering is still safer for large batches.
    inserted_rows, id_map = _insert_activities(upload_id, all_activity_items, db)

    # 8. Classify assets for all inserted activities
    #    Pass real UUID strings so classify_assets() can return them in classifications[].activity_id
    activity_dicts = [
        {"id": str(real_id), "name": item.name}
        for item, real_id in zip(
            all_activity_items,
            [id_map.get(f"__idx_{idx}") for idx, _ in enumerate(all_activity_items)],
            strict=True,
        )
        if real_id is not None
    ]
    try:
        classification = await classify_assets(activity_dicts)
        try:
            with db.begin_nested():
                _write_classifications(classification, db)
        except Exception as exc:
            logger.warning(
                "Classification persistence failed for upload %s (%s) — continuing without mappings",
                upload_id,
                exc,
            )
    except Exception as exc:
        logger.warning("Classification failed for upload %s (%s) — activities imported without mappings", upload_id, exc)

    # 9. Mark committed
    upload.status = "committed"
    db.commit()

    logger.info(
        "process_programme complete: upload=%s activities=%d completeness=%.0f%%",
        upload_id,
        inserted_rows,
        completeness_float * 100,
    )


def _insert_activities(
    upload_id: str,
    items: list[ActivityItem],
    db: Session,
) -> tuple[int, dict[str, uuid.UUID]]:
    """
    Insert all activities. Returns (count, temp_id → real_uuid mapping).
    The id_map is used by the classification step to pass real UUIDs to classify_assets().
    """
    # Pass 1: allocate a per-row identity and a separate token lookup.
    # row_id_map prevents duplicate source tokens from overwriting earlier UUIDs.
    row_id_map: dict[int, uuid.UUID] = {idx: uuid.uuid4() for idx, _ in enumerate(items)}
    token_map: dict[str, list[uuid.UUID]] = {}
    for idx, item in enumerate(items):
        source_id = _normalize_parent_token(item.id)
        if not source_id:
            continue
        token_map.setdefault(source_id, []).append(row_id_map[idx])

    # Pass 2: build rows with resolved parent links.
    db_rows: list[ProgrammeActivity] = []
    for sort_order, item in enumerate(items):
        source_id = _normalize_parent_token(item.id) or f"__idx_{sort_order}"
        real_id = row_id_map[sort_order]
        parent_token = _normalize_parent_token(item.parent_id)
        parent_candidates = token_map.get(parent_token, []) if parent_token else []
        real_parent = parent_candidates[0] if parent_candidates else None

        start = _parse_date(item.start)
        end = _parse_date(item.finish)

        flags: list[str] = []
        if item.parent_id and real_parent is None:
            flags.append("unstructured")
        if not start or not end:
            flags.append("dates_missing")

        db_rows.append(ProgrammeActivity(
            id=real_id,
            programme_upload_id=upload_id,
            parent_id=real_parent,
            name=item.name,
            start_date=start,
            end_date=end,
            duration_days=((end - start).days if start and end else None),
            level_name=item.level_name,
            zone_name=item.zone_name,
            is_summary=item.is_summary,
            wbs_code=source_id,
            sort_order=sort_order,
            import_flags=flags if flags else None,
        ))

    db.bulk_save_objects(db_rows)
    id_map: dict[str, uuid.UUID] = {f"__idx_{idx}": row_uuid for idx, row_uuid in row_id_map.items()}
    # Preserve first-seen token lookup for any downstream callers that still use token keys.
    for token, uuids in token_map.items():
        if uuids:
            id_map[token] = uuids[0]
    return len(db_rows), id_map


def _apply_mapping(
    rows: list[dict[str, Any]],
    column_mapping: dict[str, str],
    start_index: int,
) -> list[ActivityItem]:
    """
    Apply detected column_mapping deterministically to rows beyond the AI sample.
    Returns flat ActivityItems (no hierarchy — AI only detects hierarchy in sample).
    """
    name_col = column_mapping.get("name")
    start_col = column_mapping.get("start_date")
    end_col = column_mapping.get("end_date")
    parent_col = column_mapping.get("parent_id")
    source_id_col = column_mapping.get("id") or column_mapping.get("wbs_code")
    is_summary_col = column_mapping.get("is_summary")
    level_col = column_mapping.get("level_name")
    zone_col = column_mapping.get("zone_name")

    items: list[ActivityItem] = []
    for i, row in enumerate(rows):
        name_raw = row.get(name_col) if name_col else None
        name = "" if name_raw is None else str(name_raw).strip()
        if not name:
            continue
        start_value = _normalize_date_cell(row.get(start_col)) if start_col else None
        end_value = _normalize_date_cell(row.get(end_col)) if end_col else None
        parent_value = _normalize_parent_token(row.get(parent_col)) if parent_col else None
        level_raw = row.get(level_col) if level_col else None
        zone_raw = row.get(zone_col) if zone_col else None
        level_value = "" if level_raw is None else str(level_raw).strip()
        zone_value = "" if zone_raw is None else str(zone_raw).strip()
        source_value = _normalize_parent_token(row.get(source_id_col)) if source_id_col else None
        is_summary = _to_bool(row.get(is_summary_col)) if is_summary_col else False
        items.append(ActivityItem(
            id=source_value or f"extra-{start_index + i}",
            name=name,
            start=start_value,
            finish=end_value,
            parent_id=parent_value or None,
            is_summary=is_summary,
            level_name=level_value or None,
            zone_name=zone_value or None,
        ))
    return items


def _normalize_date_cell(value: Any) -> str | None:
    """Normalize spreadsheet date/datetime cells to ISO date strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text_value = str(value).strip()
    return text_value or None


def _normalize_parent_token(value: Any) -> str | None:
    """Normalize source tokens used to resolve parent-child activity links."""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value).strip()

    token = str(value).strip()
    return token or None


def _to_bool(value: Any) -> bool:
    """Convert common spreadsheet boolean representations to bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0

    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "t"}


def _parse_file(
    file_bytes: bytes,
    file_name: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Parse CSV or XLSX/XLSM bytes into a list of row dicts.
    Returns (rows, error_message). error_message is None on success.
    """
    name_lower = file_name.lower()
    try:
        if name_lower.endswith(".csv"):
            text = file_bytes.decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            return [dict(row) for row in reader], None

        if name_lower.endswith((".xlsx", ".xlsm")):
            openpyxl = importlib.import_module("openpyxl")
            wb = openpyxl.load_workbook(
                io.BytesIO(file_bytes),
                read_only=True,
                data_only=True,
            )
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(next(rows_iter, []))]
            result = [dict(zip(headers, row, strict=False)) for row in rows_iter]
            wb.close()
            return result, None

        return [], f"Unsupported file type: {file_name}"
    except Exception as exc:
        return [], str(exc)


def _compute_header_hash(rows: list[dict[str, Any]]) -> str:
    """SHA-256 of the sorted header tuple — used for cache key."""
    if not rows:
        return ""
    headers = tuple(sorted(rows[0].keys()))
    return hashlib.sha256(str(headers).encode()).hexdigest()


def _build_structure_from_cached_mapping(
    rows: list[dict[str, Any]],
    cached_structure: dict[str, Any],
) -> StructureResult:
    """
    Build a fresh per-file StructureResult using cached header mapping only.

    Row-derived fields (activities, completeness, notes, missing_fields) are
    recalculated for each upload to avoid cross-file contamination.
    """
    column_mapping = dict(cached_structure.get("column_mapping", {}))
    hierarchy_mapping = dict(cached_structure.get("hierarchy_mapping", {}))
    merged_mapping = {**column_mapping, **hierarchy_mapping}

    activities = _apply_mapping(rows, merged_mapping, start_index=0)
    total_rows = len(rows)
    dated_rows = 0
    for item in activities:
        if _parse_date(item.start) and _parse_date(item.finish):
            dated_rows += 1

    completeness = int((dated_rows / total_rows) * 100) if total_rows else 0
    missing_fields = [
        field_name
        for field_name in ("name", "start_date", "end_date")
        if field_name not in merged_mapping
    ]

    return StructureResult(
        column_mapping=dict(column_mapping),
        activities=activities,
        completeness_score=completeness,
        missing_fields=missing_fields,
        notes="Header-hash cache hit: reused column mapping and reparsed current rows.",
    )


def _parse_date(value: str | None) -> date | None:
    """Parse date/datetime strings to Python date. Returns None if unparseable."""
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    # Fast path: datetime/date ISO variants (with optional trailing Z).
    iso_candidate = text[:-1] if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(iso_candidate).date()
    except ValueError:
        pass

    # Common datetime variants with explicit separator.
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    # If datetime-like text includes a date prefix, parse only the date portion.
    date_only_candidate = text
    if "T" in text:
        date_only_candidate = text.split("T", 1)[0].strip()
    elif " " in text:
        date_only_candidate = text.split(" ", 1)[0].strip()

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_only_candidate, fmt).date()
        except (ValueError, AttributeError):
            pass
    return None


def _normalize_mapping_source(source: str | None) -> str:
    """Normalize classifier source values to DB enum-like values."""
    if not source:
        return "ai"
    normalized = source.strip().lower()
    if normalized in {"ai", "manual", "keyword"}:
        return normalized
    if normalized in {"keyword_boost", "keyword_fallback"}:
        return "keyword"
    return "ai"


def _write_classifications(classification: ClassificationResult, db: Session) -> None:
    """
    Persist classification output.

    Rules:
    - high + medium: mapping row with auto_committed=True
    - low (skipped): mapping row with asset_type=None, auto_committed=False
    - every suggestion (including low placeholders) gets an ai_suggestion_logs row
    """
    mapping_rows: list[ActivityAssetMapping] = []
    suggestion_rows: list[AISuggestionLog] = []

    for item in classification.classifications:
        if item.asset_type not in ALLOWED_ASSET_TYPES:
            logger.warning(
                "Skipping invalid classification asset_type='%s' for activity=%s",
                item.asset_type,
                item.activity_id,
            )
            continue

        confidence = (item.confidence or "").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            logger.warning(
                "Invalid classification confidence='%s' for activity=%s; defaulting to low",
                item.confidence,
                item.activity_id,
            )
            confidence = "low"

        auto_commit = confidence in {"high", "medium"}

        mapping_rows.append(
            ActivityAssetMapping(
                id=uuid.uuid4(),
                programme_activity_id=item.activity_id,
                asset_type=item.asset_type if auto_commit else None,
                confidence=confidence,
                source=_normalize_mapping_source(item.source),
                auto_committed=auto_commit,
            )
        )

        suggestion_rows.append(
            AISuggestionLog(
                id=uuid.uuid4(),
                activity_id=item.activity_id,
                suggested_asset_type=item.asset_type,
                confidence=confidence,
                accepted=auto_commit,
                correction=None,
            )
        )

    for skipped_activity_id in classification.skipped:
        mapping_rows.append(
            ActivityAssetMapping(
                id=uuid.uuid4(),
                programme_activity_id=skipped_activity_id,
                asset_type=None,
                confidence="low",
                source="ai",
                auto_committed=False,
            )
        )
        suggestion_rows.append(
            AISuggestionLog(
                id=uuid.uuid4(),
                activity_id=skipped_activity_id,
                suggested_asset_type=None,
                confidence="low",
                accepted=False,
                correction=None,
            )
        )

    if mapping_rows:
        db.bulk_save_objects(mapping_rows)
    if suggestion_rows:
        db.bulk_save_objects(suggestion_rows)


def _commit_degraded(
    upload: ProgrammeUpload,
    db: Session,
    completeness_score: float,
    notes: list[str],
) -> None:
    """Mark upload as degraded terminal state instead of committed source data."""
    upload.completeness_score = completeness_score
    upload.completeness_notes = {"missing_fields": notes, "notes": "Processing degraded."}
    upload.status = "degraded"
    db.commit()


def _mark_failed_as_committed(upload_id: str, db: Session, reason: str) -> None:
    """Last-resort: if _run raises, mark as degraded without leaking internals."""
    try:
        upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
        if upload and upload.status == "processing":
            upload.completeness_score = 0.0
            upload.completeness_notes = {"missing_fields": ["unknown_error"], "notes": reason}
            upload.status = "degraded"
            db.commit()
    except Exception:
        logger.exception("Could not mark upload %s as committed after failure", upload_id)
