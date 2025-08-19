from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class AssetBase(BaseModel):
    asset_project: str
    asset_title: str
    asset_location: Optional[str] = None
    asset_status: Optional[str] = "active"
    asset_poc: Optional[str] = None
    maintenance_start_dt: Optional[str] = None
    maintenance_end_dt: Optional[str] = None
    usage_instructions: Optional[str] = None
    asset_key: Optional[str] = None

class AssetCreate(AssetBase):
    pass

class AssetUpdate(BaseModel):
    asset_project: Optional[str] = None
    asset_title: Optional[str] = None
    asset_location: Optional[str] = None
    asset_status: Optional[str] = None
    asset_poc: Optional[str] = None
    maintenance_start_dt: Optional[str] = None
    maintenance_end_dt: Optional[str] = None
    usage_instructions: Optional[str] = None
    asset_key: Optional[str] = None

class AssetResponse(AssetBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class AssetListResponse(BaseModel):
    success: bool = True
    data: list[AssetResponse]
    message: Optional[str] = None
