from types import SimpleNamespace

from app.services.project_calendar_service import (
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
