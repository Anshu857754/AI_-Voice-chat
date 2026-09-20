"""LLM provider abstraction + OpenRouter implementation.

The rest of the codebase only ever sees :class:`LLMProvider`, so the model or
even the vendor can be swapped through ``OPENROUTER_MODEL`` /
``OPENROUTER_BASE_URL`` (any OpenAI-compatible chat-completions endpoint) with
no code change.

Reliability rules implemented here:
  * hard per-request timeout (``OPENROUTER_TIMEOUT_S``)
  * bounded retries with backoff, only for transient failures (5xx, 429,
    connect/read timeouts) - never for 4xx, which would just fail again
  * every error surfaces as :class:`LLMError`; callers turn that into a polite
    spoken fallback instead of a stack trace
  * streaming is cancellation-aware so barge-in can abandon a generation
"""

from __future__ import annotations

import abc
import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

from app.config.settings import Settings, get_settings
from app.utils.logging import TAG_LLM, get_logger, redact

log = get_logger(__name__)

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class LLMError(RuntimeError):
    """Raised for any LLM failure the caller should handle gracefully.

    Carries only a provider-side summary (status code, error class). It is
    never shown to users - the agent layer substitutes an in-character spoken
    fallback (see ``agents.prompts.failure_reply``).
    """

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLMNotConfigured(LLMError):
    """OPENROUTER_API_KEY is missing."""

    def __init__(self) -> None:
        super().__init__("OPENROUTER_API_KEY is not set", retryable=False)


class LLMProvider(abc.ABC):
    """Pluggable text-generation backend."""

    name: str = "abstract"

    @abc.abstractmethod
    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int = 220,
        temperature: float = 0.7,
    ) -> str:
        """Return the full completion text."""

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int = 220,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Yield completion deltas.

        The default implementation degrades to a single chunk so that providers
        without streaming still satisfy the interface.
        """
        yield await self.generate(messages, max_tokens=max_tokens, temperature=temperature)

    async def aclose(self) -> None:
        return None


class OpenRouterLLMProvider(LLMProvider):
    """OpenRouter chat-completions client (OpenAI-compatible wire format)."""

    name = "openrouter"

    _RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_s: float = 20.0,
        max_retries: int = 2,
        app_url: str = "",
        app_name: str = "",
        disable_reasoning: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise LLMNotConfigured
        self.model = model
        self._disable_reasoning = disable_reasoning
        self._timeout_s = timeout_s
        self._max_retries = max(0, max_retries)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Optional attribution headers; OpenRouter uses them for app ranking.
        if app_url:
            headers["HTTP-Referer"] = app_url
        if app_name:
            headers["X-Title"] = app_name
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
        )
        log.stage(
            TAG_LLM,
            event="provider_ready",
            provider=self.name,
            model=model,
            base_url=base_url,
            api_key=redact(api_key),
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> OpenRouterLLMProvider:
        s = settings or get_settings()
        return cls(
            api_key=s.openrouter_api_key,
            model=s.openrouter_model,
            base_url=s.openrouter_base_url,
            timeout_s=s.openrouter_timeout_s,
            max_retries=s.openrouter_max_retries,
            app_url=s.openrouter_app_url,
            app_name=s.openrouter_app_name,
            disable_reasoning=s.llm_disable_reasoning,
        )

    # ---- internals --------------------------------------------------------
    def _payload(
        self,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
        stream: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if self._disable_reasoning:
            # OpenRouter ignores this for models without a reasoning mode.
            payload["reasoning"] = {"enabled": False}
        return payload

    @classmethod
    def _classify(cls, exc: Exception) -> LLMError:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            retryable = status in cls._RETRYABLE_STATUS
            # The body may carry a provider message, but it is never echoed to
            # users - only the status code is logged.
            return LLMError(f"openrouter http {status}", retryable=retryable, status=status)
        if isinstance(exc, httpx.TimeoutException):
            return LLMError("openrouter timeout", retryable=True)
        if isinstance(exc, httpx.TransportError):
            return LLMError(f"openrouter transport error: {type(exc).__name__}", retryable=True)
        return LLMError(f"openrouter failure: {type(exc).__name__}")

    async def _sleep_backoff(self, attempt: int) -> None:
        # Exponential with jitter, capped so voice latency stays bounded.
        delay = min(1.5, 0.25 * (2**attempt)) * (0.5 + random.random() / 2)
        await asyncio.sleep(delay)

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int = 220,
        temperature: float = 0.7,
    ) -> str:
        last: LLMError | None = None
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                resp = await self._client.post(
                    "/chat/completions",
                    json=self._payload(messages, max_tokens, temperature, stream=False),
                )
                resp.raise_for_status()
                text = _extract_message_content(resp)
                log.stage(
                    TAG_LLM,
                    event="completed",
                    model=self.model,
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                    attempt=attempt + 1,
                    reply_chars=len(text),
                )
                return text
            except asyncio.CancelledError:
                raise
            except LLMError as err:
                last = err
                if not err.retryable or attempt >= self._max_retries:
                    raise
            except Exception as exc:
                last = self._classify(exc)
                if not last.retryable or attempt >= self._max_retries:
                    raise last from exc
            log.warning(
                tag=TAG_LLM,
                event="retry",
                attempt=attempt + 1,
                max_retries=self._max_retries,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                error=str(last),
            )
            await self._sleep_backoff(attempt)
        raise last or LLMError("openrouter failed")

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int = 220,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream deltas via SSE.

        Retries only happen before the first delta is yielded - once the caller
        holds partial text, replaying the request would duplicate speech.
        """
        for attempt in range(self._max_retries + 1):
            produced = False
            try:
                async with self._client.stream(
                    "POST",
                    "/chat/completions",
                    json=self._payload(messages, max_tokens, temperature, stream=True),
                ) as resp:
                    resp.raise_for_status()
                    async for chunk in _iter_sse_deltas(resp):
                        produced = True
                        yield chunk
                if not produced:
                    log.warning(
                        tag=TAG_LLM,
                        event="stream_empty",
                        model=self.model,
                        last_roles=",".join(m.role for m in messages[-3:]),
                        messages=len(messages),
                        max_tokens=max_tokens,
                    )
                    raise LLMError("openrouter stream produced no tokens", retryable=True)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err = exc if isinstance(exc, LLMError) else self._classify(exc)
                if produced or not err.retryable or attempt >= self._max_retries:
                    raise err from exc
                log.warning(tag=TAG_LLM, event="stream_retry", attempt=attempt + 1, error=str(err))
                await self._sleep_backoff(attempt)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _extract_message_content(resp: httpx.Response) -> str:
    """Pull the reply text out of a chat-completions body.

    Defensive on purpose: a gateway can return HTML, an error envelope, an
    empty ``choices`` list, or a ``choices[0]`` with no message. Each of those
    becomes a clean :class:`LLMError` rather than a ``KeyError`` or
    ``TypeError`` surfacing three layers up.
    """
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMError("openrouter returned a non-JSON body") from exc

    if not isinstance(data, dict):
        raise LLMError("openrouter returned an unexpected JSON shape")

    err = data.get("error")
    if err:
        code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
        # 4xx codes in the envelope are permanent; rate limits are not.
        raise LLMError(f"openrouter error envelope: {code}", retryable=code in (429, 502, 503))

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("openrouter returned no choices")

    first = choices[0]
    if not isinstance(first, dict):
        raise LLMError("openrouter returned a malformed choice")

    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMError("openrouter choice has no message object")

    content = message.get("content")
    if isinstance(content, list):
        # Some providers return content parts instead of a plain string.
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str):
        raise LLMError("openrouter message content is not text")

    text = content.strip()
    if not text:
        raise LLMError("openrouter returned empty content")
    return text


