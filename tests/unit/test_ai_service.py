import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_service import _call_api


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
             patch("app.services.ai_service.settings.AI_MODEL", "test-model"):
            text, tokens = asyncio.run(
                _call_api(
                    client,
                    "system prompt",
                    "user message",
                    max_tokens=123,
                    timeout=5.0,
                )
            )

        assert text == '{"ok": true}'
        assert tokens == 18
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
             patch("app.services.ai_service.settings.AI_MODEL", "test-model"):
            text, tokens = asyncio.run(
                _call_api(
                    client,
                    "system prompt",
                    "user message",
                    max_tokens=123,
                    timeout=5.0,
                )
            )

        assert text == '{"ok": true}'
        assert tokens == 13
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
            text, tokens = asyncio.run(
                _call_api(
                    client,
                    "system prompt",
                    "user message",
                    max_tokens=55,
                    timeout=5.0,
                )
            )

        assert text == '{"ok": true}'
        assert tokens == 5
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
