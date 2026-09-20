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

### Accounts (login / signup)

The UI opens on a ChatGPT-style login/signup page. Accounts are stored in a
local SQLite file (`backend/data/users.db`, git-ignored); passwords are hashed
with scrypt and sessions are signed JWTs. `/token` (the LiveKit join token)
requires a logged-in session, and the participant's name/identity come from the
account. Optional `.env` settings: `AUTH_REQUIRED` (default `true`),
`AUTH_SECRET` (signing key; derived from `LIVEKIT_API_SECRET` when unset),
`AUTH_DB_PATH`, `AUTH_TOKEN_TTL_HOURS`.

### AI personality, model and reply length

* Personas (tone, language rules, per-AI sentence cap) live in
  [`backend/app/config/personas.toml`](backend/app/config/personas.toml) - edit
  and restart the worker.
* `OPENROUTER_MODEL` selects the LLM. `google/gemini-2.5-flash` is the fastest
  and cheapest; `anthropic/claude-haiku-4.5` gave the most natural Hinglish in
  our runs at similar latency; `anthropic/claude-sonnet-4.5` / `openai/gpt-4o`
  are stronger but slower and costlier.
* `LLM_TEMPERATURE` (0.7), `LLM_MAX_TOKENS` (150) and `LLM_MAX_TOKENS_DETAIL`
  (400, used only when the user asks for detail).
* Check a scripted conversation against the live stack:
  `python -m scripts.convo_check` (worker must be running).

### Conversations and chat history

The app is a chat product, not just one room: a sidebar with **New Chat**, search, **Pinned** and **Recent** chats
(grouped Today / Yesterday / Previous 7 days / Earlier by their real timestamps), an **Archived** page, rename,
pin, archive, delete (with confirmation), clear, export (TXT / Markdown / JSON), per-chat settings and global
settings. Chats have stable URLs (`/chat/:id`, `/archived`); refresh, back and forward restore the right chat.

* **Storage.** Same SQLite file as before (`HISTORY_DB_PATH`, default `backend/data/chat.db`): a `conversations`
  table (title, preview, timestamps, pinned, archived, participants, settings) plus the existing `messages` table,
  now tagged with `conversation_id`. The sidebar loads summaries only (30 per page, infinite scroll); messages load
  when a chat is opened (latest 50, "Load earlier messages" for more). Every query is scoped to the logged-in user.
* **Titles.** "New Chat" until the first meaningful message; then a 3-7 word title (`app/titles.py`, no LLM needed;
  the worker refines it with one tiny LLM call when available). "hi" -> "New Conversation". A manual rename is never
  overwritten.
* **API** (all need the Bearer token): `GET/POST /conversations`, `GET /conversations/search?q=`,
  `GET/PATCH/DELETE /conversations/{id}`, `POST /conversations/{id}/clear`, `GET /conversations/{id}/messages?before=`,
  `GET /conversations/{id}/export?format=txt|md|json`.
* **Worker.** Opening a chat sends the control message `conversation {id}`; the worker verifies the chat belongs to
  that user, resets its context and restores the chat's last turns, applies the chat's settings (participants,
  language, reply length, AI Collaboration) and publishes `conversation {id, by, seq}` back. The composer stays
  disabled until this is confirmed, so a message can never be filed under the previous chat.
* **Voice safety.** Opening or switching to any chat - including one that was a voice chat - ALWAYS starts in text
  mode: no microphone, no TTS, no speaking. The worker clears voice mode on every switch and the UI resets to text.
* **Limits (by design of the single-room worker).** The worker serves ONE conversation at a time. If a second tab
  or device opens another chat, the first shows "AI is being used in another session - Use here" instead of
  fighting over it. Conversation history is stored in SQLite even when `MONGODB_URI` is set (Mongo remains the
  legacy room history only). Unread badges are not implemented: only the open chat can receive messages.

`python -m scripts.conversation_check --api http://localhost:8010` proves all of the above against the running stack
(use a throw-away stack - see below - because it opens several conversations and switches the worker between them).
A second, isolated stack for testing: run the API and worker with `ROOM_NAME=roxstar-test API_PORT=8020
HISTORY_DB_PATH=./data/test_chat.db AUTH_DB_PATH=./data/test_users.db` and `python -m app.agent_worker --room roxstar-test`.

### Realtime engine: text + voice, intent, languages, providers

Full design: [ARCHITECTURE.md](ARCHITECTURE.md). Short version:

**One pipeline for text and voice.** Typed messages and transcribed speech meet in `_process_utterance` and share
language detection, intent, context, routing and the LLM. Text is always generated first and never waits for TTS;
audio is added on top only when the user wants it.

**Input mode and response mode are independent** (`backend/app/routing/voice_policy.py`, the single decision point):

