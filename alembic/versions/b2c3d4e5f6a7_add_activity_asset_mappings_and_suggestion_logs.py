"""add_activity_asset_mappings_and_suggestion_logs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-07

Adds:
  - activity_asset_mappings : AI auto-committed + PM-correctable asset classifications
  - ai_suggestion_logs      : every AI suggestion + PM correction for learning loop
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # activity_asset_mappings
    # ------------------------------------------------------------------
    op.create_table(
        "activity_asset_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "programme_activity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programme_activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # asset_type validated in application layer against ALLOWED_ASSET_TYPES.
        # Nullable to support low-confidence rows that haven't been classified yet.
        sa.Column("asset_type", sa.String(50), nullable=True),
        # confidence: "high" | "medium" | "low"
        sa.Column("confidence", sa.String(10), nullable=False),
        # source: "ai" | "keyword" | "manual"
        sa.Column("source", sa.String(20), nullable=False),
        # True for high + medium auto-commits; False for low-confidence rows
        sa.Column("auto_committed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("manually_corrected", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "corrected_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        # Optional sub assignment — PM links an activity type to a subcontractor
        sa.Column(
            "subcontractor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subcontractors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_activity_asset_mappings_activity_id",
        "activity_asset_mappings",
        ["programme_activity_id"],
    )
    op.create_index(
        "ix_activity_asset_mappings_confidence",
        "activity_asset_mappings",
        ["confidence"],
    )
    op.create_index(
        "ix_activity_asset_mappings_subcontractor_id",
        "activity_asset_mappings",
        ["subcontractor_id"],
    )

    # ------------------------------------------------------------------
    # ai_suggestion_logs
    # Append-only. Every suggestion + every PM correction logged here.
    # Feeds the learning loop — after 50+ corrections per builder,
    # AI Dev injects these as few-shot examples.
    # ------------------------------------------------------------------
    op.create_table(
        "ai_suggestion_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "activity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programme_activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("suggested_asset_type", sa.String(50), nullable=True),
        sa.Column("confidence", sa.String(10), nullable=True),
        # accepted=True means PM left it alone; False means PM corrected it
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default="true"),
        # correction is populated when accepted=False (the value PM changed it to)
        sa.Column("correction", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_suggestion_logs_activity_id",
        "ai_suggestion_logs",
        ["activity_id"],
    )
    op.create_index(
        "ix_ai_suggestion_logs_accepted",
        "ai_suggestion_logs",
        ["accepted"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_suggestion_logs_accepted", table_name="ai_suggestion_logs")
    op.drop_index("ix_ai_suggestion_logs_activity_id", table_name="ai_suggestion_logs")
    op.drop_table("ai_suggestion_logs")

    op.drop_index("ix_activity_asset_mappings_subcontractor_id", table_name="activity_asset_mappings")
    op.drop_index("ix_activity_asset_mappings_confidence", table_name="activity_asset_mappings")
    op.drop_index("ix_activity_asset_mappings_activity_id", table_name="activity_asset_mappings")
    op.drop_table("activity_asset_mappings")
