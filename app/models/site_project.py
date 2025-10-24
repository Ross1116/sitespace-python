from sqlalchemy import Column, String, DateTime, Date, ForeignKey, Text, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.core.database import Base

# Association tables for M2M relationships
manager_site_project_association = Table(
    'manager_site_project',
    Base.metadata,
    Column('user_id', UUID(as_uuid=True), ForeignKey('users.id')),
    Column('site_project_id', UUID(as_uuid=True), ForeignKey('site_projects.id'))
)

subcontractor_site_project_association = Table(
    'subcontractor_site_project',
    Base.metadata,
    # Column('user_id', UUID(as_uuid=True), ForeignKey('users.id')),
    Column('subcontractor_id', UUID(as_uuid=True), ForeignKey('subcontractors.id')),
    Column('site_project_id', UUID(as_uuid=True), ForeignKey('site_projects.id'))
)

# class SiteProject(Base):
#     __tablename__ = "site_projects"

#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
#     # External/Integration Keys
#     contractor_key = Column(String, nullable=True, index=True)
#     contractor_project_id = Column(String, nullable=True, index=True)
#     email_id = Column(String, nullable=True)
    
#     # Stored as a raw TEXT field (JSON serialization)
#     contractor_project = Column(Text, nullable=True) 
#     created_by = Column(String, nullable=True)

#     created_at = Column(DateTime(timezone=True), server_default=func.now())
#     updated_at = Column(DateTime(timezone=True), onupdate=func.now())

#     # M2M Relationships
#     managers = relationship(
#         "User",
#         secondary=manager_site_project_association,
#         back_populates="managed_projects"
#     )
    
#     subcontractors = relationship(
#         "User",
#         secondary=subcontractor_site_project_association,
#         back_populates="assigned_projects"
#     )
    
#     # O2M Relationships
#     assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
#     slot_bookings = relationship("SlotBooking", back_populates="project", cascade="all, delete-orphan")
    
#     def __repr__(self):
#         return f"<SiteProject(id={self.id}, contractor_project_id={self.contractor_project_id})>"

class SiteProject(Base):
    __tablename__ = "site_projects"
    
    # Essential Fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)   # Optional details
    
    # Optional Fields (nice to have, but not required)
    location = Column(String(255), nullable=True)  # Optional
    start_date = Column(Date, nullable=True)       # Optional
    end_date = Column(Date, nullable=True)         # Optional  
    status = Column(String(50), nullable=True, default="active")  # Optional
    
    # Audit Fields
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships (keep all of these!)
    managers = relationship(
        "User",
        secondary=manager_site_project_association,
        back_populates="managed_projects"
    )
    
    subcontractors = relationship(
        # "User",
        "Subcontractor",
        secondary=subcontractor_site_project_association,
        back_populates="assigned_projects"
    )
    
    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    slot_bookings = relationship("SlotBooking", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SiteProject(id={self.id}, name={self.name})>"