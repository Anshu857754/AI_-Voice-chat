"""The centralized bot router.

This is the single place that decides, for every incoming utterance:

    should_respond?  -> if False, nothing happens at all
    selected_bot     -> exactly ONE bot generates
    reason           -> which rule fired (logged)
    confidence       -> how sure the rule is
    queued_bots      -> optional second speaker, strictly sequential

Two invariants this module exists to guarantee:

1. **Only one bot generates.** The bot is chosen *before* any LLM call. We
   never generate two candidate answers and pick one - that doubles cost and
   latency for no benefit.
2. **Bots stay quiet unless relevant.** Human-to-human chatter passes through
   untouched, which is what makes the room feel like a meeting rather than a
   chatbot with two skins.

The rules are deterministic and ordered by the priority in the spec:

    1. explicit bot name
    2. direct reply / follow-up to the currently active bot
    3. continuation of the previous conversation
    4. topic / persona affinity
    5. turn-taking fallback (alternate)

Deterministic first, because it is fast (microseconds), free, reproducible and
unit-testable. An LLM-based router would add a network round trip to the front
of every single turn - see docs/routing-strategy.md.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app.models.conversation import RoutingDecision, RoutingReason
from app.models.participant import BotId
from app.pipeline.language import Utterance, understand
from app.utils.logging import TAG_ROUTER, get_logger

log = get_logger(__name__)


# --- Persona affinity ------------------------------------------------------
# Deliberately narrow. Persona routing only fires when a clear majority of the
# topical keywords belong to one bot; otherwise we fall through to turn-taking.
# Widening these lists would make routing feel arbitrary to users.
_DOST_TOPICS = frozenset(
    """
    ai ml technology tech machine learning deep neural network data database
    cloud computing server servers storage devops docker kubernetes api
    software hardware code coding program programming algorithm algorithms
    python javascript react node git gpu cpu memory ram latency streaming
    security network networking internet architecture system systems
    blockchain crypto model models llm gpt training dataset
    """.split()
)

_SATHI_TOPICS = frozenset(
    """
    movie movies film films cinema bollywood song songs music gaana gana
    actor actress srk khan singer dance
    food khana recipe cooking travel ghumna trip city
    festival tyohar diwali holi shaadi family ghar
    health sehat exercise yoga sleep neend mood feeling
    book books padhai school college exam study story kahani
    daily life routine relationship friend dosti
    """.split()
)

_PERSONA_MIN_HITS = 2
_PERSONA_MIN_RATIO = 2.0


@dataclass
class RouterState:
    """The routing-relevant slice of room state.

    Supplied by :class:`~app.context.manager.RoomContextManager` so the router
    stays a pure function of (utterance, state) and is trivially testable.
    """

    # Bot that produced the last relevant bot turn.
    last_responder: BotId | None = None
    last_responder_at: float | None = None
    # Bot currently producing audio, if any.
    speaking_bot: BotId | None = None
    # Last bot a human addressed by name.
    last_addressed_bot: BotId | None = None
    # Whether the previous human turn got a bot answer (drives continuity).
    last_human_was_answered: bool = False
    # Rolling counter used only by the turn-taking fallback.
    turn_counter: int = 0
    # Recent (speaker_identity, normalized_text) pairs for duplicate suppression.
    recent_human_utterances: tuple[tuple[str, str, float], ...] = field(default_factory=tuple)
    # Is there an active topic the room is discussing?
    has_active_topic: bool = False


class BotRouter:
    """Deterministic, rule-based bot selection."""

    def __init__(
        self,
        *,
        min_question_chars: int = 3,
        followup_window_s: float = 45.0,
        duplicate_window_s: float = 6.0,
    ) -> None:
        self._min_chars = min_question_chars
        self._followup_window_s = followup_window_s
        self._duplicate_window_s = duplicate_window_s

    # ---- public API -------------------------------------------------------
    def route(
        self,
        text: str,
        *,
        speaker_identity: str,
        state: RouterState,
        utterance: Utterance | None = None,
        now: float | None = None,
    ) -> RoutingDecision:
        """Pick at most one bot to answer ``text``."""
        u = utterance or understand(text)
        clock = now if now is not None else time.time()

        decision = self._decide(u, speaker_identity=speaker_identity, state=state, now=clock)
        log.stage(
            TAG_ROUTER,
            should_respond=decision.should_respond,
            selected_bot=decision.selected_bot.value if decision.selected_bot else None,
            queued=",".join(b.value for b in decision.queued_bots) or None,
            reason=decision.reason.value,
            confidence=round(decision.confidence, 2),
            language=u.language.value,
            interrupt=decision.is_interruption,
        )
        return decision

    # ---- rule chain -------------------------------------------------------
    def _decide(
        self,
        u: Utterance,
        *,
        speaker_identity: str,
        state: RouterState,
        now: float,
    ) -> RoutingDecision:
        # --- Gate 0: nothing to route -------------------------------------
        if not u.normalized or len(u.normalized) < self._min_chars:
            return RoutingDecision(False, None, RoutingReason.TOO_SHORT)

        # A bot's own transcript must never re-enter routing, or the two bots
        # would talk to each other forever.
        if speaker_identity in _bot_identities():
            return RoutingDecision(False, None, RoutingReason.BOT_SELF_ECHO)

        # Same speaker repeating themselves within a few seconds is almost
        # always STT re-emitting a final transcript, not a real second ask.
        if self._is_duplicate(u, speaker_identity, state, now):
            return RoutingDecision(False, None, RoutingReason.DUPLICATE_SUPPRESSED)

        # --- Rule 1: explicit bot name ------------------------------------
        # Highest priority and highest confidence: the user said who they want.
        # Works even for small talk ("AI Dost, hello") and even mid-answer.
        if u.addressed_bots:
            first, *rest = u.addressed_bots
            if rest:
                return RoutingDecision(
                    True,
                    first,
                    RoutingReason.EXPLICIT_MULTI_ADDRESS,
                    confidence=0.99,
                    queued_bots=tuple(rest),
                    is_interruption=self._is_interruption(u, state),
                    matched_terms=u.matched_terms,
                )
            return RoutingDecision(
                True,
                first,
                RoutingReason.EXPLICIT_ADDRESS,
                confidence=0.99,
                is_interruption=self._is_interruption(u, state),
                matched_terms=u.matched_terms,
            )

        # --- Gate 1: is a bot wanted at all? ------------------------------
        if not self._warrants_response(u, state, now):
            reason = (
                RoutingReason.SMALL_TALK
                if u.is_small_talk
                else RoutingReason.NOT_ADDRESSED_TO_BOT
            )
            return RoutingDecision(False, None, reason)

        interrupting = self._is_interruption(u, state)

        # --- Rule 2: direct follow-up to the bot that is/was just talking --
        # "Ruko, simple example se samjhao" while Dost speaks -> Dost, not Sathi.
        # Redirecting mid-answer to the *other* bot would be jarring.
        if state.speaking_bot is not None and (u.is_followup or u.is_interrupt_cue):
            return RoutingDecision(
                True,
                state.speaking_bot,
                RoutingReason.INTERRUPTION_REDIRECT if interrupting else RoutingReason.DIRECT_FOLLOWUP,
                confidence=0.9,
                is_interruption=interrupting,
            )

        # --- Rule 3: continuation of the previous conversation -------------
        # Covers "Unki famous movie batao" and Priya's "thoda aur simple batao"
        # after Rahul's question: whoever owns the thread keeps it, regardless
        # of which human is speaking now. That is the multi-user context case.
        if (
            state.last_responder is not None
            and (u.is_followup or (u.is_question and state.has_active_topic))
            and self._within_followup_window(state, now)
        ):
            return RoutingDecision(
                True,
                state.last_responder,
                RoutingReason.CONVERSATION_CONTINUITY,
                confidence=0.85 if u.is_followup else 0.7,
                is_interruption=interrupting,
            )

        # --- Rule 3b: memory questions ------------------------------------
        # Answered from the asking speaker's own memory. Thread owner answers
        # if there is one, so the room keeps a single voice.
        if u.is_memory_query:
            bot = state.last_responder or state.last_addressed_bot or self._next_in_rotation(state)
            return RoutingDecision(
                True, bot, RoutingReason.MEMORY_QUERY, confidence=0.8, is_interruption=interrupting
            )

        # --- Rule 4: topic / persona affinity -----------------------------
        if (persona := self._persona_match(u)) is not None:
            return RoutingDecision(
                True, persona, RoutingReason.PERSONA_TOPIC, confidence=0.65,
                is_interruption=interrupting,
            )

        # --- Rule 4b: self-introduction gets a short acknowledgement ------
        if u.is_self_disclosure:
            bot = state.last_responder or self._next_in_rotation(state)
            return RoutingDecision(
                True, bot, RoutingReason.SELF_DISCLOSURE, confidence=0.6,
                is_interruption=interrupting,
            )

        # --- Rule 5: turn-taking fallback ---------------------------------
        # A fresh, unaddressed question with no topical lean: alternate so
        # neither bot monopolises the room, and so exactly one answers.
        return RoutingDecision(
            True,
            self._next_in_rotation(state),
            RoutingReason.TURN_TAKING,
            confidence=0.5,
            is_interruption=interrupting,
        )

    # ---- helpers ----------------------------------------------------------
    def _warrants_response(self, u: Utterance, state: RouterState, now: float) -> bool:
        """Relevance gate: should any bot speak?

        Yes when the utterance is a question/request, a memory query, a
        follow-up inside an active thread, or a self-introduction. Plain
        human-to-human statements and greetings get silence.
        """
        if u.is_small_talk:
            # Greetings deserve a friendly reply; acks ("haan", "ok") do not.
            return bool(_GREETING.match(u.normalized))
        if u.is_memory_query or u.is_question:
            return True
        if u.is_self_disclosure:
            return True
        if u.addressed_generic:
            return True
        # Mid-conversation with a bot, plain statements are part of the chat
        # too ("aaj thak gaya", "ignore that and ..."), not human-to-human talk.
        if state.last_responder is not None and self._within_followup_window(state, now):
            return True
        if u.is_followup and state.last_responder is not None and self._within_followup_window(state, now):
            return True
        return False

    def _within_followup_window(self, state: RouterState, now: float) -> bool:
        if state.last_responder_at is None:
            return False
        return (now - state.last_responder_at) <= self._followup_window_s

    def _is_interruption(self, u: Utterance, state: RouterState) -> bool:
        """A human talking while a bot has the floor is a barge-in."""
        return state.speaking_bot is not None

    def _is_duplicate(
        self, u: Utterance, speaker_identity: str, state: RouterState, now: float
    ) -> bool:
        for identity, normalized, at in state.recent_human_utterances:
            if identity != speaker_identity or normalized != u.normalized:
                continue
            if (now - at) <= self._duplicate_window_s:
                return True
        return False

    def _persona_match(self, u: Utterance) -> BotId | None:
        tokens = set(u.normalized.split(" "))
        dost_hits = len(tokens & _DOST_TOPICS)
        sathi_hits = len(tokens & _SATHI_TOPICS)
        if max(dost_hits, sathi_hits) < _PERSONA_MIN_HITS:
            return None
        if dost_hits >= sathi_hits * _PERSONA_MIN_RATIO and dost_hits > sathi_hits:
            return BotId.DOST
        if sathi_hits >= dost_hits * _PERSONA_MIN_RATIO and sathi_hits > dost_hits:
            return BotId.SATHI
        return None

    @staticmethod
    def _next_in_rotation(state: RouterState) -> BotId:
        """Default responder: stay with whoever spoke last, else Dost.

        One user message gets one AI reply, and the conversation keeps a
        single voice unless the user names the other AI or the topic clearly
        belongs to it.
        """
        return state.last_responder or BotId.DOST


_GREETING = re.compile(
    r"^(?:hello|hi|hey|namaste|namaskar|good\s+(?:morning|afternoon|evening|night))[\s!.]*$", re.I
)


def _bot_identities() -> frozenset[str]:
    """LiveKit identities of our own bots, so we can ignore their transcripts."""
    from app.config.settings import get_settings

    s = get_settings()
    return frozenset({s.dost_identity, s.sathi_identity})