| The user... | Reply |
|---|---|
| types "How are you?" | text |
| types "Hi, talk to me in voice." / "bhai voice mein baat kar" | text + voice (and stays voice: conversation preference `voice`) |
| types "How are you? Speak your answer." / "bol ke batao" | text + voice for this reply only |
| speaks with Voice mode on | text + voice |
| speaks "Don't speak, just type." / types "only text", "likh ke batao" | text only (sticky phrases like "only text" also set the preference to `text`, which beats the Voice button) |
| mentions the word: "What is voice AI?", "How does TTS work?", "Why isn't voice working?" | text (never a request) |

The conversation preference is `auto` (default: text, plus voice when the Voice button is on or the message asks),
`text` or `voice`; set from chat settings ("How the AI replies"), from a message, or from the chip above the input.
Pressing Voice overrides an earlier "text only"; Stop Voice ends a "talk to me in voice" preference. **Opening or
switching a conversation never starts speech** (mic off, preference reset to `auto`, worker clears voice mode).

**Languages.** English, Hindi (Devanagari), Roman Hindi and Hinglish are detected per message
(`backend/app/pipeline/language.py`); the reply follows the user ("Kaise ho?" -> natural Hinglish, "How are you?" ->
English) unless asked ("Respond in English", "Hindi mein samjhao"); a chat can also pin a language. Adding another
Indian language = one detector entry + one prompt instruction. Personas (`app/config/personas.toml`) use everyday
conversational Hindi/Hinglish, not textbook Hindi. The model is told how its reply is delivered so it never claims it
cannot speak.

**Providers** (env only; business logic never imports a vendor):

| Slot | Options | Notes |
|---|---|---|
| STT | `STT_PROVIDER=deepgram` (nova-2, `language=multi`, interim results) \| `null` | multilingual, good Hindi/English code-switching, streaming; noisy fragments are filtered (confidence, language, echo guard) |
| LLM | OpenRouter (`OPENROUTER_MODEL`, default `anthropic/claude-haiku-4.5`) \| scripted offline | fast first token; reasoning disabled; temperature 0.7, ~150 tokens |
| TTS | `TTS_PROVIDER=elevenlabs` \| `sarvam` \| `silent` | see below |

*ElevenLabs* (`eleven_turbo_v2_5`, `TTS_LANGUAGE=hi`): streaming (audio starts before the sentence ends), good Indian
Hindi and Hinglish code-switching, large voice library, official LiveKit plugin; cost is per character and the free
tier (10k chars) is exhausted quickly - when it is, voice degrades to the text fallback. *Sarvam*
(`app/pipeline/tts_sarvam.py`): built for Indian languages and priced for them; the REST endpoint returns a whole clip
per request (no token streaming) so time-to-first-audio is one round trip per sentence chunk. It is implemented and
unit-tested against a mocked transport but **was not exercised against the live service here** (no credentials):
set `TTS_PROVIDER=sarvam`, `TTS_API_KEY`, `TTS_MODEL` (a Bulbul model id), `TTS_LANGUAGE=hi-IN`, and
`DOST_VOICE_ID` / `SATHI_VOICE_ID` = Sarvam speaker names taken from Sarvam's current docs. Model and speaker names are
deliberately never defaulted. Which one sounds better for your users is a config switch plus a listening test, not a
rewrite. `silent` publishes silent audio of realistic length (tests / demos without quota).

**States** shown in the UI are real: thinking -> writing (LLM streaming) -> "Preparing voice..." (TTS requested) ->
speaking (only after the first audio frame was published). See ARCHITECTURE.md §6.

**Failures** end in success, interruption or an error with Retry: TTS failure keeps the text and shows "Voice
unavailable - text response shown" + Retry voice; STT failure shows "Couldn't understand the audio" + Retry and text
chat keeps working; LLM failure gives an in-character fallback; network loss shows "Reconnecting..." and recovers.

**Correlation and metrics.** Every reply carries `request_id` (the human message), `response_id`, `response_mode`;
logs are structured; the debug panel shows p50/p95 for STT, router, LLM first token, LLM total, TTS first audio and
end-to-end latency (measured, never invented).

**Privacy and security.** No raw audio is stored (STT streams are processed and dropped); stored: transcript
text, titles, previews and settings in SQLite. Provider keys live only in the server `.env`; the browser gets a
short-lived room-scoped LiveKit token and a signed session token; conversations are scoped to the owning account.

`python -m scripts.mode_check`, `scripts.conversation_check` and `scripts.acceptance_check [--audio] [--tts-fails]`
run the acceptance tests against a live stack (use the isolated test stack described above); `pytest` runs 297+ unit
and pipeline tests (`tests/test_engine.py` drives the real orchestrator with a fake audio source).

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
