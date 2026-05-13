import re
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Optional

from ..models.asset_type import AssetType
from ..schemas.asset_type import (
    AssetTypeCreate,
    AssetTypeUpdate,
    ProjectAssetTypeCreate,
    ProjectAssetTypeUpdate,
)


_SLUG_RE = re.compile(r"[^a-z0-9_]+")


class InvalidAssetTypeNameError(ValueError):
    """Raised when a project-local asset type name cannot produce a valid slug."""


class DuplicateAssetTypeError(ValueError):
    """Raised when a project-local asset type conflicts with an existing type."""


def normalize_local_slug(value: str) -> str:
    slug = _SLUG_RE.sub("_", str(value or "").strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug:
        raise InvalidAssetTypeNameError("Asset type name must contain at least one letter or number")
    if not slug[0].isalpha():
        slug = f"asset_{slug}"
    return slug[:32]


def _local_code(project_id: UUID, slug: str, suffix: int | None = None) -> str:
    base = f"p_{str(project_id).replace('-', '')[:12]}_{slug}"
    if suffix is not None:
        base = f"{base}_{suffix}"
    return base[:50]


def get_all(db: Session, *, active_only: bool = False) -> List[AssetType]:
    """Return global asset types, optionally filtered to active only."""
    query = db.query(AssetType).filter(AssetType.scope == "global")
    if active_only:
        query = query.filter(AssetType.is_active.is_(True))
    return query.order_by(AssetType.display_name).all()


def effective_query(db: Session, project_id: UUID | None = None):
    query = db.query(AssetType)
    if project_id is None:
        return query.filter(AssetType.scope == "global")
    return query.filter(
        or_(
            AssetType.scope == "global",
            (AssetType.scope == "project") & (AssetType.project_id == project_id),
        )
    )


def get_selectable(db: Session, project_id: UUID | None = None) -> List[AssetType]:
    """Return active, user-selectable asset types for global or project scope."""
    return (
        effective_query(db, project_id)
        .filter(AssetType.is_active.is_(True), AssetType.is_user_selectable.is_(True))
        .order_by(AssetType.scope, AssetType.display_name)
        .all()
    )


def get_by_code(db: Session, code: str) -> Optional[AssetType]:
    """Fetch a single asset type by its code PK."""
    return db.query(AssetType).filter(AssetType.code == code).first()


def get_active_codes(db: Session) -> frozenset[str]:
    """Return active global asset type codes.

    Used by validation layers that do not have project context.
    """
    rows = (
        db.query(AssetType.code)
        .filter(AssetType.scope == "global", AssetType.is_active.is_(True))
        .all()
    )
    return frozenset(row[0] for row in rows)


def get_effective_active_codes(db: Session, project_id: UUID | None) -> frozenset[str]:
    """Return active global + project-local codes for a project."""
    rows = (
        effective_query(db, project_id)
        .with_entities(AssetType.code)
        .filter(AssetType.is_active.is_(True))
        .all()
    )
    return frozenset(row[0] for row in rows)


def is_global_code(db: Session, code: str) -> bool:
    row = db.query(AssetType.scope).filter(AssetType.code == code).first()
    return bool(row and row[0] == "global")


def create(db: Session, obj_in: AssetTypeCreate) -> AssetType:
    """Insert a new asset type."""
    db_obj = AssetType(**obj_in.model_dump())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def create_project_local(
    db: Session,
    *,
    project_id: UUID,
    obj_in: ProjectAssetTypeCreate,
    created_by_user_id: UUID | None = None,
) -> AssetType:
    """Insert a project-local asset type with a generated globally unique code."""
    slug = normalize_local_slug(obj_in.display_name)
    existing_slug = (
        db.query(AssetType)
        .filter(
            AssetType.scope == "project",
            AssetType.project_id == project_id,
            AssetType.local_slug == slug,
        )
        .first()
    )
    if existing_slug is not None:
        raise DuplicateAssetTypeError("A local asset type with this name already exists for this project")

    suffix: int | None = None
    code = _local_code(project_id, slug)
    while get_by_code(db, code) is not None:
        suffix = 2 if suffix is None else suffix + 1
        code = _local_code(project_id, slug, suffix)

    db_obj = AssetType(
        code=code,
        display_name=obj_in.display_name.strip(),
        description=(obj_in.description or "").strip() or None,
        scope="project",
        project_id=project_id,
        local_slug=slug,
        parent_code=None,
        is_active=True,
        is_user_selectable=True,
        max_hours_per_day=obj_in.max_hours_per_day,
        taxonomy_version=1,
        created_by_user_id=created_by_user_id,
    )
    db.add(db_obj)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing_slug = (
            db.query(AssetType)
            .filter(
                AssetType.scope == "project",
                AssetType.project_id == project_id,
                AssetType.local_slug == slug,
            )
            .first()
        )
        if existing_slug is not None:
            raise DuplicateAssetTypeError("A local asset type with this name already exists for this project") from None
        existing_code = get_by_code(db, code)
        if existing_code is not None:
            raise DuplicateAssetTypeError("A local asset type code conflict occurred; please try again") from None
        raise
    db.refresh(db_obj)
    return db_obj


def update(db: Session, db_obj: AssetType, obj_in: AssetTypeUpdate) -> AssetType:
    """Partial update of an existing asset type."""
    update_data = obj_in.model_dump(exclude_unset=True)
    if update_data.get("parent_code") == db_obj.code:
        raise ValueError("Asset type cannot be its own parent")
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def update_project_local(
    db: Session,
    db_obj: AssetType,
    obj_in: ProjectAssetTypeUpdate,
) -> AssetType:
    """Update manager-editable fields on a project-local asset type."""
    if db_obj.scope != "project":
        raise ValueError("Only project-local asset types can be updated here")
    update_data = obj_in.model_dump(exclude_unset=True)
    if "display_name" in update_data:
        display_name = update_data.pop("display_name")
        if display_name is None or str(display_name).strip() == "":
            raise InvalidAssetTypeNameError("Asset type name must contain at least one letter or number")
        slug = normalize_local_slug(str(display_name))
        conflict = (
            db.query(AssetType)
            .filter(
                AssetType.scope == "project",
                AssetType.project_id == db_obj.project_id,
                AssetType.local_slug == slug,
                AssetType.code != db_obj.code,
            )
            .first()
        )
        if conflict is not None:
            raise DuplicateAssetTypeError("A local asset type with this name already exists for this project")
        db_obj.local_slug = slug
        db_obj.display_name = str(display_name).strip()
    if "description" in update_data:
        raw_description = update_data.pop("description")
        if raw_description is None:
            db_obj.description = None
        else:
            db_obj.description = str(raw_description).strip() or None
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateAssetTypeError("A local asset type with this name already exists for this project") from None
    db.refresh(db_obj)
    return db_obj


def get_max_hours(db: Session, code: str) -> Optional[float]:
    """Return max_hours_per_day for a given asset type code, or None if not found."""
    row = (
        db.query(AssetType.max_hours_per_day)
        .filter(AssetType.code == code, AssetType.is_active.is_(True))
        .first()
    )
    return float(row[0]) if row else None
