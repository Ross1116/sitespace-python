# schemas/subcontractor.py
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal

from .base import BaseSchema, TimestampSchema
from .enums import TradeSpecialty
from .auth import NewPasswordConfirmationMixin, PasswordConfirmationMixin

class SubcontractorBase(BaseSchema):
    """Base subcontractor schema"""
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    company_name: Optional[str] = Field(None, max_length=255)
    trade_specialty: Optional[TradeSpecialty] = None
    phone: Optional[str] = Field(None, max_length=20)

class SubcontractorCreate(SubcontractorBase, PasswordConfirmationMixin):
    """Subcontractor creation schema"""
    project_id: Optional[UUID] = None 

class SubcontractorUpdate(BaseSchema):
    """Subcontractor update schema"""
    email: Optional[EmailStr] = None
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    company_name: Optional[str] = Field(None, max_length=255)
    trade_specialty: Optional[TradeSpecialty] = None
    phone: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None

class SubcontractorPasswordUpdate(NewPasswordConfirmationMixin):
    """Schema for updating subcontractor password"""
    current_password: str

class SubcontractorResponse(SubcontractorBase, TimestampSchema):
    """Subcontractor response schema"""
    id: UUID
    is_active: bool

class ProjectAssignmentResponse(BaseSchema):
    """Project assignment details for subcontractor"""
    project_id: UUID  
    project_name: str
    project_location: Optional[str] = None  
    assigned_date: date
    hourly_rate: Optional[Decimal] = None
    is_active: bool
    
    @field_validator('hourly_rate', mode='before')
    def round_hourly_rate(cls, v):
        if v is not None:
            return round(Decimal(str(v)), 2)
        return v

class SubcontractorDetailResponse(SubcontractorResponse):
    """Detailed subcontractor response"""
    active_projects_count: int = 0
    total_bookings: int = 0
    current_assignments: List[ProjectAssignmentResponse] = []

class SubcontractorBriefResponse(BaseSchema):
    """Brief subcontractor info"""
    id: UUID
    email: EmailStr
    first_name: str
    last_name: str
    company_name: Optional[str]
    trade_specialty: Optional[TradeSpecialty]

class SubcontractorListResponse(BaseSchema):
    """Subcontractor list response"""
    subcontractors: List[SubcontractorResponse]
    total: int
    skip: int
    limit: int
    has_more: bool

class ManagerSubcontractorStatsResponse(BaseSchema):
    """Manager subcontractor statistics response"""
    total_subcontractors: int
    active_subcontractors: int
    inactive_subcontractors: int
    by_trade: dict


class BookingCountsByStatusResponse(BaseSchema):
    """Booking counts by status response"""
    subcontractor_id: UUID
    booking_counts: dict
    total: int


class SubcontractorAvailabilityResponse(BaseSchema):
    """Subcontractor availability check response"""
    subcontractor_id: UUID
    date: date
    is_available: bool
    existing_bookings: List[dict]
    conflicts: List[dict]


# Avoid circular imports
SubcontractorDetailResponse.model_rebuild()
