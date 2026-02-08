from sqlalchemy import Column, String, Boolean, DateTime, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum
from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    role = Column(String, nullable=False)    
    is_active = Column(Boolean, default=True)
    email_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships - using the association tables
    managed_projects = relationship(
        "SiteProject",
        secondary="manager_site_project",
        back_populates="managers"
    )
    
    bookings = relationship(
        "SlotBooking",
        back_populates="manager",
        cascade="all, delete-orphan"
    )