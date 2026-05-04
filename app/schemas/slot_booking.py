from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Any
from datetime import datetime, date, time, timedelta
from uuid import UUID

from .base import BaseSchema, TimestampSchema
from .enums import BookingStatus
from .user import UserBriefResponse
from .subcontractor import SubcontractorBriefResponse
from .asset import AssetBriefResponse
from .site_project import SiteProjectBriefResponse


def _normalize_to_monday(value: Optional[date]) -> Optional[date]:
    if value is None:
        return None
    return value - timedelta(days=value.weekday())


class TimeSlot(BaseSchema):
    """Time slot validation"""
    start_time: time
    end_time: time
    
    @model_validator(mode='after')
    def validate_time_slot(self) -> 'TimeSlot':
        if self.end_time <= self.start_time:
            raise ValueError('End time must be after start time')
        return self


class BookingBase(TimeSlot):
    """Base booking schema - without date validation"""
    booking_date: date
    purpose: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None


class BookingCreate(BookingBase):
    """Booking creation schema"""
    project_id: UUID
    manager_id: Optional[UUID] = None
    subcontractor_id: Optional[UUID] = None
    asset_id: UUID
    programme_activity_id: Optional[UUID] = None
    selected_week_start: Optional[date] = None
    status: Optional[BookingStatus] = None
    comment: Optional[str] = Field(
        None, 
        max_length=1000, 
        description="Optional comment for audit trail"
    )

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v
    
    @field_validator('booking_date')
    def validate_booking_date(cls, v):
        """Only validate date for creation"""
        if v < date.today():
            raise ValueError('Cannot book for past dates')
        return v

    @field_validator("selected_week_start")
    @classmethod
    def normalize_selected_week_start(cls, value: Optional[date]) -> Optional[date]:
        return _normalize_to_monday(value)


class BookingUpdate(BaseSchema):
    """Booking update schema"""
    booking_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    subcontractor_id: Optional[UUID] = None
    asset_id: Optional[UUID] = None
    status: Optional[BookingStatus] = None
    purpose: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    comment: Optional[str] = Field(
        None, 
        max_length=1000, 
        description="Optional comment for audit trail"
    )

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v
    
    @field_validator('booking_date')
    def validate_booking_date(cls, v):
        """Only validate date if provided for update"""
        if v and v < date.today():
            raise ValueError('Cannot update booking to past date')
        return v
    
    @model_validator(mode='after')
    def validate_time_slot(self) -> 'BookingUpdate':
        """Validate time slot only if both times are provided"""
        if self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                raise ValueError('End time must be after start time')
        return self


class BookingStatusUpdate(BaseSchema):
    """Status update with optional comment"""
    status: BookingStatus
    comment: Optional[str] = Field(
        None, 
        max_length=1000, 
        description="Reason for status change"
    )
    
    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v


class BookingDeleteRequest(BaseSchema):
    """Delete request with optional reason"""
    hard_delete: bool = Field(
        default=False, 
        description="Permanently delete instead of soft delete (cancel)"
    )
    comment: Optional[str] = Field(
        None, 
        max_length=1000, 
        description="Reason for deletion/cancellation"
    )


class BookingDuplicateRequest(BaseSchema):
    """Duplicate booking request"""
    new_date: date = Field(..., description="Date for the duplicated booking")
    comment: Optional[str] = Field(
        None, 
        max_length=1000, 
        description="Optional comment for audit trail"
    )
    
    @field_validator('new_date')
    def validate_new_date(cls, v):
        """Validate that new date is not in the past"""
        if v < date.today():
            raise ValueError('Cannot duplicate to a past date')
        return v


class BookingResponse(BookingBase, TimestampSchema):
    """Booking response schema - no validation, just structure"""
    id: UUID
    project_id: UUID
    manager_id: UUID
    subcontractor_id: Optional[UUID] = None
    asset_id: UUID
    status: BookingStatus
    source: Optional[str] = None
    booking_group_id: Optional[UUID] = None
    programme_activity_id: Optional[UUID] = None
    programme_activity_name: Optional[str] = None
    expected_asset_type: Optional[str] = None
    is_modified: bool = False


class BookingDetailResponse(BookingResponse):
    """Detailed booking response"""
    project: SiteProjectBriefResponse
    manager: UserBriefResponse
    subcontractor: Optional[SubcontractorBriefResponse] = None
    asset: AssetBriefResponse
    competing_pending_count: int = 0


class BookingListResponse(BaseSchema):
    """Booking list response"""
    bookings: List[BookingDetailResponse]
    total: int
    skip: int
    limit: int
    has_more: bool


class BookingFilterParams(BaseSchema):
    """Booking filter parameters"""
    project_id: Optional[UUID] = None
    manager_id: Optional[UUID] = None
    subcontractor_id: Optional[UUID] = None
    asset_id: Optional[UUID] = None
    status: Optional[BookingStatus] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    
    @model_validator(mode='after')
    def validate_date_range(self) -> 'BookingFilterParams':
        if self.date_from and self.date_to:
            if self.date_to < self.date_from:
                raise ValueError('date_to must be after date_from')
        return self


class BookingCalendarView(BaseSchema):
    """Calendar view of bookings"""
    date: date
    bookings: List[BookingDetailResponse]


