from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.item_identity import ItemClassification
from ..models.job_queue import ProgrammeUploadJob, ScheduledJobRun
from ..models.ops import SystemHealthState
from ..models.programme import ProgrammeUpload
from ..models.work_profile import AssetUsageActual, ItemContextProfile, ItemKnowledgeBase


SYSTEM_HEALTH_KEY = "primary"
STATE_HEALTHY = "healthy"
STATE_DEGRADED = "degraded"
STATE_RECOVERY = "recovery"


def _reason_codes_from_upload(upload: ProgrammeUpload) -> list[str]:
    notes = upload.completeness_notes if isinstance(upload.completeness_notes, dict) else {}
    reasons = list(notes.get("work_profile_degraded_reasons") or [])
    if upload.status == "completed_with_warnings":
        reasons.append("completed_with_warnings")
    if upload.status == "failed":
        reasons.append("upload_failed")
    if notes.get("work_profile_ai_suppressed"):
        reasons.append("work_profile_ai_suppressed")
    if notes.get("ai_quota_exhausted"):
        reasons.append("ai_quota_exhausted")
    return sorted({str(reason) for reason in reasons if reason})


def get_or_create_system_health_state(db: Session) -> SystemHealthState:
    row = db.get(SystemHealthState, SYSTEM_HEALTH_KEY)
    if row is None:
        row = SystemHealthState(key=SYSTEM_HEALTH_KEY, state=STATE_HEALTHY, reason_codes=[], clean_upload_streak=0)
        db.add(row)
        db.flush()
    return row


def record_upload_health_outcome(db: Session, upload: ProgrammeUpload) -> SystemHealthState:
    row = get_or_create_system_health_state(db)
    reasons = _reason_codes_from_upload(upload)
    now = datetime.now(timezone.utc)

    if reasons:
        row.state = STATE_DEGRADED
        row.reason_codes = reasons
        row.clean_upload_streak = 0
        row.last_transition_at = now
        row.last_trigger_upload_id = getattr(upload, "id", None)
        db.flush()
        return row

    if row.state == STATE_DEGRADED:
        row.state = STATE_RECOVERY
        row.reason_codes = []
        row.clean_upload_streak = 1
        row.last_transition_at = now
        row.last_trigger_upload_id = getattr(upload, "id", None)
    elif row.state == STATE_RECOVERY:
        row.clean_upload_streak = int(row.clean_upload_streak or 0) + 1
        if row.clean_upload_streak >= 2:
            row.state = STATE_HEALTHY
            row.reason_codes = []
            row.last_transition_at = now
            row.last_trigger_upload_id = getattr(upload, "id", None)
    else:
        row.state = STATE_HEALTHY
        row.reason_codes = []
        row.clean_upload_streak = max(int(row.clean_upload_streak or 0), 0)
        row.last_trigger_upload_id = getattr(upload, "id", None)
    db.flush()
    return row


def _queue_backlog_summary(db: Session) -> dict[str, int]:
    rows = (
        db.query(ProgrammeUploadJob.status, func.count(ProgrammeUploadJob.id))
        .group_by(ProgrammeUploadJob.status)
        .all()
    )
    summary = {"queued": 0, "running": 0, "retry_wait": 0, "dead": 0}
    for status, count in rows:
        if status in summary:
            summary[str(status)] = int(count or 0)
    return summary


def _latest_scheduled_run(db: Session, job_name: str) -> ScheduledJobRun | None:
    return (
        db.query(ScheduledJobRun)
        .filter(ScheduledJobRun.job_name == job_name)
        .order_by(ScheduledJobRun.logical_local_date.desc(), ScheduledJobRun.started_at.desc())
        .first()
    )


def build_system_health_payload(db: Session, *, database_connected: bool) -> dict[str, object]:
    row = get_or_create_system_health_state(db)
    return {
        "database_connected": database_connected,
        "state": row.state,
        "reason_codes": list(row.reason_codes or []),
        "clean_upload_streak": int(row.clean_upload_streak or 0),
        "last_transition_at": row.last_transition_at,
        "last_trigger_upload_id": row.last_trigger_upload_id,
        "queue_backlog": _queue_backlog_summary(db),
        "last_nightly_run": _latest_scheduled_run(db, "nightly_lookahead"),
        "last_feature_learning_run": _latest_scheduled_run(db, "nightly_feature_learning"),
    }


def build_ai_readiness_payload(db: Session) -> dict[str, object]:
    active_classifications = (
        db.query(func.count(ItemClassification.id))
        .filter(ItemClassification.is_active.is_(True), ItemClassification.confidence.in_(["medium", "high"]))
        .scalar()
        or 0
    )
    trusted_profiles = (
        db.query(func.count(ItemContextProfile.id))
        .filter(
            ItemContextProfile.invalidated_at.is_(None),
            ItemContextProfile.project_id.isnot(None),
            ItemContextProfile.source.in_(["manual", "learned", "ai"]),
            ItemContextProfile.sample_count >= 1,
        )
        .scalar()
        or 0
    )
    global_profiles = db.query(func.count(ItemKnowledgeBase.item_id)).scalar() or 0
    actuals = db.query(func.count(AssetUsageActual.id)).scalar() or 0
    other_share = (
        db.query(func.count(ItemClassification.id))
        .filter(ItemClassification.is_active.is_(True), ItemClassification.asset_type == "other")
        .scalar()
        or 0
    )
    total_active = (
        db.query(func.count(ItemClassification.id))
        .filter(ItemClassification.is_active.is_(True))
        .scalar()
        or 0
    )
    uploads = db.query(ProgrammeUpload).all()
    correction_rate = 0.0
    if total_active:
        correction_sum = (
            db.query(func.coalesce(func.sum(ItemClassification.correction_count), 0))
            .filter(ItemClassification.is_active.is_(True))
            .scalar()
            or 0
        )
        correction_rate = float(correction_sum) / float(total_active)
    degraded_uploads = sum(1 for upload in uploads if upload.status == "completed_with_warnings")
    total_ai_cost = sum(float(upload.ai_cost_usd or 0) for upload in uploads)

    metrics = [
        {"name": "stable_classifications", "value": float(active_classifications), "threshold": 200.0},
        {"name": "trusted_local_profiles", "value": float(trusted_profiles), "threshold": 100.0},
        {"name": "global_knowledge_entries", "value": float(global_profiles), "threshold": 50.0},
        {"name": "actuals_coverage", "value": float(actuals), "threshold": 25.0},
        {
            "name": "other_share_pct",
            "value": 0.0 if total_active == 0 else round((float(other_share) / float(total_active)) * 100.0, 2),
            "threshold": 10.0,
            "invert": True,
        },
        {"name": "correction_rate", "value": round(correction_rate, 4), "threshold": 0.25, "invert": True},
        {"name": "degraded_uploads", "value": float(degraded_uploads), "threshold": 0.0, "invert": True},
        {"name": "ai_cost_usd_total", "value": round(total_ai_cost, 4), "threshold": 100.0},
    ]

    normalized = []
    ready = True
    for metric in metrics:
        invert = bool(metric.pop("invert", False))
        is_ready = metric["value"] <= metric["threshold"] if invert else metric["value"] >= metric["threshold"]
        ready = ready and is_ready
        normalized.append({**metric, "ready": is_ready})

    return {
        "ready_for_future_ml": ready,
        "summary": (
            "Baseline maturity is sufficient for a future RAG/fine-tuning spike."
            if ready
            else "Baseline maturity is not ready for future RAG/fine-tuning work yet."
        ),
        "metrics": normalized,
    }
