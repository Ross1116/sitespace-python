"""add metadata confidence fields

Revision ID: h7i8j9k0l1m2
Revises: g6h7i8j9k0l1
Create Date: 2026-03-26 15:55:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "h7i8j9k0l1m2"
down_revision: Union[str, None] = "g6h7i8j9k0l1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("type_resolution_status", sa.String(length=20), nullable=False, server_default="unknown"),
    )
    op.add_column("assets", sa.Column("type_inference_source", sa.String(length=50), nullable=True))
    op.add_column("assets", sa.Column("type_inference_confidence", sa.DECIMAL(precision=4, scale=3), nullable=True))
    op.create_index(op.f("ix_assets_type_resolution_status"), "assets", ["type_resolution_status"], unique=False)

    op.add_column("subcontractors", sa.Column("suggested_trade_specialty", sa.String(length=100), nullable=True))
    op.add_column(
        "subcontractors",
        sa.Column("trade_resolution_status", sa.String(length=20), nullable=False, server_default="unknown"),
    )
    op.add_column("subcontractors", sa.Column("trade_inference_source", sa.String(length=50), nullable=True))
    op.add_column("subcontractors", sa.Column("trade_inference_confidence", sa.DECIMAL(precision=4, scale=3), nullable=True))
    op.create_index(
        op.f("ix_subcontractors_trade_resolution_status"),
        "subcontractors",
        ["trade_resolution_status"],
        unique=False,
    )

    op.execute(
        """
        UPDATE assets
        SET
            type_resolution_status = CASE
                WHEN NULLIF(TRIM(canonical_type), '') IS NOT NULL THEN 'inferred'
                ELSE 'unknown'
            END,
            type_inference_source = CASE
                WHEN NULLIF(TRIM(canonical_type), '') IS NOT NULL THEN 'migration'
                ELSE NULL
            END
        """
    )

    op.execute(
        """
        UPDATE subcontractors
        SET
            suggested_trade_specialty = CASE
                WHEN LOWER(NULLIF(TRIM(trade_specialty), '')) IN ('general', 'other')
                    THEN LOWER(NULLIF(TRIM(trade_specialty), ''))
                ELSE NULLIF(TRIM(suggested_trade_specialty), '')
            END,
            trade_specialty = CASE
                WHEN LOWER(NULLIF(TRIM(trade_specialty), '')) IN ('general', 'other') THEN NULL
                ELSE LOWER(NULLIF(TRIM(trade_specialty), ''))
            END,
            trade_resolution_status = CASE
                WHEN NULLIF(TRIM(trade_specialty), '') IS NULL THEN 'unknown'
                WHEN LOWER(NULLIF(TRIM(trade_specialty), '')) IN ('general', 'other') THEN 'suggested'
                ELSE 'confirmed'
            END,
            trade_inference_source = CASE
                WHEN LOWER(NULLIF(TRIM(trade_specialty), '')) IN ('general', 'other') THEN 'migration'
                ELSE NULL
            END
        """
    )

    op.alter_column("assets", "type_resolution_status", server_default=None)
    op.alter_column("subcontractors", "trade_resolution_status", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_subcontractors_trade_resolution_status"), table_name="subcontractors")
    op.drop_column("subcontractors", "trade_inference_confidence")
    op.drop_column("subcontractors", "trade_inference_source")
    op.drop_column("subcontractors", "trade_resolution_status")
    op.drop_column("subcontractors", "suggested_trade_specialty")

    op.drop_index(op.f("ix_assets_type_resolution_status"), table_name="assets")
    op.drop_column("assets", "type_inference_confidence")
    op.drop_column("assets", "type_inference_source")
    op.drop_column("assets", "type_resolution_status")
