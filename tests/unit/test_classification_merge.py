"""
Unit tests for classify_assets() in keyword-fallback mode (AI_ENABLED=false).

With AI disabled, every activity either:
  - keyword-matches -> ClassificationItem with confidence="medium", source="keyword_boost"
  - does not match  -> skipped[]

This exercises the keyword pre-screen and the fallback path without making any
AI API calls.  The merge logic (keyword + AI agreement cases) is tested here
implicitly via the keyword-only path; full AI-merge tests require mocked
Anthropic responses and belong in Stage 4 tests.

Activity names used as test inputs are generic RC / mixed-use construction
phrasings -- not tied to any specific project or fixture.
"""

import pytest
from app.services.ai_service import classify_assets


def _activity(act_id: str, name: str) -> dict:
    return {"id": act_id, "name": name}


class TestKeywordFallbackClassification:
    """AI_ENABLED=false is set in conftest.py -- all calls hit _classify_assets_fallback."""

    async def test_empty_activities_returns_empty_result(self):
        result = await classify_assets([])
        assert result.classifications == []
        assert result.skipped == []
        assert result.batch_tokens_used == 0

    async def test_crane_keyword_match_returns_medium_confidence(self):
        activities = [_activity("a1", "Install precast wall panels")]
        result = await classify_assets(activities)
        assert len(result.classifications) == 1
        item = result.classifications[0]
        assert item.activity_id == "a1"
        assert item.asset_type == "crane"
        assert item.confidence == "medium"
        assert item.source == "keyword_boost"

    async def test_concrete_pump_keyword_match(self):
        activities = [_activity("a1", "Slab pour, pour 1")]
        result = await classify_assets(activities)
        assert len(result.classifications) == 1
        assert result.classifications[0].asset_type == "concrete_pump"

    async def test_unmatched_activity_goes_to_skipped(self):
        activities = [_activity("a1", "Survey - columns set out")]
        result = await classify_assets(activities)
        assert result.classifications == []
        assert "a1" in result.skipped

    async def test_mixed_matched_and_unmatched(self):
        activities = [
            _activity("a1", "Lift column cages"),        # crane
            _activity("a2", "Reo fixing"),                # unmatched
            _activity("a3", "Ground floor column pour"),  # concrete_pump
            _activity("a4", "BD false work inspection"),  # unmatched
        ]
        result = await classify_assets(activities)
        matched_ids = {c.activity_id for c in result.classifications}
        assert "a1" in matched_ids
        assert "a3" in matched_ids
        assert "a2" in result.skipped
        assert "a4" in result.skipped

    async def test_fallback_used_flag_is_true(self):
        activities = [_activity("a1", "Precast install")]
        result = await classify_assets(activities)
        assert result.fallback_used is True

    async def test_batch_tokens_used_is_zero_in_fallback(self):
        activities = [_activity("a1", "Crane lift operation")]
        result = await classify_assets(activities)
        assert result.batch_tokens_used == 0

    async def test_multiple_crane_activities_all_classified(self):
        # Mix of explicit and generalized crane phrasings used across construction programmes.
        crane_activities = [
            _activity(f"a{i}", name) for i, name in enumerate([
                "Tower crane relocation",
                "Mobile crane lift to roof",
                "Precast stair install",
                "Lift column cages",
                "Crawler crane setup",
                "Luffing crane install",
                "Pick and carry steel beams",
            ])
        ]
        result = await classify_assets(crane_activities)
        assert len(result.classifications) == 7
        for item in result.classifications:
            assert item.asset_type == "crane", f"{item.activity_id} mapped to {item.asset_type}"

    async def test_keyword_matching_normalizes_case_and_punctuation(self):
        activities = [_activity("a1", "SCISSOR-LIFT & access setup")]
        result = await classify_assets(activities)

        assert len(result.classifications) == 1
        assert result.classifications[0].asset_type == "ewp"

    async def test_project_asset_scoping_restricts_keywords(self):
        """
        When project_assets are provided, keyword hits are restricted to asset
        types that exist on the project.  If crane is not registered, a crane
        keyword match is dropped to skipped[].
        """
        # Project only has a forklift registered
        project_assets = [{"name": "Forklift", "type": "Forklift", "code": "FL-01"}]
        activities = [
            _activity("a1", "Lift column cages"),   # would be crane -- not on project
            _activity("a2", "Forklift delivery"),   # forklift -- on project
        ]
        result = await classify_assets(activities, project_assets=project_assets)
        matched_ids = {c.activity_id for c in result.classifications}
        assert "a1" not in matched_ids, "crane keyword should be filtered (not on project)"
        assert "a2" in matched_ids

    async def test_project_asset_scoping_allows_matching_types(self):
        project_assets = [{"name": "Crane", "type": "Crane", "code": "TC-01"}]
        activities = [_activity("a1", "Install precast wall panels")]
        result = await classify_assets(activities, project_assets=project_assets)
        assert len(result.classifications) == 1
        assert result.classifications[0].asset_type == "crane"

    async def test_obvious_heading_maps_to_none_in_fallback(self):
        activities = [_activity("a1", "SUPERSTRUCTURE")]
        result = await classify_assets(activities)
        assert len(result.classifications) == 1
        assert result.classifications[0].asset_type == "none"
        assert result.classifications[0].confidence == "medium"
        assert result.skipped == []

    async def test_zone_heading_maps_to_none_in_fallback(self):
        activities = [_activity("a1", "Zone A")]
        result = await classify_assets(activities)
        assert len(result.classifications) == 1
        assert result.classifications[0].asset_type == "none"
        assert result.skipped == []

    async def test_install_bd_reo_not_classified_as_crane(self):
        """
        "Install BD reo" is manual reo-fixing -- no crane required.
        Confirm it is NOT keyword-matched and ends up in skipped[].
        """
        activities = [_activity("a1", "Install BD reo")]
        result = await classify_assets(activities)
        assert result.classifications == []
        assert "a1" in result.skipped
