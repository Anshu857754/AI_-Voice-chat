"""TTS provider abstraction + ElevenLabs streaming implementation.

Why ElevenLabs (see docs/provider-decisions.md):
  * ``eleven_turbo_v2_5`` / ``eleven_flash_v2_5`` support Hindi with natural
    Indian pronunciation and handle Hinglish code-switching without spelling
    out English words phonetically in Devanagari.
  * a large voice library with distinct male and female Indian voices, selected
    purely by env var (``DOST_VOICE_ID`` / ``SATHI_VOICE_ID``).
  * websocket streaming, so audio starts flowing before the sentence is done -
    essential for keeping perceived latency low.
  * an officially maintained LiveKit plugin.

The critical extra behaviour in this module is **cancellation**: every
synthesis exposes a cancel path that stops pulling frames immediately, so
barge-in can cut a bot off mid-word. See ``pipeline/interruption.py``.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from livekit import rtc

from app.config.settings import Settings, get_settings
from app.models.participant import BotId
from app.utils.logging import TAG_TTS, get_logger, redact

log = get_logger(__name__)

# Frames we publish into the room. 48 kHz mono matches LiveKit's native rate.
PUBLISH_SAMPLE_RATE = 48000
PUBLISH_NUM_CHANNELS = 1


class TTSError(RuntimeError):
    """Raised when synthesis fails; callers fall back to text-only delivery."""


@dataclass
class VoiceConfig:
    """Everything that makes one bot sound like itself."""

    voice_id: str
    model: str = "eleven_turbo_v2_5"
    language: str = "hi"
    # ElevenLabs voice settings: lower stability = more expressive delivery,
    # which suits a casual Hinglish conversation better than a neutral read.
    stability: float = 0.45
    similarity_boost: float = 0.75
    style: float = 0.35
    speed: float = 1.0


class TTSProvider(abc.ABC):
    """Pluggable text-to-speech backend, one instance per bot voice."""

    name: str = "abstract"
    sample_rate: int = PUBLISH_SAMPLE_RATE
    num_channels: int = PUBLISH_NUM_CHANNELS

    @abc.abstractmethod
    def synthesize(self, text: str) -> TTSHandle:
        """Begin synthesis and return a cancellable handle."""

    async def aclose(self) -> None:
        return None


class TTSHandle(abc.ABC):
    """A cancellable synthesis in progress."""

    @abc.abstractmethod
    def frames(self) -> AsyncIterator[rtc.AudioFrame]:
        """Async-iterate synthesized PCM frames."""

    @abc.abstractmethod
    async def cancel(self) -> None:
        """Stop synthesis as fast as possible. Safe to call more than once."""

    @property
    @abc.abstractmethod
    def cancelled(self) -> bool: ...


# ---------------------------------------------------------------------------
# ElevenLabs
# ---------------------------------------------------------------------------


class ElevenLabsTTSProvider(TTSProvider):
    """Streaming TTS backed by the official LiveKit ElevenLabs plugin."""

    name = "elevenlabs"

    def __init__(self, *, api_key: str, voice: VoiceConfig, label: str = "") -> None:
        if not api_key:
            raise ValueError("TTS_API_KEY is required for ElevenLabsTTSProvider")
        if not voice.voice_id:
            raise ValueError(f"voice id is required for {label or 'ElevenLabsTTSProvider'}")
        from livekit.plugins import elevenlabs

        self._impl = elevenlabs.TTS(
            api_key=api_key,
            voice_id=voice.voice_id,
            model=voice.model,
            language=voice.language,
            voice_settings=elevenlabs.VoiceSettings(
                stability=voice.stability,
                similarity_boost=voice.similarity_boost,
                style=voice.style,
                speed=voice.speed,
                use_speaker_boost=True,
            ),
        )
        self.sample_rate = self._impl.sample_rate
        self.num_channels = self._impl.num_channels
        self._voice = voice
        log.stage(
            TAG_TTS,
            event="provider_ready",
            provider=self.name,
            label=label,
            model=voice.model,
            voice_id=voice.voice_id,
            language=voice.language,
            sample_rate=self.sample_rate,
            api_key=redact(api_key),
        )

    def synthesize(self, text: str) -> TTSHandle:
        return _ElevenLabsHandle(self._impl.synthesize(text))

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._impl.aclose()


class _ElevenLabsHandle(TTSHandle):
    def __init__(self, chunked) -> None:
        self._chunked = chunked
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    async def frames(self) -> AsyncIterator[rtc.AudioFrame]:
        try:
            async for ev in self._chunked:
                if self._cancelled:
                    return
                yield ev.frame
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._cancelled:
                return
            raise TTSError(f"elevenlabs synthesis failed: {type(exc).__name__}") from exc

    async def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        # Closing the underlying stream aborts the HTTP/websocket read, which
        # is what actually stops the vendor from billing us for unused audio.
        with contextlib.suppress(Exception):
            await self._chunked.aclose()


# ---------------------------------------------------------------------------
# Offline / test providers
# ---------------------------------------------------------------------------


class SilentTTSProvider(TTSProvider):
    """Generates silence of a realistic duration instead of real speech.

    Used when TTS credentials are absent and by tests. It keeps the whole
    publish path (audio source, track, cancellation, latency stamps) genuinely
    exercised - the room really does receive audio frames, they are just
    silent, and the reply is always also delivered to room chat so the demo
    stays usable.
    """

    name = "silent"

    def __init__(self, *, words_per_minute: float = 150.0, realtime: bool = True) -> None:
        self._wpm = words_per_minute
        self._realtime = realtime
        self.sample_rate = PUBLISH_SAMPLE_RATE
        self.num_channels = PUBLISH_NUM_CHANNELS

    def synthesize(self, text: str) -> TTSHandle:
        words = max(1, len(text.split()))
        duration_s = words / self._wpm * 60.0
        return _SilentHandle(
            duration_s=duration_s,
            sample_rate=self.sample_rate,
            num_channels=self.num_channels,
            realtime=self._realtime,
        )


class _SilentHandle(TTSHandle):
    FRAME_MS = 20

    def __init__(
        self, *, duration_s: float, sample_rate: int, num_channels: int, realtime: bool
    ) -> None:
        self._duration_s = duration_s
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._realtime = realtime
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    async def frames(self) -> AsyncIterator[rtc.AudioFrame]:
        samples_per_frame = int(self._sample_rate * self.FRAME_MS / 1000)
        total = max(1, int(self._duration_s * 1000 / self.FRAME_MS))
        payload = b"\x00" * (samples_per_frame * self._num_channels * 2)
        for _ in range(total):
            if self._cancelled:
                return
            if self._realtime:
                await asyncio.sleep(self.FRAME_MS / 1000)
            yield rtc.AudioFrame(
                data=payload,
                sample_rate=self._sample_rate,
                num_channels=self._num_channels,
                samples_per_channel=samples_per_frame,
            )

    async def cancel(self) -> None:
        self._cancelled = True


class FailingTTSProvider(TTSProvider):
    """Raises mid-stream; proves TTS failure degrades to text, not a crash."""

    name = "failing"

    def __init__(self, *, frames_before_failure: int = 0) -> None:
        self._frames_before_failure = frames_before_failure

    def synthesize(self, text: str) -> TTSHandle:
        return _FailingHandle(self._frames_before_failure)


class _FailingHandle(TTSHandle):
    def __init__(self, frames_before_failure: int) -> None:
        self._n = frames_before_failure
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    async def frames(self) -> AsyncIterator[rtc.AudioFrame]:
        samples = int(PUBLISH_SAMPLE_RATE * 0.02)
        for _ in range(self._n):
            if self._cancelled:
                return
            yield rtc.AudioFrame(
                data=b"\x00" * samples * 2,
                sample_rate=PUBLISH_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=samples,
            )
        raise TTSError("simulated TTS outage")

    async def cancel(self) -> None:
        self._cancelled = True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def voice_config_for(bot: BotId, settings: Settings | None = None) -> VoiceConfig:
    s = settings or get_settings()
    voice_id = s.dost_voice_id if bot is BotId.DOST else s.sathi_voice_id
    # Distinct delivery, not just distinct voice ids: Dost is steadier, Sathi a
    # little brighter and more expressive.
    if bot is BotId.DOST:
        return VoiceConfig(
            voice_id=voice_id,
            model=s.tts_model,
            language=s.tts_language,
            stability=0.50,
            similarity_boost=0.75,
            style=0.30,
            speed=1.0,
        )
    return VoiceConfig(
        voice_id=voice_id,
        model=s.tts_model,
        language=s.tts_language,
        stability=0.40,
        similarity_boost=0.80,
        style=0.45,
        speed=1.04,
    )


def build_tts_provider(bot: BotId, settings: Settings | None = None) -> TTSProvider:
    s = settings or get_settings()
    voice = voice_config_for(bot, s)
    if s.tts_provider == "elevenlabs" and s.tts_api_key and voice.voice_id:
        return ElevenLabsTTSProvider(api_key=s.tts_api_key, voice=voice, label=bot.value)
    if s.tts_provider == "sarvam" and s.tts_api_key and voice.voice_id and not s.tts_model.startswith("eleven"):
        from app.pipeline.tts_sarvam import SarvamTTSProvider  # lazy: only when selected

        return SarvamTTSProvider(
            api_key=s.tts_api_key, speaker=voice.voice_id, model=s.tts_model, language=s.tts_language, label=bot.value
        )
    if s.tts_provider in ("elevenlabs", "sarvam"):
        missing = "TTS_API_KEY" if not s.tts_api_key else f"{bot.value.upper()}_VOICE_ID"
        log.warning(
            tag=TAG_TTS,
            event="tts_not_configured",
            bot=bot.value,
            detail=f"{missing} missing - falling back to silent audio + text replies",
        )
    return SilentTTSProvider()
