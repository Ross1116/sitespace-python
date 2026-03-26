import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.work_profile_service import (
    build_default_profile,
    generate_work_profile_ai,
    resolve_work_profile,
)


class TestDefaultProfileHardening:
    def test_event_assets_are_more_peaked_than_logistics_assets(self):
        _, _, pump_norm = build_default_profile("concrete_pump", 5, 10.0)
        _, _, hoist_norm = build_default_profile("hoist", 5, 10.0)

        assert max(pump_norm) > max(hoist_norm)
        assert pump_norm[2] == max(pump_norm)

    def test_logistics_assets_default_to_more_hours_than_event_assets(self):
        pump_total, _, _ = build_default_profile("concrete_pump", 5, 10.0)
        bay_total, _, _ = build_default_profile("loading_bay", 5, 10.0)

        assert bay_total > pump_total

    def test_context_can_raise_or_lower_default_hours(self):
        structure_ctx = {
            "phase": "structure",
            "spatial_type": "zone",
            "area_type": "internal",
            "work_type": "slab",
        }
        inspection_ctx = {
            "phase": "structure",
            "spatial_type": "zone",
            "area_type": "internal",
            "work_type": "inspection",
        }

        structure_total, _, _ = build_default_profile(
            "crane", 4, 10.0, compressed_context=structure_ctx
        )
        inspection_total, _, _ = build_default_profile(
            "crane", 4, 10.0, compressed_context=inspection_ctx
        )

        assert structure_total > inspection_total


class TestWorkProfileAIHardening:
    def test_generate_work_profile_ai_includes_context_and_prior(self):
        response = json.dumps(
            {
                "total_hours": 8.0,
                "normalized_distribution": [0.15, 0.70, 0.15],
                "confidence": 0.8,
            }
        )

        with patch("app.services.work_profile_service._get_async_client", return_value=object()), \
             patch("app.services.work_profile_service._call_api", new_callable=AsyncMock, return_value=(response, 55)), \
             patch("app.services.work_profile_service.settings.AI_ENABLED", True), \
             patch("app.services.work_profile_service.settings.AI_API_KEY", "test-key"):
            result = asyncio.run(
                generate_work_profile_ai(
                    activity_name="Pour slab",
                    asset_type="concrete_pump",
                    duration_days=3,
                    max_hours_per_day=10.0,
                )
            )

        assert result is not None
        request_json = result["request_json"]
        assert request_json["compressed_context"]["phase"] == "structure"
        assert request_json["compressed_context"]["work_type"] == "slab"
        assert request_json["deterministic_prior"]["shape_family"] == "event_peak"
        assert request_json["deterministic_prior"]["default_total_hours"] > 0

    def test_generate_work_profile_ai_caps_confidence_for_low_quality_rows(self):
        response = json.dumps(
            {
                "total_hours": 6.0,
                "normalized_distribution": [0.5, 0.5],
                "confidence": 0.95,
            }
        )

        with patch("app.services.work_profile_service._get_async_client", return_value=object()), \
             patch("app.services.work_profile_service._call_api", new_callable=AsyncMock, return_value=(response, 21)), \
             patch("app.services.work_profile_service.settings.AI_ENABLED", True), \
             patch("app.services.work_profile_service.settings.AI_API_KEY", "test-key"):
            result = asyncio.run(
                generate_work_profile_ai(
                    activity_name="Generic install activity",
                    asset_type="ewp",
                    duration_days=2,
                    max_hours_per_day=10.0,
                    row_confidence="low",
                )
            )

        assert result is not None
        assert result["confidence"] <= 0.55

    def test_trusted_baseline_uses_asset_shaped_distribution(self):
        db = MagicMock()
        cache_row = MagicMock()
        cache_row.id = uuid.uuid4()
        written_profile = MagicMock()

        with patch("app.services.work_profile_service._lookup_cache_with_reduced_context", return_value=(None, "exact-hash")), \
             patch("app.services.work_profile_service._find_trusted_baseline", return_value=8.0), \
             patch("app.services.work_profile_service._upsert_cache_from_external_observation", return_value=cache_row), \
             patch("app.services.work_profile_service._write_activity_profile", return_value=written_profile) as write_activity, \
             patch("app.core.constants.get_max_hours_for_type", return_value=10.0):
            resolve_work_profile(
                db,
                activity_id=uuid.uuid4(),
                item_id=uuid.uuid4(),
                asset_type="concrete_pump",
                duration_days=3,
                activity_name="Pour slab",
            )

        norm = write_activity.call_args.kwargs["normalized_distribution"]
        assert norm[1] == max(norm)
        assert norm != [0.333333, 0.333333, 0.333334]
