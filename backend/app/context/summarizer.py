"""Rolling conversation summarizer.

Long rooms cannot ship their whole transcript to the LLM on every turn: cost
and latency grow without bound and models degrade with very long contexts. So
the context manager keeps a short recent window verbatim and compresses
everything older into one rolling summary, which this module produces.

Two properties matter for a realtime room:

* **Off the critical path.** Summarization is triggered in a background task.
  A turn never waits for it; if it has not finished, the previous summary is
  used.
* **Never fatal.** If the LLM call fails, an extractive fallback keeps a usable
  (if blunter) summary rather than losing the older history entirely.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.models.conversation import Turn
from app.pipeline.llm import LLMError, LLMMessage, LLMProvider
from app.utils.logging import TAG_CONTEXT, get_logger

log = get_logger(__name__)

_SUMMARY_SYSTEM = """You compress a multi-person voice-room conversation into notes.

Rules:
- Write in simple Hinglish (Roman script), the way the room actually talks.
- Keep it under 120 words.
- Keep: topics discussed, who asked what, decisions, and any personal details
  people shared (attributed to the right person by name).
- Drop: greetings, filler, exact wording, politeness.
- Never invent anything that is not in the transcript.
- Output only the notes. No preamble, no headings."""


_TOPIC_SYSTEM = """Tum ek chhota note likhte ho: abhi is baat-cheet mein kya chal raha hai.

Rules:
- Sirf ek line, maximum 12 words, simple Hinglish (Roman script).
- Sirf wahi jo transcript mein hai. Kuch invent mat karo.
- Greeting/small talk ho to bhi bata do (jaise "Anshu se hello aur haal-chaal").
- Sirf line output karo. Koi preamble nahi."""


class ConversationSummarizer:
    """Compresses older turns into a rolling summary."""

    def __init__(
        self,
        llm: LLMProvider,
        *,
        max_chars: int = 1200,
        timeout_s: float = 15.0,
    ) -> None:
        self._llm = llm
        self._max_chars = max_chars
        self._timeout_s = timeout_s
        self._lock = asyncio.Lock()

    async def topic(self, turns: Sequence[Turn], previous: str = "") -> str:
        """One short line on what the conversation is about right now.

        Returns "" on any failure: the caller keeps the previous topic.
        """
        if not turns:
            return previous
        transcript = "\n".join(t.as_transcript_line() for t in turns)
        prompt = (f"Pichla note: {previous}\n\n" if previous else "") + transcript
        try:
            text = await asyncio.wait_for(
                self._llm.generate(
                    [
                        LLMMessage(role="system", content=_TOPIC_SYSTEM),
                        LLMMessage(role="user", content=prompt),
                    ],
                    max_tokens=40,
                    temperature=0.2,
                ),
                timeout=self._timeout_s,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(tag=TAG_CONTEXT, event="topic_failed", error=repr(exc))
            return ""
        return _clean_topic(text)

    async def summarize(self, turns: Sequence[Turn], previous: str = "") -> str:
        """Fold ``turns`` into ``previous`` and return the updated summary."""
        if not turns:
            return previous
        # One summarization at a time; overlapping folds would race and could
        # drop history.
        async with self._lock:
            transcript = "\n".join(t.as_transcript_line() for t in turns)
            prompt = transcript if not previous else (
                f"Existing notes:\n{previous}\n\nNew transcript to fold in:\n{transcript}"
            )
            try:
                summary = await asyncio.wait_for(
                    self._llm.generate(
                        [
                            LLMMessage(role="system", content=_SUMMARY_SYSTEM),
                            LLMMessage(role="user", content=prompt),
                        ],
                        max_tokens=260,
                        temperature=0.2,
                    ),
                    timeout=self._timeout_s,
                )
            except (TimeoutError, LLMError, Exception) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                log.warning(
                    tag=TAG_CONTEXT,
                    event="summary_failed",
                    error=repr(exc),
                    detail="using extractive fallback",
                )
                summary = extractive_summary(turns, previous, max_chars=self._max_chars)
            summary = summary.strip()[: self._max_chars]
            log.stage(
                TAG_CONTEXT,
                event="summary_updated",
                folded_turns=len(turns),
                summary_chars=len(summary),
            )
            return summary


def _clean_topic(text: str) -> str:
    return " ".join(text.replace("*", "").replace('"', "").split())[:120]


def extractive_summary(
    turns: Sequence[Turn], previous: str = "", *, max_chars: int = 1200
) -> str:
    """LLM-free fallback: keep the human questions, drop everything else.

    Crude but truthful - it only ever contains text that was actually said.
    """
    lines: list[str] = []
    if previous:
        lines.append(previous.strip())
    for turn in turns:
        if not turn.is_human:
            continue
        text = " ".join(turn.text.split())
        if len(text) < 8:
            continue
        lines.append(f"{turn.speaker_name} ne poocha: {text}")
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    # Keep the newest material, which is the most relevant.
    return joined[-max_chars:].split("\n", 1)[-1]
