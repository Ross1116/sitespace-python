from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from ..core.database import Base

class SiteProject(Base):
    __tablename__ = "site_projects"
    
    id = Column(Integer, primary_key=True, index=True)
    contractor_key = Column(String, unique=True, index=True, nullable=True)
    email_id = Column(String, nullable=True)
    contractor_project = Column(Text, nullable=True)
    contractor_project_id = Column(String, nullable=True)
    contractor_name = Column(String, nullable=True)
    contractor_company = Column(String, nullable=True)
    contractor_trade = Column(String, nullable=True)
    contractor_email = Column(String, nullable=True)
    contractor_phone = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # CORRECTED: Unique back_populates name to link to Asset.site_project_ref
    assets_ref = relationship("Asset", back_populates="site_project_ref", cascade="all, delete-orphan") 
    
    # Assuming SlotBooking links to SiteProject via a unique relationship/FK
    bookings = relationship("SlotBooking", back_populates="site_project", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<SiteProject(id={self.id}, contractor_name='{self.contractor_name}', company='{self.contractor_company}')>"