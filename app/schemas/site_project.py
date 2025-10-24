from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal

from .base import BaseSchema, TimestampSchema
from .user import UserBriefResponse
from .subcontractor import SubcontractorBriefResponse  # Import from subcontractor schema
from .enums import ProjectStatus  # You might want to create/update this enum

class SiteProjectBase(BaseSchema):
    """Base site project schema"""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    location: Optional[str] = Field(None, max_length=255)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[ProjectStatus] = Field(default=ProjectStatus.ACTIVE)

class SiteProjectCreate(SiteProjectBase):
    """Site project creation schema"""
    manager_ids: Optional[List[UUID]] = []
    subcontractor_ids: Optional[List[UUID]] = []

class SiteProjectUpdate(BaseSchema):
    """Site project update schema"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    location: Optional[str] = Field(None, max_length=255)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = Field(None, max_length=50)
    manager_ids: Optional[List[UUID]] = None
    subcontractor_ids: Optional[List[UUID]] = None

class SiteProjectResponse(SiteProjectBase, TimestampSchema):
    """Site project response schema"""
    id: UUID  # Changed from int to UUID

class SiteProjectDetailResponse(SiteProjectResponse):
    """Detailed site project response"""
    managers: List[UserBriefResponse] = []
    subcontractors: List[SubcontractorBriefResponse] = []  # Changed from UserBriefResponse
    assets_count: int = 0
    slot_bookings_count: int = 0

class SiteProjectBriefResponse(BaseSchema):
    """Brief site project info"""
    id: UUID  # Changed from int to UUID
    name: str
    location: Optional[str] = None
    status: Optional[str] = None

class SiteProjectListResponse(BaseSchema):
    """Site project list response"""
    projects: List[SiteProjectResponse]
    total: int
    skip: int
    limit: int
    has_more: bool

# Manager Assignment Schemas (remain mostly the same)
class ProjectManagerBase(BaseSchema):
    """Base project manager schema"""
    is_lead_manager: bool = False
    is_active: bool = True

class ProjectManagerCreate(ProjectManagerBase):
    """Add manager to project"""
    manager_id: UUID

class ProjectManagerResponse(ProjectManagerBase):
    """Project manager response"""
    id: UUID
    project_id: UUID  # Changed from int to UUID
    manager: UserBriefResponse
    assigned_date: date
    created_at: datetime

# Subcontractor Assignment Schemas
class ProjectSubcontractorBase(BaseSchema):
    """Base project subcontractor assignment"""
    hourly_rate: Optional[Decimal] = Field(None, ge=0)
    is_active: bool = True
    
    @field_validator('hourly_rate', mode='before')
    def round_hourly_rate(cls, v):
        if v is not None:
            return round(Decimal(str(v)), 2)
        return v

class ProjectSubcontractorCreate(ProjectSubcontractorBase):
    """Assign subcontractor to project"""
    subcontractor_id: UUID

class ProjectSubcontractorUpdate(BaseSchema):
    """Update subcontractor assignment"""
    hourly_rate: Optional[Decimal] = Field(None, ge=0)
    end_date: Optional[date] = None
    is_active: Optional[bool] = None
    
    @field_validator('hourly_rate', mode='before')
    def round_hourly_rate(cls, v):
        if v is not None:
            return round(Decimal(str(v)), 2)
        return v

class ProjectSubcontractorResponse(ProjectSubcontractorBase, TimestampSchema):
    """Project subcontractor assignment response"""
    id: UUID
    project_id: UUID  # Changed from int to UUID
    subcontractor: SubcontractorBriefResponse  # Changed from UserBriefResponse
    assigned_date: date
    end_date: Optional[date] = None