"""
Unit tests for normalize_asset_type().

This function maps raw asset.type strings (free text entered by project
managers) to canonical ALLOWED_ASSET_TYPES values.  It is used to:
  - determine valid_types when scoping classification to project assets
  - normalise booked-hours buckets in the lookahead demand engine

Project managers enter asset types as free text.  The same physical asset
appears under many names across projects: "Tower Crane", "Mobile Crane",
"Luffing Crane", "Crane" all mean the same bookable asset.  Similarly,
some asset registers use a generic bucket label like "EQUIPMENT" for items
that should be classified individually by name, or "Storage Area" for
materials systems that are not bookable plant at all.

These tests verify that all known alias phrasings resolve correctly and that
non-bookable entries return None (the signal to exclude from valid_types).
"""

import pytest
from app.services.ai_service import normalize_asset_type


class TestCraneNormalization:
    def test_crane_lowercase(self):
        assert normalize_asset_type("crane") == "crane"

    def test_crane_titlecase(self):
        assert normalize_asset_type("Crane") == "crane"

    def test_tower_crane(self):
        assert normalize_asset_type("Tower Crane") == "crane"

    def test_mobile_crane(self):
        assert normalize_asset_type("Mobile Crane") == "crane"

    def test_luffing_crane(self):
        assert normalize_asset_type("Luffing Crane") == "crane"

    def test_crawler_crane(self):
        assert normalize_asset_type("Crawler Crane") == "crane"

    def test_pick_and_carry(self):
        assert normalize_asset_type("Pick and Carry") == "crane"

    def test_pick_and_carry_hyphenated(self):
        assert normalize_asset_type("Pick-and-Carry") == "crane"


class TestHoistNormalization:
    def test_hoist_lowercase(self):
        assert normalize_asset_type("hoist") == "hoist"

    def test_builders_hoist_apostrophe(self):
        assert normalize_asset_type("Builder's Hoist") == "hoist"

    def test_builders_hoist_no_apostrophe(self):
        assert normalize_asset_type("Builders Hoist") == "hoist"

    def test_personnel_hoist(self):
        assert normalize_asset_type("Personnel Hoist") == "hoist"

    def test_materials_hoist(self):
        assert normalize_asset_type("Materials Hoist") == "hoist"

    def test_material_hoist(self):
        assert normalize_asset_type("Material Hoist") == "hoist"

    def test_construction_lift(self):
        assert normalize_asset_type("Construction Lift") == "hoist"


class TestEwpNormalization:
    def test_ewp_uppercase(self):
        assert normalize_asset_type("EWP") == "ewp"

    def test_ewp_lowercase(self):
        assert normalize_asset_type("ewp") == "ewp"

    def test_elevated_work_platform(self):
        assert normalize_asset_type("Elevated Work Platform") == "ewp"

    def test_scissor_lift(self):
        assert normalize_asset_type("Scissor Lift") == "ewp"

    def test_boom_lift(self):
        assert normalize_asset_type("Boom Lift") == "ewp"

    def test_knuckle_lift(self):
        assert normalize_asset_type("Knuckle Lift") == "ewp"

    def test_knuckle_boom(self):
        assert normalize_asset_type("Knuckle Boom") == "ewp"

    def test_cherry_picker(self):
        assert normalize_asset_type("Cherry Picker") == "ewp"

    def test_man_lift(self):
        assert normalize_asset_type("Man Lift") == "ewp"


class TestConcretePumpNormalization:
    def test_concrete_pump_titlecase(self):
        assert normalize_asset_type("Concrete Pump") == "concrete_pump"

    def test_concrete_pump_lowercase(self):
        assert normalize_asset_type("concrete pump") == "concrete_pump"

    def test_concrete_pump_underscored(self):
        assert normalize_asset_type("concrete_pump") == "concrete_pump"

    def test_boom_pump(self):
        assert normalize_asset_type("Boom Pump") == "concrete_pump"

    def test_line_pump(self):
        assert normalize_asset_type("Line Pump") == "concrete_pump"

    def test_kibble(self):
        # Kibble = crane-hung concrete bucket — classified as concrete_pump
        assert normalize_asset_type("Kibble") == "concrete_pump"


