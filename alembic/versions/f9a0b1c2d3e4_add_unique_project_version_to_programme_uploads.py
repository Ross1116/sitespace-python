"""add_unique_project_version_to_programme_uploads

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-03-07

Adds:
  - unique constraint on programme_uploads(project_id, version_number)
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_programme_upload_project_version",
        "programme_uploads",
        ["project_id", "version_number"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_programme_upload_project_version",
        "programme_uploads",
        type_="unique",
    )
