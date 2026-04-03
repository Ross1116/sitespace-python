from __future__ import annotations

import json
from pathlib import Path

from app.services.process_programme import _parse_file, _preflight_pdf


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "programmes"


def test_arc_bowden_pdf_fixture_survives_real_pdf_parser_path():
    pdf_bytes = (FIXTURE_DIR / "arc_bowden_v36_1_sanitized.pdf").read_bytes()
    expectations = json.loads((FIXTURE_DIR / "arc_bowden_v36_1_expectations.json").read_text())

    assert _preflight_pdf(pdf_bytes) is None

    rows, error = _parse_file(pdf_bytes, "arc_bowden_v36_1_sanitized.pdf")
    assert error is None
    assert len(rows) == expectations["expected_activity_count"]
    assert [row["ID"] for row in rows] == expectations["expected_ids"]
    assert {row["ID"] for row in rows if row["PctComplete"] == 100} == set(expectations["completed_activity_ids"])
    assert all("Revision 3" not in row["Name"] for row in rows)
    assert all("Page 1 of 1" not in row["Name"] for row in rows)
