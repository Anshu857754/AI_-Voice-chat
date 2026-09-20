# Roxstar AI Voice Room

A LiveKit-based realtime room where two or more humans talk by voice and text,
and two independently-connected AI participants — **Roxstar AI Dost** (male,
Hindi/Hinglish) and **Roxstar AI Sathi** (female, Hindi/Hinglish) — listen,
decide when and who should respond, maintain shared multi-user context, and
support natural interruption.

This is a working prototype, not a mockup: both bots join LiveKit as real
participants with published audio tracks, the routing decision is made by
deterministic code before any LLM call, and every latency number shown
anywhere is a real measured timestamp.

---

## 1. Project overview

```
Human voice/text
  → LiveKit
  → speaker identification
  → STT / text normalization
  → language + intent understanding
  → Bot Router (selects ONE bot, before generation)
  → Room Context Manager (shared transcript + per-speaker memory)
  → selected AI bot
  → OpenRouter LLM (streamed)
  → TTS (streamed)
  → LiveKit audio publication
  → Human participants
```

Full diagrams: [`docs/architecture.md`](docs/architecture.md) ·
[`docs/sequence-diagram.md`](docs/sequence-diagram.md).

## 2. Features

- 2+ human participants, mic + room text chat, in one LiveKit room.
- AI Dost and AI Sathi as genuine LiveKit participants (own identity, own
  published audio track, own persona) — never faked in the UI.
- Centralized router: **exactly one** bot answers a given turn; irrelevant
  chatter gets silence; explicit "AI Dost, ... AI Sathi, ..." addressing runs
  the two bots strictly sequentially, never concurrently.
- Shared multi-user context: a follow-up from a *different* human continues
  the same bot's thread; pronouns resolve against the room's active topic.
- Speaker-specific memory, strictly isolated per LiveKit participant identity.
- Real barge-in: a bot's audio is cancelled within about one frame of a human
  starting to talk, backed by VAD + interim + final STT signals.
