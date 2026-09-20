# Latency Measurement

**Every number this app reports is a real, measured timestamp difference from
the running pipeline.** Nothing is estimated, hardcoded, or simulated. This
doc explains exactly how, so the numbers can be trusted (or checked).

## What is measured, and where

`app/models/conversation.py::LatencyTrace` holds monotonic (`time.perf_counter()`)
timestamps for each stage of one turn:

```python
speech_end_at        # VAD end-of-speech (voice) or message-received time (text)
stt_final_at          # STT emitted the final transcript
router_done_at        # BotRouter returned its decision
llm_first_token_at    # first streamed delta arrived from the LLM
llm_done_at           # LLM stream finished
tts_first_frame_at    # first synthesized audio frame arrived from TTS
published_at          # first audio frame was handed to AudioSource.capture_frame
```

Each stamp is written by the actual code performing that stage, at the moment
it happens — `orchestrator.py` stamps `stt_final_at` when the STT provider's
final-transcript event fires, `agents/base.py::BotAgent.respond` stamps
`llm_first_token_at` on the literal first streamed token, `_speak_chunk` stamps
`tts_first_frame_at`/`published_at` on the literal first frame captured to the
room. There is no code path that invents or backfills one of these values.

## Derived stage latencies

`LatencyTrace` exposes these as simple differences (`None` if a stage never
ran, e.g. `tts_latency_ms` is `None` for a text-only fallback reply):

| Metric | Definition |
|---|---|
| `stt_latency_ms` | human stopped speaking → STT final transcript |
| `router_latency_ms` | STT final → routing decision made |
| `llm_ttft_ms` | routing decision → first LLM token (time-to-first-token) |
| `llm_latency_ms` | routing decision → full LLM stream complete |
| `tts_latency_ms` | first LLM token → first synthesized audio frame |
| `total_latency_ms` | human stopped speaking (or sent text) → first audio actually published to the room |

`total_latency_ms` is the number that matters for "how long did the room wait
in silence" — it is anchored to the real end of human speech (VAD
`END_OF_SPEECH`), not to when processing merely started, so it honestly
includes STT tail latency too.

## Where the numbers go

1. **Structured logs** — every completed trace is logged as
   `[LATENCY] turn_id=... stt_latency_ms=... router_latency_ms=... llm_ttft_ms=...
   llm_latency_ms=... tts_latency_ms=... total_latency_ms=...`
   (`utils/metrics.py::MetricsCollector.record`).
2. **In-memory aggregation** — a bounded ring buffer (last 200 traces) feeds
   p50/p95/mean/min/max per stage (`MetricsCollector.snapshot()`), computed
   from the actual sample list with `statistics.median` and a manual
   percentile index — no synthetic distribution.
3. **`roxstar.state` data topic** — the same snapshot is published to the room
   after every turn, so the frontend's optional **Debug panel**
   (`frontend/src/components/DebugPanel.jsx`) renders live p50/p95 numbers
   pulled directly from this payload.
4. **`GET /metrics`** on the token-server API returns the same aggregate for
   external inspection (Note: the agent worker is a separate process from the
   API server, so the authoritative numbers are the ones in the agent
   worker's own logs and in the `roxstar.state` topic; the API's `/metrics`
   reflects only that process's own activity, e.g. token issuance timing, and
   says so explicitly in its response).

## How to reproduce/observe real numbers yourself

```bash
# from backend/, with a real LiveKit + OpenRouter + Deepgram + ElevenLabs config
python -m app.agent_worker
# then join the room from the frontend and speak; watch the terminal for
# [LATENCY] lines, one per completed bot turn.
```

No latency figures are quoted in this repository's documentation as if they
were representative production numbers, because they depend entirely on your
chosen OpenRouter model, STT/TTS vendor regions, and network path to LiveKit
— exactly the things this measurement system is built to surface honestly
rather than to paper over with a marketing number.
