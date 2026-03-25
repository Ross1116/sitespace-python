from sqlalchemy.orm import Session
from typing import List, Optional

from ..models.asset_type import AssetType
from ..schemas.asset_type import AssetTypeCreate, AssetTypeUpdate


def get_all(db: Session, *, active_only: bool = False) -> List[AssetType]:
    """Return all asset types, optionally filtered to active only."""
    query = db.query(AssetType)
    if active_only:
        query = query.filter(AssetType.is_active.is_(True))
    return query.order_by(AssetType.display_name).all()


def get_selectable(db: Session) -> List[AssetType]:
    """Return active, user-selectable asset types (for dropdowns)."""
    return (
        db.query(AssetType)
        .filter(AssetType.is_active.is_(True), AssetType.is_user_selectable.is_(True))
        .order_by(AssetType.display_name)
        .all()
    )


def get_by_code(db: Session, code: str) -> Optional[AssetType]:
    """Fetch a single asset type by its code PK."""
    return db.query(AssetType).filter(AssetType.code == code).first()


def get_active_codes(db: Session) -> frozenset[str]:
    """Return a frozenset of all active asset type codes.

    Used by validation layers that need the current allowed set.
    """
    rows = (
        db.query(AssetType.code)
        .filter(AssetType.is_active.is_(True))
        .all()
    )
    return frozenset(row[0] for row in rows)


def create(db: Session, obj_in: AssetTypeCreate) -> AssetType:
    """Insert a new asset type."""
    db_obj = AssetType(**obj_in.model_dump())
    db.add(db_obj)
    db.commit()
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


def get_max_hours(db: Session, code: str) -> Optional[float]:
    """Return max_hours_per_day for a given asset type code, or None if not found."""
    row = (
        db.query(AssetType.max_hours_per_day)
        .filter(AssetType.code == code, AssetType.is_active.is_(True))
        .first()
    )
    return float(row[0]) if row else None
