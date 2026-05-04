from sqlalchemy import Column, String, DateTime, Date, Time, ForeignKey, Text, Table, SmallInteger, CheckConstraint, UniqueConstraint
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

class SiteProject(Base):
    __tablename__ = "site_projects"
    __table_args__ = (
        CheckConstraint("work_days_per_week BETWEEN 1 AND 7", name="ck_site_projects_work_days_per_week"),
    )
    
    # Essential Fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)   # Optional details
    
    # Optional Fields (nice to have, but not required)
    location = Column(String(255), nullable=True)  # Optional
    start_date = Column(Date, nullable=True)       # Optional
    end_date = Column(Date, nullable=True)         # Optional  
    status = Column(String(50), nullable=True, default="active")  # Optional
    timezone = Column(String(64), nullable=False, default="Australia/Adelaide", server_default="Australia/Adelaide")
    work_days_per_week = Column(SmallInteger, nullable=False, default=5, server_default="5")
    default_work_start_time = Column(Time, nullable=False, default="08:00", server_default="08:00")
    default_work_end_time = Column(Time, nullable=False, default="16:00", server_default="16:00")
    
    # Audit Fields
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
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
    
    assets = relationship("Asset", back_populates="project")
    slot_bookings = relationship("SlotBooking", back_populates="project")
    site_plans = relationship("SitePlan", back_populates="project", cascade="all, delete-orphan")
    non_working_days = relationship("ProjectNonWorkingDay", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SiteProject(id={self.id}, name={self.name})>"


class ProjectNonWorkingDay(Base):
    __tablename__ = "project_non_working_days"
    __table_args__ = (
        UniqueConstraint("project_id", "calendar_date", name="uq_project_non_working_days_project_date"),
        CheckConstraint(
            "kind IN ('holiday', 'shutdown', 'weather', 'custom')",
            name="ck_project_non_working_days_kind",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("site_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    calendar_date = Column(Date, nullable=False, index=True)
    label = Column(String(255), nullable=False)
    kind = Column(String(20), nullable=False, default="holiday", server_default="holiday")
    created_by = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("SiteProject", back_populates="non_working_days")
