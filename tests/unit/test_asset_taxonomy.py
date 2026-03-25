"""
Unit tests for Stage 3 — Asset Taxonomy Foundation.

Tests:
  - Seed data completeness: all 11 types present in bootstrap constants
  - max_hours_per_day values match architecture plan groupings
  - 'none' has strictly zero max_hours_per_day
  - get_active_asset_types falls back to bootstrap set on failure
  - get_max_hours_for_type falls back to bootstrap dict on failure
  - AssetTypeCreate schema validation (code pattern, max_hours bounds)
  - AssetTypeUpdate partial update (exclude_unset)
  - CRUD get_active_codes returns correct frozenset
  - CRUD get_max_hours returns correct value
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from app.core.constants import (
    ALLOWED_ASSET_TYPES,
    DEFAULT_MAX_HOURS_PER_DAY,
    get_active_asset_types,
    get_max_hours_for_type,
)
from app.schemas.asset_type import AssetTypeCreate, AssetTypeUpdate, AssetTypeResponse


# ─── Bootstrap constant completeness ────────────────────────────────────────

class TestSeedData:
    """Verify that bootstrap constants match the architecture plan's seed set."""

    EXPECTED_CODES = {
        "crane", "hoist", "loading_bay", "ewp", "concrete_pump",
        "excavator", "forklift", "telehandler", "compactor",
        "other", "none",
    }

    def test_allowed_types_match_seed(self):
        assert ALLOWED_ASSET_TYPES == self.EXPECTED_CODES

    def test_max_hours_keys_match_seed(self):
        assert set(DEFAULT_MAX_HOURS_PER_DAY.keys()) == self.EXPECTED_CODES

    @pytest.mark.parametrize("code,expected", [
        ("crane",         10.0),
        ("hoist",         10.0),
        ("loading_bay",   10.0),
        ("concrete_pump", 10.0),
        ("ewp",           16.0),
        ("excavator",     16.0),
        ("forklift",      16.0),
        ("telehandler",   16.0),
        ("compactor",     16.0),
        ("other",         16.0),
        ("none",           0.0),
    ])
    def test_max_hours_values(self, code: str, expected: float):
        assert DEFAULT_MAX_HOURS_PER_DAY[code] == expected

    def test_none_is_strictly_zero(self):
        assert DEFAULT_MAX_HOURS_PER_DAY["none"] == 0.0

    def test_none_in_allowed_types(self):
        """Architecture plan requires 'none' in all validation paths."""
        assert "none" in ALLOWED_ASSET_TYPES


# ─── Fallback behaviour ─────────────────────────────────────────────────────

class TestFallbacks:
    """Verify graceful fallback when DB is unavailable."""

    def test_get_active_asset_types_fallback_on_import_error(self):
        db = MagicMock(spec=Session)
        with patch("app.core.constants.get_active_codes", side_effect=Exception("no DB")):
            result = get_active_asset_types(db)
        assert result == ALLOWED_ASSET_TYPES

    def test_get_max_hours_fallback_on_error(self):
        db = MagicMock(spec=Session)
        with patch("app.core.constants.get_max_hours", side_effect=Exception("no DB")):
            result = get_max_hours_for_type(db, "crane")
        assert result == 10.0

    def test_get_max_hours_fallback_unknown_code(self):
        db = MagicMock(spec=Session)
        with patch("app.core.constants.get_max_hours", return_value=None):
            result = get_max_hours_for_type(db, "unknown_type")
        # Unknown type falls back to 16.0 default
        assert result == 16.0


# ─── Schema validation ──────────────────────────────────────────────────────

