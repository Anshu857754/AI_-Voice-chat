# Known Limitations

Honest accounting of what this prototype does not do, and why, so it is not
mistaken for a production claim it doesn't make.

## State and persistence

- **In-memory only, per process.** `RoomContextManager`, `MemoryStore` and
  `MetricsCollector` live inside the single `agent_worker` process. A worker
  restart loses the room's transcript, speaker memory, and metrics for that
  session. This was a deliberate MVP trade-off (see
  `docs/provider-decisions.md`) — the classes are already isolated behind
  narrow method surfaces specifically so a Redis- or Postgres-backed
  implementation can be substituted without touching the router, context
  assembly, or agent code.
- **Single room, single worker.** The orchestrator is built for one room per
  process. Running multiple simultaneous rooms means running multiple worker
  processes (one per room name), with no built-in fleet manager/scheduler.
- **No cross-session memory.** Speaker memory does not persist across a
  participant leaving and rejoining with a new identity (a new browser tab
  without the stored `sessionStorage` identity looks like a new person).
  Rejoining with the *same* identity (the frontend keeps one in
  `sessionStorage`) does restore continuity within a single worker's uptime.

## Routing

- The bot-name term list is fixed (`pipeline/language.py::_DOST_TERMS` /
  `_SATHI_TERMS`), including common STT mis-transcriptions we anticipated
  (`dosth`, `sathee`) — but an unanticipated nickname or a transcription
  error outside that list will not be recognized as an explicit address and
  falls through to the relevance/continuity/turn-taking rules instead.
- Persona/topic routing uses a small, intentionally narrow keyword set. Most
  everyday questions have no strong lean and are (correctly) handled by
  turn-taking rather than a topic guess — but a genuinely ambiguous or
  cross-domain question ("AI movies mein kaise use hoti hai?") will not
  reliably route to "the more relevant" bot; it alternates instead.
- The follow-up continuity window is time-based
  (`ROUTER_FOLLOWUP_WINDOW_S`, default 45s), not conversation-turn-based. A
  slow-paced discussion can have a follow-up "expire" out of the active
  thread and get re-routed by turn-taking to the *other* bot, changing who
  continues the topic.

## Speech understanding

- Language/intent detection (`pipeline/language.py`) is regex- and
  keyword-based by design (see `routing-strategy.md` for why: latency and
  testability). It does not do full NLU — sarcasm, indirect requests, or
  unusual phrasing outside the patterns tested in `test_language.py` may be
  misclassified as small talk (and get silence) or vice versa.
- Fact extraction for speaker memory (`context/memory.py::extract_facts`) is
  similarly pattern-based. It reliably catches the phrasings demonstrated in
  the spec's scenarios and tested in `test_memory.py`, but a self-introduction
  phrased very differently from those patterns may not be captured.

## Voice pipeline

- Barge-in latency is bounded by VAD/STT provider latency plus a 0.6s
  debounce window (`INTERRUPT_MIN_SPEECH_ms`, `InterruptionController`
  `debounce_s`) chosen to avoid false triggers from coughs/mic bumps; a very
  fast, clipped interruption could occasionally be absorbed by the debounce
  and only register on the next signal.
- TTS voice IDs must be supplied by the deployer
  (`DOST_VOICE_ID`/`SATHI_VOICE_ID`); no specific Indian voices are bundled
  or hardcoded, since ElevenLabs voice IDs are account-scoped. Without them,
  the room runs on `SilentTTSProvider` (silent audio + full chat transcript),
  which is correct for demoing the pipeline but is not the aural experience
  the spec describes.
- Diarization/speaker attribution relies on one STT stream **per LiveKit
  participant's own microphone track**, not on audio-based speaker
  diarization. This is actually more reliable for this use case (no
  cross-talk misattribution) but means a participant who is muted or whose
  track fails to publish cannot be heard by the pipeline at all, even if
  physically speaking.

## LLM behavior

- Persona adherence (concise, Hinglish, non-textbook tone) is enforced via
  prompt instructions, not a hard constraint — an unusually contrarian model
  choice via `OPENROUTER_MODEL` could occasionally drift from the intended
  tone. The prompts were validated against `google/gemini-2.5-flash`
  (the default) in real (non-mocked) calls during development.
- The rolling summarizer's LLM-based compression (`ConversationSummarizer`)
  can lose nuance from older turns even when it succeeds (any summary does);
  its extractive fallback on failure is intentionally blunter still (verbatim
  question list, no synthesis).

## Testing scope

- The automated test suite (108 tests, see `backend/tests/`) covers routing,
  context assembly, memory isolation, language detection, interruption
  concurrency, and LLM failure modes — all offline, without real provider
  credentials. It does **not** include end-to-end tests against real
  LiveKit/Deepgram/ElevenLabs infrastructure, real audio I/O, or browser
  automation of the frontend; those require live credentials and are
  exercised manually (see README "Demo scenarios").

## Frontend

- No automated frontend test suite (unit or e2e) is included; correctness
  there was verified by a production build (`npm run build`) succeeding and
  by manual API-contract review against the backend's data-channel/topic
  payloads.
- The optional debug panel shows only the *latest* `roxstar.state` snapshot,
  not a historical timeline in the UI itself (the ring-buffer history exists
  server-side and in logs, but is not separately visualized client-side).