class TestExcavatorNormalization:
    def test_excavator_titlecase(self):
        assert normalize_asset_type("Excavator") == "excavator"

    def test_excavator_uppercase(self):
        assert normalize_asset_type("EXCAVATOR") == "excavator"

    def test_mini_excavator(self):
        assert normalize_asset_type("Mini Excavator") == "excavator"

    def test_backhoe(self):
        assert normalize_asset_type("Backhoe") == "excavator"

    def test_digger(self):
        assert normalize_asset_type("Digger") == "excavator"


class TestForkliftNormalization:
    def test_forklift_titlecase(self):
        assert normalize_asset_type("Forklift") == "forklift"

    def test_rough_terrain_forklift(self):
        assert normalize_asset_type("Rough Terrain Forklift") == "forklift"


class TestTelehandlerNormalization:
    def test_telehandler(self):
        assert normalize_asset_type("Telehandler") == "telehandler"

    def test_reach_forklift(self):
        assert normalize_asset_type("Reach Forklift") == "telehandler"

    def test_telescopic_forklift(self):
        assert normalize_asset_type("Telescopic Forklift") == "telehandler"

    def test_telescopic_handler(self):
        assert normalize_asset_type("Telescopic Handler") == "telehandler"


class TestCompactorNormalization:
    def test_compactor(self):
        assert normalize_asset_type("Compactor") == "compactor"

    def test_roller(self):
        assert normalize_asset_type("Roller") == "compactor"

    def test_plate_compactor(self):
        assert normalize_asset_type("Plate Compactor") == "compactor"

    def test_vibrating_plate(self):
        assert normalize_asset_type("Vibrating Plate") == "compactor"


class TestLoadingBayNormalization:
    def test_loading_bay(self):
        assert normalize_asset_type("Loading Bay") == "loading_bay"

    def test_unloading_bay(self):
        assert normalize_asset_type("Unloading Bay") == "loading_bay"

    def test_loading_zone(self):
        assert normalize_asset_type("Loading Zone") == "loading_bay"


class TestNonMappableTypes:
    """
    Types that do not correspond to bookable plant — should return None.
    normalize_asset_type() returning None signals the asset should not
    contribute to valid_types in classification.

    Project managers sometimes enter assets with a catch-all type label
    such as "EQUIPMENT" (when the asset register groups multiple items
    under one type) or "Storage Area" (for materials systems that are not
    individually bookable plant).  These must return None so the lookup
    falls back to the asset name instead of silently filtering the asset out.
    """

    def test_equipment_generic(self):
        # Some asset registers use "EQUIPMENT" as a generic bucket type.
        # normalize_asset_type() returns None; callers then try the asset name.
        assert normalize_asset_type("EQUIPMENT") is None

    def test_storage_area(self):
        # Materials systems registered as "Storage Area" are not bookable
        # plant and must not appear in valid_types.
        assert normalize_asset_type("Storage Area") is None

    def test_empty_string(self):
        assert normalize_asset_type("") is None

    def test_whitespace_only(self):
        assert normalize_asset_type("   ") is None

    def test_unknown_freetext(self):
        assert normalize_asset_type("Concrete Saw") is None

    def test_none_like_string(self):
        # "none" is a special internal sentinel value, not a PM-entered type
        assert normalize_asset_type("none") is None

    def test_material_type_does_not_match(self):
        assert normalize_asset_type("Structural Steel") is None

    def test_scaffold_does_not_match(self):
        # Scaffolding is labour + materials — not a discrete bookable plant item
        assert normalize_asset_type("Scaffolding") is None
