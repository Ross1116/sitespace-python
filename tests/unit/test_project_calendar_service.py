from types import SimpleNamespace
from datetime import date

from app.services.project_calendar_service import (
    get_regional_rdo_days,
    infer_au_holiday_region_from_location,
    resolve_project_holiday_region,
)


def test_infer_au_holiday_region_defaults_to_south_australia():
    assert infer_au_holiday_region_from_location(None) == ("SA", "default")
    assert infer_au_holiday_region_from_location("Unknown site") == ("SA", "default")


def test_infer_au_holiday_region_from_location_keywords():
    assert infer_au_holiday_region_from_location("Adelaide CBD") == ("SA", "location")
    assert infer_au_holiday_region_from_location("Sydney NSW") == ("NSW", "location")
    assert infer_au_holiday_region_from_location("Melbourne VIC") == ("VIC", "location")


def test_manual_holiday_region_overrides_location():
    project = SimpleNamespace(
        location="Adelaide",
        holiday_country_code="AU",
        holiday_region_code="NSW",
        holiday_region_source="manual",
    )

    assert resolve_project_holiday_region(project) == ("AU", "NSW", "manual")


def test_regional_rdo_days_are_advisory_and_location_based():
    days = get_regional_rdo_days(
        country_code="AU",
        region_code="SA",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
        public_holiday_dates={date(2026, 1, 26)},
    )

    assert [(day.calendar_date, day.kind, day.source) for day in days] == [
        (date(2026, 1, 27), "rdo", "regional_rdo")
    ]


def test_queensland_rdo_calendar_uses_region_specific_anchor():
    days = get_regional_rdo_days(
        country_code="AU",
        region_code="QLD",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 2, 28),
        public_holiday_dates=set(),
    )

    assert [day.calendar_date for day in days] == [
        date(2026, 1, 19),
        date(2026, 2, 16),
    ]
