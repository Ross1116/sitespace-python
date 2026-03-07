from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..core.database import SessionLocal
from ..models.asset import Asset
from ..models.lookahead import LookaheadSnapshot, Notification
from ..models.programme import ActivityAssetMapping, ProgrammeActivity, ProgrammeUpload
from ..models.site_project import SiteProject
from ..models.slot_booking import SlotBooking
from .ai_service import ALLOWED_ASSET_TYPES

logger = logging.getLogger(__name__)


@dataclass
class LookaheadRow:
    asset_type: str
    week_start: date
    demand_hours: float
    booked_hours: float
    demand_level: str
    gap_hours: float


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _hours_between(start: time, end: time) -> float:
    start_dt = datetime.combine(date.min, start)
    end_dt = datetime.combine(date.min, end)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    hours = (end_dt - start_dt).total_seconds() / 3600.0
    return max(hours, 0.0)


def _demand_level(hours: float) -> str:
    if hours < 8:
        return "low"
    if hours < 20:
        return "medium"
    if hours < 40:
        return "high"
    return "critical"


def _iter_weekly_activity_hours(start_date: date, end_date: date) -> list[tuple[date, float]]:
    """Split an activity span into per-week demand buckets at 8h/day."""
    span_start = min(start_date, end_date)
    span_end = max(start_date, end_date)

    buckets: list[tuple[date, float]] = []
    week = _week_start(span_start)
    last_week = _week_start(span_end)

    while week <= last_week:
        week_end = week + timedelta(days=6)
        overlap_start = max(span_start, week)
        overlap_end = min(span_end, week_end)
        if overlap_end >= overlap_start:
            overlap_days = (overlap_end - overlap_start).days + 1
            buckets.append((week, float(overlap_days * 8)))
        week += timedelta(days=7)

    return buckets


def _compute_anomaly_flags(
    previous_rows: list[dict],
    current_rows: list[dict],
    previous_activity_count: int,
    current_activity_count: int,
    previous_mapping_set: set[tuple[str, str]],
    current_mapping_set: set[tuple[str, str]],
) -> dict:
    flags: dict[str, bool | float] = {
        "demand_spike_over_100pct": False,
        "mapping_changes_over_40pct": False,
        "activity_count_delta_over_30pct": False,
    }

    prev_by_key = {
        (row["asset_type"], row["week_start"]): float(row["demand_hours"])
        for row in previous_rows
    }
    for row in current_rows:
        key = (row["asset_type"], row["week_start"])
        prev = prev_by_key.get(key)
        curr = float(row["demand_hours"])
        if prev is not None and prev > 0:
            pct_change = abs(curr - prev) / prev
            if pct_change > 1.0:
                flags["demand_spike_over_100pct"] = True
                break

    if previous_mapping_set:
        changed = len(previous_mapping_set.symmetric_difference(current_mapping_set))
        ratio = changed / max(len(previous_mapping_set), 1)
        flags["mapping_change_ratio"] = round(ratio, 4)
        if ratio >= 0.4:
            flags["mapping_changes_over_40pct"] = True

    if previous_activity_count > 0:
        delta_ratio = abs(current_activity_count - previous_activity_count) / previous_activity_count
        flags["activity_count_delta_ratio"] = round(delta_ratio, 4)
        if delta_ratio > 0.3:
            flags["activity_count_delta_over_30pct"] = True

    return flags


