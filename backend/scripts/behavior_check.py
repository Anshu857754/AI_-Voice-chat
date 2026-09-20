"""Turn-taking / reply-behaviour proof against the REAL running stack.

Joins the room as a headless human and checks, with a timestamped timeline:

  1. idle join            -> no AI speaks/thinks for IDLE_S seconds
  2. "hii"                -> exactly ONE AI answers (text + voice), the other stays silent
  3. "or btao"            -> the SAME AI answers (context)
  4. "Sathi tum batao ..."-> only Sathi
  5. "Dost tum batao"     -> only Dost
  6. quiet after a reply  -> nothing new is sent on its own
  7. AI<->AI mode off     -> (covered above) no AI ever answers an AI
  8. AI<->AI mode ON      -> alternates, max 6 AI turns; Stop halts it at once

Requires the agent worker to be running.

    cd backend
    python -m scripts.behavior_check [--idle 60]
"""

from __future__ import annotations

import argparse
import array
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from livekit import rtc

from app.config.settings import get_settings
from app.livekit_rt.room import mint_human_token

BOT_NAMES = {"dost": "Dost", "sathi": "Sathi"}
SPEECH_PEAK = 1500  # int16 peak amplitude that counts as speech


@dataclass
class Probe:
    t0: float = field(default_factory=time.monotonic)
    chat: list[tuple[float, str, str]] = field(default_factory=list)  # (t, bot, text)
    states: list[tuple[float, str, str]] = field(default_factory=list)  # (t, bot, state)
    frames: dict[str, int] = field(default_factory=lambda: {"dost": 0, "sathi": 0})
    turns: list[dict] = field(default_factory=list)

    def now(self) -> float:
        return time.monotonic() - self.t0

    def bot_of(self, identity: str) -> str | None:
        return "dost" if "dost" in identity else "sathi" if "sathi" in identity else None

    def snapshot(self) -> tuple[int, int, dict[str, int], int]:
        return len(self.chat), len(self.states), dict(self.frames), len(self.turns)


NO_AUDIO = False  # set by --no-audio: skip voice-frame checks (e.g. TTS quota exhausted)


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    return cond


