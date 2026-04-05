"""set realistic asset type max hours

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-04-06

Apply conservative, realistic max_hours_per_day defaults in 30-minute
increments so capacity planning does not overstate supply when a project asset
does not have its own per-asset override.
"""

import sqlalchemy as sa
from alembic import op


revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


_asset_types = sa.table(
    "asset_types",
    sa.column("code", sa.String),
    sa.column("max_hours_per_day", sa.Numeric),
)

_UPDATED_VALUES = [
    ("crane", 10.0),
    ("hoist", 10.5),
    ("loading_bay", 12.0),
    ("ewp", 10.0),
    ("concrete_pump", 10.0),
    ("excavator", 11.0),
    ("forklift", 11.0),
    ("telehandler", 11.0),
    ("compactor", 9.5),
    ("other", 10.0),
    ("none", 0.0),
]

_PREVIOUS_VALUES = [
    ("crane", 10.0),
    ("hoist", 10.0),
    ("loading_bay", 12.0),
    ("ewp", 12.0),
    ("concrete_pump", 10.0),
    ("excavator", 16.0),
    ("forklift", 16.0),
    ("telehandler", 16.0),
    ("compactor", 10.0),
    ("other", 16.0),
    ("none", 0.0),
]


def _apply_values(rows: list[tuple[str, float]]) -> None:
    for code, hours in rows:
        op.execute(
            _asset_types.update()
            .where(_asset_types.c.code == code)
            .values(max_hours_per_day=hours)
        )


def upgrade() -> None:
    _apply_values(_UPDATED_VALUES)


def downgrade() -> None:
    _apply_values(_PREVIOUS_VALUES)
