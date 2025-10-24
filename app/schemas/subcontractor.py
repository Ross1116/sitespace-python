from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal

from .base import BaseSchema, TimestampSchema
from .enums import TradeSpecialty
from .auth import PasswordMixin

class SubcontractorBase(BaseSchema):
    """Base subcontractor schema"""
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    company_name: Optional[str] = Field(None, max_length=255)
    trade_specialty: Optional[TradeSpecialty] = None
    phone: Optional[str] = Field(None, max_length=20)

class SubcontractorCreate(SubcontractorBase, PasswordMixin):
    """Subcontractor creation schema"""
    confirm_password: str
    
    @field_validator('confirm_password')
    def passwords_match(cls, v, info):
        if 'password' in info.data and v != info.data['password']:
            raise ValueError('Passwords do not match')
        return v

class SubcontractorUpdate(BaseSchema):
    """Subcontractor update schema"""
    email: Optional[EmailStr] = None
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    company_name: Optional[str] = Field(None, max_length=255)
    trade_specialty: Optional[TradeSpecialty] = None
    phone: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None

class SubcontractorResponse(SubcontractorBase, TimestampSchema):
    """Subcontractor response schema"""
    id: UUID
    is_active: bool

class SubcontractorDetailResponse(SubcontractorResponse):
    """Detailed subcontractor response"""
    active_projects_count: int = 0
    total_bookings: int = 0
    current_assignments: List['ProjectAssignmentResponse'] = []

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

class ProjectAssignmentResponse(BaseSchema):
    """Project assignment details for subcontractor"""
    project_id: UUID  # Changed from UUID to match new SiteProject id type
    project_name: str
    project_location: Optional[str] = None  # Added to match new model
    assigned_date: date
    hourly_rate: Optional[Decimal] = None
    is_active: bool
    
    @field_validator('hourly_rate', mode='before')
    def round_hourly_rate(cls, v):
        if v is not None:
            return round(Decimal(str(v)), 2)
        return v

# Avoid circular imports
SubcontractorDetailResponse.model_rebuild()