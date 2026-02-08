from sqlalchemy import Column, Index, String, Text, Date, DateTime, Enum as SQLEnum, DECIMAL, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum
from app.core.database import Base

class AssetStatus(str, enum.Enum):
    AVAILABLE = "available"
    IN_USE = "in_use"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"

class Asset(Base):
    __tablename__ = "assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("site_projects.id", ondelete="RESTRICT"), nullable=False)
    asset_code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(100))  # excavator, crane, truck, etc.
    description = Column(Text)
    purchase_date = Column(Date)
    purchase_value = Column(DECIMAL(12, 2))
    current_value = Column(DECIMAL(12, 2))
    status = Column(SQLEnum(AssetStatus), default=AssetStatus.AVAILABLE)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_assets_project_status", "project_id", "status"),
    )

    # Relationships
    project = relationship("SiteProject", back_populates="assets")
    bookings = relationship("SlotBooking", back_populates="asset", cascade="all, delete-orphan")