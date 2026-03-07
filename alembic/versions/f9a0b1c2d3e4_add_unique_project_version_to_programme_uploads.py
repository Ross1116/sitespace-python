"""add_unique_project_version_to_programme_uploads

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-03-07

Adds:
  - unique constraint on programme_uploads(project_id, version_number)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    duplicates = bind.execute(
        sa.text(
            """
            SELECT project_id, version_number, COUNT(*) AS duplicate_count
            FROM programme_uploads
            GROUP BY project_id, version_number
            HAVING COUNT(*) > 1
            ORDER BY duplicate_count DESC
            LIMIT 20
            """
        )
    ).fetchall()

    if duplicates:
        duplicate_details = ", ".join(
            f"(project_id={row.project_id}, version_number={row.version_number}, count={row.duplicate_count})"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot add unique constraint uq_programme_upload_project_version: "
            "duplicate (project_id, version_number) rows exist. "
            "Resolve duplicates first. Sample groups: "
            f"{duplicate_details}"
        )

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
