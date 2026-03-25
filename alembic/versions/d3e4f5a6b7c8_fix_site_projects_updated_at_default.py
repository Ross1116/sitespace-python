"""fix_site_projects_updated_at_default

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-03-25

Sets a server default of now() on site_projects.updated_at so the DB
and the model (server_default=func.now()) match.  Previously the column
had no server default — new rows got NULL for updated_at until the first
UPDATE triggered the onupdate clause.
"""

import sqlalchemy as sa
from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "site_projects",
        "updated_at",
        server_default=sa.text("now()"),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "site_projects",
        "updated_at",
        server_default=None,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )
