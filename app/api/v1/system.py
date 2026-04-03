from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...core.database import engine, get_db, assert_database_connection
from ...core.security import require_role
from ...models.job_queue import ScheduledJobRun
from ...models.user import User
from ...schemas.enums import UserRole
from ...schemas.system import AIReadinessResponse, QueueBacklogSummary, ScheduledRunSummary, SystemHealthResponse
from ...services.system_health_service import build_ai_readiness_payload, build_system_health_payload


router = APIRouter(prefix="/system", tags=["System"])
logger = logging.getLogger(__name__)


def _scheduled_run_payload(run: ScheduledJobRun | None) -> ScheduledRunSummary | None:
    if run is None:
        return None
    return ScheduledRunSummary(
        job_name=run.job_name,
        logical_local_date=run.logical_local_date,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        last_error=run.last_error,
    )


def _fallback_system_health_response() -> SystemHealthResponse:
    return SystemHealthResponse(
        database_connected=False,
        state="degraded",
        reason_codes=["database_unavailable"],
        clean_upload_streak=0,
        last_transition_at=None,
        last_trigger_upload_id=None,
        queue_backlog=QueueBacklogSummary(),
        last_nightly_run=None,
        last_feature_learning_run=None,
    )


@router.get("/health", response_model=SystemHealthResponse)
def get_system_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    try:
        assert_database_connection(engine)
        database_connected = True
        payload = build_system_health_payload(db, database_connected=database_connected)
    except Exception:
        return _fallback_system_health_response()

    backlog = QueueBacklogSummary(**payload["queue_backlog"])
    return SystemHealthResponse(
        database_connected=database_connected,
        state=payload["state"],
        reason_codes=payload["reason_codes"],
        clean_upload_streak=payload["clean_upload_streak"],
        last_transition_at=payload["last_transition_at"],
        last_trigger_upload_id=payload["last_trigger_upload_id"],
        queue_backlog=backlog,
        last_nightly_run=_scheduled_run_payload(payload["last_nightly_run"]),
        last_feature_learning_run=_scheduled_run_payload(payload["last_feature_learning_run"]),
    )


@router.get("/ai-readiness", response_model=AIReadinessResponse)
def get_ai_readiness(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    try:
        payload = build_ai_readiness_payload(db)
        return AIReadinessResponse(**payload)
    except Exception:
        logger.exception("Failed to build AI readiness payload")
        return AIReadinessResponse(
            ready_for_future_ml=False,
            summary="AI readiness unavailable because the backend could not query current readiness signals.",
            metrics=[],
        )
