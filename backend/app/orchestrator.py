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
import re
import time
import uuid
from dataclasses import dataclass, field

from livekit import rtc

from app.agents.dost import build_dost
from app.agents.prompts import (
    STT_FAILURE_NOTICE,
    delivery_instruction,
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
from app.conversations import DEFAULT_SETTINGS, ConversationStore
from app.history import build_history
from app.livekit_rt.events import RoomIngress
from app.livekit_rt.participants import ParticipantRegistry
from app.livekit_rt.room import bot_identities, mint_bot_token
from app.models.conversation import (
    LatencyTrace,
    RoutingDecision,
    RoutingReason,
    Turn,
    TurnRole,
    TurnSource,
)
from app.models.participant import BotId, BotState, ParticipantRole, Speaker
from app.pipeline.interruption import InterruptionController, InterruptSignal
from app.pipeline.language import Language, Utterance, understand
from app.pipeline.llm import LLMMessage, LLMProvider, build_llm_provider
from app.pipeline.stt import STTProvider, TranscriptEvent, build_stt_provider
from app.pipeline.voice_filter import parse_languages, voice_ignore_reason
from app.routing.claims import ResponderClaims
from app.routing.router import pick_responder
from app.routing.turn_manager import CancellationToken, TurnManager
from app.routing.voice_policy import PREFS, resolve_response
from app.titles import FALLBACK_TITLE
from app.utils.logging import (
    TAG_AGENT,
    TAG_CONTEXT,
    TAG_ROOM,
    TAG_ROUTER,
    TAG_STT,
    get_logger,
)
from app.utils.metrics import METRICS

log = get_logger(__name__)

TRANSCRIPT_TOPIC = "roxstar.transcript"
STATE_TOPIC = "roxstar.state"
CONTROL_TOPIC = "roxstar.control"  # UI -> worker: AI<->AI mode, stop
# AI<->AI mode: at most this many AI turns per user message (including the first reply).
MAX_AI_TURNS = 6

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
        # One AI per message: pick_responder() decides, claims enforce it.
        self.claims = ResponderClaims()
        # AI<->AI conversation is OFF unless a human turns it on from the UI.
        self.ai_mode = False
        self._chain_left = 0
        self._last_human_identity = ""
        # Voice mode is per human, explicit, and OFF by default (text chat).
        self._voice_users: set[str] = set()
        # Conversation-level response preference: auto | text | voice (see voice_policy).
        self._response_pref = "auto"
        # Saved conversations: the worker serves ONE active conversation at a time
        # (the UI selects it with a `conversation` control message).
        self.active_conversation: str | None = None
        self._conversation_error: dict[str, str] | None = None  # last refused activation (for the UI)
        self._active_by = ""  # identity of the session that opened it (UIs use this to avoid fighting)
        self._conv_settings: dict[str, object] = dict(DEFAULT_SETTINGS)
        self._participants: set[BotId] = set(BotId)
        self._save_seq = 0  # bumps after every saved turn/title so the UI refreshes its sidebar
        # Echo guard: which AIs are producing audio now, and when speech last ended.
        self._speaking_bots: set[BotId] = set()
        self._ai_speech_ended_at: float | None = None
        self._stt_languages = parse_languages(s.stt_allowed_languages)
        self._last_error: dict[str, object] | None = None
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
        for agent in self.agents.values():
            agent.state_listener = self._on_agent_state

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

        # Persist every committed turn; reload the recent thread into context.
        self.history = build_history(s, self.room_name)
        self.conversations = ConversationStore(s.history_db_path, self.room_name)
        self.context.add_listener(self._persist_turn)
        self.context.restore_turns(self.history.recent_turns(s.history_restore_turns))

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
            on_stt_failure=self._handle_stt_failure,
        )
        self._ingress.attach()
        self.primary.room.on("data_received", self._on_data_packet)
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
        self._voice_users.discard(identity)
        if identity == self._active_by:
            self._active_by = ""  # that session is gone: nobody holds the worker any more
        await self._publish_state()

    # ---- VAD --------------------------------------------------------------
    async def _handle_speech_activity(self, identity: str, kind: str, duration_s: float) -> None:
        clock = self._clocks.setdefault(identity, _SpeakerClock())
        if kind == "end":
            # Real "human stopped talking" stamp: the honest start of the
            # end-to-end latency clock.
            clock.speech_end_at = LatencyTrace.now()
            return

        # Speech starting while an AI talks is (almost always) the AI's own
        # echo in the mic: never treat it as a barge-in.
        if self._echo_guard_active():
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
    def _on_agent_state(self, bot: BotId, state: BotState) -> None:
        """Agent hook: track real audio playback (SPEAKING) for the echo guard."""
        if state is BotState.SPEAKING:
            self._speaking_bots.add(bot)
        elif bot in self._speaking_bots:
            self._speaking_bots.discard(bot)
            self._ai_speech_ended_at = time.monotonic()

    def _echo_guard_active(self) -> bool:
        """True while an AI is speaking, and briefly after (its echo is still arriving)."""
        if self._speaking_bots:
            return True
        if self._ai_speech_ended_at is None:
            return False
        return (time.monotonic() - self._ai_speech_ended_at) < self.settings.echo_guard_tail_s

    def _voice_ignore_reason(self, ev: TranscriptEvent) -> str | None:
        since = None if self._ai_speech_ended_at is None else time.monotonic() - self._ai_speech_ended_at
        return voice_ignore_reason(
            text=ev.text,
            confidence=ev.confidence,
            language=ev.language,
            ai_speaking=bool(self._speaking_bots),
            since_ai_speech_s=since,
            min_confidence=self.settings.stt_min_confidence,
            allowed_languages=self._stt_languages,
            tail_s=self.settings.echo_guard_tail_s,
            names_an_ai=bool(understand(ev.text).addressed_bots),
        )

    async def _handle_transcript(self, ev: TranscriptEvent) -> None:
        if self.registry.is_bot(ev.speaker_identity):
            return

        if not ev.is_final:
            if self._echo_guard_active():
                return  # AI echo: no caption, no barge-in
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

        reason = self._voice_ignore_reason(ev)
        if reason is not None:
            self._clocks.setdefault(ev.speaker_identity, _SpeakerClock()).speech_end_at = None
            log.stage(
                TAG_STT, event="voice_ignored", reason=reason, speaker=ev.speaker_identity,
                transcript=ev.text, language=ev.language, confidence=round(ev.confidence, 2),
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
        decision = pick_responder(
            text,
            state,
            speaker_identity=speaker_identity,
            utterance=utterance,
            # Only STT can echo a final twice; every typed message must be answered.
            dedupe=source is TurnSource.VOICE,
        )
        if decision.should_respond and decision.selected_bot not in self._participants:
            # This conversation only includes some of the AIs.
            only = sorted(self._participants, key=lambda b: b.value)[0]
            decision = RoutingDecision(True, only, decision.reason, confidence=decision.confidence)
        router_done_at = LatencyTrace.now()
        self._last_decision = decision

        if not decision.should_respond or decision.selected_bot is None:
            await self._publish_state(decision=decision)
            return

        # Claim the reply: one responder per message_id, whatever else runs.
        message_id = turn.turn_id
        if not self.claims.claim(message_id, decision.selected_bot):
            log.warning(
                tag=TAG_ROUTER, event="claim_denied", message_id=message_id,
                bot=decision.selected_bot.value, owner=self.claims.owner(message_id),
            )
            return
        log.stage(
            TAG_ROUTER, event="claimed", message_id=message_id,
            bot=decision.selected_bot.value, reason=decision.reason.value,
        )
        self._last_human_identity = speaker_identity
        self.context.mark_active(decision.selected_bot)
        # A new human message restarts the AI<->AI budget; this reply is turn 1.
        self._chain_left = MAX_AI_TURNS - 1 if self.ai_mode else 0

        trace = LatencyTrace(
            turn_id=turn.turn_id,
            speech_end_at=speech_end_at,
            stt_final_at=stt_final_at,
            router_done_at=router_done_at,
            source=source,
            bot_id=decision.selected_bot,
        )

        # inputMode (source) and responseMode are separate: text is always produced, voice
        # is added only if the USER asked (this message, the conversation preference, or the
        # Voice button) - never because of the message source or the AI's own text.
        resp = resolve_response(
            text=text, voice_mode=speaker_identity in self._voice_users, pref=self._response_pref  # type: ignore[arg-type]
        )
        if resp.new_pref is not None:
            self._set_response_pref(resp.new_pref)
        speak = resp.mode == "voice"
        forced = resp.intent.mode  # None | text | voice: an explicit instruction in THIS message
        log.stage(
            TAG_ROUTER, event="response_mode", message_id=message_id, request_id=message_id,
            input_mode=source.value, response_mode=resp.mode, reason=resp.reason,
            pref=self._response_pref, language=utterance.reply_language.value,
            bot=decision.selected_bot.value,
        )

        async def runner(bot: BotId, token: CancellationToken) -> None:
            await self._run_bot_turn(
                bot=bot,
                decision=decision,
                utterance=utterance,
                asking_identity=speaker_identity,
                source=source,
                speak=speak,
                forced=forced,
                detail=wants_detail(text),
                message_id=message_id,
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
            # A new user message always preempts whatever the AI is doing.
            interrupt=True,
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
        speak: bool,
        forced: str | None = None,
        detail: bool = False,
        message_id: str = "",
    ) -> None:
        """Generate and deliver one bot's reply."""
        if message_id and self.claims.owner(message_id) is not bot:
            log.warning(tag=TAG_ROUTER, event="not_owner", message_id=message_id, bot=bot.value)
            return
        agent = self.agents[bot]
        # `speak` comes from voice_policy.should_speak() - the only rule. The
        # gate re-checks it live, so leaving voice mode mid-reply stops audio
        # while the text still arrives.
        def speak_gate() -> bool:
            return self._voice_wanted(asking_identity, forced)

        bundle = self.context.build_messages(
            bot=bot,
            system_prompt=agent.system_prompt,
            asking_identity=asking_identity,
            style_instruction=self._style_for(utterance, detail, voice=speak and speak_gate()),
            extra_instruction=reason_instruction(decision.reason),
        )

        # Send the reply to chat the moment the LLM finishes writing it, not
        # after the bot finishes speaking it (which can be 10-15s later).
        chat_sent = False
        # The reply is committed to the shared context (and so mirrored to the
        # conversation panel) as soon as the text exists, not after it has
        # been spoken. If the user barges in, the turn is corrected below.
        committed: Turn | None = None

        async def _text_ready(text: str) -> None:
            nonlocal chat_sent, committed
            committed = self.context.add_bot_turn(
                text=text, bot=bot, source=source, request_id=message_id or None,
                response_mode="voice" if speak else "text",
            )
            await agent.send_chat(text)
            chat_sent = True

        result = await agent.respond(
            bundle=bundle,
            trace=trace,
            token=token,
            on_text_ready=_text_ready,
            speak=speak,
            speak_gate=speak_gate,
            max_tokens=(
                self.settings.llm_max_tokens_detail if detail else self.settings.llm_max_tokens
            ),
            temperature=self.settings.llm_temperature,
            max_sentences=None if detail else max_sentences(bot.value),
        )

        # --- record what the room actually heard --------------------------
        if result.interrupted:
            if committed is not None:
                # Keep only what was actually heard before the cut-off.
                committed.text = result.text_spoken or committed.text
                committed.interrupted = True
                self._persist_turn(committed)
            else:
                self.context.mark_interrupted(bot, result.text_spoken or result.text_generated)
            METRICS.incr("responses_interrupted")
        elif result.llm_failed:
            # Speak a polite, in-character fallback instead of surfacing an error.
            fallback = failure_reply(bot.value)
            METRICS.incr("llm_failures")
            self._last_error = {
                "bot": bot.value, "kind": "llm", "at": time.time(),
                "message": f"{bot.display} ka jawab nahi ban paya (LLM error). Dobara try karo.",
            }
            if speak and speak_gate():
                with contextlib.suppress(Exception):
                    await agent.speak_text(fallback, trace=trace, token=token)
            self.context.add_bot_turn(text=fallback, bot=bot, source=source)
            await agent.send_chat(fallback)
        else:
            spoken = result.text_generated or result.text_spoken
            if spoken and committed is None:
                self.context.add_bot_turn(text=spoken, bot=bot, source=source)
                # Every reply also lands in chat: it is the transcript of the
                # room, and it is the fallback delivery when TTS is degraded.
                if not chat_sent:
                    await agent.send_chat(spoken)
            if result.tts_failed:
                METRICS.incr("tts_failures")
                self._last_error = {
                    "bot": bot.value, "kind": "tts", "at": time.time(),
                    "message": f"{bot.display} ki awaaz nahi chal paayi (TTS error). Jawab chat mein hai.",
                }
                await agent.notify_tts_failure()
            else:
                self._last_error = None
            METRICS.incr("responses_completed")

        if trace.published_at is not None or trace.llm_done_at is not None:
            METRICS.record(trace)
        await self._publish_state(decision=decision)

        # AI<->AI mode (explicitly enabled by a human, capped at MAX_AI_TURNS).
        if (
            self.ai_mode
            and self._chain_left > 0
            and committed is not None
            and not result.interrupted
            and not result.llm_failed
            and not token.cancelled
        ):
            await self._queue_ai_followup(bot, committed.text)

    def _voice_wanted(self, identity: str, forced: str | None = None) -> bool:
        """Live check (same rule as resolve_response) used to gate every TTS chunk."""
        if forced is not None:
            return forced == "voice"
        if self._response_pref != "auto":
            return self._response_pref == "voice"
        return identity in self._voice_users

    def _set_response_pref(self, pref: str) -> None:
        if pref not in PREFS or pref == self._response_pref:
            return
        self._response_pref = pref
        log.stage(TAG_ROUTER, event="response_pref", pref=pref, conversation=self.active_conversation)
        if self.active_conversation:
            with contextlib.suppress(Exception):
                self.conversations.merge_settings(self.active_conversation, {"response_pref": pref})
        task = asyncio.create_task(self._publish_state())
        task.add_done_callback(_swallow)

    async def _handle_stt_failure(self, identity: str, reason: str) -> None:
        """A speaker's voice input stopped: say so in the UI. Text chat keeps working."""
        log.error(tag=TAG_STT, event="stt_failure_reported", speaker=identity, reason=reason)
        self._last_error = {
            "bot": None, "kind": "stt", "at": time.time(), "identity": identity,
            "message": STT_FAILURE_NOTICE,
        }
        await self._publish_state()

    def _style_for(self, utterance: Utterance, detail: bool, *, voice: bool = False) -> str:
        """Language + length instruction, honouring this conversation's settings."""
        lang = self._conv_settings.get("language", "auto")
        style = style_instruction(
            reply_language=Language.HINGLISH if lang == "hinglish" else utterance.reply_language,
            wants_english=utterance.wants_english or lang == "english",
            wants_hindi=(utterance.wants_hindi or lang == "hindi") and lang != "english",
            detail=detail or self._conv_settings.get("reply_length") == "detailed",
        )
        return f"{style} {delivery_instruction(voice=voice)}"

    # ---- AI <-> AI mode (off by default) -----------------------------------
    async def _queue_ai_followup(self, spoke: BotId, text: str) -> None:
        other = BotId.SATHI if spoke is BotId.DOST else BotId.DOST
        if other not in self._participants or spoke not in self._participants:
            return
        self._chain_left -= 1
        message_id = f"ai-{uuid.uuid4().hex[:10]}"
        self.claims.claim(message_id, other)
        decision = RoutingDecision(True, other, RoutingReason.AI_CHAIN, confidence=1.0)
        utterance = understand(text)
        # AI<->AI is spoken only while the human who started it is in voice mode.
        speak = self._voice_wanted(self._last_human_identity)
        source = TurnSource.VOICE if speak else TurnSource.TEXT
        log.stage(
            TAG_ROUTER, event="ai_chain", message_id=message_id, bot=other.value,
            turns_left=self._chain_left, speak=speak,
        )

        async def runner(bot: BotId, token: CancellationToken) -> None:
            await self._run_bot_turn(
                bot=bot,
                decision=decision,
                utterance=utterance,
                asking_identity=self._last_human_identity,
                source=source,
                speak=speak,
                message_id=message_id,
                trace=LatencyTrace(
                    turn_id=message_id, stt_final_at=LatencyTrace.now(),
                    router_done_at=LatencyTrace.now(), source=source, bot_id=bot,
                ),
                token=token,
            )

        await self.turns.submit(turn_id=message_id, bots=(other,), runner=runner, interrupt=False)

    def _on_data_packet(self, pkt: rtc.DataPacket) -> None:
        """UI control messages. Only humans may send them."""
        if pkt.topic != CONTROL_TOPIC or pkt.participant is None:
            return
        if self.registry.is_bot(pkt.participant.identity):
            return
        try:
            msg = json.loads(pkt.data.decode())
        except Exception:
            return
        task = asyncio.create_task(self._handle_control(msg, pkt.participant.identity))
        task.add_done_callback(_log_control_error)

    async def _handle_control(self, msg: dict, identity: str) -> None:
        kind = msg.get("type")
        if kind == "conversation":
            await self._activate_conversation(identity, msg.get("id"), force=bool(msg.get("reload")))
        elif kind == "conversation_settings":
            cid = self.active_conversation
            if cid and self.conversations.owner(cid) == self._user_id_of(identity):
                self._apply_conversation_settings(cid)
                # Choosing a reply mode in chat settings is an explicit instruction.
                pref = str(self._conv_settings.get("response_pref", "auto"))
                if pref in PREFS and pref != self._response_pref:
                    self._response_pref = pref
                if not self._conv_settings.get("voice_enabled", True):
                    self._voice_users.clear()
                    if self._response_pref == "voice":
                        self._response_pref = "auto"
                if not self.ai_mode:
                    await self._stop_chain("ai_mode_off")
        elif kind == "ai_mode":
            self.ai_mode = bool(msg.get("enabled"))
            log.stage(TAG_ROUTER, event="ai_mode", enabled=self.ai_mode, by=identity)
            if self.active_conversation:
                with contextlib.suppress(Exception):
                    self.conversations.merge_settings(self.active_conversation, {"ai_collab": self.ai_mode})
            if not self.ai_mode:
                await self._stop_chain("ai_mode_off")
        elif kind == "response_pref":
            self._set_response_pref(str(msg.get("value")))
        elif kind == "voice_mode":
            enabled = bool(msg.get("enabled")) and bool(self._conv_settings.get("voice_enabled", True))
            (self._voice_users.add if enabled else self._voice_users.discard)(identity)
            log.stage(TAG_ROUTER, event="voice_mode", enabled=enabled, by=identity)
            # The Voice button is an explicit choice: it overrides an earlier "text only"
            # and Stop Voice ends a "talk to me in voice" preference.
            if enabled and self._response_pref == "text":
                self._set_response_pref("auto")
            if not enabled and self._response_pref == "voice":
                self._set_response_pref("auto")
            if not enabled:
                # Audio stops at once; the reply itself keeps arriving as text.
                for speaking in list(self._speaking_bots):
                    await self.agents[speaking].mute_audio()
        elif kind == "retry_voice":
            await self._retry_voice(identity)
        elif kind == "stop":
            log.stage(TAG_ROUTER, event="stop_pressed", by=identity)
            await self._stop_chain("stop_button")
        await self._publish_state()

    async def _retry_voice(self, identity: str) -> None:
        """Speak the latest AI reply again (the "Retry" on a voice-unavailable notice).

        Voice mode only: in text mode there is nothing to retry and nothing may
        be spoken. The text is already in chat; this only re-attempts the audio.
        """
        tts_pending = bool(self._last_error and self._last_error.get("kind") == "tts")
        if not (tts_pending or self._voice_wanted(identity)):
            log.warning(tag=TAG_ROUTER, event="retry_voice_ignored", reason="voice_not_wanted")
            return
        last = next(
            (t for t in reversed(self.context.recent_turns()) if t.role is TurnRole.BOT and t.bot_id),
            None,
        )
        if last is None or last.bot_id is None:
            return
        bot, text = last.bot_id, last.text
        turn_id = f"retry-{uuid.uuid4().hex[:8]}"

        async def runner(b: BotId, token: CancellationToken) -> None:
            agent = self.agents[b]
            trace = LatencyTrace(
                turn_id=turn_id, stt_final_at=LatencyTrace.now(),
                router_done_at=LatencyTrace.now(), source=TurnSource.VOICE, bot_id=b,
            )
            result = await agent.speak_text(text, trace=trace, token=token)
            if result.tts_failed:
                self._last_error = {
                    "bot": b.value, "kind": "tts", "at": time.time(),
                    "message": f"{b.display} ki awaaz nahi chal paayi (TTS error). Jawab chat mein hai.",
                }
            else:
                self._last_error = None
            await agent.set_state(BotState.IDLE)
            await self._publish_state()

        log.stage(TAG_ROUTER, event="retry_voice", bot=bot.value, by=identity)
        await self.turns.submit(turn_id=turn_id, bots=(bot,), runner=runner, interrupt=True)

    async def _stop_chain(self, reason: str) -> None:
        """Halt any AI<->AI exchange (and whatever is being said) immediately."""
        self._chain_left = 0
        await self.turns.interrupt(reason=reason, drop_pending=True)

    def _persist_turn(self, turn: Turn) -> None:
        """Save a turn without blocking the event loop.

        A turn that belongs to a saved conversation goes to the conversation
        store (which also refreshes the sidebar preview / title); anything else
        (no conversation selected) falls back to the legacy room history.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.history.save(turn)
            return
        cid = turn.conversation_id
        if cid is None:
            loop.run_in_executor(None, self.history.save, turn)
            return
        fut = loop.run_in_executor(None, self._save_conversation_turn, cid, turn)
        fut.add_done_callback(lambda f: self._after_save(cid, turn, f))

    def _save_conversation_turn(self, cid: str, turn: Turn) -> dict[str, object] | None:
        try:
            return self.conversations.add_turn(cid, turn)
        except Exception as exc:
            log.error(tag=TAG_CONTEXT, event="conversation_save_failed", id=cid, error=repr(exc))
            return None

    def _after_save(self, cid: str, turn: Turn, fut: asyncio.Future) -> None:
        """Back on the event loop: tell the UI the sidebar changed, maybe refine the title."""
        try:
            result = fut.result()
        except Exception:
            return
        if result is None:
            return
        self._save_seq += 1
        task = asyncio.create_task(self._publish_state())
        task.add_done_callback(_swallow)
        title = str(result["title"])
        if result["title_changed"] and turn.is_human and title != FALLBACK_TITLE and self.settings.llm_configured:
            t = asyncio.create_task(self._refine_title(cid, title, turn.text))
            t.add_done_callback(_swallow)

    async def _refine_title(self, cid: str, current: str, first_message: str) -> None:
        """Optional: a better 3-7 word title from the LLM. The deterministic one stays on failure."""
        try:
            text = await asyncio.wait_for(
                self.llm.generate(
                    [
                        LLMMessage(
                            role="system",
                            content=(
                                "Write a short title (3 to 7 words) for a chat that starts with the user's message. "
                                "Same language as the message (English or Roman Hinglish). Title Case. "
                                "No quotes, no trailing punctuation. Output only the title."
                            ),
                        ),
                        LLMMessage(role="user", content=first_message[:400]),
                    ],
                    max_tokens=24,
                    temperature=0.2,
                ),
                timeout=8.0,
            )
        except (Exception, asyncio.CancelledError):
            return
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        title = " ".join(first_line.strip(" \"'.*#").split()[:7])
        if len(title.split()) < 2 or len(title) > 60:
            return
        loop = asyncio.get_running_loop()
        changed = await loop.run_in_executor(None, self.conversations.set_auto_title, cid, current, title)
        if changed:
            self._save_seq += 1
            await self._publish_state()

    # ---- saved conversations ---------------------------------------------
    @staticmethod
    def _user_id_of(identity: str) -> int:
        m = re.match(r"^u(\d+)-", identity)
        return int(m.group(1)) if m else 0  # 0 = guest / auth off

    def _apply_conversation_settings(self, cid: str) -> None:
        settings = self.conversations.settings_of(cid) or dict(DEFAULT_SETTINGS)
        self._conv_settings = settings
        self._participants = {BotId(p) for p in self.conversations.participants_of(cid)} or set(BotId)
        self.ai_mode = bool(settings.get("ai_collab"))

    async def _activate_conversation(self, identity: str, cid: object, *, force: bool = False) -> None:
        """Make ``cid`` the conversation in context: reset, restore its turns, apply its settings.

        Safety: opening a conversation never starts audio - voice mode is cleared and
        any speech in progress is stopped; the user must press Voice again.
        """
        if not isinstance(cid, str) or self.conversations.owner(cid) != self._user_id_of(identity):
            log.warning(tag=TAG_ROUTER, event="conversation_denied", by=identity, conversation=str(cid)[:16])
            self._conversation_error = {"id": str(cid), "reason": "not_found_or_not_owner"}
            await self._publish_state()
            return
        if cid == self.active_conversation and not force:
            self._active_by = identity
            self._apply_conversation_settings(cid)
            await self._publish_state()
            return
        await self.turns.interrupt(reason="conversation_switch", drop_pending=True)
        for agent in self.agents.values():
            await agent.mute_audio()
        self._voice_users.clear()
        self._chain_left = 0
        self.active_conversation = cid
        self._conversation_error = None
        self._active_by = identity
        self.context.conversation_id = cid
        self.context.reset()
        loop = asyncio.get_running_loop()
        turns = await loop.run_in_executor(
            None, self.conversations.recent_turns, cid, self.settings.history_restore_turns
        )
        self.context.restore_turns(turns)
        # Rebuild speaker memory from the account's own restored messages.
        uid = self._user_id_of(identity)
        for t in turns:
            if t.is_human and self._user_id_of(t.speaker_identity) == uid:
                self.context.memory.ingest(identity, t.speaker_name, t.text, turn_id=t.turn_id)
        last_bot = next((t.bot_id for t in reversed(turns) if t.bot_id), None)
        if last_bot is not None:
            self.context.mark_active(last_bot)
        self._apply_conversation_settings(cid)
        self._last_human_identity = ""
        self._last_decision = None
        self._last_error = None
        # Reopening a chat never starts speech: a stored 'voice' preference is not auto-applied.
        self._response_pref = "text" if self._conv_settings.get("response_pref") == "text" else "auto"
        self._save_seq += 1
        log.stage(TAG_CONTEXT, event="conversation_active", id=cid, restored=len(turns), by=identity)
        await self._publish_state()

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
            "conversation_id": turn.conversation_id,
            "request_id": turn.request_id,
            "response_id": turn.turn_id if turn.role is TurnRole.BOT else None,
            "input_mode": turn.source.value if turn.role is TurnRole.HUMAN else None,
            "response_mode": turn.response_mode,
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
        # The turn manager never reports SPEAKING, so any state it sets (idle,
        # error after a timeout, interrupted...) means this bot is not talking:
        # never leave the echo guard stuck on.
        self._on_agent_state(bot, state)

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
            "ai_mode": {
                "enabled": self.ai_mode,
                "turns_left": self._chain_left,
                "max_turns": MAX_AI_TURNS,
            },
            "voice_users": sorted(self._voice_users),
            "conversation": {
                "id": self.active_conversation,
                "by": self._active_by,
                "error": self._conversation_error,
                "response_pref": self._response_pref,
                "seq": self._save_seq,
                "participants": sorted(b.value for b in self._participants),
                "settings": self._conv_settings,
            },
            "last_error": self._last_error,
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


def _log_control_error(task: asyncio.Task[None]) -> None:
    """A UI control message failed: never silent - the UI would just wait forever."""
    if task.cancelled():
        return
    if exc := task.exception():
        log.error(tag=TAG_ROOM, event="control_failed", error=repr(exc))


def _swallow(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    if exc := task.exception():
        log.error(tag=TAG_ROOM, event="publish_failed", error=repr(exc))
