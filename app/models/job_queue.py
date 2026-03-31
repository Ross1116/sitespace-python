"""
Durable job queue and scheduled-job ledger tables.

programme_upload_jobs  -- one row per upload, drives the worker polling loop.
scheduled_job_runs     -- one row per (job_name, logical_local_date), prevents
                          duplicate nightly-lookahead executions.
"""

from sqlalchemy import (
    Column,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Date,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class ProgrammeUploadJob(Base):
    __tablename__ = "programme_upload_jobs"
    __table_args__ = (
        UniqueConstraint("upload_id", name="uq_programme_upload_jobs_upload"),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'completed', 'dead')",
            name="ck_programme_upload_jobs_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_programme_upload_jobs_attempt_count",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_programme_upload_jobs_max_attempts",
        ),
        Index("ix_programme_upload_jobs_claimable", "status", "available_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    upload_id = Column(
        UUID(as_uuid=True),
        ForeignKey("programme_uploads.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(String(20), nullable=False, default="queued")
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    available_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    worker_id = Column(String(100), nullable=True)
    last_error_code = Column(String(60), nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    upload = relationship("ProgrammeUpload", foreign_keys=[upload_id])

    def __repr__(self) -> str:
        return (
            f"<ProgrammeUploadJob(id={self.id}, upload_id={self.upload_id}, "
            f"status='{self.status}', attempt={self.attempt_count}/{self.max_attempts})>"
        )


class ScheduledJobRun(Base):
    __tablename__ = "scheduled_job_runs"
    __table_args__ = (
        UniqueConstraint(
            "job_name", "logical_local_date",
            name="uq_scheduled_job_runs_job_date",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_scheduled_job_runs_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_name = Column(String(100), nullable=False)
    logical_local_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<ScheduledJobRun(job='{self.job_name}', date={self.logical_local_date}, "
            f"status='{self.status}')>"
        )
