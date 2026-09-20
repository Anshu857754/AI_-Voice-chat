# Provider Decisions

Every provider sits behind a small abstract interface
(`STTProvider`, `LLMProvider`, `TTSProvider` in `app/pipeline/{stt,llm,tts}.py`)
so a vendor swap is "write one new class + change an env var," never a
rewrite of the pipeline. This doc records *why* the current defaults were
picked and what the alternatives were.

## LLM: OpenRouter

**Why OpenRouter specifically:** it is the one LLM requirement stated
explicitly in the brief, and it already gives an OpenAI-compatible
chat-completions surface over dozens of models from one API key — exactly
the "swap the model through an env var" abstraction the spec asks for.

- `OPENROUTER_MODEL` selects the model; no code change to switch models.
- Default: `google/gemini-2.5-flash` — fast, cheap, and strong at
  Hindi/Hinglish code-switching in practice, which is the hardest language
  requirement in this project. Any OpenAI-compatible chat model slug on
  OpenRouter works (e.g. `openai/gpt-4o-mini`, `anthropic/claude-3.5-haiku`,
  `meta-llama/llama-3.1-70b-instruct`); pick on cost/latency/quality trade-off
  for your deployment.
- Streaming (`stream()`) is used in production so the first sentence can start
  reaching TTS before the whole reply is generated — this is the single
  biggest lever on perceived latency (see `docs/latency.md`).
- Retries are bounded (`OPENROUTER_MAX_RETRIES`, default 2) and only for
  transient failures (408/429/5xx/timeouts) — a 4xx is never retried, since it
  would just fail again and waste the interruption/response-timeout budget.

**Alternative considered:** calling OpenAI/Anthropic APIs directly. Rejected
because it would mean a different client per model family, defeating the
"change the model through configuration" requirement, and because the spec
explicitly asks for OpenRouter.

## STT: Deepgram (`nova-2`, `language=multi`)

**Why Deepgram:** its `language=multi` mode on `nova-2`/`nova-3` transcribes
Hindi and English **in one stream**, including intra-sentence code-switching
("Cloud mein data actually kaha store hota hai?") — the exact Hinglish case
this room is built around. A single-language model would force a hard choice
between Hindi and English and mangle whichever one loses. It also gives true
bidirectional streaming with interim (partial) results, which the barge-in
detector depends on for its second-fastest signal.

Wrapped via the official `livekit-plugins-deepgram` package
(`DeepgramSTTProvider`), not a hand-rolled websocket client.

**Configurable:** `STT_PROVIDER`, `STT_MODEL`, `STT_LANGUAGE`,
`STT_ENDPOINTING_MS`. Setting `STT_PROVIDER=null` (or omitting `STT_API_KEY`)
falls back to `ScriptedSTTProvider`, so the rest of the pipeline (router,
context, LLM, TTS) can still be exercised and tested without a Deepgram key —
voice input is simply disabled and a clear warning is logged; text chat keeps
working.

**Alternative considered:** a Hindi-only or English-only STT model — rejected
outright, it cannot satisfy the "understand Hindi, English, Hinglish, and
Roman Hindi in the same room" requirement. Whisper-based providers were
considered for cost but were not chosen for the default because they
generally lack low-latency bidirectional streaming with interim results,
which barge-in needs.

## TTS: ElevenLabs (`eleven_turbo_v2_5`)

**Why ElevenLabs:** natural Hindi pronunciation (not phonetic-English
approximation), a voice library with clearly distinct Indian male and female
voices, websocket streaming (audio starts before the full sentence is
synthesized), and an officially maintained LiveKit plugin
(`livekit-plugins-elevenlabs`).

- `DOST_VOICE_ID` / `SATHI_VOICE_ID` select the two voices; must be filled in
  with real voice IDs from an ElevenLabs account (see `.env.example`) —
  **never hardcoded**, since voice choice is a deployment-specific decision
  and the actual IDs are account-scoped, not public constants.
- The two bots are differentiated on **three independent axes**, not just a
  voice ID swap: voice (male/female), delivery settings (`stability`,
  `style`, `speed` — Sathi is set slightly more expressive and ~4% faster),
  and persona prompt (structured/technical vs. warm/example-driven). This is
  deliberate: a demo where the only difference is pitch would not feel like
  two distinct personalities.
- **Replies are spoken in Roman-script Hinglish**, not Devanagari. This
  matches how the target audience actually writes/reads Hinglish and matches
  the spec's own example outputs (all Roman script). ElevenLabs' multilingual
  models pronounce romanized Hindi acceptably; Devanagari script would give
  marginally better pronunciation of pure-Hindi passages at the cost of an
  unnatural-looking room transcript. If pronunciation quality is prioritized
  over transcript readability for your use case, this is a one-line change
  in `agents/prompts.py`'s script instruction — the persona logic does not
  depend on which script is used.

**Configurable:** `TTS_PROVIDER`, `TTS_MODEL`, `TTS_LANGUAGE`. Without
`TTS_API_KEY`/voice IDs set, `SilentTTSProvider` publishes real (silent) audio
frames of a realistic duration and every reply is still delivered via chat —
the pipeline stays fully exercisable and demoable without a paid TTS key.

**Alternative considered:** Azure/Google Cloud TTS — both have credible
Hindi voices, but ElevenLabs' voice-cloning-grade library made it easier to
source two *clearly* distinct, natural-sounding Indian voices from one
account without provisioning custom voice models.

## Storage: in-memory, with an explicit upgrade seam

Room context (`RoomContextManager`), speaker memory (`MemoryStore`) and
metrics (`MetricsCollector`) are process-local Python objects — sufficient
for a single-room MVP and avoids the operational overhead of a database for a
prototype. `docs/limitations.md` documents what this costs (state does not
survive an agent-worker restart, does not span multiple worker instances). The
seam to upgrade is intentional: every one of these classes is instantiated
once in `RoomOrchestrator.__post_init__` and used only through its own
narrow method surface (`add_human_turn`, `ingest`, `record`, …) — swapping
the backing store for Redis (session-lived context) or PostgreSQL (durable
history) means changing what's inside those classes, not their callers.
