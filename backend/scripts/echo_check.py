"""Echo-loop proof against the REAL running stack (worker must be running).

Simulates the failure seen with speakers + open mic: while an AI is speaking,
speech-like audio arrives on the user's microphone track. Checks that

  1. the AI is NOT interrupted and no new "user" turn / extra reply is created;
  2. normal voice input still works afterwards (a clear question gets ONE reply).

    cd backend
    python -m scripts.echo_check
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

RATE = 24000
SPF = RATE // 100
STEP = SPF * 2


async def synth(text: str, voice_id: str, key: str) -> bytes:
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            params={"output_format": f"pcm_{RATE}"},
            headers={"xi-api-key": key},
            json={"text": text, "model_id": "eleven_turbo_v2_5"},
        )
        r.raise_for_status()
        return r.content


async def play(source: rtc.AudioSource, pcm: bytes, tail_frames: int = 120) -> None:
    for off in range(0, len(pcm), STEP):
        chunk = pcm[off : off + STEP].ljust(STEP, b"\x00")
        await source.capture_frame(rtc.AudioFrame(chunk, RATE, 1, SPF))
    for _ in range(tail_frames):
        await source.capture_frame(rtc.AudioFrame(b"\x00" * STEP, RATE, 1, SPF))
    await source.wait_for_playout()


async def hold(source: rtc.AudioSource, seconds: float) -> None:
    """Wait like a live mic: keep sending silence so the STT stream stays open."""
    silence = rtc.AudioFrame(bytes(STEP), RATE, 1, SPF)
    for _ in range(int(seconds * 2)):
        for _ in range(50):
            await source.capture_frame(silence)
        await asyncio.sleep(0.45)


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    return cond


async def main() -> int:
    s = get_settings()
    t0 = time.monotonic()
    now = lambda: f"t={time.monotonic() - t0:6.1f}s"  # noqa: E731
    states: list[tuple[float, str, str]] = []
    chat: list[tuple[float, str, str]] = []
    turns: list[tuple[float, dict]] = []

    room = rtc.Room()
    tasks: list[asyncio.Task] = []

    def bot_of(identity: str) -> str | None:
        return "dost" if "dost" in identity else "sathi" if "sathi" in identity else None

    def on_attrs(changed: dict, p: rtc.Participant) -> None:
        bot = bot_of(p.identity)
        if bot and "bot_state" in changed:
            states.append((time.monotonic() - t0, bot, changed["bot_state"]))
            print(f"    {now()}  state {bot:5} -> {changed['bot_state']}")

    async def on_chat(reader: rtc.TextStreamReader, sender: str) -> None:
        text = await reader.read_all()
        if bot_of(sender):
            chat.append((time.monotonic() - t0, bot_of(sender), text))
            print(f"    {now()}  chat  {bot_of(sender):5}: {text[:100]}")

    def on_data(pkt: rtc.DataPacket) -> None:
        if pkt.topic != "roxstar.transcript":
            return
        try:
            p = json.loads(pkt.data.decode())
        except Exception:
            return
        if p.get("kind") == "turn" and p.get("role") == "human" and p.get("source") == "voice":
            turns.append((time.monotonic() - t0, p))
            print(f"    {now()}  HUMAN VOICE TURN: {p.get('text')!r}")

    room.on("participant_attributes_changed", on_attrs)
    room.on("data_received", on_data)
    room.register_text_stream_handler("lk.chat", lambda r, snd: tasks.append(asyncio.create_task(on_chat(r, snd))))
    token = mint_human_token(identity="echo-check", display_name="Anshu", room_name=s.room_name, settings=s)
    await room.connect(s.livekit_url, token, rtc.RoomOptions(auto_subscribe=True))
    source = rtc.AudioSource(RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("echo-mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    await hold(source, 3)
    results: list[bool] = []

    echo_pcm = await synth(
        "Yeah okay, so cloud computing means using servers on the internet instead of your own machine, right?",
        s.sathi_voice_id, s.tts_api_key,
    )
    clear_pcm = await synth("Sathi, tum batao AI kya hota hai?", s.dost_voice_id, s.tts_api_key)

    print("\nA. AI is speaking a long answer; speech-like echo hits the mic meanwhile")
    await room.local_participant.send_text("Dost, cloud computing thoda detail mein samjhao", topic="lk.chat")
    for _ in range(120):
        await asyncio.sleep(0.25)
        if any(b == "dost" and st == "speaking" for _, b, st in states):
            break
    print(f"    {now()}  >>> Dost is speaking: injecting echo audio into the mic now")
    a_start = time.monotonic() - t0
    await play(source, echo_pcm, tail_frames=80)
    print(f"    {now()}  >>> echo audio finished")
    # let Dost finish and anything wrongly triggered surface
    for _ in range(160):
        await hold(source, 0.5)
        if not any(st in ("thinking", "speaking") for _, b, st in states[-1:]) and time.monotonic() - t0 > a_start + 5:
            break
    await hold(source, 8)
    a_states = [st for t, b, st in states if t >= a_start - 20]
    results.append(ok("Dost was NOT interrupted by the echo", "interrupted" not in a_states))
    results.append(ok("no fake 'user' voice turn was created", not [x for x in turns if x[0] >= a_start]))
    results.append(ok("exactly one reply (Dost) - no self-reply", len(chat) == 1 and chat[0][1] == "dost", f"{len(chat)} replies"))

    print("\nB. After the AI is idle, a clear spoken question must still work")
    chat_before, turns_before = len(chat), len(turns)
    await play(source, clear_pcm, tail_frames=150)
    for _ in range(120):
        await hold(source, 0.5)
        if len(chat) > chat_before:
            break
    await hold(source, 6)
    new = chat[chat_before:]
    results.append(ok("clear speech was heard as a voice turn", len(turns) > turns_before))
    results.append(ok("exactly one reply, from Sathi (named)", len(new) == 1 and new[0][1] == "sathi", f"{[(b) for _, b, _ in new]}"))

    await room.disconnect()
    print(f"\nRESULT: {sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
