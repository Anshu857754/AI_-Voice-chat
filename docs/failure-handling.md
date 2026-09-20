# Failure Handling

Every provider boundary is expected to fail sometimes (network blips, vendor
outages, rate limits). The rule enforced throughout the codebase: **a failure
in one provider or one participant's pipeline degrades gracefully and never
takes down the room, the other bot, or any other participant.** Verified in
[`backend/tests/test_failures.py`](../backend/tests/test_failures.py) and
[`test_llm_provider.py`](../backend/tests/test_llm_provider.py) (offline, no
real credentials).

## LLM failure (OpenRouter timeout / error / malformed response)

- `OpenRouterLLMProvider` retries transient failures (`408/429/5xx`,
  connect/read timeouts) up to `OPENROUTER_MAX_RETRIES` times with jittered
  backoff; a `4xx` is never retried.
- A malformed response — non-JSON body, missing `choices`, a choice with no
  `message`, empty content, or an error envelope — is normalized to a single
  `LLMError` in `_extract_message_content()` rather than an unhandled
  `KeyError`/`TypeError` three layers up.
- When generation ultimately fails, `BotAgent.respond()` sets
  `result.llm_failed = True` and returns cleanly (never raises out of the
  turn). `RoomOrchestrator._run_bot_turn` then has the bot **speak and send
  to chat** a short, in-character fallback instead of the intended reply:

  > *Dost:* "Ek second, response generate karne mein issue aa gaya. Please
  > dobara poochho."
  > *Sathi:* "Arre, ek second - mujhe response banane mein dikkat aa gayi.
  > Phir se poochho na."

  No stack trace, no error code, no vendor name ever reaches the user.

## TTS failure

- If synthesis breaks mid-reply, the **text that was already generated is
  never lost** — `SpeakResult.text_generated` is preserved regardless of
  where in the audio pipeline the failure happened.
- `RoomOrchestrator` always commits the reply text to context and **always
  mirrors it to room chat** (`agent.send_chat`) independent of whether audio
  succeeded — chat delivery is not a "backup path," it is unconditional, so a
  broken TTS session degrades the room to "text-only for this one reply," not
  to silence.
- A short in-character notice is additionally sent when TTS specifically
  failed: *"Awaaz mein dikkat aa rahi hai, jawab chat mein bhej diya hai."*

## STT failure

- Each human's microphone runs its **own** STT session
  (`RoomIngress._run_audio_session`). A Deepgram socket failure for one
  speaker raises a typed `STTError`, is logged, and that speaker's session
  closes — every other human, and both bots, are unaffected. The failed
  speaker can keep participating via text chat, which shares the same
  pipeline.
- If `STT_API_KEY` is entirely absent, the app does not silently pretend
  voice works: `build_stt_provider` logs a clear warning and falls back to
  `ScriptedSTTProvider` (no real transcription), while text chat remains
  fully functional.

## LiveKit reconnect

- `RoomIngress` listens for `reconnecting` / `reconnected` / `disconnected`.
  On `reconnected`, `sync_existing()` re-adopts every participant currently in
  the room and re-subscribes to human tracks — subscriptions and per-speaker
  STT sessions do not survive a full reconnect cycle, so they are rebuilt
  rather than assumed to still be valid.
- The frontend's `useRoom` hook tracks `ConnectionState` and the join screen
  reappears if the connection is fully lost, rather than leaving the user
  staring at a stale, silently-broken UI.

## Turn-level timeout (a stuck provider cannot hold the room hostage)

- `TurnManager` wraps every bot turn in `asyncio.wait_for(...,
  timeout=TURN_RESPONSE_TIMEOUT_S)` (default 30s). A hung LLM or TTS call is
  force-cancelled, the bot's state moves to `ERROR`, and — critically — the
  floor is released (`_finish()` always runs from the `finally` block), so the
  room is never stuck waiting on one broken turn.

## Interruption never deadlocks (a fixed real bug)

- Early in development, `TurnManager.interrupt()` held its internal lock
  across the `await` that waits for the cancelled task to unwind — but that
  task's own cleanup (`_finish()`) needs the *same* lock to release the
  floor, so every barge-in silently stalled for the full 1.5s force-cancel
  timeout before recovering. This was caught by
  `test_interruption.py::test_no_overlap_between_bots_during_interruption`
  (timing-based assertion), not by inspection. The fix releases the lock
  before awaiting task completion; the same test now passes in milliseconds.
  Left here because it is a good example of *why* the concurrency logic has
  dedicated tests rather than relying on manual verification.

## Participant / network failures

- A speaker disconnecting mid-utterance closes their STT session cleanly
  (`ParticipantDisconnected` → `_close_session`); no orphaned tasks.
- Any exception inside one bot's turn runner is caught locally in
  `TurnManager._run_sequence` (`except Exception`), logs
  `[TURN] event=runner_failed`, and moves only that bot to `ERROR` — a queued
  second bot (e.g. from an explicit "Dost then Sathi" request) still gets its
  turn afterward.

## What is never done

- Internal exception messages, stack traces, HTTP status bodies, or vendor
  names are never surfaced to a human participant — only short, in-character
  Hinglish fallback lines are.
- Secrets are never logged. `utils/logging.py::redact()` masks any field
  whose name contains `key`/`secret`/`token`/`password`/`authorization`
  before it reaches a log line, and this is exercised directly in
  `test_llm_provider.py::TestLatencyLoggingDoesNotLeakSecrets`.
