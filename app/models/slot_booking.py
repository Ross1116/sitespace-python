from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from ..core.database import Base
import json

class SlotBooking(Base):
    __tablename__ = "slot_bookings"
    
    id = Column(Integer, primary_key=True, index=True)
    booking_project = Column(String, nullable=False)
    booking_title = Column(String, nullable=False)
    booking_for = Column(String)
    booked_assets = Column(Text)  # Store as JSON string for SQLite compatibility
    booking_status = Column(String, default="pending")
    booking_time_dt = Column(String)
    booking_duration_mins = Column(Integer)
    booking_description = Column(Text)
    booking_notes = Column(Text)
    booking_created_by = Column(String)
    booking_key = Column(String, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<SlotBooking(id={self.id}, title='{self.booking_title}', project='{self.booking_project}')>"
