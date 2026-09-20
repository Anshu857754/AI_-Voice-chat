"""RoomContextManager - the single shared brain of the room.

Everything that happens in the room lands here, whether it arrived as voice or
as text chat, and every bot reads from here. That is what makes the two AI
participants feel like they are in *one* conversation with *several* people
rather than each running a private chat session.

Responsibilities
----------------
* the ordered room transcript (bounded)
* a recent verbatim window + one rolling summary of everything older
  (see docs/context-management.md for the exact strategy)
* the current topic, for pronoun resolution ("Unki famous movie batao")
* per-speaker memory, strictly partitioned (:mod:`app.context.memory`)
* the routing-relevant state snapshot the router needs
* assembling the final message list sent to the LLM for a given bot

Thread/async safety: all mutation happens on the orchestrator's event loop, so
no locking is needed here beyond the summarizer's own.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.agents.prompts import render_persona
from app.config.settings import Settings, get_settings
from app.context.memory import MemoryStore
from app.context.summarizer import ConversationSummarizer
from app.models.conversation import Turn, TurnRole, TurnSource
from app.models.participant import BotId, Speaker
from app.pipeline.language import Utterance, understand
from app.pipeline.llm import LLMMessage
from app.routing.router import RouterState
from app.utils.logging import TAG_CONTEXT, get_logger

log = get_logger(__name__)

# Hard ceiling on retained turns. Older turns live on only through the summary.
_MAX_TURNS = 400
# How many recent human utterances the duplicate suppressor can see.
_DUP_WINDOW = 8
# Minimum words for a human turn to be treated as a new topic.
_TOPIC_MIN_WORDS = 3

TurnListener = Callable[[Turn], None]


@dataclass
class ContextBundle:
    """What was actually sent to the LLM for one turn. Logged for debugging."""

    messages: list[LLMMessage]
    recent_turns_used: int
    summary_used: bool
    speaker_facts_used: bool
    total_chars: int


class RoomContextManager:
    """Shared conversation state for one room."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        summarizer: ConversationSummarizer | None = None,
    ) -> None:
        s = settings or get_settings()
        self._settings = s
        self._recent_window = s.context_recent_turns
        self._summary_trigger = s.context_summary_trigger
        self._summary_keep = s.context_summary_keep
        self._summarizer = summarizer

        self._turns: deque[Turn] = deque(maxlen=_MAX_TURNS)
        self._summary: str = ""
        # Turns already folded into the summary are not re-summarized.
        self._summarized_upto: int = 0
        self._turn_seq: int = 0
        self._summary_task: asyncio.Task[None] | None = None

        self.memory = MemoryStore()
        self._speakers: dict[str, Speaker] = {}

        # Routing state.
        self._last_responder: BotId | None = None
        self._last_responder_at: float | None = None
        self._last_addressed_bot: BotId | None = None
        self._last_bot_reply: str = ""
        self._last_human_was_answered: bool = False
        self._current_topic: str = ""
        self._topic_set_at: float | None = None
        # Short LLM-written line about what is being discussed right now.
        # Shown in the UI and fed back to the bots as private background.
        self._live_topic: str = ""
        self._topic_task: asyncio.Task[None] | None = None
        self._turns_since_topic: int = 0

        self._listeners: list[TurnListener] = []
        # Called when the live topic changes so the UI can be refreshed.
        self.on_topic_change: Callable[[], None] | None = None

    # ---- participants -----------------------------------------------------
    def register_speaker(self, speaker: Speaker) -> None:
        self._speakers[speaker.identity] = speaker
        if not speaker.is_bot:
            # Pre-create the memory slot so the roster knows who is present.
            self.memory.for_speaker(speaker.identity, speaker.name)
        log.stage(
            TAG_CONTEXT, event="speaker_registered",
            identity=speaker.identity, name=speaker.name, role=speaker.role.value,
        )

    def unregister_speaker(self, identity: str) -> None:
        self._speakers.pop(identity, None)

    def speaker_name(self, identity: str) -> str:
        speaker = self._speakers.get(identity)
        if speaker:
            return speaker.name
        mem = self.memory.for_speaker(identity)
        return mem.known_name or mem.name

    @property
    def human_speakers(self) -> list[Speaker]:
        return [s for s in self._speakers.values() if not s.is_bot]

    # ---- listeners --------------------------------------------------------
    def add_listener(self, listener: TurnListener) -> None:
        """Called for every committed turn (used to mirror the transcript to UI)."""
        self._listeners.append(listener)

    def _notify(self, turn: Turn) -> None:
        for listener in self._listeners:
            try:
                listener(turn)
            except Exception as exc:
                log.error(tag=TAG_CONTEXT, event="listener_failed", error=repr(exc))

    # ---- ingestion --------------------------------------------------------
    def add_human_turn(
        self,
        *,
        text: str,
        speaker_identity: str,
        speaker_name: str = "",
        source: TurnSource = TurnSource.VOICE,
        utterance: Utterance | None = None,
    ) -> tuple[Turn, Utterance]:
        """Commit a human utterance - the single entry point for voice AND text."""
        u = utterance or understand(text)
        name = speaker_name or self.speaker_name(speaker_identity)
        turn = Turn(
            role=TurnRole.HUMAN,
            text=text.strip(),
            speaker_identity=speaker_identity,
            speaker_name=name,
            source=source,
            language=u.language.value,
        )
        self._turns.append(turn)
        self._turn_seq += 1

        if speaker := self._speakers.get(speaker_identity):
            speaker.touch()

        # Speaker-specific memory is written on every human turn, even when no
        # bot answers - silence should not cost us the fact.
        self.memory.ingest(speaker_identity, name, turn.text, turn_id=turn.turn_id)

        if u.addressed_bots:
            self._last_addressed_bot = u.addressed_bots[0]

        # A substantive question becomes the room's active topic. Follow-ups
        # deliberately do NOT reset it, so "Unki famous movie batao" still
        # resolves against "Shah Rukh Khan". Memory queries ("Maine apne
        # baare mein kya bataya tha?") are excluded too: `is_question` is
        # forced True for them regardless of phrasing, but they ask about the
        # conversation's own history rather than introducing a new subject -
        # letting one overwrite the topic derails whatever was actually being
        # discussed (observed live: asking a memory question mid-way through
        # an ML explanation made the room's "topic" become the memory
        # question itself, so a later "give an example" request lost the ML
        # thread and got a recap of the asker's own facts instead).
        if (
            u.is_question
            and not u.is_followup
            and not u.is_memory_query
            and u.word_count >= _TOPIC_MIN_WORDS
        ):
            self._current_topic = turn.text
            self._topic_set_at = turn.created_at

        self._last_human_was_answered = False
        log.stage(
            TAG_CONTEXT, event="human_turn", speaker=speaker_identity, source=source.value,
            language=u.language.value, turns=len(self._turns),
        )
        self._notify(turn)
        self._maybe_summarize()
        self._maybe_update_topic()
        return turn, u

    def add_bot_turn(
        self,
        *,
        text: str,
        bot: BotId,
        source: TurnSource = TurnSource.VOICE,
        interrupted: bool = False,
    ) -> Turn:
        """Commit a bot reply, including a partial one cut off by barge-in."""
        turn = Turn(
            role=TurnRole.BOT,
            text=text.strip(),
            speaker_identity=self._bot_identity(bot),
            speaker_name=bot.display,
            source=source,
            bot_id=bot,
            interrupted=interrupted,
        )
        self._turns.append(turn)
        self._turn_seq += 1
        self._last_responder = bot
        self._last_responder_at = turn.created_at
        self._last_bot_reply = turn.text
        self._last_human_was_answered = True
        log.stage(
            TAG_CONTEXT, event="bot_turn", bot=bot.value, interrupted=interrupted,
            chars=len(turn.text), turns=len(self._turns),
        )
        self._notify(turn)
        self._maybe_summarize()
        self._maybe_update_topic()
        return turn

    def _bot_identity(self, bot: BotId) -> str:
        return (
            self._settings.dost_identity
            if bot is BotId.DOST
            else self._settings.sathi_identity
        )

    # ---- routing state ----------------------------------------------------
    def router_state(
        self, *, speaking_bot: BotId | None = None, exclude_turn_id: str | None = None
    ) -> RouterState:
        """Snapshot for the router. Cheap: no copies of the transcript.

        ``exclude_turn_id`` must be the turn currently being routed. The
        caller always calls ``add_human_turn`` before ``router_state`` (so
        the current utterance can be part of the context the router itself
        reasons about), which means that turn is already the newest entry in
        ``self._turns``. Without excluding it here, the duplicate-suppression
        window would compare the current utterance against itself - same
        speaker, same text, near-zero elapsed time - and every turn would be
        flagged as a duplicate of itself. Filtering by turn_id (rather than
        just dropping the last element) keeps this correct even if a caller
        ever calls this before appending, or appends something else in
        between.
        """
        recent_humans = tuple(
            (t.speaker_identity, understand(t.text).normalized, t.created_at)
            for t in list(self._turns)[-(_DUP_WINDOW + 1) :]
            if t.is_human and t.turn_id != exclude_turn_id
        )[-_DUP_WINDOW:]
        return RouterState(
            last_responder=self._last_responder,
            last_responder_at=self._last_responder_at,
            speaking_bot=speaking_bot,
            last_addressed_bot=self._last_addressed_bot,
            last_human_was_answered=self._last_human_was_answered,
            turn_counter=self._turn_seq,
            recent_human_utterances=recent_humans,
            has_active_topic=bool(self._current_topic),
        )

    # ---- prompt assembly --------------------------------------------------
    def recent_turns(self, limit: int | None = None) -> list[Turn]:
        n = limit if limit is not None else self._recent_window
        return list(self._turns)[-n:] if n > 0 else []

    def build_messages(
        self,
        *,
        bot: BotId,
        system_prompt: str,
        asking_identity: str,
        style_instruction: str = "",
        extra_instruction: str = "",
        include_memory: bool = True,
    ) -> ContextBundle:
        """Assemble the LLM message list for one bot's reply.

        Layout (in order):
          1. the bot's persona system prompt
          2. reply-style instruction (language + length for this specific turn)
          3. room state block: who is present, rolling summary, active topic
          4. the asking speaker's own facts - and nobody else's
          5. the recent verbatim window as alternating chat turns
        """
        asker = self.speaker_name(asking_identity)
        # One system message: several providers (Anthropic via OpenRouter)
        # fold multiple system messages together anyway, so keep it explicit.
        parts: list[str] = [
            render_persona(system_prompt, bot_value=bot.value, user_name=asker or "user")
        ]
        if style_instruction:
            parts.append(style_instruction)

        background: list[str] = []
        roster = self.memory.render_roster()
        if roster:
            background.append(f"Baat-cheet mein ye log hain: {roster}.")
        if self._summary:
            background.append(f"Pehle ki baaton ka saar: {self._summary}")
        topic = self._live_topic or self._current_topic
        if topic:
            background.append(f"Abhi ki baat: {topic}")
        if background:
            parts.append(
                "[PRIVATE BACKGROUND - sirf tumhari yaad ke liye. Ise kabhi quote ya "
                "mention mat karo, aur 'topic', 'context', 'summary' jaise shabd mat bolo.]\n"
                + "\n".join(background)
                + "\nAgar user 'iska', 'uska', 'woh' jaisa kuch bole to pichli baat se resolve karo."
            )

        facts_used = False
        if include_memory:
            facts = self.memory.render_for(asking_identity)
            if facts:
                facts_used = True
                parts.append(
                    f"[{asker} ke baare mein jo pata hai - private]\n{facts}\n"
                    "Sirf isi insaan ki information hai; kisi aur ki details use mat karo."
                )
        if extra_instruction:
            parts.append(extra_instruction)

        messages: list[LLMMessage] = [LLMMessage(role="system", content="\n\n".join(parts))]

        window = self.recent_turns()
        # A bot reply is committed only after it has been spoken, so if the
        # user talked/typed over it, that older reply lands *after* the newest
        # human turn. Keep the message the bot must answer last: an
        # assistant-final conversation is treated as a prefill by Claude and
        # yields an empty reply.
        last_human = max((i for i, t in enumerate(window) if t.is_human), default=None)
        if last_human is not None and last_human != len(window) - 1:
            window = [*window[:last_human], *window[last_human + 1 :], window[last_human]]
        for turn in window:
            if turn.is_human:
                prefix = f"{turn.speaker_name}: "
                messages.append(LLMMessage(role="user", content=prefix + turn.text))
            elif turn.bot_id is bot:
                # This bot's own prior replies are assistant turns.
                suffix = " [...interrupted]" if turn.interrupted else ""
                messages.append(LLMMessage(role="assistant", content=turn.text + suffix))
            else:
                # The *other* bot's replies are context, not this bot's voice.
                messages.append(
                    LLMMessage(
                        role="user",
                        content=f"({turn.speaker_name} ne abhi kaha): {turn.text}",
                    )
                )

        bundle = ContextBundle(
            messages=messages,
            recent_turns_used=len(window),
            summary_used=bool(self._summary),
            speaker_facts_used=facts_used,
            total_chars=sum(len(m.content) for m in messages),
        )
        log.stage(
            TAG_CONTEXT,
            bot=bot.value,
            messages_used=len(messages),
            recent_turns=bundle.recent_turns_used,
            summary=bundle.summary_used,
            speaker_facts=bundle.speaker_facts_used,
            prompt_chars=bundle.total_chars,
        )
        return bundle

    # ---- summarization ----------------------------------------------------
    @property
    def summary(self) -> str:
        return self._summary

    def _maybe_summarize(self) -> None:
        """Kick off a background fold once the transcript outgrows the window."""
        if self._summarizer is None:
            return
        unsummarized = len(self._turns) - self._summarized_upto
        if unsummarized < self._summary_trigger:
            return
        if self._summary_task is not None and not self._summary_task.done():
            return
        # Fold everything except the tail we still want verbatim.
        cutoff = max(0, len(self._turns) - self._summary_keep)
        batch = list(self._turns)[self._summarized_upto : cutoff]
        if not batch:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop (e.g. synchronous unit test): summarize lazily later.
            return
        self._summary_task = loop.create_task(self._run_summary(batch, cutoff))
        self._summary_task.add_done_callback(self._on_summary_done)

    async def _run_summary(self, batch: Sequence[Turn], cutoff: int) -> None:
        assert self._summarizer is not None
        self._summary = await self._summarizer.summarize(batch, self._summary)
        self._summarized_upto = cutoff

    def _on_summary_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        if exc := task.exception():
            log.error(tag=TAG_CONTEXT, event="summary_task_failed", error=repr(exc))

    async def flush_summary(self) -> None:
        """Await any in-flight summarization. Used by tests and shutdown."""
        if self._summary_task is not None and not self._summary_task.done():
            await asyncio.shield(self._summary_task)

    # ---- live topic -------------------------------------------------------
    @property
    def live_topic(self) -> str:
        return self._live_topic or self._current_topic

    def _maybe_update_topic(self) -> None:
        """Refresh the one-line topic every couple of turns, off the hot path."""
        if self._summarizer is None:
            return
        self._turns_since_topic += 1
        if self._turns_since_topic < 2 and self._live_topic:
            return
        if self._topic_task is not None and not self._topic_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._turns_since_topic = 0
        batch = list(self._turns)[-8:]
        self._topic_task = loop.create_task(self._run_topic(batch))
        self._topic_task.add_done_callback(self._on_summary_done)

    async def _run_topic(self, batch: Sequence[Turn]) -> None:
        assert self._summarizer is not None
        topic = await self._summarizer.topic(batch, self._live_topic)
        if topic and topic != self._live_topic:
            self._live_topic = topic
            log.stage(TAG_CONTEXT, event="topic_updated", topic=topic)
            if self.on_topic_change is not None:
                self.on_topic_change()

    # ---- interruption bookkeeping ----------------------------------------
    def mark_interrupted(self, bot: BotId, spoken_text: str) -> None:
        """Record what a bot actually managed to say before being cut off.

        Storing the partial text (not the full intended reply) keeps context
        honest: the room only heard that much.
        """
        if spoken_text.strip():
            self.add_bot_turn(text=spoken_text, bot=bot, interrupted=True)

    # ---- introspection ----------------------------------------------------
    def transcript(self, limit: int = 100) -> list[dict[str, object]]:
        return [
            {
                "turn_id": t.turn_id,
                "role": t.role.value,
                "bot": t.bot_id.value if t.bot_id else None,
                "speaker_identity": t.speaker_identity,
                "speaker_name": t.speaker_name,
                "text": t.text,
                "source": t.source.value,
                "language": t.language,
                "interrupted": t.interrupted,
                "created_at": t.created_at,
            }
            for t in list(self._turns)[-limit:]
        ]

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "turns": len(self._turns),
            "recent_window": self._recent_window,
            "summary_chars": len(self._summary),
            "summarized_upto": self._summarized_upto,
            "current_topic": self.live_topic,
            "summary": self._summary,
            "last_responder": self._last_responder.value if self._last_responder else None,
            "speakers": [s.identity for s in self.human_speakers],
            "memory": self.memory.snapshot(),
        }

    def reset(self) -> None:
        self._turns.clear()
        self._summary = ""
        self._summarized_upto = 0
        self._turn_seq = 0
        self._last_responder = None
        self._last_responder_at = None
        self._last_addressed_bot = None
        self._last_bot_reply = ""
        self._current_topic = ""
        self._live_topic = ""
        self._turns_since_topic = 0
        self.memory.clear()