class BookingStatistics(BaseSchema):
    """Booking statistics"""
    total_bookings: int
    pending_bookings: int
    confirmed_bookings: int
    in_progress_bookings: int = 0
    completed_bookings: int
    cancelled_bookings: int
    denied_bookings: int = 0
    utilization_rate: float
    busiest_day: Optional[date] = None
    busiest_day_count: int = 0
    most_booked_asset: Optional[Any] = None  # Can be dict or AssetBriefResponse
    period: Optional[dict] = None


class BulkBookingCreate(BaseSchema):
    """Create multiple bookings"""
    project_id: UUID
    manager_id: Optional[UUID] = None
    subcontractor_id: Optional[UUID] = None 
    asset_ids: List[UUID]
    booking_dates: List[date]
    programme_activity_id: Optional[UUID] = None
    selected_week_start: Optional[date] = None
    start_time: time
    end_time: time
    purpose: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    comment: Optional[str] = Field(
        None, 
        max_length=1000, 
        description="Optional comment for audit trail (applies to all created bookings)"
    )
    
    @field_validator('asset_ids')
    def validate_assets(cls, v):
        if len(v) == 0:
            raise ValueError('At least one asset must be selected')
        if len(v) != len(set(v)):
            raise ValueError('Duplicate assets not allowed')
        return v
    
    @field_validator('booking_dates')
    def validate_dates(cls, v):
        if len(v) == 0:
            raise ValueError('At least one date must be selected')
        if len(v) != len(set(v)):
            raise ValueError('Duplicate dates not allowed')
        today = date.today()
        for booking_date in v:
            if booking_date < today:
                raise ValueError(f'Cannot book for past date: {booking_date}')
        return v

    @field_validator("selected_week_start")
    @classmethod
    def normalize_selected_week_start(cls, value: Optional[date]) -> Optional[date]:
        return _normalize_to_monday(value)
    
    @model_validator(mode='after')
    def validate_time_slot(self) -> 'BulkBookingCreate':
        if self.end_time <= self.start_time:
            raise ValueError('End time must be after start time')
        return self


class BookingConflictCheck(BaseSchema):
    """Check for booking conflicts"""
    asset_id: UUID
    booking_date: date
    start_time: time
    end_time: time
    exclude_booking_id: Optional[UUID] = None
    
    @model_validator(mode='after')
    def validate_time_slot(self) -> 'BookingConflictCheck':
        if self.end_time <= self.start_time:
            raise ValueError('End time must be after start time')
        return self


class BookingConflictResponse(BaseSchema):
    """Booking conflict response"""
    has_conflict: bool
    has_confirmed_conflict: bool = False
    pending_count: int = 0
    pending_capacity: int = 5
    can_request: bool = True
    conflicting_bookings: List[BookingResponse] = Field(default_factory=list)
    conflict_count: int = 0


class BulkRescheduleItem(TimeSlot):
    """Target slot for one existing booking in a bulk reschedule."""
    booking_id: UUID
    booking_date: date
    asset_id: Optional[UUID] = None
    subcontractor_id: Optional[UUID] = None

    @field_validator("booking_date")
    @classmethod
    def validate_booking_date(cls, value: date) -> date:
        if value < date.today():
            raise ValueError("Cannot reschedule bookings to past dates")
        return value


class BulkRescheduleRequest(BaseSchema):
    """Preview or apply exact target slots for selected existing bookings."""
    project_id: UUID
    items: List[BulkRescheduleItem]
    allow_non_working_days: bool = False
    allow_outside_working_hours: bool = False
    comment: Optional[str] = Field(None, max_length=1000)

    @field_validator("items")
    @classmethod
    def validate_items(cls, value: List[BulkRescheduleItem]) -> List[BulkRescheduleItem]:
        if not value:
            raise ValueError("At least one booking must be selected")
        booking_ids = [item.booking_id for item in value]
        if len(booking_ids) != len(set(booking_ids)):
            raise ValueError("Duplicate booking IDs are not allowed")
        return value


class BulkRescheduleBookingSnapshot(BaseSchema):
    booking_id: UUID
    project_id: UUID
    asset_id: UUID
    subcontractor_id: Optional[UUID] = None
    booking_date: date
    start_time: time
    end_time: time
    status: BookingStatus


class BulkRescheduleIssue(BaseSchema):
    code: str
    message: str
    field: Optional[str] = None


class BulkRescheduleItemResult(BaseSchema):
    booking_id: UUID
    original: Optional[BulkRescheduleBookingSnapshot] = None
    target: Optional[BulkRescheduleBookingSnapshot] = None
    work_days_per_week: int = 5
    work_days_source: str = "default"
    holiday_region_code: Optional[str] = None
    holiday_region_source: Optional[str] = None
    errors: List[BulkRescheduleIssue] = Field(default_factory=list)
    warnings: List[BulkRescheduleIssue] = Field(default_factory=list)
    conflicts: List[BookingResponse] = Field(default_factory=list)


class BulkRescheduleSummary(BaseSchema):
    total: int
    valid: int
    invalid: int
    warnings: int


class BulkRescheduleValidationResponse(BaseSchema):
    can_apply: bool
    summary: BulkRescheduleSummary
    items: List[BulkRescheduleItemResult]


class BulkRescheduleApplyResponse(BaseSchema):
    validation: BulkRescheduleValidationResponse
    bookings: List[BookingDetailResponse] = Field(default_factory=list)
