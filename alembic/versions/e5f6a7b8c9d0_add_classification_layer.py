"""add_classification_layer

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-26

Stage 4 — Classification layer.

Adds:
  item_classifications               persistent item-level asset-type memory
  item_classification_events         append-only audit log for classification changes
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── item_classifications ─────────────────────────────────────────────────
    op.create_table(
        "item_classifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_type",
            sa.String(50),
            sa.ForeignKey("asset_types.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("confirmation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("correction_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_item_classifications_confidence",
        ),
        sa.CheckConstraint(
            "source IN ('ai', 'keyword', 'manual')",
            name="ck_item_classifications_source",
        ),
    )
    op.create_index(
        "ix_item_classifications_item_id",
        "item_classifications",
        ["item_id"],
    )
    # Partial unique index: at most one active classification per item.
    op.execute(
        "CREATE UNIQUE INDEX idx_item_classifications_active "
        "ON item_classifications (item_id) WHERE is_active = TRUE"
    )

    # ── item_classification_events ───────────────────────────────────────────
    op.create_table(
        "item_classification_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "classification_id",
            UUID(as_uuid=True),
            sa.ForeignKey("item_classifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("old_asset_type", sa.String(50), nullable=True),
        sa.Column("new_asset_type", sa.String(50), nullable=True),
        sa.Column(
            "triggered_by_upload_id",
            UUID(as_uuid=True),
            sa.ForeignKey("programme_uploads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "performed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("details_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "event_type IN ('created','confirmed','deactivated','correction_flagged',"
            "'manual_override','merge_reconcile')",
            name="ck_item_classification_events_type",
        ),
    )
    op.create_index(
        "ix_item_classification_events_item_id",
        "item_classification_events",
        ["item_id"],
    )
    op.create_index(
        "ix_item_classification_events_classification_id",
        "item_classification_events",
        ["classification_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_item_classification_events_classification_id",
        table_name="item_classification_events",
    )
    op.drop_index(
        "ix_item_classification_events_item_id",
        table_name="item_classification_events",
    )
    op.drop_table("item_classification_events")

    op.execute("DROP INDEX IF EXISTS idx_item_classifications_active")
    op.drop_index(
        "ix_item_classifications_item_id",
        table_name="item_classifications",
    )
    op.drop_table("item_classifications")
