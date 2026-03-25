from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, AliasChoices
from typing import Optional, List
from datetime import date, time
from uuid import UUID
from decimal import Decimal

from .base import BaseSchema, TimestampSchema
from .enums import AssetStatus


class AssetBase(BaseSchema):
    """Base asset schema"""
    asset_code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    type: Optional[str] = Field(
        None, max_length=100,
        validation_alias=AliasChoices('type', 'asset_type')
    )
    description: Optional[str] = None
    purchase_date: Optional[date] = None
    purchase_value: Optional[Decimal] = Field(None, ge=0)
    current_value: Optional[Decimal] = Field(None, ge=0)
    pending_booking_capacity: int = Field(5, ge=1, le=20)

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
    maintenance_start_date: Optional[date] = None
    maintenance_end_date: Optional[date] = None

    @model_validator(mode='after')
    def validate_maintenance_window(self):
        """Enforce both-or-none and ordering for maintenance dates.

        This catches cases the field_validator cannot: e.g. providing
        only one of the two dates, which would leave the model in an
        invalid half-set state.
        """
        if (self.maintenance_start_date is None) != (self.maintenance_end_date is None):
            raise ValueError(
                'Both maintenance_start_date and maintenance_end_date are required together'
            )
        if self.maintenance_start_date and self.maintenance_end_date:
            if self.maintenance_end_date < self.maintenance_start_date:
                raise ValueError(
                    'maintenance_end_date must be >= maintenance_start_date'
                )
        return self


class AssetUpdate(BaseSchema):
    """Asset update schema — all fields optional for partial updates.

    Uses ``model_dump(exclude_unset=True)`` in the CRUD layer so only
    fields explicitly provided by the caller are written to the database.
    """
    asset_code: Optional[str] = Field(None, min_length=1, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    type: Optional[str] = Field(
        None, max_length=100,
        validation_alias=AliasChoices('type', 'asset_type')
    )
    description: Optional[str] = None
    current_value: Optional[Decimal] = Field(None, ge=0)
    pending_booking_capacity: Optional[int] = Field(None, ge=1, le=20)
    status: Optional[AssetStatus] = None
    canonical_type: Optional[str] = Field(None, max_length=50)
    project_id: Optional[UUID] = None
    maintenance_start_date: Optional[date] = None
    maintenance_end_date: Optional[date] = None

    @field_validator('current_value', mode='before')
    def round_decimal(cls, v):
        if v is not None:
            return round(Decimal(str(v)), 2)
        return v

    @model_validator(mode='after')
    def validate_maintenance_window(self):
        """Enforce both-or-none and ordering for maintenance dates.

        In a partial update context (exclude_unset=True), a caller that
        sends only ``maintenance_start_date`` without
        ``maintenance_end_date`` would silently create a start > end
        mismatch against the existing DB value.  This validator catches
        that at the schema boundary so the CRUD layer never receives an
        inconsistent pair.

        Clearing both dates (setting both to ``None``) is explicitly
        allowed — that is how the CRUD layer removes a maintenance
        window.
        """
        start = self.maintenance_start_date
        end = self.maintenance_end_date

        # Both None is valid (clearing the window)
        if start is None and end is None:
            return self

        # One set without the other → ambiguous partial update
        if (start is None) != (end is None):
            raise ValueError(
                'Both maintenance_start_date and maintenance_end_date are required together'
            )

        # Both set — enforce ordering
        if end < start:
            raise ValueError(
                'maintenance_end_date must be >= maintenance_start_date'
            )

        return self


class AssetTransfer(BaseSchema):
    """Asset transfer between projects"""
    new_project_id: UUID
    transfer_date: date = Field(default_factory=date.today)
    reason: Optional[str] = None
    force_transfer: bool = False
    update_status: Optional[AssetStatus] = None
    update_bookings: bool = False


class AssetResponse(AssetBase, TimestampSchema):
    """Asset response schema"""
    id: UUID
    project_id: UUID
    status: AssetStatus
    canonical_type: Optional[str] = None
    maintenance_start_date: Optional[date] = None
    maintenance_end_date: Optional[date] = None
    pending_booking_capacity: int = 5


class AssetBriefResponse(BaseSchema):
    """Brief asset info"""
    id: UUID
    asset_code: str
    name: str
    type: Optional[str] = None
    canonical_type: Optional[str] = None
    status: AssetStatus
    pending_booking_capacity: int = 5


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
    status: Optional[str] = None


class RecentBookingSummary(BaseSchema):
    """Summary of a recent booking for asset detail views"""
    id: UUID
    booking_date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    subcontractor_id: Optional[UUID] = None
    status: Optional[str] = None


class AssetDetailResponse(AssetResponse):
    """Detailed asset response with statistics and history"""
    project_name: Optional[str] = None
    project_location: Optional[str] = None
    total_bookings: int = 0
    active_bookings: int = 0
    completed_bookings: int = 0
    utilization_rate: float = 0.0
    depreciation_amount: Optional[float] = None
    depreciation_percentage: Optional[float] = None
    maintenance_history: List[MaintenanceRecord] = []
    recent_bookings: List[RecentBookingSummary] = []


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
    """Asset availability check result"""
    is_available: bool
    conflicts: List[BookingConflict] = []
    reason: Optional[str] = None
    asset_status: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class ImpactedBookingSummary(BaseSchema):
    """Booking summary used in asset status-change impact responses."""
    id: UUID
    booking_date: date
    start_time: time
    end_time: time
    status: str
    project_id: UUID
    manager_id: UUID
    subcontractor_id: Optional[UUID] = None


class AssetStatusChangeImpactResponse(BaseSchema):
    """Preview payload for bookings impacted by an asset status change."""
    asset_id: UUID
    target_status: AssetStatus
    maintenance_start_date: Optional[date] = None
    maintenance_end_date: Optional[date] = None
    requires_confirmation: bool
    total_impacted_bookings: int
    impacted_bookings: List[ImpactedBookingSummary] = []
    message: str