"""STT provider abstraction + Deepgram streaming implementation.

Why Deepgram (see docs/provider-decisions.md for the full comparison):
  * ``language=multi`` on nova-2/nova-3 transcribes Hindi and English in one
    stream, including intra-sentence code-switching - exactly the Hinglish case
    this room needs. Single-language models force a choice and mangle the other.
  * true bidirectional websocket streaming with interim (partial) transcripts,
    which the barge-in detector uses to react before the final transcript.
  * an officially maintained LiveKit plugin, so we do not hand-roll the socket.

This module wraps the official ``livekit-plugins-deepgram`` STT rather than
reimplementing it, but keeps it behind :class:`STTProvider` so the vendor can
be replaced without touching the orchestrator.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from livekit import rtc

from app.config.settings import Settings, get_settings
from app.utils.logging import TAG_STT, get_logger, redact

log = get_logger(__name__)

# Deepgram's realtime endpoint accepts 16 kHz mono; resampling once here is
# cheaper than shipping 48 kHz room audio to the vendor.
STT_SAMPLE_RATE = 16000
STT_NUM_CHANNELS = 1


@dataclass
class TranscriptEvent:
    """A transcription result attributed to a specific human participant.

    ``speaker_identity`` comes from the LiveKit participant that owns the audio
    track, not from diarization - that is what makes speaker-specific memory
    reliable with multiple humans in the room.
    """

    speaker_identity: str
    speaker_name: str
    text: str
    is_final: bool
    language: str | None = None
    confidence: float = 0.0
    # perf_counter stamp captured the moment the event left the provider.
    received_at: float = 0.0


class STTError(RuntimeError):
    """Raised when a transcription stream fails unrecoverably."""


class STTProvider(abc.ABC):
    """Pluggable speech-to-text backend.

    One :meth:`stream` per human audio track; implementations are expected to
    tolerate the stream being cancelled at any time (participant leaves).
    """

    name: str = "abstract"

    @abc.abstractmethod
    def stream(self, *, speaker_identity: str, speaker_name: str) -> STTStream:
        """Open a streaming session for one participant's microphone."""

    async def aclose(self) -> None:
        return None


class STTStream(abc.ABC):
    """A per-participant transcription session."""

    @abc.abstractmethod
    def push_frame(self, frame: rtc.AudioFrame) -> None:
        """Feed one PCM frame. Must not block."""

    @abc.abstractmethod
    def events(self) -> AsyncIterator[TranscriptEvent]:
        """Async-iterate interim and final transcripts."""

    @abc.abstractmethod
    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Deepgram
# ---------------------------------------------------------------------------


