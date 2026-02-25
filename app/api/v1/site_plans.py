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
    return SitePlanResponse(
        id=plan.id,
        title=plan.title,
        project_id=plan.project_id,
        file=StoredFileBrief(
            id=plan.file.id,
            original_filename=plan.file.original_filename,
            content_type=plan.file.content_type,
            file_size=plan.file.file_size,
            preview_url=preview_url,
            image_url=image_url,
            raw_url=raw_url,
            created_at=plan.file.created_at,
        ),
        created_by=UserBriefResponse(
            id=plan.created_by.id,
            email=plan.created_by.email,
            first_name=plan.created_by.first_name,
            last_name=plan.created_by.last_name,
            role=plan.created_by.role,
        ),
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _load_plan(plan_id: UUID, db: Session) -> SitePlan:
    plan = (
        db.query(SitePlan)
        .options(joinedload(SitePlan.file), joinedload(SitePlan.created_by))
        .filter(SitePlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Site plan not found")
    return plan


@router.post("/", response_model=SitePlanResponse, status_code=status.HTTP_201_CREATED)
def create_site_plan(
    payload: SitePlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
):
    """
    Create a site plan. file_id must come from POST /api/files/upload.
    Each site plan exclusively owns its file — file_id cannot be reused across plans.
    """
    if not db.query(StoredFile).filter(StoredFile.id == payload.file_id).first():
        raise HTTPException(status_code=404, detail="File not found. Upload the file first via POST /api/files/upload")

    if db.query(SitePlan).filter(SitePlan.file_id == payload.file_id).first():
        raise HTTPException(status_code=409, detail="This file is already used by another site plan. Upload a fresh copy.")

    if not db.query(SiteProject).filter(SiteProject.id == payload.project_id).first():
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


@router.get("/", response_model=List[SitePlanResponse])
def list_site_plans(
    project_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """List site plans. Filter by project_id if provided."""
    q = db.query(SitePlan).options(joinedload(SitePlan.file), joinedload(SitePlan.created_by))
    if project_id:
        q = q.filter(SitePlan.project_id == project_id)
    return [_build_response(p) for p in q.order_by(SitePlan.created_at.desc()).all()]


@router.get("/{plan_id}", response_model=SitePlanResponse)
def get_site_plan(
    plan_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    return _build_response(_load_plan(plan_id, db))


@router.patch("/{plan_id}", response_model=SitePlanResponse)
def update_site_plan(
    plan_id: UUID,
    payload: SitePlanUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
):
    """
    Update title and/or replace file. Storage deletion happens after DB commit —
    a failed disk remove won't leave the DB inconsistent.
    """
    plan = _load_plan(plan_id, db)

    if payload.title is not None:
        plan.title = payload.title

    old_storage_path: Optional[str] = None

    if payload.file_id is not None and payload.file_id != plan.file_id:
        if not db.query(StoredFile).filter(StoredFile.id == payload.file_id).first():
            raise HTTPException(status_code=404, detail="New file not found. Upload it first via POST /api/files/upload")

        if db.query(SitePlan).filter(SitePlan.file_id == payload.file_id, SitePlan.id != plan_id).first():
            raise HTTPException(status_code=409, detail="This file is already used by another site plan. Upload a fresh copy.")

        old_file = plan.file
        no_other_refs = not db.query(SitePlan).filter(
            SitePlan.file_id == old_file.id, SitePlan.id != plan_id
        ).first()

        plan.file_id = payload.file_id

        if no_other_refs:
            old_storage_path = old_file.storage_path
            db.delete(old_file)

    db.commit()

    # Storage deletion after commit — if this fails, DB is already consistent.
    # Lingering files are handled by the future orphan-sweep job.
    if old_storage_path:
        storage.delete(old_storage_path)

    return _build_response(_load_plan(plan_id, db))


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site_plan(
    plan_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
):
    """Deletes the plan and its file. Storage deletion happens after DB commit."""
    plan = _load_plan(plan_id, db)
    file_record = plan.file

    no_other_refs = not db.query(SitePlan).filter(
        SitePlan.file_id == file_record.id, SitePlan.id != plan_id
    ).first()

    storage_path_to_delete: Optional[str] = file_record.storage_path if no_other_refs else None

    db.delete(plan)
    if storage_path_to_delete:
        db.delete(file_record)
    db.commit()

    if storage_path_to_delete:
        storage.delete(storage_path_to_delete)
