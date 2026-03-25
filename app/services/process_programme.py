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

import sentry_sdk

from sqlalchemy.orm import Session, joinedload

from ..core.config import settings
from ..core.database import SessionLocal
from ..models.asset import Asset
from ..models.programme import ActivityAssetMapping, AISuggestionLog, ProgrammeActivity, ProgrammeUpload
from ..models.site_project import SiteProject
from ..utils.storage import storage
from .ai_service import (
    ActivityItem,
    ClassificationResult,
    StructureResult,
    classify_assets,
    classify_row_kind,
    detect_structure,
    parse_pct_raw,
    score_row_confidence,
    suggest_subcontractor_asset_types,
)
from .identity_service import normalize_activity_name, resolve_or_create_item
from .lookahead_engine import calculate_lookahead_for_project

logger = logging.getLogger(__name__)
SAFE_FAILURE_REASON = "processing_failed"

# Header hash cache: sha256(headers_tuple) -> cached structure metadata
# Reused across uploads with identical column headers — skips AI entirely.
_header_cache: dict[str, dict[str, Any]] = {}
_header_cache_lock = threading.Lock()


def preflight_validate(file_bytes: bytes, file_name: str) -> str | None:
    """
    Quick synchronous parse check — called before the background task is enqueued.
    Returns an error message string if the file is corrupt or empty, None if valid.

    PDFs get a lightweight check (page 1 only) — full parsing takes 30-60s on large
    Gantt exports and must not block the async event loop. The background task does
    the real parse. Run this via run_in_executor from async endpoints.
    """
    name_lower = file_name.lower()
    if name_lower.endswith(".pdf"):
        return _preflight_pdf(file_bytes)

    rows, error = _parse_file(file_bytes, file_name)
    if error:
        return error
    if not rows:
        return "File contains no data rows."
    return None


def _preflight_pdf(file_bytes: bytes) -> str | None:
    """Check page 1 only — confirms the PDF is readable and has at least one activity row."""
    import re as _re
    try:
        pdfplumber = importlib.import_module("pdfplumber")
        _RE = _re.compile(
            r"^(\d{3,6})\s+.+?\s+\d+%\s+\d+\s+days?"
            r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{1,2}/\d{1,2}/\d{2,4}"
        )
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if not pdf.pages:
                return "PDF contains no pages."
            text = pdf.pages[0].extract_text() or ""
            for line in text.splitlines():
                if _RE.match(line.strip()):
                    return None  # found at least one activity row — good enough
        return (
            "No schedule activities found on page 1 of PDF. "
            "Ensure the PDF is a P6 Gantt export with ID, Name, Start, and Finish columns."
        )
    except Exception as exc:
        return f"Could not read PDF: {exc}"


