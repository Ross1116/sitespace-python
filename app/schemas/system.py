from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from .base import BaseSchema


class QueueBacklogSummary(BaseSchema):
    queued: int = 0
    running: int = 0
    retry_wait: int = 0
    dead: int = 0


class ScheduledRunSummary(BaseSchema):
    job_name: str
    logical_local_date: date
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    last_error: Optional[str] = None


class SystemHealthResponse(BaseSchema):
    database_connected: bool
    state: str
    reason_codes: list[str]
    clean_upload_streak: int
    last_transition_at: Optional[datetime] = None
    last_trigger_upload_id: Optional[UUID] = None
    queue_backlog: QueueBacklogSummary
    last_nightly_run: Optional[ScheduledRunSummary] = None
    last_feature_learning_run: Optional[ScheduledRunSummary] = None


class AIReadinessMetric(BaseSchema):
    name: str
    value: float
    threshold: float
    ready: bool


class AIReadinessResponse(BaseSchema):
    ready_for_future_ml: bool
    summary: str
    metrics: list[AIReadinessMetric]
