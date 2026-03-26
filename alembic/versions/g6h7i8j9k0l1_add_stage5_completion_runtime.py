"""add_stage5_completion_runtime

Revision ID: g6h7i8j9k0l1
Revises: f5g6h7i8j9k0
Create Date: 2026-03-26

Stage 5 completion runtime support.

Adds:
  work_profile_ai_logs
  lookahead_rows
  project_alert_policies
  subcontractor_asset_type_assignments
  processing_outcome on programme_uploads
  week_start + severity_score on notifications
  unique activity_work_profiles.activity_id

Seeds:
  inference_policies row for version=2 (real work-profile AI bundle)
  project_alert_policies defaults for existing projects
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "g6h7i8j9k0l1"
down_revision = "f5g6h7i8j9k0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO inference_policies
            (version, model_name, model_family, prompt_version,
             validation_rules_version, pattern_library_version, hours_policy_version)
        SELECT
            2, 'claude-haiku-4-5-20251001', 'claude', 'work_profile_v2',
            'rules_v2', 'patterns_v1', 'hours_v1'
        WHERE NOT EXISTS (
            SELECT 1 FROM inference_policies WHERE version = 2
        )
        """
    )

    op.add_column(
        "programme_uploads",
        sa.Column("processing_outcome", sa.String(length=30), nullable=True),
    )
    op.execute(
        """
        UPDATE programme_uploads
        SET processing_outcome = CASE
            WHEN status = 'degraded' THEN 'completed_with_warnings'
            WHEN status = 'committed' THEN 'committed'
            ELSE 'processing'
        END
        """
    )

    op.add_column(
        "notifications",
        sa.Column("week_start", sa.Date(), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("severity_score", sa.Numeric(10, 4), nullable=True),
    )
    op.create_index(
        "ix_notifications_project_week_asset",
        "notifications",
        ["project_id", "week_start", "asset_type"],
    )
    op.create_index(
        "ix_notifications_active_lookahead",
        "notifications",
        ["project_id", "sub_id", "asset_type", "week_start"],
        unique=True,
        postgresql_where=sa.text(
            "trigger_type = 'lookahead' AND status IN ('pending', 'sent')"
        ),
    )

    op.create_table(
        "work_profile_ai_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "activity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("programme_activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column(
            "inference_version",
            sa.SmallInteger,
            sa.ForeignKey("inference_policies.version", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("request_json", JSONB, nullable=False),
        sa.Column("response_json", JSONB, nullable=True),
        sa.Column("validation_errors_json", JSONB, nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("retry_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_work_profile_ai_logs_activity_id",
        "work_profile_ai_logs",
        ["activity_id"],
    )
    op.create_index(
        "ix_work_profile_ai_logs_item_id",
        "work_profile_ai_logs",
        ["item_id"],
    )
    op.create_index(
        "ix_work_profile_ai_logs_context_hash",
        "work_profile_ai_logs",
        ["context_hash"],
    )

    op.create_table(
        "project_alert_policies",
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mode", sa.String(20), nullable=False, server_default="observe_only"),
        sa.Column("external_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("min_demand_hours", sa.Numeric(10, 2), nullable=False, server_default="8"),
        sa.Column("min_gap_hours", sa.Numeric(10, 2), nullable=False, server_default="8"),
        sa.Column("min_gap_ratio", sa.Numeric(6, 4), nullable=False, server_default="0.25"),
        sa.Column("min_lead_weeks", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "max_alerts_per_subcontractor_per_week",
            sa.SmallInteger(),
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "max_alerts_per_project_per_week",
            sa.SmallInteger(),
            nullable=False,
            server_default="20",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('observe_only', 'thresholded', 'active')",
            name="ck_project_alert_policies_mode",
        ),
    )
    op.execute(
        """
        INSERT INTO project_alert_policies
            (project_id, mode, external_enabled, min_demand_hours, min_gap_hours,
             min_gap_ratio, min_lead_weeks, max_alerts_per_subcontractor_per_week,
             max_alerts_per_project_per_week)
        SELECT
            id, 'observe_only', FALSE, 8, 8, 0.25, 1, 3, 20
        FROM site_projects
        WHERE NOT EXISTS (
            SELECT 1 FROM project_alert_policies p WHERE p.project_id = site_projects.id
        )
        """
    )

    op.create_table(
        "subcontractor_asset_type_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subcontractor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subcontractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_type", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id",
            "subcontractor_id",
            "asset_type",
            name="uq_subcontractor_asset_type_assignments",
        ),
    )
    op.create_index(
        "ix_subcontractor_asset_type_assignments_project_id",
        "subcontractor_asset_type_assignments",
        ["project_id"],
    )
    op.create_index(
        "ix_subcontractor_asset_type_assignments_subcontractor_id",
        "subcontractor_asset_type_assignments",
        ["subcontractor_id"],
    )
    op.create_index(
        "ix_subcontractor_asset_type_assignments_asset_type",
        "subcontractor_asset_type_assignments",
        ["asset_type"],
    )

    op.create_table(
        "lookahead_rows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("lookahead_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("site_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("asset_type", sa.String(50), nullable=False),
        sa.Column("demand_hours", sa.Numeric(10, 2), nullable=False),
        sa.Column("booked_hours", sa.Numeric(10, 2), nullable=False),
        sa.Column("gap_hours", sa.Numeric(10, 2), nullable=False),
        sa.Column("is_anomalous", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("anomaly_flags_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "week_start",
            "asset_type",
            name="uq_lookahead_rows_snapshot_week_asset",
        ),
        sa.CheckConstraint("demand_hours >= 0", name="ck_lookahead_rows_demand_hours"),
        sa.CheckConstraint("booked_hours >= 0", name="ck_lookahead_rows_booked_hours"),
        sa.CheckConstraint("gap_hours >= 0", name="ck_lookahead_rows_gap_hours"),
    )
    op.create_index("ix_lookahead_rows_snapshot_id", "lookahead_rows", ["snapshot_id"])
    op.create_index("ix_lookahead_rows_project_id", "lookahead_rows", ["project_id"])
    op.create_index("ix_lookahead_rows_week_start", "lookahead_rows", ["week_start"])

    op.create_unique_constraint(
        "uq_activity_work_profiles_activity_id",
        "activity_work_profiles",
        ["activity_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_activity_work_profiles_activity_id",
        "activity_work_profiles",
        type_="unique",
    )

    op.drop_index("ix_lookahead_rows_week_start", table_name="lookahead_rows")
    op.drop_index("ix_lookahead_rows_project_id", table_name="lookahead_rows")
    op.drop_index("ix_lookahead_rows_snapshot_id", table_name="lookahead_rows")
    op.drop_table("lookahead_rows")

    op.drop_index(
        "ix_subcontractor_asset_type_assignments_asset_type",
        table_name="subcontractor_asset_type_assignments",
    )
    op.drop_index(
        "ix_subcontractor_asset_type_assignments_subcontractor_id",
        table_name="subcontractor_asset_type_assignments",
    )
    op.drop_index(
        "ix_subcontractor_asset_type_assignments_project_id",
        table_name="subcontractor_asset_type_assignments",
    )
    op.drop_table("subcontractor_asset_type_assignments")

    op.drop_table("project_alert_policies")

    op.drop_index("ix_work_profile_ai_logs_context_hash", table_name="work_profile_ai_logs")
    op.drop_index("ix_work_profile_ai_logs_item_id", table_name="work_profile_ai_logs")
    op.drop_index("ix_work_profile_ai_logs_activity_id", table_name="work_profile_ai_logs")
    op.drop_table("work_profile_ai_logs")

    op.drop_index("ix_notifications_active_lookahead", table_name="notifications")
    op.drop_index("ix_notifications_project_week_asset", table_name="notifications")
    op.drop_column("notifications", "severity_score")
    op.drop_column("notifications", "week_start")

    op.drop_column("programme_uploads", "processing_outcome")

    op.execute("DELETE FROM inference_policies WHERE version = 2")
