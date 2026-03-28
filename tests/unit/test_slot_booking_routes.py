from datetime import date, time, timedelta
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import ANY, MagicMock

from app.api.v1 import slot_booking as booking_api
from app.schemas.enums import BookingStatus, UserRole
from app.schemas.slot_booking import BookingCreate, BookingStatusUpdate, BulkBookingCreate


def test_create_booking_refreshes_lookahead(monkeypatch):
    project_id = uuid4()
    booking_id = uuid4()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role=UserRole.MANAGER.value)
    booking_data = BookingCreate(
        project_id=project_id,
        asset_id=uuid4(),
        booking_date=date.today() + timedelta(days=1),
        start_time=time(8, 0),
        end_time=time(16, 0),
        purpose="Book crane",
    )

    monkeypatch.setattr(booking_api, "get_user_role", lambda entity: UserRole.MANAGER)
    monkeypatch.setattr(booking_api, "get_entity_id", lambda entity: user_id)
    project = SimpleNamespace(id=project_id, managers=[SimpleNamespace(id=user_id)], subcontractors=[])
    asset = SimpleNamespace(id=booking_data.asset_id)
    monkeypatch.setattr(booking_api, "_load_project_booking_context", lambda db, project_id: project)
    monkeypatch.setattr(booking_api, "_load_asset_booking_context", lambda db, asset_id: asset)
    monkeypatch.setattr(
        booking_api.booking_crud,
        "check_booking_conflicts",
        lambda db, payload, asset=None: SimpleNamespace(has_confirmed_conflict=False, can_request=True),
    )
    create_mock = MagicMock(return_value=SimpleNamespace(id=booking_id, project_id=project_id))
    monkeypatch.setattr(booking_api.booking_crud, "create_booking", create_mock)
    detail_response = SimpleNamespace(id=booking_id, project_id=project_id)
    monkeypatch.setattr(booking_api.booking_crud, "get_booking_detail", lambda db, booking_id: detail_response)

    notify_mock = MagicMock()
    refresh_mock = MagicMock()
    monkeypatch.setattr(booking_api, "notify_booking_change", notify_mock)
    monkeypatch.setattr(booking_api, "refresh_lookahead_after_project_change", refresh_mock)

    response = booking_api.create_booking(booking_data, db=MagicMock(), current_entity=current_user)

    assert response is detail_response
    create_mock.assert_called_once_with(
        ANY,
        booking_data,
        created_by_id=user_id,
        created_by_role=UserRole.MANAGER,
        comment=booking_data.comment,
        project=project,
        asset=asset,
    )
    notify_mock.assert_called_once_with(ANY, booking_id, "created", user_id)
    refresh_mock.assert_called_once_with(project_id)


def test_update_booking_status_refreshes_project_after_approval(monkeypatch):
    booking_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    booking = SimpleNamespace(id=booking_id, project_id=project_id, status=BookingStatus.PENDING)
    updated_booking = SimpleNamespace(id=booking_id, project_id=project_id, status=BookingStatus.CONFIRMED)
    current_user = SimpleNamespace(id=user_id, role=UserRole.MANAGER.value)

    monkeypatch.setattr(booking_api, "get_user_role", lambda entity: UserRole.MANAGER)
    monkeypatch.setattr(booking_api, "get_entity_id", lambda entity: user_id)
    monkeypatch.setattr(booking_api.booking_crud, "get_booking", lambda db, booking_id: booking)
    monkeypatch.setattr(booking_api, "check_booking_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        booking_api.booking_crud,
        "update_booking_status",
        lambda *args, **kwargs: (updated_booking, [uuid4()]),
    )
    monkeypatch.setattr(booking_api.booking_crud, "get_booking_detail", lambda db, booking_id: updated_booking)

    notify_mock = MagicMock()
    refresh_mock = MagicMock()
    monkeypatch.setattr(booking_api, "notify_booking_change", notify_mock)
    monkeypatch.setattr(booking_api, "refresh_lookahead_after_project_change", refresh_mock)

    response = booking_api.update_booking_status(
        booking_id=booking_id,
        status_update=BookingStatusUpdate(status=BookingStatus.CONFIRMED, comment="approve"),
        db=MagicMock(),
        current_entity=current_user,
    )

    assert response is updated_booking
    assert notify_mock.call_count == 2
    refresh_mock.assert_called_once_with(project_id)


def test_create_bulk_bookings_uses_preloaded_project_context_for_access(monkeypatch):
    project_id = uuid4()
    asset_id = uuid4()
    subcontractor_id = uuid4()
    booking_id = uuid4()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role=UserRole.MANAGER.value)
    bulk_data = BulkBookingCreate(
        project_id=project_id,
        subcontractor_id=subcontractor_id,
        asset_ids=[asset_id],
        booking_dates=[date.today() + timedelta(days=1)],
        start_time=time(8, 0),
        end_time=time(16, 0),
        purpose="Bulk booking",
    )

    monkeypatch.setattr(booking_api, "get_user_role", lambda entity: UserRole.MANAGER)
    monkeypatch.setattr(booking_api, "get_entity_id", lambda entity: user_id)
    monkeypatch.setattr(
        booking_api,
        "_load_project_booking_context",
        lambda db, project_id: SimpleNamespace(
            id=project_id,
            managers=[SimpleNamespace(id=user_id)],
            subcontractors=[SimpleNamespace(id=subcontractor_id)],
        ),
    )
    monkeypatch.setattr(
        booking_api.project_crud,
        "has_project_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("bulk route should use preloaded project managers")
        ),
    )
    monkeypatch.setattr(
        booking_api.project_crud,
        "is_subcontractor_assigned",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("bulk route should use preloaded project subcontractors")
        ),
    )
    monkeypatch.setattr(
        booking_api.booking_crud,
        "check_booking_conflicts",
        lambda db, payload: SimpleNamespace(has_confirmed_conflict=False, can_request=True),
    )
    monkeypatch.setattr(
        booking_api.booking_crud,
        "create_bulk_bookings",
        lambda *args, **kwargs: [SimpleNamespace(id=booking_id, project_id=project_id)],
    )
    monkeypatch.setattr(
        booking_api.booking_crud,
        "get_booking_detail",
        lambda db, booking_id: SimpleNamespace(id=booking_id, project_id=project_id),
    )

    notify_mock = MagicMock()
    refresh_mock = MagicMock()
    monkeypatch.setattr(booking_api, "notify_booking_change", notify_mock)
    monkeypatch.setattr(booking_api, "refresh_lookahead_after_project_change", refresh_mock)

    response = booking_api.create_bulk_bookings(
        bulk_data=bulk_data,
        db=MagicMock(),
        current_entity=current_user,
    )

    assert len(response) == 1
    notify_mock.assert_called_once_with(ANY, booking_id, "created", user_id)
    refresh_mock.assert_called_once_with(project_id)
