from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

from app.crud import slot_booking as booking_crud
from app.schemas.enums import AssetStatus, BookingStatus, UserRole


def test_resolve_booking_actor_uses_preloaded_project_members_for_manager():
    manager_id = uuid4()
    subcontractor_id = uuid4()
    project_id = uuid4()
    db = MagicMock()
    project = SimpleNamespace(
        id=project_id,
        managers=[SimpleNamespace(id=manager_id)],
        subcontractors=[SimpleNamespace(id=subcontractor_id)],
    )

    resolved_manager_id, resolved_subcontractor_id, booking_status = booking_crud._resolve_booking_actor(
        db=db,
        actor_id=manager_id,
        actor_role=UserRole.MANAGER,
        provided_manager_id=None,
        provided_subcontractor_id=subcontractor_id,
        project_id=project_id,
        project=project,
    )

    assert resolved_manager_id == manager_id
    assert resolved_subcontractor_id == subcontractor_id
    assert booking_status.value == "confirmed"
    db.query.assert_not_called()


def test_check_booking_conflicts_reuses_preloaded_asset_capacity():
    asset_id = uuid4()
    query_confirmed = MagicMock()
    query_confirmed.filter.return_value = query_confirmed
    query_confirmed.all.return_value = []

    query_pending = MagicMock()
    query_pending.filter.return_value = query_pending
    query_pending.scalar.return_value = 1

    db = MagicMock()
    db.query.side_effect = [query_confirmed, query_pending]

    response = booking_crud.check_booking_conflicts(
        db,
        conflict_check=SimpleNamespace(
            asset_id=asset_id,
            booking_date=SimpleNamespace(),
            start_time=SimpleNamespace(),
            end_time=SimpleNamespace(),
            exclude_booking_id=None,
        ),
        asset=SimpleNamespace(id=asset_id, pending_booking_capacity=3),
    )

    assert response.pending_capacity == 3
    assert db.query.call_count == 2


def test_resolve_booking_actor_reloads_project_when_preloaded_project_mismatches():
    manager_id = uuid4()
    project_id = uuid4()
    wrong_project_id = uuid4()
    canonical_project = SimpleNamespace(
        id=project_id,
        managers=[SimpleNamespace(id=manager_id)],
        subcontractors=[],
    )

    query = MagicMock()
    query.options.return_value = query
    query.filter.return_value = query
    query.first.return_value = canonical_project

    db = MagicMock()
    db.query.return_value = query

    resolved_manager_id, resolved_subcontractor_id, booking_status = booking_crud._resolve_booking_actor(
        db=db,
        actor_id=manager_id,
        actor_role=UserRole.MANAGER,
        provided_manager_id=None,
        provided_subcontractor_id=None,
        project_id=project_id,
        project=SimpleNamespace(id=wrong_project_id, managers=[], subcontractors=[]),
    )

    assert resolved_manager_id == manager_id
    assert resolved_subcontractor_id is None
    assert booking_status == BookingStatus.CONFIRMED
    db.query.assert_called_once_with(booking_crud.SiteProject)


def test_create_booking_reloads_asset_when_preloaded_asset_mismatches(monkeypatch):
    project_id = uuid4()
    asset_id = uuid4()
    wrong_asset_id = uuid4()
    manager_id = uuid4()
    booking_data = SimpleNamespace(
        project_id=project_id,
        asset_id=asset_id,
        booking_date=SimpleNamespace(),
        start_time=SimpleNamespace(),
        end_time=SimpleNamespace(),
        manager_id=None,
        subcontractor_id=None,
        programme_activity_id=None,
        selected_week_start=None,
        purpose="purpose",
        notes="notes",
    )
    canonical_asset = SimpleNamespace(
        id=asset_id,
        name="Crane 1",
        canonical_type="crane",
        type_resolution_status="confirmed",
        status=AssetStatus.AVAILABLE,
        maintenance_start_date=None,
        maintenance_end_date=None,
    )
    asset_query = MagicMock()
    asset_query.filter.return_value = asset_query
    asset_query.first.return_value = canonical_asset

    db = MagicMock()
    db.query.return_value = asset_query

    monkeypatch.setattr(
        booking_crud,
        "_resolve_booking_actor",
        lambda **kwargs: (manager_id, None, BookingStatus.CONFIRMED),
    )
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(booking_crud, "log_booking_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        booking_crud,
        "_auto_deny_competing_pending_bookings",
        lambda *args, **kwargs: [],
    )

    acted_assets = []
    monkeypatch.setattr(
        booking_crud,
        "_mark_matching_lookahead_notifications_acted",
        lambda *args, asset=None, **kwargs: acted_assets.append(asset) or [],
    )

    booking_crud.create_booking(
        db=db,
        booking_data=booking_data,
        created_by_id=manager_id,
        created_by_role=UserRole.MANAGER,
        project=SimpleNamespace(id=project_id, managers=[], subcontractors=[]),
        asset=SimpleNamespace(id=wrong_asset_id),
    )

    db.query.assert_called_once_with(booking_crud.Asset)
    assert acted_assets == [canonical_asset]


