"""TEXT vs VOICE separation proof against the REAL running stack.

Joins the room as a headless human and drives the 8 scenarios of the spec,
printing a timeline. "The AI spoke" is observed from the outside: the bot's
``bot_state`` attribute reaches ``speaking`` (only ever set while audio is being
published). Run the worker with ``TTS_PROVIDER=silent`` to see real speaking
states without spending ElevenLabs quota; run it normally to see the real TTS
failure path.

    cd backend
    python -m scripts.mode_check            # all scenarios
    python -m scripts.mode_check --tts-fails  # expect TTS to fail in voice mode
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from livekit import rtc

from app.config.settings import get_settings
from app.livekit_rt.room import mint_human_token

NAMES = {"dost": "Dost", "sathi": "Sathi"}


class Probe:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.chat: list[tuple[float, str, str]] = []
        self.states: list[tuple[float, str, str]] = []
        self.notices: list[tuple[float, str]] = []
        self.turns: list[dict] = []
        self.voice_users: list[str] = []

    def now(self) -> float:
        return time.monotonic() - self.t0

    @staticmethod
    def bot_of(identity: str) -> str | None:
        return "dost" if "dost" in identity else "sathi" if "sathi" in identity else None

    def mark(self) -> tuple[int, int, int]:
        return len(self.chat), len(self.states), len(self.notices)

    def since(self, m: tuple[int, int, int]):
        return self.chat[m[0]:], self.states[m[1]:], self.notices[m[2]:]


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    return cond


async def main(tts_fails: bool) -> int:
    s = get_settings()
    p = Probe()
    room = rtc.Room()
    tasks: list[asyncio.Task] = []
    identity = "mode-check"

    def on_attrs(changed: dict, part: rtc.Participant) -> None:
        bot = p.bot_of(part.identity)
        if bot and "bot_state" in changed:
            p.states.append((p.now(), bot, changed["bot_state"]))
            print(f"    t={p.now():6.1f}s  state {NAMES[bot]:5} -> {changed['bot_state']}")

    async def on_chat(reader: rtc.TextStreamReader, sender: str) -> None:
        text = await reader.read_all()
        bot = p.bot_of(sender)
        if not bot:
            return
        if text.startswith("(") and text.endswith(")"):
            p.notices.append((p.now(), text))
            print(f"    t={p.now():6.1f}s  notice {bot}: {text}")
            return
        p.chat.append((p.now(), bot, text))
        print(f"    t={p.now():6.1f}s  chat  {NAMES[bot]:5}: {text[:100]}")

    def on_data(pkt: rtc.DataPacket) -> None:
        try:
            msg = json.loads(pkt.data.decode())
        except Exception:
            return
        if pkt.topic == "roxstar.transcript" and msg.get("kind") == "turn":
            p.turns.append(msg)
        elif pkt.topic == "roxstar.state":
            p.voice_users = msg.get("voice_users", p.voice_users)

    room.on("participant_attributes_changed", on_attrs)
    room.on("data_received", on_data)
    room.register_text_stream_handler("lk.chat", lambda r, snd: tasks.append(asyncio.create_task(on_chat(r, snd))))
    tok = mint_human_token(identity=identity, display_name="Anshu", room_name=s.room_name, settings=s)
    await room.connect(s.livekit_url, tok, rtc.RoomOptions(auto_subscribe=True))
    await asyncio.sleep(2.5)
    print(f"Joined '{s.room_name}' as {identity}; TTS provider configured: {s.tts_provider}")

    async def control(payload: dict) -> None:
        await room.local_participant.publish_data(json.dumps(payload), reliable=True, topic="roxstar.control")

    async def say(text: str, *, replies: int = 1, timeout: float = 90.0, settle: float = 3.0):
        m = p.mark()
        print(f"\n  >> user types: {text!r}")
        await room.local_participant.send_text(text, topic="lk.chat")
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(p.chat) - m[0] < replies:
            await asyncio.sleep(0.4)
        # let speech (if any) run to its end, then a quiet period
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            await asyncio.sleep(0.5)
            latest: dict[str, str] = {}
            for _, b, st in p.states[m[1]:]:
                latest[b] = st
            if not any(v in ("thinking", "speaking") for v in latest.values()):
                break
        await asyncio.sleep(settle)
        return p.since(m)

    def spoke(states) -> bool:
        return any(st == "speaking" for _, _, st in states)

    results: list[bool] = []

    print("\n=== 1. App opens: mode is TEXT; user types 'Hi' ===")
    chat, states, notes = await say("Hi")
    results += [
        ok("AI answered in text", len(chat) == 1, f"{len(chat)} replies"),
        ok("NO speaking state / no audio", not spoke(states)),
        ok("no voice-failure notice (TTS never called)", not notes),
        ok("worker has nobody in voice mode", identity not in p.voice_users, f"voice_users={p.voice_users}"),
    ]

    print("\n=== 2. Several typed messages ===")
    m = p.mark()
    for t in ("kaise ho?", "AI kya hai?", "Sathi tum batao cloud computing kya hota hai"):
        await say(t)
    chat, states, notes = p.since(m)
    results += [
        ok("every message got a text reply", len(chat) == 3, f"{len(chat)} replies"),
        ok("never spoke", not spoke(states)),
        ok("no TTS notices", not notes),
    ]

    print("\n=== 3. User presses Voice ===")
    await control({"type": "voice_mode", "enabled": True})
    await asyncio.sleep(2)
    results.append(ok("worker now knows this user is in voice mode", identity in p.voice_users, f"voice_users={p.voice_users}"))

    print("\n=== 4. In voice mode a message is answered in text + voice ===")
    chat, states, notes = await say("AI, mujhe machine learning samjhao", timeout=120)
    results.append(ok("reply text still arrives in chat", len(chat) >= 1, f"{len(chat)} replies"))
    if tts_fails:
        results += [
            ok("TTS attempted and failed -> voice-failure notice", bool(notes)),
            ok("text reply NOT hidden by the TTS failure", len(chat) >= 1),
        ]
    else:
        results += [ok("AI reached 'speaking'", spoke(states)), ok("no failure notice", not notes)]

    print("\n=== 5. User leaves voice mode; next typed message is text only ===")
    await control({"type": "voice_mode", "enabled": False})
    await asyncio.sleep(2)
    results.append(ok("worker forgot voice mode", identity not in p.voice_users, f"voice_users={p.voice_users}"))
    chat, states, notes = await say("text mein batao, docker kya hai")
    results += [
        ok("text reply", len(chat) == 1),
        ok("NO speaking", not spoke(states)),
        ok("no TTS notice", not notes),
    ]

    print("\n=== 6. AI<->AI ON in TEXT mode ===")
    await control({"type": "ai_mode", "enabled": True})
    chat, states, notes = await say("Dost, Sathi se poocho AI kya hota hai", replies=6, timeout=150, settle=4)
    results += [
        ok("AIs exchanged several text turns", len(chat) >= 3, f"{len(chat)} replies"),
        ok("chain never spoke", not spoke(states)),
        ok("no TTS notices", not notes),
    ]
    await control({"type": "stop"})
    await asyncio.sleep(3)

    print("\n=== 7. AI<->AI ON in VOICE mode ===")
    await control({"type": "voice_mode", "enabled": True})
    await asyncio.sleep(1.5)
    chat, states, notes = await say("Dost, Sathi se poocho cloud kya hota hai", replies=2, timeout=180, settle=4)
    bots_seen = {b for _, b, _ in chat}
    if tts_fails:
        results += [ok("chain replies exist as text", len(chat) >= 1), ok("TTS was attempted (failure notices)", bool(notes))]
    else:
        spoke_bots = {b for _, b, st in states if st == "speaking"}
        results += [
            ok("both AIs replied", len(bots_seen) >= 2, f"{sorted(bots_seen)}"),
            ok("AI<->AI spoke (text + voice)", len(spoke_bots) >= 1, f"spoke={sorted(spoke_bots)}"),
        ]
    await control({"type": "stop"})
    await control({"type": "ai_mode", "enabled": False})
    await asyncio.sleep(3)

    if not tts_fails:
        print("\n=== 9. Leaving voice mode mid-speech stops audio at once, text stays whole ===")
        m = p.mark()
        await room.local_participant.send_text("Dost, cloud computing detail mein samjhao", topic="lk.chat")
        end = time.monotonic() + 60
        while time.monotonic() < end and not spoke(p.since(m)[1]):
            await asyncio.sleep(0.3)
        await asyncio.sleep(1.0)
        t_off = p.now()
        await control({"type": "voice_mode", "enabled": False})
        await asyncio.sleep(4)
        chat, states, _ = p.since(m)
        stop_times = [t for t, _, st in states if t >= t_off and st != "speaking"]
        stopped = bool(stop_times) and (min(stop_times) - t_off) < 3.0
        results.append(ok("speech stopped within 3s of leaving voice mode", stopped, f"{(min(stop_times) - t_off):.1f}s" if stop_times else "never"))
        results.append(ok("full reply text still delivered to chat", len(chat) >= 1 and len(chat[0][2]) > 40, f"{len(chat[0][2]) if chat else 0} chars"))
        await asyncio.sleep(3)

    print("\n=== 8. Explicit request 'voice mein batao' in TEXT mode ===")
    results.append(ok("still in text mode", identity not in p.voice_users, f"voice_users={p.voice_users}"))
    chat, states, notes = await say("voice mein batao AI kya hai", timeout=120)
    if tts_fails:
        results.append(ok("voice was attempted for that one reply (failure notice)", bool(notes)))
    else:
        results.append(ok("that ONE reply was spoken", spoke(states)))
    results.append(ok("mic/voice mode NOT switched on by the phrase", identity not in p.voice_users, f"voice_users={p.voice_users}"))
    chat, states, notes = await say("aur ek chhota example do")
    results += [ok("next plain message is text only", not spoke(states) and not notes)]

    await room.disconnect()
    print(f"\nRESULT: {sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts-fails", action="store_true", help="expect TTS failures in voice mode (quota exhausted)")
    raise SystemExit(asyncio.run(main(ap.parse_args().tts_fails)))
