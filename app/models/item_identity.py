from sqlalchemy import Column, String, SmallInteger, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class Item(Base):
    __tablename__ = "items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name = Column(Text, nullable=False)
    identity_status = Column(String(20), nullable=False, default="active")  # 'active' | 'merged'
    merged_into_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("identity_status IN ('active', 'merged')", name="ck_items_identity_status"),
    )

    # Self-referential: where this item merged into
    merged_into = relationship("Item", foreign_keys=[merged_into_item_id], remote_side="Item.id")
    aliases = relationship("ItemAlias", back_populates="item", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Item(id={self.id}, name='{self.display_name}', status='{self.identity_status}')>"


class ItemAlias(Base):
    __tablename__ = "item_aliases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias_normalised_name = Column(Text, nullable=False)
    normalizer_version = Column(SmallInteger, nullable=False, default=1)
    alias_type = Column(String(20), nullable=False)   # 'exact' | 'variant' | 'manual'
    confidence = Column(String(10), nullable=False)    # 'high' | 'medium' | 'low'
    source = Column(String(20), nullable=False)        # 'parser' | 'manual' | 'reconciled'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("alias_normalised_name", "normalizer_version", name="uq_item_aliases_name_version"),
        CheckConstraint("alias_type IN ('exact', 'variant', 'manual')", name="ck_item_aliases_alias_type"),
        CheckConstraint("confidence IN ('high', 'medium', 'low')", name="ck_item_aliases_confidence"),
        CheckConstraint("source IN ('parser', 'manual', 'reconciled')", name="ck_item_aliases_source"),
    )

    item = relationship("Item", back_populates="aliases", foreign_keys=[item_id])

    def __repr__(self) -> str:
        return f"<ItemAlias(name='{self.alias_normalised_name}', type='{self.alias_type}')>"


class ItemIdentityEvent(Base):
    __tablename__ = "item_identity_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(20), nullable=False)   # 'merge' | 'alias_add'
    source_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    details_json = Column(JSONB, nullable=True)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("event_type IN ('merge', 'alias_add')", name="ck_item_identity_events_type"),
    )

    source_item = relationship("Item", foreign_keys=[source_item_id])
    target_item = relationship("Item", foreign_keys=[target_item_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self) -> str:
        return f"<ItemIdentityEvent(type='{self.event_type}', source={self.source_item_id}, target={self.target_item_id})>"
