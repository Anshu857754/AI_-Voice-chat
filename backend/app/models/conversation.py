"""Conversation, routing and latency models.

These are plain dataclasses on purpose: they are passed between the STT,
router, context and TTS stages many times per turn, so they must be cheap to
construct and trivially testable without any I/O.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from app.models.participant import BotId


class TurnSource(str, Enum):
    """How an utterance entered the room."""

    VOICE = "voice"
    TEXT = "text"
    SYSTEM = "system"


class TurnRole(str, Enum):
    HUMAN = "human"
    BOT = "bot"


@dataclass
class Turn:
    """One utterance in the shared room transcript.

    Voice and text share this type - that is what makes both input paths land
    in a single context (requirement: no separate context for chat).
    """

    role: TurnRole
    text: str
    speaker_identity: str
    speaker_name: str
    source: TurnSource = TurnSource.VOICE
    bot_id: BotId | None = None
    language: str | None = None
    created_at: float = field(default_factory=time.time)
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    interrupted: bool = False
    # Which saved conversation this turn belongs to (None = legacy room history).
    conversation_id: str | None = None
    # Correlation: a bot reply points at the human message it answers (request_id) and
    # records how it was delivered (response_mode: text | voice). turn_id is the response id.
    request_id: str | None = None
    response_mode: str | None = None

    @property
    def is_human(self) -> bool:
        return self.role is TurnRole.HUMAN

    def as_transcript_line(self) -> str:
        who = self.speaker_name if self.is_human else (self.bot_id.display if self.bot_id else "AI")
        return f"{who}: {self.text}"


class RoutingReason(str, Enum):
    """Why the router reached its decision - logged verbatim for debugging."""

    EXPLICIT_ADDRESS = "explicit_address"
    EXPLICIT_MULTI_ADDRESS = "explicit_multi_address"
    DIRECT_FOLLOWUP = "direct_followup"
    CONVERSATION_CONTINUITY = "conversation_continuity"
    PERSONA_TOPIC = "persona_topic"
    TURN_TAKING = "turn_taking"
    MEMORY_QUERY = "memory_query"
    SELF_DISCLOSURE = "self_disclosure"
    NOT_ADDRESSED_TO_BOT = "not_addressed_to_bot"
    SMALL_TALK = "small_talk"
    TOO_SHORT = "too_short"
    BOT_SELF_ECHO = "bot_self_echo"
    DUPLICATE_SUPPRESSED = "duplicate_suppressed"
    INTERRUPTION_REDIRECT = "interruption_redirect"
    AI_CHAIN = "ai_chain"


@dataclass
class RoutingDecision:
    """Output of the centralized router.

    ``selected_bot`` is chosen *before* any LLM call. ``queued_bots`` supports
    the "Dost tum answer karo, Sathi baad mein example dena" case: a second bot
    may be scheduled to speak strictly after the first finishes - never
    concurrently.
    """

    should_respond: bool
    selected_bot: BotId | None
    reason: RoutingReason
    confidence: float = 0.0
    queued_bots: tuple[BotId, ...] = ()
    is_interruption: bool = False
    matched_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.should_respond:
            # Invariant the turn manager relies on: no responder, no queue.
            self.selected_bot = None
            self.queued_bots = ()

    @property
    def all_bots(self) -> tuple[BotId, ...]:
        if not self.should_respond or self.selected_bot is None:
            return ()
        return (self.selected_bot, *self.queued_bots)


@dataclass
class LatencyTrace:
    """Real measured timings for one turn. Nothing here is estimated.

    Each stage stamps a monotonic timestamp as it completes; the derived
    properties are simple differences, so a stage that never ran reports
    ``None`` rather than a made-up number.
    """

    turn_id: str
    # Monotonic stamps (seconds). None == stage did not run.
    speech_end_at: float | None = None
    stt_final_at: float | None = None
    router_done_at: float | None = None
    llm_first_token_at: float | None = None
    llm_done_at: float | None = None
    tts_first_frame_at: float | None = None
    published_at: float | None = None

    source: TurnSource = TurnSource.VOICE
    bot_id: BotId | None = None

    @staticmethod
    def now() -> float:
        return time.perf_counter()

    @staticmethod
    def _ms(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return round((b - a) * 1000.0, 1)

    @property
    def stt_latency_ms(self) -> float | None:
        return self._ms(self.speech_end_at, self.stt_final_at)

    @property
    def router_latency_ms(self) -> float | None:
        return self._ms(self.stt_final_at, self.router_done_at)

    @property
    def llm_ttft_ms(self) -> float | None:
        return self._ms(self.router_done_at, self.llm_first_token_at)

    @property
    def llm_latency_ms(self) -> float | None:
        return self._ms(self.router_done_at, self.llm_done_at)

    @property
    def tts_latency_ms(self) -> float | None:
        """First LLM token to first synthesized audio frame."""
        return self._ms(self.llm_first_token_at, self.tts_first_frame_at)

    @property
    def total_latency_ms(self) -> float | None:
        """End of human speech (or chat send) to first audio on the wire."""
        start = self.speech_end_at if self.speech_end_at is not None else self.stt_final_at
        return self._ms(start, self.published_at)

    def as_log_fields(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "source": self.source.value,
            "bot": self.bot_id.value if self.bot_id else None,
            "stt_latency_ms": self.stt_latency_ms,
            "router_latency_ms": self.router_latency_ms,
            "llm_ttft_ms": self.llm_ttft_ms,
            "llm_latency_ms": self.llm_latency_ms,
            "tts_latency_ms": self.tts_latency_ms,
            "total_latency_ms": self.total_latency_ms,
        }
