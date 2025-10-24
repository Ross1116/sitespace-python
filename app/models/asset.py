from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base

class Asset(Base):
    __tablename__ = "assets"
    
    id = Column(Integer, primary_key=True, index=True)
    # Primary link: required, links to Project
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    
    # Secondary link: optional, links to SiteProject
    site_project_id = Column(Integer, ForeignKey("site_projects.id", ondelete="SET NULL"), nullable=True)

    # Relationship back to Project (back_populates is "assets")
    project = relationship("Project", back_populates="assets")

    # Relationship back to SiteProject (unique back_populates is "assets_ref")
    site_project_ref = relationship("SiteProject", back_populates="assets_ref")


    asset_title = Column(String, nullable=False)
    asset_location = Column(String)
    asset_status = Column(String, default="active")
    asset_poc = Column(String)
    maintenance_start_dt = Column(String)
    maintenance_end_dt = Column(String)
    usage_instructions = Column(Text)
    asset_key = Column(String, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<Asset(id={self.id}, title='{self.asset_title}', project='{self.project_id}')>"