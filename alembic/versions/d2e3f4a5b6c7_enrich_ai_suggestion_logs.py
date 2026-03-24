"""enrich_ai_suggestion_logs

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-03-24

Adds observability columns to ai_suggestion_logs so each row carries full
pipeline context — which upload triggered it, which AI model was used, whether
the keyword fallback ran, and which pipeline stage produced the suggestion.

Also adds upload_id FK so suggestion logs can be queried per-upload without
joining through programme_activities.

New columns (all nullable — existing rows are grandfathered as NULL):
  upload_id        UUID  FK → programme_uploads(id)  ON DELETE SET NULL
  source           VARCHAR(20)   "ai" | "keyword_boost"
  pipeline_stage   VARCHAR(30)   "classify_assets"
  model_name       VARCHAR(100)  e.g. "claude-haiku-4-5-20251001"
  fallback_used    BOOLEAN       True when AI was unavailable
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # upload_id FK — nullable so historical rows without an upload are preserved
    op.add_column(
        "ai_suggestion_logs",
        sa.Column(
            "upload_id",
            sa.UUID(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_ai_suggestion_logs_upload_id",
        "ai_suggestion_logs",
        ["upload_id"],
    )
    op.create_foreign_key(
        "ai_suggestion_logs_upload_id_fkey",
        "ai_suggestion_logs",
        "programme_uploads",
        ["upload_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Observability columns — no FKs, just plain scalars
    op.add_column(
        "ai_suggestion_logs",
        sa.Column("source", sa.String(20), nullable=True),
    )
    op.add_column(
        "ai_suggestion_logs",
        sa.Column("pipeline_stage", sa.String(30), nullable=True),
    )
    op.add_column(
        "ai_suggestion_logs",
        sa.Column("model_name", sa.String(100), nullable=True),
    )
    op.add_column(
        "ai_suggestion_logs",
        sa.Column("fallback_used", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_suggestion_logs", "fallback_used")
    op.drop_column("ai_suggestion_logs", "model_name")
    op.drop_column("ai_suggestion_logs", "pipeline_stage")
    op.drop_column("ai_suggestion_logs", "source")

    op.drop_constraint(
        "ai_suggestion_logs_upload_id_fkey",
        "ai_suggestion_logs",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_ai_suggestion_logs_upload_id",
        table_name="ai_suggestion_logs",
    )
    op.drop_column("ai_suggestion_logs", "upload_id")
