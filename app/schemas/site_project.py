from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date
from uuid import UUID

from .base import BaseSchema, TimestampSchema
from .user import UserBriefResponse
from .subcontractor import SubcontractorBriefResponse  # Import from subcontractor schema
from .enums import ProjectStatus  # You might want to create/update this enum


class SiteProjectFilters(BaseSchema):
    """Explicit project-list filters shared by routes and CRUD helpers."""

    name: Optional[str] = None
    location: Optional[str] = None
    status: Optional[ProjectStatus] = None
    start_date_from: Optional[date] = None
    start_date_to: Optional[date] = None
    end_date_from: Optional[date] = None
    end_date_to: Optional[date] = None
    user_id: Optional[UUID] = None


class SiteProjectBase(BaseSchema):
    """Base site project schema"""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    location: Optional[str] = Field(None, max_length=255)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[ProjectStatus] = Field(default=ProjectStatus.ACTIVE)
    timezone: Optional[str] = Field(None, max_length=64)

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
    timezone: Optional[str] = Field(None, max_length=64)
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
    is_active: bool = True

class ProjectSubcontractorCreate(ProjectSubcontractorBase):
    """Assign subcontractor to project"""
    subcontractor_id: UUID

class ProjectSubcontractorUpdate(BaseSchema):
    """Update subcontractor assignment"""
    end_date: Optional[date] = None
    is_active: Optional[bool] = None

class ProjectSubcontractorResponse(ProjectSubcontractorBase, TimestampSchema):
    """Project subcontractor assignment response"""
    id: UUID
    project_id: UUID  # Changed from int to UUID
    subcontractor: SubcontractorBriefResponse  # Changed from UserBriefResponse
    assigned_date: date
    end_date: Optional[date] = None


class ProjectStatisticsResponse(BaseSchema):
    """Project statistics response"""
    project_id: str
    project_name: str
    total_managers: int
    total_subcontractors: int
    total_assets: int = 0
    total_bookings: int = 0
    status: Optional[str] = None


class PlanningCompletenessCountsResponse(BaseSchema):
    unknown_assets: int
    inferred_assets: int
    confirmed_assets: int
    unknown_trades: int
    suggested_trades: int
    confirmed_trades: int
    blocking_unknown_assets: int
    blocking_unknown_trades: int


class PlanningCompletenessTaskResponse(BaseSchema):
    kind: str
    severity: str
    entity_type: str
    entity_id: UUID
    title: str
    suggested_value: Optional[str] = None
    blocking: bool
    affects_next_6_weeks: bool


class PlanningCompletenessResponse(BaseSchema):
    project_id: UUID
    score: int
    status: str
    window_start: date
    window_end: date
    counts: PlanningCompletenessCountsResponse
    tasks: List[PlanningCompletenessTaskResponse]