async def _iter_sse_deltas(resp: httpx.Response) -> AsyncIterator[str]:
    """Parse an OpenAI-style SSE body into content deltas."""
    async for raw in resp.aiter_lines():
        line = raw.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        err = event.get("error")
        if err:
            code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
            raise LLMError(f"openrouter stream error: {code}")
        for choice in event.get("choices") or []:
            delta = (choice.get("delta") or {}).get("content")
            if delta:
                yield delta


class ScriptedLLMProvider(LLMProvider):
    """Offline provider used by tests and by the ``--offline`` demo mode.

    It never touches the network. Replies are short canned Hinglish lines,
    which keeps the full pipeline (router -> context -> TTS -> publish)
    exercisable without credentials. It is NOT a substitute for the real
    integration and the orchestrator logs loudly when it is selected.
    """

    name = "scripted"

    def __init__(self, replies: Iterable[str] | None = None, *, delay_s: float = 0.0) -> None:
        self._replies = list(replies or ["Haan bilkul, ek second."])
        self._delay_s = delay_s
        self.calls: list[list[LLMMessage]] = []

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int = 220,
        temperature: float = 0.7,
    ) -> str:
        self.calls.append(list(messages))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        idx = min(len(self.calls) - 1, len(self._replies) - 1)
        return self._replies[idx]

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int = 220,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        text = await self.generate(messages, max_tokens=max_tokens, temperature=temperature)
        for word in text.split(" "):
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield word + " "


class FailingLLMProvider(LLMProvider):
    """Always raises. Used by tests to prove failure handling is real."""

    name = "failing"

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or LLMError("simulated outage", retryable=False)

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int = 220,
        temperature: float = 0.7,
    ) -> str:
        raise self._error

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int = 220,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        raise self._error
        yield ""  # pragma: no cover - unreachable, keeps this an async generator


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Factory honouring configuration, with a clearly-logged offline fallback."""
    s = settings or get_settings()
    if s.llm_configured:
        return OpenRouterLLMProvider.from_settings(s)
    log.warning(
        tag=TAG_LLM,
        event="llm_not_configured",
        detail="OPENROUTER_API_KEY missing - using scripted offline provider",
    )
    return ScriptedLLMProvider()
