from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import logging
from typing import Iterable
import uuid

from sqlalchemy.orm import Session

from ..models.site_project import ProjectNonWorkingDay, SiteProject

logger = logging.getLogger(__name__)

AU_HOLIDAY_REGION_CODES = frozenset({"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"})
DEFAULT_HOLIDAY_COUNTRY_CODE = "AU"
DEFAULT_HOLIDAY_REGION_CODE = "SA"


@dataclass(frozen=True)
class CalendarDay:
    calendar_date: date
    label: str
    kind: str
    source: str
    id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime | None = None


def normalize_holiday_region_code(value: str | None) -> str:
    token = str(value or "").strip().upper()
    return token if token in AU_HOLIDAY_REGION_CODES else DEFAULT_HOLIDAY_REGION_CODE


def infer_au_holiday_region_from_location(location: str | None) -> tuple[str, str]:
    text = str(location or "").strip().lower()
    if not text:
        return DEFAULT_HOLIDAY_REGION_CODE, "default"

    region_keywords = (
        ("SA", ("adelaide", "south australia", " sa", "sa ")),
        ("NSW", ("sydney", "new south wales", " nsw", "nsw ")),
        ("VIC", ("melbourne", "victoria", " vic", "vic ")),
        ("QLD", ("brisbane", "queensland", " qld", "qld ")),
        ("WA", ("perth", "western australia", " wa", "wa ")),
        ("TAS", ("hobart", "tasmania", " tas", "tas ")),
        ("NT", ("darwin", "northern territory", " nt", "nt ")),
        ("ACT", ("canberra", "australian capital territory", " act", "act ")),
    )
    padded = f" {text} "
    for region, keywords in region_keywords:
        if any(keyword in padded for keyword in keywords):
            return region, "location"
    return DEFAULT_HOLIDAY_REGION_CODE, "default"


def resolve_project_holiday_region(project: SiteProject) -> tuple[str, str, str]:
    country = str(getattr(project, "holiday_country_code", None) or DEFAULT_HOLIDAY_COUNTRY_CODE).strip().upper()
    if country != DEFAULT_HOLIDAY_COUNTRY_CODE:
        country = DEFAULT_HOLIDAY_COUNTRY_CODE

    source = str(getattr(project, "holiday_region_source", None) or "").strip().lower()
    stored_region = getattr(project, "holiday_region_code", None)
    if source == "manual" and stored_region:
        return country, normalize_holiday_region_code(stored_region), "manual"

    inferred_region, inferred_source = infer_au_holiday_region_from_location(getattr(project, "location", None))
    return country, inferred_region, inferred_source


def apply_project_holiday_region_defaults(project: SiteProject) -> None:
    source = str(getattr(project, "holiday_region_source", None) or "").strip().lower()
    if source == "manual":
        project.holiday_country_code = DEFAULT_HOLIDAY_COUNTRY_CODE
        project.holiday_region_code = normalize_holiday_region_code(project.holiday_region_code)
        project.holiday_region_source = "manual"
        return

    country, region, resolved_source = resolve_project_holiday_region(project)
    project.holiday_country_code = country
    project.holiday_region_code = region
    project.holiday_region_source = resolved_source


def _iter_years(date_from: date, date_to: date) -> Iterable[int]:
    for year in range(date_from.year, date_to.year + 1):
        yield year


def get_regional_public_holidays(
    *,
    country_code: str,
    region_code: str,
    date_from: date,
    date_to: date,
) -> list[CalendarDay]:
    if country_code != "AU":
        return []

    region = normalize_holiday_region_code(region_code)
    try:
        import holidays as holidays_lib
    except ImportError:
        logger.warning("holidays package is not installed; regional holidays unavailable")
        return []

    try:
        calendar = holidays_lib.country_holidays("AU", subdiv=region, years=list(_iter_years(date_from, date_to)))
    except Exception as exc:
        logger.warning("Could not load AU holiday calendar for region %s: %s", region, exc)
        return []

    days: list[CalendarDay] = []
    for holiday_date, label in calendar.items():
        if date_from <= holiday_date <= date_to:
            days.append(
                CalendarDay(
                    calendar_date=holiday_date,
                    label=str(label),
                    kind="holiday",
                    source="regional_public_holiday",
                )
            )
    return sorted(days, key=lambda item: item.calendar_date)


def get_project_calendar_days(
    db: Session,
    project: SiteProject,
    *,
    date_from: date,
    date_to: date,
    include_regional: bool = True,
) -> list[CalendarDay]:
    manual_days = (
        db.query(ProjectNonWorkingDay)
        .filter(
            ProjectNonWorkingDay.project_id == project.id,
            ProjectNonWorkingDay.calendar_date >= date_from,
            ProjectNonWorkingDay.calendar_date <= date_to,
        )
        .all()
    )
    combined = {
        day.calendar_date: CalendarDay(
            calendar_date=day.calendar_date,
            label=day.label,
            kind=day.kind,
            source="manual",
            id=day.id,
            project_id=day.project_id,
            created_by=day.created_by,
            created_at=day.created_at,
        )
        for day in manual_days
    }

    if include_regional:
        country, region, _ = resolve_project_holiday_region(project)
        for day in get_regional_public_holidays(
            country_code=country,
            region_code=region,
            date_from=date_from,
            date_to=date_to,
        ):
            combined.setdefault(day.calendar_date, day)

    return sorted(combined.values(), key=lambda item: item.calendar_date)
