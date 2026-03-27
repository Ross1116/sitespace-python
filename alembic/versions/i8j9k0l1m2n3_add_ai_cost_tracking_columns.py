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
    op.add_column(
        "work_profile_ai_logs",
        sa.Column("input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "work_profile_ai_logs",
        sa.Column("output_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "work_profile_ai_logs",
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("work_profile_ai_logs", "cost_usd")
    op.drop_column("work_profile_ai_logs", "output_tokens")
    op.drop_column("work_profile_ai_logs", "input_tokens")
    op.drop_column("programme_uploads", "ai_cost_usd")