- Hindi / Hinglish / Roman Hindi / English understanding, replies in natural
  Hinglish (see `docs/routing-strategy.md`'s language handling).
- Graceful degradation: LLM/TTS/STT failures never crash the room or expose a
  stack trace to a user.
- Structured logs and real, measured latency stats, visible in an optional
  in-app debug panel.
- 108 automated tests, all offline (no real API keys used in CI).

## 3. Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full component
diagram and the reasoning behind two separate LiveKit connections sharing one
router/context/turn-manager. Short version: one Python process
(`app/agent_worker.py`) holds two `livekit.rtc.Room` connections (one per
bot) plus one shared `RoomContextManager`, `BotRouter`, `TurnManager` and
`MemoryStore`. A separate FastAPI process (`app/main.py`) only mints LiveKit
join tokens for browsers — it never touches conversation state.

## 4. Technology choices

| Layer | Choice | Why (full detail in [provider-decisions.md](docs/provider-decisions.md)) |
|---|---|---|
| Frontend | React 19 + Vite + Tailwind v4 + `livekit-client` | fast dev loop, official LiveKit JS SDK |
| Backend | Python 3.12, FastAPI, `livekit-agents`/`livekit-api` | official LiveKit Python SDKs, async-native |
| LLM | OpenRouter (`OPENROUTER_MODEL`, default `google/gemini-2.5-flash`) | one key, swap models via env var, OpenAI-compatible streaming |
| STT | Deepgram `nova-2`, `language=multi` | one stream handles Hindi+English code-switching, true streaming + interim results |
| TTS | ElevenLabs `eleven_turbo_v2_5` | natural Hindi pronunciation, distinct Indian male/female voices, streaming |
| Realtime | LiveKit Cloud or self-hosted `livekit-server` | audio tracks + data channels + text streams in one SDK |
| State | In-memory (per-process) | sufficient for the MVP; storage classes are isolated behind narrow interfaces for a later Redis/Postgres upgrade |

## 5. Prerequisites

- Python 3.10–3.13 (this repo was built and tested on 3.12; `livekit-agents`
  does not yet support 3.14)
- Node.js 20+ and npm
- A LiveKit server: [LiveKit Cloud](https://cloud.livekit.io) (free tier
  works) or a local `livekit-server --dev`
- An [OpenRouter](https://openrouter.ai) API key
- A [Deepgram](https://deepgram.com) API key (optional — voice input is
  disabled without it, text chat still works)
- An [ElevenLabs](https://elevenlabs.io) API key + two voice IDs (optional —
  falls back to silent audio + full chat transcript without it)

## 6. Environment variables

Copy the template and fill in real values — **never commit `.env`**:

```bash
cp .env.example .env
```

All variables are documented inline in [`.env.example`](.env.example).
Summary of the required ones for full functionality:

```
LIVEKIT_URL=                 # wss://your-project.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

OPENROUTER_API_KEY=
OPENROUTER_MODEL=google/gemini-2.5-flash

STT_API_KEY=                 # Deepgram
TTS_API_KEY=                 # ElevenLabs
DOST_VOICE_ID=                # male Indian Hindi voice from your ElevenLabs library
SATHI_VOICE_ID=                # female Indian Hindi voice from your ElevenLabs library
```

The frontend additionally reads `frontend/.env` (its own template at
`frontend/.env.example`):

```
VITE_TOKEN_ENDPOINT=http://localhost:8000
VITE_DEFAULT_ROOM=roxstar-room
```

On startup the agent worker validates `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`, `OPENROUTER_API_KEY` and the Deepgram key
(`STT_API_KEY`, or `DEEPGRAM_API_KEY` as an alias), plus the ElevenLabs
key/voice ids, and **refuses to start with a clear error listing what is
missing**. The token server only needs the `LIVEKIT_*` variables. Run
`python -m app.agent_worker --check` for a full report. Runtime provider
failures (timeouts, rate limits) never crash the worker — see
`docs/failure-handling.md`.

> `VITE_TOKEN_ENDPOINT` must point at the token server's `API_PORT`
> (`frontend/.env` is the file Vite reads, not the root `.env`). If you change
> `API_PORT`, change it in `frontend/.env` too and restart `npm run dev`.

## 7. Local setup

```bash
git clone <this-repo>
cd roxstar-ai-voice-room
cp .env.example .env            # fill in real values
cd frontend && cp .env.example .env && cd ..
```

### Backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### Frontend

```bash
cd frontend
npm install
```

## 8. Running the frontend

```bash
cd frontend
npm run dev
# open http://localhost:5173
```

## 9. Running the backend (token server)

```bash
cd backend
python -m app.main
# or: uvicorn app.main:app --reload --port 8000
```

Verify: `curl http://localhost:8000/health` — reports which providers are
actually configured (never claims a feature works without checking).

## 10. Running the AI agents

```bash
cd backend
python -m app.agent_worker --check   # configuration report, no connection
python -m app.agent_worker           # joins the room as AI Dost + AI Sathi
```

Run this **alongside** the token server (`app/main.py`) — they are
independent processes so an API restart never disconnects the bots.

The bots join the room named by `ROOM_NAME` and the token server always issues
tokens for that same room, so humans and AI always meet. There is no
`agent_name` / dispatch involved: the worker connects both bots itself, so no
explicit dispatch is needed.

**Run exactly one worker.** A second worker with the same identities makes
LiveKit kick the first with `DuplicateIdentity`; the kicked worker logs
`duplicate_identity` and exits instead of fighting for the room.

### Quick start (three terminals)

```bash
# 1. token server        -> http://localhost:8000 (or your API_PORT)
cd backend && python -m app.main
# 2. AI agent worker     -> look for  worker_registered  and  room_ready  in the log
cd backend && python -m app.agent_worker
# 3. frontend            -> http://localhost:5173
cd frontend && npm run dev
```

Windows (PowerShell) example with the repo's venv:
`..\.venv\Scripts\python.exe -m app.agent_worker`.

### Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| "AI Dost and AI Sathi have not joined yet" | Worker not running, or a second worker was kicked/kicking (`duplicate_identity` in logs) — stop all `agent_worker` processes, start one. Also check `VITE_TOKEN_ENDPOINT` matches `API_PORT`. |
| Chat/transcript work but you hear nothing | Click anywhere on the page (browser autoplay policy) and check the tab isn't muted. |
| Worker exits immediately | Read the `cannot_start` line: it lists the missing env vars. |

## 11. Testing

```bash
cd backend
python -m pytest tests/ -v
python -m ruff check app/ tests/
```

108 tests, fully offline (no real API keys — the OpenRouter tests use
`httpx.MockTransport`, everything else uses the Scripted/Failing test
doubles defined next to each real provider). Covers: explicit/implicit
routing for both bots, no-duplicate-response, continuation/follow-up
routing, irrelevant-sentence suppression, speaker-memory isolation, Hindi/
Hinglish/Roman-Hindi/English detection, interruption concurrency (including
a real deadlock the tests caught and that was fixed — see
`docs/failure-handling.md`), and LLM timeout/API-error/missing-key/
malformed-response handling.

## 12. Demo scenarios

All seven scenarios from the spec are asserted directly in
`backend/tests/test_routing.py` and reproduced live end-to-end once
credentials are configured:

1. **"AI kya hota hai?"** → one bot (turn-taking) answers in concise Hinglish.
2. **"Can you explain cloud computing?"** → answered in Hinglish (English
   question, Hinglish room voice) unless English was explicitly requested.
3. **"Shah Rukh Khan ke baare mein batao." → "Unki koi famous movie batao."**
   → second question resolves "Unki" against the first via the active-topic
   mechanism in `RoomContextManager`.
4. **Rahul asks, bot answers. Priya: "Thoda aur simple batao."** → routed to
   the *same* bot that answered Rahul (`conversation_continuity`), not
   re-routed just because a different human spoke.
5. **"Mera naam Rahul hai aur mujhe cricket pasand hai." → later "Maine apne
   baare mein kya bataya tha?"** → answered from Rahul's own memory only.
6. **Bot mid-sentence, human: "Ruko, simple example se samjhao."** → audio
   cancelled immediately, same bot answers the new request.
7. **"AI Dost, tum answer karo. AI Sathi, baad mein ek example dena."** →
   Dost answers first, Sathi speaks strictly after — no overlap.

## 13. Routing strategy

[`docs/routing-strategy.md`](docs/routing-strategy.md) — deterministic,
priority-ordered rules (explicit address → direct follow-up/interrupt
redirect → conversation continuity → memory query → persona/topic affinity →
self-disclosure ack → turn-taking fallback), selected **before** any LLM
call, with structural guards against duplicate/self-echo responses.

## 14. Context strategy

[`docs/context-management.md`](docs/context-management.md) — layered prompt
assembly (persona → per-turn style → room state/summary/topic → asking
speaker's own facts only → bounded recent-turn window), rolling summarization
of older history, and why voice and text share exactly one context (one
entry point, `RoomContextManager.add_human_turn`).

## 15. Interruption handling

[`docs/sequence-diagram.md`](docs/sequence-diagram.md) §2 and
[`docs/failure-handling.md`](docs/failure-handling.md) — three debounced
detection signals (VAD start, interim transcript, final transcript) cancel a
cooperative `CancellationToken`, the TTS stream is closed and the audio
source's buffered queue is cleared so cancellation is audible immediately,
and the freed "floor" (`TurnManager`) is available for the very next turn.

## 16. Failure handling

[`docs/failure-handling.md`](docs/failure-handling.md) — every provider
boundary (STT/LLM/TTS/LiveKit reconnect) degrades to an in-character fallback
or a scoped-to-one-speaker failure, never a crash or an exposed stack trace.

## 17. Latency measurement

[`docs/latency.md`](docs/latency.md) — every stage timestamp is a real
`time.perf_counter()` stamp taken by the code performing that stage; nothing
is estimated. Logged per-turn (`[LATENCY]`) and aggregated (p50/p95) into the
optional in-app debug panel via the `roxstar.state` data topic.

## 18. Privacy

**What is stored (in memory, per running agent-worker process only):**
- the room transcript (bounded ring buffer, 400 turns) and a rolling text
  summary of older turns, for context continuity
- per-speaker extracted facts (name/interest/location/work), keyed by
  LiveKit identity, used only to answer that same speaker's own follow-up
  questions
- aggregate latency numbers (timings only, no content)

**What is NOT stored by default:**
- raw room audio is never recorded or persisted (`PERSIST_TRANSCRIPTS=false`
  by default; setting it to `true` only enables writing the text transcript
  to `TRANSCRIPT_DIR`, and audio is still never captured to disk)
- nothing survives an agent-worker process restart — there is no database in
  the default configuration

**Third-party data flow:** audio and text a human sends is forwarded to
Deepgram (STT) and the generated reply text is forwarded to OpenRouter (LLM)
and ElevenLabs (TTS), per that provider's own data-handling terms. No data is
sent to any provider not listed in `.env.example`. API keys are read only
from environment variables, are never logged (`utils/logging.py::redact`),
and never appear in a request to an unrelated service.

**No real customer data is used anywhere in this repository or its tests.**

## 19. Cost considerations

Every user turn that gets a response costs, per selected bot: one STT stream
(continuous per human, independent of turn count), one LLM completion
(streamed — `OPENROUTER_MODEL` controls cost/quality trade-off; the default
`google/gemini-2.5-flash` is chosen for low per-token cost), and one TTS
synthesis proportional to reply length. Because the router selects exactly
one bot before generation (never both, never a "generate both and pick"
pattern), cost scales with actual conversation turns, not with
`bots × turns`. Small-talk suppression means silence costs nothing beyond the
STT stream that's already running for live captions.

## 20. Known limitations

Full list, with reasoning: [`docs/limitations.md`](docs/limitations.md).
Highlights: in-memory state (no persistence across a worker restart),
regex/keyword-based language and intent understanding rather than a full
NLU model, no automated end-to-end tests against live provider
infrastructure or browser automation.

## 21. Future improvements

- Redis/PostgreSQL-backed `RoomContextManager`/`MemoryStore` for durability
  across restarts and multi-worker deployments (the seam is already there).
- Audio-based speaker diarization as a fallback for participants without a
  clean per-track mic signal.
- An LLM-assisted routing fallback for the narrow cases the deterministic
  rules genuinely can't resolve (kept as a fallback, not the primary path, to
  preserve latency).
- Frontend end-to-end tests (Playwright) driving a real two-browser +
  mocked-agent session.
- Per-room horizontal scaling / fleet management for the agent worker.

---

## Repository layout

```
roxstar-ai-voice-room/
  backend/
    app/
      main.py              # FastAPI token server
      agent_worker.py      # entrypoint: joins room as AI Dost + AI Sathi
      orchestrator.py      # RoomOrchestrator - wires the whole pipeline
      agents/              # dost.py, sathi.py, prompts.py, base.py (BotAgent)
      routing/             # router.py, turn_manager.py
      context/             # manager.py, memory.py, summarizer.py
      pipeline/            # stt.py, llm.py, tts.py, language.py, interruption.py
      livekit_rt/          # room.py (tokens), participants.py, events.py (ingress)
      models/               # conversation.py, participant.py
      config/               # settings.py
      utils/                # logging.py, metrics.py
    tests/                  # 108 tests, pytest
  frontend/
    src/
      components/           # Room, ParticipantList/Card, Chat, MessageList,
                            # VoiceControls, BotStatus, DebugPanel, JoinScreen
      hooks/                # useRoom, useParticipants, useChat, useTranscript,
                            # useDebugState
      services/livekit.js  # the only module touching the LiveKit SDK directly
  docs/
    architecture.md, sequence-diagram.md, routing-strategy.md,
    context-management.md, provider-decisions.md, failure-handling.md,
    latency.md, limitations.md
  .env.example
  .gitignore
```
