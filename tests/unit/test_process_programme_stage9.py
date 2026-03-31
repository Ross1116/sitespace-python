from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

from app.services import process_programme as process_programme_service
from app.services.process_programme import (
    _commit_failed,
    _commit_with_warnings,
    recover_stale_processing_uploads,
)


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
    assert upload.completeness_notes["notes"] == "Completed with warnings."
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


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *predicates, **kwargs):
        for predicate in predicates:
            if callable(predicate):
                self._rows = [row for row in self._rows if predicate(row)]
        return self

    def all(self):
        return list(self._rows)


class _FakeField:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return lambda row: getattr(row, self.name) == other

    def __le__(self, other):
        return lambda row: getattr(row, self.name) is not None and getattr(row, self.name) <= other

    def is_(self, other):
        return lambda row: getattr(row, self.name) is other


class _FakeProgrammeUploadModel:
    status = _FakeField("status")
    created_at = _FakeField("created_at")
    project_id = _FakeField("project_id")


def _fake_or(*predicates):
    return lambda row: any(predicate(row) for predicate in predicates)


def test_recover_stale_processing_uploads_marks_rows_failed(monkeypatch):
    now = datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc)
    target_project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    uploads = [
        SimpleNamespace(
            created_at=now - timedelta(hours=2),
            project_id=target_project_id,
            completeness_notes=None,
            completeness_score=None,
            status="processing",
            processing_outcome="processing",
        ),
        SimpleNamespace(
            created_at=now - timedelta(minutes=5),
            project_id=target_project_id,
            completeness_notes={"missing_fields": ["parse_error"], "notes": ""},
            completeness_score=0.4,
            status="processing",
            processing_outcome="processing",
        ),
        SimpleNamespace(
            created_at=now - timedelta(hours=2),
            project_id=other_project_id,
            completeness_notes=None,
            completeness_score=0.8,
            status="processing",
            processing_outcome="processing",
        ),
    ]
    monkeypatch.setattr(process_programme_service, "ProgrammeUpload", _FakeProgrammeUploadModel)
    monkeypatch.setattr(process_programme_service, "or_", _fake_or)
    db = MagicMock()
    db.query.return_value = _FakeQuery(uploads)

    recovered = recover_stale_processing_uploads(
        db,
        stale_after=timedelta(minutes=30),
        project_id=target_project_id,
        now=now,
    )

    assert recovered == 1
    assert uploads[0].status == "failed"
    assert uploads[0].processing_outcome == "failed"
    assert "processing_interrupted" in uploads[0].completeness_notes["missing_fields"]
    assert uploads[1].status == "processing"
    assert uploads[1].processing_outcome == "processing"
    assert uploads[2].status == "processing"
    assert uploads[2].processing_outcome == "processing"
    db.commit.assert_called_once()
