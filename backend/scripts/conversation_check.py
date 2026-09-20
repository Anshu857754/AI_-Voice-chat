"""Saved-conversations proof against the REAL running stack (API + worker + DB).

Two fresh accounts, real LiveKit clients. Checks: create / auto-title / preview /
ordering, switching + restoring context, per-conversation isolation, rename /
pin / archive / restore / delete / clear / search / export / pagination, user
isolation, and the voice-safety rule (opening a conversation never speaks).

Run the worker with TTS_PROVIDER=silent to observe real `speaking` states
without TTS quota:

    cd backend
    python -m scripts.conversation_check [--api http://localhost:8010]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from livekit import rtc

NAMES = {"dost": "Dost", "sathi": "Sathi"}


def ok(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    return cond


class Client:
    def __init__(self, api: str, name: str) -> None:
        self.api = api
        self.http = httpx.Client(base_url=api, timeout=20)
        r = self.http.post("/auth/signup", json={"name": name, "email": f"c{uuid.uuid4().hex[:8]}@example.com", "password": "password123"})
        r.raise_for_status()
        self.token = r.json()["token"]
        self.user = r.json()["user"]
        self.http.headers["Authorization"] = f"Bearer {self.token}"
        self.room = rtc.Room()
        self.chat: list[tuple[float, str, str]] = []
        self.states: list[tuple[float, str, str]] = []
        self.wstate: dict = {}
        self.t0 = time.monotonic()
        self._tasks: list[asyncio.Task] = []

    def now(self) -> float:
        return time.monotonic() - self.t0

    async def join(self) -> None:
        r = self.http.post("/token", json={})
        r.raise_for_status()
        j = r.json()

        def bot_of(i: str) -> str | None:
            return "dost" if "dost" in i else "sathi" if "sathi" in i else None

        def on_attrs(changed: dict, p: rtc.Participant) -> None:
            if (b := bot_of(p.identity)) and "bot_state" in changed:
                self.states.append((self.now(), b, changed["bot_state"]))

        async def on_chat(reader: rtc.TextStreamReader, sender: str) -> None:
            text = await reader.read_all()
            b = bot_of(sender)
            if b and not (text.startswith("(") and text.endswith(")")):
                self.chat.append((self.now(), b, text))

        def on_data(pkt: rtc.DataPacket) -> None:
            if pkt.topic == "roxstar.state":
                try:
                    self.wstate = json.loads(pkt.data.decode())
                except Exception:
                    pass

        self.room.on("participant_attributes_changed", on_attrs)
        self.room.on("data_received", on_data)
        self.room.register_text_stream_handler("lk.chat", lambda rd, s: self._tasks.append(asyncio.create_task(on_chat(rd, s))))
        await self.room.connect(j["url"], j["token"], rtc.RoomOptions(auto_subscribe=True))
        await asyncio.sleep(2)

    async def control(self, payload: dict) -> None:
        await self.room.local_participant.publish_data(json.dumps(payload), reliable=True, topic="roxstar.control")

    async def activate(self, cid: str, *, reload: bool = False) -> None:
        await self.control({"type": "conversation", "id": cid, **({"reload": True} if reload else {})})
        end = time.monotonic() + 10
        while time.monotonic() < end and (self.wstate.get("conversation") or {}).get("id") != cid:
            await asyncio.sleep(0.3)
        await asyncio.sleep(0.5)

    async def ask(self, text: str, timeout: float = 60.0) -> str:
        n, s = len(self.chat), len(self.states)
        await self.room.local_participant.send_text(text, topic="lk.chat")
        end = time.monotonic() + timeout
        while time.monotonic() < end and len(self.chat) == n:
            await asyncio.sleep(0.3)
        await asyncio.sleep(2.5)
        self._last_states = self.states[s:]
        return self.chat[n][2] if len(self.chat) > n else ""

    def spoke_since_last_ask(self) -> bool:
        return any(st == "speaking" for _, _, st in self._last_states)


async def main(api: str) -> int:
    a = Client(api, "Alice")
    b = Client(api, "Bob")
    await a.join()
    R: list[bool] = []
    H = a.http

    print("\n=== 1. New chats: separate ids, untitled, no auto-create on reload ===")
    c1 = H.post("/conversations", json={"kind": "text"}).json()
    c2 = H.post("/conversations", json={"kind": "text"}).json()
    lst = H.get("/conversations").json()
    R += [
        ok("two distinct ids", c1["id"] != c2["id"]),
        ok("titled 'New Chat', empty", c1["title"] == "New Chat" and c1["message_count"] == 0),
        ok("listing (a 'page refresh') does not create chats", lst["total"] == 2, f"total={lst['total']}"),
    ]

    print("\n=== 2. Chat in conversation 1: auto-title, preview, persistence ===")
    await a.activate(c1["id"])
    reply = await a.ask("Explain machine learning in simple Hindi")
    print(f"    AI: {reply[:90]}")
    got = H.get(f"/conversations/{c1['id']}").json()
    R += [
        ok("AI replied", bool(reply)),
        ok("auto title from first message", got["title"] not in ("New Chat", "New Conversation"), repr(got["title"])),
        ok("2 messages saved, preview = AI reply", got["message_count"] == 2 and got["last_preview"].startswith(reply[:20]), f"count={got['message_count']}"),
        ok("did not speak (text mode)", not a.spoke_since_last_ask()),
    ]
    await asyncio.sleep(6)
    refined = H.get(f"/conversations/{c1['id']}").json()["title"]
    print(f"    title after LLM refinement window: {refined!r}")
    R.append(ok("title stays short (<=7 words)", len(refined.split()) <= 7))

    print("\n=== 3. Conversation 2 is isolated; switching restores context ===")
    await a.activate(c2["id"])
    await a.ask("hi")
    g2 = H.get(f"/conversations/{c2['id']}").json()
    R.append(ok("'hi' -> title 'New Conversation'", g2["title"] == "New Conversation", repr(g2["title"])))
    await a.activate(c1["id"])
    reply = await a.ask("ek example do isi ka")
    print(f"    AI (back in chat 1): {reply[:110]}")
    R += [
        ok("context restored: follow-up still about machine learning", any(w in reply.lower() for w in ("machine", "ml", "learning", "data", "spam", "netflix", "recommend")), reply[:60]),
        ok("chat 1 has 4 msgs, chat 2 still 2", H.get(f"/conversations/{c1['id']}").json()["message_count"] == 4 and H.get(f"/conversations/{c2['id']}").json()["message_count"] == 2),
    ]
    m = H.get(f"/conversations/{c1['id']}/messages").json()
    R.append(ok("messages endpoint restores full transcript in order", [x["role"] for x in m["messages"]] == ["human", "bot", "human", "bot"]))

    print("\n=== 4. Ordering, pin, rename ===")
    order = [x["id"] for x in H.get("/conversations").json()["conversations"]]
    R.append(ok("most recently active first", order[0] == c1["id"], f"order={order}"))
    H.patch(f"/conversations/{c2['id']}", json={"pinned": True})
    order = [x["id"] for x in H.get("/conversations").json()["conversations"]]
    R.append(ok("pinned conversation sorts first", order[0] == c2["id"]))
    r = H.patch(f"/conversations/{c1['id']}", json={"title": "My ML Notes"}).json()
    R.append(ok("rename works and is permanent", r["title"] == "My ML Notes" and not r["title_auto"]))
    await a.ask("aur ek chhota point batao")
    R.append(ok("later messages do not overwrite a manual title", H.get(f"/conversations/{c1['id']}").json()["title"] == "My ML Notes"))

    print("\n=== 5. Search (titles + messages) ===")
    res = H.get("/conversations/search", params={"q": "machine learning"}).json()["results"]
    R += [
        ok("finds the conversation by message/title text", any(x["id"] == c1["id"] for x in res), f"{len(res)} result(s)"),
        ok("empty query -> no results", H.get("/conversations/search", params={"q": " "}).json()["results"] == []),
    ]

    print("\n=== 6. Archive / restore / delete / clear / export ===")
    H.patch(f"/conversations/{c2['id']}", json={"archived": True})
    R += [
        ok("archived chat leaves Recent", all(x["id"] != c2["id"] for x in H.get("/conversations").json()["conversations"])),
        ok("...and is listed under Archived (unpinned)", [x["pinned"] for x in H.get("/conversations", params={"archived": True}).json()["conversations"]] == [False]),
    ]
    H.patch(f"/conversations/{c2['id']}", json={"archived": False})
    R.append(ok("restore brings it back", any(x["id"] == c2["id"] for x in H.get("/conversations").json()["conversations"])))
    exp = H.get(f"/conversations/{c1['id']}/export", params={"format": "md"})
    R.append(ok("export markdown", exp.status_code == 200 and exp.text.startswith("# My ML Notes") and "AI Dost" in exp.text or "AI Sathi" in exp.text, exp.headers.get("content-disposition", "")))
    await a.activate(c1["id"], reload=True)
    cleared = H.post(f"/conversations/{c1['id']}/clear").json()
    R.append(ok("clear keeps metadata, removes messages", cleared["message_count"] == 0 and cleared["title"] == "My ML Notes"))
    c3 = H.post("/conversations", json={"kind": "text"}).json()
    d = H.delete(f"/conversations/{c3['id']}")
    R += [ok("delete -> 204", d.status_code == 204), ok("deleted chat is really gone (404)", H.get(f"/conversations/{c3['id']}").status_code == 404)]

    print("\n=== 7. Another user cannot see or use these chats ===")
    hb = b.http
    R += [
        ok("Bob's list is empty", hb.get("/conversations").json()["total"] == 0),
        ok("Bob GET Alice's chat -> 404", hb.get(f"/conversations/{c1['id']}").status_code == 404),
        ok("Bob cannot delete it", hb.delete(f"/conversations/{c1['id']}").status_code == 404),
        ok("Bob's search finds nothing", hb.get("/conversations/search", params={"q": "machine"}).json()["results"] == []),
        ok("no token -> 401", httpx.get(f"{api}/conversations").status_code == 401),
    ]
    await b.join()
    before = (a.wstate.get("conversation") or {}).get("id")
    await b.control({"type": "conversation", "id": c1["id"]})
    await asyncio.sleep(2)
    R.append(ok("worker refuses to activate another user's chat", (a.wstate.get("conversation") or {}).get("id") == before, f"active still {before}"))
    await b.room.disconnect()

    print("\n=== 8. VOICE SAFETY: opening an old (voice) conversation never speaks ===")
    cv = H.post("/conversations", json={"kind": "voice"}).json()
    await a.activate(cv["id"])
    await a.control({"type": "voice_mode", "enabled": True})
    await asyncio.sleep(1.5)
    reply = await a.ask("Dost, batao machine learning kya hota hai", timeout=90)
    spoke_in_voice = a.spoke_since_last_ask()
    print(f"    (voice mode) spoke = {spoke_in_voice}")
    await asyncio.sleep(10)
    await a.activate(c1["id"])  # leave the voice conversation...
    await a.activate(cv["id"])  # ...and reopen it
    ws = a.wstate
    R += [
        ok("voice mode was on before switching", spoke_in_voice),
        ok("reopened voice chat: worker has NO voice users", ws.get("voice_users") == [], f"voice_users={ws.get('voice_users')}"),
        ok("conversation still marked 'voice'", H.get(f"/conversations/{cv['id']}").json()["mode"] == "voice"),
    ]
    m = H.get(f"/conversations/{cv['id']}/messages").json()["messages"]
    R.append(ok("transcript restored after reopening", len(m) >= 2, f"{len(m)} messages"))
    reply = await a.ask("aur ek line mein batao", timeout=90)
    R += [ok("typed message in the reopened voice chat -> text only", bool(reply) and not a.spoke_since_last_ask())]

    print("\n=== 9. Conversation settings: participants + language ===")
    H.patch(f"/conversations/{cv['id']}", json={"participants": ["sathi"], "settings": {"language": "english"}})
    await a.control({"type": "conversation_settings"})
    await asyncio.sleep(1)
    n = len(a.chat)
    reply = await a.ask("Dost, tell me one fact about cats")
    who = a.chat[n][1] if len(a.chat) > n else None
    R += [
        ok("only the selected AI (Sathi) answers, even when Dost is named", who == "sathi", f"answered by {who}"),
        ok("english setting honoured", bool(reply) and not any(0x900 <= ord(ch) <= 0x97F for ch in reply) and any(w in reply.lower().split() for w in ("the", "are", "can", "their", "is", "a")), reply[:70]),
    ]
    H.patch(f"/conversations/{cv['id']}", json={"participants": ["dost", "sathi"], "settings": {"language": "auto"}})

    print("\n=== 10. Pagination ===")
    for _ in range(35):
        H.post("/conversations", json={"kind": "text"})
    p1 = H.get("/conversations", params={"limit": 30}).json()
    p2 = H.get("/conversations", params={"limit": 30, "offset": 30}).json()
    R += [ok("page 1 = 30 with has_more", len(p1["conversations"]) == 30 and p1["has_more"]), ok("page 2 has the rest", len(p2["conversations"]) >= 5 and not p2["has_more"], f"{len(p2['conversations'])}")]

    await a.room.disconnect()
    print(f"\nRESULT: {sum(R)}/{len(R)} checks passed")
    return 0 if all(R) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8010")
    raise SystemExit(asyncio.run(main(ap.parse_args().api)))
