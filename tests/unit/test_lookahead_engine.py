from datetime import date
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.constants import DEFAULT_MAX_HOURS_PER_DAY
from app.services import lookahead_engine


class _RowsQuery:
    def __init__(self, rows):
        self._rows = rows

    def join(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _SessionContext:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc, tb):
        return False


def test_get_weekly_activity_candidates_reuses_batched_max_hours(monkeypatch):
    project_id = uuid4()
    upload = SimpleNamespace(id=uuid4())
    mapping_one = SimpleNamespace(asset_type="forklift")
    mapping_two = SimpleNamespace(asset_type="forklift")
    activity_one = SimpleNamespace(
        id=uuid4(),
        name="Activity One",
        start_date=date(2026, 4, 6),
        end_date=date(2026, 4, 7),
        level_name="L1",
        zone_name="Zone A",
        row_confidence="medium",
        sort_order=1,
    )
    activity_two = SimpleNamespace(
        id=uuid4(),
        name="Activity Two",
        start_date=date(2026, 4, 8),
        end_date=date(2026, 4, 9),
        level_name="L2",
        zone_name="Zone B",
        row_confidence="medium",
        sort_order=2,
    )
    rows = [
        (mapping_one, activity_one, upload, None),
        (mapping_two, activity_two, upload, None),
    ]

    query_results = iter(
        [
            _RowsQuery(rows),
            _RowsQuery([]),
            _RowsQuery([(activity_one.id, 0), (activity_two.id, 0)]),
        ]
    )

    db = SimpleNamespace(query=lambda *args, **kwargs: next(query_results))

    monkeypatch.setattr(lookahead_engine, "_get_latest_processed_upload", lambda project_id, db: upload)
    monkeypatch.setattr(lookahead_engine, "build_eligible_activity_mapping_filters", lambda: ())

    loaded_asset_types: list[set[str]] = []

    def _load_max_hours(db, asset_types):
        loaded_asset_types.append(set(asset_types))
        return {"forklift": 8.0}

    resolver_maps: list[dict[str, float] | None] = []

    def _resolve_distribution(db, *, mapping, activity, upload, profile, max_hours_by_type=None):
        resolver_maps.append(max_hours_by_type)
        return {
            "work_dates": [activity.start_date],
            "distribution": [8.0],
            "low_confidence": False,
            "missing_profile": profile is None,
            "per_day_cap_repaired": False,
        }

    monkeypatch.setattr(lookahead_engine, "_load_max_hours_by_type", _load_max_hours)
    monkeypatch.setattr(lookahead_engine, "_resolve_activity_distribution", _resolve_distribution)

    candidates = lookahead_engine.get_weekly_activity_candidates(
        project_id=project_id,
        week_start=date(2026, 4, 6),
        asset_type="forklift",
        db=db,
    )

    assert len(candidates) == 2
    assert loaded_asset_types == [{"forklift"}]
    assert resolver_maps == [{"forklift": 8.0}, {"forklift": 8.0}]


def test_load_max_hours_by_type_fills_defaults_for_missing_codes(monkeypatch):
    lookup_query = MagicMock()
    lookup_query.filter.return_value = lookup_query
    lookup_query.all.return_value = [("forklift", 8.0)]

    lookup_db = MagicMock()
    lookup_db.query.return_value = lookup_query

    caller_db = MagicMock()
    monkeypatch.setattr(lookahead_engine, "SessionLocal", _SessionContext(lookup_db))

    result = lookahead_engine._load_max_hours_by_type(caller_db, {"forklift", "crane"})

    assert result["forklift"] == 8.0
    assert result["crane"] == DEFAULT_MAX_HOURS_PER_DAY["crane"]
    caller_db.rollback.assert_not_called()


def test_load_max_hours_by_type_does_not_rollback_caller_session_on_failure(monkeypatch):
    lookup_query = MagicMock()
    lookup_query.filter.return_value = lookup_query
    lookup_query.all.side_effect = SQLAlchemyError("boom")

    lookup_db = MagicMock()
    lookup_db.query.return_value = lookup_query

    caller_db = MagicMock()
    monkeypatch.setattr(lookahead_engine, "SessionLocal", _SessionContext(lookup_db))

    result = lookahead_engine._load_max_hours_by_type(caller_db, {"forklift"})

    assert result == {"forklift": DEFAULT_MAX_HOURS_PER_DAY["forklift"]}
    caller_db.rollback.assert_not_called()


