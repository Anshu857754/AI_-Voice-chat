# Roxstar AI - Architecture

One coherent realtime assistant: **text and voice share the same conversation engine**. LiveKit carries the
room, audio and text streams; STT, LLM and TTS are replaceable provider adapters; the two AIs (AI Dost, AI Sathi)
are real LiveKit participants.

## 1. Components

```
 Browser (React)                              Server
 ┌───────────────────────┐   HTTPS   ┌──────────────────────────────┐
 │ Sidebar / chat / voice│──────────▶│ API (FastAPI)  app/main.py   │  auth, LiveKit tokens,
 │ UI  (frontend/src)    │           │  - /auth /token              │  conversations CRUD, search,
 │  useRoom  useTranscript│          │  - /conversations ...        │  export (SQLite)
 └──────────┬────────────┘           └──────────────┬───────────────┘
            │ WebRTC + data channels                 │ same SQLite file (chat.db)
            ▼                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                          LiveKit room  (audio, lk.chat, data topics)         │
 └───────────────▲────────────────────────────────────────────▲────────────────┘
                 │ two bot connections                          │ ingress (humans' audio + chat)
        ┌────────┴──────────────────────────────────────────────┴───────────────┐
        │ Agent worker  app/agent_worker.py  ->  RoomOrchestrator (orchestrator.py)│
        │                                                                          │
        │  livekit_rt/events.py  RoomIngress     speaker audio -> STT stream, chat │
        │  pipeline/stt.py       STTProvider     (Deepgram | null | failing)       │
        │  pipeline/language.py  understand()    language, questions, follow-ups   │
        │  routing/voice_policy  intent + response mode   <- ONE decision point    │
        │  routing/router.py     pick_responder  which single AI answers           │
        │  routing/claims.py     ResponderClaims one reply per message id          │
        │  routing/turn_manager  TurnManager     single floor, cancel tokens       │
        │  context/manager.py    RoomContextManager  history, summary, memory      │
        │  pipeline/llm.py       LLMProvider     (OpenRouter | scripted | failing) │
        │  agents/base.py        BotAgent        LLM stream -> chat + TTS -> audio │
        │  pipeline/tts.py       TTSProvider     (ElevenLabs | Sarvam | silent)    │
        │  conversations.py      ConversationStore  persistence (SQLite)           │
        └──────────────────────────────────────────────────────────────────────────┘
```

Why a custom worker instead of the `livekit-agents` job framework: the product needs **two AI participants in one
room sharing one conversation floor** (never talking over each other, one reply per message, AI<->AI budgets).
`livekit-agents` models one agent per job/room; we use its pieces (Silero VAD, the ElevenLabs plugin) and the
`livekit` rtc SDK directly, and keep the floor control in `TurnManager`.

## 2. Text pipeline

```
lk.chat text stream ─▶ _handle_chat ─▶ _process_utterance
   normalize/language ─▶ context.add_human_turn ─▶ resolve_response (text|voice)
   ─▶ pick_responder ─▶ claim message id ─▶ TurnManager.submit(interrupt=True)
   ─▶ BotAgent.respond ─▶ LLM tokens ─▶ chat message (published as soon as text is complete)
                                     └▶ (only if responseMode=voice) TTS chunks ─▶ audio track
```

## 3. Voice pipeline

```
mic track ─▶ RoomIngress ─▶ VAD (barge-in signal) + STT stream ─▶ TranscriptEvent (interim/final)
   ─▶ voice filter (echo guard, low confidence, wrong language) ─▶ _process_utterance  (same path as text)
```

Both paths meet at `_process_utterance`; nothing below it knows whether the message was typed or spoken
(`inputMode` is only metadata). Text is generated independently of audio and is **never** blocked by TTS.

## 4. Sequence: "Hi, talk to me in voice."

```
User      Worker                         LLM            TTS            LiveKit
 │ chat ─▶│ resolve_response → voice, sticky pref
 │        │ claim Dost, submit turn ─────▶│
 │        │◀──────────── tokens ──────────│   state: thinking → generating
 │◀ text ─│ (chat published at text-complete)
 │        │ sentence chunk ───────────────────────▶│ state: synthesizing ("Preparing voice…")
 │        │◀──────────────── audio frames ─────────│──────────────▶ published
 │        │ first frame captured → state: speaking   (never earlier)
 │ types  │ new message → interrupt: cancel token, clear audio queue → state: interrupted → new turn
```