def test_create_booking_reloads_asset_when_preloaded_asset_has_no_identity(monkeypatch):
    project_id = uuid4()
    asset_id = uuid4()
    manager_id = uuid4()
    booking_data = SimpleNamespace(
        project_id=project_id,
        asset_id=asset_id,
        booking_date=SimpleNamespace(),
        start_time=SimpleNamespace(),
        end_time=SimpleNamespace(),
        manager_id=None,
        subcontractor_id=None,
        programme_activity_id=None,
        selected_week_start=None,
        purpose="purpose",
        notes="notes",
    )
    canonical_asset = SimpleNamespace(
        id=asset_id,
        name="Crane 1",
        canonical_type="crane",
        type_resolution_status="confirmed",
        status=AssetStatus.AVAILABLE,
        maintenance_start_date=None,
        maintenance_end_date=None,
    )
    asset_query = MagicMock()
    asset_query.filter.return_value = asset_query
    asset_query.first.return_value = canonical_asset

    db = MagicMock()
    db.query.return_value = asset_query

    monkeypatch.setattr(
        booking_crud,
        "_resolve_booking_actor",
        lambda **kwargs: (manager_id, None, BookingStatus.CONFIRMED),
    )
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(booking_crud, "log_booking_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        booking_crud,
        "_auto_deny_competing_pending_bookings",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        booking_crud,
        "_mark_matching_lookahead_notifications_acted",
        lambda *args, **kwargs: [],
    )

    booking_crud.create_booking(
        db=db,
        booking_data=booking_data,
        created_by_id=manager_id,
        created_by_role=UserRole.MANAGER,
        project=SimpleNamespace(id=project_id, managers=[], subcontractors=[]),
        asset=SimpleNamespace(id=None),
    )

    db.query.assert_called_once_with(booking_crud.Asset)


def test_create_booking_reuses_matching_preloaded_asset(monkeypatch):
    project_id = uuid4()
    asset_id = uuid4()
    manager_id = uuid4()
    booking_data = SimpleNamespace(
        project_id=project_id,
        asset_id=asset_id,
        booking_date=SimpleNamespace(),
        start_time=SimpleNamespace(),
        end_time=SimpleNamespace(),
        manager_id=None,
        subcontractor_id=None,
        programme_activity_id=None,
        selected_week_start=None,
        purpose="purpose",
        notes="notes",
    )
    preloaded_project = SimpleNamespace(id=project_id, managers=[], subcontractors=[])
    preloaded_asset = SimpleNamespace(
        id=asset_id,
        name="Crane 1",
        canonical_type="crane",
        type_resolution_status="confirmed",
        status=AssetStatus.AVAILABLE,
        maintenance_start_date=None,
        maintenance_end_date=None,
    )
    db = MagicMock()

    monkeypatch.setattr(
        booking_crud,
        "_resolve_booking_actor",
        lambda **kwargs: (manager_id, None, BookingStatus.CONFIRMED),
    )
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(booking_crud, "log_booking_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        booking_crud,
        "_auto_deny_competing_pending_bookings",
        lambda *args, **kwargs: [],
    )

    acted_assets = []
    monkeypatch.setattr(
        booking_crud,
        "_mark_matching_lookahead_notifications_acted",
        lambda *args, asset=None, **kwargs: acted_assets.append(asset) or [],
    )

    booking_crud.create_booking(
        db=db,
        booking_data=booking_data,
        created_by_id=manager_id,
        created_by_role=UserRole.MANAGER,
        project=preloaded_project,
        asset=preloaded_asset,
    )

    db.query.assert_not_called()
    assert acted_assets == [preloaded_asset]


def test_check_booking_conflicts_reloads_asset_when_preloaded_asset_mismatches():
    asset_id = uuid4()
    query_confirmed = MagicMock()
    query_confirmed.filter.return_value = query_confirmed
    query_confirmed.all.return_value = []

    query_pending = MagicMock()
    query_pending.filter.return_value = query_pending
    query_pending.scalar.return_value = 1

    query_asset = MagicMock()
    query_asset.filter.return_value = query_asset
    query_asset.first.return_value = SimpleNamespace(id=asset_id, pending_booking_capacity=7)

    db = MagicMock()
    db.query.side_effect = [query_confirmed, query_pending, query_asset]

    response = booking_crud.check_booking_conflicts(
        db,
        conflict_check=SimpleNamespace(
            asset_id=asset_id,
            booking_date=SimpleNamespace(),
            start_time=SimpleNamespace(),
            end_time=SimpleNamespace(),
            exclude_booking_id=None,
        ),
        asset=SimpleNamespace(id=uuid4(), pending_booking_capacity=3),
    )

    assert response.pending_capacity == 7
    assert db.query.call_count == 3
