"""add project non-working days

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-05-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "t0u1v2w3x4y5"
down_revision: Union[str, None] = "s9t0u1v2w3x4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "site_projects",
        sa.Column("default_work_start_time", sa.Time(), nullable=False, server_default="08:00"),
    )
    op.add_column(
        "site_projects",
        sa.Column("default_work_end_time", sa.Time(), nullable=False, server_default="16:00"),
    )
    op.add_column(
        "site_projects",
        sa.Column("holiday_country_code", sa.String(length=2), nullable=False, server_default="AU"),
    )
    op.add_column(
        "site_projects",
        sa.Column("holiday_region_code", sa.String(length=3), nullable=False, server_default="SA"),
    )
    op.add_column(
        "site_projects",
        sa.Column("holiday_region_source", sa.String(length=20), nullable=False, server_default="default"),
    )

    op.create_table(
        "project_non_working_days",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("calendar_date", sa.Date(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="holiday"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('holiday', 'shutdown', 'weather', 'custom')",
            name="ck_project_non_working_days_kind",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["site_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "calendar_date", name="uq_project_non_working_days_project_date"),
    )
    op.create_index(
        op.f("ix_project_non_working_days_project_id"),
        "project_non_working_days",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_non_working_days_calendar_date"),
        "project_non_working_days",
        ["calendar_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_non_working_days_created_by"),
        "project_non_working_days",
        ["created_by"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_non_working_days_created_by"), table_name="project_non_working_days")
    op.drop_index(op.f("ix_project_non_working_days_calendar_date"), table_name="project_non_working_days")
    op.drop_index(op.f("ix_project_non_working_days_project_id"), table_name="project_non_working_days")
    op.drop_table("project_non_working_days")
    op.drop_column("site_projects", "holiday_region_source")
    op.drop_column("site_projects", "holiday_region_code")
    op.drop_column("site_projects", "holiday_country_code")
    op.drop_column("site_projects", "default_work_end_time")
    op.drop_column("site_projects", "default_work_start_time")
