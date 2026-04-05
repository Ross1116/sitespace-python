from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from unittest.mock import MagicMock

from app.api.v1 import lookahead
from app.services import lookahead_engine


def test_get_fresh_snapshot_uses_degraded_upload_when_no_committed_exists(monkeypatch):
    project_id = uuid4()
    degraded_upload = SimpleNamespace(id=uuid4())
    db = MagicMock()
    monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda project_id, db: degraded_upload)
    monkeypatch.setattr(lookahead_engine, "get_latest_snapshot", lambda project_id, db: None)
    monkeypatch.setattr(lookahead_engine, "get_latest_booking_update_for_project", lambda project_id, db: None)

    expected_snapshot = SimpleNamespace(programme_upload_id=degraded_upload.id)
    calc_mock = MagicMock(return_value=expected_snapshot)
    monkeypatch.setattr(lookahead_engine, "calculate_lookahead_for_project", calc_mock)

    snapshot = lookahead_engine.get_fresh_snapshot(project_id, db)

    assert snapshot is expected_snapshot
    calc_mock.assert_called_once_with(project_id, db)


def test_get_fresh_snapshot_recalculates_when_booking_is_newer(monkeypatch):
    project_id = uuid4()
    degraded_upload = SimpleNamespace(id=uuid4())
    stale_snapshot = SimpleNamespace(
        programme_upload_id=degraded_upload.id,
        created_at=lookahead_engine.datetime(2026, 3, 27, 8, 0, tzinfo=lookahead_engine.timezone.utc),
        data={"generated_at": "2026-03-27T08:00:00+00:00"},
    )
    db = MagicMock()
    monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda project_id, db: degraded_upload)
    monkeypatch.setattr(lookahead_engine, "get_latest_snapshot", lambda project_id, db: stale_snapshot)
    monkeypatch.setattr(
        lookahead_engine,
        "get_latest_booking_update_for_project",
        lambda project_id, db: lookahead_engine.datetime(2026, 3, 27, 9, 0, tzinfo=lookahead_engine.timezone.utc),
    )

    refreshed_snapshot = SimpleNamespace(programme_upload_id=degraded_upload.id)
    calc_mock = MagicMock(return_value=refreshed_snapshot)
    monkeypatch.setattr(lookahead_engine, "calculate_lookahead_for_project", calc_mock)

    snapshot = lookahead_engine.get_fresh_snapshot(project_id, db)

    assert snapshot is refreshed_snapshot
    calc_mock.assert_called_once_with(project_id, db)


def test_get_fresh_snapshot_uses_generated_at_to_avoid_repeated_recalculation(monkeypatch):
    project_id = uuid4()
    degraded_upload = SimpleNamespace(id=uuid4())
    refreshed_snapshot = SimpleNamespace(
        programme_upload_id=degraded_upload.id,
        created_at=lookahead_engine.datetime(2026, 3, 27, 8, 0, tzinfo=lookahead_engine.timezone.utc),
        data={"generated_at": "2026-03-27T10:00:00+00:00"},
    )
    db = MagicMock()
    monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda project_id, db: degraded_upload)
    monkeypatch.setattr(lookahead_engine, "get_latest_snapshot", lambda project_id, db: refreshed_snapshot)
    monkeypatch.setattr(
        lookahead_engine,
        "get_latest_booking_update_for_project",
        lambda project_id, db: lookahead_engine.datetime(2026, 3, 27, 9, 0, tzinfo=lookahead_engine.timezone.utc),
    )

    calc_mock = MagicMock()
    monkeypatch.setattr(lookahead_engine, "calculate_lookahead_for_project", calc_mock)

    snapshot = lookahead_engine.get_fresh_snapshot(project_id, db)

    assert snapshot is refreshed_snapshot
    calc_mock.assert_not_called()


def test_get_fresh_snapshot_normalizes_naive_booking_timestamp(monkeypatch):
    project_id = uuid4()
    degraded_upload = SimpleNamespace(id=uuid4())
    stale_snapshot = SimpleNamespace(
        programme_upload_id=degraded_upload.id,
        created_at=lookahead_engine.datetime(2026, 3, 27, 8, 0, tzinfo=lookahead_engine.timezone.utc),
        data={"generated_at": "2026-03-27T08:00:00+00:00"},
    )
    db = MagicMock()
    monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda project_id, db: degraded_upload)
    monkeypatch.setattr(lookahead_engine, "get_latest_snapshot", lambda project_id, db: stale_snapshot)
    monkeypatch.setattr(
        lookahead_engine,
        "get_latest_booking_update_for_project",
        lambda project_id, db: lookahead_engine.datetime(2026, 3, 27, 9, 0),
    )

    refreshed_snapshot = SimpleNamespace(programme_upload_id=degraded_upload.id)
    calc_mock = MagicMock(return_value=refreshed_snapshot)
    monkeypatch.setattr(lookahead_engine, "calculate_lookahead_for_project", calc_mock)

    snapshot = lookahead_engine.get_fresh_snapshot(project_id, db)

    assert snapshot is refreshed_snapshot
    calc_mock.assert_called_once_with(project_id, db)


