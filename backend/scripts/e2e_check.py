"""End-to-end check against the REAL running stack (LiveKit + Deepgram + OpenRouter + ElevenLabs).

Joins the room as a headless human, then verifies:
  A. text chat  -> a bot replies in chat AND publishes audio
  B. voice      -> spoken audio (synthesized by ElevenLabs) is published as a mic
                   track, transcribed by Deepgram, routed, answered, and the
                   bot's reply audio comes back

Requires the agent worker to be running:  python -m app.agent_worker

    cd backend
    python -m scripts.e2e_check
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from livekit import rtc

from app.config.settings import get_settings
from app.livekit_rt.room import mint_human_token

SAMPLE_RATE = 24000


class Probe:
    def __init__(self) -> None:
        self.chat: list[tuple[str, str]] = []
        self.turns: list[dict] = []
        self.states: list[dict] = []
        self.audio_frames: dict[str, int] = {}
        self._tasks: list[asyncio.Task] = []

    def audio_from(self, who_substr: str) -> int:
        return sum(n for ident, n in self.audio_frames.items() if who_substr in ident)


async def _count_frames(probe: Probe, identity: str, track: rtc.Track) -> None:
    stream = rtc.AudioStream(track)
    async for ev in stream:
        # Ignore digital silence so a SilentTTS fallback would not count as speech.
        data = bytes(ev.frame.data)
        if any(data[:64]):
            probe.audio_frames[identity] = probe.audio_frames.get(identity, 0) + 1


async def synth_pcm(text: str, voice_id: str, api_key: str) -> bytes:
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            params={"output_format": f"pcm_{SAMPLE_RATE}"},
            headers={"xi-api-key": api_key},
            json={"text": text, "model_id": "eleven_turbo_v2_5"},
        )
        r.raise_for_status()
        return r.content


async def wait_for(predicate, timeout: float) -> bool:
    try:
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.25)
        return True
    except TimeoutError:
        return False


def report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")
    return ok


async def main() -> int:
    s = get_settings()
    if not s.livekit_configured:
        print("LIVEKIT_* not configured")
        return 2

    probe = Probe()
    room = rtc.Room()
    identity = f"e2e-{int(time.time()) % 100000}"
    token = mint_human_token(identity=identity, display_name="E2E Tester", room_name=s.room_name)

    def on_data(pkt: rtc.DataPacket) -> None:
        try:
            payload = json.loads(pkt.data.decode())
        except Exception:
            return
        if pkt.topic == "roxstar.transcript" and payload.get("kind") == "turn":
            probe.turns.append(payload)
        elif pkt.topic == "roxstar.state":
            probe.states.append(payload)

    async def on_chat(reader: rtc.TextStreamReader, sender: str) -> None:
        probe.chat.append((sender, await reader.read_all()))

    room.on("data_received", on_data)
    room.on(
        "track_subscribed",
        lambda track, pub, p: probe._tasks.append(
            asyncio.create_task(_count_frames(probe, p.identity, track))
        )
        if track.kind == rtc.TrackKind.KIND_AUDIO
        else None,
    )
    room.register_text_stream_handler(
        "lk.chat", lambda reader, sender: probe._tasks.append(asyncio.create_task(on_chat(reader, sender)))
    )

    await room.connect(s.livekit_url, token, rtc.RoomOptions(auto_subscribe=True))
    print(f"Joined room '{s.room_name}' as {identity}")
    await asyncio.sleep(2)

    results: list[bool] = []
    bots = {p.identity: p.attributes.get("bot_state") for p in room.remote_participants.values()}
    print("Participants:", ", ".join(room.remote_participants.keys()))
    results.append(report("both bots present", {s.dost_identity, s.sathi_identity} <= set(bots)))

    # ---- A. text chat --------------------------------------------------
    print("\nA. Text chat: 'AI kya hota hai?'")
    t0 = time.monotonic()
    await room.local_participant.send_text("AI kya hota hai?", topic="lk.chat")
    got = await wait_for(lambda: any("dost" in i or "sathi" in i for i, _ in probe.chat), 30)
    bot_msgs = [(i, t) for i, t in probe.chat if "dost" in i or "sathi" in i]
    results.append(report("bot replied in chat", got, f"{time.monotonic() - t0:.1f}s"))
    if bot_msgs:
        print(f"      {bot_msgs[0][0]}: {bot_msgs[0][1][:140]}")
        results.append(report("only ONE bot answered", len({i for i, _ in bot_msgs}) == 1))
    await asyncio.sleep(6)
    results.append(
        report("bot audio (real speech, not silence) published", probe.audio_from("roxstar-ai") > 0,
               f"{probe.audio_from('roxstar-ai')} frames")
    )

    # ---- B. voice ------------------------------------------------------
    print("\nB. Voice: publishing spoken 'Can you explain what machine learning is...' as a mic track")
    frames_before = probe.audio_from("roxstar-ai")
    chat_before = len(probe.chat)
    turns_before = len(probe.turns)
    pcm = await synth_pcm("Can you explain what machine learning is, in simple words?", s.dost_voice_id, s.tts_api_key)
    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("e2e-mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    await asyncio.sleep(1.5)
    spf = SAMPLE_RATE // 100  # 10ms frames
    step = spf * 2
    for off in range(0, len(pcm), step):
        chunk = pcm[off : off + step]
        if len(chunk) < step:
            chunk = chunk + b"\x00" * (step - len(chunk))
        await source.capture_frame(rtc.AudioFrame(chunk, SAMPLE_RATE, 1, spf))
    # trailing silence so VAD/endpointing closes the utterance
    for _ in range(150):
        await source.capture_frame(rtc.AudioFrame(b"\x00" * step, SAMPLE_RATE, 1, spf))
    await source.wait_for_playout()
    t1 = time.monotonic()

    heard = await wait_for(
        lambda: any(t.get("source") == "voice" and t.get("role") == "human" for t in probe.turns[turns_before:]), 25
    )
    results.append(report("Deepgram transcribed the speech", heard))
    for t in probe.turns[turns_before:]:
        if t.get("source") == "voice" and t.get("role") == "human":
            print(f"      heard: {t['text']!r} (language={t.get('language')})")
    replied = await wait_for(lambda: len(probe.chat) > chat_before, 30)
    results.append(report("bot answered the voice question", replied, f"{time.monotonic() - t1:.1f}s after speech"))
    if replied:
        print(f"      {probe.chat[chat_before][0]}: {probe.chat[chat_before][1][:140]}")
    await asyncio.sleep(8)
    results.append(
        report("bot spoke the answer back (audio frames)", probe.audio_from("roxstar-ai") > frames_before,
               f"+{probe.audio_from('roxstar-ai') - frames_before} frames")
    )

    # ---- latency the pipeline itself measured --------------------------
    last = next((st for st in reversed(probe.states) if st.get("recent_latency")), None)
    if last:
        print("\nLatency measured by the pipeline (real, most recent turns):")
        for row in last["recent_latency"][:3]:
            print("   ", {k: v for k, v in row.items() if v is not None and k != "turn_id"})

    await room.disconnect()
    ok = all(results)
    print(f"\n{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'} ({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
