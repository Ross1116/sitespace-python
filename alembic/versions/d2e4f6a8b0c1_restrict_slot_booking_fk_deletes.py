"""restrict_slot_booking_fk_deletes

Revision ID: d2e4f6a8b0c1
Revises: 1c2d3e4f5a6b
Create Date: 2026-02-18

Switch slot_bookings foreign keys away from ON DELETE CASCADE.

Rationale:
- Deleting projects/users/assets/subcontractors should not cascade-delete
  bookings, which can destroy historical records and conflict with booking
  audit logs.

This migration recreates the FK constraints with ON DELETE RESTRICT.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2e4f6a8b0c1"
down_revision: Union[str, None] = "1c2d3e4f5a6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop existing FK constraints (default Postgres naming)
    op.drop_constraint("slot_bookings_project_id_fkey", "slot_bookings", type_="foreignkey")
    op.drop_constraint("slot_bookings_manager_id_fkey", "slot_bookings", type_="foreignkey")
    op.drop_constraint("slot_bookings_subcontractor_id_fkey", "slot_bookings", type_="foreignkey")
    op.drop_constraint("slot_bookings_asset_id_fkey", "slot_bookings", type_="foreignkey")

    # Recreate with RESTRICT
    op.create_foreign_key(
        "slot_bookings_project_id_fkey",
        "slot_bookings",
        "site_projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "slot_bookings_manager_id_fkey",
        "slot_bookings",
        "users",
        ["manager_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "slot_bookings_subcontractor_id_fkey",
        "slot_bookings",
        "subcontractors",
        ["subcontractor_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "slot_bookings_asset_id_fkey",
        "slot_bookings",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Revert to CASCADE
    op.drop_constraint("slot_bookings_project_id_fkey", "slot_bookings", type_="foreignkey")
    op.drop_constraint("slot_bookings_manager_id_fkey", "slot_bookings", type_="foreignkey")
    op.drop_constraint("slot_bookings_subcontractor_id_fkey", "slot_bookings", type_="foreignkey")
    op.drop_constraint("slot_bookings_asset_id_fkey", "slot_bookings", type_="foreignkey")

    op.create_foreign_key(
        "slot_bookings_project_id_fkey",
        "slot_bookings",
        "site_projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "slot_bookings_manager_id_fkey",
        "slot_bookings",
        "users",
        ["manager_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "slot_bookings_subcontractor_id_fkey",
        "slot_bookings",
        "subcontractors",
        ["subcontractor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "slot_bookings_asset_id_fkey",
        "slot_bookings",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="CASCADE",
    )
