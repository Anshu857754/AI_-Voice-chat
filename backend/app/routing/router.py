"""The centralized responder picker.

``pick_responder(text, state, ...)`` is the ONE place that decides who answers a
human message. Nothing else in the codebase (frontend, agents, workers) makes
that decision.

Rules (in order):

    1. Every human message gets an answer - greetings, acks, statements, all of it.
    2. Exactly ONE AI answers. If the message names Dost or Sathi, that AI
       answers; when both are named, the one named FIRST answers and the other
       stays silent.
    3. No name -> the *active* AI (whoever answered last) keeps the conversation
       going, so it feels like one continuous chat. With no history: Dost.
    4. Never answer an AI's own messages (bot identities are ignored), and never
       answer twice for a duplicate STT final (voice only).

There is deliberately no relevance gate, persona affinity or rotation: those
made replies unpredictable ("Idle" with no answer, or the other AI jumping in).
The rules are deterministic and free, so they run before any LLM call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.models.conversation import RoutingDecision, RoutingReason
from app.models.participant import BotId
from app.pipeline.language import Utterance, understand
from app.utils.logging import TAG_ROUTER, get_logger

log = get_logger(__name__)

_DUPLICATE_WINDOW_S = 6.0


@dataclass
class RouterState:
    """The routing-relevant slice of room state (supplied by the context manager)."""

    # Bot that answered last: the "active AI".
    last_responder: BotId | None = None
    last_responder_at: float | None = None
    # Bot currently producing audio, if any.
    speaking_bot: BotId | None = None
    last_addressed_bot: BotId | None = None
    last_human_was_answered: bool = False
    turn_counter: int = 0
    # Recent (speaker_identity, normalized_text, at) for voice duplicate suppression.
    recent_human_utterances: tuple[tuple[str, str, float], ...] = field(default_factory=tuple)
    has_active_topic: bool = False


def pick_responder(
    text: str,
    state: RouterState,
    *,
    speaker_identity: str = "",
    utterance: Utterance | None = None,
    dedupe: bool = True,
    now: float | None = None,
) -> RoutingDecision:
    """Choose the single AI that answers ``text`` (or nobody, for non-messages)."""
    u = utterance or understand(text)
    clock = now if now is not None else time.time()
    decision = _decide(u, state, speaker_identity=speaker_identity, dedupe=dedupe, now=clock)
    log.stage(
        TAG_ROUTER,
        should_respond=decision.should_respond,
        selected_bot=decision.selected_bot.value if decision.selected_bot else None,
        reason=decision.reason.value,
        language=u.language.value,
        interrupt=decision.is_interruption,
    )
    return decision


def _decide(
    u: Utterance, state: RouterState, *, speaker_identity: str, dedupe: bool, now: float
) -> RoutingDecision:
    if not u.normalized:
        return RoutingDecision(False, None, RoutingReason.TOO_SHORT)

    # An AI's own words must never trigger a reply (no AI -> AI chatter).
    if speaker_identity in _bot_identities():
        return RoutingDecision(False, None, RoutingReason.BOT_SELF_ECHO)

    # STT can re-emit the same final transcript; typed messages never dedupe.
    if dedupe and _is_duplicate(u, speaker_identity, state, now):
        return RoutingDecision(False, None, RoutingReason.DUPLICATE_SUPPRESSED)

    interrupting = state.speaking_bot is not None

    if u.addressed_bots:
        # First-named AI answers; any others named later stay silent.
        return RoutingDecision(
            True,
            u.addressed_bots[0],
            RoutingReason.EXPLICIT_ADDRESS,
            confidence=0.99,
            is_interruption=interrupting,
            matched_terms=u.matched_terms,
        )

    if state.last_responder is not None:
        return RoutingDecision(
            True,
            state.last_responder,
            RoutingReason.CONVERSATION_CONTINUITY,
            confidence=0.85,
            is_interruption=interrupting,
        )
    return RoutingDecision(
        True, BotId.DOST, RoutingReason.TURN_TAKING, confidence=0.5, is_interruption=interrupting
    )


def _is_duplicate(
    u: Utterance, speaker_identity: str, state: RouterState, now: float
) -> bool:
    for identity, normalized, at in state.recent_human_utterances:
        if identity == speaker_identity and normalized == u.normalized and (now - at) <= _DUPLICATE_WINDOW_S:
            return True
    return False


def _bot_identities() -> frozenset[str]:
    """LiveKit identities of our own bots, so we can ignore their transcripts."""
    from app.config.settings import get_settings

    s = get_settings()
    return frozenset({s.dost_identity, s.sathi_identity})


class BotRouter:
    """Thin wrapper kept for callers that hold a router object."""

    def __init__(self, **_: object) -> None:
        pass

    def route(
        self,
        text: str,
        *,
        speaker_identity: str,
        state: RouterState,
        utterance: Utterance | None = None,
        now: float | None = None,
        dedupe: bool = True,
    ) -> RoutingDecision:
        return pick_responder(
            text, state, speaker_identity=speaker_identity, utterance=utterance, dedupe=dedupe, now=now
        )
