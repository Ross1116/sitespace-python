from types import SimpleNamespace
from uuid import uuid4

from unittest.mock import MagicMock

from app.services import programme_upload_service


class _FakeColumn:
    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other):
        return ("eq", self.name, other)

    def __lt__(self, other):
        return ("lt", self.name, other)

    def in_(self, values):
        return ("in", self.name, tuple(values))

    def desc(self):
        return ("desc", self.name)


class _FakeProgrammeUpload:
    project_id = _FakeColumn("project_id")
    status = _FakeColumn("status")
    version_number = _FakeColumn("version_number")


class _FakeQuery:
    def __init__(self, first_result):
        self._first_result = first_result
        self.filters = []
        self.orderings = []

    def filter(self, *conditions):
        self.filters.extend(conditions)
        return self

    def order_by(self, *orderings):
        self.orderings.extend(orderings)
        return self

    def first(self):
        return self._first_result(self.filters, self.orderings)


def test_upload_status_helpers_normalize_legacy_degraded_state():
    upload = SimpleNamespace(status="degraded")

    assert programme_upload_service.normalize_upload_status("degraded") == "completed_with_warnings"
    assert programme_upload_service.get_upload_status(upload) == "completed_with_warnings"
    assert programme_upload_service.is_upload_successful_for_planning(upload) is True
    assert programme_upload_service.is_upload_readable(upload) is True
    assert programme_upload_service.is_upload_terminal_success(upload) is True
    assert programme_upload_service.upload_has_warnings(upload) is True
    assert programme_upload_service.is_upload_processing(upload) is False
    assert programme_upload_service.is_upload_failed(upload) is False


def test_get_active_programme_upload_uses_latest_planning_successful_statuses(monkeypatch):
    project_id = uuid4()
    expected_upload = SimpleNamespace(id=uuid4())

    def _first_result(filters, orderings):
        assert ("eq", "project_id", project_id) in filters
        assert (
            "in",
            "status",
            (
                "committed",
                "completed_with_warnings",
                "degraded",
            ),
        ) in filters
        assert ("desc", "version_number") in orderings
        return expected_upload

    db = MagicMock()
    db.query.return_value = _FakeQuery(_first_result)
    monkeypatch.setattr(programme_upload_service, "ProgrammeUpload", _FakeProgrammeUpload)

    assert programme_upload_service.get_active_programme_upload(project_id, db) is expected_upload


def test_get_previous_successful_programme_upload_skips_newer_unsuccessful_versions(monkeypatch):
    project_id = uuid4()
    expected_upload = SimpleNamespace(id=uuid4())

    def _first_result(filters, orderings):
        assert ("eq", "project_id", project_id) in filters
        assert ("lt", "version_number", 7) in filters
        assert (
            "in",
            "status",
            (
                "committed",
                "completed_with_warnings",
                "degraded",
            ),
        ) in filters
        assert ("desc", "version_number") in orderings
        return expected_upload

    db = MagicMock()
    db.query.return_value = _FakeQuery(_first_result)
    monkeypatch.setattr(programme_upload_service, "ProgrammeUpload", _FakeProgrammeUpload)

    assert programme_upload_service.get_previous_successful_programme_upload(project_id, 7, db) is expected_upload
