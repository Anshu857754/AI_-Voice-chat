# Sequence Diagrams

## 1. Normal voice turn (no interruption)

```mermaid
sequenceDiagram
    participant H as Human (Rahul)
    participant LK as LiveKit
    participant ING as RoomIngress
    participant STT as STT (Deepgram)
    participant LANG as Language understanding
    participant RTR as BotRouter
    participant CTX as RoomContextManager
    participant TM as TurnManager
    participant DOST as AI Dost agent
    participant LLM as OpenRouter LLM
    participant TTS as TTS (ElevenLabs)

    H->>LK: speaks "AI kya hota hai?"
    LK->>ING: audio frames (subscribed human track)
    ING->>STT: push_frame() (resampled 16kHz)
    STT-->>ING: interim transcripts (partial)
    ING-->>LK: publish roxstar.transcript (kind=interim)
    STT-->>ING: final transcript + speech_end timestamp
    ING->>LANG: understand(text)
    LANG-->>ING: language=hinglish, is_question=true
    ING->>CTX: add_human_turn(text, speaker, VOICE)
    CTX-->>CTX: extract speaker facts, update topic
    ING->>RTR: route(text, state)
    RTR-->>ING: selected_bot=dost, reason=turn_taking
    ING->>TM: submit(bots=(dost,), runner)
    TM->>DOST: run_sequence -> respond()
    DOST->>CTX: build_messages(bot=dost, asking=rahul)
    CTX-->>DOST: system + room context + speaker facts + recent turns
    DOST->>LLM: stream(messages)
    LLM-->>DOST: token deltas
    DOST->>TTS: synthesize(sentence chunk)
    TTS-->>DOST: audio frames
    DOST->>LK: capture_frame() (publishes to room)
    LK->>H: hears AI Dost's answer
    DOST->>CTX: add_bot_turn(text, bot=dost)
    DOST-->>LK: send_text() mirrors reply to lk.chat
    ING-->>LK: publish roxstar.state (bot_state, latency)
```

## 2. Barge-in / interruption

```mermaid
sequenceDiagram
    participant H as Human (Rahul)
    participant LK as LiveKit
    participant ING as RoomIngress
    participant VAD as Silero VAD
    participant STT as STT
    participant INT as InterruptionController
    participant TM as TurnManager
    participant DOST as AI Dost agent (speaking)
    participant TTS as TTS handle

    DOST->>TTS: streaming "Machine learning mein pehle..."
    TTS->>LK: audio frames (SPEAKING)
    H->>LK: starts talking: "Ruko, simple example se samjhao"
    LK->>ING: audio frames
    ING->>VAD: push_frame()
    VAD-->>ING: START_OF_SPEECH (speech_duration >= threshold)
    ING->>INT: trigger(VAD_SPEECH_START, speaking_bot=dost)
    Note over INT: debounced - only the first signal within 0.6s acts
    INT->>TM: interrupt(reason="vad_speech_start")
    TM->>DOST: token.cancel() (cooperative)
    DOST->>TTS: cancel() -- closes stream, stops billing
    DOST->>LK: source.clear_queue() -- drops buffered audio
    Note over LK: audio stops within ~1 frame, no overlap
    TM-->>TM: floor freed (_finish), busy=False

    ING->>STT: (continues transcribing new speech)
    STT-->>ING: final: "Ruko, simple example se samjhao"
    ING->>ING: route() -> reason=interruption_redirect, bot=dost (same bot)
    ING->>TM: submit(bots=(dost,), interrupt=True)
    TM->>DOST: new turn starts immediately
    DOST->>LK: "Bilkul, ek simple example lete hain..."
```

Three independent detection signals feed the same debounced
`InterruptionController.trigger()`, from fastest-but-noisiest to
slowest-but-certain:

1. **VAD start-of-speech** while a bot is `SPEAKING` (~100-300ms)
2. **Interim STT transcript** with ≥2 words, or an explicit cue word
   ("ruko", "stop", "ek second") even as a single word (~300-800ms)
3. **Final STT transcript** while a bot is (still) speaking — the backstop

Only the *first* of these to fire within the 0.6s debounce window actually
cancels; the other two are suppressed logging-only events. See
`app/pipeline/interruption.py`.

## 3. Text chat (same pipeline as voice)

```mermaid
sequenceDiagram
    participant H as Human (Priya)
    participant LK as LiveKit (lk.chat text stream)
    participant ING as RoomIngress
    participant RTR as BotRouter
    participant CTX as RoomContextManager
    participant SATHI as AI Sathi agent

    H->>LK: sendText("Machine learning simple words mein batao")
    LK->>ING: registerTextStreamHandler(lk.chat) fires
    ING->>CTX: add_human_turn(text, source=TEXT)
    ING->>RTR: route(text, state) -- same router as voice
    RTR-->>ING: selected_bot=sathi
    ING->>SATHI: generate + respond
    SATHI->>LK: publishes audio (if TTS available) AND send_text() reply
    Note over CTX: this turn is now part of the SAME shared\ncontext voice turns use - no separate text history
```

## 4. Explicit two-bot sequencing (no overlap)

```mermaid
sequenceDiagram
    participant H as Human
    participant RTR as BotRouter
    participant TM as TurnManager
    participant DOST as AI Dost
    participant SATHI as AI Sathi

    H->>RTR: "AI Dost, tum answer karo. AI Sathi, baad mein example dena."
    RTR-->>TM: selected_bot=dost, queued_bots=(sathi,)
    TM->>DOST: run turn 1 (SPEAKING)
    DOST-->>TM: turn complete (IDLE)
    Note over TM: strictly sequential - Sathi cannot start\nuntil Dost's task has fully finished
    TM->>SATHI: run turn 2 (SPEAKING)
    SATHI-->>TM: turn complete (IDLE)
```
