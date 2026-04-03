from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...core.database import engine, get_db, assert_database_connection
from ...core.security import require_role
from ...models.user import User
from ...schemas.enums import UserRole
from ...schemas.system import AIReadinessResponse, QueueBacklogSummary, ScheduledRunSummary, SystemHealthResponse
from ...services.system_health_service import build_ai_readiness_payload, build_system_health_payload


router = APIRouter(prefix="/system", tags=["System"])


def _scheduled_run_payload(run) -> ScheduledRunSummary | None:
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


@router.get("/health", response_model=SystemHealthResponse)
def get_system_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.ADMIN, UserRole.MANAGER])),
):
    database_connected = True
    try:
        assert_database_connection(engine)
    except Exception:
        database_connected = False

    payload = build_system_health_payload(db, database_connected=database_connected)
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
    payload = build_ai_readiness_payload(db)
    return AIReadinessResponse(**payload)