## 5. Response mode (`routing/voice_policy.py`)

`inputMode` (text|voice) and `responseMode` (text|voice) are separate. Text is always produced; voice only adds audio.
Decision order, all from the **user**: (1) explicit instruction in the message ("talk to me in voice", "only
text", "bol ke batao", "likh ke batao" ...), (2) the conversation preference (`auto` | `text` | `voice`), (3) the Voice
button, (4) otherwise text. Sticky phrases ("talk to me", "only text") change the preference; one-shot phrases ("say
it", "bol ke batao") affect only that reply. Mentioning the word "voice" ("What is voice AI?") is never a request.
Opening or switching a conversation never starts speech (preference resets to `auto`, voice mode is cleared).

## 6. States (real, not timers)

`BotState`: idle → **thinking** (waiting for the first token) → **generating** (tokens streaming) →
**synthesizing** (TTS requested, no audio yet) → **speaking** (first audio frame really published) → idle;
plus interrupted / error. Tested in `tests/test_states.py`: `speaking` is never reported before a frame reached
the audio source, and text-only replies never enter synthesizing/speaking.

## 7. Routing

`pick_responder` (one function): explicit name → first named AI; no name → the active AI (last responder) → Dost.
`ResponderClaims` guarantees one responder per message id; `TurnManager` gives one floor (one AI speaks at a time,
audio queue = the floor). AI<->AI is off by default, explicit per conversation, max 6 turns per human message, and
speaks only for a user who wants voice. A conversation can restrict participants (e.g. Sathi only).

## 8. Context and memory

Short-term: last N turns (`CONTEXT_MAX_TURNS`), rolling summary of older turns (LLM, best effort), live topic line.
Speaker memory (`context/memory.py`): facts each human states about themselves, stored per identity and injected
only into that speaker's prompt ("[Rahul ke baare mein jo pata hai - private]"); the shared history always names who
said what. The LLM never receives unlimited history. Conversations are restored from SQLite on open.

## 9. Failure strategy

Every request ends in SUCCESS, INTERRUPTED or ERROR + retry:

| Failure | Behaviour |
|---|---|
| TTS fails | text reply already delivered; `last_error{kind:tts}` → "Voice unavailable — text response shown." + Retry (`retry_voice` re-speaks the last reply) |
| STT session fails | `last_error{kind:stt}` → "Couldn't understand the audio" + Retry (restart voice input); text chat unaffected |
| LLM fails/times out | in-character fallback reply + `last_error{kind:llm}` |
| Turn exceeds `TURN_RESPONSE_TIMEOUT_S` | floor released, state → error |
| LiveKit drops | client shows "Reconnecting…", composer disabled, auto-recovers; worker rejoin logic for bots |
| Duplicate voice transcript | suppressed (6 s window); typed messages are always answered |
| Duplicate reply / TTS | claims per message id; each text chunk synthesized once (tests) |

## 10. Providers

`STTProvider`, `LLMProvider`, `TTSProvider` (+ `TTSHandle` with cancel) in `app/pipeline/`; chosen by
`build_*_provider()` from env. Business logic never imports a vendor SDK. See README §"Providers" for the ElevenLabs
vs Sarvam evaluation and configuration.

## 11. Observability

Structured `key=value` log lines per stage: `[ROUTER] event=response_mode message_id request_id input_mode
response_mode reason pref language bot`, `[STT]`, `[LLM] ttft_ms`, `[TTS]`, `[PUBLISH] first_audio_ms`, `[LATENCY]`
per turn, plus aggregate p50/p95 in the debug panel. Keys are redacted (`api_key=********abcd`); transcripts are
logged at debug-level detail only for STT finals (needed for the latency proof) and never contain credentials.

## 12. Correlation ids

Human turn id = `requestId` (= `message_id` in logs). Each bot reply has its own turn id (`responseId`) and stores
`request_id` and `response_mode`; both are in the `roxstar.transcript` payload together with `conversation_id`,
`input_mode` and language. LiveKit room = `roomId`.
