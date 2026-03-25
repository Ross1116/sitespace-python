"""add_asset_taxonomy

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-03-25

Stage 3 — Asset Taxonomy Foundation.

Adds:
  asset_types              new table (PK = code VARCHAR(50))
  seed data                11 initial asset types with max_hours_per_day
  assets.canonical_type    nullable FK → asset_types.code
"""

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

# Seed data: (code, display_name, max_hours_per_day, is_user_selectable)
_SEED_TYPES = [
    ("crane",         "Crane",         10.0, True),
    ("hoist",         "Hoist",         10.0, True),
    ("loading_bay",   "Loading Bay",   10.0, True),
    ("ewp",           "EWP",           16.0, True),
    ("concrete_pump", "Concrete Pump", 10.0, True),
    ("excavator",     "Excavator",     16.0, True),
    ("forklift",      "Forklift",      16.0, True),
    ("telehandler",   "Telehandler",   16.0, True),
    ("compactor",     "Compactor",     16.0, True),
    ("other",         "Other",         16.0, True),
    ("none",          "None",           0.0, False),
]


def upgrade() -> None:
    op.create_table(
        "asset_types",
        sa.Column("code", sa.String(50), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column(
            "parent_code",
            sa.String(50),
            sa.ForeignKey("asset_types.code", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_user_selectable", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("max_hours_per_day", sa.NUMERIC(4, 1), nullable=False),
        sa.Column("taxonomy_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("introduced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Seed the 11 initial asset types
    asset_types_table = sa.table(
        "asset_types",
        sa.column("code", sa.String),
        sa.column("display_name", sa.String),
        sa.column("max_hours_per_day", sa.NUMERIC),
        sa.column("is_user_selectable", sa.Boolean),
    )
    op.bulk_insert(
        asset_types_table,
        [
            {
                "code": code,
                "display_name": display_name,
                "max_hours_per_day": max_hours,
                "is_user_selectable": selectable,
            }
            for code, display_name, max_hours, selectable in _SEED_TYPES
        ],
    )

    # Add canonical_type FK column to assets
    op.add_column(
        "assets",
        sa.Column(
            "canonical_type",
            sa.String(50),
            sa.ForeignKey("asset_types.code", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_assets_canonical_type", "assets", ["canonical_type"])


def downgrade() -> None:
    op.drop_index("ix_assets_canonical_type", table_name="assets")
    op.drop_column("assets", "canonical_type")
    op.drop_table("asset_types")
