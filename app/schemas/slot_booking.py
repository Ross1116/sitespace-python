from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Any
from datetime import datetime, date, time
from uuid import UUID

from .base import BaseSchema, TimestampSchema
from .enums import BookingStatus
from .user import UserBriefResponse
from .subcontractor import SubcontractorBriefResponse
from .asset import AssetBriefResponse
from .site_project import SiteProjectBriefResponse
    
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
    # REMOVED date validation from here since it's inherited by response schemas

class BookingCreate(BookingBase):
    """Booking creation schema"""
    project_id: UUID
    manager_id: Optional[UUID] = None
    subcontractor_id: Optional[UUID] = None
    asset_id: UUID
    
    
    @field_validator('booking_date')
    def validate_booking_date(cls, v):
        """Only validate date for creation"""
        if v < date.today():
            raise ValueError('Cannot book for past dates')
        return v

class BookingUpdate(BaseSchema):
    """Booking update schema"""
    booking_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    status: Optional[BookingStatus] = None
    purpose: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    
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

class BookingResponse(BookingBase, TimestampSchema):
    """Booking response schema - no validation, just structure"""
    id: UUID
    project_id: UUID
    manager_id: UUID
    subcontractor_id: Optional[UUID] = None
    asset_id: UUID
    status: BookingStatus

class BookingDetailResponse(BookingResponse):
    """Detailed booking response"""
    project: SiteProjectBriefResponse
    manager: UserBriefResponse
    subcontractor: Optional[SubcontractorBriefResponse] = None
    asset: AssetBriefResponse

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
    completed_bookings: int
    cancelled_bookings: int
    utilization_rate: float
    busiest_day: Optional[date]
    most_booked_asset: Optional[AssetBriefResponse]

class BulkBookingCreate(BaseSchema):
    """Create multiple bookings"""
    project_id: UUID
    manager_id: UUID
    subcontractor_id: Optional[UUID] = None 
    asset_ids: List[UUID]
    booking_dates: List[date]
    start_time: time
    end_time: time
    purpose: Optional[str] = None
    
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
        # Validate that dates are not in the past
        today = date.today()
        for booking_date in v:
            if booking_date < today:
                raise ValueError(f'Cannot book for past date: {booking_date}')
        return v
    
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

class BookingConflictResponse(BaseSchema):
    """Booking conflict response"""
    has_conflict: bool
    conflicting_bookings: List[BookingResponse] = []