from sqlalchemy import CheckConstraint, Column, String, Boolean, Integer, DateTime, ForeignKey, Index, NUMERIC, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class AssetType(Base):
    __tablename__ = "asset_types"
    __table_args__ = (
        CheckConstraint("scope IN ('global', 'project')", name="ck_asset_types_scope"),
        CheckConstraint(
            (
                "(scope = 'global' AND project_id IS NULL AND local_slug IS NULL) OR "
                "(scope = 'project' AND project_id IS NOT NULL AND local_slug IS NOT NULL)"
            ),
            name="ck_asset_types_scope_project",
        ),
        Index(
            "ux_asset_types_project_local_slug",
            "project_id",
            "local_slug",
            unique=True,
            postgresql_where=text("scope = 'project'"),
        ),
    )

    code = Column(String(50), primary_key=True)
    display_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    scope = Column(String(20), nullable=False, default="global", server_default="global")
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("site_projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    local_slug = Column(String(50), nullable=True)
    parent_code = Column(
        String(50),
        ForeignKey("asset_types.code", ondelete="SET NULL"),
        nullable=True,
    )
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    is_user_selectable = Column(Boolean, nullable=False, default=True, server_default="true")
    max_hours_per_day = Column(NUMERIC(4, 1), nullable=False)
    planning_attributes_json = Column(JSONB, nullable=True)
    taxonomy_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    introduced_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    retired_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    parent = relationship("AssetType", remote_side=[code], foreign_keys=[parent_code])
    assets = relationship("Asset", back_populates="asset_type_rel", foreign_keys="Asset.canonical_type")
    project = relationship("SiteProject", foreign_keys=[project_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self) -> str:
        return f"<AssetType(code='{self.code}', display_name='{self.display_name}')>"
