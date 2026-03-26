import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
