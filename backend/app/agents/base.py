"""BotAgent - one AI participant with its own LiveKit connection.

Each bot connects to the room as a genuine LiveKit participant with its own
identity, name, attributes and published audio track. Nothing about either bot
is faked in the UI: if the agent worker is not running, the participant list
simply does not show them.

Per-turn flow inside :meth:`respond`:

    LLM stream (deltas)
      -> sentence chunker
      -> TTS synthesis per chunk
      -> AudioSource.capture_frame  (published to the room)

Chunking at sentence boundaries is what keeps time-to-first-audio low: we start
speaking the first sentence while the model is still writing the second.

Cancellation is checked at three points - before each chunk, before each frame,
and inside the TTS handle - and ``AudioSource.clear_queue()`` drops frames that
are already buffered in the SDK. Without that clear, a "cancelled" bot keeps
talking for as long as the jitter buffer holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from livekit import rtc

from app.agents.prompts import persona_for, tts_failure_notice
from app.context.manager import ContextBundle
from app.models.conversation import LatencyTrace
from app.models.participant import BotId, BotState
from app.pipeline.llm import LLMError, LLMProvider
from app.pipeline.tts import TTSError, TTSProvider
from app.routing.turn_manager import CancellationToken, TurnCancelled
from app.utils.logging import (
    TAG_AGENT,
    TAG_LLM,
    TAG_PUBLISH,
    TAG_TTS,
    get_logger,
)

log = get_logger(__name__)

CHAT_TOPIC = "lk.chat"
STATE_TOPIC = "roxstar.state"

# Sentence boundaries for streaming synthesis. Devanagari danda included
# because the model may emit it even when writing romanized Hindi.
_SENTENCE_GAP = re.compile(r"(?<=[.!?।])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+|\n+")
# Minimum characters before we flush a chunk to TTS. Too small and prosody
# suffers; too large and first-audio latency rises.
_MIN_CHUNK_CHARS = 48
_MAX_CHUNK_CHARS = 240

# Stripped before speaking: markdown, emoji, stray symbols the TTS would
# pronounce literally.
_MARKDOWN = re.compile(r"[*_`#>]+")
_BULLET = re.compile(r"^\s*[-*•]\s*", re.MULTILINE)
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff️]+"
)
_MULTI_WS = re.compile(r"\s+")
# The model sometimes echoes the "Name:" label it sees in the history.
_SPEAKER_LABEL = re.compile(
    r"^\(?(?:roxstar\s+)?(?:ai\s+)?(?:dost|sathi)\)?\s*:\s*", re.I
)
# Filler openers the personas forbid; stripped as a safety net when the model
# still produces them.
_FILLER_OPENER = re.compile(
    r"^(?:dekho|basically|achha sawal hai|accha sawal hai|good question|great question)\b[,!.\s-]*",
    re.I,
)


def prepare_for_speech(text: str) -> str:
    """Make model output safe to speak aloud."""
    cleaned = _BULLET.sub("", text)
    cleaned = _MARKDOWN.sub("", cleaned)
    cleaned = _EMOJI.sub("", cleaned)
    cleaned = cleaned.replace("&", " and ")
    return _MULTI_WS.sub(" ", cleaned).strip()


def strip_opening(text: str) -> str:
    """Drop an echoed speaker label and filler openers ("Dekho, basically,")."""
    out = _SPEAKER_LABEL.sub("", text.lstrip())
    for _ in range(3):
        stripped = _FILLER_OPENER.sub("", out, count=1)
        if stripped == out or not stripped.strip():
            break
        out = stripped
    return out[:1].upper() + out[1:] if out else out


def clean_reply(text: str) -> str:
    """Final text for chat and shared context: speech-safe, no label/filler."""
    return strip_opening(prepare_for_speech(text))


@dataclass
class BotIdentityConfig:
    bot_id: BotId
    identity: str
    display_name: str
    voice_label: str


@dataclass
class SpeakResult:
    """Outcome of one reply."""

    text_generated: str = ""
    text_spoken: str = ""
    interrupted: bool = False
    tts_failed: bool = False
    llm_failed: bool = False
    frames_published: int = 0


class BotAgent:
    """One AI participant: LiveKit connection + voice + generation."""

    def __init__(
        self,
        *,
        config: BotIdentityConfig,
        llm: LLMProvider,
        tts: TTSProvider,
        room: rtc.Room | None = None,
    ) -> None:
        self.config = config
        self.bot_id = config.bot_id
        self._llm = llm
        self._tts = tts
        self.room: rtc.Room = room or rtc.Room()
        self._source: rtc.AudioSource | None = None
        self._track: rtc.LocalAudioTrack | None = None
        self._publication: rtc.LocalTrackPublication | None = None
        self._connected = False
        self.state: BotState = BotState.OFFLINE
        # The agent alone knows when audio is really playing (SPEAKING); the
        # orchestrator listens here for its echo guard and barge-in logic.
        self.state_listener: Callable[[BotId, BotState], None] | None = None
        # Set when the user leaves voice mode: silences audio only, the reply
        # keeps being written and delivered as text.
        self.audio_muted = False
        self._system_prompt = persona_for(self.bot_id.value)

    # ---- lifecycle --------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected and self.room.isconnected()

    @property
    def identity(self) -> str:
        return self.config.identity

    @property
    def display_name(self) -> str:
        return self.config.display_name

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def tts_name(self) -> str:
        return self._tts.name

    async def connect(self, url: str, token: str) -> None:
        """Join the room and publish this bot's microphone track."""
        await self.room.connect(url, token, rtc.RoomOptions(auto_subscribe=False))
        self._connected = True

        self._source = rtc.AudioSource(self._tts.sample_rate, self._tts.num_channels)
        self._track = rtc.LocalAudioTrack.create_audio_track(
            f"{self.config.identity}-voice", self._source
        )
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        self._publication = await self.room.local_participant.publish_track(
            self._track, options
        )
        # Attributes let the UI label the bot without any hardcoded knowledge.
        await self._set_attributes(BotState.IDLE)
        self.state = BotState.IDLE
        log.stage(
            TAG_AGENT,
            event="connected",
            bot=self.bot_id.value,
            identity=self.config.identity,
            track=self._publication.sid if self._publication else None,
            sample_rate=self._tts.sample_rate,
        )

    async def _set_attributes(self, state: BotState) -> None:
        if not self.room.isconnected():
            return
        with contextlib.suppress(Exception):
            await self.room.local_participant.set_attributes(
                {
                    "role": "bot",
                    "bot_id": self.bot_id.value,
                    "bot_state": state.value,
                    "voice": self.config.voice_label,
                    "display_name": self.config.display_name,
                }
            )

    async def set_state(self, state: BotState) -> None:
        if state is self.state:
            return
        self.state = state
        if self.state_listener is not None:
            self.state_listener(self.bot_id, state)
        await self._set_attributes(state)

    def reset_room(self) -> None:
        """Fresh rtc.Room for a re-join; a closed Room object is not reusable."""
        self.room = rtc.Room()
        self._source = None
        self._track = None
        self._publication = None
        self._connected = False
        self.state = BotState.OFFLINE

    async def disconnect(self) -> None:
        await self.set_state(BotState.OFFLINE)
        if self._source is not None:
            with contextlib.suppress(Exception):
                await self._source.aclose()
        with contextlib.suppress(Exception):
            await self.room.disconnect()
        self._connected = False
        log.stage(TAG_AGENT, event="disconnected", bot=self.bot_id.value)

    # ---- room messaging ---------------------------------------------------
    async def send_chat(self, text: str) -> None:
        """Publish a chat message as this bot."""
        if not self.room.isconnected():
            return
        try:
            await self.room.local_participant.send_text(text, topic=CHAT_TOPIC)
        except Exception as exc:
            log.warning(tag=TAG_AGENT, event="chat_send_failed", bot=self.bot_id.value, error=repr(exc))

    async def publish_state_payload(self, payload: dict[str, object]) -> None:
        """Broadcast room/debug state on a side channel for the UI."""
        if not self.room.isconnected():
            return
        with contextlib.suppress(Exception):
            await self.room.local_participant.publish_data(
                json.dumps(payload, default=str), topic=STATE_TOPIC, reliable=True
            )

    # ---- generation + speech ---------------------------------------------
    async def respond(
        self,
        *,
        bundle: ContextBundle,
        trace: LatencyTrace,
        token: CancellationToken,
        speak: bool = True,
        max_tokens: int = 220,
        temperature: float = 0.7,
        on_text_ready: Callable[[str], Awaitable[None]] | None = None,
        max_sentences: int | None = None,
        speak_gate: Callable[[], bool] | None = None,
    ) -> SpeakResult:
        """Generate a reply and speak it, streaming sentence by sentence.

        ``speak_gate`` is re-checked before every chunk: if it turns False (the
        user left voice mode mid-reply) the rest is delivered as text only.

        Generation and speech are two concurrent stages joined by a queue.
        Speaking is real-time (the audio source blocks once ~1s is buffered),
        so if the LLM stream were consumed *inside* the speaking loop, the
        model would be throttled to speech speed and the full reply text would
        only exist once the bot had finished talking. Decoupling them lets
        the text be complete - and sent to chat via ``on_text_ready`` - as
        soon as the LLM finishes, while the audio is still playing.
        """
        result = SpeakResult()
        self.audio_muted = False
        await self.set_state(BotState.THINKING)

        chunks_spoken: list[str] = []
        buffer = ""
        generated: list[str] = []
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def speaker() -> None:
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    if token.cancelled or result.tts_failed:
                        continue  # drain: nothing more should be spoken
                    if speak_gate is not None and not speak_gate():
                        chunks_spoken.append(item)  # text only from here on
                        continue
                    spoken = await self._speak_chunk(item, trace, token, result)
                    if spoken:
                        chunks_spoken.append(spoken)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result.tts_failed = True
                log.error(tag=TAG_TTS, event="speaker_failed", bot=self.bot_id.value, error=repr(exc))

        speaker_task = asyncio.create_task(speaker()) if speak else None

        opening_done = False

        def emit(chunk_text: str) -> None:
            nonlocal opening_done
            if not opening_done:
                opening_done = True
                chunk_text = strip_opening(chunk_text)
                if not chunk_text:
                    opening_done = False
                    return
            if speak and (speak_gate is None or speak_gate()):
                queue.put_nowait(chunk_text)
            else:
                chunks_spoken.append(chunk_text)

        try:
            llm_stream = self._llm.stream(
                bundle.messages, max_tokens=max_tokens, temperature=temperature
            )
            async for delta in llm_stream:
                if token.cancelled:
                    raise TurnCancelled(token.reason or "cancelled")
                if trace.llm_first_token_at is None:
                    trace.llm_first_token_at = LatencyTrace.now()
                    await self.set_state(BotState.GENERATING)
                    log.stage(
                        TAG_LLM,
                        event="first_token",
                        bot=self.bot_id.value,
                        ttft_ms=trace.llm_ttft_ms,
                    )
                generated.append(delta)
                buffer += delta

                # Voice replies stay short: once N full sentences exist, keep
                # exactly those and stop the model.
                capped = False
                if max_sentences:
                    full = "".join(generated)
                    gaps = list(_SENTENCE_GAP.finditer(full))
                    if len(gaps) >= max_sentences:
                        cut = gaps[max_sentences - 1].start()
                        consumed = len(full) - len(buffer)
                        buffer = full[consumed:cut] if cut > consumed else ""
                        generated[:] = [full[: max(cut, consumed)]]
                        capped = True

                # Hand complete sentences to the speaker as soon as they are long enough.
                while (chunk := _pop_chunk(buffer)) is not None:
                    chunk_text, buffer = chunk
                    emit(chunk_text)
                if capped:
                    log.stage(TAG_LLM, event="reply_capped", bot=self.bot_id.value, sentences=max_sentences)
                    break
            with contextlib.suppress(Exception):
                await llm_stream.aclose()

            trace.llm_done_at = LatencyTrace.now()
            result.text_generated = clean_reply("".join(generated))

            tail = buffer.strip()
            if tail and not token.cancelled:
                emit(tail)

            if on_text_ready is not None and result.text_generated and not token.cancelled:
                try:
                    await on_text_ready(result.text_generated)
                except Exception as exc:
                    log.warning(tag=TAG_AGENT, event="text_ready_failed", error=repr(exc))

            if speaker_task is not None:
                queue.put_nowait(None)
                await speaker_task
            if token.cancelled:
                raise TurnCancelled(token.reason or "cancelled")

        except TurnCancelled:
            result.interrupted = True
            result.text_generated = clean_reply("".join(generated))
            await self._cancel_speaker(speaker_task)
            await self._stop_audio()
            log.stage(
                TAG_TTS, event="cancelled", bot=self.bot_id.value,
                spoken_chars=sum(len(c) for c in chunks_spoken),
            )
        except asyncio.CancelledError:
            result.interrupted = True
            result.text_generated = clean_reply("".join(generated))
            await self._cancel_speaker(speaker_task)
            await self._stop_audio()
            raise
        except LLMError as exc:
            result.llm_failed = True
            result.text_generated = clean_reply("".join(generated))
            await self._cancel_speaker(speaker_task)
            log.error(tag=TAG_LLM, event="generation_failed", bot=self.bot_id.value, error=str(exc))
        except TTSError as exc:
            result.tts_failed = True
            result.text_generated = clean_reply("".join(generated))
            await self._cancel_speaker(speaker_task)
            log.error(tag=TAG_TTS, event="synthesis_failed", bot=self.bot_id.value, error=str(exc))
        except Exception as exc:
            result.llm_failed = True
            result.text_generated = clean_reply("".join(generated))
            await self._cancel_speaker(speaker_task)
            log.exception(tag=TAG_AGENT, event="respond_failed", bot=self.bot_id.value, error=repr(exc))

        result.text_spoken = " ".join(c for c in chunks_spoken if c).strip()
        if result.interrupted:
            await self.set_state(BotState.INTERRUPTED)
        elif result.llm_failed or result.tts_failed:
            await self.set_state(BotState.ERROR)
        else:
            await self.set_state(BotState.IDLE)
        return result

    @staticmethod
    async def _cancel_speaker(task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def speak_text(
        self, text: str, *, trace: LatencyTrace, token: CancellationToken
    ) -> SpeakResult:
        """Speak a fixed string (used for failure fallbacks)."""
        result = SpeakResult(text_generated=text)
        self.audio_muted = False
        spoken = await self._speak_chunk(text, trace, token, result)
        result.text_spoken = spoken
        await self.set_state(BotState.IDLE if not result.interrupted else BotState.INTERRUPTED)
        return result

    async def _speak_chunk(
        self,
        text: str,
        trace: LatencyTrace,
        token: CancellationToken,
        result: SpeakResult,
    ) -> str:
        """Synthesize and publish one chunk. Returns the text actually spoken."""
        speakable = prepare_for_speech(text)
        if not speakable:
            return ""
        if token.cancelled:
            result.interrupted = True
            return ""
        if self.audio_muted:
            return speakable  # user left voice mode: text only, no TTS call
        if self._source is None:
            # No audio path (not connected): the caller still delivers text.
            return speakable

        # SPEAKING is only claimed once the first audio frame is really published;
        # until then the honest state is SYNTHESIZING ("Preparing voice...").
        if self.state is not BotState.SPEAKING:
            await self.set_state(BotState.SYNTHESIZING)
        handle = self._tts.synthesize(speakable)
        published = 0
        try:
            async for frame in handle.frames():
                if token.cancelled or self.audio_muted:
                    await handle.cancel()
                    await self._stop_audio()
                    log.stage(
                        TAG_TTS, event="stream_cut", bot=self.bot_id.value,
                        frames=published, reason=token.reason or "audio_muted",
                    )
                    if token.cancelled:
                        result.interrupted = True
                    else:
                        await self.set_state(BotState.THINKING)  # text keeps coming
                    return speakable
                if trace.tts_first_frame_at is None:
                    trace.tts_first_frame_at = LatencyTrace.now()
                await self._source.capture_frame(frame)
                if self.state is not BotState.SPEAKING:
                    await self.set_state(BotState.SPEAKING)
                if trace.published_at is None:
                    trace.published_at = LatencyTrace.now()
                    log.stage(
                        TAG_PUBLISH,
                        bot=self.bot_id.value,
                        first_audio_ms=trace.total_latency_ms,
                        tts_latency_ms=trace.tts_latency_ms,
                    )
                published += 1
            result.frames_published += published
            return speakable
        except TTSError as exc:
            result.tts_failed = True
            log.error(
                tag=TAG_TTS, event="chunk_failed", bot=self.bot_id.value,
                error=str(exc), frames=published,
            )
            with contextlib.suppress(Exception):
                await handle.cancel()
            return speakable if published else ""
        except asyncio.CancelledError:
            result.interrupted = True
            with contextlib.suppress(Exception):
                await handle.cancel()
            await self._stop_audio()
            raise

    async def _stop_audio(self) -> None:
        """Drop buffered audio so cancellation is audible immediately.

        Without ``clear_queue`` the SDK would keep playing out up to
        ``queue_size_ms`` of already-captured frames after we stop feeding it.
        """
        if self._source is None:
            return
        with contextlib.suppress(Exception):
            self._source.clear_queue()

    async def interrupt_audio(self) -> None:
        """Public hook used by the orchestrator on barge-in."""
        await self._stop_audio()
        await self.set_state(BotState.INTERRUPTED)

    async def mute_audio(self) -> None:
        """Stop speaking now without cancelling the turn (user left voice mode)."""
        self.audio_muted = True
        await self._stop_audio()

    async def notify_tts_failure(self) -> None:
        await self.send_chat(tts_failure_notice(self.bot_id.value))

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._tts.aclose()
        await self.disconnect()


def _pop_chunk(buffer: str) -> tuple[str, str] | None:
    """Split off a speakable chunk, or None if we should keep buffering.

    Prefers a sentence boundary; falls back to a hard length cap so a model
    that forgets punctuation cannot stall audio indefinitely.
    """
    if len(buffer) < _MIN_CHUNK_CHARS:
        return None
    matches = list(_SENTENCE_END.finditer(buffer))
    for match in matches:
        if match.end() >= _MIN_CHUNK_CHARS:
            return buffer[: match.start()].strip(), buffer[match.end() :]
    if len(buffer) >= _MAX_CHUNK_CHARS:
        # Break at the last space before the cap to avoid splitting a word.
        cut = buffer.rfind(" ", 0, _MAX_CHUNK_CHARS)
        if cut <= 0:
            cut = _MAX_CHUNK_CHARS
        return buffer[:cut].strip(), buffer[cut:].lstrip()
    return None
