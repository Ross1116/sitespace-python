from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class SlotBookingBase(BaseModel):
    project_id: int
    booking_title: str
    booking_for: Optional[str] = None
    booked_assets: Optional[List[str]] = None
    booking_status: Optional[str] = "pending"
    booking_time_dt: Optional[str] = None
    booking_duration_mins: Optional[int] = None
    booking_description: Optional[str] = None
    booking_notes: Optional[str] = None
    booking_created_by: Optional[str] = None

class SlotBookingCreate(SlotBookingBase):
    pass

class SlotBookingUpdate(BaseModel):
    project_id: Optional[int] = None
    booking_title: Optional[str] = None
    booking_for: Optional[str] = None
    booked_assets: Optional[List[str]] = None
    booking_status: Optional[str] = None
    booking_time_dt: Optional[str] = None
    booking_duration_mins: Optional[int] = None
    booking_description: Optional[str] = None
    booking_notes: Optional[str] = None
    booking_created_by: Optional[str] = None

class SlotBookingResponse(SlotBookingBase):
    id: int
    booking_key: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class SlotBookingListResponse(BaseModel):
    success: bool
    message: str
    data: List[SlotBookingResponse]
