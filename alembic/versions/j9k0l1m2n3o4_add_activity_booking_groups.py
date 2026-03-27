"""add activity booking groups

Revision ID: j9k0l1m2n3o4
Revises: i8j9k0l1m2n3
Create Date: 2026-03-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "j9k0l1m2n3o4"
down_revision: Union[str, None] = "i8j9k0l1m2n3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_booking_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("programme_activity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expected_asset_type", sa.String(length=50), nullable=False),
        sa.Column("selected_week_start", sa.Date(), nullable=True),
        sa.Column("origin_source", sa.String(length=20), nullable=False),
        sa.Column("is_modified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "origin_source IN ('activity_row', 'lookahead_week_row')",
            name="ck_activity_booking_groups_origin_source",
        ),
        sa.ForeignKeyConstraint(["programme_activity_id"], ["programme_activities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["site_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("programme_activity_id", name="uq_activity_booking_groups_activity"),
    )
    op.create_index(
        op.f("ix_activity_booking_groups_created_by"),
        "activity_booking_groups",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activity_booking_groups_programme_activity_id"),
        "activity_booking_groups",
        ["programme_activity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activity_booking_groups_project_id"),
        "activity_booking_groups",
        ["project_id"],
        unique=False,
    )

    op.add_column(
        "slot_bookings",
        sa.Column("booking_group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_slot_bookings_booking_group_id",
        "slot_bookings",
        "activity_booking_groups",
        ["booking_group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_slot_bookings_booking_group_id"),
        "slot_bookings",
        ["booking_group_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_slot_bookings_booking_group_id"), table_name="slot_bookings")
    op.drop_constraint("fk_slot_bookings_booking_group_id", "slot_bookings", type_="foreignkey")
    op.drop_column("slot_bookings", "booking_group_id")

    op.drop_index(op.f("ix_activity_booking_groups_project_id"), table_name="activity_booking_groups")
    op.drop_index(op.f("ix_activity_booking_groups_programme_activity_id"), table_name="activity_booking_groups")
    op.drop_index(op.f("ix_activity_booking_groups_created_by"), table_name="activity_booking_groups")
    op.drop_table("activity_booking_groups")
