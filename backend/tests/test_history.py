"""Chat history persistence: SQLite, MongoDB (fake collection) and fallback."""

from __future__ import annotations

from app.config.settings import Settings
from app.context.manager import RoomContextManager
from app.history import (
    MongoChatHistory,
    SQLiteChatHistory,
    build_history,
)
from app.models.conversation import Turn, TurnRole, TurnSource
from app.models.participant import BotId


def _human(text: str, identity: str = "u1-aaaa", at: float = 1.0) -> Turn:
    return Turn(
        role=TurnRole.HUMAN, text=text, speaker_identity=identity, speaker_name="Anshu",
        source=TurnSource.TEXT, created_at=at,
    )


def _bot(text: str, at: float = 2.0) -> Turn:
    return Turn(
        role=TurnRole.BOT, text=text, speaker_identity="roxstar-dost", speaker_name="AI Dost",
        source=TurnSource.TEXT, bot_id=BotId.DOST, created_at=at,
    )


class _FakeCursor(list):
    def sort(self, key, direction):
        return _FakeCursor(sorted(self, key=lambda d: d[key], reverse=direction < 0))

    def limit(self, n):
        return _FakeCursor(self[:n])


class _FakeCollection:
    """Just enough of pymongo's Collection for MongoChatHistory."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def update_one(self, flt, update, upsert=False):
        doc = self.docs.setdefault(flt["_id"], {"_id": flt["_id"]})
        doc.update(update["$set"])

    def find(self, flt):
        return _FakeCursor(d for d in self.docs.values() if d["room"] == flt["room"])


def _mongo(room: str = "r", col: _FakeCollection | None = None) -> MongoChatHistory:
    h = MongoChatHistory.__new__(MongoChatHistory)
    h._room = room
    h._col = col or _FakeCollection()
    return h


def test_sqlite_roundtrip_and_order(tmp_path):
    h = SQLiteChatHistory(tmp_path / "chat.db", "room")
    h.save(_bot("second", at=2.0))
    h.save(_human("first", at=1.0))
    msgs = h.recent(10)
    assert [m["text"] for m in msgs] == ["first", "second"]
    assert msgs[1]["bot"] == "dost" and msgs[0]["role"] == "human"


def test_sqlite_persists_across_instances(tmp_path):
    SQLiteChatHistory(tmp_path / "chat.db", "room").save(_human("hello"))
    again = SQLiteChatHistory(tmp_path / "chat.db", "room")
    assert [m["text"] for m in again.recent(10)] == ["hello"]


def test_rooms_are_isolated(tmp_path):
    a = SQLiteChatHistory(tmp_path / "chat.db", "a")
    b = SQLiteChatHistory(tmp_path / "chat.db", "b")
    a.save(_human("in a"))
    assert b.recent(10) == []


def test_save_updates_existing_turn(tmp_path):
    h = SQLiteChatHistory(tmp_path / "chat.db", "room")
    t = _bot("long reply that got cut")
    h.save(t)
    t.text, t.interrupted = "long reply", True
    h.save(t)
    (m,) = h.recent(10)
    assert m["text"] == "long reply" and m["interrupted"] is True


def test_empty_text_not_saved(tmp_path):
    h = SQLiteChatHistory(tmp_path / "chat.db", "room")
    h.save(_human("   "))
    assert h.recent(10) == []


def test_limit_returns_newest_oldest_first(tmp_path):
    h = SQLiteChatHistory(tmp_path / "chat.db", "room")
    for i in range(5):
        h.save(_human(f"m{i}", at=float(i)))
    assert [m["text"] for m in h.recent(2)] == ["m3", "m4"]


def test_mongo_backend_roundtrip():
    h = _mongo()
    h.save(_bot("b", at=2.0))
    h.save(_human("a", at=1.0))
    msgs = h.recent(10)
    assert [m["text"] for m in msgs] == ["a", "b"]
    assert set(msgs[0]) >= {"turn_id", "role", "identity", "name", "text", "created_at"}
    # Upsert keeps one document per turn.
    t = _bot("b2", at=2.0)
    t.turn_id = msgs[1]["turn_id"]
    h.save(t)
    assert len(h._col.docs) == 2


def test_mongo_rooms_are_isolated():
    col = _FakeCollection()
    a, b = _mongo("a", col), _mongo("b", col)
    a.save(_human("in a"))
    assert b.recent(10) == []


def test_save_failure_never_raises():
    class Boom(_FakeCollection):
        def update_one(self, *a, **k):
            raise RuntimeError("network down")

    _mongo(col=Boom()).save(_human("x"))  # must not raise


def test_recent_turns_failure_returns_empty():
    class Boom(_FakeCollection):
        def find(self, *a, **k):
            raise RuntimeError("network down")

    assert _mongo(col=Boom()).recent_turns(5) == []


def test_unreachable_mongo_falls_back_to_sqlite(tmp_path):
    s = Settings(
        _env_file=None,
        mongodb_uri="mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=200",
        history_db_path=tmp_path / "chat.db",
    )
    assert build_history(s, "room").kind == "sqlite"


def test_no_uri_uses_sqlite(tmp_path):
    s = Settings(_env_file=None, mongodb_uri="", history_db_path=tmp_path / "chat.db")
    assert build_history(s, "room").kind == "sqlite"


def test_context_restore_is_silent_and_visible_to_bots(tmp_path):
    h = SQLiteChatHistory(tmp_path / "chat.db", "room")
    h.save(_human("cloud computing kya hai", at=1.0))
    h.save(_bot("Cloud matlab internet par servers.", at=2.0))

    ctx = RoomContextManager()
    seen: list[Turn] = []
    ctx.add_listener(seen.append)
    ctx.restore_turns(h.recent_turns(20))

    assert seen == []  # nothing re-published / re-saved
    assert [t.text for t in ctx.recent_turns()][-2:] == [
        "cloud computing kya hai",
        "Cloud matlab internet par servers.",
    ]
