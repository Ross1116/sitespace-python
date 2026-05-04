from app.services.ai_service import ActivityItem
from app.services.process_programme import _infer_work_days_per_week, _parse_duration_days


def _item(start: str, finish: str, duration: int) -> ActivityItem:
    return ActivityItem(
        id=f"{start}-{finish}",
        name="Task",
        start=start,
        finish=finish,
        parent_id=None,
        is_summary=False,
        level_name=None,
        zone_name=None,
        duration_days=duration,
    )


def test_infer_work_days_per_week_defaults_to_five_when_weak():
    assert _infer_work_days_per_week([]) == 5
    assert _infer_work_days_per_week([_item("2026-03-02", "2026-03-07", 6)]) == 5


def test_infer_work_days_per_week_detects_five_day_programme():
    items = [
        _item("2026-03-02", "2026-03-06", 5),
        _item("2026-03-09", "2026-03-13", 5),
        _item("2026-03-16", "2026-03-20", 5),
    ]

    assert _infer_work_days_per_week(items) == 5


def test_infer_work_days_per_week_detects_six_day_programme():
    items = [
        _item("2026-03-02", "2026-03-08", 6),
        _item("2026-03-09", "2026-03-15", 6),
        _item("2026-03-16", "2026-03-22", 6),
    ]

    assert _infer_work_days_per_week(items) == 6


def test_infer_work_days_per_week_detects_seven_day_programme():
    items = [
        _item("2026-03-02", "2026-03-08", 7),
        _item("2026-03-09", "2026-03-15", 7),
        _item("2026-03-16", "2026-03-22", 7),
    ]

    assert _infer_work_days_per_week(items) == 7


def test_parse_duration_days_accepts_common_duration_formats():
    assert _parse_duration_days("6 days") == 6
    assert _parse_duration_days("6d") == 6
    assert _parse_duration_days(6.0) == 6
    assert _parse_duration_days("") is None