async def main(idle_s: int) -> int:
    s = get_settings()
    probe = Probe()
    identity = "behavior-check"
    token = mint_human_token(identity=identity, display_name="Anshu", room_name=s.room_name, settings=s)
    room = rtc.Room()
    tasks: list[asyncio.Task] = []

    async def count_frames(bot: str, track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track):
            # Real speech is loud; an idle track only carries codec noise.
            samples = array.array("h", bytes(ev.frame.data))
            if samples and max(abs(x) for x in samples) > SPEECH_PEAK:
                probe.frames[bot] += 1

    def on_track(track: rtc.Track, _pub, participant: rtc.RemoteParticipant) -> None:
        bot = probe.bot_of(participant.identity)
        if bot and track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.create_task(count_frames(bot, track)))

    def on_attrs(changed: dict, participant: rtc.Participant) -> None:
        bot = probe.bot_of(participant.identity)
        if bot and "bot_state" in changed:
            probe.states.append((probe.now(), bot, changed["bot_state"]))
            print(f"    t={probe.now():6.1f}s  state {BOT_NAMES[bot]:5} -> {changed['bot_state']}")

    async def on_chat(reader: rtc.TextStreamReader, sender: str) -> None:
        text = await reader.read_all()
        bot = probe.bot_of(sender)
        if text.startswith("(") and text.endswith(")"):
            print(f"    t={probe.now():6.1f}s  notice {bot}: {text}")  # TTS-failure notice, not a reply
            return
        if bot:
            probe.chat.append((probe.now(), bot, text))
            print(f"    t={probe.now():6.1f}s  chat  {BOT_NAMES[bot]:5}: {text[:110]}")

    def on_data(pkt: rtc.DataPacket) -> None:
        if pkt.topic == "roxstar.transcript":
            try:
                p = json.loads(pkt.data.decode())
            except Exception:
                return
            if p.get("kind") == "turn":
                probe.turns.append(p)

    room.on("track_subscribed", on_track)
    room.on("participant_attributes_changed", on_attrs)
    room.on("data_received", on_data)
    room.register_text_stream_handler("lk.chat", lambda r, snd: tasks.append(asyncio.create_task(on_chat(r, snd))))
    await room.connect(s.livekit_url, token, rtc.RoomOptions(auto_subscribe=True))
    print(f"Joined '{s.room_name}' as {identity}")
    await asyncio.sleep(2)
    others = [p.identity for p in room.remote_participants.values() if probe.bot_of(p.identity) is None]
    print("Other humans in room:", others or "none")
    if others:
        print("WARNING: another human is connected; their mic could add replies. Results may be affected.")
    results: list[bool] = []

    async def control(payload: dict) -> None:
        await room.local_participant.publish_data(json.dumps(payload), reliable=True, topic="roxstar.control")

    def busy() -> bool:
        latest: dict[str, str] = {}
        for _, bot, st in probe.states:
            latest[bot] = st
        return any(v in ("thinking", "speaking") for v in latest.values())

    async def settle(min_replies: int, base_chat: int, timeout: float = 75.0) -> None:
        """Wait for >= min_replies chat replies, then for the AIs to go idle again."""
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(probe.chat) - base_chat < min_replies:
            await asyncio.sleep(0.5)
        await asyncio.sleep(2)
        while time.monotonic() < end and busy():
            await asyncio.sleep(0.5)
        await asyncio.sleep(4)  # a wrongly-queued second AI would show up here

    async def ask(msg: str, expect: str | None, label: str) -> None:
        print(f"\n{label}: user -> {msg!r}")
        chat0, st0, fr0, _ = probe.snapshot()
        await room.local_participant.send_text(msg, topic="lk.chat")
        await settle(1, chat0)
        replies = probe.chat[chat0:]
        who = {b for _, b, _ in replies}
        frames = {b: probe.frames[b] - fr0[b] for b in probe.frames}
        results.append(ok("exactly one reply", len(replies) == 1, f"{len(replies)} chat replies from {sorted(who)}"))
        results.append(ok("only one AI answered", len(who) == 1, f"responder={sorted(who)}"))
        if expect:
            results.append(ok(f"responder is {expect}", who == {expect}))
        resp = next(iter(who), None)
        other = "sathi" if resp == "dost" else "dost"
        if not NO_AUDIO:
            results.append(ok("voice from the responder", bool(resp) and frames[resp] > 0, f"frames={frames}"))
            results.append(ok("other AI silent (no audio)", frames.get(other, 0) == 0))
        states_other = [st for _, b, st in probe.states[st0:] if b == other and st != "idle"]
        results.append(ok("other AI never left Idle", not states_other, f"other states={states_other}"))
        probe._last = resp  # type: ignore[attr-defined]

    # ---- 1. idle ----------------------------------------------------------
    print(f"\n1. Idle: joined, saying nothing for {idle_s}s")
    chat0, st0, fr0, turns0 = probe.snapshot()
    await asyncio.sleep(idle_s)
    chat1, st1, fr1, turns1 = probe.snapshot()
    results.append(ok("no AI chat message", chat1 == chat0))
    results.append(ok("no AI state change (thinking/speaking)", not [x for x in probe.states[st0:] if x[2] != "idle"]))
    if not NO_AUDIO:
        results.append(ok("no AI audio", fr1 == fr0))
    results.append(ok("no new transcript turn", turns1 == turns0))

    # ---- 2..5 -------------------------------------------------------------
    await ask("hii", None, "2. Greeting")
    first = probe._last  # type: ignore[attr-defined]
    await ask("or btao", first, "3. Follow-up (same AI, with context)")
    await ask("Sathi tum batao AI kya hota hai", "sathi", "4. Names Sathi")
    await ask("Dost tum batao", "dost", "5. Names Dost")

    # ---- 6. quiet after reply ---------------------------------------------
    print("\n6. After the last reply the user says nothing for 30s")
    chat0, st0, fr0, turns0 = probe.snapshot()
    await asyncio.sleep(30)
    chat1, st1, fr1, turns1 = probe.snapshot()
    results.append(ok("AI sent nothing on its own", chat1 == chat0 and (NO_AUDIO or fr1 == fr0) and turns1 == turns0))

    # ---- 8. AI<->AI mode ON ------------------------------------------------
    print("\n8a. AI<->AI mode ON: one user message -> alternating AI turns, max 6")
    await control({"type": "ai_mode", "enabled": True})
    await asyncio.sleep(1)
    chat0, *_ = probe.snapshot()
    await room.local_participant.send_text("Dost, cloud computing ke baare mein Sathi se baat karo", topic="lk.chat")
    await settle(6, chat0, timeout=200)
    replies = probe.chat[chat0:]
    order = [b for _, b, _ in replies]
    print("    order:", " -> ".join(BOT_NAMES[b] for b in order))
    results.append(ok("2..6 AI turns", 2 <= len(replies) <= 6, f"{len(replies)} turns"))
    results.append(ok("first is the named AI (Dost)", order[:1] == ["dost"]))
    results.append(ok("AIs alternate", all(a != b for a, b in zip(order, order[1:], strict=False))))

    print("\n8b. Stop button halts the exchange immediately")
    chat0, *_ = probe.snapshot()
    await room.local_participant.send_text("Dost, AI ke baare mein Sathi se baat karo", topic="lk.chat")
    for _ in range(90):
        await asyncio.sleep(0.5)
        if len(probe.chat) - chat0 >= 1:
            break
    await control({"type": "stop"})
    stop_at = len(probe.chat)
    await asyncio.sleep(25)
    results.append(ok("no AI reply after Stop", len(probe.chat) == stop_at, f"{len(probe.chat) - chat0} turns total"))
    await control({"type": "ai_mode", "enabled": False})
    await asyncio.sleep(1)

    await room.disconnect()
    passed = sum(results)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle", type=int, default=60)
    ap.add_argument("--no-audio", action="store_true", help="skip voice-frame checks")
    args = ap.parse_args()
    NO_AUDIO = args.no_audio
    raise SystemExit(asyncio.run(main(args.idle)))
