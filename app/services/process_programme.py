"""
Background orchestrator for programme upload processing.

Flow:
  1. Read uploaded file (CSV or XLSX)
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

import csv
import hashlib
import importlib
import io
import logging
import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ..core.database import SessionLocal
from ..models.programme import ActivityAssetMapping, AISuggestionLog, ProgrammeActivity, ProgrammeUpload
from ..utils.storage import storage
from .ai_service import ALLOWED_ASSET_TYPES, ActivityItem, ClassificationResult, StructureResult, classify_assets, detect_structure

logger = logging.getLogger(__name__)

# Header hash cache: sha256(headers_tuple) -> StructureResult
# Reused across uploads with identical column headers — skips AI entirely.
_header_cache: dict[str, StructureResult] = {}


async def process_programme(upload_id: str) -> None:
    """
    Entry point called by BackgroundTasks.
    Opens its own DB session — never shares with the request session.
    """
    db = SessionLocal()
    try:
        await _run(upload_id, db)
    except Exception as exc:
        logger.exception("Unhandled error in process_programme for upload %s", upload_id)
        _mark_failed_as_committed(upload_id, db, str(exc))
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
    except Exception as exc:
        logger.error("Could not read file for upload %s: %s", upload_id, exc)
        _commit_degraded(upload, db, completeness_score=0.0, notes=["file_read_error"])
        return

    # 2. Parse rows from CSV or XLSX
    rows, parse_error = _parse_file(file_bytes, upload.file_name)
    if parse_error or not rows:
        logger.warning("Could not parse file for upload %s: %s", upload_id, parse_error)
        _commit_degraded(upload, db, completeness_score=0.0, notes=["parse_error"])
        return

    sample = rows[:100]

    # 3. Header hash cache — skip AI if same headers seen before
    header_hash = _compute_header_hash(sample)
    cached = _header_cache.get(header_hash)

    if cached:
        logger.info("Header hash cache hit for upload %s — reusing column_mapping", upload_id)
        structure = cached
    else:
        structure = await detect_structure(sample)
        _header_cache[header_hash] = structure

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
        for item, real_id in zip(all_activity_items, [id_map[i.id] for i in all_activity_items])
    ]
    try:
        classification = await classify_assets(activity_dicts)
        _write_classifications(classification, db)
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
    id_map: dict[str, uuid.UUID] = {item.id: uuid.uuid4() for item in items}

    db_rows: list[ProgrammeActivity] = []
    for sort_order, item in enumerate(items):
        real_id = id_map[item.id]
        real_parent = id_map.get(item.parent_id) if item.parent_id else None

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
            sort_order=sort_order,
            import_flags=flags if flags else None,
        ))

    db.bulk_save_objects(db_rows)
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

    items: list[ActivityItem] = []
    for i, row in enumerate(rows):
        name = str(row.get(name_col, "")).strip() if name_col else ""
        if not name:
            continue
        items.append(ActivityItem(
            id=f"extra-{start_index + i}",
            name=name,
            start=str(row.get(start_col, "")).strip() if start_col else None,
            finish=str(row.get(end_col, "")).strip() if end_col else None,
            parent_id=None,
            is_summary=False,
            level_name=None,
            zone_name=None,
        ))
    return items


def _parse_file(
    file_bytes: bytes,
    file_name: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Parse CSV or XLSX bytes into a list of row dicts.
    Returns (rows, error_message). error_message is None on success.
    """
    name_lower = file_name.lower()
    try:
        if name_lower.endswith(".csv"):
            text = file_bytes.decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            return [dict(row) for row in reader], None

        if name_lower.endswith((".xlsx", ".xls")):
            openpyxl = importlib.import_module("openpyxl")
            wb = openpyxl.load_workbook(
                io.BytesIO(file_bytes),
                read_only=True,
                data_only=True,
            )
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(next(rows_iter, []))]
            result = [dict(zip(headers, row)) for row in rows_iter]
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


def _parse_date(value: str | None) -> date | None:
    """Parse ISO date string to Python date. Returns None if unparseable."""
    if not value:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
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
                accepted=True,
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
                accepted=True,
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
    """Mark upload as committed with zero/low completeness rather than leaving it stuck."""
    upload.completeness_score = completeness_score
    upload.completeness_notes = {"missing_fields": notes, "notes": "Processing degraded."}
    upload.status = "committed"
    db.commit()


def _mark_failed_as_committed(upload_id: str, db: Session, reason: str) -> None:
    """Last-resort: if _run itself raises, still mark committed so PM isn't blocked."""
    try:
        upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
        if upload and upload.status == "processing":
            upload.completeness_score = 0.0
            upload.completeness_notes = {"missing_fields": ["unknown_error"], "notes": reason}
            upload.status = "committed"
            db.commit()
    except Exception:
        logger.exception("Could not mark upload %s as committed after failure", upload_id)
