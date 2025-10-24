from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.core.database import Base
from .site_project import subcontractor_site_project_association

class Subcontractor(Base):
    __tablename__ = "subcontractors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    company_name = Column(String(255))
    trade_specialty = Column(String(100))  # electrician, plumber, etc.
    phone = Column(String(20))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    bookings = relationship(
        "SlotBooking",
        back_populates="subcontractor",
        cascade="all, delete-orphan"
    )
    
    assigned_projects = relationship(
        "SiteProject",
        secondary=subcontractor_site_project_association,
        back_populates="subcontractors"
    )