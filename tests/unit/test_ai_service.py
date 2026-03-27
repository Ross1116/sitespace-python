import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_service import (
    AIExecutionContext,
    _call_api,
    _detect_structure_real,
    build_ai_usage,
    classify_assets,
    detect_structure,
    keyword_classify_activity_name,
)


class TestCallApiDeterminism:
    def test_openai_branch_uses_zero_temperature(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=response))
            )
        )

        with patch("app.services.ai_service._is_openai_client", return_value=True), \
             patch("app.services.ai_service.settings.AI_MODEL", "test-model"), \
             patch("app.services.ai_service.settings.AI_INPUT_COST_PER_MILLION_USD", 1.0), \
             patch("app.services.ai_service.settings.AI_OUTPUT_COST_PER_MILLION_USD", 2.0):
            text, usage = asyncio.run(
                _call_api(
                    client,
                    "system prompt",
                    "user message",
                    max_tokens=123,
                    timeout=5.0,
                )
            )

        assert text == '{"ok": true}'
        assert usage.input_tokens == 11
        assert usage.output_tokens == 7
        assert usage.total_tokens == 18
        assert usage.cost_usd == Decimal("0.000025")
        assert client.chat.completions.create.await_args.kwargs["temperature"] == 0

    def test_anthropic_branch_uses_zero_temperature(self):
        response = SimpleNamespace(
            content=[SimpleNamespace(text='{"ok": true}')],
            usage=SimpleNamespace(input_tokens=9, output_tokens=4),
        )
        client = SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=response))
        )

        with patch("app.services.ai_service._is_openai_client", return_value=False), \
             patch("app.services.ai_service.settings.AI_MODEL", "test-model"), \
             patch("app.services.ai_service.settings.AI_INPUT_COST_PER_MILLION_USD", 3.0), \
             patch("app.services.ai_service.settings.AI_OUTPUT_COST_PER_MILLION_USD", 15.0):
            text, usage = asyncio.run(
                _call_api(
                    client,
                    "system prompt",
                    "user message",
                    max_tokens=123,
                    timeout=5.0,
                )
            )

        assert text == '{"ok": true}'
        assert usage.input_tokens == 9
        assert usage.output_tokens == 4
        assert usage.total_tokens == 13
        assert usage.cost_usd == Decimal("0.000087")
        assert client.messages.create.await_args.kwargs["temperature"] == 0


class TestCallApiBackpressure:
    def test_openai_branch_acquires_and_releases_provider_slot(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=response))
            )
        )

        with patch("app.services.ai_service._is_openai_client", return_value=True), \
             patch("app.services.ai_service._acquire_provider_request_slot", new=AsyncMock()) as acquire, \
             patch("app.services.ai_service._release_provider_request_slot") as release, \
             patch("app.services.ai_service.settings.AI_MODEL", "test-model"):
            text, usage = asyncio.run(
                _call_api(
                    client,
                    "system prompt",
                    "user message",
                    max_tokens=55,
                    timeout=5.0,
                )
            )

        assert text == '{"ok": true}'
        assert usage.total_tokens == 5
        acquire.assert_awaited_once()
        release.assert_called_once()

    def test_anthropic_branch_releases_provider_slot_on_error(self):
        client = SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom")))
        )

        with patch("app.services.ai_service._is_openai_client", return_value=False), \
             patch("app.services.ai_service._acquire_provider_request_slot", new=AsyncMock()) as acquire, \
             patch("app.services.ai_service._release_provider_request_slot") as release, \
             patch("app.services.ai_service.settings.AI_MODEL", "test-model"):
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(
                    _call_api(
                        client,
                        "system prompt",
                        "user message",
                        max_tokens=55,
                        timeout=5.0,
                    )
                )

        acquire.assert_awaited_once()
        release.assert_called_once()

    def test_provider_start_delay_spaces_calls_monotonically(self):
        with patch("app.services.ai_service.AI_PROVIDER_MIN_REQUEST_SPACING_SECONDS", 0.5), \
             patch("app.services.ai_service.time.monotonic", side_effect=[100.0, 100.1]), \
             patch("app.services.ai_service._AI_PROVIDER_NEXT_REQUEST_AT", 0.0):
            from app.services.ai_service import _reserve_provider_start_delay

            first_delay = _reserve_provider_start_delay()
            second_delay = _reserve_provider_start_delay()

        assert first_delay == 0.0
        assert second_delay == pytest.approx(0.4)


