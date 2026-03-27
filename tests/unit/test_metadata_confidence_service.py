from datetime import date, time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.crud.slot_booking import _ensure_asset_planning_ready
from app.services import lookahead_engine
from app.services.metadata_confidence_service import (
    AssetTypeResolutionStatus,
    TradeResolutionStatus,
    infer_asset_type_resolution,
    infer_subcontractor_trade_resolution,
)


class _FakeBookingQuery:
    def __init__(self, rows):
        self._rows = rows

    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_args, **_kwargs):
        return _FakeBookingQuery(self._rows)


def test_infer_asset_type_resolution_from_name():
    resolution = infer_asset_type_resolution(
        raw_type=None,
        asset_name="Genie Z45 boom lift",
        asset_code="EWP-01",
    )

    assert resolution.status == AssetTypeResolutionStatus.INFERRED.value
    assert resolution.canonical_type == "ewp"


def test_infer_asset_type_resolution_conflict_returns_unknown():
    resolution = infer_asset_type_resolution(
        raw_type="Crane",
        asset_name="Excavator hire",
        asset_code=None,
    )

    assert resolution.status == AssetTypeResolutionStatus.UNKNOWN.value
    assert resolution.canonical_type is None


def test_infer_subcontractor_trade_resolution_from_company_name():
    resolution = infer_subcontractor_trade_resolution(
        company_name="Acme Electrical Services",
        email="ops@acmeelectrical.com.au",
    )

    assert resolution.status == TradeResolutionStatus.SUGGESTED.value
    assert resolution.suggested_trade_specialty == "electrician"


def test_ensure_asset_planning_ready_rejects_unknown_assets():
    asset = SimpleNamespace(
        name="Mystery Plant",
        canonical_type=None,
        type_resolution_status=AssetTypeResolutionStatus.UNKNOWN.value,
    )

    with pytest.raises(ValueError, match="confirmed or inferred canonical type"):
        _ensure_asset_planning_ready(asset)


def test_compute_booked_by_week_asset_skips_unresolved_assets(monkeypatch):
    project_id = uuid4()
    booking_date = date(2026, 4, 13)
    resolved_booking = SimpleNamespace(
        id=uuid4(),
        project_id=project_id,
        booking_date=booking_date,
        start_time=time(8, 0),
        end_time=time(12, 0),
        status="confirmed",
    )
    unresolved_booking = SimpleNamespace(
        id=uuid4(),
        project_id=project_id,
        booking_date=booking_date,
        start_time=time(13, 0),
        end_time=time(17, 0),
        status="confirmed",
    )
    resolved_asset = SimpleNamespace(
        name="Tower Crane 01",
        type="Tower Crane",
        canonical_type="crane",
        type_resolution_status=AssetTypeResolutionStatus.CONFIRMED.value,
    )
    unresolved_asset = SimpleNamespace(
        name="Mystery Plant",
        type="Mystery Plant",
        canonical_type=None,
        type_resolution_status=AssetTypeResolutionStatus.UNKNOWN.value,
    )
    rows = [
        (resolved_booking, resolved_asset),
        (unresolved_booking, unresolved_asset),
    ]

    monkeypatch.setattr(lookahead_engine, "get_active_asset_types", lambda _db: {"crane"})

    result = lookahead_engine._compute_booked_by_week_asset(
        db=_FakeDB(rows),
        project_id=project_id,
        tz=lookahead_engine.ZoneInfo("Australia/Adelaide"),
    )

    assert sum(result.values()) == 4.0
    assert list(result.values()) == [4.0]
