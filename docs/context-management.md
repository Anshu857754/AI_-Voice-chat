# Context Management Strategy

Implementation: [`backend/app/context/manager.py`](../backend/app/context/manager.py)
(`RoomContextManager`), [`memory.py`](../backend/app/context/memory.py)
(`MemoryStore`), [`summarizer.py`](../backend/app/context/summarizer.py)
(`ConversationSummarizer`). Tests:
[`test_context.py`](../backend/tests/test_context.py),
[`test_memory.py`](../backend/tests/test_memory.py).

## Why not "send the whole transcript"

Sending unbounded history to the LLM on every turn grows cost and latency
without bound, and long, noisy contexts measurably degrade generation
quality. `RoomContextManager` instead assembles a **layered** context for
every single reply:

```
┌─────────────────────────────────────────────┐
│ 1. Persona system prompt (Dost or Sathi)      │
│ 2. Reply-style instruction for THIS turn      │
│    (language, brevity, why-this-bot nudge)    │
│ 3. Room state block:                          │
│    - who else is in the room (names only)     │
│    - rolling summary of older conversation    │
│    - the room's current active topic          │
│ 4. Speaker-specific facts for the ASKER ONLY  │
│ 5. Recent verbatim turns (bounded window)      │
└─────────────────────────────────────────────┘
```

This is assembled fresh in `RoomContextManager.build_messages()` for every
bot turn and logged (`[CONTEXT] messages_used=... recent_turns=... summary=...
speaker_facts=...`) so the exact shape sent to the LLM is always visible in
the logs — nothing about what was "used" is guessed after the fact.

## Layer 1-2: Persona + per-turn style

Static persona text (`agents/prompts.py`) plus a *dynamic* instruction picked
per turn from the detected language (Hindi/Hinglish/English/explicit
request) and from *why* the router picked this bot (e.g. "this is a
follow-up to your own last answer — don't restart from scratch, go simpler").

## Layer 3: Room state

- **Roster**: names of humans currently in the room (from `MemoryStore`, so a
  bot can say "room mein Priya bhi hai" without knowing Priya's private facts).
- **Rolling summary**: see below.
- **Active topic**: the most recent substantive (≥3-word) *new* question,
  deliberately **not** overwritten by follow-up turns. This is what lets
  "Unki koi famous movie batao" resolve "Unki" back to "Shah Rukh Khan" two
  turns later — the topic string is injected verbatim with an explicit
  pronoun-resolution instruction.

## Layer 4: Speaker-specific memory (strict isolation)

`MemoryStore` is keyed by **LiveKit participant identity**, not display name
(names can collide; identity cannot). Rule-based extraction
(`context/memory.py::extract_facts`) runs synchronously on every human turn —
free, deterministic, and unit-tested against Hindi/Hinglish/English
self-disclosure phrasing ("mera naam X hai", "mujhe X pasand hai", "I'm from
X", "main X se hoon").

`RoomContextManager.build_messages()` calls
`memory.render_for(asking_identity)` — **only** the asking speaker's own
facts are ever injected, under an explicit instruction ("ye sirf isi
speaker ki information hai, kisi doosre participant ki details yahan use mat
karo"). `test_memory.py::TestSpeakerIsolation` and
`test_context.py::TestPromptAssembly::test_only_asking_speakers_memory_is_included`
assert this directly: Priya's facts never appear in the block built for
Rahul's question, even though her own *spoken words* (a separate thing —
see next paragraph) legitimately remain visible in the shared transcript.

> **Isolation is about the *facts block*, not about hiding what people said.**
> The recent-turns window (layer 5) intentionally includes everything every
> human actually said aloud or typed — that is the multi-user shared-context
> requirement. What must never cross speaker boundaries is the *derived*
> "known facts about X" summary used for memory-recall questions like "what
> did I tell you about myself?".

Single-value facts (name, location, work) are **overwritten** on
correction ("Actually main Pune se hoon" replaces "Delhi"); multi-value
facts (interests) accumulate up to a small cap.

## Layer 5: Recent window + rolling summary

- `CONTEXT_RECENT_TURNS` (default 20) turns are sent **verbatim**, in role
  order (`user` for humans, `assistant` for this bot's own prior turns, and —
  importantly — `user` again, prefixed with the speaker's name, for the
  *other* bot's turns. Sathi's replies are context for Dost, never Dost's own
  "memory" of having said them himself; `test_context.py` asserts this
  distinction directly).
- Once the transcript grows past `CONTEXT_SUMMARY_TRIGGER` (default 30) turns
  beyond what's already been folded, everything older than the last
  `CONTEXT_SUMMARY_KEEP` (default 10) turns is compressed into one rolling
  summary by `ConversationSummarizer`, in the background (`asyncio.create_task`,
  never blocking the turn in flight).
- The summarizer asks the LLM for ≤120-word Hinglish notes retaining topics,
  who-asked-what, decisions and shared personal details; on any LLM failure
  it falls back to a pure-extractive summary (every human question, verbatim,
  truncated to the char budget) — summarization degrading is never allowed to
  silently drop history, only to drop *polish*.
- `CONTEXT_MAX_SUMMARY_CHARS` (default 1200) hard-caps the summary itself so
  it cannot grow into its own unbounded-context problem over a very long room.

## What is bounded, and by what

| Thing | Bound | Config |
|---|---|---|
| Verbatim recent turns sent to LLM | last N turns | `CONTEXT_RECENT_TURNS` |
| Full retained transcript (for UI/debug) | 400 turns (ring buffer) | hardcoded `_MAX_TURNS` |
| Rolling summary length | chars | `CONTEXT_MAX_SUMMARY_CHARS` |
| Per-speaker interest facts | count | 5 (`_MAX_VALUES_PER_KEY`) |
| Per-speaker single-value facts (name/location/work) | 1 (latest wins) | — |

## Text and voice share one context — literally

There is exactly one entry point, `RoomContextManager.add_human_turn()`,
called with `source=TurnSource.VOICE` from the STT final-transcript handler
and with `source=TurnSource.TEXT` from the chat handler
(`orchestrator.py::_handle_chat`). Both paths run through the identical
router, identical memory extraction, and identical prompt assembly — the
"never build a separate context system for text" requirement is enforced by
there being only one code path, not by convention.