class TestAISuppressionContext:
    def test_quota_error_marks_execution_context_suppressed(self):
        client = SimpleNamespace(
            messages=SimpleNamespace(
                create=AsyncMock(side_effect=RuntimeError("credit balance is too low"))
            )
        )
        execution_context = AIExecutionContext()

        with patch("app.services.ai_service._is_openai_client", return_value=False), \
             patch("app.services.ai_service.settings.AI_MODEL", "test-model"):
            with pytest.raises(RuntimeError, match="AI quota exhausted"):
                asyncio.run(
                    _call_api(
                        client,
                        "system prompt",
                        "user message",
                        max_tokens=55,
                        timeout=5.0,
                        execution_context=execution_context,
                    )
                )

        assert execution_context.quota_exhausted is True
        assert execution_context.suppress_ai is True
        assert execution_context.quota_error_count == 1

    async def test_detect_structure_uses_fallback_when_execution_context_is_suppressed(self):
        execution_context = AIExecutionContext(suppress_ai=True, quota_exhausted=True)
        rows = [{"Task Name": "Excavate footing", "Start": "2026-03-27", "Finish": "2026-03-28"}]

        with patch("app.services.ai_service.settings.AI_ENABLED", True), \
             patch("app.services.ai_service._detect_structure_real", new_callable=AsyncMock) as detect_real:
            result = await detect_structure(rows, execution_context=execution_context)

        detect_real.assert_not_called()
        assert result.activities
        assert result.column_mapping

    async def test_classify_assets_uses_fallback_when_execution_context_is_suppressed(self):
        execution_context = AIExecutionContext(suppress_ai=True, quota_exhausted=True)
        activities = [{"id": "a1", "name": "Install precast wall panels"}]

        with patch("app.services.ai_service.settings.AI_ENABLED", True), \
             patch("app.services.ai_service._classify_assets_real", new_callable=AsyncMock) as classify_real:
            result = await classify_assets(activities, execution_context=execution_context)

        classify_real.assert_not_called()
        assert len(result.classifications) == 1
        assert result.classifications[0].asset_type == "crane"
        assert result.fallback_used is True

    def test_call_api_rechecks_suppression_after_waiting_for_provider_slot(self):
        response = SimpleNamespace(
            content=[SimpleNamespace(text='{"ok": true}')],
            usage=SimpleNamespace(input_tokens=9, output_tokens=4),
        )
        client = SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock(return_value=response))
        )
        execution_context = AIExecutionContext()

        async def _acquire_and_suppress() -> None:
            execution_context.suppress_ai = True

        with patch("app.services.ai_service._is_openai_client", return_value=False), \
             patch("app.services.ai_service._acquire_provider_request_slot", new=AsyncMock(side_effect=_acquire_and_suppress)) as acquire, \
             patch("app.services.ai_service._release_provider_request_slot") as release, \
             patch("app.services.ai_service.settings.AI_MODEL", "test-model"):
            with pytest.raises(RuntimeError, match="AI suppressed for this upload"):
                asyncio.run(
                    _call_api(
                        client,
                        "system prompt",
                        "user message",
                        max_tokens=55,
                        timeout=5.0,
                        execution_context=execution_context,
                    )
                )

        acquire.assert_awaited_once()
        release.assert_called_once()
        client.messages.create.assert_not_awaited()

    async def test_detect_structure_real_preserves_usage_when_ai_response_is_invalid(self):
        rows = [{"Task Name": "Excavate footing", "Start": "2026-03-27", "Finish": "2026-03-28"}]
        usage = build_ai_usage(12, 8)

        with patch("app.services.ai_service._get_async_client", return_value=object()), \
             patch("app.services.ai_service._load_prompt", return_value="prompt"), \
             patch("app.services.ai_service._call_api", new=AsyncMock(return_value=("not json", usage))):
            result = await _detect_structure_real(rows)

        assert result.activities
        assert result.ai_tokens_used == usage.total_tokens
        assert result.ai_cost_usd == usage.cost_usd
        assert "regex fallback" in result.notes.lower()


class TestKeywordNormalization:
    def test_keyword_classification_normalizes_apostrophes_in_keywords(self):
        assert keyword_classify_activity_name("Builder's hoist landing at level 4") == "hoist"


class TestAiUsagePricing:
    def test_build_ai_usage_returns_none_when_pricing_is_unconfigured(self):
        with patch("app.services.ai_service.settings.AI_INPUT_COST_PER_MILLION_USD", None), \
             patch("app.services.ai_service.settings.AI_OUTPUT_COST_PER_MILLION_USD", None):
            usage = build_ai_usage(12, 8)

        assert usage.total_tokens == 20
        assert usage.cost_usd is None
