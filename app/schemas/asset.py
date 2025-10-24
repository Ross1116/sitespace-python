from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import TYPE_CHECKING, Optional, List, Annotated
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal

from .base import BaseSchema, TimestampSchema
from .enums import AssetStatus

# Remove or comment out the TYPE_CHECKING import since you're not using it
# if TYPE_CHECKING:
#     from .slot_booking import BookingConflictResponse

class AssetBase(BaseSchema):
    """Base asset schema"""
    asset_code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    type: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    purchase_date: Optional[date] = None
    purchase_value: Optional[Decimal] = Field(None, ge=0)
    current_value: Optional[Decimal] = Field(None, ge=0)
    
    @field_validator('purchase_value', 'current_value', mode='before')
    def round_decimal(cls, v):
        if v is not None:
            return round(Decimal(str(v)), 2)
        return v
    
    @field_validator('current_value')
    def validate_current_value(cls, v, info):
        if v and info.data.get('purchase_value'):
            if v > info.data['purchase_value']:
                raise ValueError('Current value cannot exceed purchase value')
        return v

class AssetCreate(AssetBase):
    """Asset creation schema"""
    project_id: UUID
    status: AssetStatus = AssetStatus.AVAILABLE

class AssetUpdate(BaseSchema):
    """Asset update schema"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    type: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    current_value: Optional[Decimal] = Field(None, ge=0)
    status: Optional[AssetStatus] = None
    
    @field_validator('current_value', mode='before')
    def round_decimal(cls, v):
        if v is not None:
            return round(Decimal(str(v)), 2)
        return v

class AssetTransfer(BaseSchema):
    """Asset transfer between projects"""
    new_project_id: UUID
    transfer_date: date = Field(default_factory=date.today)
    reason: Optional[str] = None

class AssetResponse(AssetBase, TimestampSchema):
    """Asset response schema"""
    id: UUID
    project_id: UUID
    status: AssetStatus

class AssetBriefResponse(BaseSchema):
    """Brief asset info"""
    id: UUID
    asset_code: str
    name: str
    type: Optional[str]
    status: AssetStatus

class MaintenanceRecord(BaseSchema):
    """Maintenance record for asset"""
    date: date
    type: str  # routine, repair, inspection
    description: str
    cost: Optional[Decimal] = None
    performed_by: str
    
    @field_validator('cost', mode='before')
    def round_cost(cls, v):
        if v is not None:
            return round(Decimal(str(v)), 2)
        return v

class BookingConflict(BaseSchema):
    """Booking conflict details"""
    booking_id: UUID
    start_time: str
    end_time: str
    booked_by: str

class AssetDetailResponse(AssetResponse):
    """Detailed asset response"""
    project_name: str
    project_code: str
    total_bookings: int = 0
    active_bookings: int = 0
    utilization_rate: float = 0.0  # Percentage of time booked
    maintenance_history: List[MaintenanceRecord] = []

class AssetListResponse(BaseSchema):
    """Asset list response"""
    assets: List[AssetResponse]
    total: int
    skip: int
    limit: int
    has_more: bool

class AssetAvailabilityCheck(BaseSchema):
    """Check asset availability"""
    asset_id: UUID
    date: date
    start_time: str  # HH:MM format
    end_time: str    # HH:MM format

class AssetAvailabilityResponse(BaseModel):
    available: bool
    conflicts: List[BookingConflict] = []  # Use the locally defined BookingConflict
    message: Optional[str] = None
    
    class Config:
        from_attributes = True

# Only rebuild if needed for forward references within this file
# AssetDetailResponse.model_rebuild()  # Not needed since MaintenanceRecord is defined before it