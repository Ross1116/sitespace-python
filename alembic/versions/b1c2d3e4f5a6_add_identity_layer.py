"""add_identity_layer

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-03-24

Stage 2 — Identity layer.

Adds:
  items                              new table
  item_aliases                       new table (UNIQUE on normalised_name + version)
  item_identity_events               new table (audit log)
  programme_activities.item_id       nullable FK → items.id
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("identity_status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("merged_into_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("identity_status IN ('active', 'merged')", name="ck_items_identity_status"),
    )

    op.create_table(
        "item_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_normalised_name", sa.Text, nullable=False),
        sa.Column("normalizer_version", sa.SmallInteger, nullable=False, server_default="1"),
        sa.Column("alias_type", sa.String(20), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("alias_normalised_name", "normalizer_version", name="uq_item_aliases_name_version"),
        sa.CheckConstraint("alias_type IN ('exact', 'variant', 'manual')", name="ck_item_aliases_alias_type"),
        sa.CheckConstraint("confidence IN ('high', 'medium', 'low')", name="ck_item_aliases_confidence"),
        sa.CheckConstraint("source IN ('parser', 'manual', 'reconciled')", name="ck_item_aliases_source"),
    )
    op.create_index("ix_item_aliases_item_id", "item_aliases", ["item_id"])

    op.create_table(
        "item_identity_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("source_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("details_json", JSONB, nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("event_type IN ('merge', 'alias_add')", name="ck_item_identity_events_type"),
    )
    op.create_index("ix_item_identity_events_source_item_id", "item_identity_events", ["source_item_id"])
    op.create_index("ix_item_identity_events_target_item_id", "item_identity_events", ["target_item_id"])

    op.add_column(
        "programme_activities",
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_programme_activities_item_id", "programme_activities", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_programme_activities_item_id", table_name="programme_activities")
    op.drop_column("programme_activities", "item_id")

    op.drop_index("ix_item_identity_events_target_item_id", table_name="item_identity_events")
    op.drop_index("ix_item_identity_events_source_item_id", table_name="item_identity_events")
    op.drop_table("item_identity_events")

    op.drop_index("ix_item_aliases_item_id", table_name="item_aliases")
    op.drop_table("item_aliases")

    op.drop_table("items")
