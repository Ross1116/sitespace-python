from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services.system_health_service import record_upload_health_outcome
from app.services.work_profile_service import apportion_daily_hours


def test_apportion_daily_hours_uses_half_hour_largest_remainder():
    distribution = apportion_daily_hours([1.0, 1.0, 1.0], 10.0, max_hours_per_day=8.0)
    assert distribution == [3.5, 3.0, 3.5]
    assert sum(distribution) == 10.0


def test_system_health_transitions_from_degraded_to_recovery_to_healthy(monkeypatch):
    state = SimpleNamespace(
        key="primary",
        state="healthy",
        reason_codes=[],
        clean_upload_streak=0,
        last_transition_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        last_trigger_upload_id=None,
    )

    class FakeQuery:
        def __init__(self, row):
            self.row = row

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

        def scalar(self):
            return 0

        def all(self):
            return []

    class FakeDB:
        def __init__(self, row):
            self.row = row

        def get(self, model, key):
            assert key == "primary"
            return self.row

        def add(self, _row):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery(self.row)

    db = FakeDB(state)
    degraded_upload = SimpleNamespace(
        id=uuid4(),
        status="completed_with_warnings",
        completeness_notes={"work_profile_degraded_reasons": ["materialization_failed"]},
    )
    healthy_upload = SimpleNamespace(
        id=uuid4(),
        status="committed",
        completeness_notes={},
    )

    record_upload_health_outcome(db, degraded_upload)
    assert state.state == "degraded"
    assert state.reason_codes == ["completed_with_warnings", "materialization_failed"]

    record_upload_health_outcome(db, healthy_upload)
    assert state.state == "recovery"
    assert state.clean_upload_streak == 1

    record_upload_health_outcome(db, healthy_upload)
    assert state.state == "healthy"
