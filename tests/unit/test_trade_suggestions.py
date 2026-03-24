"""
Unit tests for suggest_subcontractor_asset_types() and the underlying
_lookup_trade_asset_types() helper.

suggest_subcontractor_asset_types() maps each subcontractor's registered
trade_specialty to the asset types they are likely to need.  It is used by
the lookahead planning feature to pre-assign likely demand before explicit
bookings exist.

Resolution order (inside _lookup_trade_asset_types):
  1. Exact match against TRADE_TO_ASSET_TYPES keys
  2. Substring match (key in specialty, or specialty in key) — picks the
     longest (most specific) matching key
  3. Word-level partial match (any shared word)
  4. Fallback → ["other"]

Tests are intentionally trade-agnostic where possible: the function should
work for any correctly described specialty, not just known-fixture trades.
"""

import pytest
from app.services.ai_service import suggest_subcontractor_asset_types


def _sub(sub_id: str, specialty: str) -> dict:
    return {"id": sub_id, "trade_specialty": specialty}


# ---------------------------------------------------------------------------
# Exact matches
# ---------------------------------------------------------------------------

class TestExactMatches:
    def test_structural_returns_crane_and_hoist(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "structural")])
        assert len(result) == 1
        assert "crane" in result[0].suggested_asset_types
        assert "hoist" in result[0].suggested_asset_types

    def test_concreter_returns_concrete_pump(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "concreter")])
        assert "concrete_pump" in result[0].suggested_asset_types

    def test_excavation_returns_excavator(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "excavation")])
        assert "excavator" in result[0].suggested_asset_types

    def test_scaffolding_returns_ewp(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "scaffolding")])
        assert "ewp" in result[0].suggested_asset_types

    def test_electrician_returns_ewp(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "electrician")])
        assert "ewp" in result[0].suggested_asset_types

    def test_painting_returns_ewp(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "painting")])
        assert "ewp" in result[0].suggested_asset_types

    def test_roofing_returns_crane(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "roofing")])
        assert "crane" in result[0].suggested_asset_types

    def test_glazier_returns_crane_and_ewp(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "glazier")])
        types = result[0].suggested_asset_types
        assert "crane" in types
        assert "ewp" in types


# ---------------------------------------------------------------------------
# Substring / partial matches
# ---------------------------------------------------------------------------

class TestSubstringMatches:
    def test_precast_matches_structural(self):
        # "precast" contains word "structural" after word split? No — but "precast"
        # is in key "structural" — no.  "precast" is not an exact key.
        # It should fall through to word-level matching or return other.
        result = suggest_subcontractor_asset_types([_sub("s1", "precast")])
        # "precast" matches TRADE_TO_ASSET_TYPES["precast"] → ["crane", "hoist"]
        assert "crane" in result[0].suggested_asset_types

    def test_civil_works_matches_civil(self):
        # "civil works" contains "civil"
        result = suggest_subcontractor_asset_types([_sub("s1", "civil works")])
        assert "excavator" in result[0].suggested_asset_types

    def test_hvac_contractor_matches_hvac(self):
        # "hvac contractor" contains "hvac"
        result = suggest_subcontractor_asset_types([_sub("s1", "hvac contractor")])
        assert "ewp" in result[0].suggested_asset_types

    def test_electrical_contractor_matches_electrical(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "electrical contractor")])
        assert "ewp" in result[0].suggested_asset_types


# ---------------------------------------------------------------------------
# Unknown / empty specialty → fallback
# ---------------------------------------------------------------------------

class TestFallback:
    def test_unknown_specialty_returns_other(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "underwater welding")])
        assert result[0].suggested_asset_types == ["other"]

    def test_empty_specialty_returns_other(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "")])
        assert result[0].suggested_asset_types == ["other"]

    def test_none_specialty_treated_as_empty(self):
        result = suggest_subcontractor_asset_types([{"id": "s1", "trade_specialty": None}])
        assert result[0].suggested_asset_types == ["other"]


# ---------------------------------------------------------------------------
# Return contract
# ---------------------------------------------------------------------------

class TestReturnContract:
    def test_returns_one_result_per_subcontractor(self):
        subs = [_sub("s1", "concreter"), _sub("s2", "electrician"), _sub("s3", "roofing")]
        result = suggest_subcontractor_asset_types(subs)
        assert len(result) == 3

    def test_result_subcontractor_ids_match_input(self):
        subs = [_sub("abc-123", "structural"), _sub("xyz-456", "plumber")]
        result = suggest_subcontractor_asset_types(subs)
        ids = {r.subcontractor_id for r in result}
        assert "abc-123" in ids
        assert "xyz-456" in ids

    def test_empty_input_returns_empty_list(self):
        result = suggest_subcontractor_asset_types([])
        assert result == []

    def test_trade_specialty_preserved_in_result(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "concreter")])
        assert result[0].trade_specialty == "concreter"

    def test_suggested_types_are_strings(self):
        result = suggest_subcontractor_asset_types([_sub("s1", "structural")])
        for t in result[0].suggested_asset_types:
            assert isinstance(t, str)

    def test_suggested_types_not_empty(self):
        # Even unknown trades return ["other"] — never an empty list
        result = suggest_subcontractor_asset_types([_sub("s1", "unknown trade xyz")])
        assert len(result[0].suggested_asset_types) >= 1
