"""add upload job queue and scheduled runs

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-03-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "n4o5p6q7r8s9"
down_revision: Union[str, None] = "m3n4o5p6q7r8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- programme_upload_jobs ---
    op.create_table(
        "programme_upload_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "upload_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programme_uploads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("last_error_code", sa.String(60), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("upload_id", name="uq_programme_upload_jobs_upload"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'completed', 'dead')",
            name="ck_programme_upload_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_programme_upload_jobs_attempt_count",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_programme_upload_jobs_max_attempts",
        ),
    )
    op.create_index(
        "ix_programme_upload_jobs_claimable",
        "programme_upload_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_programme_upload_jobs_reclaimable",
        "programme_upload_jobs",
        ["status", "heartbeat_at"],
    )

    # --- scheduled_job_runs ---
    op.create_table(
        "scheduled_job_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_name", sa.String(100), nullable=False),
        sa.Column("logical_local_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "job_name", "logical_local_date",
            name="uq_scheduled_job_runs_job_date",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_scheduled_job_runs_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("scheduled_job_runs")
    op.drop_index("ix_programme_upload_jobs_reclaimable", table_name="programme_upload_jobs")
    op.drop_index("ix_programme_upload_jobs_claimable", table_name="programme_upload_jobs")
    op.drop_table("programme_upload_jobs")
