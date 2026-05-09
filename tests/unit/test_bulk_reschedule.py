from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, ANY
from uuid import uuid4

from app.api.v1 import slot_booking as booking_api
from app.crud import slot_booking as booking_crud
from app.models.asset import Asset
from app.models.site_project import ProjectNonWorkingDay, SiteProject
from app.models.slot_booking import SlotBooking
from app.schemas.enums import AssetStatus, BookingStatus, UserRole
from app.schemas.slot_booking import BulkRescheduleItem, BulkRescheduleRequest


class _Query:
    def __init__(self, *, first_value=None, all_value=None, scalar_value=None):
        self.first_value = first_value
        self.all_value = all_value if all_value is not None else []
        self.scalar_value = scalar_value

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.first_value

    def all(self):
        return self.all_value

    def scalar(self):
        return self.scalar_value


def _future_weekday(days_ahead: int = 7) -> date:
    target = date.today() + timedelta(days=days_ahead)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def _future_weekday_number(weekday: int) -> date:
    target = date.today() + timedelta(days=1)
    while target.weekday() != weekday:
        target += timedelta(days=1)
    return target


def _booking(booking_id, project_id, asset_id, *, start=time(8), end=time(12)):
    return SimpleNamespace(
        id=booking_id,
        project_id=project_id,
        manager_id=uuid4(),
        subcontractor_id=None,
        asset_id=asset_id,
        booking_group_id=None,
        booking_group=None,
        booking_date=_future_weekday(),
        start_time=start,
        end_time=end,
        status=BookingStatus.CONFIRMED,
        purpose="Lift",
        notes=None,
        source="manual",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _asset(asset_id, project_id):
    return SimpleNamespace(
        id=asset_id,
        project_id=project_id,
        name="Crane 1",
        canonical_type="crane",
        type_resolution_status="confirmed",
        status=AssetStatus.AVAILABLE,
        maintenance_start_date=None,
        maintenance_end_date=None,
        pending_booking_capacity=5,
    )


def _stub_calendar(monkeypatch, days=None):
    monkeypatch.setattr(
        booking_crud,
        "get_project_calendar_days",
        lambda *args, **kwargs: days or [],
    )


def _stub_active_upload(monkeypatch, work_days_per_week=None):
    upload = (
        SimpleNamespace(work_days_per_week=work_days_per_week)
        if work_days_per_week is not None
        else None
    )
    monkeypatch.setattr(
        booking_crud,
        "get_active_programme_upload",
        lambda *args, **kwargs: upload,
    )


def test_bulk_reschedule_blocks_project_non_working_day(monkeypatch):
    project_id = uuid4()
    booking_id = uuid4()
    asset_id = uuid4()
    target_date = _future_weekday()
    project = SimpleNamespace(id=project_id, managers=[], subcontractors=[], work_days_per_week=5)
    booking = _booking(booking_id, project_id, asset_id)
    asset = _asset(asset_id, project_id)
    non_working_day = SimpleNamespace(calendar_date=target_date, label="Site shutdown")
    db = MagicMock()
    db.query.side_effect = [
        _Query(first_value=project),
        _Query(all_value=[booking]),
        _Query(all_value=[asset]),
        _Query(all_value=[]),
    ]
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    _stub_active_upload(monkeypatch)
    _stub_calendar(monkeypatch, [non_working_day])

    response = booking_crud.validate_bulk_reschedule(
        db,
        BulkRescheduleRequest(
            project_id=project_id,
            items=[
                BulkRescheduleItem(
                    booking_id=booking_id,
                    booking_date=target_date,
                    start_time=time(9),
                    end_time=time(13),
                )
            ],
        ),
        actor_id=uuid4(),
        actor_role=UserRole.MANAGER,
    )

    assert response.can_apply is False
    assert response.items[0].errors[0].code == "project_non_working_day"


def test_bulk_reschedule_non_working_override_warns_for_manager(monkeypatch):
    project_id = uuid4()
    booking_id = uuid4()
    asset_id = uuid4()
    target_date = _future_weekday()
    project = SimpleNamespace(id=project_id, managers=[], subcontractors=[], work_days_per_week=5)
    booking = _booking(booking_id, project_id, asset_id)
    asset = _asset(asset_id, project_id)
    non_working_day = SimpleNamespace(calendar_date=target_date, label="Site shutdown")
    db = MagicMock()
    db.query.side_effect = [
        _Query(first_value=project),
        _Query(all_value=[booking]),
        _Query(all_value=[asset]),
        _Query(all_value=[]),
    ]
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    _stub_active_upload(monkeypatch)
    _stub_calendar(monkeypatch, [non_working_day])

    response = booking_crud.validate_bulk_reschedule(
        db,
        BulkRescheduleRequest(
            project_id=project_id,
            allow_non_working_days=True,
            items=[
                BulkRescheduleItem(
                    booking_id=booking_id,
                    booking_date=target_date,
                    start_time=time(9),
                    end_time=time(13),
                )
            ],
        ),
        actor_id=uuid4(),
        actor_role=UserRole.MANAGER,
    )

    assert response.can_apply is True
    assert response.items[0].warnings[0].code == "project_non_working_day"


def test_bulk_reschedule_blocks_outside_project_working_hours(monkeypatch):
    project_id = uuid4()
    booking_id = uuid4()
    asset_id = uuid4()
    target_date = _future_weekday()
    project = SimpleNamespace(
        id=project_id,
        managers=[],
        subcontractors=[],
        work_days_per_week=5,
        default_work_start_time=time(8),
        default_work_end_time=time(16),
    )
    booking = _booking(booking_id, project_id, asset_id)
    asset = _asset(asset_id, project_id)
    db = MagicMock()
    db.query.side_effect = [
        _Query(first_value=project),
        _Query(all_value=[booking]),
        _Query(all_value=[asset]),
        _Query(all_value=[]),
    ]
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    _stub_active_upload(monkeypatch)
    _stub_calendar(monkeypatch)

    response = booking_crud.validate_bulk_reschedule(
        db,
        BulkRescheduleRequest(
            project_id=project_id,
            items=[
                BulkRescheduleItem(
                    booking_id=booking_id,
                    booking_date=target_date,
                    start_time=time(6),
                    end_time=time(10),
                )
            ],
        ),
        actor_id=uuid4(),
        actor_role=UserRole.MANAGER,
    )

    assert response.can_apply is False
    assert response.items[0].errors[0].code == "outside_working_hours"


def test_bulk_reschedule_outside_project_working_hours_override_warns_for_manager(monkeypatch):
    project_id = uuid4()
    booking_id = uuid4()
    asset_id = uuid4()
    target_date = _future_weekday()
    project = SimpleNamespace(
        id=project_id,
        managers=[],
        subcontractors=[],
        work_days_per_week=5,
        default_work_start_time=time(8),
        default_work_end_time=time(16),
    )
    booking = _booking(booking_id, project_id, asset_id)
    asset = _asset(asset_id, project_id)
    db = MagicMock()
    db.query.side_effect = [
        _Query(first_value=project),
        _Query(all_value=[booking]),
        _Query(all_value=[asset]),
        _Query(all_value=[]),
    ]
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    _stub_active_upload(monkeypatch)
    _stub_calendar(monkeypatch)

    response = booking_crud.validate_bulk_reschedule(
        db,
        BulkRescheduleRequest(
            project_id=project_id,
            allow_outside_working_hours=True,
            items=[
                BulkRescheduleItem(
                    booking_id=booking_id,
                    booking_date=target_date,
                    start_time=time(6),
                    end_time=time(10),
                )
            ],
        ),
        actor_id=uuid4(),
        actor_role=UserRole.MANAGER,
    )

    assert response.can_apply is True
    assert response.items[0].warnings[0].code == "outside_working_hours"


def test_bulk_reschedule_allows_selected_bookings_to_swap_slots(monkeypatch):
    project_id = uuid4()
    asset_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    target_date = _future_weekday()
    project = SimpleNamespace(id=project_id, managers=[], subcontractors=[], work_days_per_week=5)
    first = _booking(first_id, project_id, asset_id, start=time(8), end=time(10))
    second = _booking(second_id, project_id, asset_id, start=time(10), end=time(12))
    first.booking_date = target_date
    second.booking_date = target_date
    asset = _asset(asset_id, project_id)
    db = MagicMock()
    db.query.side_effect = [
        _Query(first_value=project),
        _Query(all_value=[first, second]),
        _Query(all_value=[asset]),
        _Query(all_value=[]),
        _Query(all_value=[]),
    ]
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    _stub_active_upload(monkeypatch)
    _stub_calendar(monkeypatch)

    response = booking_crud.validate_bulk_reschedule(
        db,
        BulkRescheduleRequest(
            project_id=project_id,
            items=[
                BulkRescheduleItem(
                    booking_id=first_id,
                    booking_date=target_date,
                    start_time=time(10),
                    end_time=time(12),
                ),
                BulkRescheduleItem(
                    booking_id=second_id,
                    booking_date=target_date,
                    start_time=time(8),
                    end_time=time(10),
                ),
            ],
        ),
        actor_id=uuid4(),
        actor_role=UserRole.MANAGER,
    )

    assert response.can_apply is True
    assert all(not item.errors for item in response.items)


def test_bulk_reschedule_uses_active_upload_work_days_per_week(monkeypatch):
    project_id = uuid4()
    booking_id = uuid4()
    asset_id = uuid4()
    target_date = _future_weekday_number(5)
    project = SimpleNamespace(id=project_id, managers=[], subcontractors=[])
    booking = _booking(booking_id, project_id, asset_id)
    asset = _asset(asset_id, project_id)
    db = MagicMock()
    db.query.side_effect = [
        _Query(first_value=project),
        _Query(all_value=[booking]),
        _Query(all_value=[asset]),
        _Query(all_value=[]),
    ]
    monkeypatch.setattr(booking_crud, "sync_maintenance_status", lambda *args, **kwargs: None)
    _stub_active_upload(monkeypatch, work_days_per_week=6)
    _stub_calendar(monkeypatch)

    response = booking_crud.validate_bulk_reschedule(
        db,
        BulkRescheduleRequest(
            project_id=project_id,
            items=[
                BulkRescheduleItem(
                    booking_id=booking_id,
                    booking_date=target_date,
                    start_time=time(8),
                    end_time=time(12),
                )
            ],
        ),
        actor_id=uuid4(),
        actor_role=UserRole.MANAGER,
    )

    assert response.can_apply is True
    assert response.items[0].work_days_per_week == 6
    assert response.items[0].work_days_source == "active_project_upload"


def test_bulk_reschedule_route_notifies_each_booking_and_refreshes_once(monkeypatch):
    project_id = uuid4()
    booking_id = uuid4()
    user_id = uuid4()
    current_user = SimpleNamespace(id=user_id, role=UserRole.MANAGER.value)
    payload = BulkRescheduleRequest(
        project_id=project_id,
        items=[
            BulkRescheduleItem(
                booking_id=booking_id,
                booking_date=_future_weekday(),
                start_time=time(8),
                end_time=time(12),
            )
        ],
    )
    project = SimpleNamespace(id=project_id, managers=[SimpleNamespace(id=user_id)], subcontractors=[])
    booking_detail = SimpleNamespace(id=booking_id, project_id=project_id)

    monkeypatch.setattr(booking_api, "get_user_role", lambda entity: UserRole.MANAGER)
    monkeypatch.setattr(booking_api, "get_entity_id", lambda entity: user_id)
    monkeypatch.setattr(booking_api, "_load_project_booking_context", lambda db, pid: project)
    monkeypatch.setattr(
        booking_api.booking_crud,
        "apply_bulk_reschedule",
        lambda *args, **kwargs: SimpleNamespace(
            validation=SimpleNamespace(can_apply=True),
            bookings=[booking_detail],
        ),
    )
    notify_mock = MagicMock()
    refresh_mock = MagicMock()
    monkeypatch.setattr(booking_api, "notify_booking_change", notify_mock)
    monkeypatch.setattr(booking_api, "refresh_lookahead_after_project_change", refresh_mock)

    response = booking_api.bulk_reschedule_bookings(
        payload=payload,
        db=MagicMock(),
        current_entity=current_user,
    )

    assert response.bookings == [booking_detail]
    notify_mock.assert_called_once_with(ANY, booking_id, "rescheduled", user_id)
    refresh_mock.assert_called_once_with(project_id)