def test_load_max_hours_by_type_propagates_non_sqlalchemy_failures(monkeypatch):
    lookup_query = MagicMock()
    lookup_query.filter.return_value = lookup_query
    lookup_query.all.side_effect = RuntimeError("boom")

    lookup_db = MagicMock()
    lookup_db.query.return_value = lookup_query

    monkeypatch.setattr(lookahead_engine, "SessionLocal", _SessionContext(lookup_db))

    with pytest.raises(RuntimeError, match="boom"):
        lookahead_engine._load_max_hours_by_type(MagicMock(), {"forklift"})


def test_sync_thresholded_notifications_stamps_lineage_and_cancels_stale_rows(monkeypatch):
    project_id = uuid4()
    latest_upload_id = uuid4()
    snapshot_id = uuid4()
    week_start = date(2026, 4, 6)
    surviving_sub_id = uuid4()
    new_sub_id = uuid4()

    assignments = [
        SimpleNamespace(asset_type="forklift", subcontractor_id=surviving_sub_id, is_active=True),
        SimpleNamespace(asset_type="forklift", subcontractor_id=new_sub_id, is_active=True),
    ]
    surviving_existing = SimpleNamespace(
        project_id=project_id,
        sub_id=surviving_sub_id,
        asset_type="forklift",
        week_start=week_start,
        status="pending",
        severity_score=0.1,
        programme_upload_id=uuid4(),
        snapshot_id=uuid4(),
    )
    stale_existing = SimpleNamespace(
        project_id=project_id,
        sub_id=uuid4(),
        asset_type="crane",
        week_start=week_start,
        status="sent",
        severity_score=0.2,
        programme_upload_id=uuid4(),
        snapshot_id=uuid4(),
    )
    legacy_existing = SimpleNamespace(
        project_id=project_id,
        sub_id=uuid4(),
        asset_type="excavator",
        week_start=week_start,
        status="pending",
        severity_score=0.3,
        programme_upload_id=None,
        snapshot_id=None,
    )
    acted_existing = SimpleNamespace(
        project_id=project_id,
        sub_id=uuid4(),
        asset_type="grader",
        week_start=week_start,
        status="acted",
        severity_score=0.4,
        programme_upload_id=uuid4(),
        snapshot_id=uuid4(),
    )

    query_results = iter(
        [
            _RowsQuery(assignments),
            _RowsQuery([surviving_existing, stale_existing, legacy_existing, acted_existing]),
        ]
    )
    added_notifications = []
    db = SimpleNamespace(query=lambda *args, **kwargs: next(query_results), add=added_notifications.append)

    monkeypatch.setattr(
        lookahead_engine,
        "ensure_project_alert_policy",
        lambda db, project_id: SimpleNamespace(
            mode="notify",
            external_enabled=True,
            max_alerts_per_subcontractor_per_week=5,
            max_alerts_per_project_per_week=5,
            min_demand_hours=1,
            min_gap_hours=1,
            min_gap_ratio=0.1,
            min_lead_weeks=0,
        ),
    )
    breadcrumb_mock = MagicMock()
    monkeypatch.setattr(lookahead_engine.sentry_sdk, "add_breadcrumb", breadcrumb_mock)

    lookahead_engine._sync_thresholded_notifications(
        db,
        project_id=project_id,
        latest_upload_id=latest_upload_id,
        snapshot_id=snapshot_id,
        snapshot_date=week_start,
        row_payloads=[
            {
                "week_start": week_start,
                "asset_type": "forklift",
                "demand_hours": 12.0,
                "gap_hours": 4.0,
                "anomaly_flags_json": {},
                "is_anomalous": False,
            }
        ],
        suppress_external=False,
    )

    assert surviving_existing.programme_upload_id == latest_upload_id
    assert surviving_existing.snapshot_id == snapshot_id
    assert surviving_existing.status == "pending"
    assert stale_existing.status == "cancelled"
    assert legacy_existing.status == "cancelled"
    assert acted_existing.status == "acted"

    assert len(added_notifications) == 1
    added_notification = added_notifications[0]
    assert added_notification.sub_id == new_sub_id
    assert added_notification.project_id == project_id
    assert added_notification.programme_upload_id == latest_upload_id
    assert added_notification.snapshot_id == snapshot_id
    assert added_notification.status == "pending"
    breadcrumb_mock.assert_called_once()
