from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sentry_sdk

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..core.database import SessionLocal
from ..models.asset import Asset
from ..models.lookahead import (
    LookaheadRow as LookaheadRowModel,
    LookaheadSnapshot,
    Notification,
    ProjectAlertPolicy,
    SubcontractorAssetTypeAssignment,
)
from ..models.programme import ActivityAssetMapping, ProgrammeActivity, ProgrammeUpload
from ..models.site_project import SiteProject
from ..models.slot_booking import SlotBooking
from ..models.work_profile import ActivityWorkProfile
from ..schemas.enums import BookingStatus
from ..core.constants import (
    ANOMALY_ACTIVITY_DELTA_THRESHOLD,
    ANOMALY_DEMAND_SPIKE_THRESHOLD,
    ANOMALY_MAPPING_CHANGE_THRESHOLD,
    DEMAND_HOURS_PER_DAY,
    DEMAND_LEVEL_HIGH_MAX,
    DEMAND_LEVEL_LOW_MAX,
    DEMAND_LEVEL_MEDIUM_MAX,
    get_active_asset_types,
    get_max_hours_for_type,
)
from .ai_service import normalize_asset_type, suggest_subcontractor_asset_types
from .work_profile_service import build_default_profile, derive_distribution, _uniform_normalized

logger = logging.getLogger(__name__)


@dataclass
class ComputedLookaheadRow:
    asset_type: str
    week_start: date
    demand_hours: float
    booked_hours: float
    demand_level: str
    gap_hours: float
    low_confidence_flag: bool = False
    is_anomalous: bool = False
    anomaly_flags_json: dict[str, bool | float] | None = None


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
    if hours < DEMAND_LEVEL_LOW_MAX:
        return "low"
    if hours < DEMAND_LEVEL_MEDIUM_MAX:
        return "medium"
    if hours < DEMAND_LEVEL_HIGH_MAX:
        return "high"
    return "critical"


def _working_days_in_range(start: date, end: date, work_days_per_week: int) -> int:
    """Count working days (inclusive) in [start, end] for a given work_days_per_week.

    work_days_per_week=5 → Mon–Fri
    work_days_per_week=6 → Mon–Sat
    work_days_per_week=7 → all days
    """
    count = 0
    current = start
    while current <= end:
        if current.weekday() < work_days_per_week:
            count += 1
        current += timedelta(days=1)
    return count


def _iter_working_dates(start: date, end: date, work_days_per_week: int) -> list[date]:
    span_start = min(start, end)
    span_end = max(start, end)
    days: list[date] = []
    current = span_start
    while current <= span_end:
        if current.weekday() < work_days_per_week:
            days.append(current)
        current += timedelta(days=1)
    return days


def _iter_weekly_activity_hours(
    start_date: date,
    end_date: date,
    work_days_per_week: int = 5,
) -> list[tuple[date, float]]:
    """Split an activity span into per-week demand buckets at 8h/working-day.

    work_days_per_week controls which days count:
      5 → Mon–Fri (default, Australian standard)
      6 → Mon–Sat (common in construction)
      7 → all days

    Weeks with zero working days within the span are omitted.
    """
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
            working_days = _working_days_in_range(overlap_start, overlap_end, work_days_per_week)
            if working_days > 0:
                buckets.append((week, float(working_days * DEMAND_HOURS_PER_DAY)))
        week += timedelta(days=7)

    return buckets