def process_programme(upload_id: str) -> None:
    """
    Entry point called by BackgroundTasks.
    Opens its own DB session — never shares with the request session.
    """
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("task", "process_programme")
        scope.set_tag("upload_id", upload_id)
        db = SessionLocal()
        try:
            asyncio.run(_run(upload_id, db))
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
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

    # Capture primitive values before releasing the connection — ORM objects
    # become detached/expired after Session.close().
    stored_file = upload.file
    _storage_path = stored_file.storage_path
    _file_name = upload.file_name
    _project_id = str(upload.project_id)
    _file_ext = _file_name.rsplit(".", 1)[-1].lower() if "." in _file_name else "unknown"
    sentry_sdk.set_tag("project_id", _project_id)
    sentry_sdk.set_tag("file_format", _file_ext)

    # Release the connection back to the pool BEFORE long-running CPU/AI work.
    # PDF parsing takes 30-60s and AI structure detection adds 10-15s — holding
    # the connection idle that long risks SSL EOF from Railway's load balancer
    # even though pool_pre_ping validated it at checkout.  The session remains
    # usable; the next query will check out a fresh, pre-pinged connection.
    db.close()

    # 1. Read file bytes from storage
    try:
        file_bytes = storage.read(_storage_path)
    except Exception:
        logger.exception("Could not read file for upload %s", upload_id)
        upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
        if upload:
            _commit_degraded(upload, db, completeness_score=0.0, notes=["file_read_error"])
        return

    # 2. Parse rows from file (CSV, XLSX/XLSM, or PDF)
    rows, parse_error = _parse_file(file_bytes, _file_name)
    if parse_error or not rows:
        logger.warning("Could not parse file for upload %s: %s", upload_id, parse_error)
        upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
        if upload:
            _commit_degraded(upload, db, completeness_score=0.0, notes=["parse_error"])
        return

    # 3. Detect column structure.
    is_pdf = _file_name.lower().endswith(".pdf")
    if is_pdf:
        # PDFs have fixed columns (ID, Name, Start, Finish) — pre-build the
        # activities from the known mapping, then let Claude score data quality.
        pdf_result = _pdf_structure(rows)
        try:
            ai_score = await detect_structure(rows[:100])
            score = min(ai_score.completeness_score, 90)
        except Exception:
            logger.warning("PDF upload %s — AI scoring failed, falling back to row-based score", upload_id)
            total = len(pdf_result.activities)
            dated = sum(1 for a in pdf_result.activities if a.start and a.finish)
            score = int((dated / total) * 90) if total > 0 else 0
        structure = StructureResult(
            column_mapping=pdf_result.column_mapping,
            activities=pdf_result.activities,
            completeness_score=score,
            missing_fields=pdf_result.missing_fields,
            notes=pdf_result.notes,
        )
        logger.info(
            "PDF upload %s — pre-determined column mapping, AI-scored completeness %d%% (%d activities)",
            upload_id, score, len(structure.activities),
        )
    else:
        sample = rows[:100]
        header_hash = _compute_header_hash(sample)
        with _header_cache_lock:
            cached = _header_cache.get(header_hash)

        if cached:
            logger.info("Header hash cache hit for upload %s — reusing cached structure metadata", upload_id)
            structure = _build_structure_from_cached_mapping(sample, cached)
        else:
            structure = await detect_structure(sample)
            with _header_cache_lock:
                _header_cache[header_hash] = {"column_mapping": dict(structure.column_mapping)}

    # 4. completeness_score: AI returns int 0–100, store as float 0.0–1.0
    completeness_float = max(0.0, min(1.0, structure.completeness_score / 100.0))

    # 6. Build activity list (no DB needed).
    #    PDFs: all rows already parsed into ActivityItems by _pdf_structure.
    #    CSV/XLSX: AI analysed first 100 rows; apply mapping deterministically to the rest.
    ai_activities = structure.activities
    extra_rows = [] if _file_name.lower().endswith(".pdf") else (rows[100:] if len(rows) > 100 else [])
    extra_activities = _apply_mapping(extra_rows, structure.column_mapping, start_index=len(ai_activities))
    all_activity_items = ai_activities + extra_activities

    # Re-acquire a fresh DB connection (pool_pre_ping verifies it) for all writes.
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        logger.error("process_programme: upload %s disappeared after parse", upload_id)
        return

    # 5. Persist column_mapping + score on programme_uploads
    upload.column_mapping = structure.column_mapping
    upload.completeness_score = completeness_float
    upload.completeness_notes = {
        "missing_fields": structure.missing_fields,
        "notes": structure.notes,
    }
    db.flush()

    # 7. Bulk-insert programme_activities
    #    Insert parents before children to satisfy the deferred FK constraint.
    #    The deferred FK means the constraint is checked at commit time, not per-row,
    #    but ordering is still safer for large batches.
    inserted_rows, id_map = _insert_activities(upload_id, all_activity_items, db)

    # 8. Classify assets — only task rows generate demand; summary and milestone
    #    rows are excluded so they don't pollute asset-type suggestions.
    activity_dicts = [
        {"id": str(real_id), "name": item.name}
        for item, real_id in zip(
            all_activity_items,
            [id_map.get(f"__idx_{idx}") for idx, _ in enumerate(all_activity_items)],
            strict=True,
        )
        if real_id is not None and item.activity_kind != "summary" and item.activity_kind != "milestone"
    ]

    # Fetch the project's registered assets so the AI classifies against what
    # is actually on site rather than a hardcoded generic list.
    project_assets: list[dict] = []
    try:
        db_assets = (
            db.query(Asset)
            .filter(Asset.project_id == upload.project_id)
            .all()
        )
        project_assets = [
            {"name": a.name, "type": a.type or "", "code": a.asset_code, "canonical_type": a.canonical_type or ""}
            for a in db_assets
        ]
        logger.info(
            "Loaded %d project assets for classification (project %s)",
            len(project_assets),
            upload.project_id,
        )
    except Exception as exc:
        logger.warning(
            "Could not load project assets for classification (project %s): %s — using generic prompt",
            _project_id,
            exc,
        )

    # Commit all writes so far (upload metadata + activities) before releasing the
    # connection. db.close() without a prior commit would roll back the transaction,
    # leaving the activity rows absent from the DB — causing FK violations when
    # activity_asset_mappings are inserted after classification.
    db.commit()

    # Release connection again before AI classification — large programmes run
    # many batches in parallel and the idle window can reach 60+ seconds.
    # upload is re-fetched below before any further writes.
    db.close()

    classification: ClassificationResult | None = None
    try:
        classification = await classify_assets(activity_dicts, project_assets=project_assets or None)
    except Exception as exc:
        logger.warning("Classification failed for upload %s (%s) — activities imported without mappings", upload_id, exc)

    # Re-acquire a fresh connection for post-classification writes.
    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
    if not upload:
        logger.error("process_programme: upload %s disappeared after classification", upload_id)
        return

    if classification is not None:
        if classification.batch_tokens_used is not None:
            upload.ai_tokens_used = classification.batch_tokens_used
        if classification.fallback_used and activity_dicts:
            # AI was unavailable — stamp a visible warning into completeness_notes
            # so the status poll response surfaces it on the frontend.
            notes_dict = dict(upload.completeness_notes or {})
            existing = str(notes_dict.get("notes") or "")
            warning = "AI classification unavailable — asset demand forecast may be incomplete. Re-upload once the AI service is restored."
            notes_dict["notes"] = f"{existing} | {warning}" if existing else warning
            notes_dict["ai_classification_fallback"] = True
            upload.completeness_notes = notes_dict
        try:
            with db.begin_nested():
                _write_classifications(
                    classification,
                    db,
                    upload_id=upload_id,
                    model_name=settings.AI_MODEL,
                    fallback_used=classification.fallback_used,
                )
        except Exception as exc:
            logger.warning(
                "Classification persistence failed for upload %s (%s) — continuing without mappings",
                upload_id,
                exc,
            )
            # A connection-level error (e.g. SSL EOF) inside begin_nested() leaves
            # the session in PendingRollback state.  db.is_active is False in that
            # case.  We must rollback fully before any further DB work; and because
            # the outer transaction (activities) was on the same dead connection it
            # is also gone, so we cannot commit as "committed".
            if not db.is_active:
                try:
                    db.rollback()
                except Exception as rb_exc:
                    logger.warning("Rollback failed during degraded classification cleanup for upload %s: %s", upload_id, rb_exc)
                _mark_failed_as_committed(
                    upload_id,
                    db,
                    "DB connection lost during classification persistence — re-upload to retry.",
                )
                return

    # 9b. Assign subcontractor suggestions based on trade specialty + asset type match.
    #     Best-effort — failure does not block commit. Wrapped in a savepoint so any
    #     SQL error rolls back only to here and does not poison the outer session.
    try:
        with db.begin_nested():
            _assign_subcontractor_suggestions(upload.project_id, upload_id, db)
    except Exception as exc:
        logger.warning(
            "Subcontractor suggestion assignment failed for upload %s (%s) — continuing",
            upload_id,
            exc,
        )
        if not db.is_active:
            try:
                db.rollback()
            except Exception as rb_exc:
                logger.warning("Rollback failed during degraded subcontractor assignment for upload %s: %s", upload_id, rb_exc)
            _mark_failed_as_committed(
                upload_id,
                db,
                "DB connection lost during subcontractor assignment — re-upload to retry.",
            )
            return

    # 10. Mark committed
    upload.status = "committed"

    db.commit()

    logger.info(
        "process_programme complete: upload=%s activities=%d completeness=%.0f%%",
        upload_id,
        inserted_rows,
        completeness_float * 100,
    )

    # Refresh the lookahead snapshot immediately so the dashboard reflects this
    # upload without waiting for the nightly job.
    try:
        calculate_lookahead_for_project(uuid.UUID(_project_id), db)
        logger.info("Lookahead snapshot refreshed for project %s", _project_id)
    except Exception:
        logger.warning("Lookahead snapshot refresh failed for project %s — nightly job will catch up", _project_id)


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
    # Cache item-id resolution per upload so repeated activity names don't hit the DB twice.
    item_id_cache: dict[str, uuid.UUID | None] = {}
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

        cache_key = normalize_activity_name(item.name)
        if cache_key not in item_id_cache:
            item_id_cache[cache_key] = resolve_or_create_item(db, item.name)
        item_id = item_id_cache[cache_key]

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
            pct_complete=item.pct_complete,
            activity_kind=item.activity_kind,
            row_confidence=item.row_confidence,
            item_id=item_id,
        ))

    db.bulk_save_objects(db_rows)
    id_map: dict[str, uuid.UUID] = {f"__idx_{idx}": row_uuid for idx, row_uuid in row_id_map.items()}
    # Preserve first-seen token lookup for any downstream callers that still use token keys.
    for token, uuids in token_map.items():
        if uuids:
            id_map[token] = uuids[0]
    return len(db_rows), id_map


