"""fix_asset_type_max_hours

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-03-26

Correct three max_hours_per_day seed values to better reflect real-world
Australian construction-site constraints:

  ewp          16.0 → 12.0  EWPs require certified operators; double-shift
                             ceiling is ~12 hrs, not 16.
  loading_bay  10.0 → 12.0  Loading bays are zone-based, not operator-limited;
                             they routinely service extended-shift sites.
  compactor    16.0 → 10.0  Compactors generate significant noise/vibration and
                             are typically restricted to daylight hours (~10 hrs)
                             by council/EPA permits on most urban sites.
"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

_asset_types = sa.table(
    "asset_types",
    sa.column("code", sa.String),
    sa.column("max_hours_per_day", sa.Numeric),
)

_UPDATES = [
    ("ewp",         12.0),
    ("loading_bay", 12.0),
    ("compactor",   10.0),
]


def upgrade() -> None:
    for code, hours in _UPDATES:
        op.execute(
            _asset_types.update()
            .where(_asset_types.c.code == code)
            .values(max_hours_per_day=hours)
        )


def downgrade() -> None:
    _ORIGINALS = [
        ("ewp",         16.0),
        ("loading_bay", 10.0),
        ("compactor",   16.0),
    ]
    for code, hours in _ORIGINALS:
        op.execute(
            _asset_types.update()
            .where(_asset_types.c.code == code)
            .values(max_hours_per_day=hours)
        )
