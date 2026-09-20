"""Guards that keep the AI's own voice (and mic noise) from becoming a "user message".

With speakers and an open mic, the AI's speech leaks back into the user's
microphone; speech-to-text then hears gibberish ("Debo no de ser la sed.",
language=es, confidence 0.6), treats it as the user talking, interrupts the AI
and replies to it - the AI ends up talking to itself.

Two independent guards, applied to VOICE input only (typed messages are never
filtered):

* half-duplex: while an AI is speaking, and for ``tail_s`` afterwards, spoken
  input is ignored - it is almost certainly the AI's own audio.
* low-quality filter: transcripts below ``min_confidence``, in a language the
  room does not use, or with no letters at all are dropped.
"""

from __future__ import annotations

import re

_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def voice_ignore_reason(
    *,
    text: str,
    confidence: float,
    language: str | None,
    ai_speaking: bool,
    since_ai_speech_s: float | None,
    min_confidence: float,
    allowed_languages: frozenset[str],
    tail_s: float,
    names_an_ai: bool = False,
) -> str | None:
    """Why this spoken transcript must be ignored, or ``None`` to accept it."""
    if ai_speaking:
        return "ai_speaking"
    if since_ai_speech_s is not None and since_ai_speech_s < tail_s:
        return "ai_speech_tail"
    if not _HAS_LETTER.search(text or ""):
        return "no_words"
    if names_an_ai:
        # "Sathi," / "Dost." often arrives as a short, low-confidence fragment
        # before the rest of the sentence; dropping it would lose who was asked.
        return None
    if confidence < min_confidence:
        return f"low_confidence({confidence:.2f})"
    if language and allowed_languages and language.split("-")[0].lower() not in allowed_languages:
        return f"language({language})"
    return None


def parse_languages(csv: str) -> frozenset[str]:
    return frozenset(x.strip().lower() for x in csv.split(",") if x.strip())
