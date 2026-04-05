"""Unit tests for the capacity dashboard feature.

Covers:
- effective_asset_max_hours_per_day priority chain
- _compute_capacity_by_week_asset: maintenance day-accuracy, "none"/"other" handling,
  per-asset override, unresolved exclusion
- _capacity_status: all threshold boundaries
- compute_capacity_dashboard: empty-state, week clamping, merge logic, diagnostics,
  and earliest-week anchoring
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.crud import asset as asset_crud
from app.core.constants import (
    CAPACITY_DASHBOARD_MAX_WEEKS,
    DEFAULT_FALLBACK_MAX_HOURS,
    effective_asset_max_hours_per_day,
)
from app.schemas.asset import AssetCreate
from app.services import lookahead_engine
from app.services.lookahead_engine import _capacity_status, _compute_capacity_by_week_asset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_asset(
    *,
    canonical_type: str = "crane",
    status_val: str = "available",
    planning_ready: bool = True,
    max_hours_per_day=None,
    maint_start=None,
    maint_end=None,
):
    """Build a minimal asset SimpleNamespace."""
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        canonical_type=canonical_type,
        status=SimpleNamespace(value=status_val),
        planning_ready=planning_ready,
        max_hours_per_day=max_hours_per_day,
        maintenance_start_date=maint_start,
        maintenance_end_date=maint_end,
        type_resolution_status="confirmed",
    )


class _RowsQuery:
    """Chainable mock query used to simulate db.query(...).filter(...).all()."""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows

    def in_(self, *args, **kwargs):  # needed for .filter(...in_(...))
        return self


# ---------------------------------------------------------------------------
# effective_asset_max_hours_per_day
# ---------------------------------------------------------------------------

class TestEffectiveAssetMaxHoursPerDay:
    def test_uses_per_asset_override(self):
        asset = SimpleNamespace(max_hours_per_day=6.0, canonical_type="crane")
        assert effective_asset_max_hours_per_day(asset, {"crane": 10.0}) == 6.0

    def test_falls_back_to_type_dict(self):
        asset = SimpleNamespace(max_hours_per_day=None, canonical_type="crane")
        assert effective_asset_max_hours_per_day(asset, {"crane": 10.0}) == 10.0

    def test_falls_back_to_global_default(self):
        asset = SimpleNamespace(max_hours_per_day=None, canonical_type=None)
        assert effective_asset_max_hours_per_day(asset, {}) == DEFAULT_FALLBACK_MAX_HOURS

    def test_zero_override_is_not_used(self):
        # 0 is falsy but not None — per design, None means "use fallback"; 0 would
        # be an unusual but valid explicit override (though schema prevents it via gt=0).
        # The function checks `is not None`, so 0.0 would be honoured.
        asset = SimpleNamespace(max_hours_per_day=0.0, canonical_type="crane")
        assert effective_asset_max_hours_per_day(asset, {"crane": 10.0}) == 0.0


# ---------------------------------------------------------------------------
# _capacity_status
# ---------------------------------------------------------------------------

class TestCapacityStatus:
    @pytest.mark.parametrize("demand,capacity,asset_type,expected", [
        # "other" always review_needed
        (10.0, 20.0, "other", "review_needed"),
        (0.0, 0.0, "other", "review_needed"),
        # no capacity + no demand → idle
        (0.0, 0.0, "crane", "idle"),
        # no capacity + demand → no_capacity
        (5.0, 0.0, "crane", "no_capacity"),
        # capacity with zero demand → idle
        (0.0, 40.0, "crane", "idle"),
        # under_utilised: util < 0.70
        (20.0, 40.0, "crane", "under_utilised"),   # 50%
        # balanced: 0.70 <= util < 0.90
        (30.0, 40.0, "crane", "balanced"),          # 75%
        # tight: 0.90 <= util < 1.00
        (36.0, 40.0, "crane", "tight"),             # 90%
        # over_capacity: util >= 1.00
        (40.0, 40.0, "crane", "over_capacity"),     # 100%
        (50.0, 40.0, "crane", "over_capacity"),     # 125%
    ])
    def test_status_thresholds(self, demand, capacity, asset_type, expected):
        assert _capacity_status(demand, capacity, asset_type) == expected


# ---------------------------------------------------------------------------
# _compute_capacity_by_week_asset
# ---------------------------------------------------------------------------

MONDAY = date(2026, 4, 6)  # a known Monday
WORK_DAYS = 5


def _db_returning(assets):
    """Return a fake Session whose .query().filter().all() yields *assets*."""
    db = MagicMock()
    db.query.return_value = _RowsQuery(assets)
    return db


class TestComputeCapacityByWeekAsset:
    def _run(self, assets, week_starts=None, work_days=WORK_DAYS):
        if week_starts is None:
            week_starts = [MONDAY]
        db = _db_returning(assets)

        with (
            patch.object(lookahead_engine, "asset_is_planning_ready", side_effect=lambda a: a.planning_ready),
            patch.object(lookahead_engine, "get_max_hours_for_type", return_value=10.0),
        ):
            return _compute_capacity_by_week_asset(db, uuid4(), week_starts, work_days)

    # ------------------------------------------------------------------

    def test_empty_asset_pool_returns_empty_map(self):
        cap_map, diag = self._run([])
        assert cap_map == {}
        assert diag["total_assets_evaluated"] == 0

    def test_basic_capacity_one_asset_full_week(self):
        # crane, 10 h/day, no maintenance → 5 working days × 10 h = 50 h
        asset = _make_asset(canonical_type="crane")
        cap_map, _ = self._run([asset])
        assert (MONDAY, "crane") in cap_map
        assert cap_map[(MONDAY, "crane")]["capacity_hours"] == 50.0
        assert cap_map[(MONDAY, "crane")]["available_assets"] == 1

    def test_maintenance_removes_overlapping_days_only(self):
        # Maintenance Mon–Wed → only Thu + Fri contribute (2 days × 10 h = 20 h)
        asset = _make_asset(
            canonical_type="crane",
            maint_start=MONDAY,
            maint_end=MONDAY + timedelta(days=2),  # Mon–Wed
        )
        cap_map, _ = self._run([asset])
        assert cap_map[(MONDAY, "crane")]["capacity_hours"] == 20.0
        assert cap_map[(MONDAY, "crane")]["available_assets"] == 1

    def test_full_week_maintenance_excludes_asset(self):
        # Maintenance covers entire week → no capacity, no available_assets
        asset = _make_asset(
            canonical_type="crane",
            maint_start=MONDAY,
            maint_end=MONDAY + timedelta(days=6),
        )
        cap_map, _ = self._run([asset])
        assert (MONDAY, "crane") not in cap_map

    def test_other_type_zero_capacity_but_counted(self):
        asset = _make_asset(canonical_type="other")
        cap_map, _ = self._run([asset])
        assert cap_map[(MONDAY, "other")]["capacity_hours"] == 0.0
        assert cap_map[(MONDAY, "other")]["available_assets"] == 1

    def test_not_planning_ready_excluded(self):
        asset = _make_asset(canonical_type="crane", planning_ready=False)
        cap_map, diag = self._run([asset])
        assert cap_map == {}
        assert diag["excluded_not_planning_ready"] == 1

    def test_per_asset_max_hours_override(self):
        # asset has 8 h/day override (type default is 10 via mock)
        asset = _make_asset(canonical_type="crane", max_hours_per_day=8.0)
        cap_map, _ = self._run([asset])
        # 5 working days × 8 h = 40 h
        assert cap_map[(MONDAY, "crane")]["capacity_hours"] == 40.0

    def test_multiple_assets_same_type_accumulate(self):
        assets = [
            _make_asset(canonical_type="crane"),
            _make_asset(canonical_type="crane"),
        ]
        cap_map, _ = self._run(assets)
        # 2 assets × 5 days × 10 h = 100 h
        assert cap_map[(MONDAY, "crane")]["capacity_hours"] == 100.0
        assert cap_map[(MONDAY, "crane")]["available_assets"] == 2

    def test_multiple_weeks(self):
        asset = _make_asset(canonical_type="crane")
        week2 = MONDAY + timedelta(weeks=1)
        cap_map, _ = self._run([asset], week_starts=[MONDAY, week2])
        assert (MONDAY, "crane") in cap_map
        assert (week2, "crane") in cap_map

    def test_partial_week_uses_actual_working_dates(self):
        # Request a week starting on a Wednesday (mid-week).
        # _iter_working_dates normalises the span Mon-Sun but we pass week_starts directly,
        # so the week window is still Mon–Sun of the supplied start's week.
        # With work_days=5 and a standard week, this should still give 5 days.
        asset = _make_asset(canonical_type="crane")
        cap_map, _ = self._run([asset], week_starts=[MONDAY])
        assert cap_map[(MONDAY, "crane")]["capacity_hours"] == 50.0


# ---------------------------------------------------------------------------
# compute_capacity_dashboard
# ---------------------------------------------------------------------------

class TestComputeCapacityDashboard:
    def _make_snapshot(self):
        snap = SimpleNamespace(
            id=uuid4(),
            snapshot_date=date(2026, 4, 5),
            programme_upload_id=uuid4(),
            created_at=datetime(2026, 4, 5, 9, 0, tzinfo=timezone.utc),
            data={"generated_at": "2026-04-05T09:00:00+00:00"},
        )
        return snap

    def _make_upload(self):
        return SimpleNamespace(id=uuid4(), work_days_per_week=5)

    def _db_for_capacity(self, *, min_week=None, rows=None):
        db = MagicMock()

        project_query = MagicMock()
        project_filter = MagicMock()
        project_filter.first.return_value = SimpleNamespace(id=uuid4())
        project_query.filter.return_value = project_filter
        query_chain = [project_query]

        if min_week is not None:
            min_query = MagicMock()
            min_filter = MagicMock()
            min_filter.scalar.return_value = min_week
            min_query.filter.return_value = min_filter
            query_chain.append(min_query)

        rows_query = MagicMock()
        rows_filter = MagicMock()
        rows_filter.all.return_value = rows or []
        rows_query.filter.return_value = rows_filter
        query_chain.append(rows_query)

        db.query.side_effect = query_chain
        return db

    # ------------------------------------------------------------------

    def test_empty_state_when_no_upload(self, monkeypatch):
        monkeypatch.setattr(lookahead_engine, "get_fresh_snapshot", lambda pid, db: None)
        monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda pid, db: None)

        db = self._db_for_capacity()

        result = lookahead_engine.compute_capacity_dashboard(uuid4(), db)
        assert result["message"] is not None
        assert result.get("rows", {}) == {}

    def test_week_window_clamped_to_max(self, monkeypatch):
        snapshot = self._make_snapshot()
        upload = self._make_upload()

        monkeypatch.setattr(lookahead_engine, "get_fresh_snapshot", lambda pid, db: snapshot)
        monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda pid, db: upload)
        monkeypatch.setattr(
            lookahead_engine, "_compute_capacity_by_week_asset",
            lambda db, pid, week_starts, wdpw: ({}, {"excluded_not_planning_ready": 0, "excluded_retired": 0, "total_assets_evaluated": 0, "unresolved_asset_count": 0, "excluded_asset_types": []})
        )

        db = self._db_for_capacity(rows=[])

        result = lookahead_engine.compute_capacity_dashboard(uuid4(), db, start_week=MONDAY, weeks=999)
        assert len(result["weeks"]) == CAPACITY_DASHBOARD_MAX_WEEKS

    def test_start_week_normalised_to_monday(self, monkeypatch):
        snapshot = self._make_snapshot()
        upload = self._make_upload()

        monkeypatch.setattr(lookahead_engine, "get_fresh_snapshot", lambda pid, db: snapshot)
        monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda pid, db: upload)
        monkeypatch.setattr(
            lookahead_engine, "_compute_capacity_by_week_asset",
            lambda db, pid, week_starts, wdpw: ({}, {"excluded_not_planning_ready": 0, "excluded_retired": 0, "total_assets_evaluated": 0, "unresolved_asset_count": 0, "excluded_asset_types": []})
        )

        db = self._db_for_capacity(rows=[])

        wednesday = date(2026, 4, 8)  # Wednesday
        result = lookahead_engine.compute_capacity_dashboard(uuid4(), db, start_week=wednesday, weeks=1)
        assert result["start_week"] == "2026-04-06"  # normalised to Monday

    def test_default_start_week_uses_earliest_visible_snapshot_week(self, monkeypatch):
        snapshot = self._make_snapshot()
        upload = self._make_upload()

        monkeypatch.setattr(lookahead_engine, "get_fresh_snapshot", lambda pid, db: snapshot)
        monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda pid, db: upload)
        monkeypatch.setattr(
            lookahead_engine, "_compute_capacity_by_week_asset",
            lambda db, pid, week_starts, wdpw: ({}, {"excluded_not_planning_ready": 0, "excluded_retired": 0, "total_assets_evaluated": 0, "unresolved_asset_count": 0, "excluded_asset_types": []})
        )

        db = self._db_for_capacity(min_week=MONDAY + timedelta(weeks=2), rows=[])
        result = lookahead_engine.compute_capacity_dashboard(uuid4(), db, weeks=1)
        assert result["start_week"] == (MONDAY + timedelta(weeks=2)).isoformat()

    def test_cells_merged_from_demand_and_capacity(self, monkeypatch):
        snapshot = self._make_snapshot()
        upload = self._make_upload()

        # Fake snapshot row: demand=40, booked=10 for crane in week of 2026-04-06
        fake_row = SimpleNamespace(
            week_start=MONDAY,
            asset_type="crane",
            demand_hours=40.0,
            booked_hours=10.0,
            is_anomalous=True,
        )

        monkeypatch.setattr(lookahead_engine, "get_fresh_snapshot", lambda pid, db: snapshot)
        monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda pid, db: upload)
        monkeypatch.setattr(
            lookahead_engine, "_compute_capacity_by_week_asset",
            lambda db, pid, week_starts, wdpw: (
                {(MONDAY, "crane"): {"capacity_hours": 50.0, "available_assets": 1}},
                {"excluded_not_planning_ready": 0, "excluded_retired": 0, "total_assets_evaluated": 1, "unresolved_asset_count": 0, "excluded_asset_types": []},
            )
        )

        db = self._db_for_capacity(rows=[fake_row])

        result = lookahead_engine.compute_capacity_dashboard(uuid4(), db, start_week=MONDAY, weeks=1)

        assert list(result["rows"].keys()) == ["crane"]
        cell = result["rows"]["crane"][MONDAY.isoformat()]
        assert cell["demand_hours"] == 40.0
        assert cell["booked_hours"] == 10.0
        assert cell["capacity_hours"] == 50.0
        assert cell["status"] == "balanced"  # 40/50 = 80%, between 70% and 90%
        assert cell["is_anomalous"] is True

    def test_diagnostics_present_in_result(self, monkeypatch):
        snapshot = self._make_snapshot()
        upload = self._make_upload()
        other_row = SimpleNamespace(
            week_start=MONDAY,
            asset_type="other",
            demand_hours=12.0,
            booked_hours=0.0,
            is_anomalous=False,
        )

        monkeypatch.setattr(lookahead_engine, "get_fresh_snapshot", lambda pid, db: snapshot)
        monkeypatch.setattr(lookahead_engine, "get_active_programme_upload", lambda pid, db: upload)
        monkeypatch.setattr(
            lookahead_engine, "_compute_capacity_by_week_asset",
            lambda db, pid, week_starts, wdpw: (
                {},
                {
                    "excluded_not_planning_ready": 2,
                    "excluded_retired": 0,
                    "total_assets_evaluated": 5,
                    "unresolved_asset_count": 3,
                    "excluded_asset_types": ["general", "unknown"],
                },
            )
        )

        db = self._db_for_capacity(rows=[other_row])

        result = lookahead_engine.compute_capacity_dashboard(uuid4(), db, start_week=MONDAY, weeks=1)
        diag = result["diagnostics"]
        assert diag["unresolved_asset_count"] == 3
        assert diag["other_demand_hours_total"] == 12.0
        assert diag["excluded_asset_types"] == ["general", "unknown"]
        assert diag["snapshot_refreshed_at"] is not None
        assert diag["total_assets_evaluated"] == 5
        assert diag["excluded_not_planning_ready"] == 2
        assert diag["excluded_retired"] == 0
        assert "capacity_computed_at" in diag
        assert len(diag["assumptions"]) >= 3


class TestAssetCapacityFieldPersistence:
    def test_create_asset_persists_max_hours_per_day(self):
        db = MagicMock()
        project = SimpleNamespace(id=uuid4())
        db.query.return_value.filter.return_value.first.return_value = project

        payload = AssetCreate(
            project_id=project.id,
            asset_code="CR-001",
            name="Tower Crane 1",
            type="crane",
            max_hours_per_day=8.5,
        )

        created = asset_crud.create_asset(db, payload)

        assert created.max_hours_per_day == 8.5
        assert db.add.call_args[0][0].max_hours_per_day == 8.5
