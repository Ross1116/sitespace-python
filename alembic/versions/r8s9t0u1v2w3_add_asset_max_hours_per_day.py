"""add asset max_hours_per_day

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-04-05

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("max_hours_per_day", sa.Numeric(4, 1), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "max_hours_per_day")
