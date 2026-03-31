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
from datetime import datetime, timezone

import sentry_sdk
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..core.config import settings
from ..core.database import SessionLocal
from ..models.job_queue import ScheduledJobRun
from ..services.lookahead_engine import nightly_lookahead_job

logger = logging.getLogger(__name__)

JOB_NAME = "nightly_lookahead"
# Advisory lock key — deterministic from job name
ADVISORY_LOCK_KEY = int.from_bytes(JOB_NAME.encode("utf-8")[:8].ljust(8, b"\x00"), byteorder="big", signed=True)


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
            # Another instance is mid-run (shouldn't happen with advisory lock, but be safe)
            logger.info("Nightly tick: already running for %s, skipping", today)
            return
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
            nightly_lookahead_job()
            run_record.status = "succeeded"
            run_record.finished_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("Nightly tick: completed successfully for %s", today)
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