def _compute_anomaly_flags(
    previous_rows: list[dict],
    current_rows: list[dict],
    previous_activity_count: int,
    current_activity_count: int,
    previous_mapping_set: set[tuple[str, str]],
    current_mapping_set: set[tuple[str, str]],
) -> dict[str, bool | float]:
    flags: dict[str, bool | float] = {
        # Accurate names (reflect actual thresholds from constants).
        "demand_spike_over_150pct": False,
        "mapping_changes_over_50pct": False,
        "activity_count_delta_over_30pct": False,
        # Legacy aliases kept for backward-compatibility with existing snapshots.
        "demand_spike_over_100pct": False,
        "mapping_changes_over_40pct": False,
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
            if pct_change > ANOMALY_DEMAND_SPIKE_THRESHOLD:
                flags["demand_spike_over_150pct"] = True
                flags["demand_spike_over_100pct"] = True  # legacy alias
                break

    if previous_mapping_set:
        prev_by_activity = {activity_id: asset_type for activity_id, asset_type in previous_mapping_set}
        curr_by_activity = {activity_id: asset_type for activity_id, asset_type in current_mapping_set}
        activity_ids = set(prev_by_activity) | set(curr_by_activity)

        changed = sum(
            1
            for activity_id in activity_ids
            if prev_by_activity.get(activity_id) != curr_by_activity.get(activity_id)
        )

        ratio = changed / max(len(prev_by_activity), 1)
        flags["mapping_change_ratio"] = round(ratio, 4)
        if ratio >= ANOMALY_MAPPING_CHANGE_THRESHOLD:
            flags["mapping_changes_over_50pct"] = True
            flags["mapping_changes_over_40pct"] = True  # legacy alias

    if previous_activity_count > 0:
        delta_ratio = abs(current_activity_count - previous_activity_count) / previous_activity_count
        flags["activity_count_delta_ratio"] = round(delta_ratio, 4)
        if delta_ratio > ANOMALY_ACTIVITY_DELTA_THRESHOLD:
            flags["activity_count_delta_over_30pct"] = True

    return flags


def _compute_demand_by_week_asset(
    db: Session,
    upload_id: uuid.UUID,
) -> tuple[
    dict[tuple[date, str], float],
    set[tuple[str, str]],
    set[tuple[date, str]],
    dict[tuple[date, str], dict[str, bool | float]],
]:
    """Query committed activity-asset mappings and bucket demand hours by (week, asset_type).

    Returns (demand_by_week_asset, current_mapping_set, low_confidence_buckets).

    Stage 1 rules applied here:
      - Activities with pct_complete = 100 are excluded (no remaining work).
      - work_days_per_week from the upload drives the hours-per-day calculation.
      - low_confidence_buckets: (week, asset_type) keys where ALL contributing
        activities have row_confidence = 'low'. Downstream alerts should not
        depend solely on these buckets (plan §20.8).
    """
    mapping_rows = (
        db.query(ActivityAssetMapping, ProgrammeActivity, ProgrammeUpload, ActivityWorkProfile)
        .join(ProgrammeActivity, ProgrammeActivity.id == ActivityAssetMapping.programme_activity_id)
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .outerjoin(ActivityWorkProfile, ActivityWorkProfile.activity_id == ProgrammeActivity.id)
        .filter(
            ProgrammeActivity.programme_upload_id == upload_id,
            or_(
                ActivityAssetMapping.auto_committed.is_(True),
                and_(
                    ActivityAssetMapping.manually_corrected.is_(True),
                    ActivityAssetMapping.source == "manual",
                    ActivityAssetMapping.auto_committed.is_(False),
                ),
            ),
            ActivityAssetMapping.asset_type.isnot(None),
            or_(
                ActivityAssetMapping.confidence.in_(["high", "medium"]),
                ActivityAssetMapping.manually_corrected.is_(True),
            ),
            # Skip fully-complete activities — no remaining work to forecast.
            or_(
                ProgrammeActivity.pct_complete.is_(None),
                ProgrammeActivity.pct_complete < 100,
            ),
        )
        .all()
    )

    demand_by_week_asset: dict[tuple[date, str], float] = defaultdict(float)
    current_mapping_set: set[tuple[str, str]] = set()
    # Track confidence values per bucket to compute low_confidence_flag.
    bucket_confidences: dict[tuple[date, str], set[str]] = defaultdict(set)
    bucket_flags: dict[tuple[date, str], dict[str, bool | float]] = defaultdict(
        lambda: {
            "demand_spike": False,
            "mapping_change": False,
            "activity_delta": False,
            "low_confidence_only": False,
            "missing_work_profile": False,
            "per_day_cap_repaired": False,
        }
    )

    active_types = get_active_asset_types(db)
    for mapping, activity, upload, profile in mapping_rows:
        if mapping.asset_type not in active_types:
            logger.warning("Invalid mapping asset_type=%s for activity=%s; skipping", mapping.asset_type, activity.id)
            continue
        if not activity.start_date or not activity.end_date:
            continue

        raw_wdpw = upload.work_days_per_week
        if raw_wdpw and 1 <= raw_wdpw <= 7:
            work_days = raw_wdpw
        else:
            if raw_wdpw is not None:
                logger.warning(
                    "upload %s has invalid work_days_per_week=%s; defaulting to 5",
                    upload.id, raw_wdpw,
                )
            work_days = 5
        work_dates = _iter_working_dates(activity.start_date, activity.end_date, work_days)
        if not work_dates:
            continue

        max_hours_per_day = get_max_hours_for_type(db, mapping.asset_type)
        low_confidence = bool(activity.row_confidence == "low")
        repaired = False
        missing_profile = profile is None

        if profile is None:
            fallback_total, _, _ = build_default_profile(
                mapping.asset_type,
                len(work_dates),
                max_hours_per_day,
            )
            distribution = derive_distribution(
                _uniform_normalized(len(work_dates)),
                fallback_total,
                max_hours_per_day=max_hours_per_day,
            )
            low_confidence = True
        else:
            profile_distribution = [float(value) for value in list(profile.distribution_json)]
            if len(profile_distribution) != len(work_dates):
                distribution = derive_distribution(
                    _uniform_normalized(len(work_dates)),
                    float(profile.total_hours),
                    max_hours_per_day=max_hours_per_day,
                )
                low_confidence = True
                repaired = True
            elif any(value > max_hours_per_day for value in profile_distribution):
                distribution = derive_distribution(
                    _uniform_normalized(len(work_dates)),
                    float(profile.total_hours),
                    max_hours_per_day=max_hours_per_day,
                )
                low_confidence = True
                repaired = True
            else:
                distribution = profile_distribution
                low_confidence = low_confidence or bool(profile.low_confidence_flag)

        for activity_day, demand_hours in zip(work_dates, distribution, strict=True):
            key = (_week_start(activity_day), mapping.asset_type)
            demand_by_week_asset[key] += float(demand_hours)
            bucket_confidences[key].add("low" if low_confidence else "high")
            bucket_flags[key]["missing_work_profile"] = bool(
                bucket_flags[key]["missing_work_profile"] or missing_profile
            )
            bucket_flags[key]["per_day_cap_repaired"] = bool(
                bucket_flags[key]["per_day_cap_repaired"] or repaired
            )

        current_mapping_set.add((str(activity.id), mapping.asset_type))

    low_confidence_buckets: set[tuple[date, str]] = {
        key
        for key, confidences in bucket_confidences.items()
        if confidences and all(c == "low" for c in confidences)
    }
    for key in low_confidence_buckets:
        bucket_flags[key]["low_confidence_only"] = True

    return demand_by_week_asset, current_mapping_set, low_confidence_buckets, bucket_flags


def _compute_booked_by_week_asset(
    db: Session,
    project_id: uuid.UUID,
    tz: ZoneInfo,
) -> dict[tuple[date, str], float]:
    """Query active bookings for a project and bucket booked hours by (week, asset_type)."""
    booking_rows = (
        db.query(SlotBooking, Asset)
        .join(Asset, Asset.id == SlotBooking.asset_id)
        .filter(
            SlotBooking.project_id == project_id,
            SlotBooking.status.notin_([BookingStatus.CANCELLED, BookingStatus.DENIED]),
        )
        .all()
    )

    booked_by_week_asset: dict[tuple[date, str], float] = defaultdict(float)
    _warned_unknown_types: set[str] = set()
    active_types = get_active_asset_types(db)

    for booking, asset in booking_rows:
        # Prefer canonical_type (Stage 3) → normalize raw type → normalize name.
        raw_asset_type = asset.type or ""
        asset_type = asset.canonical_type
        if asset_type is None:
            asset_type = normalize_asset_type(raw_asset_type)
            if asset_type is None:
                asset_type = normalize_asset_type(asset.name or "")
        if asset_type is None or asset_type not in active_types:
            raw_attempted = raw_asset_type or (asset.name or "")
            if raw_attempted and len(_warned_unknown_types) < 5 and raw_attempted not in _warned_unknown_types:
                logger.warning(
                    "Asset type '%s' not in allowed set; bucketing as 'other' (booking %s). "
                    "Check asset configuration.",
                    raw_attempted,
                    booking.id,
                )
                _warned_unknown_types.add(raw_attempted)
            elif raw_attempted:
                logger.debug("Asset type '%s' not in allowed set; bucketing as 'other' for booking %s", raw_attempted, booking.id)
            asset_type = "other"

        if not booking.booking_date or not booking.start_time or not booking.end_time:
            continue

        start_dt = datetime.combine(booking.booking_date, booking.start_time, tzinfo=tz)
        end_dt = datetime.combine(booking.booking_date, booking.end_time, tzinfo=tz)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        segment_start = start_dt
        while segment_start < end_dt:
            next_day_start = datetime.combine(
                (segment_start + timedelta(days=1)).date(),
                time.min,
                tzinfo=tz,
            )
            segment_end = min(end_dt, next_day_start)
            segment_hours = _hours_between(segment_start.time(), segment_end.time())
            if segment_hours > 0:
                local_day = segment_start.date()
                week = _week_start(local_day)
                booked_by_week_asset[(week, asset_type)] += segment_hours
            segment_start = segment_end

    return booked_by_week_asset


def _ensure_project_alert_policy(db: Session, project_id: uuid.UUID) -> ProjectAlertPolicy:
    policy = (
        db.query(ProjectAlertPolicy)
        .filter(ProjectAlertPolicy.project_id == project_id)
        .one_or_none()
    )
    if policy is None:
        policy = ProjectAlertPolicy(project_id=project_id)
        db.add(policy)
        db.flush()
    return policy


def _severity_score(demand_hours: float, gap_hours: float) -> float:
    gap_ratio = (gap_hours / demand_hours) if demand_hours > 0 else 0.0
    return round((1.0 * gap_hours) + (16.0 * gap_ratio) + (0.25 * demand_hours), 4)


def _upsert_snapshot_with_rows(
    db: Session,
    *,
    project_id: uuid.UUID,
    latest_upload_id: uuid.UUID,
    snapshot_date: date,
    snapshot_payload: dict,
    anomaly_flags: dict,
    row_payloads: list[dict[str, object]],
) -> LookaheadSnapshot:
    snapshot = (
        db.query(LookaheadSnapshot)
        .filter(
            LookaheadSnapshot.project_id == project_id,
            LookaheadSnapshot.snapshot_date == snapshot_date,
        )
        .first()
    )

    if snapshot is None:
        snapshot = LookaheadSnapshot(
            id=uuid.uuid4(),
            project_id=project_id,
            programme_upload_id=latest_upload_id,
            snapshot_date=snapshot_date,
            data=snapshot_payload,
            anomaly_flags=anomaly_flags,
        )
        db.add(snapshot)
        db.flush()
    else:
        snapshot.programme_upload_id = latest_upload_id
        snapshot.data = snapshot_payload
        snapshot.anomaly_flags = anomaly_flags
        db.flush()

    (
        db.query(LookaheadRowModel)
        .filter(LookaheadRowModel.snapshot_id == snapshot.id)
        .delete(synchronize_session=False)
    )
    for row in row_payloads:
        db.add(
            LookaheadRowModel(
                snapshot_id=snapshot.id,
                project_id=project_id,
                week_start=row["week_start"],
                asset_type=row["asset_type"],
                demand_hours=row["demand_hours"],
                booked_hours=row["booked_hours"],
                gap_hours=row["gap_hours"],
                is_anomalous=row["is_anomalous"],
                anomaly_flags_json=row["anomaly_flags_json"],
            )
        )
    db.flush()
    return snapshot


def _sync_thresholded_notifications(
    db: Session,
    *,
    project_id: uuid.UUID,
    snapshot_date: date,
    row_payloads: list[dict[str, object]],
    suppress_external: bool,
) -> None:
    policy = _ensure_project_alert_policy(db, project_id)
    assignments = (
        db.query(SubcontractorAssetTypeAssignment)
        .filter(
            SubcontractorAssetTypeAssignment.project_id == project_id,
            SubcontractorAssetTypeAssignment.is_active.is_(True),
        )
        .all()
    )
    assignments_by_type: dict[str, list[uuid.UUID]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_type[assignment.asset_type].append(assignment.subcontractor_id)

    candidates: list[dict[str, object]] = []
    if not suppress_external and policy.mode != "observe_only" and bool(policy.external_enabled):
        for row in row_payloads:
            demand_hours = float(row["demand_hours"])
            gap_hours = float(row["gap_hours"])
            week_start = row["week_start"]
            asset_type = str(row["asset_type"])
            flags = row["anomaly_flags_json"] or {}
            gap_ratio = (gap_hours / demand_hours) if demand_hours > 0 else 0.0

            if asset_type == "other":
                continue
            if gap_hours <= 0:
                continue
            if demand_hours < float(policy.min_demand_hours):
                continue
            if not (gap_hours >= float(policy.min_gap_hours) or gap_ratio >= float(policy.min_gap_ratio)):
                continue
            if week_start < snapshot_date + timedelta(weeks=int(policy.min_lead_weeks)):
                continue
            if bool(row["is_anomalous"]):
                continue
            if bool(flags.get("low_confidence_only")):
                continue

            for subcontractor_id in assignments_by_type.get(asset_type, []):
                candidates.append(
                    {
                        "key": (project_id, subcontractor_id, asset_type, week_start),
                        "sub_id": subcontractor_id,
                        "asset_type": asset_type,
                        "week_start": week_start,
                        "severity_score": _severity_score(demand_hours, gap_hours),
                    }
                )

    candidates.sort(
        key=lambda row: (
            -float(row["severity_score"]),
            str(row["sub_id"]),
            str(row["asset_type"]),
            row["week_start"].isoformat(),
        )
    )

    surviving: list[dict[str, object]] = []
    per_sub_counts: dict[uuid.UUID, int] = defaultdict(int)
    project_count = 0
    for candidate in candidates:
        if per_sub_counts[candidate["sub_id"]] >= int(policy.max_alerts_per_subcontractor_per_week):
            continue
        if project_count >= int(policy.max_alerts_per_project_per_week):
            continue
        surviving.append(candidate)
        per_sub_counts[candidate["sub_id"]] += 1
        project_count += 1

    existing = (
        db.query(Notification)
        .filter(
            Notification.project_id == project_id,
            Notification.trigger_type == "lookahead",
            Notification.status.in_(["pending", "sent"]),
        )
        .all()
    )
    existing_by_key = {
        (row.project_id, row.sub_id, row.asset_type, row.week_start): row
        for row in existing
    }
    surviving_keys = {row["key"] for row in surviving}

    for row in surviving:
        existing_row = existing_by_key.get(row["key"])
        if existing_row is not None:
            existing_row.severity_score = row["severity_score"]
            existing_row.week_start = row["week_start"]
            continue
        db.add(
            Notification(
                sub_id=row["sub_id"],
                project_id=project_id,
                activity_id=None,
                asset_type=row["asset_type"],
                week_start=row["week_start"],
                trigger_type="lookahead",
                status="pending",
                severity_score=row["severity_score"],
            )
        )

    for row in existing:
        key = (row.project_id, row.sub_id, row.asset_type, row.week_start)
        if key not in surviving_keys and row.status in ("pending", "sent"):
            row.status = "cancelled"


def _upsert_snapshot(
    db: Session,
    project_id: uuid.UUID,
    latest_upload_id: uuid.UUID,
    snapshot_date: date,
    snapshot_payload: dict,
    anomaly_flags: dict,
) -> LookaheadSnapshot:
    """Upsert a LookaheadSnapshot for (project_id, snapshot_date), handling concurrent races."""
    snapshot = (
        db.query(LookaheadSnapshot)
        .filter(
            LookaheadSnapshot.project_id == project_id,
            LookaheadSnapshot.snapshot_date == snapshot_date,
        )
        .first()
    )

    if snapshot:
        snapshot.programme_upload_id = latest_upload_id
        snapshot.data = snapshot_payload
        snapshot.anomaly_flags = anomaly_flags
    else:
        snapshot = LookaheadSnapshot(
            id=uuid.uuid4(),
            project_id=project_id,
            programme_upload_id=latest_upload_id,
            snapshot_date=snapshot_date,
            data=snapshot_payload,
            anomaly_flags=anomaly_flags,
        )
        db.add(snapshot)

    try:
        db.commit()
    except IntegrityError:
        # Concurrent runs can race on (project_id, snapshot_date); reload and update.
        db.rollback()
        snapshot = (
            db.query(LookaheadSnapshot)
            .filter(
                LookaheadSnapshot.project_id == project_id,
                LookaheadSnapshot.snapshot_date == snapshot_date,
            )
            .first()
        )
        if snapshot is None:
            raise
        snapshot.programme_upload_id = latest_upload_id
        snapshot.data = snapshot_payload
        snapshot.anomaly_flags = anomaly_flags
        db.commit()

    db.refresh(snapshot)
    return snapshot


def calculate_lookahead_for_project(project_id: uuid.UUID, db: Session) -> LookaheadSnapshot | None:
    project = db.query(SiteProject).filter(SiteProject.id == project_id).first()
    if not project:
        return None

    latest_upload = (
        db.query(ProgrammeUpload)
        .filter(
            ProgrammeUpload.project_id == project_id,
            ProgrammeUpload.status.in_(["committed", "degraded"]),
        )
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )
    if not latest_upload:
        return None

    timezone_name = project.timezone or "Australia/Adelaide"
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid project timezone '%s'; using Australia/Adelaide", timezone_name)
        timezone_name = "Australia/Adelaide"
        tz = ZoneInfo("Australia/Adelaide")

    demand_by_week_asset, current_mapping_set, low_confidence_buckets, bucket_flags = _compute_demand_by_week_asset(db, latest_upload.id)
    booked_by_week_asset = _compute_booked_by_week_asset(db, project_id, tz)

    all_keys = sorted(set(demand_by_week_asset.keys()) | set(booked_by_week_asset.keys()))
    rows: list[ComputedLookaheadRow] = [
        ComputedLookaheadRow(
            asset_type=asset_type,
            week_start=week,
            demand_hours=round(demand_by_week_asset.get((week, asset_type), 0.0), 2),
            booked_hours=round(booked_by_week_asset.get((week, asset_type), 0.0), 2),
            demand_level=_demand_level(demand_by_week_asset.get((week, asset_type), 0.0)),
            gap_hours=round(max(demand_by_week_asset.get((week, asset_type), 0.0) - booked_by_week_asset.get((week, asset_type), 0.0), 0.0), 2),
            low_confidence_flag=(week, asset_type) in low_confidence_buckets,
            anomaly_flags_json=dict(bucket_flags.get((week, asset_type), {})),
        )
        for week, asset_type in all_keys
    ]

    previous_snapshot = (
        db.query(LookaheadSnapshot)
        .filter(LookaheadSnapshot.project_id == project_id)
        .order_by(LookaheadSnapshot.snapshot_date.desc(), LookaheadSnapshot.created_at.desc())
        .first()
    )

    previous_data = (previous_snapshot.data or {}) if previous_snapshot else {}
    previous_rows = previous_data.get("rows", [])
    previous_activity_count = int(previous_data.get("activity_count", 0))
    previous_mapping_set = {
        (entry.get("activity_id", ""), entry.get("asset_type", ""))
        for entry in previous_data.get("mapping_set", [])
    }

    activity_count = (
        db.query(ProgrammeActivity)
        .filter(ProgrammeActivity.programme_upload_id == latest_upload.id)
        .count()
    )
    anomaly_flags = _compute_anomaly_flags(
        previous_rows=previous_rows,
        current_rows=[
            {
                "asset_type": r.asset_type,
                "week_start": r.week_start.isoformat(),
                "demand_hours": r.demand_hours,
            }
            for r in rows
        ],
        previous_activity_count=previous_activity_count,
        current_activity_count=activity_count,
        previous_mapping_set=previous_mapping_set,
        current_mapping_set=current_mapping_set,
    )

    previous_by_key = {
        (row["asset_type"], row["week_start"]): float(row["demand_hours"])
        for row in previous_rows
    }
    row_payloads: list[dict[str, object]] = []
    snapshot_rows: list[dict[str, object]] = []
    for row in rows:
        key = (row.asset_type, row.week_start.isoformat())
        prev_demand = previous_by_key.get(key)
        row_flags = dict(row.anomaly_flags_json or {})
        if prev_demand is not None and prev_demand > 0:
            pct_change = abs(row.demand_hours - prev_demand) / prev_demand
            if pct_change > ANOMALY_DEMAND_SPIKE_THRESHOLD:
                row_flags["demand_spike"] = True
        if anomaly_flags.get("mapping_changes_over_50pct"):
            row_flags["mapping_change"] = True
        if anomaly_flags.get("activity_count_delta_over_30pct"):
            row_flags["activity_delta"] = True
        if row.asset_type == "other" and row.demand_hours >= DEMAND_LEVEL_LOW_MAX:
            anomaly_flags["other_asset_threshold_exceeded"] = True

        row.is_anomalous = bool(
            row_flags.get("demand_spike")
            or row_flags.get("mapping_change")
            or row_flags.get("activity_delta")
            or row_flags.get("per_day_cap_repaired")
        )
        row.anomaly_flags_json = row_flags

        row_payloads.append(
            {
                "asset_type": row.asset_type,
                "week_start": row.week_start,
                "demand_hours": row.demand_hours,
                "booked_hours": row.booked_hours,
                "gap_hours": row.gap_hours,
                "is_anomalous": row.is_anomalous,
                "anomaly_flags_json": row.anomaly_flags_json,
            }
        )
        snapshot_rows.append(
            {
                "asset_type": row.asset_type,
                "week_start": row.week_start.isoformat(),
                "demand_hours": row.demand_hours,
                "booked_hours": row.booked_hours,
                "demand_level": row.demand_level,
                "gap_hours": row.gap_hours,
                "low_confidence_flag": row.low_confidence_flag,
                "is_anomalous": row.is_anomalous,
                "anomaly_flags_json": row.anomaly_flags_json,
            }
        )

    snapshot_date = datetime.now(tz).date()
    snapshot_payload = {
        "timezone": timezone_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "activity_count": activity_count,
        "rows": snapshot_rows,
        "mapping_set": [
            {"activity_id": activity_id, "asset_type": asset_type}
            for activity_id, asset_type in sorted(current_mapping_set)
        ],
    }
    snapshot = _upsert_snapshot_with_rows(
        db,
        project_id=project_id,
        latest_upload_id=latest_upload.id,
        snapshot_date=snapshot_date,
        snapshot_payload=snapshot_payload,
        anomaly_flags=anomaly_flags,
        row_payloads=row_payloads,
    )
    suppress_external = (
        latest_upload.status == "degraded"
        or getattr(latest_upload, "processing_outcome", None) == "completed_with_warnings"
    )
    _sync_thresholded_notifications(
        db,
        project_id=project_id,
        snapshot_date=snapshot_date,
        row_payloads=row_payloads,
        suppress_external=suppress_external,
    )
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
        .outerjoin(ProgrammeActivity, ProgrammeActivity.id == Notification.activity_id)
        .outerjoin(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .filter(
            Notification.sub_id == sub_id,
            or_(
                ProgrammeUpload.project_id == project_id,
                and_(
                    Notification.activity_id.is_(None),
                    Notification.project_id == project_id,
                ),
            ),
        )
        .order_by(Notification.created_at.desc())
        .all()
    )


def get_sub_asset_suggestions_for_project(
    project_id: uuid.UUID,
    db: Session,
) -> list[dict]:
    """
    Return per-subcontractor asset demand suggestions for a project.

    For each subcontractor assigned to the project, uses their trade_specialty
    to predict which asset types they are likely to need. Cross-references with
    the latest lookahead snapshot to show actual projected demand hours by week.

    Returns a list of dicts:
    [
      {
        "subcontractor_id": "...",
        "company_name": "...",
        "trade_specialty": "...",
        "suggested_asset_types": ["crane", "hoist"],
        "demand_rows": [
          {
            "asset_type": "crane",
            "week_start": "2026-04-07",
            "demand_hours": 40.0,
            "booked_hours": 16.0,
            "gap_hours": 24.0,
            "demand_level": "high"
          }
        ]
      }
    ]
    """
    project = (
        db.query(SiteProject)
        .options(joinedload(SiteProject.subcontractors))
        .filter(SiteProject.id == project_id)
        .first()
    )
    if not project or not project.subcontractors:
        return []

    snapshot = get_latest_snapshot(project_id, db)
    demand_rows: list[dict] = (snapshot.data or {}).get("rows", []) if snapshot else []

    sub_dicts = [
        {"id": str(sub.id), "trade_specialty": sub.trade_specialty or ""}
        for sub in project.subcontractors
    ]
    suggestions = suggest_subcontractor_asset_types(sub_dicts)

    result: list[dict] = []
    sub_map = {str(sub.id): sub for sub in project.subcontractors}

    for suggestion in suggestions:
        sub = sub_map.get(suggestion.subcontractor_id)
        if not sub:
            continue

        matching_demand = [
            row for row in demand_rows
            if row.get("asset_type") in suggestion.suggested_asset_types
        ]

        result.append({
            "subcontractor_id": suggestion.subcontractor_id,
            "company_name": getattr(sub, "company_name", None) or "",
            "trade_specialty": suggestion.trade_specialty,
            "suggested_asset_types": suggestion.suggested_asset_types,
            "demand_rows": matching_demand,
        })

    return result


def nightly_lookahead_job() -> None:
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("task", "nightly_lookahead_job")
        db = SessionLocal()
        try:
            project_ids = [
                row[0]
                for row in (
                    db.query(ProgrammeUpload.project_id)
                    .filter(ProgrammeUpload.status.in_(["committed", "degraded"]))
                    .distinct()
                    .all()
                )
            ]

            for project_id in project_ids:
                try:
                    calculate_lookahead_for_project(project_id=project_id, db=db)
                except Exception as exc:
                    with sentry_sdk.new_scope() as project_scope:
                        project_scope.set_tag("project_id", str(project_id))
                        sentry_sdk.capture_exception(exc)
                    logger.exception("Nightly lookahead failed for project %s", project_id)
                    db.rollback()
        finally:
            db.close()
