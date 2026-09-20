"""Sarvam AI text-to-speech adapter (Indian languages), behind the same TTSProvider interface.

Why it exists: Sarvam's Bulbul voices are built for Indian languages (Hindi,
English-India and others), which is the product's core use case. Selecting it is
configuration only::

    TTS_PROVIDER=sarvam
    TTS_API_KEY=<your Sarvam API subscription key>
    TTS_MODEL=<a Bulbul model id from Sarvam's current docs>
    TTS_LANGUAGE=hi-IN                     # BCP-47 style; "hi" -> hi-IN, "en" -> en-IN
    DOST_VOICE_ID=<a Sarvam speaker name from the docs>   # male voice
    SATHI_VOICE_ID=<a Sarvam speaker name from the docs>  # female voice

NOTHING is defaulted for the model or the speakers on purpose: model and speaker
names change between Sarvam releases and must come from their documentation, not
from this repository.

Assumed API shape (Sarvam REST text-to-speech): ``POST {base}/text-to-speech`` with header
``api-subscription-key`` and JSON ``{text, target_language_code, speaker, model,
speech_sample_rate, pace}``, answering ``{"audios": ["<base64 WAV>"]}``. This adapter has
unit tests against a mocked HTTP transport but has NOT been exercised against the live
service in this repository (no credentials were available) - verify with your key first.

Trade-off vs ElevenLabs: this endpoint returns a complete clip per request (no token
streaming), so time-to-first-audio is one round trip per sentence chunk. The agent already
chunks replies by sentence, so speech still starts after the first sentence.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import wave
from collections.abc import AsyncIterator

import httpx
from livekit import rtc

from app.pipeline.tts import TTSError, TTSHandle, TTSProvider
from app.utils.logging import TAG_TTS, get_logger, redact

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.sarvam.ai"
DEFAULT_SAMPLE_RATE = 22050
_FRAME_MS = 20
# Short language codes used elsewhere in the app -> the BCP-47 style codes Sarvam expects.
_LANG = {"hi": "hi-IN", "en": "en-IN", "bn": "bn-IN", "ta": "ta-IN", "te": "te-IN", "mr": "mr-IN", "gu": "gu-IN", "kn": "kn-IN", "ml": "ml-IN", "pa": "pa-IN"}


def normalize_language(code: str) -> str:
    code = (code or "hi-IN").strip()
    return code if "-" in code else _LANG.get(code.lower(), code)


def parse_wav(data: bytes) -> tuple[bytes, int, int]:
    """(PCM16 bytes, sample rate, channels) from a WAV file."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                raise TTSError("sarvam returned non-16-bit audio")
            return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
    except wave.Error as exc:
        raise TTSError("sarvam returned audio that is not a valid WAV") from exc


class SarvamTTSProvider(TTSProvider):
    name = "sarvam"

    def __init__(
        self,
        *,
        api_key: str,
        speaker: str,
        model: str,
        language: str = "hi-IN",
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        pace: float = 1.0,
        base_url: str = DEFAULT_BASE_URL,
        label: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("TTS_API_KEY is required for the Sarvam provider")
        if not speaker:
            raise ValueError(f"a Sarvam speaker name is required for {label or 'this bot'}")
        if not model or model.startswith("eleven"):
            raise ValueError("TTS_MODEL must be a Sarvam Bulbul model id (see Sarvam docs)")
        self._headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
        self._url = f"{base_url.rstrip('/')}/text-to-speech"
        self._speaker = speaker
        self._model = model
        self._language = normalize_language(language)
        self._pace = pace
        self.sample_rate = sample_rate
        self.num_channels = 1
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0), transport=transport)
        log.stage(
            TAG_TTS, event="provider_ready", provider=self.name, label=label, model=model,
            speaker=speaker, language=self._language, sample_rate=sample_rate, api_key=redact(api_key),
        )

    def synthesize(self, text: str) -> TTSHandle:
        payload = {
            "text": text,
            "target_language_code": self._language,
            "speaker": self._speaker,
            "model": self._model,
            "speech_sample_rate": self.sample_rate,
            "pace": self._pace,
        }
        return _SarvamHandle(self._client, self._url, self._headers, payload, self.sample_rate)

    async def aclose(self) -> None:
        await self._client.aclose()


class _SarvamHandle(TTSHandle):
    def __init__(self, client: httpx.AsyncClient, url: str, headers: dict[str, str], payload: dict[str, object], rate: int) -> None:
        self._client, self._url, self._headers, self._payload, self._rate = client, url, headers, payload, rate
        self._cancelled = False
        self._request: asyncio.Task[httpx.Response] | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    async def _fetch(self) -> bytes:
        self._request = asyncio.ensure_future(self._client.post(self._url, json=self._payload, headers=self._headers))
        try:
            resp = await self._request
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise TTSError(f"sarvam request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            # Status only - never the body or headers (they may echo credentials).
            raise TTSError(f"sarvam http {resp.status_code}")
        try:
            return base64.b64decode(resp.json()["audios"][0])
        except Exception as exc:
            raise TTSError("sarvam response had no audio") from exc

    async def frames(self) -> AsyncIterator[rtc.AudioFrame]:
        try:
            wav = await self._fetch()
        except asyncio.CancelledError:
            if self._cancelled:
                return
            raise
        if self._cancelled:
            return
        pcm, rate, channels = parse_wav(wav)
        if rate != self._rate or channels != 1:
            raise TTSError(f"sarvam returned {rate} Hz / {channels} ch, expected {self._rate} Hz mono")
        step = int(rate * _FRAME_MS / 1000) * 2
        for off in range(0, len(pcm), step):
            if self._cancelled:
                return
            chunk = pcm[off : off + step].ljust(step, b"\x00")
            yield rtc.AudioFrame(data=chunk, sample_rate=rate, num_channels=1, samples_per_channel=step // 2)

    async def cancel(self) -> None:
        self._cancelled = True
        if self._request is not None and not self._request.done():
            self._request.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._request
