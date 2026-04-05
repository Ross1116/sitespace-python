from __future__ import annotations

from datetime import date, timedelta
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
from ...schemas.capacity_dashboard import (
    CapacityAssetTypeSummary,
    CapacityCell,
    CapacityDashboardDiagnostics,
    CapacityDashboardResponse,
    CapacityWeekSummary,
)
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
    compute_capacity_dashboard,
    get_fresh_snapshot,
    get_weekly_activity_candidates,
    get_snapshot_history,
    get_sub_notifications,
    get_sub_asset_suggestions_for_project,
)
from ...core.constants import CAPACITY_DASHBOARD_DEFAULT_WEEKS, CAPACITY_DASHBOARD_MAX_WEEKS

router = APIRouter(prefix="/lookahead", tags=["Lookahead"])


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



@router.get("/{project_id}", response_model=LookaheadResponse)
def get_lookahead(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> LookaheadResponse:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)

    snapshot = get_fresh_snapshot(project_id, db)
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

    snapshot = get_fresh_snapshot(project_id, db)
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

    snapshot = get_fresh_snapshot(project_id, db)
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

    snapshot = get_fresh_snapshot(project_id, db)
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

    snapshot = get_fresh_snapshot(project_id, db)
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


@router.get("/{project_id}/capacity-dashboard", response_model=CapacityDashboardResponse)
def get_capacity_dashboard(
    project_id: UUID,
    start_week: date | None = Query(None, description="Monday-aligned week start (defaults to the earliest visible week from the fresh snapshot)"),
    weeks: int = Query(
        CAPACITY_DASHBOARD_DEFAULT_WEEKS,
        ge=1,
        le=CAPACITY_DASHBOARD_MAX_WEEKS,
        description="Number of weeks to include",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> CapacityDashboardResponse:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, current_user)

    result = compute_capacity_dashboard(project_id, db, start_week=start_week, weeks=weeks)

    diagnostics_data = result.get("diagnostics")
    diagnostics = CapacityDashboardDiagnostics(**diagnostics_data) if diagnostics_data else None

    return CapacityDashboardResponse(
        project_id=result["project_id"],
        upload_id=result.get("upload_id"),
        start_week=result.get("start_week"),
        weeks=result.get("weeks", []),
        work_days_per_week=result.get("work_days_per_week", 5),
        asset_types=result.get("asset_types", []),
        rows={
            asset_type: {
                week_start: CapacityCell(**cell)
                for week_start, cell in week_cells.items()
            }
            for asset_type, week_cells in result.get("rows", {}).items()
        },
        summary_by_week={
            week_start: CapacityWeekSummary(**summary)
            for week_start, summary in result.get("summary_by_week", {}).items()
        },
        summary_by_asset_type={
            asset_type: CapacityAssetTypeSummary(**summary)
            for asset_type, summary in result.get("summary_by_asset_type", {}).items()
        },
        diagnostics=diagnostics,
        message=result.get("message"),
    )
