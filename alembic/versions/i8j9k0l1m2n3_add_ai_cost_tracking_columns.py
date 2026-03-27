"""add_ai_cost_tracking_columns

Revision ID: i8j9k0l1m2n3
Revises: h7i8j9k0l1m2
Create Date: 2026-03-27

Adds:
  - programme_uploads.ai_cost_usd
  - work_profile_ai_logs.input_tokens
  - work_profile_ai_logs.output_tokens
  - work_profile_ai_logs.cost_usd
"""

from alembic import op
import sqlalchemy as sa


revision = "i8j9k0l1m2n3"
down_revision = "h7i8j9k0l1m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "programme_uploads",
        sa.Column("ai_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.create_check_constraint(
        "ck_programme_uploads_ai_cost_usd_nonneg",
        "programme_uploads",
        "ai_cost_usd IS NULL OR ai_cost_usd >= 0",
    )
    op.add_column(
        "work_profile_ai_logs",
        sa.Column("input_tokens", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_work_profile_ai_logs_input_tokens_nonneg",
        "work_profile_ai_logs",
        "input_tokens IS NULL OR input_tokens >= 0",
    )
    op.add_column(
        "work_profile_ai_logs",
        sa.Column("output_tokens", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_work_profile_ai_logs_output_tokens_nonneg",
        "work_profile_ai_logs",
        "output_tokens IS NULL OR output_tokens >= 0",
    )
    op.add_column(
        "work_profile_ai_logs",
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.create_check_constraint(
        "ck_work_profile_ai_logs_cost_usd_nonneg",
        "work_profile_ai_logs",
        "cost_usd IS NULL OR cost_usd >= 0",
    )
    op.create_check_constraint(
        "ck_work_profile_ai_logs_tokens_used_nonneg",
        "work_profile_ai_logs",
        "tokens_used IS NULL OR tokens_used >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_work_profile_ai_logs_tokens_used_nonneg", "work_profile_ai_logs", type_="check")
    op.drop_constraint("ck_work_profile_ai_logs_cost_usd_nonneg", "work_profile_ai_logs", type_="check")
    op.drop_constraint("ck_work_profile_ai_logs_output_tokens_nonneg", "work_profile_ai_logs", type_="check")
    op.drop_constraint("ck_work_profile_ai_logs_input_tokens_nonneg", "work_profile_ai_logs", type_="check")
    op.drop_constraint("ck_programme_uploads_ai_cost_usd_nonneg", "programme_uploads", type_="check")
    op.drop_column("work_profile_ai_logs", "cost_usd")
    op.drop_column("work_profile_ai_logs", "output_tokens")
    op.drop_column("work_profile_ai_logs", "input_tokens")
    op.drop_column("programme_uploads", "ai_cost_usd")
