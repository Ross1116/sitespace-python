"""multi asset requirements

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-05-11
"""

from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, None] = "t0u1v2w3x4y5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activity_asset_mappings", sa.Column("asset_role", sa.String(length=20), nullable=True))
    op.add_column("activity_asset_mappings", sa.Column("estimated_total_hours", sa.Numeric(8, 2), nullable=True))
    op.add_column("activity_asset_mappings", sa.Column("profile_shape", sa.String(length=50), nullable=True))
    op.add_column("activity_asset_mappings", sa.Column("label_confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("activity_asset_mappings", sa.Column("requirement_source", sa.String(length=20), nullable=True))
    op.add_column(
        "activity_asset_mappings",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_check_constraint(
        "ck_activity_asset_mappings_asset_role",
        "activity_asset_mappings",
        "asset_role IS NULL OR asset_role IN ('lead', 'support', 'incidental')",
    )
    op.create_check_constraint(
        "ck_activity_asset_mappings_requirement_source",
        "activity_asset_mappings",
        "requirement_source IS NULL OR requirement_source IN ('ai', 'keyword', 'manual', 'imported_gold')",
    )
    op.create_check_constraint(
        "ck_activity_asset_mappings_label_confidence",
        "activity_asset_mappings",
        "label_confidence IS NULL OR (label_confidence >= 0 AND label_confidence <= 1)",
    )

    op.execute(
        sa.text(
            """
            UPDATE activity_asset_mappings
            SET asset_role = COALESCE(asset_role, CASE WHEN asset_type IS NULL THEN NULL ELSE 'lead' END),
                label_confidence = COALESCE(
                    label_confidence,
                    CASE confidence
                        WHEN 'high' THEN 0.85
                        WHEN 'medium' THEN 0.55
                        WHEN 'low' THEN 0.25
                        ELSE 0.55
                    END
                ),
                requirement_source = COALESCE(requirement_source, source),
                is_active = true
            """
        )
    )

    op.add_column(
        "activity_work_profiles",
        sa.Column("activity_asset_mapping_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_activity_work_profiles_activity_asset_mapping_id"),
        "activity_work_profiles",
        ["activity_asset_mapping_id"],
    )
    op.create_foreign_key(
        "fk_activity_work_profiles_activity_asset_mapping_id",
        "activity_work_profiles",
        "activity_asset_mappings",
        ["activity_asset_mapping_id"],
        ["id"],
        ondelete="CASCADE",
    )

    conn = op.get_bind()
    missing_profiles = conn.execute(
        sa.text(
            """
            SELECT awp.id, awp.activity_id, awp.asset_type, awp.total_hours
            FROM activity_work_profiles awp
            WHERE NOT EXISTS (
                SELECT 1
                FROM activity_asset_mappings aam
                WHERE aam.programme_activity_id = awp.activity_id
                  AND aam.asset_type = awp.asset_type
                  AND aam.is_active = true
            )
            """
        )
    ).mappings()
    for row in missing_profiles:
        conn.execute(
            sa.text(
                """
                INSERT INTO activity_asset_mappings (
                    id, programme_activity_id, asset_type, confidence, source,
                    auto_committed, manually_corrected, asset_role,
                    estimated_total_hours, label_confidence, requirement_source, is_active
                )
                VALUES (
                    :id, :activity_id, :asset_type, 'medium', 'manual',
                    true, true, 'lead', :total_hours, 0.55, 'manual', true
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "activity_id": row["activity_id"],
                "asset_type": row["asset_type"],
                "total_hours": row["total_hours"],
            },
        )

    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    awp.id AS profile_id,
                    aam.id AS mapping_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY awp.id
                        ORDER BY aam.manually_corrected DESC, aam.auto_committed DESC, aam.created_at DESC
                    ) AS rn
                FROM activity_work_profiles awp
                JOIN activity_asset_mappings aam
                  ON aam.programme_activity_id = awp.activity_id
                 AND aam.asset_type = awp.asset_type
                 AND aam.is_active = true
            )
            UPDATE activity_work_profiles awp
            SET activity_asset_mapping_id = ranked.mapping_id
            FROM ranked
            WHERE ranked.profile_id = awp.id
              AND ranked.rn = 1
            """
        )
    )
    op.drop_constraint("uq_activity_work_profiles_activity_id", "activity_work_profiles", type_="unique")
    op.create_unique_constraint(
        "uq_activity_work_profiles_activity_asset_mapping_id",
        "activity_work_profiles",
        ["activity_asset_mapping_id"],
    )

    op.add_column(
        "activity_booking_groups",
        sa.Column("activity_asset_mapping_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_activity_booking_groups_activity_asset_mapping_id"),
        "activity_booking_groups",
        ["activity_asset_mapping_id"],
    )
    op.create_foreign_key(
        "fk_activity_booking_groups_activity_asset_mapping_id",
        "activity_booking_groups",
        "activity_asset_mappings",
        ["activity_asset_mapping_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    abg.id AS booking_group_id,
                    aam.id AS mapping_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY abg.id
                        ORDER BY
                            CASE WHEN aam.asset_type = abg.expected_asset_type THEN 0 ELSE 1 END,
                            CASE WHEN aam.asset_role = 'lead' THEN 0 ELSE 1 END,
                            aam.manually_corrected DESC,
                            aam.auto_committed DESC,
                            aam.created_at DESC
                    ) AS rn
                FROM activity_booking_groups abg
                JOIN activity_asset_mappings aam
                  ON aam.programme_activity_id = abg.programme_activity_id
                 AND aam.asset_type IS NOT NULL
                 AND aam.is_active = true
            )
            UPDATE activity_booking_groups abg
            SET activity_asset_mapping_id = ranked.mapping_id
            FROM ranked
            WHERE ranked.booking_group_id = abg.id
              AND ranked.rn = 1
            """
        )
    )
    op.drop_constraint("uq_activity_booking_groups_activity", "activity_booking_groups", type_="unique")
    op.create_unique_constraint(
        "uq_activity_booking_groups_mapping",
        "activity_booking_groups",
        ["activity_asset_mapping_id"],
    )

    op.create_table(
        "item_asset_requirements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.String(length=50), nullable=False),
        sa.Column("default_role", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.String(length=10), nullable=False, server_default="medium"),
        sa.Column("label_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("support_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["asset_type"], ["asset_types.code"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "asset_type", "is_active", name="uq_item_asset_requirements_item_asset_active"),
        sa.CheckConstraint(
            "default_role IS NULL OR default_role IN ('lead', 'support', 'incidental')",
            name="ck_item_asset_requirements_role",
        ),
        sa.CheckConstraint("confidence IN ('high', 'medium', 'low')", name="ck_item_asset_requirements_confidence"),
        sa.CheckConstraint(
            "source IN ('ai', 'keyword', 'manual', 'imported_gold')",
            name="ck_item_asset_requirements_source",
        ),
        sa.CheckConstraint(
            "label_confidence IS NULL OR (label_confidence >= 0 AND label_confidence <= 1)",
            name="ck_item_asset_requirements_label_confidence",
        ),
    )
    op.create_index(op.f("ix_item_asset_requirements_item_id"), "item_asset_requirements", ["item_id"])
    op.create_index(op.f("ix_item_asset_requirements_is_active"), "item_asset_requirements", ["is_active"])

    op.create_table(
        "item_asset_requirement_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("old_asset_type", sa.String(length=50), nullable=True),
        sa.Column("new_asset_type", sa.String(length=50), nullable=True),
        sa.Column("old_role", sa.String(length=20), nullable=True),
        sa.Column("new_role", sa.String(length=20), nullable=True),
        sa.Column("triggered_by_upload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("performed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["performed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requirement_id"], ["item_asset_requirements.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by_upload_id"], ["programme_uploads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "event_type IN ('created','confirmed','corrected','deactivated','merged')",
            name="ck_item_asset_requirement_events_type",
        ),
    )
    op.create_index(op.f("ix_item_asset_requirement_events_item_id"), "item_asset_requirement_events", ["item_id"])
    op.create_index(
        op.f("ix_item_asset_requirement_events_requirement_id"),
        "item_asset_requirement_events",
        ["requirement_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_item_asset_requirement_events_requirement_id"), table_name="item_asset_requirement_events")
    op.drop_index(op.f("ix_item_asset_requirement_events_item_id"), table_name="item_asset_requirement_events")
    op.drop_table("item_asset_requirement_events")
    op.drop_index(op.f("ix_item_asset_requirements_is_active"), table_name="item_asset_requirements")
    op.drop_index(op.f("ix_item_asset_requirements_item_id"), table_name="item_asset_requirements")
    op.drop_table("item_asset_requirements")

    op.drop_constraint("uq_activity_booking_groups_mapping", "activity_booking_groups", type_="unique")
    op.create_unique_constraint(
        "uq_activity_booking_groups_activity",
        "activity_booking_groups",
        ["programme_activity_id"],
    )
    op.drop_constraint("fk_activity_booking_groups_activity_asset_mapping_id", "activity_booking_groups", type_="foreignkey")
    op.drop_index(op.f("ix_activity_booking_groups_activity_asset_mapping_id"), table_name="activity_booking_groups")
    op.drop_column("activity_booking_groups", "activity_asset_mapping_id")

    op.drop_constraint("uq_activity_work_profiles_activity_asset_mapping_id", "activity_work_profiles", type_="unique")
    op.create_unique_constraint(
        "uq_activity_work_profiles_activity_id",
        "activity_work_profiles",
        ["activity_id"],
    )
    op.drop_constraint("fk_activity_work_profiles_activity_asset_mapping_id", "activity_work_profiles", type_="foreignkey")
    op.drop_index(op.f("ix_activity_work_profiles_activity_asset_mapping_id"), table_name="activity_work_profiles")
    op.drop_column("activity_work_profiles", "activity_asset_mapping_id")

    op.drop_constraint("ck_activity_asset_mappings_label_confidence", "activity_asset_mappings", type_="check")
    op.drop_constraint("ck_activity_asset_mappings_requirement_source", "activity_asset_mappings", type_="check")
    op.drop_constraint("ck_activity_asset_mappings_asset_role", "activity_asset_mappings", type_="check")
    op.drop_column("activity_asset_mappings", "is_active")
    op.drop_column("activity_asset_mappings", "requirement_source")
    op.drop_column("activity_asset_mappings", "label_confidence")
    op.drop_column("activity_asset_mappings", "profile_shape")
    op.drop_column("activity_asset_mappings", "estimated_total_hours")
    op.drop_column("activity_asset_mappings", "asset_role")
