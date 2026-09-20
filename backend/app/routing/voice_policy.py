"""Response-mode policy: does THIS reply go out as text only, or text + voice?

Two independent things are tracked (never conflated):

    inputMode     how the user sent the message        (text | voice)
    responseMode  how the AI should answer             (text | voice)

Text is ALWAYS produced; ``voice`` only adds audio on top. The decision comes
only from the USER - never from the AI's own reply, the room name, or AI<->AI:

    1. an explicit instruction in the current message      (highest)
    2. the conversation's response preference set earlier  (text | voice)
    3. the manual Voice button (voice mode)                 (auto preference)
    4. otherwise TEXT

The conversation preference has three values:

    auto   default. Text, unless the Voice button is on or the message asks.
    text   the user said "only text / don't speak": text even in voice mode.
    voice  the user said "talk to me in voice": every reply is spoken.

Intent detection is deliberately narrow. Mentioning the word "voice" ("What is
voice AI?", "Why isn't voice working?", "How does TTS work?") is NOT a request.
When in doubt: text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ResponseMode = Literal["text", "voice"]
ResponsePref = Literal["auto", "text", "voice"]
PREFS: tuple[str, ...] = ("auto", "text", "voice")

# --- explicit "answer aloud" requests -----------------------------------------
# Sticky: changes the conversation preference (the user is describing how to talk).
_VOICE_STICKY = re.compile(
    r"""\b(?:
        talk\s+to\s+me
      | speak\s+to\s+me
      | let'?s\s+(?:talk|speak)
      | from\s+now\s+(?:on\s+)?(?:speak|talk|reply|answer|respond)
      | mujhse\s+(?:voice\s+(?:me|mein|main)\s+)?baat\s+(?:karo|kar|kr|kariye)
      | baat\s+(?:karo|kar|kr)\s+voice\s+(?:me|mein|main)
      | voice\s+(?:me|mein|main)\s+baat
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
# One-shot: this reply only.
_VOICE_ONCE = re.compile(
    r"""\b(?:
        (?:answer|respond|reply)\s+(?:with|in|by)\s+voice
      | tell\s+me\s+verbally
      | speak\s+(?:this|it|out|aloud|your\s+answer|the\s+answer)
      | read\s+(?:it|this)\s+(?:out|aloud)
      | out\s+loud | aloud
      | voice\s+(?:me|mein|main|se)\s+(?:bata|bol|sun|jawab|reply|answer|samjha)\w*
      | (?:awaaz|avaaz|aawaz)\s+(?:me|mein|main)
      | bol\s*(?:ke|kar)\s*(?:bata|samjha|sun|jawab|do|dena|dijiye)?\w*
      | bolkar\s+\w+
      | mujhe\s+sunao | sunao\s+mujhe
      | (?:english|hindi|hinglish)\s+(?:me|mein|main)\s+bol(?:o|na|kar|ke)?
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
# "say it" only as an instruction (end of message), not "how do I say it politely".
_VOICE_SAY_IT = re.compile(r"(?:^|[,.!?]\s*)(?:please\s+|can\s+you\s+|could\s+you\s+)?say\s+(?:it|this)\s*[.!?]*$", re.IGNORECASE)

# --- explicit "text only" requests --------------------------------------------
_TEXT_STICKY = re.compile(
    r"""\b(?:
        (?:only|just)\s+(?:text|type|typing|chat)
      | text\s+only
      | (?:sirf|bas|only)\s+text
      | (?:now|ab|abhi)\s+(?:only\s+)?text
      | don'?t\s+speak | do\s+not\s+speak | stop\s+speaking
      | no\s+voice | without\s+voice
      | voice\s+(?:mat|band|nahi\s+chahiye)
      | mat\s+bol(?:o|na)? | bolna\s+band | bolo\s+mat
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
_TEXT_ONCE = re.compile(
    r"""\b(?:
        (?:reply|answer|respond)\s+(?:in|with|by)\s+text
      | text\s+(?:me|mein|main)\s+(?:bata|batao|samjha|jawab|reply|likh)\w*
      | likh\s*(?:ke|kar)\s*\w*
      | likhkar\s+\w+
      | chat\s+(?:me|mein|main)\s+(?:reply|jawab|bata|batao)\w*
      | type\s+kar(?:ke)?\s+\w+
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class OutputIntent:
    """What the current message explicitly asks for (None = nothing asked)."""

    mode: ResponseMode | None = None
    sticky: bool = False  # applies to the rest of the conversation, not just this reply
    matched: str = ""


def detect_output_intent(text: str) -> OutputIntent:
    """Explicit response-mode instruction in ``text``. Text instructions win over voice."""
    if not text or not text.strip():
        return OutputIntent()
    if m := _TEXT_STICKY.search(text):
        return OutputIntent("text", True, m.group(0))
    if m := _TEXT_ONCE.search(text):
        return OutputIntent("text", False, m.group(0))
    if m := _VOICE_STICKY.search(text):
        return OutputIntent("voice", True, m.group(0))
    if m := _VOICE_ONCE.search(text):
        return OutputIntent("voice", False, m.group(0))
    if m := _VOICE_SAY_IT.search(text.strip()):
        return OutputIntent("voice", False, m.group(0).strip())
    return OutputIntent()


@dataclass(frozen=True)
class ResponseDecision:
    mode: ResponseMode
    reason: str
    new_pref: ResponsePref | None = None  # set when this message changes the preference
    intent: OutputIntent = OutputIntent()


def resolve_response(*, text: str, voice_mode: bool, pref: ResponsePref = "auto") -> ResponseDecision:
    """The single decision: text or voice for the reply to ``text``."""
    intent = detect_output_intent(text)
    if intent.mode is not None:
        new_pref: ResponsePref | None = intent.mode if intent.sticky else None
        return ResponseDecision(intent.mode, f"intent:{intent.matched.lower()}", new_pref, intent)
    if pref == "text":
        return ResponseDecision("text", "pref:text")
    if pref == "voice":
        return ResponseDecision("voice", "pref:voice")
    if voice_mode:
        return ResponseDecision("voice", "voice_mode")
    return ResponseDecision("text", "default")


# --- compatibility helpers (older call sites / tests) --------------------------
def has_explicit_voice_intent(text: str) -> bool:
    """True if the USER's message explicitly asks for a spoken answer."""
    return detect_output_intent(text).mode == "voice"


def should_speak(*, voice_mode: bool, user_text: str = "", pref: ResponsePref = "auto") -> bool:
    """Single source of truth for "should this reply be spoken?"."""
    return resolve_response(text=user_text, voice_mode=voice_mode, pref=pref).mode == "voice"
