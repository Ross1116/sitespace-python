from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    manager = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    bookings = relationship("SlotBooking", back_populates="project", cascade="all, delete-orphan")
    subcontractors = relationship("Subcontractor", back_populates="project", cascade="all, delete-orphan")
