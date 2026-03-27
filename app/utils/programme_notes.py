from __future__ import annotations

import ast
import json
from typing import Any


DEFAULT_PROGRAMME_COMPLETENESS_NOTES: dict[str, Any] = {
    "missing_fields": [],
    "notes": "",
    "ai_quota_exhausted": False,
    "classification_ai_suppressed": False,
    "work_profile_ai_suppressed": False,
    "unclassified_mapping_count": 0,
    "non_planning_ready_asset_count": 0,
    "excluded_booking_count": 0,
}


def normalize_programme_completeness_notes(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    def _normalize_missing_fields(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            for parser in (json.loads, ast.literal_eval):
                try:
                    parsed = parser(raw)
                except (ValueError, SyntaxError, json.JSONDecodeError, TypeError):
                    continue
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in raw.split(",") if item.strip()]
        return []

    def _normalize_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value) == 1
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes"}:
                return True
            if normalized in {"0", "false", "no", ""}:
                return False
            try:
                return int(normalized) == 1
            except ValueError:
                return False
        return False

    notes = dict(DEFAULT_PROGRAMME_COMPLETENESS_NOTES)
    if isinstance(existing, dict):
        notes.update(existing)

    notes["missing_fields"] = _normalize_missing_fields(notes.get("missing_fields"))
    notes["notes"] = "" if notes.get("notes") is None else str(notes.get("notes"))

    for key in (
        "ai_quota_exhausted",
        "classification_ai_suppressed",
        "work_profile_ai_suppressed",
    ):
        notes[key] = _normalize_bool(notes.get(key))

    for key in (
        "unclassified_mapping_count",
        "non_planning_ready_asset_count",
        "excluded_booking_count",
    ):
        try:
            notes[key] = int(notes.get(key) or 0)
        except (TypeError, ValueError):
            notes[key] = 0

    return notes
