from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import normalize_role, require_role
from ...crud.site_project import check_sub_project_access
from ...models.site_project import SiteProject
from ...models.subcontractor import Subcontractor
from ...models.user import User
from ...schemas.enums import UserRole
from ...schemas.lookahead import (
    LookaheadAlertsResponse,
    LookaheadActivitiesResponse,
    LookaheadHistoryResponse,
    LookaheadActivityCandidate,
    LookaheadResponse,
    SnapshotHistoryItem,
    SubAssetSuggestionsResponse,
    SubNotification,
    SubcontractorLookaheadResponse,
)
from ...services.lookahead_engine import (
    calculate_lookahead_for_project,
    get_latest_booking_update_for_project,
    get_latest_snapshot,
    get_weekly_activity_candidates,
    get_snapshot_history,
    get_sub_notifications,
    get_sub_asset_suggestions_for_project,
)

router = APIRouter(prefix="/lookahead", tags=["Lookahead"])


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _check_project_exists(project_id: UUID, db: Session) -> SiteProject:
    project = db.query(SiteProject).filter(SiteProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _check_manager_project_access(project: SiteProject, current_user: User) -> None:
    role = normalize_role(getattr(current_user, "role", ""))
    if role == UserRole.ADMIN.value:
        return

    is_assigned_manager = any(str(manager.id) == str(current_user.id) for manager in project.managers)
    if not is_assigned_manager:
        raise HTTPException(status_code=403, detail="You don't have access to this project")


def _snapshot_refreshed_at(snapshot) -> datetime | None:
    if snapshot is None:
        return None

    generated_at = None
    if getattr(snapshot, "data", None):
        generated_at = snapshot.data.get("generated_at")
    if isinstance(generated_at, str):
        try:
            parsed = datetime.fromisoformat(generated_at)
        except ValueError:
            parsed = None
        else:
            parsed = _normalize_timestamp(parsed)
        if parsed is not None:
            return parsed

    return _normalize_timestamp(getattr(snapshot, "created_at", None))


def _get_fresh_snapshot(project_id: UUID, db: Session):
    """Return the current snapshot, recalculating if it is stale relative to the latest processed upload."""
    from ...models.programme import ProgrammeUpload

    latest_upload = (
        db.query(ProgrammeUpload)
        .filter(
            ProgrammeUpload.project_id == project_id,
            ProgrammeUpload.status.in_(["committed", "degraded"]),
        )
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )
    if latest_upload is None:
        return None
    snapshot = get_latest_snapshot(project_id, db)
    latest_booking_update = _normalize_timestamp(get_latest_booking_update_for_project(project_id, db))
    snapshot_refreshed_at = _snapshot_refreshed_at(snapshot)
    booking_is_newer = bool(
        snapshot_refreshed_at
        and latest_booking_update
        and latest_booking_update > snapshot_refreshed_at
    )
    if not snapshot or snapshot.programme_upload_id != latest_upload.id or booking_is_newer:
        snapshot = calculate_lookahead_for_project(project_id, db)
    return snapshot


@router.get("/{project_id}", response_model=LookaheadResponse)
def get_lookahead(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> LookaheadResponse:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)

    snapshot = _get_fresh_snapshot(project_id, db)
    if not snapshot:
        return LookaheadResponse(project_id=project_id, rows=[], message="No processed programme available yet.")

    return LookaheadResponse(
        project_id=project_id,
        snapshot_id=snapshot.id,
        snapshot_date=snapshot.snapshot_date.isoformat(),
        timezone=snapshot.data.get("timezone") if snapshot.data else None,
        rows=snapshot.data.get("rows", []) if snapshot.data else [],
    )


@router.get("/{project_id}/alerts", response_model=LookaheadAlertsResponse)
def get_lookahead_alerts(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> LookaheadAlertsResponse:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)

    snapshot = _get_fresh_snapshot(project_id, db)
    if not snapshot:
        return LookaheadAlertsResponse(project_id=project_id, alerts={})

    return LookaheadAlertsResponse(
        project_id=project_id,
        snapshot_id=snapshot.id,
        snapshot_date=snapshot.snapshot_date.isoformat(),
        alerts=snapshot.anomaly_flags or {},
    )


@router.get("/{project_id}/sub/{sub_id}", response_model=SubcontractorLookaheadResponse)
def get_subcontractor_lookahead(
    project_id: UUID,
    sub_id: UUID,
    db: Session = Depends(get_db),
    current_sub: Subcontractor = Depends(require_role([UserRole.SUBCONTRACTOR])),
) -> SubcontractorLookaheadResponse:
    project = _check_project_exists(project_id, db)
    if not check_sub_project_access(db, current_sub, project):
        raise HTTPException(status_code=403, detail="You are not assigned to this project")

    if str(current_sub.id) != str(sub_id):
        raise HTTPException(status_code=403, detail="You can only view your own lookahead data")

    snapshot = _get_fresh_snapshot(project_id, db)
    notifications = get_sub_notifications(project_id, sub_id, db)

    return SubcontractorLookaheadResponse(
        project_id=project_id,
        sub_id=sub_id,
        snapshot_date=snapshot.snapshot_date.isoformat() if snapshot else None,
        timezone=(snapshot.data or {}).get("timezone") if snapshot else None,
        rows=(snapshot.data or {}).get("rows", []) if snapshot else [],
        notifications=[
            SubNotification(
                id=n.id,
                activity_id=n.activity_id,
                asset_type=n.asset_type,
                trigger_type=n.trigger_type,
                status=n.status,
                sent_at=n.sent_at.isoformat() if n.sent_at else None,
                acted_at=n.acted_at.isoformat() if n.acted_at else None,
                booking_id=n.booking_id,
                created_at=n.created_at.isoformat() if n.created_at else None,
            )
            for n in notifications
        ],
    )


@router.get("/{project_id}/sub-asset-suggestions", response_model=SubAssetSuggestionsResponse)
def get_subcontractor_asset_suggestions(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> SubAssetSuggestionsResponse:
    """
    Return per-subcontractor asset demand suggestions for lookahead planning.

    Uses each subcontractor's registered trade_specialty to predict which
    bookable site assets they are likely to need, and overlays projected
    weekly demand hours from the latest snapshot.

    Response shape:
    {
      "project_id": "...",
      "snapshot_date": "2026-04-07",
      "suggestions": [
        {
          "subcontractor_id": "...",
          "company_name": "Smith Electrical",
          "trade_specialty": "electrician",
          "suggested_asset_types": ["ewp", "loading_bay"],
          "demand_rows": [
            {
              "asset_type": "ewp",
              "week_start": "2026-04-07",
              "demand_hours": 40.0,
              "booked_hours": 8.0,
              "gap_hours": 32.0,
              "demand_level": "high"
            }
          ]
        }
      ]
    }
    """
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)

    snapshot = _get_fresh_snapshot(project_id, db)
    suggestions = get_sub_asset_suggestions_for_project(project_id, db)

    return SubAssetSuggestionsResponse(
        project_id=project_id,
        snapshot_date=snapshot.snapshot_date.isoformat() if snapshot else None,
        suggestions=suggestions,
    )


@router.get("/{project_id}/activities", response_model=LookaheadActivitiesResponse)
def get_lookahead_week_activities(
    project_id: UUID,
    week_start: date = Query(..., description="Monday-aligned lookahead week start"),
    asset_type: str = Query(..., min_length=1, description="Canonical asset type"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> LookaheadActivitiesResponse:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)
    normalized_week_start = week_start - timedelta(days=week_start.weekday())

    snapshot = _get_fresh_snapshot(project_id, db)
    if not snapshot:
        return LookaheadActivitiesResponse(
            project_id=project_id,
            week_start=normalized_week_start.isoformat(),
            asset_type=asset_type.strip().lower(),
            activities=[],
        )

    candidates = get_weekly_activity_candidates(
        project_id=project_id,
        week_start=normalized_week_start,
        asset_type=asset_type,
        db=db,
    )
    return LookaheadActivitiesResponse(
        project_id=project_id,
        week_start=normalized_week_start.isoformat(),
        asset_type=asset_type.strip().lower(),
        activities=[LookaheadActivityCandidate(**candidate) for candidate in candidates],
    )


@router.get("/{project_id}/history", response_model=LookaheadHistoryResponse)
def get_lookahead_history(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> LookaheadHistoryResponse:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)

    snapshots = get_snapshot_history(project_id, db)
    return LookaheadHistoryResponse(
        project_id=project_id,
        history=[
            SnapshotHistoryItem(
                snapshot_id=s.id,
                snapshot_date=s.snapshot_date.isoformat(),
                created_at=s.created_at.isoformat() if s.created_at else None,
                anomaly_flags=s.anomaly_flags or {},
            )
            for s in snapshots
        ],
    )
