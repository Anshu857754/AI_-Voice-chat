"""Barge-in detection and cancellation.

The requirement: while a bot is speaking, a human saying "Ruko, simple example
se samjhao" must stop the bot's audio as fast as possible, and then the new
request must be processed.

How it is detected
------------------
Three independent signals, cheapest first, because each earlier one fires
sooner than the next:

1. **VAD speech start** (~100-300 ms) - Silero VAD on the human's track says
   someone started talking while a bot held the floor. Fastest, but noisy, so
   it is gated on a minimum speech duration to ignore coughs and mic bumps.
2. **Interim transcript** (~300-800 ms) - Deepgram partials. Any real words
   while a bot speaks is a genuine interruption, and an explicit cue
   ("ruko", "stop", "ek second") makes it unambiguous.
3. **Final transcript** - the backstop. By this point the audio is definitely
   stale and must be cut.

What cancellation actually does
------------------------------
``InterruptionController.trigger`` calls into the :class:`TurnManager`, which
cancels the active turn's token. The bot's speak loop observes the token
between frames, stops pushing audio, clears the LiveKit audio source queue
(so already-buffered frames are dropped rather than played out), and closes the
TTS stream. The partial text that was actually spoken is committed to context
as an interrupted turn, so the room's history stays truthful.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from app.models.participant import BotId
from app.utils.logging import TAG_INTERRUPT, get_logger

log = get_logger(__name__)


class InterruptSignal(str, Enum):
    VAD_SPEECH_START = "vad_speech_start"
    INTERIM_TRANSCRIPT = "interim_transcript"
    FINAL_TRANSCRIPT = "final_transcript"
    EXPLICIT_CUE = "explicit_cue"
    MANUAL = "manual"


@dataclass
class InterruptionEvent:
    signal: InterruptSignal
    speaker_identity: str
    interrupted_bot: BotId | None
    text: str = ""
    at: float = field(default_factory=time.monotonic)
    tts_cancelled: bool = False


# Called to actually cancel the floor. Returns True if something was cancelled.
CancelHook = Callable[[str], Awaitable[bool]]


class InterruptionController:
    """Decides whether a human utterance counts as a barge-in, and cancels.

    Deliberately conservative: a bot being cut off by accident is worse than a
    slightly late cancellation, so short noises and a speaker's own echo are
    filtered out. Debouncing prevents the three detection signals from firing
    three separate cancellations for one interruption.
    """

    def __init__(
        self,
        *,
        cancel_hook: CancelHook,
        min_speech_ms: int = 200,
        debounce_s: float = 0.6,
    ) -> None:
        self._cancel = cancel_hook
        self._min_speech_ms = min_speech_ms
        self._debounce_s = debounce_s
        self._last_trigger_at: float = 0.0
        self._lock = asyncio.Lock()
        self.events: list[InterruptionEvent] = []
        self._counter = 0

    @property
    def interruption_count(self) -> int:
        return self._counter

    # ---- detection --------------------------------------------------------
    def should_interrupt_on_vad(
        self, *, speaking_bot: BotId | None, speech_duration_s: float
    ) -> bool:
        """Fastest path: sustained human speech while a bot holds the floor."""
        if speaking_bot is None:
            return False
        return (speech_duration_s * 1000.0) >= self._min_speech_ms

    def should_interrupt_on_text(
        self, *, speaking_bot: BotId | None, text: str, is_final: bool
    ) -> bool:
        """Words arrived while a bot was speaking.

        An interim transcript needs at least two words (single-word partials
        are frequently STT noise); a final transcript always counts.
        """
        if speaking_bot is None:
            return False
        stripped = text.strip()
        if not stripped:
            return False
        if is_final:
            return True
        return len(stripped.split()) >= 2

    # ---- action -----------------------------------------------------------
    async def trigger(
        self,
        *,
        signal: InterruptSignal,
        speaker_identity: str,
        speaking_bot: BotId | None,
        text: str = "",
    ) -> InterruptionEvent | None:
        """Cancel the active bot turn. Debounced; returns None if suppressed."""
        async with self._lock:
            now = time.monotonic()
            if (now - self._last_trigger_at) < self._debounce_s:
                log.debug(
                    tag=TAG_INTERRUPT, event="debounced", signal=signal.value,
                    speaker=speaker_identity,
                )
                return None
            self._last_trigger_at = now

            cancelled = await self._cancel(signal.value)
            self._counter += 1
            event = InterruptionEvent(
                signal=signal,
                speaker_identity=speaker_identity,
                interrupted_bot=speaking_bot,
                text=text,
                tts_cancelled=cancelled,
            )
            self.events.append(event)
            del self.events[:-50]  # bounded history for the debug panel
            log.stage(
                TAG_INTERRUPT,
                speaker=speaker_identity,
                bot=speaking_bot.value if speaking_bot else None,
                signal=signal.value,
                tts_cancelled=cancelled,
                text=text[:60] or None,
            )
            return event

    def snapshot(self) -> dict[str, object]:
        return {
            "count": self._counter,
            "recent": [
                {
                    "signal": e.signal.value,
                    "speaker": e.speaker_identity,
                    "bot": e.interrupted_bot.value if e.interrupted_bot else None,
                    "tts_cancelled": e.tts_cancelled,
                    "text": e.text[:80],
                }
                for e in self.events[-10:]
            ],
        }
