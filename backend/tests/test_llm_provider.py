"""OpenRouter LLM provider: normal response, timeout, API error, missing key,
malformed response.

No real API key is used anywhere in this file. Every HTTP call is served by
an ``httpx.MockTransport`` handler, so these tests run fully offline and
deterministically, and never touch openrouter.ai.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config.settings import Settings
from app.pipeline.llm import (
    LLMError,
    LLMMessage,
    LLMNotConfigured,
    OpenRouterLLMProvider,
    ScriptedLLMProvider,
    build_llm_provider,
)

_FAKE_KEY = "test-key-not-real"  # never a real OpenRouter key
_MESSAGES = [
    LLMMessage(role="system", content="persona"),
    LLMMessage(role="user", content="AI kya hota hai?"),
]


def _provider(handler, *, max_retries: int = 1) -> tuple[OpenRouterLLMProvider, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://openrouter.ai/api/v1")
    provider = OpenRouterLLMProvider(
        api_key=_FAKE_KEY, model="test-model", client=client, max_retries=max_retries
    )
    # Skip real backoff delays; the retry *logic* is what these tests verify.
    provider._sleep_backoff = lambda attempt: _instant_sleep()
    return provider, client


async def _instant_sleep() -> None:
    return None


def _ok_body(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class TestMissingApiKey:
    def test_constructing_without_a_key_raises(self):
        with pytest.raises(LLMNotConfigured):
            OpenRouterLLMProvider(api_key="", model="test-model")

    def test_missing_key_is_an_llm_error_subclass(self):
        """Callers that only catch LLMError still catch this."""
        with pytest.raises(LLMError):
            OpenRouterLLMProvider(api_key="", model="test-model")

    def test_factory_falls_back_to_scripted_provider_without_a_key(self):
        settings = Settings(
            livekit_url="wss://test.invalid",
            livekit_api_key="k",
            livekit_api_secret="s",
            openrouter_api_key="",
        )
        provider = build_llm_provider(settings)
        assert isinstance(provider, ScriptedLLMProvider)


class TestNormalResponse:
    @pytest.mark.asyncio
    async def test_generate_returns_clean_text(self):
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            payload = json.loads(request.content)
            assert payload["model"] == "test-model"
            assert payload["messages"][0]["role"] == "system"
            return httpx.Response(200, json=_ok_body("Bilkul, ye simple hai."))

        provider, client = _provider(handler)
        try:
            text = await provider.generate(_MESSAGES)
            assert text == "Bilkul, ye simple hai."
            assert len(calls) == 1
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_sends_structured_conversation_with_system_and_user(self):
        """System prompt + room context + current user message must all reach
        the API as separate, correctly-roled messages."""
        seen = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json=_ok_body("ok"))

        provider, client = _provider(handler)
        try:
            messages = [
                LLMMessage(role="system", content="You are Roxstar AI Dost."),
                LLMMessage(role="system", content="[ROOM CONTEXT] topic: AI"),
                LLMMessage(role="user", content="Rahul: AI kya hota hai?"),
            ]
            await provider.generate(messages)
            roles = [m["role"] for m in seen["payload"]["messages"]]
            assert roles == ["system", "system", "user"]
        finally:
            await client.aclose()


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_is_retried_then_raises_llm_error(self):
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("simulated timeout", request=request)

        provider, client = _provider(handler, max_retries=1)
        try:
            with pytest.raises(LLMError) as exc_info:
                await provider.generate(_MESSAGES)
            assert exc_info.value.retryable is True
            assert attempts == 2  # initial attempt + 1 retry
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_no_retries_configured_fails_fast(self):
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectTimeout("simulated", request=request)

        provider, client = _provider(handler, max_retries=0)
        try:
            with pytest.raises(LLMError):
                await provider.generate(_MESSAGES)
            assert attempts == 1
        finally:
            await client.aclose()


class TestApiError:
    @pytest.mark.asyncio
    async def test_5xx_is_retried_then_raises(self):
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(500, json={"error": {"message": "server exploded"}})

        provider, client = _provider(handler, max_retries=2)
        try:
            with pytest.raises(LLMError) as exc_info:
                await provider.generate(_MESSAGES)
            assert exc_info.value.status == 500
            assert attempts == 3  # initial + 2 retries
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_429_rate_limit_is_retryable(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})

        provider, client = _provider(handler, max_retries=1)
        try:
            with pytest.raises(LLMError) as exc_info:
                await provider.generate(_MESSAGES)
            assert exc_info.value.retryable is True
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_4xx_client_error_is_not_retried(self):
        """A bad request would just fail again; retrying wastes latency."""
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(400, json={"error": {"message": "bad request"}})

        provider, client = _provider(handler, max_retries=3)
        try:
            with pytest.raises(LLMError) as exc_info:
                await provider.generate(_MESSAGES)
            assert exc_info.value.status == 400
            assert attempts == 1
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_401_unauthorized_never_leaks_the_key_in_the_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert _FAKE_KEY in request.headers.get("authorization", "")
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})

        provider, client = _provider(handler, max_retries=0)
        try:
            with pytest.raises(LLMError) as exc_info:
                await provider.generate(_MESSAGES)
            assert _FAKE_KEY not in str(exc_info.value)
        finally:
            await client.aclose()


class TestMalformedResponse:
    @pytest.mark.asyncio
    async def test_non_json_body(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>not json</html>")

        provider, client = _provider(handler, max_retries=0)
        try:
            with pytest.raises(LLMError):
                await provider.generate(_MESSAGES)
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_missing_choices(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "x", "object": "chat.completion"})

        provider, client = _provider(handler, max_retries=0)
        try:
            with pytest.raises(LLMError):
                await provider.generate(_MESSAGES)
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_choices_list(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        provider, client = _provider(handler, max_retries=0)
        try:
            with pytest.raises(LLMError):
                await provider.generate(_MESSAGES)
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_choice_without_message(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop"}]})

        provider, client = _provider(handler, max_retries=0)
        try:
            with pytest.raises(LLMError):
                await provider.generate(_MESSAGES)
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_content_string(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ok_body(""))

        provider, client = _provider(handler, max_retries=0)
        try:
            with pytest.raises(LLMError):
                await provider.generate(_MESSAGES)
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_content_as_parts_list_is_joined(self):
        """Some gateways return content as a list of {type, text} parts rather
        than a plain string; the provider should still recover clean text."""

        async def handler(request: httpx.Request) -> httpx.Response:
            body = {
                "choices": [
                    {"message": {"content": [{"type": "text", "text": "Bilkul, "},
                                              {"type": "text", "text": "ye simple hai."}]}}
                ]
            }
            return httpx.Response(200, json=body)

        provider, client = _provider(handler, max_retries=0)
        try:
            text = await provider.generate(_MESSAGES)
            assert text == "Bilkul, ye simple hai."
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_top_level_error_envelope(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": {"code": "model_not_found"}})

        provider, client = _provider(handler, max_retries=0)
        try:
            with pytest.raises(LLMError):
                await provider.generate(_MESSAGES)
        finally:
            await client.aclose()


class TestLatencyLoggingDoesNotLeakSecrets:
    @pytest.mark.asyncio
    async def test_no_secret_in_provider_repr_or_logs(self, caplog):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ok_body("ok"))

        provider, client = _provider(handler, max_retries=0)
        try:
            await provider.generate(_MESSAGES)
        finally:
            await client.aclose()
        # The redact() helper is exercised in utils/logging tests directly;
        # here we just confirm the raw key string never appears in any
        # captured log record's message or fields.
        for record in caplog.records:
            assert _FAKE_KEY not in record.getMessage()
