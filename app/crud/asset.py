from sqlalchemy.orm import Session
from typing import Optional, List
from ..models.asset import Asset
from ..schemas.asset import AssetCreate, AssetUpdate

def get_asset(db: Session, asset_id: int) -> Optional[Asset]:
    return db.query(Asset).filter(Asset.id == asset_id).first()

def get_asset_by_key(db: Session, asset_key: str) -> Optional[Asset]:
    return db.query(Asset).filter(Asset.asset_key == asset_key).first()

def get_assets_by_project(db: Session, project_id: int) -> List[Asset]:
    return db.query(Asset).filter(Asset.project_id == project_id).all()

def get_assets(db: Session, skip: int = 0, limit: int = 100) -> List[Asset]:
    return db.query(Asset).offset(skip).limit(limit).all()

def create_asset(db: Session, asset: AssetCreate) -> Asset:
    db_asset = Asset(
        project_id=asset.project_id,
        asset_title=asset.asset_title,
        asset_location=asset.asset_location,
        asset_status=asset.asset_status,
        asset_poc=asset.asset_poc,
        maintenance_start_dt=asset.maintenance_start_dt,
        maintenance_end_dt=asset.maintenance_end_dt,
        usage_instructions=asset.usage_instructions,
        asset_key=asset.asset_key
    )
    db.add(db_asset)
    db.commit()
    db.refresh(db_asset)
    return db_asset

def update_asset(db: Session, asset_id: int, asset_update: AssetUpdate) -> Optional[Asset]:
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return None
    
    update_data = asset_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_asset, field, value)
    
    db.commit()
    db.refresh(db_asset)
    return db_asset

def delete_asset(db: Session, asset_id: int) -> bool:
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return False
    
    db.delete(db_asset)
    db.commit()
    return True
