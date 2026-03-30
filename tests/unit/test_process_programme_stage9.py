from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.process_programme import _commit_failed, _commit_with_warnings


def test_commit_with_warnings_marks_upload_as_readable_success():
    upload = SimpleNamespace(
        completeness_notes=None,
        completeness_score=None,
        status="processing",
        processing_outcome="processing",
    )
    db = MagicMock()

    _commit_with_warnings(upload, db, completeness_score=0.65, notes=["work_profile_ai_suppressed"])

    assert upload.completeness_score == 0.65
    assert upload.status == "completed_with_warnings"
    assert upload.processing_outcome == "completed_with_warnings"
    assert "work_profile_ai_suppressed" in upload.completeness_notes["missing_fields"]
    db.commit.assert_called_once()


def test_commit_failed_marks_upload_as_terminal_failure():
    upload = SimpleNamespace(
        completeness_notes=None,
        completeness_score=None,
        status="processing",
        processing_outcome="processing",
    )
    db = MagicMock()

    _commit_failed(
        upload,
        db,
        notes=["parse_error"],
        reason="The uploaded file could not be parsed into a usable programme.",
    )

    assert upload.completeness_score == 0.0
    assert upload.status == "failed"
    assert upload.processing_outcome == "failed"
    assert "parse_error" in upload.completeness_notes["missing_fields"]
    assert upload.completeness_notes["notes"] == "The uploaded file could not be parsed into a usable programme."
    db.commit.assert_called_once()
