"""Stage 10 learning local/global boundary B

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-03-31 12:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "m3n4o5p6q7r8"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM item_context_profiles icp
        WHERE icp.project_id IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM activity_work_profiles awp
            WHERE awp.context_profile_id = icp.id
          )
        """
    )
    op.drop_index("ux_item_context_profiles_project_local_key_tmp", table_name="item_context_profiles")
    op.alter_column("item_context_profiles", "project_id", nullable=False)
    op.create_unique_constraint(
        "uq_item_context_profiles_key",
        "item_context_profiles",
        [
            "project_id",
            "item_id",
            "asset_type",
            "duration_days",
            "context_version",
            "inference_version",
            "context_hash",
        ],
    )


def downgrade() -> None:
    op.drop_constraint("uq_item_context_profiles_key", "item_context_profiles", type_="unique")
    op.alter_column("item_context_profiles", "project_id", nullable=True)
    op.create_index(
        "ux_item_context_profiles_project_local_key_tmp",
        "item_context_profiles",
        [
            "project_id",
            "item_id",
            "asset_type",
            "duration_days",
            "context_version",
            "inference_version",
            "context_hash",
        ],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
