"""
Unit tests for _apply_mapping in process_programme.

Verifies Stage 1 field population:
  - activity_kind derived from is_summary + dates
  - row_confidence derived from name completeness + dates
  - pct_complete extracted and clamped to 0-100
  - summary/milestone rows included in output (they are filtered
    from classification later, not from parsing)
"""

import pytest
from app.services.process_programme import _apply_mapping


_BASE_MAPPING = {
    "id": "ID",
    "name": "Name",
    "start_date": "Start",
    "end_date": "Finish",
    "is_summary": "IsSummary",
    "pct_complete": "PctComplete",
}


def _row(**kwargs) -> dict:
    return {
        "ID": kwargs.get("ID", "1001"),
        "Name": kwargs.get("Name", "Install formwork"),
        "Start": kwargs.get("Start", "2025-02-03"),
        "Finish": kwargs.get("Finish", "2025-02-14"),
        "IsSummary": kwargs.get("IsSummary", "0"),
        "PctComplete": kwargs.get("PctComplete", "50"),
    }


class TestActivityKind:

    def test_summary_row(self):
        rows = [_row(IsSummary="1")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].activity_kind == "summary"

    def test_milestone_zero_duration(self):
        rows = [_row(IsSummary="0", Start="2025-06-01", Finish="2025-06-01")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].activity_kind == "milestone"

    def test_task_normal(self):
        rows = [_row(IsSummary="0", Start="2025-02-03", Finish="2025-02-14")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].activity_kind == "task"

    def test_task_no_dates(self):
        rows = [_row(IsSummary="0", Start=None, Finish=None)]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].activity_kind == "task"


class TestRowConfidence:

    def test_high_name_and_both_dates(self):
        rows = [_row()]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].row_confidence == "high"

    def test_medium_missing_finish(self):
        rows = [_row(Finish=None)]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].row_confidence == "medium"

    def test_medium_missing_start(self):
        rows = [_row(Start=None)]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].row_confidence == "medium"

    def test_low_no_dates(self):
        rows = [_row(Start=None, Finish=None)]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].row_confidence == "low"


class TestPctComplete:

    def test_integer_value(self):
        rows = [_row(PctComplete="75")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete == 75

    def test_zero_value(self):
        rows = [_row(PctComplete="0")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete == 0

    def test_hundred_value(self):
        rows = [_row(PctComplete="100")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete == 100

    def test_float_string(self):
        rows = [_row(PctComplete="33.3")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete == 33

    def test_percent_sign_stripped(self):
        rows = [_row(PctComplete="50%")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete == 50

    def test_none_value(self):
        rows = [_row(PctComplete=None)]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete is None

    def test_invalid_string_gives_none(self):
        rows = [_row(PctComplete="N/A")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete is None

    def test_clamped_above_100(self):
        rows = [_row(PctComplete="150")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete == 100

    def test_clamped_below_zero(self):
        rows = [_row(PctComplete="-10")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert items[0].pct_complete == 0

    def test_no_pct_col_in_mapping(self):
        mapping = {k: v for k, v in _BASE_MAPPING.items() if k != "pct_complete"}
        rows = [_row()]
        items = _apply_mapping(rows, mapping, start_index=0)
        assert items[0].pct_complete is None


class TestEmptyNameFiltered:

    def test_empty_name_skipped(self):
        rows = [_row(Name=""), _row(Name="  "), _row(Name="Actual task")]
        items = _apply_mapping(rows, _BASE_MAPPING, start_index=0)
        assert len(items) == 1
        assert items[0].name == "Actual task"
