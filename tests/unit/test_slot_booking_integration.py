from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

from app.crud.slot_booking import (
    _build_booking_detail_response,
    _mark_booking_group_modified_if_needed,
    _mark_matching_lookahead_notifications_acted,
)
from app.schemas.enums import BookingStatus


def test_mark_matching_lookahead_notifications_acted_marks_matching_rows():
    booking = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        subcontractor_id=uuid4(),
        booking_date=date(2026, 3, 30),
        status=BookingStatus.CONFIRMED,
        asset_id=uuid4(),
    )
    asset = SimpleNamespace(canonical_type="crane", type_resolution_status="confirmed")
    notification = SimpleNamespace(
        id=uuid4(),
        status="pending",
        acted_at=None,
        booking_id=None,
    )

    query = MagicMock()
    query.filter.return_value = query
    query.all.return_value = [notification]
    db = MagicMock()
    db.query.return_value = query

    acted_ids = _mark_matching_lookahead_notifications_acted(db, booking, asset=asset)

    assert acted_ids == [notification.id]
    assert notification.status == "acted"
    assert notification.booking_id == booking.id
    assert notification.acted_at is not None


def test_mark_matching_lookahead_notifications_acted_skips_non_confirmed_bookings():
    booking = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        subcontractor_id=uuid4(),
        booking_date=date(2026, 3, 30),
        status=BookingStatus.PENDING,
        asset_id=uuid4(),
    )
    db = MagicMock()

    acted_ids = _mark_matching_lookahead_notifications_acted(db, booking)

    assert acted_ids == []
    db.query.assert_not_called()


def test_build_booking_detail_response_includes_source():
    booking = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        manager_id=uuid4(),
        subcontractor_id=uuid4(),
        asset_id=uuid4(),
        booking_date=date(2026, 3, 30),
        start_time=time(7, 0),
        end_time=time(11, 0),
        status=BookingStatus.CONFIRMED,
        source="manual",
        purpose="Crane booking",
        notes="Confirmed by manager",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project=SimpleNamespace(id=uuid4(), name="Project", location="Adelaide", status="active"),
        manager=SimpleNamespace(
            id=uuid4(),
            email="manager@example.com",
            first_name="Mia",
            last_name="Manager",
            role="manager",
        ),
        subcontractor=SimpleNamespace(
            id=uuid4(),
            email="sub@example.com",
            first_name="Sam",
            last_name="Sub",
            company_name="Sub Co",
            trade_specialty="general",
        ),
        asset=SimpleNamespace(
            id=uuid4(),
            asset_code="CR-01",
            name="Tower Crane",
            type="Crane",
            status=SimpleNamespace(value="available"),
            pending_booking_capacity=2,
        ),
        booking_group=SimpleNamespace(
            id=uuid4(),
            expected_asset_type="crane",
            is_modified=False,
            activity=SimpleNamespace(id=uuid4(), name="Install tower crane"),
        ),
    )

    response = _build_booking_detail_response(booking)

    assert response.source == "manual"
    assert response.booking_group_id == booking.booking_group.id
    assert response.programme_activity_id == booking.booking_group.activity.id
    assert response.programme_activity_name == "Install tower crane"
    assert response.expected_asset_type == "crane"
    assert response.is_modified is False
    assert response.asset.name == "Tower Crane"


def test_mark_booking_group_modified_if_needed_ignores_same_type_asset_swap():
    booking_group = SimpleNamespace(expected_asset_type="crane", is_modified=False)
    booking = SimpleNamespace(
        booking_date=date(2026, 3, 30),
        start_time=time(7, 0),
        end_time=time(11, 0),
        subcontractor_id=uuid4(),
    )

    _mark_booking_group_modified_if_needed(
        booking_group,
        booking=booking,
        previous_date=booking.booking_date,
        previous_start_time=booking.start_time,
        previous_end_time=booking.end_time,
        previous_subcontractor_id=booking.subcontractor_id,
        previous_asset_type="crane",
        current_asset_type="crane",
    )

    assert booking_group.is_modified is False


def test_mark_booking_group_modified_if_needed_marks_date_drift():
    booking_group = SimpleNamespace(expected_asset_type="crane", is_modified=False)
    subcontractor_id = uuid4()
    booking = SimpleNamespace(
        booking_date=date(2026, 3, 31),
        start_time=time(7, 0),
        end_time=time(11, 0),
        subcontractor_id=subcontractor_id,
    )

    _mark_booking_group_modified_if_needed(
        booking_group,
        booking=booking,
        previous_date=date(2026, 3, 30),
        previous_start_time=time(7, 0),
        previous_end_time=time(11, 0),
        previous_subcontractor_id=subcontractor_id,
        previous_asset_type="crane",
        current_asset_type="crane",
    )

    assert booking_group.is_modified is True
