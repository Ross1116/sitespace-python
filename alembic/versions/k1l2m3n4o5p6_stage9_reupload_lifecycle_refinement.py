"""stage9 reupload lifecycle refinement

Revision ID: k1l2m3n4o5p6
Revises: j9k0l1m2n3o4
Create Date: 2026-03-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, None] = "j9k0l1m2n3o4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "programme_uploads",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
    op.execute(
        """
        UPDATE programme_uploads
        SET status = 'completed_with_warnings'
        WHERE status = 'degraded'
        """
    )
    op.create_check_constraint(
        "ck_programme_uploads_status_stage9",
        "programme_uploads",
        "status IN ('processing', 'committed', 'completed_with_warnings', 'failed')",
    )

    op.add_column(
        "notifications",
        sa.Column("programme_upload_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_notifications_programme_upload_id",
        "notifications",
        "programme_uploads",
        ["programme_upload_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_notifications_snapshot_id",
        "notifications",
        "lookahead_snapshots",
        ["snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_notifications_programme_upload_id"),
        "notifications",
        ["programme_upload_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notifications_snapshot_id"),
        "notifications",
        ["snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_snapshot_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_programme_upload_id"), table_name="notifications")
    op.drop_constraint("fk_notifications_snapshot_id", "notifications", type_="foreignkey")
    op.drop_constraint("fk_notifications_programme_upload_id", "notifications", type_="foreignkey")
    op.drop_column("notifications", "snapshot_id")
    op.drop_column("notifications", "programme_upload_id")

    op.drop_constraint("ck_programme_uploads_status_stage9", "programme_uploads", type_="check")
    op.execute(
        """
        UPDATE programme_uploads
        SET status = 'degraded'
        WHERE status = 'completed_with_warnings'
        """
    )
    op.alter_column(
        "programme_uploads",
        "status",
        existing_type=sa.String(length=30),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