class DeepgramSTTProvider(STTProvider):
    """Streaming STT backed by the official LiveKit Deepgram plugin."""

    name = "deepgram"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "nova-2",
        language: str = "multi",
        interim_results: bool = True,
        endpointing_ms: int = 250,
    ) -> None:
        if not api_key:
            raise ValueError("STT_API_KEY is required for DeepgramSTTProvider")
        # Imported lazily so the module is importable (and testable) without
        # the plugin's native deps present.
        from livekit.plugins import deepgram

        self._impl = deepgram.STT(
            api_key=api_key,
            model=model,
            language=language,
            interim_results=interim_results,
            punctuate=True,
            smart_format=True,
            sample_rate=STT_SAMPLE_RATE,
            endpointing_ms=endpointing_ms,
            # Keep filler words: "haan...", "matlab..." carry turn-taking signal.
            filler_words=True,
            # Bias recognition towards the bot names so "Dost, ..." is not
            # heard as "dust" (which would defeat name-based routing).
            keywords=[("Dost", 2.0), ("Sathi", 2.0)],
        )
        log.stage(
            TAG_STT,
            event="provider_ready",
            provider=self.name,
            model=model,
            language=language,
            interim=interim_results,
            api_key=redact(api_key),
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> DeepgramSTTProvider:
        s = settings or get_settings()
        return cls(
            api_key=s.stt_api_key,
            model=s.stt_model,
            language=s.stt_language,
            interim_results=s.stt_interim_results,
            endpointing_ms=s.stt_endpointing_ms,
        )

    def stream(self, *, speaker_identity: str, speaker_name: str) -> STTStream:
        return _DeepgramStream(
            self._impl.stream(),
            speaker_identity=speaker_identity,
            speaker_name=speaker_name,
        )

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._impl.aclose()


class _DeepgramStream(STTStream):
    def __init__(self, impl, *, speaker_identity: str, speaker_name: str) -> None:
        self._impl = impl
        self._identity = speaker_identity
        self._name = speaker_name
        self._closed = False

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        if self._closed:
            return
        try:
            self._impl.push_frame(frame)
        except Exception as exc:
            # A dead socket must not kill the audio reader task; the provider
            # plugin reconnects internally, and the reader keeps going.
            log.warning(tag=TAG_STT, event="push_failed", speaker=self._identity, error=repr(exc))

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        from livekit.agents import stt as agents_stt

        loop_time = asyncio.get_running_loop().time
        try:
            async for ev in self._impl:
                if ev.type not in (
                    agents_stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    agents_stt.SpeechEventType.FINAL_TRANSCRIPT,
                ):
                    continue
                if not ev.alternatives:
                    continue
                alt = ev.alternatives[0]
                text = (alt.text or "").strip()
                if not text:
                    continue
                yield TranscriptEvent(
                    speaker_identity=self._identity,
                    speaker_name=self._name,
                    text=text,
                    is_final=ev.type == agents_stt.SpeechEventType.FINAL_TRANSCRIPT,
                    language=getattr(alt, "language", None),
                    confidence=getattr(alt, "confidence", 0.0) or 0.0,
                    received_at=loop_time(),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Surfaced to the caller, which logs and drops just this speaker's
            # stream - the room and the other participants stay up.
            raise STTError(f"deepgram stream failed: {type(exc).__name__}") from exc

    async def aclose(self) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            await self._impl.aclose()


# ---------------------------------------------------------------------------
# Test / offline provider
# ---------------------------------------------------------------------------


@dataclass
class ScriptedSTTProvider(STTProvider):
    """Feeds pre-set transcripts. Lets the pipeline be tested without audio."""

    name: str = "scripted"
    streams: list[ScriptedSTTStream] = field(default_factory=list)

    def stream(self, *, speaker_identity: str, speaker_name: str) -> ScriptedSTTStream:
        s = ScriptedSTTStream(speaker_identity=speaker_identity, speaker_name=speaker_name)
        self.streams.append(s)
        return s


class ScriptedSTTStream(STTStream):
    def __init__(self, *, speaker_identity: str, speaker_name: str) -> None:
        self._identity = speaker_identity
        self._name = speaker_name
        self._queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        self.frames_pushed = 0

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        self.frames_pushed += 1

    def feed(self, text: str, *, is_final: bool = True, language: str = "hi") -> None:
        """Test hook: inject a transcript as if the vendor produced it."""
        self._queue.put_nowait(
            TranscriptEvent(
                speaker_identity=self._identity,
                speaker_name=self._name,
                text=text,
                is_final=is_final,
                language=language,
                confidence=0.95,
            )
        )

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def aclose(self) -> None:
        self._queue.put_nowait(None)


class FailingSTTProvider(STTProvider):
    """Raises on first event; proves a bad STT socket does not crash the room."""

    name = "failing"

    def stream(self, *, speaker_identity: str, speaker_name: str) -> STTStream:
        return _FailingSTTStream()


class _FailingSTTStream(STTStream):
    def push_frame(self, frame: rtc.AudioFrame) -> None:
        return None

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        raise STTError("simulated STT outage")
        yield  # pragma: no cover

    async def aclose(self) -> None:
        return None


def build_stt_provider(settings: Settings | None = None) -> STTProvider:
    s = settings or get_settings()
    if s.stt_provider == "deepgram" and s.stt_api_key:
        return DeepgramSTTProvider.from_settings(s)
    if s.stt_provider == "deepgram":
        log.warning(
            tag=TAG_STT,
            event="stt_not_configured",
            detail="STT_API_KEY missing - voice input disabled, text chat still works",
        )
    return ScriptedSTTProvider()


# ---------------------------------------------------------------------------
# Audio plumbing shared by the ingress
# ---------------------------------------------------------------------------


class FrameResampler:
    """Lazily-built 48 kHz -> 16 kHz mono resampler for one track.

    LiveKit delivers room audio at the publisher's rate; Deepgram is configured
    for 16 kHz. Building the resampler on the first frame means we adopt the
    actual input rate instead of assuming one.
    """

    def __init__(self, target_rate: int = STT_SAMPLE_RATE) -> None:
        self._target = target_rate
        self._resampler: rtc.AudioResampler | None = None
        self._input_rate: int | None = None

    def process(self, frame: rtc.AudioFrame) -> list[rtc.AudioFrame]:
        if frame.sample_rate == self._target and frame.num_channels == STT_NUM_CHANNELS:
            return [frame]
        if self._resampler is None or self._input_rate != frame.sample_rate:
            self._input_rate = frame.sample_rate
            self._resampler = rtc.AudioResampler(
                input_rate=frame.sample_rate,
                output_rate=self._target,
                num_channels=frame.num_channels,
            )
        return list(self._resampler.push(frame))

    def flush(self) -> list[rtc.AudioFrame]:
        if self._resampler is None:
            return []
        return list(self._resampler.flush())


AudioFrameCallback = Callable[[rtc.AudioFrame], None]
