"""Deterministic conversation titles.

A new conversation is "New Chat". After the first *meaningful* user message it
gets a short (3-7 word) title derived from that message; greetings ("hi") only
move it to "New Conversation". The worker may later refine the title with a
tiny LLM call, but this function alone is always enough (no LLM needed, no cost).
"""

from __future__ import annotations

import re

DEFAULT_TITLE = "New Chat"
FALLBACK_TITLE = "New Conversation"
UNTITLED = frozenset({DEFAULT_TITLE, FALLBACK_TITLE})
MAX_WORDS = 7
MAX_CHARS = 48

_GREETING = re.compile(
    r"^(?:(?:hey+|hi+|hello+|hlo|namaste|namaskar|yo|ok(?:ay)?|so|please|pls|bhai|yaar|arre?)\b[\s,!.:-]*)+",
    re.I,
)
_ADDRESS = re.compile(r"^(?:ai\s+)?(?:dost|sathi)\b[\s,:-]*", re.I)
_ASK_LEAD = re.compile(
    r"^(?:(?:can|could|will|would) you |i (?:want|need|wanna) (?:you )?(?:to )?|please |help me (?:to )?|"
    r"tell me (?:about )?|explain (?:me )?|what(?:'s| is| are) |how (?:to|do i|can i) |mujhe |mujhko |"
    r"ek |thoda |zara |kya tum |kya aap |(?:tum|aap) (?:batao|bata do|bataiye|bolo) )+",
    re.I,
)
_WHAT_IS = re.compile(r"^(.{1,40}?)\s+(?:kya (?:hota )?hai|kya hota hai)\s*[?.!]*$", re.I)
_TAIL = re.compile(
    r"\s+(?:samjhao|samjha do|batao|bata do|bataiye|batana|explain karo|kaise kaam karta hai|na|yaar)\s*[?.!]*$",
    re.I,
)
_FILLER_WORDS = frozenset({"simple", "simply", "please", "pls", "basically", "just"})
_SMALL_WORDS = frozenset(
    {"a", "an", "the", "and", "or", "of", "in", "on", "to", "for", "with", "vs", "ka", "ki", "ke", "mein", "me", "se", "ko", "my"}
)
_NON_CONTENT = frozenset(
    {
        "kaise", "ho", "hai", "hain", "kya", "aap", "tum", "theek", "thik", "accha", "acha", "nahi", "haan",
        "ha", "ok", "okay", "cool", "nice", "thanks", "thank", "bye", "bas", "kuch", "aur", "or", "btao",
        "bta", "batao", "kaisa", "kaisi", "hii", "hiii", "sun", "suno", "bol", "bolo", "kar", "kro", "karo",
        "how", "are", "you", "who", "what", "why", "yes", "no", "hmm", "haha", "lol", "test", "testing",
    }
)


def _title_case(word: str) -> str:
    if word.isupper() or any(c.isupper() for c in word[1:]):
        return word  # acronyms / CamelCase: AI, React, LiveKit
    return word[:1].upper() + word[1:]


def derive_title(text: str) -> str | None:
    """A 3-7 word title for a first user message, or None if it says nothing."""
    s = " ".join((text or "").split())
    if not s:
        return None
    # First clause only: titles are not summaries.
    s = re.split(r"(?<=[.?!;])\s|\n", s, maxsplit=1)[0]
    s = _GREETING.sub("", s)
    s = _ADDRESS.sub("", s)
    s = _GREETING.sub("", s)
    if m := _WHAT_IS.match(s):
        subject = _ASK_LEAD.sub("", m.group(1)).strip(" ,.?!")
        words = [w for w in subject.split() if w]
        if 1 <= len(words) <= 4 and any(w.lower() not in _NON_CONTENT for w in words):
            return _shape(["what", "is", *words])
    s = _ASK_LEAD.sub("", s)
    s = _TAIL.sub("", s).strip(" ,.?!:;-")
    words = [w.strip(",.?!:;") for w in s.split()]
    words = [w for w in words if w and w.lower() not in _FILLER_WORDS]
    while words and words[-1].lower() in _SMALL_WORDS:
        words.pop()  # no dangling "mein" / "in" after a removed filler
    content = [w for w in words if w.lower() not in _NON_CONTENT and w.lower() not in _SMALL_WORDS]
    if not content:
        return None
    if len(content) < 2 and len(content[0]) < 5:
        return None
    return _shape(words)


def _shape(words: list[str]) -> str:
    words = words[:MAX_WORDS]
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        out.append(lw if (lw in _SMALL_WORDS and i > 0) else _title_case(w))
    title = " ".join(out)
    if len(title) > MAX_CHARS:
        title = title[:MAX_CHARS].rsplit(" ", 1)[0]
    return title or None
