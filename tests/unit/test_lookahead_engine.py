from datetime import date
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

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
    lookup_query.all.side_effect = RuntimeError("boom")

    lookup_db = MagicMock()
    lookup_db.query.return_value = lookup_query

    caller_db = MagicMock()
    monkeypatch.setattr(lookahead_engine, "SessionLocal", _SessionContext(lookup_db))

    result = lookahead_engine._load_max_hours_by_type(caller_db, {"forklift"})

    assert result == {"forklift": DEFAULT_MAX_HOURS_PER_DAY["forklift"]}
    caller_db.rollback.assert_not_called()
