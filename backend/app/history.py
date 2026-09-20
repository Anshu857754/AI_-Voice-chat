"""Persistent chat history.

Every committed turn - human or bot, typed or spoken - is saved so the
conversation survives a page reload, a re-login and a worker restart.

* Backend: MongoDB Atlas when ``MONGODB_URI`` is set; otherwise a local
  SQLite file, so the app still works with no database configured.
* The agent worker writes (context listener) and, on startup, reloads the
  recent turns into the shared context so the bots remember the thread.
* The API process reads it for ``GET /history``.

Saving never raises: losing a history row must not break a live conversation.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from app.models.conversation import Turn, TurnRole, TurnSource
from app.models.participant import BotId
from app.utils.logging import TAG_CONTEXT, get_logger

if TYPE_CHECKING:
    from app.config.settings import Settings

log = get_logger(__name__)

_KEYS = (
    "turn_id", "role", "bot", "identity", "name", "text", "source",
    "language", "interrupted", "created_at",
)


def _row(turn: Turn) -> dict[str, object] | None:
    text = turn.text.strip()
    if not text:
        return None
    return {
        "turn_id": turn.turn_id,
        "role": turn.role.value,
        "bot": turn.bot_id.value if turn.bot_id else None,
        "identity": turn.speaker_identity,
        "name": turn.speaker_name,
        "text": text,
        "source": turn.source.value,
        "language": turn.language,
        "interrupted": bool(turn.interrupted),
        "created_at": turn.created_at,
    }


class ChatHistory(ABC):
    kind: str = "abstract"

    def __init__(self, room: str) -> None:
        self._room = room

    @abstractmethod
    def _insert(self, row: dict[str, object]) -> None: ...

    @abstractmethod
    def _query(self, limit: int) -> list[dict[str, object]]: ...

    def save(self, turn: Turn) -> None:
        """Insert a turn, or update it (e.g. text trimmed after a barge-in)."""
        row = _row(turn)
        if row is None:
            return
        try:
            self._insert(row)
        except Exception as exc:
            log.error(tag=TAG_CONTEXT, event="history_save_failed", backend=self.kind, error=repr(exc))

    def recent(self, limit: int = 100) -> list[dict[str, object]]:
        """Newest ``limit`` messages, oldest first."""
        return self._query(max(1, min(limit, 500)))

    def recent_turns(self, limit: int = 20) -> list[Turn]:
        """The same rows as ``Turn`` objects, for restoring the bots' context."""
        try:
            rows = self.recent(limit)
        except Exception as exc:
            log.error(tag=TAG_CONTEXT, event="history_load_failed", backend=self.kind, error=repr(exc))
            return []
        return [
            Turn(
                role=TurnRole(m["role"]),
                text=str(m["text"]),
                speaker_identity=str(m["identity"]),
                speaker_name=str(m["name"]),
                source=TurnSource(m["source"]),
                bot_id=BotId(m["bot"]) if m["bot"] else None,
                language=m["language"],  # type: ignore[arg-type]
                created_at=float(m["created_at"]),  # type: ignore[arg-type]
                turn_id=str(m["turn_id"]),
                interrupted=bool(m["interrupted"]),
            )
            for m in rows
        ]


class SQLiteChatHistory(ChatHistory):
    kind = "sqlite"

    def __init__(self, path: Path | str, room: str) -> None:
        super().__init__(room)
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "turn_id TEXT PRIMARY KEY, room TEXT NOT NULL, role TEXT NOT NULL,"
                "bot TEXT, identity TEXT NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL,"
                "source TEXT NOT NULL, language TEXT, interrupted INTEGER NOT NULL DEFAULT 0,"
                "created_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_room_time ON messages (room, created_at)"
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5)

    def _insert(self, row: dict[str, object]) -> None:
        with closing(self._db()) as db, db:
            db.execute(
                "INSERT INTO messages (turn_id, room, role, bot, identity, name, text, source,"
                " language, interrupted, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(turn_id) DO UPDATE SET text=excluded.text,"
                " interrupted=excluded.interrupted",
                (row["turn_id"], self._room, row["role"], row["bot"], row["identity"],
                 row["name"], row["text"], row["source"], row["language"],
                 int(bool(row["interrupted"])), row["created_at"]),
            )

    def _query(self, limit: int) -> list[dict[str, object]]:
        with closing(self._db()) as db:
            rows = db.execute(
                "SELECT turn_id, role, bot, identity, name, text, source, language,"
                " interrupted, created_at FROM messages WHERE room = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (self._room, limit),
            ).fetchall()
        out = [dict(zip(_KEYS, r, strict=True)) for r in reversed(rows)]
        for m in out:
            m["interrupted"] = bool(m["interrupted"])
        return out


class MongoChatHistory(ChatHistory):
    """MongoDB Atlas backend. One document per turn, ``_id`` = turn_id."""

    kind = "mongodb"

    def __init__(self, uri: str, db_name: str, room: str, *, collection: str = "messages") -> None:
        super().__init__(room)
        from pymongo import ASCENDING, MongoClient

        # Short timeouts: an unreachable cluster must degrade quickly, not hang a turn.
        self._client: MongoClient = MongoClient(
            uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, appname="roxstar-ai"
        )
        self._col = self._client[db_name][collection]
        self._col.create_index([("room", ASCENDING), ("created_at", ASCENDING)])

    def ping(self) -> None:
        self._client.admin.command("ping")

    def _insert(self, row: dict[str, object]) -> None:
        doc = {k: v for k, v in row.items() if k != "turn_id"}
        self._col.update_one(
            {"_id": row["turn_id"]}, {"$set": {**doc, "room": self._room}}, upsert=True
        )

    def _query(self, limit: int) -> list[dict[str, object]]:
        docs = list(self._col.find({"room": self._room}).sort("created_at", -1).limit(limit))
        out: list[dict[str, object]] = []
        for d in reversed(docs):
            m = {k: d.get(k) for k in _KEYS if k != "turn_id"}
            m["turn_id"] = d["_id"]
            m["interrupted"] = bool(m.get("interrupted"))
            out.append({k: m[k] for k in _KEYS})
        return out


def build_history(settings: Settings, room: str) -> ChatHistory:
    """MongoDB Atlas if configured and reachable, else local SQLite."""
    if settings.mongodb_uri:
        try:
            mongo = MongoChatHistory(settings.mongodb_uri, settings.mongodb_db, room)
            mongo.ping()
            log.stage(TAG_CONTEXT, event="history_backend", backend="mongodb", db=settings.mongodb_db)
            return mongo
        except Exception as exc:
            # Never log the URI: it contains the password.
            log.error(
                tag=TAG_CONTEXT, event="mongodb_unavailable",
                error=type(exc).__name__, fallback="sqlite",
            )
    log.stage(TAG_CONTEXT, event="history_backend", backend="sqlite")
    return SQLiteChatHistory(settings.history_db_path, room)
