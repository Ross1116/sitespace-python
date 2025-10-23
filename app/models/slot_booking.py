from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base
import json

class SlotBooking(Base):
    __tablename__ = "slot_bookings"
    
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    project = relationship("Project", back_populates="bookings")

    booking_title = Column(String, nullable=False)
    booking_for = Column(String)
    booked_assets = Column(Text)
    booking_status = Column(String, default="pending")
    booking_time_dt = Column(String)
    booking_duration_mins = Column(Integer)
    booking_description = Column(Text)
    booking_notes = Column(Text)
    booking_created_by = Column(String)
    booking_key = Column(String, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    project = relationship("SiteProject", back_populates="bookings")
    def __repr__(self):
        return f"<SlotBooking(id={self.id}, title='{self.booking_title}', project='{self.project_id}')>"
