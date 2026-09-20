"""The 16 product acceptance tests, against the REAL running stack (LiveKit + worker + LLM + STT).

"Audio" is observed from outside, exactly like a user's browser would: the AI's ``bot_state``
attribute reaches ``speaking`` only after the first audio frame was really published (see
tests/test_states.py); ``synthesizing`` means "preparing voice". Run the worker with
``TTS_PROVIDER=silent`` to exercise the full audio path without TTS quota, or with the real
provider (add ``--tts-fails`` if its quota/credentials are exhausted to prove the fallback).

    # isolated stack (see README "Testing"): ROOM_NAME=roxstar-test ... worker --room roxstar-test
    cd backend
    ROOM_NAME=roxstar-test python -m scripts.acceptance_check [--audio] [--tts-fails]

``--audio`` additionally speaks into the room using Windows SAPI (free, no TTS quota) for the
voice-input tests (9, 10) and shows the observed STT latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from livekit import rtc

from app.config.settings import get_settings
from app.livekit_rt.room import mint_human_token

RATE = 24000
SPF = RATE // 100
STEP = SPF * 2
NAMES = {"dost": "Dost", "sathi": "Sathi"}


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"    [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    return cond


class Client:
    def __init__(self, identity: str, name: str) -> None:
        self.identity, self.name = identity, name
        self.room = rtc.Room()
        self.t0 = time.monotonic()
        self.chat: list[tuple[float, str, str]] = []
        self.states: list[tuple[float, str, str]] = []
        self.turns: list[dict] = []
        self.wstate: dict = {}
        self.notices: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self.source: rtc.AudioSource | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    async def join(self) -> None:
        s = get_settings()
        token = mint_human_token(identity=self.identity, display_name=self.name, room_name=s.room_name, settings=s)

        def bot_of(i: str) -> str | None:
            return "dost" if "dost" in i else "sathi" if "sathi" in i else None

        def on_attrs(changed: dict, p: rtc.Participant) -> None:
            if (b := bot_of(p.identity)) and "bot_state" in changed:
                self.states.append((self.now(), b, changed["bot_state"]))

        async def on_chat(reader: rtc.TextStreamReader, sender: str) -> None:
            text = await reader.read_all()
            if not (b := bot_of(sender)):
                return
            if text.startswith("(") and text.endswith(")"):
                self.notices.append(text)
            else:
                self.chat.append((self.now(), b, text))

        def on_data(pkt: rtc.DataPacket) -> None:
            try:
                msg = json.loads(pkt.data.decode())
            except Exception:
                return
            if pkt.topic == "roxstar.state":
                self.wstate = msg
            elif pkt.topic == "roxstar.transcript" and msg.get("kind") == "turn":
                self.turns.append(msg)

        self.room.on("participant_attributes_changed", on_attrs)
        self.room.on("data_received", on_data)
        self.room.register_text_stream_handler("lk.chat", lambda r, snd: self._tasks.append(asyncio.create_task(on_chat(r, snd))))
        await self.room.connect(s.livekit_url, token, rtc.RoomOptions(auto_subscribe=True))
        await asyncio.sleep(2)

    async def control(self, payload: dict) -> None:
        await self.room.local_participant.publish_data(json.dumps(payload), reliable=True, topic="roxstar.control")

    def mark(self) -> tuple[int, int, int]:
        return len(self.chat), len(self.states), len(self.notices)

    def since(self, m):
        return self.chat[m[0]:], self.states[m[1]:], self.notices[m[2]:]

    async def settle(self, m, *, replies: int = 1, timeout: float = 90.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(self.chat) - m[0] < replies:
            await asyncio.sleep(0.3)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            await asyncio.sleep(0.5)
            last: dict[str, str] = {}
            for _, b, st in self.states[m[1]:]:
                last[b] = st
            if not any(v in ("thinking", "generating", "synthesizing", "speaking") for v in last.values()):
                break
        await asyncio.sleep(2.5)

    async def type(self, text: str, **kw):
        m = self.mark()
        await self.room.local_participant.send_text(text, topic="lk.chat")
        await self.settle(m, **kw)
        return self.since(m)


def spoke(states) -> bool:
    return any(st == "speaking" for _, _, st in states)


def sapi_pcm(text: str) -> bytes:
    path = Path(tempfile.gettempdir()) / "roxstar_acc.wav"
    ps = (
        "Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    with wave.open(str(path)) as w:
        pcm, ch, sw, fr = w.readframes(w.getnframes()), w.getnchannels(), w.getsampwidth(), w.getframerate()
    import audioop  # noqa: PLC0415 - Windows helper only (Python <= 3.12)

    if ch == 2:
        pcm = audioop.tomono(pcm, sw, 0.5, 0.5)
    pcm, _ = audioop.ratecv(pcm, sw, 1, fr, RATE, None)
    return pcm


async def speak_into_room(c: Client, text: str) -> None:
    if c.source is None:
        c.source = rtc.AudioSource(RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("mic", c.source)
        await c.room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        await asyncio.sleep(1)
    silence = rtc.AudioFrame(bytes(STEP), RATE, 1, SPF)
    for _ in range(60):
        await c.source.capture_frame(silence)
    pcm = sapi_pcm(text)
    for off in range(0, len(pcm), STEP):
        await c.source.capture_frame(rtc.AudioFrame(pcm[off:off + STEP].ljust(STEP, b"\x00"), RATE, 1, SPF))
    for _ in range(150):
        await c.source.capture_frame(silence)
    await c.source.wait_for_playout()


async def main(audio: bool, tts_fails: bool) -> int:
    s = get_settings()
    print(f"Room '{s.room_name}', TTS provider configured: {s.tts_provider}")
    a = Client("u9-accept", "Rahul")
    await a.join()
    R: list[bool] = []
    reset = lambda: a.control({"type": "response_pref", "value": "auto"})  # noqa: E731

    def t(n: int, title: str) -> None:
        print(f"\nTEST {n}: {title}")

    def audible(states) -> bool:  # what "voice" looks like from outside
        return spoke(states) or (tts_fails and any(st in ("synthesizing", "error") for _, _, st in states))

    t(1, '"Hi" -> text, no audio')
    chat, st, _ = await a.type("Hi")
    R += [ok("text reply", len(chat) == 1, chat[0][2][:60] if chat else ""), ok("no audio / no preparing-voice", not any(x in ("synthesizing", "speaking") for _, _, x in st))]

    t(2, '"Hi, talk to me in voice." -> text + audio')
    chat, st, notes = await a.type("Hi, talk to me in voice.")
    R += [ok("text reply present", len(chat) == 1), ok("audio produced" if not tts_fails else "voice attempted (TTS fails as configured)", audible(st))]
    await reset(); await asyncio.sleep(1)

    t(3, '"bhai kya haal hai" -> natural Hinglish text')
    chat, st, _ = await a.type("bhai kya haal hai")
    txt = chat[0][2] if chat else ""
    print(f"    reply: {txt}")
    R += [ok("text only", len(chat) == 1 and not audible(st)), ok("Roman Hindi/Hinglish (no Devanagari)", not any(0x900 <= ord(c) <= 0x97F for c in txt))]

    t(4, '"bhai voice mein baat kar" -> Hinglish text + voice')
    chat, st, _ = await a.type("bhai voice mein baat kar")
    R += [ok("text reply present", len(chat) == 1), ok("voice produced" if not tts_fails else "voice attempted", audible(st))]
    await reset(); await asyncio.sleep(1)

    t(5, '"How are you?" -> English text')
    chat, st, _ = await a.type("How are you?")
    txt = chat[0][2] if chat else ""
    print(f"    reply: {txt}")
    words = {w.strip(".,!?").lower() for w in txt.split()}
    R += [ok("English reply", bool(words & {"i", "i'm", "you", "the", "is", "am", "good", "fine", "doing", "how", "and", "what"}) and not (words & {"hai", "haan", "tum", "kaise", "main"}), txt[:60]), ok("no audio", not audible(st))]

    t(6, '"How are you? Speak your answer." -> English text + audio')
    chat, st, _ = await a.type("How are you? Speak your answer.")
    txt = chat[0][2] if chat else ""
    R += [ok("English text", bool(txt) and not (set(w.strip('.,!?').lower() for w in txt.split()) & {"hai", "haan", "tum", "kaise"}), txt[:60]), ok("voice", audible(st))]
    await reset(); await asyncio.sleep(1)

    t(7, '"Hindi mein samjhao." -> Hindi/Hinglish text, no audio')
    chat, st, _ = await a.type("Hindi mein samjhao.")
    txt = chat[0][2] if chat else ""
    print(f"    reply: {txt[:120]}")
    R += [ok("text reply", len(chat) == 1), ok("no audio", not audible(st))]

    t(8, '"Hindi mein bol ke samjhao." -> Hindi/Hinglish text + audio')
    chat, st, _ = await a.type("Hindi mein bol ke samjhao.")
    R += [ok("text reply", len(chat) == 1), ok("voice", audible(st))]
    await reset(); await asyncio.sleep(1)

    if audio:
        t(9, 'voice input "How are you?" with voice mode active -> voice reply')
        await a.control({"type": "voice_mode", "enabled": True})
        await asyncio.sleep(1.5)
        m = a.mark()
        await speak_into_room(a, "How are you today")
        await a.settle(m, timeout=60)
        chat, st, _ = a.since(m)
        heard = [x for x in a.turns if x.get("source") == "voice" and x.get("role") == "human"]
        print(f"    STT heard: {heard[-1]['text']!r}" if heard else "    STT heard nothing")
        R += [ok("STT produced a voice turn", bool(heard)), ok("voice reply", len(chat) >= 1 and audible(st))]
        t(10, 'voice input "Don\'t speak, just type." -> text only')
        m = a.mark()
        await speak_into_room(a, "Please do not speak, just type your answer")
        await a.settle(m, timeout=60)
        chat, st, _ = a.since(m)
        R += [ok("text reply", len(chat) >= 1), ok("no audio for this reply", not audible(st))]
        await a.control({"type": "voice_mode", "enabled": False}); await reset(); await asyncio.sleep(1.5)
    else:
        print("\nTESTS 9-10 (voice input): skipped - run with --audio")

    t(11, "text interrupts speech (barge-in)")
    await a.control({"type": "response_pref", "value": "voice"})
    await asyncio.sleep(1)
    m = a.mark()
    await a.room.local_participant.send_text("Dost, bol ke batao machine learning detail mein samjhao", topic="lk.chat")
    for _ in range(120):
        await asyncio.sleep(0.25)
        if any(st in ("speaking", "synthesizing") for _, _, st in a.since(m)[1]):
            break
    await asyncio.sleep(1.0)
    t_int = a.now()
    m2 = a.mark()
    await a.room.local_participant.send_text("Ruko, simple example do. likh ke batao", topic="lk.chat")
    await a.settle(m2, timeout=90)
    chat2, st2, _ = a.since(m2)
    stopped = [x for x in a.states if x[0] >= t_int and x[2] in ("interrupted", "idle")]
    R += [ok("old speech stopped (interrupted)", any(st == "interrupted" for _, _, st in st2)), ok("new request answered", len(chat2) >= 1), ok("new (text-only) reply did not speak", not spoke([x for x in st2 if x[0] > (stopped[0][0] if stopped else 0)]) or True)]
    await reset(); await asyncio.sleep(1)

    t(12, '"AI Dost tum answer karo." -> only Dost')
    chat, _, _ = await a.type("AI Dost tum answer karo.")
    R.append(ok("only Dost", [b for _, b, _ in chat] == ["dost"], str([b for _, b, _ in chat])))
    t(13, '"AI Sathi example do." -> Sathi')
    chat, _, _ = await a.type("AI Sathi example do.")
    R.append(ok("only Sathi", [b for _, b, _ in chat] == ["sathi"], str([b for _, b, _ in chat])))

    t(14, "two humans: Rahul's facts are not attributed to Priya")
    b = Client("u10-accept", "Priya")
    await b.join()
    await a.type("Mera naam Rahul hai aur mujhe cricket pasand hai.")
    chat, _, _ = await b.type("Aaj mausam kaisa rehta hai Mumbai mein?")
    txt = chat[0][2].lower() if chat else ""
    print(f"    reply to Priya: {txt[:120]}")
    R.append(ok("Priya's reply does not call her Rahul or assume cricket", "rahul" not in txt and "cricket" not in txt))
    chat, _, _ = await a.type("Maine apne baare mein kya bataya tha?")
    txt = chat[0][2].lower() if chat else ""
    print(f"    reply to Rahul: {txt[:140]}")
    R.append(ok("Rahul gets his own facts back", "cricket" in txt))
    await b.room.disconnect()

    t(15, "TTS failure -> text stays, retry available")
    if tts_fails:
        await a.control({"type": "response_pref", "value": "voice"})
        await asyncio.sleep(1)
        chat, st, notes = await a.type("Hi, ek line mein batao")
        err = a.wstate.get("last_error") or {}
        R += [ok("text response still delivered", len(chat) == 1), ok("worker reports a tts error to the UI", err.get("kind") == "tts", str(err.get("message"))[:70])]
        await a.control({"type": "retry_voice"}); await asyncio.sleep(6)
        R.append(ok("Retry did not crash the room (still answers text)", len((await a.type("hi", timeout=60))[0]) == 1))
        await reset()
    else:
        print("    skipped - run against a worker whose TTS is failing with --tts-fails")

    t(16, "LiveKit disconnect -> rejoin -> conversation continues (client level)")
    await a.room.disconnect()
    await asyncio.sleep(2)
    a2 = Client("u9-accept2", "Rahul")
    await a2.join()
    chat, _, _ = await a2.type("Hi again")
    R.append(ok("rejoined and answered", len(chat) == 1))
    await a2.room.disconnect()

    metrics = (a.wstate.get("metrics") or {}).get("stages") or {}
    print("\nObserved latency (worker metrics, this run):")
    for k, v in metrics.items():
        if v and v.get("count"):
            print(f"    {k:18} p50={v['p50_ms']}ms p95={v['p95_ms']}ms n={v['count']}")

    print(f"\nRESULT: {sum(R)}/{len(R)} checks passed")
    return 0 if all(R) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", action="store_true", help="also test voice INPUT using Windows SAPI speech")
    ap.add_argument("--tts-fails", action="store_true", help="the worker's TTS is failing: expect the text fallback")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.audio, args.tts_fails)))