def test_get_fresh_snapshot_normalizes_naive_snapshot_timestamp(monkeypatch):
    project_id = uuid4()
    degraded_upload = SimpleNamespace(id=uuid4())
    refreshed_snapshot = SimpleNamespace(
        programme_upload_id=degraded_upload.id,
        created_at=lookahead_engine.datetime(2026, 3, 27, 10, 0),
        data={},
    )
    db = MagicMock()
    monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda project_id, db: degraded_upload)
    monkeypatch.setattr(lookahead_engine, "get_latest_snapshot", lambda project_id, db: refreshed_snapshot)
    monkeypatch.setattr(
        lookahead_engine,
        "get_latest_booking_update_for_project",
        lambda project_id, db: lookahead_engine.datetime(2026, 3, 27, 9, 0, tzinfo=lookahead_engine.timezone.utc),
    )

    calc_mock = MagicMock()
    monkeypatch.setattr(lookahead_engine, "calculate_lookahead_for_project", calc_mock)

    snapshot = lookahead_engine.get_fresh_snapshot(project_id, db)

    assert snapshot is refreshed_snapshot
    calc_mock.assert_not_called()


def test_get_lookahead_empty_state_mentions_processed_programme(monkeypatch):
    project_id = uuid4()
    db = MagicMock()
    user = SimpleNamespace(id=uuid4(), role="manager")

    monkeypatch.setattr(lookahead, "_check_project_exists", lambda project_id, db: SimpleNamespace(managers=[user]))
    monkeypatch.setattr(lookahead, "_check_manager_project_access", lambda project, current_user: None)
    monkeypatch.setattr(lookahead, "get_fresh_snapshot", lambda project_id, db: None)

    response = lookahead.get_lookahead(project_id, db=db, _=user)

    assert response.rows == []
    assert response.message == "No processed programme available yet."


def test_get_lookahead_week_activities_normalizes_week_start(monkeypatch):
    project_id = uuid4()
    db = MagicMock()
    user = SimpleNamespace(id=uuid4(), role="manager")
    project = SimpleNamespace(managers=[user])

    monkeypatch.setattr(lookahead, "_check_project_exists", lambda project_id, db: project)
    monkeypatch.setattr(lookahead, "_check_manager_project_access", lambda project, current_user: None)
    monkeypatch.setattr(
        lookahead,
        "get_fresh_snapshot",
        lambda project_id, db: SimpleNamespace(id=uuid4()),
    )

    candidate_mock = MagicMock(
        return_value=[
            {
                "activity_id": uuid4(),
                "programme_upload_id": uuid4(),
                "activity_name": "Install tower crane",
                "start_date": "2026-03-30",
                "end_date": "2026-04-02",
                "overlap_hours": 16.0,
                "level_name": "L1",
                "zone_name": "Zone A",
                "row_confidence": "medium",
                "sort_order": 10,
                "booking_group_id": None,
                "linked_booking_count": 0,
            }
        ]
    )
    monkeypatch.setattr(lookahead, "get_weekly_activity_candidates", candidate_mock)

    response = lookahead.get_lookahead_week_activities(
        project_id,
        week_start=date(2026, 4, 1),
        asset_type="Crane",
        db=db,
        _=user,
    )

    assert response.week_start == "2026-03-30"
    assert response.asset_type == "crane"
    assert response.activities[0].activity_name == "Install tower crane"
    candidate_mock.assert_called_once_with(
        project_id=project_id,
        week_start=date(2026, 3, 30),
        asset_type="Crane",
        db=db,
    )


def test_get_lookahead_week_activities_empty_state_normalizes_week_start(monkeypatch):
    project_id = uuid4()
    db = MagicMock()
    user = SimpleNamespace(id=uuid4(), role="manager")
    project = SimpleNamespace(managers=[user])

    monkeypatch.setattr(lookahead, "_check_project_exists", lambda project_id, db: project)
    monkeypatch.setattr(lookahead, "_check_manager_project_access", lambda project, current_user: None)
    monkeypatch.setattr(lookahead, "get_fresh_snapshot", lambda project_id, db: None)

    response = lookahead.get_lookahead_week_activities(
        project_id,
        week_start=date(2026, 4, 1),
        asset_type="Crane",
        db=db,
        _=user,
    )

    assert response.week_start == "2026-03-30"
    assert response.asset_type == "crane"
    assert response.activities == []
