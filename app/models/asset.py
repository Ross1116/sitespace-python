from sqlalchemy import Column, Index, Integer, String, Text, Date, DateTime, Enum as SQLEnum, DECIMAL, NUMERIC, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.schemas.enums import ASSET_TYPE_RESOLUTION_READY, AssetStatus

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
    maintenance_start_date = Column(Date, nullable=True)
    maintenance_end_date = Column(Date, nullable=True)
    # Stage 3 — canonical asset type from taxonomy
    canonical_type = Column(
        String(50),
        ForeignKey("asset_types.code", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    type_resolution_status = Column(String(20), nullable=False, default="unknown", server_default="unknown", index=True)
    type_inference_source = Column(String(50), nullable=True)
    type_inference_confidence = Column(DECIMAL(4, 3), nullable=True)
    pending_booking_capacity = Column(Integer, nullable=False, default=5, server_default="5")
    planning_attributes_json = Column(JSONB, nullable=True)
    max_hours_per_day = Column(NUMERIC(4, 1), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_assets_project_status", "project_id", "status"),
    )

    # Relationships
    project = relationship("SiteProject", back_populates="assets")
    bookings = relationship("SlotBooking", back_populates="asset")
    asset_type_rel = relationship("AssetType", back_populates="assets", foreign_keys=[canonical_type])

    @property
    def planning_ready(self) -> bool:
        return bool(self.canonical_type) and (self.type_resolution_status or "unknown") in ASSET_TYPE_RESOLUTION_READY

    @property
    def capacity_ready(self) -> bool:
        status_value = getattr(getattr(self, "status", None), "value", getattr(self, "status", None))
        return (
            self.planning_ready
            and (self.canonical_type or "").strip().lower() not in {"", "none"}
            and status_value not in {AssetStatus.RETIRED, AssetStatus.RETIRED.value}
        )
