"""RoomIngress - turns LiveKit room events into pipeline events.

This owns the *input* half of the realtime loop, on a single LiveKit
connection (the primary bot's). Its jobs:

* subscribe to **human** audio tracks only, and never to the other bot's -
  that is what structurally prevents a bot-hears-bot feedback loop, and it
  means we pay for one STT stream per human rather than per bot-pair
* run one STT stream + one VAD stream per human track, attributing every
  transcript to that participant's LiveKit identity
* receive room text chat and hand it to the same callback as voice
* report joins, leaves, mutes and reconnects

Why one shared ingress rather than one per bot: the router must see each human
utterance exactly once. Two ingresses would produce two transcripts of the same
sentence, and deduplicating after the fact is strictly worse than not
duplicating in the first place.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from livekit import rtc

from app.livekit_rt.participants import ParticipantRegistry
from app.pipeline.stt import (
    STT_SAMPLE_RATE,
    FrameResampler,
    STTError,
    STTProvider,
    TranscriptEvent,
)
from app.utils.logging import TAG_CHAT, TAG_ROOM, TAG_STT, get_logger

log = get_logger(__name__)

CHAT_TOPIC = "lk.chat"

TranscriptCallback = Callable[[TranscriptEvent], Awaitable[None]]
ChatCallback = Callable[[str, str, str], Awaitable[None]]  # identity, name, text
# identity, "start" | "end", speech_duration_s
SpeechActivityCallback = Callable[[str, str, float], Awaitable[None]]
ParticipantCallback = Callable[[str], Awaitable[None]]


@dataclass
class _TrackSession:
    identity: str
    task: asyncio.Task[None]
    stt_stream: object | None = None


class RoomIngress:
    """Wires one LiveKit connection's inbound events to the pipeline."""

    def __init__(
        self,
        room: rtc.Room,
        *,
        registry: ParticipantRegistry,
        stt: STTProvider,
        on_transcript: TranscriptCallback,
        on_chat: ChatCallback,
        on_speech_activity: SpeechActivityCallback | None = None,
        on_participant_joined: ParticipantCallback | None = None,
        on_participant_left: ParticipantCallback | None = None,
        vad: object | None = None,
        on_stt_failure: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._on_stt_failure = on_stt_failure
        self._room = room
        self._registry = registry
        self._stt = stt
        self._on_transcript = on_transcript
        self._on_chat = on_chat
        self._on_speech_activity = on_speech_activity
        self._on_joined = on_participant_joined
        self._on_left = on_participant_left
        self._vad = vad
        self._sessions: dict[str, _TrackSession] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- setup ------------------------------------------------------------
    def attach(self) -> None:
        """Register all room event handlers. Call before/after connect."""
        room = self._room
        room.on("participant_connected", self._on_participant_connected)
        room.on("participant_disconnected", self._on_participant_disconnected)
        room.on("track_published", self._on_track_published)
        room.on("track_subscribed", self._on_track_subscribed)
        room.on("track_unsubscribed", self._on_track_unsubscribed)
        room.on("track_muted", self._on_track_muted)
        room.on("track_unmuted", self._on_track_unmuted)
        room.on("connection_state_changed", self._on_connection_state)
        room.on("reconnecting", self._on_reconnecting)
        room.on("reconnected", self._on_reconnected)
        room.on("disconnected", self._on_disconnected)
        room.register_text_stream_handler(CHAT_TOPIC, self._on_text_stream)
        log.stage(TAG_ROOM, event="ingress_attached", topic=CHAT_TOPIC)

    def sync_existing(self) -> None:
        """Adopt participants that were already in the room when we joined."""
        for participant in self._room.remote_participants.values():
            self._register_participant(participant)
            for publication in participant.track_publications.values():
                self._maybe_subscribe(participant, publication)

    # ---- participants -----------------------------------------------------
    def _register_participant(self, participant: rtc.RemoteParticipant) -> None:
        speaker = self._registry.add(participant)
        if not speaker.is_bot and self._on_joined is not None:
            self._spawn(self._on_joined(participant.identity))

    def _on_participant_connected(self, participant: rtc.RemoteParticipant) -> None:
        self._register_participant(participant)

    def _on_participant_disconnected(self, participant: rtc.RemoteParticipant) -> None:
        self._registry.remove(participant.identity)
        self._spawn(self._close_session(participant.identity))
        if self._on_left is not None:
            self._spawn(self._on_left(participant.identity))

    # ---- track subscription ----------------------------------------------
    def _maybe_subscribe(
        self, participant: rtc.RemoteParticipant, publication: rtc.RemoteTrackPublication
    ) -> None:
        """Subscribe to human microphones only."""
        if publication.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if self._registry.role_of(participant).value == "bot":
            log.debug(
                tag=TAG_ROOM, event="skip_bot_track",
                identity=participant.identity, sid=publication.sid,
            )
            return
        if publication.subscribed:
            return
        with contextlib.suppress(Exception):
            publication.set_subscribed(True)
            log.stage(
                TAG_ROOM, event="subscribing",
                identity=participant.identity, sid=publication.sid,
            )

    def _on_track_published(
        self, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
    ) -> None:
        self._maybe_subscribe(participant, publication)

    def _on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if self._registry.role_of(participant).value == "bot":
            # Defensive: should not happen given _maybe_subscribe, but if the
            # server ever auto-subscribes us, do not feed bot audio to STT.
            with contextlib.suppress(Exception):
                publication.set_subscribed(False)
            return
        identity = participant.identity
        self._registry.track_owner[publication.sid] = identity
        # Replace any previous session for this speaker (mic toggled off/on).
        self._spawn(self._restart_session(identity, participant.name or identity, track))

    def _on_track_unsubscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        self._registry.track_owner.pop(publication.sid, None)
        self._spawn(self._close_session(participant.identity))

    def _on_track_muted(
        self, publication: rtc.TrackPublication, participant: rtc.Participant
    ) -> None:
        log.stage(TAG_ROOM, event="track_muted", identity=participant.identity)

    def _on_track_unmuted(
        self, publication: rtc.TrackPublication, participant: rtc.Participant
    ) -> None:
        log.stage(TAG_ROOM, event="track_unmuted", identity=participant.identity)

    # ---- connection state -------------------------------------------------
    def _on_connection_state(self, state: rtc.ConnectionState) -> None:
        log.stage(TAG_ROOM, event="connection_state", state=str(state))

    def _on_reconnecting(self) -> None:
        log.warning(tag=TAG_ROOM, event="reconnecting")

    def _on_reconnected(self) -> None:
        log.stage(TAG_ROOM, event="reconnected")
        # Subscriptions and STT streams do not survive a full reconnect; adopt
        # whatever is present now and rebuild.
        self.sync_existing()

    def _on_disconnected(self, *args: object) -> None:
        log.warning(tag=TAG_ROOM, event="disconnected", detail=str(args[0]) if args else None)

    # ---- text chat --------------------------------------------------------
    def _on_text_stream(self, reader: rtc.TextStreamReader, participant_identity: str) -> None:
        """Room chat arrives here and joins the SAME pipeline as voice."""
        self._spawn(self._read_chat(reader, participant_identity))

    async def _read_chat(self, reader: rtc.TextStreamReader, identity: str) -> None:
        try:
            text = await reader.read_all()
        except Exception as exc:
            log.warning(tag=TAG_CHAT, event="read_failed", identity=identity, error=repr(exc))
            return
        text = (text or "").strip()
        if not text:
            return
        if self._registry.is_bot(identity):
            return  # our own bots' chat messages must not re-enter routing
        name = self._registry.name_of(identity)
        log.stage(TAG_CHAT, event="received", identity=identity, chars=len(text))
        await self._on_chat(identity, name, text)

    # ---- STT sessions -----------------------------------------------------
    async def _restart_session(self, identity: str, name: str, track: rtc.Track) -> None:
        await self._close_session(identity)
        task = asyncio.create_task(self._run_audio_session(identity, name, track))
        task.add_done_callback(_log_error)
        self._sessions[identity] = _TrackSession(identity=identity, task=task)

    async def _close_session(self, identity: str) -> None:
        session = self._sessions.pop(identity, None)
        if session is None:
            return
        session.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await session.task

    async def _run_audio_session(self, identity: str, name: str, track: rtc.Track) -> None:
        """Pump one human's audio into STT (and VAD), forwarding transcripts.

        A failure here is contained to this one speaker: the room, the other
        humans and both bots keep working, and the speaker can still use chat.
        """
        stt_stream = self._stt.stream(speaker_identity=identity, speaker_name=name)
        audio_stream = rtc.AudioStream.from_track(
            track=track, sample_rate=STT_SAMPLE_RATE, num_channels=1
        )
        resampler = FrameResampler()
        vad_stream = None
        if self._vad is not None:
            with contextlib.suppress(Exception):
                vad_stream = self._vad.stream()

        log.stage(TAG_STT, event="session_open", speaker=identity, provider=self._stt.name)

        async def pump_audio() -> None:
            async for event in audio_stream:
                for frame in resampler.process(event.frame):
                    stt_stream.push_frame(frame)
                    if vad_stream is not None:
                        with contextlib.suppress(Exception):
                            vad_stream.push_frame(frame)

        async def pump_transcripts() -> None:
            async for ev in stt_stream.events():
                await self._on_transcript(ev)

        async def pump_vad() -> None:
            if vad_stream is None or self._on_speech_activity is None:
                return
            from livekit.agents import vad as agents_vad

            async for ev in vad_stream:
                # START drives barge-in (fastest signal available); END gives a
                # real "human stopped talking" timestamp for latency measurement.
                if ev.type is agents_vad.VADEventType.START_OF_SPEECH:
                    await self._on_speech_activity(identity, "start", ev.speech_duration)
                elif ev.type is agents_vad.VADEventType.END_OF_SPEECH:
                    await self._on_speech_activity(identity, "end", ev.speech_duration)

        tasks = [
            asyncio.create_task(pump_audio()),
            asyncio.create_task(pump_transcripts()),
            asyncio.create_task(pump_vad()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.cancelled():
                    continue
                if exc := task.exception():
                    raise exc
        except asyncio.CancelledError:
            raise
        except STTError as exc:
            log.error(
                tag=TAG_STT, event="session_failed", speaker=identity, error=str(exc),
                detail="this speaker's voice input stopped; chat still works",
            )
            await self._report_stt_failure(identity, "stt_stream_failed")
        except Exception as exc:
            log.exception(tag=TAG_STT, event="session_error", speaker=identity, error=repr(exc))
            await self._report_stt_failure(identity, "stt_session_error")
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            with contextlib.suppress(Exception):
                await stt_stream.aclose()
            if vad_stream is not None:
                with contextlib.suppress(Exception):
                    await vad_stream.aclose()
            with contextlib.suppress(Exception):
                await audio_stream.aclose()
            log.stage(TAG_STT, event="session_closed", speaker=identity)

    async def _report_stt_failure(self, identity: str, reason: str) -> None:
        """Tell the orchestrator (and so the UI) that this speaker's voice input stopped."""
        if self._on_stt_failure is not None:
            with contextlib.suppress(Exception):
                await self._on_stt_failure(identity, reason)

    # ---- housekeeping -----------------------------------------------------
    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_error)

    async def aclose(self) -> None:
        for identity in list(self._sessions):
            await self._close_session(identity)
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            self._room.unregister_text_stream_handler(CHAT_TOPIC)


def _log_error(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    if exc := task.exception():
        log.error(tag=TAG_ROOM, event="ingress_task_failed", error=repr(exc))
