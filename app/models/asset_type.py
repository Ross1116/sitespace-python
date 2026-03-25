from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, NUMERIC
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class AssetType(Base):
    __tablename__ = "asset_types"

    code = Column(String(50), primary_key=True)
    display_name = Column(String(255), nullable=False)
    parent_code = Column(
        String(50),
        ForeignKey("asset_types.code", ondelete="SET NULL"),
        nullable=True,
    )
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    is_user_selectable = Column(Boolean, nullable=False, default=True, server_default="true")
    max_hours_per_day = Column(NUMERIC(4, 1), nullable=False)
    taxonomy_version = Column(Integer, nullable=False, default=1, server_default="1")
    introduced_at = Column(DateTime(timezone=True), server_default=func.now())
    retired_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    parent = relationship("AssetType", remote_side=[code], foreign_keys=[parent_code])
    assets = relationship("Asset", back_populates="asset_type_rel", foreign_keys="Asset.canonical_type")

    def __repr__(self) -> str:
        return f"<AssetType(code='{self.code}', display_name='{self.display_name}')>"
