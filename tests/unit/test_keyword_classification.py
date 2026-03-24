"""
Unit tests for _KEYWORD_MAP keyword pre-screening.

Verifies that the keyword boost layer correctly maps activity names to asset
types, with longer/more-specific keys taking priority over shorter ones.

Test cases cover the range of phrasings found across Australian commercial
construction programmes (mid/high-rise RC, steel-frame, mixed-use).  The same
activity — e.g. a precast panel install — is phrased differently on every
project: "Install precast wall panels", "Precast concrete install (A+B)",
"Commence precast install", etc.  All must resolve to the same asset type.

The tests are intentionally project-agnostic: any programme that includes RC
superstructure, concrete pours, hoist operations, or bulk-earthworks should
exercise these paths.
"""

import pytest
from app.services.ai_service import _KEYWORD_MAP


def _keyword_match(name: str) -> str | None:
    """Replicate the exact lookup logic from _classify_assets_real."""
    name_lower = name.lower()
    for keyword, asset_type in sorted(_KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
        if keyword in name_lower:
            return asset_type
    return None


# ---------------------------------------------------------------------------
# Crane — explicit keyword in name
# ---------------------------------------------------------------------------

class TestCraneExplicit:
    def test_install_steel_canopy_small_crane(self):
        assert _keyword_match("Install steel canopy with the use of small crane") == "crane"

    def test_install_canopy_with_tc(self):
        assert _keyword_match("Install canopy steel with use of TC") == "crane"

    def test_deliver_services_plant_before_crane_removal(self):
        assert _keyword_match("Deliver services plant before crane removal") == "crane"

    def test_bare_crane_keyword(self):
        assert _keyword_match("Tower crane lift") == "crane"

    # Generic phrasings that appear across many projects
    def test_tower_crane_operations(self):
        assert _keyword_match("Tower crane operations level 12") == "crane"

    def test_mobile_crane_set_up(self):
        assert _keyword_match("Mobile crane set up for steel erection") == "crane"

    def test_crane_lift_structural_steel(self):
        assert _keyword_match("Crane lift structural steel columns") == "crane"


# ---------------------------------------------------------------------------
# Crane — implicit superstructure lifts (never say "crane")
# RC programmes write work-package names, not plant names.  The tower crane
# is implicit in activities like precast installs, column cage lifts, and
# floor-slab falsework — none of which say "crane" in the activity name.
# ---------------------------------------------------------------------------

class TestCraneImplicit:
    # Precast
    def test_precast_concrete_install(self):
        assert _keyword_match("Precast Concrete install (A+B)") == "crane"

    def test_install_precast_wall_panels(self):
        assert _keyword_match("Install precast wall panels") == "crane"

    def test_day1_commence_precast_install(self):
        assert _keyword_match("Day 1 - Commence precast install") == "crane"

    def test_day2_continue_precast(self):
        assert _keyword_match("Day 2 - Continue Precast") == "crane"

    def test_day4_complete_precast_installation(self):
        assert _keyword_match("Day 4 - Complete precast installation") == "crane"

    def test_precast_wall_panels_west_side(self):
        assert _keyword_match("Precast wall panels to the west side (Double late)") == "crane"

    # Column cages
    def test_lift_column_cages(self):
        assert _keyword_match("Lift column cages") == "crane"

    def test_day1_install_pour_one_column_cages(self):
        assert _keyword_match("Day 1 - Install pour one column cages") == "crane"

    # Column formwork
    def test_lift_column_formwork(self):
        assert _keyword_match("Lift column formwork") == "crane"

    # Bubbledeck (post-tensioned biaxial slab system — TC-lifted in RC highrise)
    def test_bubbledeck_installation_zone_a(self):
        assert _keyword_match("Bubbledeck installation ZONE (A)") == "crane"

    def test_bubbledeck_installation_zone_b(self):
        assert _keyword_match("Bubbledeck installation ZONE (B)") == "crane"

    def test_day7_commence_bubbledeck_install(self):
        assert _keyword_match("Day 7 - Commence Bubbledeck install (4 men)") == "crane"

    def test_day8_continue_bubbledeck_install(self):
        assert _keyword_match("Day 8 - Continue Bubbledeck install (4 men)") == "crane"

    def test_day9_complete_bubbledeck_install(self):
        assert _keyword_match("Day 9 - Complete Bubbledeck install (4 men)") == "crane"

    # Bubbledeck false work
    def test_bubbledeck_false_work_l1(self):
        assert _keyword_match("Bubbledeck false work") == "crane"

    def test_lift_bd_false_work(self):
        assert _keyword_match("Lift BD false work") == "crane"

    def test_install_bd_false_work(self):
        assert _keyword_match("Install BD false work") == "crane"

    # BD panels
    def test_install_bd_panels(self):
        assert _keyword_match("Install BD panels") == "crane"

    # TC lifts reo bundles to deck level (the reo gang then fixes manually)
    def test_lift_bd_reo(self):
        assert _keyword_match("Lift BD reo") == "crane"

    # Screw-in bars
    def test_lift_screw_in_bars(self):
        assert _keyword_match("Lift screw in bars") == "crane"

    # Jump operations
    def test_jump_the_stretcher_stairs(self):
        assert _keyword_match("Jump the stretcher stairs") == "crane"

    def test_jump_the_hoist_maps_to_crane_not_hoist(self):
        # TC raises the construction hoist to the next level — NOT a hoist booking
        assert _keyword_match("Jump the Hoist") == "crane"

    # Hoist removal
    def test_hoist_off_site_maps_to_crane_not_hoist(self):
        # TC removes the construction hoist from site
        assert _keyword_match("Hoist off site following Builders Lift Ready") == "crane"

    # Props recycling
    def test_recycle_props_to_upper_levels(self):
        assert _keyword_match("Recycle props to upper levels") == "crane"

    # Builder's lift installation (TC installs the permanent hoist level by level)
    def test_install_builders_lift(self):
        assert _keyword_match("Install Builder's Lift @ 4d/ level") == "crane"


# ---------------------------------------------------------------------------
# Hoist — plain hoist keyword (not overridden by longer crane rules)
# ---------------------------------------------------------------------------

class TestHoist:
    def test_hoist_apartments_phase_header(self):
        assert _keyword_match("HOIST APARTMENTS (LEVEL 3 TO 11)") == "hoist"

    def test_builders_hoist(self):
        assert _keyword_match("Builder's Hoist") == "hoist"

    def test_materials_hoist(self):
        assert _keyword_match("materials hoist") == "hoist"

    # Generic phrasings
    def test_personnel_hoist_available(self):
        assert _keyword_match("Personnel hoist available from level 4") == "hoist"

    def test_construction_hoist_operations(self):
        assert _keyword_match("Construction hoist operations level 6-18") == "hoist"


# ---------------------------------------------------------------------------
# Concrete pump — slab pours, column pours, boom pump, line pump
# ---------------------------------------------------------------------------

class TestConcretePump:
    def test_slab_pour_1(self):
        assert _keyword_match("Slab pour, pour 1") == "concrete_pump"

    def test_slab_pour_2(self):
        assert _keyword_match("Slab pour, pour 2") == "concrete_pump"

    def test_day11_slab_pour(self):
        assert _keyword_match("Day 11 - Slab pour, pour 1") == "concrete_pump"

    def test_concrete_pour_floor_slab_zone_a(self):
        assert _keyword_match("Concrete pour floor slab Zone [A] pour (1)") == "concrete_pump"

    def test_concrete_pour_floor_slab_zone_b(self):
        assert _keyword_match("Concrete pour floor slab Zone [B] pour (3)") == "concrete_pump"

    def test_concrete_pour_beam_ramp_infill(self):
        assert _keyword_match("Concrete Pour beam + ramp infill Zone [B]") == "concrete_pump"

    def test_pour_concrete_columns_reversed_word_order(self):
        # "Pour concrete columns" has reversed word order vs "concrete pour" —
        # the new "pour concrete" keyword handles this case.
        assert _keyword_match("Pour concrete columns") == "concrete_pump"

    def test_pour_columns_pour_1(self):
        assert _keyword_match("Day 3 - Pour columns to pour 1") == "concrete_pump"

    def test_pour_columns_pour_2(self):
        assert _keyword_match("Day 5 - Pour columns pour 2") == "concrete_pump"

    def test_ground_floor_column_pour(self):
        assert _keyword_match("Ground floor column pour") == "concrete_pump"

    def test_concrete_pour_5_install_threaded_rod(self):
        assert _keyword_match("Concrete Pour (5) - Install Threaded Rod and Pour 1.2m Strip") == "concrete_pump"

    # Generic phrasings across projects
    def test_roof_slab_pour(self):
        assert _keyword_match("Roof slab pour") == "concrete_pump"

    def test_podium_slab_pour(self):
        assert _keyword_match("Podium level slab pour stage 2") == "concrete_pump"

    def test_basement_slab_concrete_pour(self):
        assert _keyword_match("Basement slab concrete pour — zone B") == "concrete_pump"


# ---------------------------------------------------------------------------
# Excavator
# ---------------------------------------------------------------------------

class TestExcavator:
    def test_excavator_keyword(self):
        assert _keyword_match("Excavator works") == "excavator"

    def test_dig_footings(self):
        assert _keyword_match("Dig footings / piles for steel canopy columns/ posts") == "excavator"

    # Generic phrasings
    def test_excavator_bulk_earthworks(self):
        assert _keyword_match("Excavator — bulk earthworks level B3") == "excavator"

    def test_excavator_trench_dig(self):
        assert _keyword_match("Excavator trench dig for stormwater drainage") == "excavator"


# ---------------------------------------------------------------------------
# EWP
# ---------------------------------------------------------------------------

class TestEwp:
    def test_scissor_lift(self):
        assert _keyword_match("Scissor lift access") == "ewp"

    def test_boom_lift(self):
        assert _keyword_match("boom lift operations") == "ewp"

    def test_ewp_keyword(self):
        assert _keyword_match("EWP required for facade") == "ewp"

    def test_elevated_work_platform(self):
        assert _keyword_match("Elevated Work Platform access") == "ewp"

    def test_knuckle_lift(self):
        assert _keyword_match("knuckle lift boom") == "ewp"

    # Generic phrasings
    def test_man_lift_painting(self):
        assert _keyword_match("Man lift required for external painting") == "ewp"

    def test_ewp_cladding(self):
        assert _keyword_match("EWP — cladding installation level 8") == "ewp"


# ---------------------------------------------------------------------------
# Forklift / Telehandler / Compactor / Loading Bay
# ---------------------------------------------------------------------------

class TestOtherAssets:
    def test_forklift(self):
        assert _keyword_match("Forklift unloading") == "forklift"

    def test_telehandler(self):
        assert _keyword_match("Telehandler required") == "telehandler"

    def test_compactor(self):
        assert _keyword_match("Compactor for fill") == "compactor"

    def test_loading_bay(self):
        assert _keyword_match("loading bay access required") == "loading_bay"

    # Generic phrasings
    def test_forklift_materials_delivery(self):
        assert _keyword_match("Forklift — materials delivery to level 5") == "forklift"

    def test_telehandler_roof_access(self):
        assert _keyword_match("Telehandler access for roof plant delivery") == "telehandler"

    def test_compactor_subgrade(self):
        assert _keyword_match("Compactor — subgrade preparation carpark") == "compactor"

    def test_loading_bay_fitout(self):
        assert _keyword_match("Loading bay operations — fitout deliveries") == "loading_bay"


# ---------------------------------------------------------------------------
# Activities that must NOT false-match (should return None)
# ---------------------------------------------------------------------------

class TestNoFalseMatches:
    def test_install_bd_reo_is_manual_fix(self):
        # "Install BD reo" is a manual reo-fixing gang activity — no crane required.
        # "lift bd" would NOT match (no "lift bd" substring present).
        assert _keyword_match("Install BD reo") is None

    def test_install_bd_reo_parenthesised(self):
        assert _keyword_match("Install (BD) reo") is None

    def test_bd_false_work_inspection_is_not_crane(self):
        # Inspections involve no plant.  "bd false work" is not in the keyword
        # map precisely to avoid triggering on "BD false work inspection".
        assert _keyword_match("BD false work inspection and sign off") is None

    def test_survey_columns_set_out(self):
        assert _keyword_match("Survey - columns set out") is None

    def test_survey_as_built_slab(self):
        assert _keyword_match("Survey - as built slab") is None

    def test_reo_fixing(self):
        assert _keyword_match("Reo fixing") is None

    def test_continue_reo_fixing(self):
        assert _keyword_match("Day 9 - Commenced reo fixing") is None

    def test_column_formwork_install_is_manual(self):
        # Manual carpentry — no TC needed to install the formwork panels
        assert _keyword_match("Column formwork install") is None

    def test_strip_column_formwork_is_manual(self):
        assert _keyword_match("Strip and remove column form work") is None

    def test_dog_box_works(self):
        assert _keyword_match("Dog box works, caulking panels") is None

    def test_pre_pour_inspections(self):
        assert _keyword_match("Pre-pour engineer inspections") is None

    def test_shoring(self):
        assert _keyword_match("Day 3 - Commence shoring (6 men)") is None

    def test_shelf_angle_sealing(self):
        assert _keyword_match("Day 8 - Commence shelf angle and sealing deck (2 men)") is None

    def test_fitout_painting(self):
        assert _keyword_match("Complete all 1st coat paining to walls and ceiling") is None

    def test_services_1st_fix(self):
        assert _keyword_match("Prototype 1st fix fire services high level") is None

    def test_stud_framing(self):
        assert _keyword_match("Install Stud Framing. Sheet 1 Side Fire Walls") is None

    def test_commissioning(self):
        assert _keyword_match("Testing and commissioning to all L4") is None

    # Generic non-plant activities from any project type
    def test_reo_fixing_generic(self):
        assert _keyword_match("Level 8 slab reo fixing") is None

    def test_formwork_strip_generic(self):
        assert _keyword_match("Strip and remove column formwork level 5") is None

    def test_waterproofing_no_plant(self):
        assert _keyword_match("Waterproofing membrane to podium deck") is None

    def test_survey_set_out(self):
        assert _keyword_match("Survey set out grid lines level 10") is None

    def test_safety_inspection(self):
        assert _keyword_match("Weekly safety inspection all levels") is None

    def test_internal_painting(self):
        assert _keyword_match("Internal painting apartments level 3-7") is None


# ---------------------------------------------------------------------------
# Priority / length-ordering invariants
# ---------------------------------------------------------------------------

class TestKeyPriority:
    def test_jump_the_hoist_beats_hoist(self):
        """'jump the hoist' (14 chars) must win over 'hoist' (5 chars)."""
        result = _keyword_match("Jump the Hoist")
        assert result == "crane", f"Expected crane, got {result}"

    def test_hoist_off_site_beats_hoist(self):
        """'hoist off site' (14 chars) must win over 'hoist' (5 chars)."""
        result = _keyword_match("Hoist off site following Builders Lift Ready")
        assert result == "crane", f"Expected crane, got {result}"

    def test_install_builders_lift_beats_hoist(self):
        """'install builder's lift' must win over 'hoist' — hoist is not even in the name."""
        result = _keyword_match("Install Builder's Lift @ 4d/ level")
        assert result == "crane", f"Expected crane, got {result}"

    def test_bubbledeck_installation_beats_bubbledeck_install(self):
        """'bubbledeck installation' (24 chars) beats 'bubbledeck install' (18 chars)."""
        result = _keyword_match("Bubbledeck installation ZONE (A)")
        assert result == "crane"

    def test_scissor_lift_beats_man_lift_as_ewp(self):
        """Both map to ewp — just confirm no collision with crane keywords."""
        assert _keyword_match("Scissor lift access") == "ewp"
        assert _keyword_match("man lift platform") == "ewp"
