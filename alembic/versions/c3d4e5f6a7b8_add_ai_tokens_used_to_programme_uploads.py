"""add_ai_tokens_used_to_programme_uploads

Revision ID: c3d4e5f6a7b8
Revises: d2e3f4a5b6c7
Create Date: 2026-03-24

Adds ai_tokens_used (INTEGER, nullable) to programme_uploads so the API cost
of each classification run is tracked at upload level.  The value is written by
process_programme after classify_assets() returns its ClassificationResult.

Nullable — existing rows and uploads where the AI fallback ran (zero tokens)
stay NULL rather than storing a misleading zero.
"""

from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "programme_uploads",
        sa.Column("ai_tokens_used", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("programme_uploads", "ai_tokens_used")
