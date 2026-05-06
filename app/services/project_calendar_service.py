from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Iterable
import uuid

from sqlalchemy.orm import Session

from ..models.site_project import ProjectNonWorkingDay, SiteProject

logger = logging.getLogger(__name__)

AU_HOLIDAY_REGION_CODES = frozenset({"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"})
DEFAULT_HOLIDAY_COUNTRY_CODE = "AU"
DEFAULT_HOLIDAY_REGION_CODE = "SA"
DEFAULT_RDO_ANCHOR = date(2026, 1, 26)
RDO_ANCHORS = {
    # Queensland's published 2026 construction RDO calendar starts one cycle earlier.
    "QLD": date(2026, 1, 19),
}


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


def _next_working_day(candidate: date, blocked_dates: set[date]) -> date:
    next_date = candidate
    while next_date.weekday() >= 5 or next_date in blocked_dates:
        next_date += timedelta(days=1)
    return next_date


def get_regional_rdo_days(
    *,
    country_code: str,
    region_code: str,
    date_from: date,
    date_to: date,
    public_holiday_dates: set[date] | None = None,
) -> list[CalendarDay]:
    """Return advisory construction RDO dates for the resolved AU region."""

    if country_code != "AU":
        return []

    region = normalize_holiday_region_code(region_code)
    blocked_dates = public_holiday_dates or set()
    anchor = RDO_ANCHORS.get(region, DEFAULT_RDO_ANCHOR)
    cycle = timedelta(days=28)

    current = anchor
    while current + cycle < date_from:
        current += cycle
    while current - cycle >= date_from:
        current -= cycle

    days: list[CalendarDay] = []
    while current <= date_to:
        adjusted = _next_working_day(current, blocked_dates)
        if date_from <= adjusted <= date_to:
            days.append(
                CalendarDay(
                    calendar_date=adjusted,
                    label="Rostered Day Off (RDO)",
                    kind="rdo",
                    source="regional_rdo",
                )
            )
        current += cycle

    return sorted(days, key=lambda item: item.calendar_date)


def get_project_calendar_days(
    db: Session,
    project: SiteProject,
    *,
    date_from: date,
    date_to: date,
    include_regional: bool = True,
    include_rdo: bool = True,
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

    public_holiday_days: list[CalendarDay] = []
    if include_regional:
        country, region, _ = resolve_project_holiday_region(project)
        public_holiday_days = get_regional_public_holidays(
            country_code=country,
            region_code=region,
            date_from=date_from,
            date_to=date_to,
        )
        for day in public_holiday_days:
            combined.setdefault(day.calendar_date, day)

        if include_rdo:
            public_holiday_dates = {day.calendar_date for day in public_holiday_days}
            for day in get_regional_rdo_days(
                country_code=country,
                region_code=region,
                date_from=date_from,
                date_to=date_to,
                public_holiday_dates=public_holiday_dates,
            ):
                combined.setdefault(day.calendar_date, day)

    return sorted(combined.values(), key=lambda item: item.calendar_date)
