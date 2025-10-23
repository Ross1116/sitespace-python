from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base

class Subcontractor(Base):
    __tablename__ = "subcontractors"
    
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    project = relationship("Project", back_populates="subcontractors")

    name = Column(String)
    email_id = Column(String)
    contractor_name = Column(String)
    contractor_company = Column(String)
    contractor_trade = Column(String)
    contractor_email = Column(String)
    contractor_phone = Column(String)
    contractor_pass = Column(String)
    created_by = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<Subcontractor(id={self.id}, name='{self.name}', company='{self.contractor_company}')>"
