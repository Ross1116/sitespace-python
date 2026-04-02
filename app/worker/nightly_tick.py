"""
One-shot nightly lookahead tick runner for Railway scheduled jobs.

Railway cron wakes this every 5 minutes. The runner checks whether the
nightly lookahead is due (local-timezone-aware), skips if not, and uses
a scheduled_job_runs ledger + advisory lock to prevent duplicates.

Exit codes:
    0 — not due or successfully completed
    1 — execution failed
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

import sentry_sdk
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..core.config import settings
from ..core.database import SessionLocal
from ..models.job_queue import ScheduledJobRun
from ..services.lookahead_engine import nightly_lookahead_job
from ..services.feature_learning_service import nightly_feature_learning_job

logger = logging.getLogger(__name__)

JOB_NAME = "nightly_lookahead"
# Advisory lock key — deterministic from job name
ADVISORY_LOCK_KEY = int.from_bytes(JOB_NAME.encode("utf-8")[:8].ljust(8, b"\x00"), byteorder="big", signed=True)

FEATURE_LEARNING_JOB_NAME = "nightly_feature_learning"


def _local_now() -> datetime:
    """Return current time in the configured nightly-lookahead timezone."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]
    tz = ZoneInfo(settings.NIGHTLY_LOOKAHEAD_TIMEZONE)
    return datetime.now(tz)


def _is_due(local_now: datetime) -> bool:
    """True if local time is at or after the configured hour:minute."""
    return (
        local_now.hour > settings.NIGHTLY_LOOKAHEAD_HOUR
        or (
            local_now.hour == settings.NIGHTLY_LOOKAHEAD_HOUR
            and local_now.minute >= settings.NIGHTLY_LOOKAHEAD_MINUTE
        )
    )


def _run_feature_learning_tick(db: Session, today: date) -> None:
    """
    Run the nightly feature-learning batch as an independent ScheduledJobRun entry.

    Failures are logged but do NOT propagate — feature learning is supplementary
    and must not block the main nightly tick from completing.
    """
    existing = (
        db.query(ScheduledJobRun)
        .filter(
            ScheduledJobRun.job_name == FEATURE_LEARNING_JOB_NAME,
            ScheduledJobRun.logical_local_date == today,
        )
        .first()
    )
    if existing and existing.status == "succeeded":
        logger.info("Feature learning tick: already succeeded for %s, skipping", today)
        return

    if existing and existing.status in ("running", "failed"):
        existing.status = "running"
        existing.started_at = datetime.now(timezone.utc)
        existing.finished_at = None
        existing.last_error = None
        db.commit()
        run_record = existing
    else:
        run_record = ScheduledJobRun(
            job_name=FEATURE_LEARNING_JOB_NAME,
            logical_local_date=today,
            status="running",
        )
        try:
            db.add(run_record)
            db.commit()
        except Exception:
            db.rollback()
            logger.info("Feature learning tick: concurrent insert for %s, skipping", today)
            return

    logger.info("Feature learning tick: running batch for %s", today)
    try:
        result = nightly_feature_learning_job()
        run_record.status = "succeeded"
        run_record.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "Feature learning tick: completed for %s — %s",
            today, result,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("Feature learning tick: failed for %s", today)
        try:
            db.rollback()
            run_record.status = "failed"
            run_record.finished_at = datetime.now(timezone.utc)
            run_record.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
        except Exception:
            db.rollback()


