"""RoomOrchestrator - the realtime loop that ties every stage together.

    human voice --> LiveKit --> speaker id --> STT --> language/intent
        --> BotRouter --> RoomContextManager --> selected bot --> LLM --> TTS
        --> LiveKit audio --> humans

    human text  --> LiveKit chat ------^  (same router, same context)

Topology
--------
Two LiveKit connections, one per bot, so both are genuine participants:

* **Dost** is the *primary* connection. It also hosts the room ingress:
  subscribes to human microphones, runs STT + VAD, and receives chat.
* **Sathi** is audio/chat egress only; she never subscribes to anything.

Only the primary subscribes because each human utterance must reach the router
exactly once. Both bots share one context manager, one router and one turn
manager, which is what enforces "only one bot answers".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field

from livekit import rtc

from app.agents.dost import build_dost
from app.agents.prompts import (
    failure_reply,
    max_sentences,
    reason_instruction,
    style_instruction,
    wants_detail,
)
from app.agents.sathi import build_sathi
from app.config.settings import Settings, get_settings
from app.context.manager import RoomContextManager
from app.context.summarizer import ConversationSummarizer
from app.livekit_rt.events import RoomIngress
from app.livekit_rt.participants import ParticipantRegistry
from app.livekit_rt.room import bot_identities, mint_bot_token
from app.models.conversation import (
    LatencyTrace,
    RoutingDecision,
    Turn,
    TurnSource,
)
from app.models.participant import BotId, BotState, ParticipantRole, Speaker
from app.pipeline.interruption import InterruptionController, InterruptSignal
from app.pipeline.language import Utterance, understand
from app.pipeline.llm import LLMProvider, build_llm_provider
from app.pipeline.stt import STTProvider, TranscriptEvent, build_stt_provider
from app.routing.router import BotRouter
from app.routing.turn_manager import CancellationToken, TurnManager
from app.utils.logging import (
    TAG_AGENT,
    TAG_ROOM,
    TAG_STT,
    get_logger,
)
from app.utils.metrics import METRICS

log = get_logger(__name__)

TRANSCRIPT_TOPIC = "roxstar.transcript"
STATE_TOPIC = "roxstar.state"

# A VAD end-of-speech stamp older than this is not trusted as the start of the
# latency clock for a final transcript.
_SPEECH_END_MAX_AGE_S = 4.0
# Throttle for interim caption broadcasts, per speaker.
_INTERIM_PUBLISH_INTERVAL_S = 0.25


@dataclass
class _SpeakerClock:
    """Last real VAD timestamps for one speaker (monotonic)."""

    speech_end_at: float | None = None
    last_interim_publish: float = 0.0


@dataclass
class RoomOrchestrator:
    settings: Settings = field(default_factory=get_settings)
    room_name: str = ""

    def __post_init__(self) -> None:
        s = self.settings
        self.room_name = self.room_name or s.room_name

        # --- providers (shared where sharing is correct) ------------------
        self.llm: LLMProvider = build_llm_provider(s)
        self.stt: STTProvider = build_stt_provider(s)

        # --- core state ----------------------------------------------------
        self.context = RoomContextManager(
            settings=s,
            summarizer=ConversationSummarizer(self.llm, max_chars=s.context_max_summary_chars),
        )
        self.router = BotRouter(
            min_question_chars=s.router_min_question_chars,
            followup_window_s=s.router_followup_window_s,
        )
        self.turns = TurnManager(
            response_timeout_s=s.turn_response_timeout_s,
            on_state_change=self._on_bot_state_change,
        )
        self.interruptions = InterruptionController(
            cancel_hook=self._cancel_floor,
            min_speech_ms=s.interrupt_min_speech_ms,
        )
        self.registry = ParticipantRegistry(bot_identities=bot_identities(s))

        # --- agents --------------------------------------------------------
        self.agents: dict[BotId, object] = {
            BotId.DOST: build_dost(llm=self.llm, settings=s),
            BotId.SATHI: build_sathi(llm=self.llm, settings=s),
        }

        self._ingress: RoomIngress | None = None
        self._clocks: dict[str, _SpeakerClock] = {}
        self._vad = None
        self._running = False
        self._last_decision: RoutingDecision | None = None
        self._stop_event: asyncio.Event | None = None
        self._rejoining = False
        self.context.on_topic_change = lambda: asyncio.get_running_loop().create_task(
            self._publish_state()
        )
        self._rejoin_task: asyncio.Task | None = None
        # Set when LiveKit kicks a bot for DUPLICATE_IDENTITY: another worker
        # owns these identities, and rejoining would start a kick-war.
        self.fatal = asyncio.Event()

        self.context.add_listener(self._mirror_turn_to_room)

    # ---- convenience ------------------------------------------------------
    @property
    def dost(self):
        return self.agents[BotId.DOST]

    @property
    def sathi(self):
        return self.agents[BotId.SATHI]

    @property
    def primary(self):
        """The connection that hosts the ingress."""
        return self.dost

    # ---- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        s = self.settings
        missing = s.missing_required()
        if missing:
            log.warning(
                tag=TAG_AGENT,
                event="degraded_start",
                missing=",".join(missing),
                detail="running with offline/fallback providers for the missing pieces",
            )
        if not s.livekit_configured:
            raise RuntimeError(
                "Cannot join a room without LIVEKIT_URL / LIVEKIT_API_KEY / "
                "LIVEKIT_API_SECRET. See .env.example."
            )

        self._vad = await self._load_vad()

        # Register bots in context so the transcript attributes them correctly.
        for bot in BotId:
            agent = self.agents[bot]
            self.context.register_speaker(
                Speaker(
                    identity=agent.identity,
                    name=bot.display,
                    role=ParticipantRole.BOT,
                )
            )

        self._running = True
        await self._join()

    async def _join(self) -> None:
        """Connect both bots to the room and attach the ingress.

        Split out of ``start()`` so the same sequence can be re-run by
        ``_rejoin()`` after the room closes or the network drops.
        """
        s = self.settings

        # Primary first: the ingress must be attached before it connects so no
        # participant or track event is missed.
        self._ingress = RoomIngress(
            self.primary.room,
            registry=self.registry,
            stt=self.stt,
            on_transcript=self._handle_transcript,
            on_chat=self._handle_chat,
            on_speech_activity=self._handle_speech_activity,
            on_participant_joined=self._handle_participant_joined,
            on_participant_left=self._handle_participant_left,
            vad=self._vad,
        )
        self._ingress.attach()
        for bot in BotId:
            room = self.agents[bot].room
            room.on(
                "disconnected",
                lambda *args, _room=room, _bot=bot: self._on_room_disconnected(_room, _bot, *args),
            )

        await self.primary.connect(
            s.livekit_url, mint_bot_token(BotId.DOST, room_name=self.room_name, settings=s)
        )
        self._ingress.sync_existing()

        await self.sathi.connect(
            s.livekit_url, mint_bot_token(BotId.SATHI, room_name=self.room_name, settings=s)
        )

        for bot in BotId:
            self.turns.set_state(bot, BotState.LISTENING)

        log.stage(
            TAG_ROOM,
            event="room_ready",
            room=self.room_name,
            bots=",".join(self.agents[b].identity for b in BotId),
            llm=self.llm.name,
            stt=self.stt.name,
            humans=self.registry.human_count,
        )
        await self._publish_state()

    def _on_room_disconnected(self, room: rtc.Room, bot: BotId, *args: object) -> None:
        """A bot's LiveKit connection ended (room closed, network, token expiry).

        Without this the worker would keep running with both bots silently
        gone - which is exactly what happened when the last human left and
        LiveKit closed the room. Re-join so the next human finds them.
        """
        if not self._running or self._rejoining:
            return
        if room is not self.agents[bot].room:
            # Late event from a Room we already replaced during a rejoin.
            # Acting on it would tear down the fresh connections and loop.
            return
        log.warning(
            tag=TAG_ROOM, event="bot_disconnected", bot=bot.value,
            detail=str(args[0]) if args else None,
        )
        if args and args[0] == rtc.DisconnectReason.DUPLICATE_IDENTITY:
            log.error(
                tag=TAG_ROOM,
                event="duplicate_identity",
                detail="another agent worker is already running with the same bot identity; "
                "stop the other one (only ONE `python -m app.agent_worker` may run). Exiting.",
            )
            self._running = False
            self.fatal.set()
            return
        self._rejoin_task = asyncio.get_running_loop().create_task(self._rejoin())

    async def _rejoin(self) -> None:
        self._rejoining = True
        try:
            for bot in BotId:
                self.turns.set_state(bot, BotState.OFFLINE)
            if self._ingress is not None:
                with contextlib.suppress(Exception):
                    await self._ingress.aclose()
            for bot in BotId:
                with contextlib.suppress(Exception):
                    await self.agents[bot].disconnect()
            self.registry.speakers.clear()
            self.registry.track_owner.clear()

            attempt = 0
            while self._running:
                delay = min(30.0, 2.0**attempt)
                await asyncio.sleep(delay if attempt else 1.0)
                try:
                    for bot in BotId:
                        self.agents[bot].reset_room()
                    await self._join()
                    log.stage(TAG_ROOM, event="rejoined", attempts=attempt + 1)
                    return
                except Exception as exc:
                    attempt += 1
                    log.warning(
                        tag=TAG_ROOM, event="rejoin_failed", attempt=attempt, error=repr(exc)
                    )
        finally:
            self._rejoining = False

    async def _load_vad(self):
        """Silero VAD for barge-in. Optional: the room works without it."""
        try:
            from livekit.plugins import silero

            vad = silero.VAD.load(min_silence_duration=0.35, activation_threshold=0.5)
            log.stage(TAG_STT, event="vad_loaded", provider="silero")
            return vad
        except Exception as exc:
            log.warning(
                tag=TAG_STT,
                event="vad_unavailable",
                error=repr(exc),
                detail="barge-in will fall back to transcript-based detection",
            )
            return None

    async def stop(self) -> None:
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()
        await self.turns.shutdown()
        if self._ingress is not None:
            await self._ingress.aclose()
        for bot in BotId:
            with contextlib.suppress(Exception):
                await self.agents[bot].aclose()
        with contextlib.suppress(Exception):
            await self.stt.aclose()
        with contextlib.suppress(Exception):
            await self.llm.aclose()
        log.stage(TAG_ROOM, event="room_closed", room=self.room_name)

    async def run_forever(self) -> None:
        await self.start()
        self._stop_event = asyncio.Event()
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # ---- participant events ----------------------------------------------
    async def _handle_participant_joined(self, identity: str) -> None:
        speaker = self.registry.get(identity)
        if speaker is not None:
            self.context.register_speaker(speaker)
        self._clocks.setdefault(identity, _SpeakerClock())
        await self._publish_state()

    async def _handle_participant_left(self, identity: str) -> None:
        self.context.unregister_speaker(identity)
        self._clocks.pop(identity, None)
        await self._publish_state()

    # ---- VAD --------------------------------------------------------------
    async def _handle_speech_activity(self, identity: str, kind: str, duration_s: float) -> None:
        clock = self._clocks.setdefault(identity, _SpeakerClock())
        if kind == "end":
            # Real "human stopped talking" stamp: the honest start of the
            # end-to-end latency clock.
            clock.speech_end_at = LatencyTrace.now()
            return

        speaking = self.turns.speaking_bot
        if speaking is None:
            return
        if self.interruptions.should_interrupt_on_vad(
            speaking_bot=speaking, speech_duration_s=duration_s
        ):
            await self.interruptions.trigger(
                signal=InterruptSignal.VAD_SPEECH_START,
                speaker_identity=identity,
                speaking_bot=speaking,
            )

    async def _cancel_floor(self, reason: str) -> bool:
        """Cancel hook handed to the interruption controller."""
        speaking = self.turns.speaking_bot
        cancelled = await self.turns.interrupt(reason=reason)
        if speaking is not None:
            with contextlib.suppress(Exception):
                await self.agents[speaking].interrupt_audio()
        return cancelled

    # ---- STT --------------------------------------------------------------
    async def _handle_transcript(self, ev: TranscriptEvent) -> None:
        if self.registry.is_bot(ev.speaker_identity):
            return

        if not ev.is_final:
            await self._publish_interim(ev)
            # Interim transcripts are the second-fastest barge-in signal.
            speaking = self.turns.speaking_bot
            if speaking is not None and self.interruptions.should_interrupt_on_text(
                speaking_bot=speaking, text=ev.text, is_final=False
            ):
                await self.interruptions.trigger(
                    signal=(
                        InterruptSignal.EXPLICIT_CUE
                        if understand(ev.text).is_interrupt_cue
                        else InterruptSignal.INTERIM_TRANSCRIPT
                    ),
                    speaker_identity=ev.speaker_identity,
                    speaking_bot=speaking,
                    text=ev.text,
                )
            return

        stt_final_at = LatencyTrace.now()
        log.stage(
            TAG_STT,
            speaker=ev.speaker_identity,
            transcript=ev.text,
            language=ev.language,
            confidence=round(ev.confidence, 2),
        )

        # Final transcript while a bot speaks: definitely stale audio.
        speaking = self.turns.speaking_bot
        if speaking is not None:
            await self.interruptions.trigger(
                signal=InterruptSignal.FINAL_TRANSCRIPT,
                speaker_identity=ev.speaker_identity,
                speaking_bot=speaking,
                text=ev.text,
            )

        clock = self._clocks.setdefault(ev.speaker_identity, _SpeakerClock())
        speech_end_at = clock.speech_end_at
        if speech_end_at is not None and (stt_final_at - speech_end_at) > _SPEECH_END_MAX_AGE_S:
            speech_end_at = None  # too old to be this utterance
        clock.speech_end_at = None

        await self._process_utterance(
            text=ev.text,
            speaker_identity=ev.speaker_identity,
            speaker_name=ev.speaker_name,
            source=TurnSource.VOICE,
            speech_end_at=speech_end_at,
            stt_final_at=stt_final_at,
        )

    # ---- text chat --------------------------------------------------------
    async def _handle_chat(self, identity: str, name: str, text: str) -> None:
        """Room chat enters the exact same pipeline as voice."""
        now = LatencyTrace.now()
        # No separate transcript publish here: add_human_turn() below notifies
        # the context listener, which mirrors the turn (with its turn_id) to
        # the UI. Publishing it here as well made every typed message appear
        # twice in the conversation panel.
        await self._process_utterance(
            text=text,
            speaker_identity=identity,
            speaker_name=name,
            source=TurnSource.TEXT,
            # For text there is no speech: the clock starts when we received it.
            speech_end_at=now,
            stt_final_at=now,
        )

    # ---- the shared pipeline ---------------------------------------------
    async def _process_utterance(
        self,
        *,
        text: str,
        speaker_identity: str,
        speaker_name: str,
        source: TurnSource,
        speech_end_at: float | None,
        stt_final_at: float,
    ) -> None:
        """Normalize -> context -> route -> (maybe) respond. One entry point."""
        utterance = understand(text)

        # Commit to shared context first: the room remembers what was said even
        # when no bot answers, and speaker memory is written either way.
        turn, utterance = self.context.add_human_turn(
            text=text,
            speaker_identity=speaker_identity,
            speaker_name=speaker_name,
            source=source,
            utterance=utterance,
        )

        state = self.context.router_state(
            speaking_bot=self.turns.speaking_bot, exclude_turn_id=turn.turn_id
        )
        decision = self.router.route(
            text, speaker_identity=speaker_identity, state=state, utterance=utterance
        )
        router_done_at = LatencyTrace.now()
        self._last_decision = decision

        if not decision.should_respond or decision.selected_bot is None:
            await self._publish_state(decision=decision)
            return

        trace = LatencyTrace(
            turn_id=turn.turn_id,
            speech_end_at=speech_end_at,
            stt_final_at=stt_final_at,
            router_done_at=router_done_at,
            source=source,
            bot_id=decision.selected_bot,
        )

        async def runner(bot: BotId, token: CancellationToken) -> None:
            await self._run_bot_turn(
                bot=bot,
                decision=decision,
                utterance=utterance,
                asking_identity=speaker_identity,
                source=source,
                detail=wants_detail(text),
                trace=trace if bot is decision.selected_bot else LatencyTrace(
                    turn_id=turn.turn_id,
                    stt_final_at=stt_final_at,
                    router_done_at=LatencyTrace.now(),
                    source=source,
                    bot_id=bot,
                ),
                token=token,
            )

        accepted = await self.turns.submit(
            turn_id=turn.turn_id,
            bots=decision.all_bots,
            runner=runner,
            interrupt=decision.is_interruption,
        )
        METRICS.incr("turns_accepted" if accepted else "turns_dropped")
        await self._publish_state(decision=decision)

    async def _run_bot_turn(
        self,
        *,
        bot: BotId,
        decision: RoutingDecision,
        utterance: Utterance,
        asking_identity: str,
        source: TurnSource,
        trace: LatencyTrace,
        token: CancellationToken,
        detail: bool = False,
    ) -> None:
        """Generate and deliver one bot's reply."""
        agent = self.agents[bot]

        bundle = self.context.build_messages(
            bot=bot,
            system_prompt=agent.system_prompt,
            asking_identity=asking_identity,
            style_instruction=style_instruction(
                reply_language=utterance.reply_language,
                wants_english=utterance.wants_english,
                wants_hindi=utterance.wants_hindi,
                detail=detail,
            ),
            extra_instruction=reason_instruction(decision.reason),
        )

        # Send the reply to chat the moment the LLM finishes writing it, not
        # after the bot finishes speaking it (which can be 10-15s later).
        chat_sent = False

        async def _text_ready(text: str) -> None:
            nonlocal chat_sent
            await agent.send_chat(text)
            chat_sent = True

        result = await agent.respond(
            bundle=bundle,
            trace=trace,
            token=token,
            on_text_ready=_text_ready,
            max_tokens=(
                self.settings.llm_max_tokens_detail if detail else self.settings.llm_max_tokens
            ),
            temperature=self.settings.llm_temperature,
            max_sentences=None if detail else max_sentences(bot.value),
        )

        # --- record what the room actually heard --------------------------
        if result.interrupted:
            self.context.mark_interrupted(bot, result.text_spoken or result.text_generated)
            METRICS.incr("responses_interrupted")
        elif result.llm_failed:
            # Speak a polite, in-character fallback instead of surfacing an error.
            fallback = failure_reply(bot.value)
            METRICS.incr("llm_failures")
            with contextlib.suppress(Exception):
                await agent.speak_text(fallback, trace=trace, token=token)
            self.context.add_bot_turn(text=fallback, bot=bot, source=source)
            await agent.send_chat(fallback)
        else:
            spoken = result.text_generated or result.text_spoken
            if spoken:
                self.context.add_bot_turn(text=spoken, bot=bot, source=source)
                # Every reply also lands in chat: it is the transcript of the
                # room, and it is the fallback delivery when TTS is degraded.
                if not chat_sent:
                    await agent.send_chat(spoken)
            if result.tts_failed:
                METRICS.incr("tts_failures")
                await agent.notify_tts_failure()
            METRICS.incr("responses_completed")

        if trace.published_at is not None or trace.llm_done_at is not None:
            METRICS.record(trace)
        await self._publish_state(decision=decision)

    # ---- outbound UI channels --------------------------------------------
    def _mirror_turn_to_room(self, turn: Turn) -> None:
        """Context listener: push every committed turn to the UI transcript."""
        payload = {
            "kind": "turn",
            "turn_id": turn.turn_id,
            "role": turn.role.value,
            "bot": turn.bot_id.value if turn.bot_id else None,
            "identity": turn.speaker_identity,
            "name": turn.speaker_name,
            "text": turn.text,
            "source": turn.source.value,
            "language": turn.language,
            "interrupted": turn.interrupted,
            "is_final": True,
            "created_at": turn.created_at,
        }
        task = asyncio.create_task(self._publish_transcript_entry(payload))
        task.add_done_callback(_swallow)

    async def _publish_interim(self, ev: TranscriptEvent) -> None:
        clock = self._clocks.setdefault(ev.speaker_identity, _SpeakerClock())
        now = time.monotonic()
        if (now - clock.last_interim_publish) < _INTERIM_PUBLISH_INTERVAL_S:
            return
        clock.last_interim_publish = now
        await self._publish_transcript_entry(
            {
                "kind": "interim",
                "identity": ev.speaker_identity,
                "name": ev.speaker_name,
                "text": ev.text,
                "is_final": False,
                "role": "human",
                "source": "voice",
            },
            reliable=False,
        )

    async def _publish_transcript_entry(
        self, payload: dict[str, object], *, reliable: bool = True
    ) -> None:
        room: rtc.Room = self.primary.room
        if not room.isconnected():
            return
        with contextlib.suppress(Exception):
            await room.local_participant.publish_data(
                json.dumps(payload, default=str), topic=TRANSCRIPT_TOPIC, reliable=reliable
            )

    def _on_bot_state_change(self, bot: BotId, state: BotState):
        """TurnManager hook: reflect bot state onto LiveKit attributes + UI."""

        async def apply() -> None:
            with contextlib.suppress(Exception):
                await self.agents[bot].set_state(state)
            await self._publish_state()

        return apply()

    async def _publish_state(self, decision: RoutingDecision | None = None) -> None:
        room: rtc.Room = self.primary.room
        if not room.isconnected():
            return
        d = decision or self._last_decision
        payload: dict[str, object] = {
            "kind": "state",
            "bots": self.turns.states(),
            "speaking_bot": self.turns.speaking_bot.value if self.turns.speaking_bot else None,
            "humans": self.registry.human_count,
            "providers": {
                "llm": self.llm.name,
                "llm_model": getattr(self.llm, "model", None),
                "stt": self.stt.name,
                "tts": {b.value: self.agents[b].tts_name for b in BotId},
            },
            "routing": (
                {
                    "should_respond": d.should_respond,
                    "selected_bot": d.selected_bot.value if d.selected_bot else None,
                    "queued": [b.value for b in d.queued_bots],
                    "reason": d.reason.value,
                    "confidence": round(d.confidence, 2),
                    "is_interruption": d.is_interruption,
                }
                if d
                else None
            ),
            "context": self.context.debug_snapshot(),
            "interruptions": self.interruptions.snapshot(),
            "metrics": METRICS.snapshot(),
            "recent_latency": METRICS.recent(8),
        }
        with contextlib.suppress(Exception):
            await room.local_participant.publish_data(
                json.dumps(payload, default=str), topic=STATE_TOPIC, reliable=True
            )

    # ---- introspection for the API ---------------------------------------
    def status(self) -> dict[str, object]:
        return {
            "running": self._running,
            "room": self.room_name,
            "bots": {
                b.value: {
                    "identity": self.agents[b].identity,
                    "display_name": self.agents[b].display_name,
                    "connected": self.agents[b].connected,
                    "state": self.turns.state_of(b).value,
                    "tts": self.agents[b].tts_name,
                }
                for b in BotId
            },
            "participants": self.registry.snapshot(),
            "providers": {"llm": self.llm.name, "stt": self.stt.name},
            "metrics": METRICS.snapshot(),
        }


def _swallow(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    if exc := task.exception():
        log.error(tag=TAG_ROOM, event="publish_failed", error=repr(exc))
