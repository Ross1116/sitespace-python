from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base
import json

class SlotBooking(Base):
    __tablename__ = "slot_bookings"
    
    id = Column(Integer, primary_key=True, index=True)
    # Primary link to the main projects table
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # NEW: Secondary, optional link to SiteProject
    site_project_id = Column(Integer, ForeignKey("site_projects.id", ondelete="SET NULL"), nullable=True)

    # Primary relationship (links to Project)
    project = relationship("Project", back_populates="bookings")
    # Secondary relationship (links to SiteProject) - MUST have a unique name
    site_project_ref = relationship("SiteProject", back_populates="bookings_ref")

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
    
    def __repr__(self):
        return f"<SlotBooking(id={self.id}, title='{self.booking_title}', project='{self.project_id}')>"