def run_nightly_tick() -> None:
    """Entry point for the Railway scheduled job service."""
    local_now = _local_now()
    today = local_now.date()

    if not _is_due(local_now):
        logger.info(
            "Nightly tick: not due yet (local=%s, target=%02d:%02d %s)",
            local_now.strftime("%H:%M"), settings.NIGHTLY_LOOKAHEAD_HOUR,
            settings.NIGHTLY_LOOKAHEAD_MINUTE, settings.NIGHTLY_LOOKAHEAD_TIMEZONE,
        )
        return

    db = SessionLocal()
    advisory_locked = False
    try:
        # Advisory lock — only one tick instance can own the nightly run
        advisory_locked = bool(
            db.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": ADVISORY_LOCK_KEY},
            ).scalar()
        )
        if not advisory_locked:
            logger.info("Nightly tick: another instance holds the advisory lock, skipping")
            return

        # Check if today's run already succeeded
        existing = (
            db.query(ScheduledJobRun)
            .filter(
                ScheduledJobRun.job_name == JOB_NAME,
                ScheduledJobRun.logical_local_date == today,
            )
            .first()
        )
        if existing and existing.status == "succeeded":
            logger.info("Nightly tick: already succeeded for %s, skipping", today)
            return

        # If a previous run failed today, allow retry
        if existing and existing.status == "failed":
            existing.status = "running"
            existing.started_at = datetime.now(timezone.utc)
            existing.finished_at = None
            existing.last_error = None
            db.commit()
            run_record = existing
        elif existing and existing.status == "running":
            # We hold the advisory lock, so a "running" row means the previous
            # instance crashed.  Persist the failure audit first so it's
            # visible in the DB, then reset for a fresh run.
            logger.warning(
                "Nightly tick: found stale 'running' row for %s (started %s) — marking failed and retrying",
                today, existing.started_at,
            )
            existing.status = "failed"
            existing.finished_at = datetime.now(timezone.utc)
            existing.last_error = "Stale running state recovered by advisory-lock holder"
            db.commit()

            # Reset the same row for the retry (unique constraint prevents a
            # second row for the same job_name + date).
            existing.status = "running"
            existing.started_at = datetime.now(timezone.utc)
            existing.finished_at = None
            existing.last_error = None
            db.commit()
            run_record = existing
        else:
            # Create new run record
            run_record = ScheduledJobRun(
                job_name=JOB_NAME,
                logical_local_date=today,
                status="running",
            )
            try:
                db.add(run_record)
                db.commit()
            except IntegrityError:
                db.rollback()
                logger.info("Nightly tick: concurrent insert for %s, skipping", today)
                return

        logger.info("Nightly tick: executing nightly lookahead for %s", today)

        # Run the actual nightly lookahead job
        try:
            result = nightly_lookahead_job()
            finished = datetime.now(timezone.utc)

            if result.get("failed", 0) > 0:
                run_record.status = "failed"
                run_record.finished_at = finished
                run_record.last_error = (
                    f"{result['failed']}/{result['total']} projects failed "
                    f"({result['succeeded']} succeeded)"
                )
                db.commit()
                logger.warning(
                    "Nightly tick: partial failure for %s — %d/%d projects failed",
                    today, result["failed"], result["total"],
                )
                # Exit 1 so the same-day retry path can re-run
                sys.exit(1)
            else:
                run_record.status = "succeeded"
                run_record.finished_at = finished
                db.commit()
                logger.info(
                    "Nightly tick: completed successfully for %s — %d projects",
                    today, result.get("succeeded", 0),
                )
                _run_feature_learning_tick(db, today)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.exception("Nightly tick: failed for %s", today)
            db.rollback()
            # Re-fetch to avoid stale state after rollback
            run_record = (
                db.query(ScheduledJobRun)
                .filter(
                    ScheduledJobRun.job_name == JOB_NAME,
                    ScheduledJobRun.logical_local_date == today,
                )
                .first()
            )
            if run_record:
                run_record.status = "failed"
                run_record.finished_at = datetime.now(timezone.utc)
                run_record.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                db.commit()
            sys.exit(1)

    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("Nightly tick: unexpected error")
        db.rollback()
        sys.exit(1)
    finally:
        if advisory_locked:
            try:
                db.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": ADVISORY_LOCK_KEY},
                )
                db.commit()
            except Exception:
                db.rollback()
        db.close()


if __name__ == "__main__":
    run_nightly_tick()
