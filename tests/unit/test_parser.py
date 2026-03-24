"""
Unit tests for the file-parsing layer in process_programme.py.

Covers:
  - _parse_file: CSV (and unsupported format) parsing.
    XLSX/XLSM requires openpyxl binary fixtures; tested via a separate integration
    fixture when one is available.  PDF requires pdfplumber and a real PDF; also
    deferred to fixtures.
  - _parse_date: the programme-level date parser that handles the full range of
    date strings produced by P6, MS Project, Excel, and CSV exports.

The tests construct synthetic inputs entirely in code — no external fixture
files required.  This keeps the suite portable across machines and CI without
depending on any specific project's file.
"""

import csv
import io
import pytest
from datetime import date

from app.services.process_programme import _parse_file, _parse_date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv(headers: list[str], rows: list[dict]) -> bytes:
    """Build a UTF-8 encoded CSV from headers and a list of row dicts."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _make_csv_bom(headers: list[str], rows: list[dict]) -> bytes:
    """Same as _make_csv but with a UTF-8 BOM prepended (common in Excel exports)."""
    return b"\xef\xbb\xbf" + _make_csv(headers, rows)


# ---------------------------------------------------------------------------
# _parse_file — CSV
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_valid_csv_returns_rows(self):
        content = _make_csv(
            ["Name", "Start", "Finish"],
            [
                {"Name": "Pour ground floor slab", "Start": "17/03/25", "Finish": "18/03/25"},
                {"Name": "Install structural steel level 1", "Start": "19/03/25", "Finish": "22/03/25"},
            ],
        )
        rows, error = _parse_file(content, "programme.csv")
        assert error is None
        assert len(rows) == 2
        assert rows[0]["Name"] == "Pour ground floor slab"
        assert rows[1]["Name"] == "Install structural steel level 1"

    def test_csv_headers_preserved(self):
        content = _make_csv(
            ["Activity ID", "Activity Name", "Start Date", "Finish Date"],
            [{"Activity ID": "1000", "Activity Name": "Site clearance", "Start Date": "01/03/25", "Finish Date": "05/03/25"}],
        )
        rows, error = _parse_file(content, "schedule.csv")
        assert error is None
        assert "Activity ID" in rows[0]
        assert "Activity Name" in rows[0]

    def test_csv_with_bom_parsed_correctly(self):
        # UTF-8 BOM (\xef\xbb\xbf) is produced by Excel "Save as CSV".
        # It must not appear in the first header key.
        content = _make_csv_bom(
            ["Name", "Start", "Finish"],
            [{"Name": "Bulk excavation", "Start": "01/03/25", "Finish": "10/03/25"}],
        )
        rows, error = _parse_file(content, "export.csv")
        assert error is None
        assert len(rows) == 1
        # Header must not start with BOM character
        first_key = list(rows[0].keys())[0]
        assert first_key == "Name", f"First key was {first_key!r} — BOM not stripped"

    def test_empty_csv_returns_empty_list_no_error(self):
        rows, error = _parse_file(b"", "empty.csv")
        assert error is None
        assert rows == []

    def test_header_only_csv_returns_empty_list(self):
        content = _make_csv(["Name", "Start", "Finish"], [])
        rows, error = _parse_file(content, "headers_only.csv")
        assert error is None
        assert rows == []

    def test_single_row_csv(self):
        content = _make_csv(
            ["Name", "Start", "Finish"],
            [{"Name": "Concrete pour level 5", "Start": "2026-04-01", "Finish": "2026-04-01"}],
        )
        rows, error = _parse_file(content, "single.csv")
        assert error is None
        assert len(rows) == 1

    def test_many_rows_all_parsed(self):
        data = [{"Name": f"Activity {i}", "Start": "01/03/25", "Finish": "02/03/25"} for i in range(500)]
        content = _make_csv(["Name", "Start", "Finish"], data)
        rows, error = _parse_file(content, "large.csv")
        assert error is None
        assert len(rows) == 500

    def test_csv_with_extra_columns(self):
        content = _make_csv(
            ["ID", "Name", "Start", "Finish", "Resource", "Notes"],
            [{"ID": "100", "Name": "Erect formwork", "Start": "01/04/26", "Finish": "03/04/26", "Resource": "Gang A", "Notes": ""}],
        )
        rows, error = _parse_file(content, "extended.csv")
        assert error is None
        assert rows[0]["Resource"] == "Gang A"


# ---------------------------------------------------------------------------
# _parse_file — unsupported formats
# ---------------------------------------------------------------------------

class TestParseUnsupportedFormat:
    def test_txt_file_returns_error(self):
        rows, error = _parse_file(b"some text content", "schedule.txt")
        assert rows == []
        assert error is not None
        assert "Unsupported" in error

    def test_xml_file_returns_error(self):
        rows, error = _parse_file(b"<root/>", "schedule.xml")
        assert rows == []
        assert error is not None

    def test_no_extension_returns_error(self):
        rows, error = _parse_file(b"data", "schedule")
        assert rows == []
        assert error is not None

    def test_error_contains_filename(self):
        rows, error = _parse_file(b"data", "mystery_file.xyz")
        assert error is not None
        assert "mystery_file.xyz" in error


# ---------------------------------------------------------------------------
# _parse_date — the programme-level date parser
# ---------------------------------------------------------------------------

class TestParseDate:
    # ISO formats
    def test_iso_date(self):
        assert _parse_date("2026-03-17") == date(2026, 3, 17)

    def test_iso_datetime(self):
        assert _parse_date("2026-03-17T08:00:00") == date(2026, 3, 17)

    def test_iso_datetime_with_z(self):
        assert _parse_date("2026-03-17T00:00:00Z") == date(2026, 3, 17)

    def test_iso_datetime_with_space_separator(self):
        assert _parse_date("2026-03-17 08:00:00") == date(2026, 3, 17)

    # d/m/y formats (common in Australian Excel / P6 exports)
    def test_dd_mm_yyyy(self):
        assert _parse_date("17/03/2026") == date(2026, 3, 17)

    def test_dd_mm_yy(self):
        assert _parse_date("17/03/26") == date(2026, 3, 17)

    def test_single_digit_day_and_month(self):
        assert _parse_date("1/3/2026") == date(2026, 3, 1)

    # P6 PDF export format
    def test_p6_dd_mon_yyyy(self):
        assert _parse_date("17-Mar-2026") == date(2026, 3, 17)

    def test_p6_dd_mon_yy(self):
        assert _parse_date("17-Mar-26") == date(2026, 3, 17)

    def test_p6_dd_mon_yyyy_different_month(self):
        assert _parse_date("05-Jan-2027") == date(2027, 1, 5)

    # dd Mon yyyy format
    def test_dd_mon_yyyy_space(self):
        assert _parse_date("17 Mar 2026") == date(2026, 3, 17)

    # Edge cases
    def test_none_returns_none(self):
        assert _parse_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_date("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_date("   ") is None

    def test_garbage_string_returns_none(self):
        assert _parse_date("not-a-date") is None

    def test_partial_date_returns_none(self):
        assert _parse_date("17/03") is None
