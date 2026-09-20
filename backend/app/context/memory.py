"""Per-speaker memory.

Each human participant gets their own fact store, keyed by **LiveKit
participant identity** (not display name, which can collide). When a bot
answers "Maine apne baare mein kya bataya tha?" it is handed only the asking
speaker's facts, so Priya's details can never surface in Rahul's answer.

Extraction is rule-based and runs synchronously on every human turn:
  * it costs nothing and cannot fail mid-conversation
  * it is deterministic, so the behaviour is unit-testable
  * the patterns cover the self-disclosure forms people actually use in
    Hinglish ("mera naam X hai", "mujhe X pasand hai", "main X se hoon")

Storage is in-memory by design for the MVP. :class:`MemoryStore` is the seam
where Redis or PostgreSQL would slot in later - see docs/context-management.md.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app.utils.logging import TAG_MEMORY, get_logger

log = get_logger(__name__)

# Fact keys we recognise. Kept small and closed so rendered memory stays short.
KEY_NAME = "name"
KEY_INTEREST = "interest"
KEY_LOCATION = "location"
KEY_WORK = "work"
KEY_NOTE = "note"

_SINGLE_VALUE_KEYS = frozenset({KEY_NAME, KEY_LOCATION, KEY_WORK})
_MAX_VALUES_PER_KEY = 5
_MAX_VALUE_CHARS = 80

# Words that are never a person's name even if they follow "mera naam".
_NAME_STOPWORDS = frozenset({"kya", "kaun", "batao", "hai", "he", "what", "is"})

_WORD = r"[A-Za-zऀ-ॿ][\wऀ-ॿ'.-]*"

# (key, compiled pattern) — first capture group is the value.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (KEY_NAME, re.compile(rf"\bmer[aei]\s+naam\s+({_WORD}(?:\s+{_WORD})?)\s*(?:hai|h\b|hun|hoon)", re.I)),
    (KEY_NAME, re.compile(rf"\bmy\s+name\s+is\s+({_WORD}(?:\s+{_WORD})?)", re.I)),
    (KEY_NAME, re.compile(rf"\bi\s?.?m\s+({_WORD})\s*(?:,|and|\.|$)", re.I)),
    (KEY_NAME, re.compile(rf"\bमेर[ाीे]\s+नाम\s+({_WORD}(?:\s+{_WORD})?)\s*ह")),
    # Interests. Non-greedy up to the marker so "mujhe cricket pasand hai"
    # yields "cricket" and not the rest of the sentence.
    (KEY_INTEREST, re.compile(r"\bmujhe\s+(.{2,60}?)\s+(?:pasand|achha lagta|acha lagta|accha lagta|bahut pasand)\b", re.I)),
    (KEY_INTEREST, re.compile(r"\bi\s+(?:really\s+)?(?:like|love|enjoy)\s+(.{2,60}?)(?:\s+(?:a lot|very much))?\s*(?:,|and|\.|$)", re.I)),
    (KEY_INTEREST, re.compile(r"\bमुझे\s+(.{2,60}?)\s+पसंद\b")),
    (KEY_INTEREST, re.compile(r"\bmera\s+(?:favourite|favorite|pasandida)\s+\w+\s+(?:hai\s+)?(.{2,40}?)\s*(?:hai|,|\.|$)", re.I)),
    # Location.
    (KEY_LOCATION, re.compile(rf"\bmain\s+({_WORD}(?:\s+{_WORD})?)\s+se\s+(?:hoon|hun|hu|ho)\b", re.I)),
    (KEY_LOCATION, re.compile(rf"\bi\s?.?m\s+from\s+({_WORD}(?:\s+{_WORD})?)", re.I)),
    (KEY_LOCATION, re.compile(rf"\bi\s+live\s+in\s+({_WORD}(?:\s+{_WORD})?)", re.I)),
    (KEY_LOCATION, re.compile(rf"\bमैं\s+({_WORD}(?:\s+{_WORD})?)\s+से\s+ह")),
    # Work / study.
    (KEY_WORK, re.compile(r"\bi\s+work\s+(?:as|at|in)\s+(.{2,50}?)\s*(?:,|and|\.|$)", re.I)),
    (KEY_WORK, re.compile(r"\bmain\s+(.{2,50}?)\s+(?:ka\s+kaam|kaam)\s+karta\s+h", re.I)),
    (KEY_WORK, re.compile(r"\bi\s+(?:study|am studying)\s+(.{2,50}?)\s*(?:,|and|\.|$)", re.I)),
)

_TRAILING_JUNK = re.compile(r"\s*(?:hai|hain|hoon|hun|h|and|aur|bhi|.)\s*$", re.I)


@dataclass(frozen=True)
class Fact:
    key: str
    value: str
    source_turn_id: str = ""
    created_at: float = field(default_factory=time.time)


def _clean_value(value: str) -> str:
    text = " ".join(value.split()).strip(" ,.;:!?-")
    # Drop a dangling copula the regex may have swept up.
    for _ in range(2):
        stripped = re.sub(r"\s+(?:hai|hain|hoon|hun|aur|and|bhi)$", "", text, flags=re.I).strip()
        if stripped == text:
            break
        text = stripped
    return text[:_MAX_VALUE_CHARS].strip()


def extract_facts(text: str, *, turn_id: str = "") -> list[Fact]:
    """Pull self-disclosed facts out of one utterance.

    Runs on the raw (case-preserving) text so names keep their capitalisation.
    """
    if not text:
        return []
    found: list[Fact] = []
    seen: set[tuple[str, str]] = set()
    for key, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = _clean_value(match.group(1))
            if not value or len(value) < 2:
                continue
            if key == KEY_NAME and value.lower() in _NAME_STOPWORDS:
                continue
            dedupe = (key, value.lower())
            if dedupe in seen:
                continue
            seen.add(dedupe)
            found.append(Fact(key=key, value=value, source_turn_id=turn_id))
    return found


class SpeakerMemory:
    """Facts about one participant."""

    def __init__(self, identity: str, name: str) -> None:
        self.identity = identity
        self.name = name
        self._facts: dict[str, list[Fact]] = {}

    # ---- mutation ---------------------------------------------------------
    def add(self, fact: Fact) -> bool:
        """Store a fact. Returns True if it was new.

        Single-value keys (name, location, work) are overwritten by the latest
        statement - people correct themselves. Interests accumulate up to a cap.
        """
        values = self._facts.setdefault(fact.key, [])
        if any(v.value.lower() == fact.value.lower() for v in values):
            return False
        if fact.key in _SINGLE_VALUE_KEYS:
            self._facts[fact.key] = [fact]
        else:
            values.append(fact)
            if len(values) > _MAX_VALUES_PER_KEY:
                del values[0]
        return True

    def ingest(self, text: str, *, turn_id: str = "") -> list[Fact]:
        added = [f for f in extract_facts(text, turn_id=turn_id) if self.add(f)]
        if added:
            log.stage(
                TAG_MEMORY,
                event="facts_learned",
                speaker=self.identity,
                facts=";".join(f"{f.key}={f.value}" for f in added),
            )
        return added

    def forget(self) -> None:
        self._facts.clear()

    # ---- access -----------------------------------------------------------
    def get(self, key: str) -> list[str]:
        return [f.value for f in self._facts.get(key, [])]

    @property
    def known_name(self) -> str | None:
        vals = self.get(KEY_NAME)
        return vals[0] if vals else None

    @property
    def is_empty(self) -> bool:
        return not self._facts

    def as_dict(self) -> dict[str, list[str]]:
        return {k: [f.value for f in v] for k, v in self._facts.items() if v}

    def render(self) -> str:
        """Compact prompt block. Empty string when nothing is known."""
        if self.is_empty:
            return ""
        lines: list[str] = []
        label_order = (KEY_NAME, KEY_LOCATION, KEY_WORK, KEY_INTEREST, KEY_NOTE)
        labels = {
            KEY_NAME: "naam",
            KEY_LOCATION: "jagah",
            KEY_WORK: "kaam",
            KEY_INTEREST: "pasand",
            KEY_NOTE: "note",
        }
        for key in label_order:
            values = self.get(key)
            if values:
                lines.append(f"- {labels[key]}: {', '.join(values)}")
        return "\n".join(lines)


class MemoryStore:
    """All speakers' memories.

    The only way to read a speaker's facts is by their identity, which is what
    structurally prevents cross-speaker leakage.
    """

    def __init__(self) -> None:
        self._by_identity: dict[str, SpeakerMemory] = {}

    def for_speaker(self, identity: str, name: str = "") -> SpeakerMemory:
        mem = self._by_identity.get(identity)
        if mem is None:
            mem = SpeakerMemory(identity=identity, name=name or identity)
            self._by_identity[identity] = mem
        elif name and mem.name != name:
            mem.name = name
        return mem

    def ingest(self, identity: str, name: str, text: str, *, turn_id: str = "") -> list[Fact]:
        return self.for_speaker(identity, name).ingest(text, turn_id=turn_id)

    def render_for(self, identity: str) -> str:
        """Facts for exactly one speaker - never a merged view."""
        mem = self._by_identity.get(identity)
        return mem.render() if mem else ""

    def render_roster(self, exclude: str | None = None) -> str:
        """Who else is in the room, names only.

        Deliberately excludes other speakers' *facts*: the bots may know that
        Priya is present without being able to recite her private details into
        an answer meant for Rahul.
        """
        names = [
            m.known_name or m.name
            for identity, m in self._by_identity.items()
            if identity != exclude
        ]
        return ", ".join(n for n in names if n)

    def forget(self, identity: str) -> None:
        if mem := self._by_identity.get(identity):
            mem.forget()
            log.stage(TAG_MEMORY, event="forgotten", speaker=identity)

    def clear(self) -> None:
        self._by_identity.clear()

    def snapshot(self) -> dict[str, dict[str, list[str]]]:
        return {i: m.as_dict() for i, m in self._by_identity.items() if not m.is_empty}
