"""Participant and bot identity models."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class BotId(str, Enum):
    """Stable identifiers for the two AI participants."""

    DOST = "dost"
    SATHI = "sathi"

    @property
    def display(self) -> str:
        return "Roxstar AI Dost" if self is BotId.DOST else "Roxstar AI Sathi"


class BotState(str, Enum):
    """Lifecycle state surfaced to the UI and used by the turn manager."""

    OFFLINE = "offline"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    ERROR = "error"


class ParticipantRole(str, Enum):
    HUMAN = "human"
    BOT = "bot"


@dataclass
class Speaker:
    """A human participant as seen by the pipeline.

    ``identity`` is the LiveKit participant identity and is the authoritative
    key for speaker-specific memory — names are display-only and may collide.
    """

    identity: str
    name: str
    role: ParticipantRole = ParticipantRole.HUMAN
    joined_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_active_at = time.time()

    @property
    def is_bot(self) -> bool:
        return self.role is ParticipantRole.BOT