class TestAssetTypeSchemas:

    def test_create_valid(self):
        obj = AssetTypeCreate(
            code="scaffold",
            display_name="Scaffolding",
            max_hours_per_day=Decimal("12.0"),
        )
        assert obj.code == "scaffold"
        assert obj.max_hours_per_day == Decimal("12.0")

    def test_create_code_rejects_uppercase(self):
        with pytest.raises(Exception):
            AssetTypeCreate(
                code="Scaffold",
                display_name="Scaffolding",
                max_hours_per_day=Decimal("12.0"),
            )

    def test_create_code_rejects_spaces(self):
        with pytest.raises(Exception):
            AssetTypeCreate(
                code="loading bay",
                display_name="Loading Bay",
                max_hours_per_day=Decimal("10.0"),
            )

    def test_create_max_hours_rounds_to_one_decimal(self):
        obj = AssetTypeCreate(
            code="test_type",
            display_name="Test",
            max_hours_per_day=Decimal("10.55"),
        )
        assert obj.max_hours_per_day == Decimal("10.6")

    def test_create_max_hours_rejects_negative(self):
        with pytest.raises(Exception):
            AssetTypeCreate(
                code="test_type",
                display_name="Test",
                max_hours_per_day=Decimal("-1"),
            )

    def test_create_max_hours_rejects_over_24(self):
        with pytest.raises(Exception):
            AssetTypeCreate(
                code="test_type",
                display_name="Test",
                max_hours_per_day=Decimal("25"),
            )

    def test_update_partial(self):
        obj = AssetTypeUpdate(display_name="Updated Name")
        dumped = obj.model_dump(exclude_unset=True)
        assert dumped == {"display_name": "Updated Name"}
        assert "max_hours_per_day" not in dumped

    def test_update_max_hours_rounds(self):
        obj = AssetTypeUpdate(max_hours_per_day=Decimal("8.75"))
        assert obj.max_hours_per_day == Decimal("8.8")


# ─── CRUD unit tests (mocked DB) ────────────────────────────────────────────

class TestAssetTypeCRUD:

    def test_get_active_codes(self):
        db = MagicMock(spec=Session)
        # Mock the query chain: db.query().filter().all()
        mock_rows = [("crane",), ("hoist",), ("none",)]
        db.query.return_value.filter.return_value.all.return_value = mock_rows

        from app.crud.asset_type import get_active_codes
        result = get_active_codes(db)
        assert result == frozenset({"crane", "hoist", "none"})

    def test_get_max_hours_found(self):
        db = MagicMock(spec=Session)
        db.query.return_value.filter.return_value.first.return_value = (Decimal("10.0"),)

        from app.crud.asset_type import get_max_hours
        result = get_max_hours(db, "crane")
        assert result == 10.0

    def test_get_max_hours_not_found(self):
        db = MagicMock(spec=Session)
        db.query.return_value.filter.return_value.first.return_value = None

        from app.crud.asset_type import get_max_hours
        result = get_max_hours(db, "nonexistent")
        assert result is None


# ─── Exclusive vs. fungible grouping ─────────────────────────────────────────

class TestTaxonomyGrouping:
    """Verify the plan's grouping of asset types by max_hours_per_day."""

    EXCLUSIVE_TYPES = {"crane", "hoist", "loading_bay", "concrete_pump"}
    FUNGIBLE_TYPES = {"ewp", "excavator", "forklift", "telehandler", "compactor", "other"}

    def test_exclusive_group_all_10h(self):
        for code in self.EXCLUSIVE_TYPES:
            assert DEFAULT_MAX_HOURS_PER_DAY[code] == 10.0, f"{code} should be 10h"

    def test_fungible_group_all_16h(self):
        for code in self.FUNGIBLE_TYPES:
            assert DEFAULT_MAX_HOURS_PER_DAY[code] == 16.0, f"{code} should be 16h"

    def test_none_group_zero(self):
        assert DEFAULT_MAX_HOURS_PER_DAY["none"] == 0.0

    def test_groups_cover_all_types(self):
        covered = self.EXCLUSIVE_TYPES | self.FUNGIBLE_TYPES | {"none"}
        assert covered == ALLOWED_ASSET_TYPES
