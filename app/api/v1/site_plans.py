from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from ...core.database import get_db
from ...core.security import get_current_active_user, require_role
from ...models.site_plan import SitePlan
from ...models.site_project import SiteProject
from ...models.stored_file import StoredFile
from ...models.user import User
from ...schemas.enums import UserRole
from ...schemas.site_plan import SitePlanCreate, SitePlanResponse, SitePlanUpdate
from ...schemas.stored_file import StoredFileBrief
from ...schemas.user import UserBriefResponse
from ...utils.storage import storage

router = APIRouter(prefix="/site-plans", tags=["Site Plans"])


def _file_urls(file_id: UUID) -> tuple[str, str, str]:
    base = f"/api/files/{file_id}"
    return f"{base}/preview", f"{base}/image", base


def _build_response(plan: SitePlan) -> SitePlanResponse:
    preview_url, image_url, raw_url = _file_urls(plan.file.id)
    file_brief = StoredFileBrief(
        id=plan.file.id,
        original_filename=plan.file.original_filename,
        content_type=plan.file.content_type,
        file_size=plan.file.file_size,
        preview_url=preview_url,
        image_url=image_url,
        raw_url=raw_url,
        created_at=plan.file.created_at,
    )
    creator = UserBriefResponse(
        id=plan.created_by.id,
        email=plan.created_by.email,
        first_name=plan.created_by.first_name,
        last_name=plan.created_by.last_name,
        role=plan.created_by.role,
    )
    return SitePlanResponse(
        id=plan.id,
        title=plan.title,
        project_id=plan.project_id,
        file=file_brief,
        created_by=creator,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _load_plan(plan_id: UUID, db: Session) -> SitePlan:
    plan = (
        db.query(SitePlan)
        .options(
            joinedload(SitePlan.file),
            joinedload(SitePlan.created_by),
        )
        .filter(SitePlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Site plan not found")
    return plan


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.post("/", response_model=SitePlanResponse, status_code=status.HTTP_201_CREATED)
def create_site_plan(
    payload: SitePlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
):
    """
    Create a site plan. Requires an already-uploaded file_id and an existing project_id.
    Each site plan exclusively owns its file — a file_id cannot be shared between plans.
    """
    file_record = db.query(StoredFile).filter(StoredFile.id == payload.file_id).first()
    if not file_record:
        raise HTTPException(
            status_code=404,
            detail="File not found. Upload the file first via POST /api/files/upload",
        )

    # Enforce one-file-per-plan: a StoredFile must not be shared across site plans
    existing_plan = db.query(SitePlan).filter(SitePlan.file_id == payload.file_id).first()
    if existing_plan:
        raise HTTPException(
            status_code=409,
            detail="This file is already used by another site plan. Upload a fresh copy.",
        )

    project = db.query(SiteProject).filter(SiteProject.id == payload.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    plan = SitePlan(
        title=payload.title,
        file_id=payload.file_id,
        project_id=payload.project_id,
        created_by_id=current_user.id,
    )
    db.add(plan)
    db.commit()

    return _build_response(_load_plan(plan.id, db))


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[SitePlanResponse])
def list_site_plans(
    project_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """List site plans. Optionally filter by project_id."""
    q = db.query(SitePlan).options(
        joinedload(SitePlan.file),
        joinedload(SitePlan.created_by),
    )
    if project_id:
        q = q.filter(SitePlan.project_id == project_id)
    plans = q.order_by(SitePlan.created_at.desc()).all()
    return [_build_response(p) for p in plans]


# ---------------------------------------------------------------------------
# Get single
# ---------------------------------------------------------------------------

@router.get("/{plan_id}", response_model=SitePlanResponse)
def get_site_plan(
    plan_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    return _build_response(_load_plan(plan_id, db))


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

@router.patch("/{plan_id}", response_model=SitePlanResponse)
def update_site_plan(
    plan_id: UUID,
    payload: SitePlanUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
):
    """
    Update a site plan's title and/or replace its file.
    When file_id is replaced, the old file is removed from the DB and disk —
    but storage deletion only happens AFTER the DB transaction commits successfully.
    """
    plan = _load_plan(plan_id, db)

    if payload.title is not None:
        plan.title = payload.title

    old_storage_path: Optional[str] = None

    if payload.file_id is not None and payload.file_id != plan.file_id:
        new_file = db.query(StoredFile).filter(StoredFile.id == payload.file_id).first()
        if not new_file:
            raise HTTPException(
                status_code=404,
                detail="New file not found. Upload it first via POST /api/files/upload",
            )

        # Enforce one-file-per-plan on the replacement file too
        other_plan = db.query(SitePlan).filter(
            SitePlan.file_id == payload.file_id,
            SitePlan.id != plan_id,
        ).first()
        if other_plan:
            raise HTTPException(
                status_code=409,
                detail="This file is already used by another site plan. Upload a fresh copy.",
            )

        old_file = plan.file
        # Check before we detach the relationship
        other_refs = db.query(SitePlan).filter(
            SitePlan.file_id == old_file.id,
            SitePlan.id != plan_id,
        ).first()

        plan.file_id = payload.file_id

        if not other_refs:
            # Capture path and delete from DB — storage deletion happens after commit below
            old_storage_path = old_file.storage_path
            db.delete(old_file)

    # Single commit: all DB changes land atomically
    db.commit()

    # Storage deletion only AFTER the DB commit succeeds.
    # If this fails, the DB is already consistent; the orphaned file can be
    # cleaned up by the background orphan-sweep job (future work).
    if old_storage_path:
        storage.delete(old_storage_path)

    return _build_response(_load_plan(plan_id, db))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site_plan(
    plan_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
):
    """
    Delete a site plan and its associated stored file.
    Storage deletion happens AFTER the DB transaction commits successfully.
    """
    plan = _load_plan(plan_id, db)
    file_record = plan.file

    # Determine whether the file will become orphaned after this plan is deleted
    other_refs = db.query(SitePlan).filter(
        SitePlan.file_id == file_record.id,
        SitePlan.id != plan_id,
    ).first()

    storage_path_to_delete: Optional[str] = None
    if not other_refs:
        storage_path_to_delete = file_record.storage_path

    # Delete plan (and file record if orphaned) in one transaction
    db.delete(plan)
    if storage_path_to_delete:
        db.delete(file_record)
    db.commit()

    # Storage deletion only AFTER successful DB commit
    if storage_path_to_delete:
        storage.delete(storage_path_to_delete)