def _pdf_structure(rows: list[dict[str, Any]]) -> StructureResult:
    """
    Build a StructureResult directly from PDF rows without calling detect_structure.
    The PDF regex parser always outputs fixed columns: ID, Name, PctComplete, Start, Finish.
    """
    _PDF_MAPPING = {
        "id": "ID",
        "name": "Name",
        "pct_complete": "PctComplete",
        "start_date": "Start",
        "end_date": "Finish",
    }
    activities = _apply_mapping(rows, _PDF_MAPPING, start_index=0)
    total = len(activities)
    dated = sum(1 for a in activities if a.start and a.finish)
    score = int((dated / total) * 90) if total > 0 else 0
    return StructureResult(
        column_mapping=_PDF_MAPPING,
        activities=activities,
        completeness_score=score,
        missing_fields=[],
        notes="P6 Gantt PDF — column structure pre-determined by parser.",
    )


def _apply_mapping(
    rows: list[dict[str, Any]],
    column_mapping: dict[str, str],
    start_index: int,
) -> list[ActivityItem]:
    """
    Apply detected column_mapping deterministically to rows beyond the AI sample.
    Returns flat ActivityItems (no hierarchy — AI only detects hierarchy in sample).
    Populates Stage 1 correctness fields: pct_complete, activity_kind, row_confidence.
    """
    name_col = column_mapping.get("name")
    start_col = column_mapping.get("start_date")
    end_col = column_mapping.get("end_date")
    parent_col = column_mapping.get("parent_id")
    source_id_col = column_mapping.get("id") or column_mapping.get("wbs_code")
    is_summary_col = column_mapping.get("is_summary")
    level_col = column_mapping.get("level_name")
    zone_col = column_mapping.get("zone_name")
    pct_col = column_mapping.get("pct_complete")

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

        pct_complete = parse_pct_raw(row.get(pct_col) if pct_col else None)

        activity_kind = classify_row_kind(
            is_summary=is_summary,
            start=start_value,
            finish=end_value,
        )
        row_confidence = score_row_confidence(
            name=name,
            start=start_value,
            finish=end_value,
            activity_kind=activity_kind,
        )

        items.append(ActivityItem(
            id=source_value or f"extra-{start_index + i}",
            name=name,
            start=start_value,
            finish=end_value,
            parent_id=parent_value or None,
            is_summary=is_summary,
            level_name=level_value or None,
            zone_name=zone_value or None,
            pct_complete=pct_complete,
            activity_kind=activity_kind,
            row_confidence=row_confidence,
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
    Parse CSV, XLSX/XLSM, or PDF bytes into a list of row dicts.
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

        if name_lower.endswith(".pdf"):
            # P6 Gantt PDFs export as landscape tables where pdfplumber cannot
            # reliably separate cells — Gantt bar labels bleed into date columns.
            # Text extraction + regex is far more reliable for this format.
            import re as _re
            pdfplumber = importlib.import_module("pdfplumber")

            # Matches lines like:
            #   1028 SITE CLEARANCE 100% 17 daysFri 17/01/25 Sat 8/02/25 SITE CLEARANCE
            # Duration and start day-of-week are concatenated with no space in P6 exports.
            # Group 3 captures % complete (integer, 0-100).
            _ACTIVITY_RE = _re.compile(
                r"^(\d{3,6})\s+"                                  # Group 1: ID
                r"(.+?)\s+"                                        # Group 2: Name (non-greedy)
                r"(\d+)%\s+"                                       # Group 3: % Complete
                r"\d+\s+days?"                                     # Duration (discard)
                r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"             # Start DOW (discard)
                r"(\d{1,2}/\d{1,2}/\d{2,4})\s+"                  # Group 4: Start date
                r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"             # Finish DOW (discard)
                r"(\d{1,2}/\d{1,2}/\d{2,4})",                    # Group 5: Finish date
            )

            # Noise suppression: skip lines that are page headers, legends,
            # column-header repetitions, revision footers, or page titles.
            # These patterns are checked BEFORE the activity regex.
            _NOISE_RE = _re.compile(
                r"(?:"
                r"^(?:activity\s+id|id\s+activity|wbs|name\s+%|task\s+name)"  # column headers
                r"|^(?:revision|data\s+date|print\s+date|printed|page\s+\d+)"  # footers
                r"|^(?:critical|near\s*critical|total\s+float|driving)"        # legend keys
                r"|^\d+\s+of\s+\d+$"                                           # page N of M
                r")",
                _re.IGNORECASE,
            )

            all_rows: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for line in text.splitlines():
                        stripped = line.strip()
                        if not stripped or _NOISE_RE.match(stripped):
                            continue
                        m = _ACTIVITY_RE.match(stripped)
                        if m and m.group(1) not in seen_ids:
                            seen_ids.add(m.group(1))
                            pct_raw = m.group(3)
                            all_rows.append({
                                "ID": m.group(1),
                                "Name": m.group(2).strip(),
                                "PctComplete": int(pct_raw) if pct_raw.isdigit() else None,
                                "Start": m.group(4),
                                "Finish": m.group(5),
                            })

            if not all_rows:
                return [], (
                    "No schedule activities found in PDF. "
                    "Ensure the PDF is a P6 Gantt export with ID, Name, Start, and Finish columns."
                )
            return all_rows, None

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

    activities = _apply_mapping(rows, column_mapping, start_index=0)
    total_rows = len(rows)
    dated_rows = 0
    for item in activities:
        if _parse_date(item.start) and _parse_date(item.finish):
            dated_rows += 1

    completeness = int((dated_rows / total_rows) * 100) if total_rows else 0
    missing_fields = [
        field_name
        for field_name in ("name", "start_date", "end_date")
        if field_name not in column_mapping
    ]

    return StructureResult(
        column_mapping=column_mapping,
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

    # Space-separated text-month formats ("01 Mar 2026") must be tried on the
    # full string before the space-split below strips the month and year away.
    # The manual %y pivot (yy >= 69 → 1900+yy, else 2000+yy) overrides Python's
    # default strptime pivot of 2068/1969 to match our codebase-wide convention.
    for fmt in ("%d %b %Y", "%d %b %y"):
        try:
            parsed = datetime.strptime(text, fmt)
            if "%y" in fmt and "%Y" not in fmt:
                yy = parsed.year % 100
                parsed = parsed.replace(year=1900 + yy if yy >= 69 else 2000 + yy)
            return parsed.date()
        except (ValueError, AttributeError):
            pass

    # If datetime-like text includes a date prefix, parse only the date portion.
    date_only_candidate = text
    if "T" in text:
        date_only_candidate = text.split("T", 1)[0].strip()
    elif " " in text:
        date_only_candidate = text.split(" ", 1)[0].strip()

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%d-%b-%y"):
        try:
            parsed = datetime.strptime(date_only_candidate, fmt)
            if "%y" in fmt and "%Y" not in fmt:
                yy = parsed.year % 100
                parsed = parsed.replace(year=1900 + yy if yy >= 69 else 2000 + yy)
            return parsed.date()
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


def _write_classifications(
    classification: ClassificationResult,
    db: Session,
    *,
    upload_id: str | None = None,
    model_name: str | None = None,
    fallback_used: bool | None = None,
) -> None:
    """
    Persist classification output.

    Rules:
    - high + medium: mapping row with auto_committed=True
    - low (skipped): mapping row with asset_type=None, auto_committed=False
    - every suggestion (including low placeholders) gets an ai_suggestion_logs row

    Keyword-only args populate the new observability columns on AISuggestionLog:
      upload_id    — UUID of the programme_uploads row that triggered this run
      model_name   — AI model name (e.g. "claude-haiku-4-5-20251001") or None if fallback
      fallback_used — True when keyword-only fallback ran instead of AI
    """
    mapping_rows: list[ActivityAssetMapping] = []
    suggestion_rows: list[AISuggestionLog] = []

    for item in classification.classifications:
        normalised_type = (item.asset_type or "").strip().lower()
        if not normalised_type or normalised_type == "none":
            logger.warning(
                "Skipping none/empty asset_type for activity=%s",
                item.activity_id,
            )
            continue
        item.asset_type = normalised_type

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
                upload_id=upload_id,
                suggested_asset_type=item.asset_type,
                confidence=confidence,
                accepted=auto_commit,
                correction=None,
                source=item.source,
                pipeline_stage="classify_assets",
                model_name=None if fallback_used else model_name,
                fallback_used=fallback_used,
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
                upload_id=upload_id,
                suggested_asset_type=None,
                confidence="low",
                accepted=False,
                correction=None,
                source="keyword" if fallback_used else "ai",
                pipeline_stage="classify_assets",
                model_name=None if fallback_used else model_name,
                fallback_used=fallback_used,
            )
        )

    if mapping_rows:
        db.bulk_save_objects(mapping_rows)
    if suggestion_rows:
        db.bulk_save_objects(suggestion_rows)


def _assign_subcontractor_suggestions(
    project_id: str,
    upload_id: str,
    db: Session,
) -> None:
    """
    Auto-assign subcontractor_id on ActivityAssetMapping rows when exactly one
    subcontractor on the project has a trade_specialty that maps to that asset type.

    Logic:
    - Query all subcontractors assigned to the project.
    - Build a map: asset_type → [subcontractor_ids] using suggest_subcontractor_asset_types().
    - For each mapping row with auto_committed=True and a known asset_type:
        - If exactly one subcontractor's trade matches → set subcontractor_id.
        - If zero or multiple match → leave subcontractor_id null (ambiguous).
    """
    project = (
        db.query(SiteProject)
        .options(joinedload(SiteProject.subcontractors))
        .filter(SiteProject.id == project_id)
        .first()
    )
    if not project or not project.subcontractors:
        return

    sub_dicts = [
        {"id": str(sub.id), "trade_specialty": sub.trade_specialty or ""}
        for sub in project.subcontractors
    ]
    suggestions = suggest_subcontractor_asset_types(sub_dicts)

    # Build: asset_type → list of sub_ids whose trade maps to it
    asset_to_subs: dict[str, list[str]] = {}
    for suggestion in suggestions:
        for asset_type in suggestion.suggested_asset_types:
            asset_to_subs.setdefault(asset_type, []).append(suggestion.subcontractor_id)

    # Fetch all auto-committed mapping rows for this upload
    mapping_rows = (
        db.query(ActivityAssetMapping)
        .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
        .filter(
            ProgrammeActivity.programme_upload_id == upload_id,
            ActivityAssetMapping.auto_committed.is_(True),
            ActivityAssetMapping.manually_corrected.is_(False),
            ActivityAssetMapping.asset_type.isnot(None),
            ActivityAssetMapping.subcontractor_id.is_(None),
        )
        .all()
    )

    assigned_count = 0
    for mapping in mapping_rows:
        asset_type = mapping.asset_type
        matching_subs = asset_to_subs.get(asset_type, [])
        if len(matching_subs) == 1:
            mapping.subcontractor_id = uuid.UUID(matching_subs[0])
            assigned_count += 1

    if assigned_count:
        db.flush()
        logger.info(
            "Subcontractor assignment: %d mappings auto-assigned for upload %s",
            assigned_count,
            upload_id,
        )


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