def calculate_lookahead_for_project(project_id: uuid.UUID, db: Session) -> LookaheadSnapshot | None:
    project = db.query(SiteProject).filter(SiteProject.id == project_id).first()
    if not project:
        return None

    latest_upload = (
        db.query(ProgrammeUpload)
        .filter(ProgrammeUpload.project_id == project_id, ProgrammeUpload.status == "committed")
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )
    if not latest_upload:
        return None

    timezone_name = project.timezone or "Australia/Adelaide"
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        logger.warning("Invalid project timezone '%s'; using Australia/Adelaide", timezone_name)
        tz = ZoneInfo("Australia/Adelaide")

    mapping_rows = (
        db.query(ActivityAssetMapping, ProgrammeActivity)
        .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
        .filter(
            ProgrammeActivity.programme_upload_id == latest_upload.id,
            or_(
                ActivityAssetMapping.auto_committed.is_(True),
                and_(
                    ActivityAssetMapping.manually_corrected.is_(True),
                    ActivityAssetMapping.source == "manual",
                    ActivityAssetMapping.auto_committed.is_(False),
                ),
            ),
            ActivityAssetMapping.asset_type.isnot(None),
            ActivityAssetMapping.confidence.in_(["high", "medium"]),
        )
        .all()
    )

    demand_by_week_asset: dict[tuple[date, str], float] = defaultdict(float)
    current_mapping_set: set[tuple[str, str]] = set()
    activity_ids = []

    for mapping, activity in mapping_rows:
        if mapping.asset_type not in ALLOWED_ASSET_TYPES:
            logger.warning("Invalid mapping asset_type=%s for activity=%s; skipping", mapping.asset_type, activity.id)
            continue
        if not activity.start_date or not activity.end_date:
            continue

        for week, demand_hours in _iter_weekly_activity_hours(activity.start_date, activity.end_date):
            demand_by_week_asset[(week, mapping.asset_type)] += demand_hours

        current_mapping_set.add((str(activity.id), mapping.asset_type))
        activity_ids.append(activity.id)

    booking_rows = (
        db.query(SlotBooking, Asset)
        .join(Asset, Asset.id == SlotBooking.asset_id)
        .filter(SlotBooking.project_id == project_id)
        .all()
    )

    booked_by_week_asset: dict[tuple[date, str], float] = defaultdict(float)
    for booking, asset in booking_rows:
        asset_type = (asset.type or "").strip().lower()
        if asset_type not in ALLOWED_ASSET_TYPES:
            logger.warning("Asset type '%s' is not in allowed set; skipping booking %s", asset.type, booking.id)
            continue
        if not booking.booking_date or not booking.start_time or not booking.end_time:
            continue

        local_day = datetime.combine(booking.booking_date, time.min, tzinfo=tz).date()
        week = _week_start(local_day)
        booked_by_week_asset[(week, asset_type)] += _hours_between(booking.start_time, booking.end_time)

    all_keys = sorted(set(demand_by_week_asset.keys()) | set(booked_by_week_asset.keys()))
    rows: list[LookaheadRow] = []

    for week, asset_type in all_keys:
        demand = round(demand_by_week_asset.get((week, asset_type), 0.0), 2)
        booked = round(booked_by_week_asset.get((week, asset_type), 0.0), 2)
        gap = round(max(demand - booked, 0.0), 2)
        rows.append(
            LookaheadRow(
                asset_type=asset_type,
                week_start=week,
                demand_hours=demand,
                booked_hours=booked,
                demand_level=_demand_level(demand),
                gap_hours=gap,
            )
        )

    previous_snapshot = (
        db.query(LookaheadSnapshot)
        .filter(LookaheadSnapshot.project_id == project_id)
        .order_by(LookaheadSnapshot.snapshot_date.desc(), LookaheadSnapshot.created_at.desc())
        .first()
    )

    previous_rows = previous_snapshot.data.get("rows", []) if previous_snapshot and previous_snapshot.data else []
    previous_activity_count = int(previous_snapshot.data.get("activity_count", 0)) if previous_snapshot and previous_snapshot.data else 0
    previous_mapping_set = {
        (entry.get("activity_id", ""), entry.get("asset_type", ""))
        for entry in (previous_snapshot.data.get("mapping_set", []) if previous_snapshot and previous_snapshot.data else [])
    }

    current_rows_payload = [
        {
            "asset_type": r.asset_type,
            "week_start": r.week_start.isoformat(),
            "demand_hours": r.demand_hours,
            "booked_hours": r.booked_hours,
            "demand_level": r.demand_level,
            "gap_hours": r.gap_hours,
        }
        for r in rows
    ]

    activity_count = (
        db.query(ProgrammeActivity)
        .filter(ProgrammeActivity.programme_upload_id == latest_upload.id)
        .count()
    )

    anomaly_flags = _compute_anomaly_flags(
        previous_rows=previous_rows,
        current_rows=current_rows_payload,
        previous_activity_count=previous_activity_count,
        current_activity_count=activity_count,
        previous_mapping_set=previous_mapping_set,
        current_mapping_set=current_mapping_set,
    )

    snapshot = LookaheadSnapshot(
        id=uuid.uuid4(),
        project_id=project_id,
        programme_upload_id=latest_upload.id,
        snapshot_date=datetime.now(tz).date(),
        data={
            "timezone": timezone_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "activity_count": activity_count,
            "rows": current_rows_payload,
            "mapping_set": [
                {"activity_id": activity_id, "asset_type": asset_type}
                for activity_id, asset_type in sorted(current_mapping_set)
            ],
        },
        anomaly_flags=anomaly_flags,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def get_latest_snapshot(project_id: uuid.UUID, db: Session) -> LookaheadSnapshot | None:
    return (
        db.query(LookaheadSnapshot)
        .filter(LookaheadSnapshot.project_id == project_id)
        .order_by(LookaheadSnapshot.snapshot_date.desc(), LookaheadSnapshot.created_at.desc())
        .first()
    )


def get_snapshot_history(project_id: uuid.UUID, db: Session) -> list[LookaheadSnapshot]:
    return (
        db.query(LookaheadSnapshot)
        .filter(LookaheadSnapshot.project_id == project_id)
        .order_by(LookaheadSnapshot.snapshot_date.desc(), LookaheadSnapshot.created_at.desc())
        .all()
    )


def get_sub_notifications(project_id: uuid.UUID, sub_id: uuid.UUID, db: Session) -> list[Notification]:
    return (
        db.query(Notification)
        .join(ProgrammeActivity, ProgrammeActivity.id == Notification.activity_id)
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .filter(
            Notification.sub_id == sub_id,
            ProgrammeUpload.project_id == project_id,
        )
        .order_by(Notification.created_at.desc())
        .all()
    )


def nightly_lookahead_job() -> None:
    db = SessionLocal()
    try:
        project_ids = [
            row[0]
            for row in (
                db.query(ProgrammeUpload.project_id)
                .filter(ProgrammeUpload.status == "committed")
                .distinct()
                .all()
            )
        ]

        for project_id in project_ids:
            try:
                calculate_lookahead_for_project(project_id=project_id, db=db)
            except Exception:
                logger.exception("Nightly lookahead failed for project %s", project_id)
                db.rollback()
    finally:
        db.close()
