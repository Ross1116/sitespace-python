"""
Unit tests for _detect_structure_fallback().

This is the regex-heuristic path used when AI is unavailable.  It must always
produce a usable StructureResult — even from malformed or minimal input.

Completeness score formula (fallback):
  score = int((rows_with_dates / total_rows) * 80)  capped at 80

Notes column always starts with "Regex fallback — AI unavailable."
"""

import pytest
from app.services.ai_service import _detect_structure_fallback


def _rows(*entries) -> list[dict]:
    """Helper: build a list of row dicts from (name, start, finish) tuples."""
    return [{"Name": e[0], "Start": e[1], "Finish": e[2]} for e in entries]


class TestEmptyInput:
    def test_empty_list_returns_zero_score(self):
        result = _detect_structure_fallback([])
        assert result.completeness_score == 0

    def test_empty_list_reports_missing_fields(self):
        result = _detect_structure_fallback([])
        assert "name" in result.missing_fields
        assert "start_date" in result.missing_fields
        assert "end_date" in result.missing_fields

    def test_empty_list_returns_no_activities(self):
        result = _detect_structure_fallback([])
        assert result.activities == []


class TestColumnDetection:
    def test_detects_name_column(self):
        rows = _rows(("Install precast wall panels", "17/03/25", "20/03/25"))
        result = _detect_structure_fallback(rows)
        assert "name" in result.column_mapping
        assert result.column_mapping["name"] == "Name"

    def test_detects_start_date_column(self):
        rows = _rows(("Install precast wall panels", "17/03/25", "20/03/25"))
        result = _detect_structure_fallback(rows)
        assert "start_date" in result.column_mapping

    def test_detects_end_date_column(self):
        rows = _rows(("Install precast wall panels", "17/03/25", "20/03/25"))
        result = _detect_structure_fallback(rows)
        assert "end_date" in result.column_mapping

    def test_missing_dates_reports_missing_fields(self):
        rows = [{"Name": "Survey work", "Notes": "na"}]
        result = _detect_structure_fallback(rows)
        assert "start_date" in result.missing_fields
        assert "end_date" in result.missing_fields


class TestDateFormats:
    def test_dd_mm_yy_format(self):
        rows = _rows(("Pour slab", "17/03/25", "20/03/25"))
        result = _detect_structure_fallback(rows)
        assert len(result.activities) == 1
        assert result.activities[0].start == "2025-03-17"
        assert result.activities[0].finish == "2025-03-20"

    def test_dd_mm_yyyy_format(self):
        rows = _rows(("Pour slab", "17/03/2025", "20/03/2025"))
        result = _detect_structure_fallback(rows)
        assert len(result.activities) == 1
        assert result.activities[0].start == "2025-03-17"

    def test_yyyy_mm_dd_format(self):
        rows = _rows(("Pour slab", "2025-03-17", "2025-03-20"))
        result = _detect_structure_fallback(rows)
        assert len(result.activities) == 1
        assert result.activities[0].start == "2025-03-17"

    def test_p6_dd_mon_yy_format(self):
        # Primavera P6 PDF export format
        rows = _rows(("Lift column cages", "17-Mar-25", "19-Mar-25"))
        result = _detect_structure_fallback(rows)
        assert len(result.activities) == 1
        assert result.activities[0].start == "2025-03-17"

    def test_p6_dd_mon_yyyy_format(self):
        rows = _rows(("Lift column cages", "17-Mar-2025", "19-Mar-2025"))
        result = _detect_structure_fallback(rows)
        assert len(result.activities) == 1
        assert result.activities[0].start == "2025-03-17"


class TestCompletenessScore:
    def test_all_rows_dated_score_approaches_80(self):
        rows = _rows(
            ("Activity A", "17/03/25", "20/03/25"),
            ("Activity B", "21/03/25", "25/03/25"),
        )
        result = _detect_structure_fallback(rows)
        assert result.completeness_score == 80  # 2/2 * 80 = 80

    def test_half_rows_dated_score_is_40(self):
        rows = [
            {"Name": "Activity A", "Start": "17/03/25", "Finish": "20/03/25"},
            {"Name": "Activity B", "Start": "", "Finish": ""},
        ]
        result = _detect_structure_fallback(rows)
        assert result.completeness_score == 40  # 1/2 * 80 = 40

    def test_no_dates_score_is_zero(self):
        rows = [{"Name": "Activity A", "Start": "", "Finish": ""}]
        result = _detect_structure_fallback(rows)
        assert result.completeness_score == 0

    def test_score_capped_at_80(self):
        # Score formula caps at 80 for the fallback path — AI gets 0–100
        rows = _rows(*[
            (f"Activity {i}", "17/03/25", "20/03/25") for i in range(100)
        ])
        result = _detect_structure_fallback(rows)
        assert result.completeness_score <= 80


class TestActivityExtraction:
    def test_rows_without_names_are_skipped(self):
        rows = [
            {"Name": "", "Start": "17/03/25", "Finish": "20/03/25"},
            {"Name": "Real activity", "Start": "17/03/25", "Finish": "20/03/25"},
        ]
        result = _detect_structure_fallback(rows)
        assert len(result.activities) == 1
        assert result.activities[0].name == "Real activity"

    def test_activity_names_preserved(self):
        rows = _rows(
            ("Install mechanical services level 4", "17/03/25", "20/03/25"),
            ("Structural steel erection zone B", "21/03/25", "21/03/25"),
        )
        result = _detect_structure_fallback(rows)
        names = [a.name for a in result.activities]
        assert "Install mechanical services level 4" in names
        assert "Structural steel erection zone B" in names

    def test_fallback_produces_flat_hierarchy(self):
        """Fallback cannot infer hierarchy — all activities have parent_id=None."""
        rows = _rows(
            ("STRUCTURE", "17/03/25", "20/06/25"),
            ("Install columns level 3", "17/03/25", "17/03/25"),
        )
        result = _detect_structure_fallback(rows)
        for activity in result.activities:
            assert activity.parent_id is None


class TestNotes:
    def test_notes_always_contain_regex_fallback_marker(self):
        rows = _rows(("Pour ground floor slab", "17/03/25", "20/03/25"))
        result = _detect_structure_fallback(rows)
        assert "Regex fallback" in result.notes

    def test_missing_columns_reported_in_notes(self):
        rows = [{"ActivityName": "Install precast", "Week": "1"}]
        result = _detect_structure_fallback(rows)
        assert "Missing" in result.notes or result.missing_fields
