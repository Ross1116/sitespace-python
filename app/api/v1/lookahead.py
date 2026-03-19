from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import require_role
from ...models.site_project import SiteProject
from ...models.subcontractor import Subcontractor
from ...models.user import User
from ...schemas.enums import UserRole
from ...services.lookahead_engine import (
    calculate_lookahead_for_project,
    get_latest_snapshot,
    get_snapshot_history,
    get_sub_notifications,
    get_sub_asset_suggestions_for_project,
)

router = APIRouter(prefix="/lookahead", tags=["Lookahead"])


def _check_project_exists(project_id: UUID, db: Session) -> SiteProject:
    project = db.query(SiteProject).filter(SiteProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _normalize_role(role: object) -> str:
    if hasattr(role, "value"):
        return str(getattr(role, "value")).strip().lower()
    return str(role).strip().lower()


def _check_manager_project_access(project: SiteProject, current_user: User) -> None:
    role = _normalize_role(getattr(current_user, "role", ""))
    if role == UserRole.ADMIN.value:
        return

    is_assigned_manager = any(str(manager.id) == str(current_user.id) for manager in project.managers)
    if not is_assigned_manager:
        raise HTTPException(status_code=403, detail="You don't have access to this project")


def _check_sub_project_access(project: SiteProject, current_sub: Subcontractor) -> None:
    is_assigned_sub = any(str(sub.id) == str(current_sub.id) for sub in project.subcontractors)
    if not is_assigned_sub:
        raise HTTPException(status_code=403, detail="You are not assigned to this project")


@router.get("/{project_id}")
def get_lookahead(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> dict:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)

    from ...models.programme import ProgrammeUpload

    snapshot = get_latest_snapshot(project_id, db)
    latest_upload = (
        db.query(ProgrammeUpload)
        .filter(ProgrammeUpload.project_id == project_id, ProgrammeUpload.status == "committed")
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )
    # Recalculate if no snapshot exists, or if the snapshot is from an older upload.
    if not snapshot or (latest_upload and snapshot.programme_upload_id != latest_upload.id):
        snapshot = calculate_lookahead_for_project(project_id, db)
    if not snapshot:
        return {"project_id": str(project_id), "rows": [], "message": "No committed programme available yet."}

    return {
        "project_id": str(project_id),
        "snapshot_id": str(snapshot.id),
        "snapshot_date": snapshot.snapshot_date.isoformat(),
        "timezone": snapshot.data.get("timezone") if snapshot.data else None,
        "rows": snapshot.data.get("rows", []) if snapshot.data else [],
    }


@router.get("/{project_id}/alerts")
def get_lookahead_alerts(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> dict:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)

    from ...models.programme import ProgrammeUpload

    snapshot = get_latest_snapshot(project_id, db)
    latest_upload = (
        db.query(ProgrammeUpload)
        .filter(ProgrammeUpload.project_id == project_id, ProgrammeUpload.status == "committed")
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )
    if not snapshot or (latest_upload and snapshot.programme_upload_id != latest_upload.id):
        snapshot = calculate_lookahead_for_project(project_id, db)
    if not snapshot:
        return {"project_id": str(project_id), "alerts": {}}

    return {
        "project_id": str(project_id),
        "snapshot_id": str(snapshot.id),
        "snapshot_date": snapshot.snapshot_date.isoformat(),
        "alerts": snapshot.anomaly_flags or {},
    }


@router.get("/{project_id}/sub/{sub_id}")
def get_subcontractor_lookahead(
    project_id: UUID,
    sub_id: UUID,
    db: Session = Depends(get_db),
    current_sub: Subcontractor = Depends(require_role([UserRole.SUBCONTRACTOR])),
) -> dict:
    project = _check_project_exists(project_id, db)
    _check_sub_project_access(project, current_sub)

    if str(current_sub.id) != str(sub_id):
        raise HTTPException(status_code=403, detail="You can only view your own lookahead data")

    snapshot = get_latest_snapshot(project_id, db)
    notifications = get_sub_notifications(project_id, sub_id, db)

    return {
        "project_id": str(project_id),
        "sub_id": str(sub_id),
        "snapshot_date": snapshot.snapshot_date.isoformat() if snapshot else None,
        "timezone": (snapshot.data or {}).get("timezone") if snapshot else None,
        "rows": (snapshot.data or {}).get("rows", []) if snapshot else [],
        "notifications": [
            {
                "id": str(n.id),
                "activity_id": str(n.activity_id),
                "asset_type": n.asset_type,
                "trigger_type": n.trigger_type,
                "status": n.status,
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                "acted_at": n.acted_at.isoformat() if n.acted_at else None,
                "booking_id": str(n.booking_id) if n.booking_id else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
    }


@router.get("/{project_id}/sub-asset-suggestions")
def get_subcontractor_asset_suggestions(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> dict:
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

    from ...models.programme import ProgrammeUpload

    snapshot = get_latest_snapshot(project_id, db)
    latest_upload = (
        db.query(ProgrammeUpload)
        .filter(ProgrammeUpload.project_id == project_id, ProgrammeUpload.status == "committed")
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )
    if not snapshot or (latest_upload and snapshot.programme_upload_id != latest_upload.id):
        snapshot = calculate_lookahead_for_project(project_id, db)

    suggestions = get_sub_asset_suggestions_for_project(project_id, db)

    return {
        "project_id": str(project_id),
        "snapshot_date": snapshot.snapshot_date.isoformat() if snapshot else None,
        "suggestions": suggestions,
    }


@router.get("/{project_id}/history")
def get_lookahead_history(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
) -> dict:
    project = _check_project_exists(project_id, db)
    _check_manager_project_access(project, _)

    snapshots = get_snapshot_history(project_id, db)
    return {
        "project_id": str(project_id),
        "history": [
            {
                "snapshot_id": str(s.id),
                "snapshot_date": s.snapshot_date.isoformat(),
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "anomaly_flags": s.anomaly_flags or {},
            }
            for s in snapshots
        ],
    }
