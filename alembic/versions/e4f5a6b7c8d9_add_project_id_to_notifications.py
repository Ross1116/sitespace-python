"""add_project_id_to_notifications

Revision ID: e4f5a6b7c8d9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-24

Adds notifications.project_id (nullable FK → site_projects.id, CASCADE delete)
so that notifications with a NULL activity_id (e.g. weekly demand-gap alerts
not tied to a specific programme activity) remain queryable by project without
relying on the now-nullable activity_id → programme_activities join chain.

Nullable — existing rows that predate this migration retain project_id = NULL.
A backfill can be applied separately if needed:

    UPDATE notifications n
    SET    project_id = pu.project_id
    FROM   programme_activities pa
    JOIN   programme_uploads     pu ON pu.id = pa.programme_upload_id
    WHERE  pa.id = n.activity_id;
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("project_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_notifications_project_id",
        "notifications",
        ["project_id"],
    )
    op.create_foreign_key(
        "notifications_project_id_fkey",
        "notifications",
        "site_projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "notifications_project_id_fkey",
        "notifications",
        type_="foreignkey",
    )
    op.drop_index("ix_notifications_project_id", table_name="notifications")
    op.drop_column("notifications", "project_id")
