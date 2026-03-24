"""
Unit tests for the PDF activity parser in process_programme._parse_file.

Tests:
  - pct_complete captured from the % complete field
  - noise lines (headers, footers, legends) are suppressed
  - duplicate IDs are deduplicated (first occurrence wins)
  - valid activity rows are parsed with correct fields
"""

import pytest
from app.services.process_programme import _parse_file


def _make_pdf_bytes(lines: list[str]) -> bytes:
    """
    Build a minimal fake PDF that pdfplumber will read.
    We mock pdfplumber so this helper just returns dummy bytes;
    actual parsing is tested via the regex logic by monkey-patching.
    """
    # Return a sentinel — tests that need real parsing will mock pdfplumber.
    return b"%PDF-1.4 fake"


# ---------------------------------------------------------------------------
# Regex / noise suppression tests via direct regex validation
# ---------------------------------------------------------------------------
# Rather than spinning up a real PDF (which needs pdfplumber + a binary fixture),
# we test the parsing logic by injecting a mock pdfplumber page.

class FakePage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self):
        return self._text


class FakePDF:
    def __init__(self, pages_text: list[str]):
        self.pages = [FakePage(t) for t in pages_text]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _parse_pdf_lines(lines: list[str], monkeypatch, *, assert_no_error: bool = True) -> list[dict]:
    """Helper: run _parse_file with mocked pdfplumber returning the given lines.

    assert_no_error=True (default): asserts err is None so parsing failures
    surface immediately. Pass False when testing intentionally empty inputs
    (e.g. noise-only lines) where an "no activities found" error is expected.
    """
    import sys

    fake_pdf = FakePDF(["\n".join(lines)])
    fake_module = type(sys)("pdfplumber")
    fake_module.open = lambda *a, **kw: fake_pdf
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_module)

    rows, err = _parse_file(b"fake", "programme.pdf")
    if assert_no_error:
        assert not err, f"_parse_file returned unexpected error: {err}"
    return rows


VALID_LINE = "1001 Install formwork Level 3 75% 10 daysMon 03/02/25 Fri 14/02/25 Install formwork"
VALID_LINE_2 = "1002 Pour concrete slab 0% 1 dayThu 20/02/25 Thu 20/02/25 Pour concrete slab"
VALID_LINE_3 = "1003 Strip formwork 100% 5 daysMon 24/02/25 Fri 28/02/25 Strip formwork"


class TestPDFParser:

    def test_valid_row_parsed(self, monkeypatch):
        rows = _parse_pdf_lines([VALID_LINE], monkeypatch)
        assert len(rows) == 1
        r = rows[0]
        assert r["ID"] == "1001"
        assert "formwork" in r["Name"].lower()
        assert r["PctComplete"] == 75
        assert r["Start"] == "03/02/25"
        assert r["Finish"] == "14/02/25"

    def test_zero_pct_captured(self, monkeypatch):
        rows = _parse_pdf_lines([VALID_LINE_2], monkeypatch)
        assert rows[0]["PctComplete"] == 0

    def test_hundred_pct_captured(self, monkeypatch):
        rows = _parse_pdf_lines([VALID_LINE_3], monkeypatch)
        assert rows[0]["PctComplete"] == 100

    def test_duplicate_id_deduplicated(self, monkeypatch):
        rows = _parse_pdf_lines([VALID_LINE, VALID_LINE], monkeypatch)
        assert len(rows) == 1  # second occurrence of ID 1001 dropped

    def test_multiple_valid_rows(self, monkeypatch):
        rows = _parse_pdf_lines([VALID_LINE, VALID_LINE_2, VALID_LINE_3], monkeypatch)
        assert len(rows) == 3
        assert [r["ID"] for r in rows] == ["1001", "1002", "1003"]

    @pytest.mark.parametrize("noise_line", [
        # Column headers
        "Activity ID Name % Complete Duration Start Finish",
        "ID Activity Name % Complete Duration Start Finish",
        # Footers
        "Revision 3 Data Date: 01/01/25",
        "Print Date: 15/02/25",
        "Printed: 15/02/25",
        "Page 1 of 12",
        # Legend entries
        "Critical",
        "Near Critical",
        "Total Float",
        # Empty line (implicit — whitespace-only)
        "   ",
    ])
    def test_noise_suppressed(self, noise_line, monkeypatch):
        rows = _parse_pdf_lines([noise_line], monkeypatch, assert_no_error=False)
        assert rows == [], f"Expected noise line to be suppressed: {noise_line!r}"

    def test_noise_does_not_block_valid_row(self, monkeypatch):
        """Noise lines interspersed between valid rows should not prevent parsing."""
        lines = [
            "Activity ID Name % Complete Duration Start Finish",
            VALID_LINE,
            "Revision 3",
            VALID_LINE_2,
            "Page 1 of 12",
            VALID_LINE_3,
        ]
        rows = _parse_pdf_lines(lines, monkeypatch)
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# _pdf_structure uses PctComplete column
# ---------------------------------------------------------------------------

class TestPdfStructure:

    def test_pct_complete_in_column_mapping(self):
        from app.services.process_programme import _pdf_structure
        rows = [
            {"ID": "1001", "Name": "Install formwork", "PctComplete": 50, "Start": "03/02/25", "Finish": "07/02/25"},
        ]
        result = _pdf_structure(rows)
        assert result.column_mapping.get("pct_complete") == "PctComplete"
        assert len(result.activities) == 1
        assert result.activities[0].pct_complete == 50
