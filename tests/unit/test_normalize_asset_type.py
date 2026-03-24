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


# ---------------------------------------------------------------------------
# Crane
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "crane",
    "Crane",
    "Tower Crane",
    "Mobile Crane",
    "Luffing Crane",
    "Crawler Crane",
    "Pick and Carry",
    "Pick-and-Carry",
])
def test_crane_normalization(raw):
    assert normalize_asset_type(raw) == "crane"


# ---------------------------------------------------------------------------
# Hoist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "hoist",
    "Builder's Hoist",
    "Builders Hoist",
    "Personnel Hoist",
    "Materials Hoist",
    "Material Hoist",
    "Construction Lift",
])
def test_hoist_normalization(raw):
    assert normalize_asset_type(raw) == "hoist"


# ---------------------------------------------------------------------------
# EWP
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "EWP",
    "ewp",
    "Elevated Work Platform",
    "Scissor Lift",
    "Boom Lift",
    "Knuckle Lift",
    "Knuckle Boom",
    "Cherry Picker",
    "Man Lift",
])
def test_ewp_normalization(raw):
    assert normalize_asset_type(raw) == "ewp"


# ---------------------------------------------------------------------------
# Concrete pump
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Concrete Pump",
    "concrete pump",
    "concrete_pump",
    "Boom Pump",
    "Line Pump",
    "Kibble",
])
def test_concrete_pump_normalization(raw):
    assert normalize_asset_type(raw) == "concrete_pump"


# ---------------------------------------------------------------------------
# Excavator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Excavator",
    "EXCAVATOR",
    "Mini Excavator",
    "Backhoe",
    "Digger",
])
def test_excavator_normalization(raw):
    assert normalize_asset_type(raw) == "excavator"


# ---------------------------------------------------------------------------
# Forklift
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Forklift",
    "Rough Terrain Forklift",
])
def test_forklift_normalization(raw):
    assert normalize_asset_type(raw) == "forklift"


# ---------------------------------------------------------------------------
# Telehandler
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Telehandler",
    "Reach Forklift",
    "Telescopic Forklift",
    "Telescopic Handler",
])
def test_telehandler_normalization(raw):
    assert normalize_asset_type(raw) == "telehandler"


# ---------------------------------------------------------------------------
# Compactor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Compactor",
    "Roller",
    "Plate Compactor",
    "Vibrating Plate",
])
def test_compactor_normalization(raw):
    assert normalize_asset_type(raw) == "compactor"


# ---------------------------------------------------------------------------
# Loading bay
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "Loading Bay",
    "Unloading Bay",
    "Loading Zone",
    "loading_bay",   # canonical underscore form must round-trip
])
def test_loading_bay_normalization(raw):
    assert normalize_asset_type(raw) == "loading_bay"


# ---------------------------------------------------------------------------
# Non-mappable types (return None)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "EQUIPMENT",        # generic PM bucket label
    "Storage Area",     # materials system, not bookable plant
    "",                 # empty string
    "   ",              # whitespace only
    "Concrete Saw",     # unknown freetext
    "none",             # internal sentinel value
    "Structural Steel", # material type, not plant
    "Scaffolding",      # labour + materials, not a discrete plant item
])
def test_non_mappable_returns_none(raw):
    assert normalize_asset_type(raw) is None
