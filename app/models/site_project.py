from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from ..core.database import Base

class SiteProject(Base):
    __tablename__ = "site_projects"
    
    id = Column(Integer, primary_key=True, index=True)
    contractor_key = Column(String, unique=True, index=True)
    email_id = Column(String)
    contractor_project = Column(String)  # Store as JSON string for SQLite compatibility
    contractor_project_id = Column(String)
    contractor_name = Column(String)
    contractor_company = Column(String)
    contractor_trade = Column(String)
    contractor_email = Column(String)
    contractor_phone = Column(String)
    created_by = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<SiteProject(id={self.id}, contractor_name='{self.contractor_name}', company='{self.contractor_company}')>"
