"""Conversations: many saved chats per user, with real persistence.

This extends the existing history storage instead of adding a second one: the
same SQLite file (``HISTORY_DB_PATH``) and the same ``messages`` table (one
row per turn, keyed by ``turn_id``), now tagged with a ``conversation_id``.
A ``conversations`` table holds the sidebar metadata (title, preview, pinned,
archived, ...), so the sidebar never has to load messages.

Writers: the agent worker (every committed turn) and the API (rename, pin,
archive, delete, clear, settings). Readers: the API. Every read/write that
comes from a user is scoped to that user's id.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from app.history import _KEYS, _row
from app.models.conversation import Turn, TurnRole, TurnSource
from app.models.participant import BotId
from app.titles import DEFAULT_TITLE, FALLBACK_TITLE, UNTITLED, derive_title

PARTICIPANTS = ("dost", "sathi")
KINDS = ("text", "voice", "collab")
LANGUAGES = ("auto", "hinglish", "english", "hindi")
REPLY_LENGTHS = ("short", "detailed")
RESPONSE_PREFS = ("auto", "text", "voice")
TITLE_MAX = 80
PREVIEW_MAX = 90

DEFAULT_SETTINGS: dict[str, object] = {
    "ai_collab": False,
    "voice_enabled": True,
    "language": "auto",
    "reply_length": "short",
    "response_pref": "auto",  # auto | text | voice (see routing/voice_policy.py)
}

_SUMMARY_COLS = (
    "id, title, title_auto, created_at, updated_at, last_preview, message_count, mode,"
    " pinned, archived, participants, settings"
)


def _preview(text: str) -> str:
    s = " ".join(text.split())
    return s if len(s) <= PREVIEW_MAX else s[: PREVIEW_MAX - 1].rstrip() + "…"


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _snippet(text: str, q: str) -> str:
    flat = " ".join(text.split())
    i = flat.lower().find(q.lower())
    if i < 0:
        return _preview(flat)
    start = max(0, i - 40)
    end = min(len(flat), i + len(q) + 60)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


class ConversationError(Exception):
    """User-safe error (bad input or not found)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class ConversationStore:
    def __init__(self, path: Path | str, room: str) -> None:
        self._path = Path(path)
        self._room = room
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,"
                "title_auto INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,"
                "updated_at REAL NOT NULL, last_preview TEXT NOT NULL DEFAULT '',"
                "message_count INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT 'text',"
                "pinned INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,"
                "participants TEXT NOT NULL, settings TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations (user_id, archived, pinned, updated_at)"
            )
            # `messages` is created by the legacy history store too; keep both in sync.
            db.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "turn_id TEXT PRIMARY KEY, room TEXT NOT NULL, role TEXT NOT NULL,"
                "bot TEXT, identity TEXT NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL,"
                "source TEXT NOT NULL, language TEXT, interrupted INTEGER NOT NULL DEFAULT 0,"
                "created_at REAL NOT NULL)"
            )
            cols = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
            if "conversation_id" not in cols:
                db.execute("ALTER TABLE messages ADD COLUMN conversation_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conv_time ON messages (conversation_id, created_at)"
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5)

    # ---- shaping ----------------------------------------------------------
    @staticmethod
    def _summary(r: tuple) -> dict[str, object]:
        settings = {**DEFAULT_SETTINGS, **json.loads(r[11])}
        return {
            "id": r[0],
            "title": r[1],
            "title_auto": bool(r[2]),
            "created_at": r[3],
            "updated_at": r[4],
            "last_preview": r[5],
            "message_count": r[6],
            "mode": r[7],
            "pinned": bool(r[8]),
            "archived": bool(r[9]),
            "participants": json.loads(r[10]),
            "settings": settings,
        }

    # ---- CRUD -------------------------------------------------------------
    def create(
        self,
        user_id: int,
        kind: str = "text",
        *,
        participants: list[str] | None = None,
        settings: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if kind not in KINDS:
            raise ConversationError("Invalid chat type.")
        parts = [p for p in PARTICIPANTS if p in (participants or PARTICIPANTS)] or list(PARTICIPANTS)
        now = time.time()
        cid = uuid.uuid4().hex[:12]
        merged = self._merged_settings(dict(DEFAULT_SETTINGS), settings or {})
        if kind == "collab":
            merged["ai_collab"] = True
            parts = list(PARTICIPANTS)  # collaboration needs both AIs
        settings = merged
        with closing(self._db()) as db, db:
            db.execute(
                "INSERT INTO conversations (id, user_id, title, title_auto, created_at, updated_at,"
                " mode, participants, settings) VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, user_id, DEFAULT_TITLE, 1, now, now, "voice" if kind == "voice" else "text",
                 json.dumps(parts), json.dumps(settings)),
            )
        return self.get(user_id, cid)

    def get(self, user_id: int, cid: str) -> dict[str, object]:
        with closing(self._db()) as db:
            r = db.execute(
                f"SELECT {_SUMMARY_COLS} FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id)
            ).fetchone()
        if r is None:
            raise ConversationError("Conversation nahi mili.", 404)
        return self._summary(r)

    def owner(self, cid: str) -> int | None:
        with closing(self._db()) as db:
            r = db.execute("SELECT user_id FROM conversations WHERE id = ?", (cid,)).fetchone()
        return None if r is None else int(r[0])

    def list_conversations(self, user_id: int, *, archived: bool = False, limit: int = 30, offset: int = 0) -> dict[str, object]:
        limit = max(1, min(limit, 100))
        with closing(self._db()) as db:
            rows = db.execute(
                f"SELECT {_SUMMARY_COLS} FROM conversations WHERE user_id = ? AND archived = ?"
                " ORDER BY (CASE WHEN archived = 0 THEN pinned ELSE 0 END) DESC, updated_at DESC"
                " LIMIT ? OFFSET ?",
                (user_id, int(archived), limit + 1, max(0, offset)),
            ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) FROM conversations WHERE user_id = ? AND archived = ?", (user_id, int(archived))
            ).fetchone()[0]
        return {
            "conversations": [self._summary(r) for r in rows[:limit]],
            "has_more": len(rows) > limit,
            "total": total,
        }

    def update(self, user_id: int, cid: str, patch: dict[str, object]) -> dict[str, object]:
        cur = self.get(user_id, cid)
        sets: dict[str, object] = {}
        if "title" in patch:
            title = " ".join(str(patch["title"]).split())
            if not title:
                raise ConversationError("Title khaali nahi ho sakta.")
            sets["title"] = title[:TITLE_MAX]
            sets["title_auto"] = 0
        if "pinned" in patch:
            sets["pinned"] = int(bool(patch["pinned"]))
        if "archived" in patch:
            sets["archived"] = int(bool(patch["archived"]))
            if patch["archived"]:
                sets["pinned"] = 0  # an archived chat is not pinned
        if "mode" in patch:
            if patch["mode"] not in ("text", "voice"):
                raise ConversationError("Invalid mode.")
            sets["mode"] = patch["mode"]
        if "participants" in patch:
            parts = [p for p in PARTICIPANTS if p in (patch["participants"] or [])]
            if not parts:
                raise ConversationError("Kam se kam ek AI chunna zaroori hai.")
            sets["participants"] = json.dumps(parts)
        if "settings" in patch:
            sets["settings"] = json.dumps(self._merged_settings(dict(cur["settings"]), patch["settings"]))
        if not sets:
            return cur
        cols = ", ".join(f"{k} = ?" for k in sets)
        with closing(self._db()) as db, db:
            db.execute(f"UPDATE conversations SET {cols} WHERE id = ? AND user_id = ?", (*sets.values(), cid, user_id))
        return self.get(user_id, cid)

    @staticmethod
    def _merged_settings(current: dict[str, object], patch: object) -> dict[str, object]:
        if not isinstance(patch, dict):
            raise ConversationError("Invalid settings.")
        out = dict(current)
        for key, val in patch.items():
            if key in ("ai_collab", "voice_enabled"):
                out[key] = bool(val)
            elif key == "language" and val in LANGUAGES:
                out[key] = val
            elif key == "reply_length" and val in REPLY_LENGTHS:
                out[key] = val
            elif key == "response_pref" and val in RESPONSE_PREFS:
                out[key] = val
            else:
                raise ConversationError(f"Invalid setting: {key}")
        return out

    def merge_settings(self, cid: str, patch: dict[str, object]) -> None:
        """Worker-side settings write (e.g. the AI Collaboration toggle)."""
        with closing(self._db()) as db, db:
            r = db.execute("SELECT settings FROM conversations WHERE id = ?", (cid,)).fetchone()
            if r is None:
                return
            merged = self._merged_settings({**DEFAULT_SETTINGS, **json.loads(r[0])}, patch)
            db.execute("UPDATE conversations SET settings = ? WHERE id = ?", (json.dumps(merged), cid))

    def settings_of(self, cid: str) -> dict[str, object] | None:
        with closing(self._db()) as db:
            r = db.execute("SELECT settings FROM conversations WHERE id = ?", (cid,)).fetchone()
        return None if r is None else {**DEFAULT_SETTINGS, **json.loads(r[0])}

    def participants_of(self, cid: str) -> list[str]:
        with closing(self._db()) as db:
            r = db.execute("SELECT participants FROM conversations WHERE id = ?", (cid,)).fetchone()
        return list(PARTICIPANTS) if r is None else json.loads(r[0])

    def delete(self, user_id: int, cid: str) -> None:
        self.get(user_id, cid)  # ownership / existence
        with closing(self._db()) as db, db:
            db.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
            db.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id))

    def clear(self, user_id: int, cid: str) -> dict[str, object]:
        """Remove the messages but keep the conversation (title, pin, settings)."""
        self.get(user_id, cid)
        with closing(self._db()) as db, db:
            db.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
            db.execute("UPDATE conversations SET message_count = 0, last_preview = '' WHERE id = ?", (cid,))
        return self.get(user_id, cid)

    # ---- messages ---------------------------------------------------------
    def add_turn(self, cid: str, turn: Turn) -> dict[str, object] | None:
        """Persist (insert or update) one committed turn and refresh sidebar metadata.

        Returns ``{"title": ..., "title_changed": bool}``, or None if the turn is
        empty / the conversation no longer exists (e.g. deleted mid-reply).
        """
        row = _row(turn)
        if row is None:
            return None
        with closing(self._db()) as db, db:
            # The worker saves the human turn and the bot reply from two threads: take the write
            # lock first so the title / count read-modify-write cannot interleave.
            db.execute("BEGIN IMMEDIATE")
            conv = db.execute(
                "SELECT title, title_auto, mode FROM conversations WHERE id = ?", (cid,)
            ).fetchone()
            if conv is None:
                return None
            title, title_auto, mode = conv
            existed = db.execute("SELECT 1 FROM messages WHERE turn_id = ?", (row["turn_id"],)).fetchone()
            db.execute(
                "INSERT INTO messages (turn_id, room, role, bot, identity, name, text, source,"
                " language, interrupted, created_at, conversation_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(turn_id) DO UPDATE SET text=excluded.text, interrupted=excluded.interrupted",
                (row["turn_id"], self._room, row["role"], row["bot"], row["identity"], row["name"],
                 row["text"], row["source"], row["language"], int(bool(row["interrupted"])),
                 row["created_at"], cid),
            )
            new_title = title
            if not existed and turn.role is TurnRole.HUMAN and title_auto and title in UNTITLED:
                derived = derive_title(turn.text)
                if derived:
                    new_title = derived
                elif title == DEFAULT_TITLE:
                    new_title = FALLBACK_TITLE
            new_mode = "voice" if (not existed and turn.source is TurnSource.VOICE) else mode
            if existed:
                db.execute(
                    "UPDATE conversations SET last_preview = ? WHERE id = ? AND ? >= updated_at",
                    (_preview(str(row["text"])), cid, row["created_at"]),
                )
            else:
                db.execute(
                    "UPDATE conversations SET message_count = message_count + 1, updated_at = ?,"
                    " last_preview = ?, title = ?, mode = ? WHERE id = ?",
                    (row["created_at"], _preview(str(row["text"])), new_title, new_mode, cid),
                )
        return {"title": new_title, "title_changed": new_title != title}

    def set_auto_title(self, cid: str, expected: str, new_title: str) -> bool:
        """Refine an auto title (LLM), unless the user renamed it meanwhile."""
        new_title = " ".join(new_title.split())[:TITLE_MAX]
        if not new_title:
            return False
        with closing(self._db()) as db, db:
            cur = db.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title_auto = 1 AND title = ?",
                (new_title, cid, expected),
            )
        return cur.rowcount > 0

    def messages(
        self, user_id: int, cid: str, *, limit: int = 50, before: float | None = None
    ) -> dict[str, object]:
        self.get(user_id, cid)
        limit = max(1, min(limit, 200))
        args: list[object] = [cid]
        cond = ""
        if before is not None:
            cond = " AND created_at < ?"
            args.append(before)
        with closing(self._db()) as db:
            rows = db.execute(
                "SELECT turn_id, role, bot, identity, name, text, source, language, interrupted, created_at"
                f" FROM messages WHERE conversation_id = ?{cond} ORDER BY created_at DESC LIMIT ?",
                (*args, limit + 1),
            ).fetchall()
        out = [dict(zip(_KEYS, r, strict=True)) for r in reversed(rows[:limit])]
        for m in out:
            m["interrupted"] = bool(m["interrupted"])
        return {"messages": out, "has_more": len(rows) > limit}

    def recent_turns(self, cid: str, limit: int = 20) -> list[Turn]:
        """The latest turns as ``Turn`` objects, to restore the bots' context."""
        with closing(self._db()) as db:
            rows = db.execute(
                "SELECT turn_id, role, bot, identity, name, text, source, language, interrupted, created_at"
                " FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
                (cid, max(1, min(limit, 200))),
            ).fetchall()
        turns = []
        for r in reversed(rows):
            m = dict(zip(_KEYS, r, strict=True))
            turns.append(
                Turn(
                    role=TurnRole(m["role"]), text=str(m["text"]), speaker_identity=str(m["identity"]),
                    speaker_name=str(m["name"]), source=TurnSource(m["source"]),
                    bot_id=BotId(m["bot"]) if m["bot"] else None, language=m["language"],
                    created_at=float(m["created_at"]), turn_id=str(m["turn_id"]),
                    interrupted=bool(m["interrupted"]), conversation_id=cid,
                )
            )
        return turns

    # ---- search / export --------------------------------------------------
    def search(self, user_id: int, q: str, limit: int = 30) -> list[dict[str, object]]:
        q = q.strip()
        if not q:
            return []
        like = _like(q)
        found: dict[str, dict[str, object]] = {}
        with closing(self._db()) as db:
            for r in db.execute(
                "SELECT id, title, updated_at, archived, last_preview, pinned FROM conversations"
                " WHERE user_id = ? AND title LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT ?",
                (user_id, like, limit),
            ):
                found[r[0]] = {"id": r[0], "title": r[1], "updated_at": r[2], "archived": bool(r[3]),
                               "snippet": r[4], "matched": "title", "pinned": bool(r[5])}
            for r in db.execute(
                "SELECT c.id, c.title, c.updated_at, c.archived, m.text, c.pinned FROM messages m"
                " JOIN conversations c ON c.id = m.conversation_id"
                " WHERE c.user_id = ? AND m.text LIKE ? ESCAPE '\\' ORDER BY m.created_at DESC LIMIT 200",
                (user_id, like),
            ):
                if r[0] not in found:
                    found[r[0]] = {"id": r[0], "title": r[1], "updated_at": r[2], "archived": bool(r[3]),
                                   "snippet": _snippet(r[4], q), "matched": "message", "pinned": bool(r[5])}
                elif found[r[0]]["matched"] == "title" and not found[r[0]]["snippet"]:
                    found[r[0]]["snippet"] = _snippet(r[4], q)
        return sorted(found.values(), key=lambda c: -float(c["updated_at"]))[:limit]  # type: ignore[arg-type]

    def export(self, user_id: int, cid: str, fmt: str) -> tuple[str, str, str]:
        """(filename, media type, body) for txt / md / json."""
        if fmt not in ("txt", "md", "json"):
            raise ConversationError("Format txt, md ya json hona chahiye.")
        conv = self.get(user_id, cid)
        msgs = self.messages(user_id, cid, limit=200)["messages"]
        # Full export: page back until everything is included.
        page = self.messages(user_id, cid, limit=200)
        while page["has_more"]:
            page = self.messages(user_id, cid, limit=200, before=float(page["messages"][0]["created_at"]))  # type: ignore[index]
            msgs = [*page["messages"], *msgs]  # type: ignore[misc]
        stamp = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(t)))  # noqa: E731
        who = lambda m: m["name"] if m["role"] == "human" else str(m["name"] or "AI")  # noqa: E731
        slug = re.sub(r"[^A-Za-z0-9]+", "-", str(conv["title"])).strip("-").lower() or "conversation"
        parts = ", ".join(p.capitalize() for p in conv["participants"])  # type: ignore[union-attr]
        if fmt == "json":
            body = json.dumps({"conversation": conv, "messages": msgs}, ensure_ascii=False, indent=2)
            return f"{slug}.json", "application/json", body
        if fmt == "md":
            lines = [f"# {conv['title']}", "", f"- Date: {stamp(conv['created_at'])}",  # type: ignore[arg-type]
                     f"- AI participants: {parts}", f"- Messages: {len(msgs)}", ""]
            for m in msgs:
                lines += [f"**{who(m)}** · {stamp(m['created_at'])}", "", str(m["text"]), ""]
            return f"{slug}.md", "text/markdown", "\n".join(lines)
        lines = [str(conv["title"]), f"Date: {stamp(conv['created_at'])}", f"AI participants: {parts}", "-" * 40]  # type: ignore[arg-type]
        for m in msgs:
            lines.append(f"[{stamp(m['created_at'])}] {who(m)}: {m['text']}")
        return f"{slug}.txt", "text/plain", "\n".join(lines)
