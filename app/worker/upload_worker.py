"""
Persistent upload-worker polling loop.

Claims queued ProgrammeUploadJob rows via FOR UPDATE SKIP LOCKED,
runs the processing pipeline, and handles retries / heartbeats.

Designed to run as a separate Railway service (persistent, no HTTP).
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import uuid
from datetime import datetime, timedelta, timezone

import sentry_sdk

from ..core.config import settings
from ..core.database import SessionLocal
from ..models.job_queue import ProgrammeUploadJob
from ..models.programme import ProgrammeActivity, ProgrammeUpload
from ..services.process_programme import process_programme

logger = logging.getLogger(__name__)

WORKER_ID = f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class UploadWorker:
    def __init__(self) -> None:
        self._shutdown = threading.Event()
        self._poll_seconds = settings.UPLOAD_WORKER_POLL_SECONDS
        self._heartbeat_seconds = settings.UPLOAD_WORKER_HEARTBEAT_SECONDS
        self._claim_ttl_seconds = settings.UPLOAD_WORKER_CLAIM_TTL_SECONDS
        self._requeue_interval_polls = max(1, self._claim_ttl_seconds // self._poll_seconds)
        self._polls_since_requeue = 0

    def run(self) -> None:
        """Main loop — poll for jobs until shutdown signal."""
        logger.info(
            "Upload worker started: id=%s poll=%ds heartbeat=%ds claim_ttl=%ds",
            WORKER_ID, self._poll_seconds, self._heartbeat_seconds, self._claim_ttl_seconds,
        )
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # On startup, requeue any jobs abandoned by a previous worker instance
        self._requeue_expired_claims()

        while not self._shutdown.is_set():
            try:
                # Periodically requeue expired claims (not just at startup)
                self._polls_since_requeue += 1
                if self._polls_since_requeue >= self._requeue_interval_polls:
                    self._requeue_expired_claims()
                    self._polls_since_requeue = 0

                claimed = self._poll_once()
                if not claimed:
                    self._shutdown.wait(timeout=self._poll_seconds)
            except Exception:
                logger.exception("Unexpected error in worker poll loop")
                sentry_sdk.capture_exception()
                self._shutdown.wait(timeout=self._poll_seconds)

        logger.info("Upload worker shutting down: id=%s", WORKER_ID)

    def _handle_signal(self, signum: int, frame: object) -> None:
        logger.info("Received signal %d, initiating graceful shutdown", signum)
        self._shutdown.set()

    def _requeue_expired_claims(self) -> None:
        """Requeue jobs claimed by dead workers (heartbeat expired)."""
        db = SessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._claim_ttl_seconds)
            expired = (
                db.query(ProgrammeUploadJob)
                .filter(
                    ProgrammeUploadJob.status == "running",
                    ProgrammeUploadJob.heartbeat_at < cutoff,
                )
                .all()
            )
            for job in expired:
                if job.attempt_count >= job.max_attempts:
                    job.status = "dead"
                    job.last_error_code = "heartbeat_expired"
                    job.last_error_message = "Worker heartbeat expired after max attempts"
                    # Mark the upload as failed
                    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == job.upload_id).first()
                    if upload and upload.status == "processing":
                        upload.status = "failed"
                        upload.processing_outcome = "failed"
                    logger.warning("Dead-lettered job %s (upload %s) — max attempts exhausted", job.id, job.upload_id)
                else:
                    job.status = "retry_wait"
                    job.available_at = datetime.now(timezone.utc) + timedelta(
                        seconds=min(30 * (2 ** job.attempt_count), 300)
                    )
                    job.worker_id = None
                    job.claimed_at = None
                    job.heartbeat_at = None
                    # Keep upload in "processing" so the guard blocks overlapping
                    # uploads while this job is still retryable.
                    upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == job.upload_id).first()
                    if upload and upload.status == "failed":
                        upload.status = "processing"
                    logger.info(
                        "Requeued expired job %s (upload %s) — attempt %d/%d, available_at=%s",
                        job.id, job.upload_id, job.attempt_count, job.max_attempts, job.available_at,
                    )
            if expired:
                db.commit()
                logger.info("Requeued %d expired jobs on startup", len([j for j in expired if j.status != "dead"]))
        except Exception:
            db.rollback()
            logger.exception("Failed to requeue expired claims on startup")
        finally:
            db.close()

    def _poll_once(self) -> bool:
        """Try to claim and process one job. Returns True if a job was claimed."""
        db = SessionLocal()
        try:
            # Also promote retry_wait jobs whose available_at has passed
            now = datetime.now(timezone.utc)
            promoted = (
                db.query(ProgrammeUploadJob)
                .filter(
                    ProgrammeUploadJob.status == "retry_wait",
                    ProgrammeUploadJob.available_at <= now,
                )
                .all()
            )
            for job in promoted:
                job.status = "queued"
            if promoted:
                db.commit()

            # Claim one job via FOR UPDATE SKIP LOCKED
            job = (
                db.query(ProgrammeUploadJob)
                .filter(
                    ProgrammeUploadJob.status == "queued",
                    ProgrammeUploadJob.available_at <= now,
                )
                .order_by(ProgrammeUploadJob.available_at.asc())
                .with_for_update(skip_locked=True)
                .first()
            )
            if job is None:
                db.rollback()
                return False

            job.status = "running"
            job.claimed_at = now
            job.heartbeat_at = now
            job.worker_id = WORKER_ID
            job.attempt_count += 1
            db.commit()

            logger.info(
                "Claimed job %s (upload %s) — attempt %d/%d",
                job.id, job.upload_id, job.attempt_count, job.max_attempts,
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to claim job")
            return False
        finally:
            db.close()

        # Process the job outside the claim transaction
        job_id = job.id
        upload_id = str(job.upload_id)
        attempt = job.attempt_count
        max_attempts = job.max_attempts

        # Start heartbeat thread
        heartbeat_stop = threading.Event()
        heartbeat_failed = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, heartbeat_stop, heartbeat_failed),
            daemon=True,
        )
        heartbeat_thread.start()

        try:
            # Reset partial state before retry
            if attempt > 1:
                self._reset_partial_state(upload_id)

            process_programme(upload_id)

            # If heartbeat signaled claim loss, abandon — another worker may
            # have reclaimed this job.
            if heartbeat_failed.is_set():
                logger.warning("Job %s: heartbeat lost during processing, abandoning", job_id)
                return True

            # process_programme swallows its own exceptions and sets upload
            # status internally.  Check the persisted status to decide the
            # queue transition rather than assuming success.
            upload_status = self._get_upload_status(upload_id)
            if upload_status == "failed":
                self._handle_job_failure(
                    job_id, upload_id, attempt, max_attempts,
                    RuntimeError("process_programme marked upload as failed"),
                )
            else:
                self._mark_job_completed(job_id)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.exception("Job %s failed (upload %s)", job_id, upload_id)
            if not heartbeat_failed.is_set():
                self._handle_job_failure(job_id, upload_id, attempt, max_attempts, exc)
            else:
                logger.warning("Job %s: heartbeat lost, skipping failure handling", job_id)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=5)

        return True

    def _heartbeat_loop(
        self,
        job_id: uuid.UUID,
        stop: threading.Event,
        failed: threading.Event,
    ) -> None:
        """Periodically update heartbeat_at so the job isn't reclaimed.

        Sets *failed* if the update query affects 0 rows (claim lost) or
        raises, signaling _poll_once to treat the job as lost.
        """
        while not stop.wait(timeout=self._heartbeat_seconds):
            db = SessionLocal()
            try:
                rows = db.query(ProgrammeUploadJob).filter(
                    ProgrammeUploadJob.id == job_id,
                    ProgrammeUploadJob.status == "running",
                    ProgrammeUploadJob.worker_id == WORKER_ID,
                ).update({"heartbeat_at": datetime.now(timezone.utc)})
                db.commit()
                if rows == 0:
                    logger.warning("Heartbeat: job %s no longer owned by this worker, signaling loss", job_id)
                    failed.set()
                    return
            except Exception:
                db.rollback()
                logger.warning("Heartbeat update failed for job %s, signaling loss", job_id)
                failed.set()
                return
            finally:
                db.close()

    def _reset_partial_state(self, upload_id: str) -> None:
        """
        Reset partial upload state before a retry attempt.

        Deletes previously inserted programme_activities for this upload
        (cascade handles mappings, AI suggestions, work-profile rows).
        Clears upload-derived fields but keeps the upload row, file, version, and audit.

        Raises on failure so process_programme() is never called on dirty state.
        """
        db = SessionLocal()
        try:
            # Delete activities — CASCADE handles activity_asset_mappings,
            # ai_suggestion_logs, activity_work_profiles
            db.query(ProgrammeActivity).filter(
                ProgrammeActivity.programme_upload_id == upload_id,
            ).delete(synchronize_session=False)

            upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
            if upload:
                upload.column_mapping = None
                upload.completeness_score = None
                upload.completeness_notes = None
                upload.ai_tokens_used = None
                upload.ai_cost_usd = None
                upload.processing_outcome = None

            db.commit()
            logger.info("Reset partial state for upload %s before retry", upload_id)
        except Exception:
            db.rollback()
            logger.exception("Failed to reset partial state for upload %s", upload_id)
            raise
        finally:
            db.close()

    def _get_upload_status(self, upload_id: str) -> str | None:
        """Reload the upload row and return its status."""
        db = SessionLocal()
        try:
            upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
            return upload.status if upload else None
        finally:
            db.close()

    def _mark_job_completed(self, job_id: uuid.UUID) -> None:
        db = SessionLocal()
        try:
            rows = (
                db.query(ProgrammeUploadJob)
                .filter(
                    ProgrammeUploadJob.id == job_id,
                    ProgrammeUploadJob.status == "running",
                    ProgrammeUploadJob.worker_id == WORKER_ID,
                )
                .update({
                    "status": "completed",
                    "last_error_code": None,
                    "last_error_message": None,
                })
            )
            db.commit()
            if rows == 0:
                logger.warning("Job %s: conditional completion update matched 0 rows (claim lost?)", job_id)
            else:
                logger.info("Job %s completed successfully", job_id)
        except Exception:
            db.rollback()
            logger.exception("Failed to mark job %s as completed", job_id)
        finally:
            db.close()

    def _handle_job_failure(
        self,
        job_id: uuid.UUID,
        upload_id: str,
        attempt: int,
        max_attempts: int,
        exc: Exception,
    ) -> None:
        db = SessionLocal()
        try:
            job = (
                db.query(ProgrammeUploadJob)
                .filter(
                    ProgrammeUploadJob.id == job_id,
                    ProgrammeUploadJob.status == "running",
                    ProgrammeUploadJob.worker_id == WORKER_ID,
                )
                .first()
            )
            if not job:
                logger.warning("Job %s: not found with expected claim (lost?), skipping failure handling", job_id)
                return

            error_code = type(exc).__name__[:60]
            error_message = str(exc)[:2000]
            job.last_error_code = error_code
            job.last_error_message = error_message

            if attempt >= max_attempts:
                job.status = "dead"
                # Mark the upload as failed
                upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
                if upload and upload.status == "processing":
                    upload.status = "failed"
                    upload.processing_outcome = "failed"
                    from ..utils.programme_notes import normalize_programme_completeness_notes
                    notes = normalize_programme_completeness_notes(upload.completeness_notes)
                    notes["notes"] = (
                        f"Processing failed after {max_attempts} attempts. "
                        f"Last error: {error_code}. Please re-upload."
                    )
                    upload.completeness_notes = notes
                logger.warning(
                    "Job %s dead-lettered (upload %s) — %d/%d attempts exhausted: %s",
                    job_id, upload_id, attempt, max_attempts, error_code,
                )
            else:
                # Exponential backoff: 30s, 60s, 120s... capped at 300s
                backoff = min(30 * (2 ** (attempt - 1)), 300)
                job.status = "retry_wait"
                job.available_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                job.worker_id = None
                job.claimed_at = None
                job.heartbeat_at = None
                # Keep upload in "processing" so the guard doesn't allow
                # overlapping uploads while this job is still retryable.
                upload = db.query(ProgrammeUpload).filter(ProgrammeUpload.id == upload_id).first()
                if upload and upload.status == "failed":
                    upload.status = "processing"
                logger.info(
                    "Job %s scheduled for retry (upload %s) — attempt %d/%d, backoff=%ds",
                    job_id, upload_id, attempt, max_attempts, backoff,
                )

            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to handle job failure for %s", job_id)
        finally:
            db.close()


def run_upload_worker() -> None:
    """Entry point for the upload worker service."""
    worker = UploadWorker()
    worker.run()


if __name__ == "__main__":
    run_upload_worker()
