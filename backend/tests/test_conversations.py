import time

import pytest

from app.conversations import ConversationError, ConversationStore
from app.models.conversation import Turn, TurnRole, TurnSource
from app.models.participant import BotId


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "chat.db", "room")


def human(text, *, t=None, source=TurnSource.TEXT, conv=None):
    return Turn(role=TurnRole.HUMAN, text=text, speaker_identity="u1-abc", speaker_name="Anshu",
                source=source, created_at=t or time.time(), conversation_id=conv)


def bot(text, *, t=None, who=BotId.DOST):
    return Turn(role=TurnRole.BOT, text=text, speaker_identity="roxstar-ai-dost", speaker_name="AI Dost",
                source=TurnSource.TEXT, bot_id=who, created_at=t or time.time())


def test_new_chat_is_untitled_and_empty(store):
    c = store.create(1)
    assert c["title"] == "New Chat" and c["message_count"] == 0
    assert c["participants"] == ["dost", "sathi"] and c["pinned"] is False and c["archived"] is False


def test_chat_types(store):
    assert store.create(1, "collab")["settings"]["ai_collab"] is True
    assert store.create(1, "voice")["mode"] == "voice"
    with pytest.raises(ConversationError):
        store.create(1, "nope")


def test_first_meaningful_message_titles_the_chat_and_updates_preview(store):
    c = store.create(1)
    r = store.add_turn(c["id"], human("Explain machine learning in simple Hindi"))
    assert r["title"] == "Machine Learning in Hindi" and r["title_changed"]
    store.add_turn(c["id"], bot("Machine learning matlab computer ko examples se sikhana."))
    got = store.get(1, c["id"])
    assert got["title"] == "Machine Learning in Hindi"
    assert got["message_count"] == 2
    assert got["last_preview"].startswith("Machine learning matlab")


def test_greeting_gives_new_conversation_then_a_real_message_retitles(store):
    c = store.create(1)
    store.add_turn(c["id"], human("hi"))
    assert store.get(1, c["id"])["title"] == "New Conversation"
    store.add_turn(c["id"], human("Help me plan my React project"))
    assert store.get(1, c["id"])["title"] == "Plan my React Project"
    store.add_turn(c["id"], human("Ab Docker ke baare mein bataao details"))
    assert store.get(1, c["id"])["title"] == "Plan my React Project"  # settled


def test_manual_rename_is_never_overwritten(store):
    c = store.create(1)
    store.update(1, c["id"], {"title": "My project"})
    store.add_turn(c["id"], human("Explain machine learning in simple Hindi"))
    assert store.get(1, c["id"])["title"] == "My project"
    assert not store.set_auto_title(c["id"], "Machine Learning", "Other")


def test_same_turn_saved_twice_is_one_message(store):
    c = store.create(1)
    t = human("kya haal hai bhai kaise chal raha hai sab")
    store.add_turn(c["id"], t)
    t.text = "edited text"
    store.add_turn(c["id"], t)
    assert store.get(1, c["id"])["message_count"] == 1
    assert store.messages(1, c["id"])["messages"][0]["text"] == "edited text"


def test_voice_message_marks_conversation_voice(store):
    c = store.create(1)
    store.add_turn(c["id"], human("Dost please tell me about python", source=TurnSource.VOICE))
    assert store.get(1, c["id"])["mode"] == "voice"


def test_sorting_pinned_first_then_recent(store):
    a, b, c = (store.create(1) for _ in range(3))
    now = time.time()
    store.add_turn(a["id"], human("alpha topic discussion here", t=now - 300))
    store.add_turn(b["id"], human("beta topic discussion here", t=now - 200))
    store.add_turn(c["id"], human("gamma topic discussion here", t=now - 100))
    order = [x["id"] for x in store.list_conversations(1)["conversations"]]
    assert order == [c["id"], b["id"], a["id"]]
    store.update(1, a["id"], {"pinned": True})
    order = [x["id"] for x in store.list_conversations(1)["conversations"]]
    assert order == [a["id"], c["id"], b["id"]]
    store.add_turn(b["id"], bot("newest activity", t=now))
    assert [x["id"] for x in store.list_conversations(1)["conversations"]] == [a["id"], b["id"], c["id"]]


def test_archive_hides_from_recent_and_unpins_restore_brings_back(store):
    c = store.create(1)
    store.update(1, c["id"], {"pinned": True})
    store.update(1, c["id"], {"archived": True})
    assert store.list_conversations(1)["total"] == 0
    arch = store.list_conversations(1, archived=True)["conversations"]
    assert [x["id"] for x in arch] == [c["id"]] and arch[0]["pinned"] is False
    store.update(1, c["id"], {"archived": False})
    assert store.list_conversations(1)["total"] == 1


def test_pagination(store):
    for _ in range(7):
        store.create(1)
    p1 = store.list_conversations(1, limit=3)
    assert len(p1["conversations"]) == 3 and p1["has_more"] and p1["total"] == 7
    p3 = store.list_conversations(1, limit=3, offset=6)
    assert len(p3["conversations"]) == 1 and not p3["has_more"]


def test_users_cannot_see_or_touch_each_others_chats(store):
    c = store.create(1)
    store.add_turn(c["id"], human("private discussion about salaries"))
    assert store.list_conversations(2)["total"] == 0
    for fn in (
        lambda: store.get(2, c["id"]),
        lambda: store.update(2, c["id"], {"title": "x"}),
        lambda: store.delete(2, c["id"]),
        lambda: store.clear(2, c["id"]),
        lambda: store.messages(2, c["id"]),
        lambda: store.export(2, c["id"], "txt"),
    ):
        with pytest.raises(ConversationError) as e:
            fn()
        assert e.value.status == 404
    assert store.search(2, "salaries") == []


def test_delete_removes_messages_too(store):
    c = store.create(1)
    store.add_turn(c["id"], human("some words to delete later"))
    store.delete(1, c["id"])
    assert store.owner(c["id"]) is None
    assert store.search(1, "delete later") == []
    # a late worker write into a deleted chat is ignored, not resurrected
    assert store.add_turn(c["id"], bot("late reply")) is None


def test_clear_keeps_metadata(store):
    c = store.create(1)
    store.update(1, c["id"], {"title": "Keep me", "pinned": True})
    store.add_turn(c["id"], human("first message about python"))
    got = store.clear(1, c["id"])
    assert got["message_count"] == 0 and got["last_preview"] == ""
    assert got["title"] == "Keep me" and got["pinned"] is True
    assert store.messages(1, c["id"])["messages"] == []


def test_messages_pagination_oldest_first(store):
    c = store.create(1)
    now = time.time()
    for i in range(12):
        store.add_turn(c["id"], human(f"message number {i} here", t=now + i))
    page = store.messages(1, c["id"], limit=5)
    assert [m["text"] for m in page["messages"]][-1].endswith("11 here") and page["has_more"]
    older = store.messages(1, c["id"], limit=5, before=page["messages"][0]["created_at"])
    assert older["messages"][-1]["created_at"] < page["messages"][0]["created_at"]
    assert len(older["messages"]) == 5


def test_search_titles_and_messages_with_snippets_and_escaping(store):
    a, b = store.create(1), store.create(1)
    store.add_turn(a["id"], human("Explain machine learning in simple Hindi"))
    store.add_turn(b["id"], human("random unrelated planning talk"))
    store.add_turn(b["id"], bot("We can use machine learning for that project quickly."))
    res = {r["id"]: r for r in store.search(1, "machine learning")}
    assert set(res) == {a["id"], b["id"]}
    assert res[a["id"]]["matched"] == "title"
    assert "machine learning" in res[b["id"]]["snippet"].lower() and res[b["id"]]["matched"] == "message"
    assert store.search(1, "100%") == []  # % is literal, not a wildcard
    assert store.search(1, "   ") == []


def test_settings_validation_and_merge(store):
    c = store.create(1)
    got = store.update(1, c["id"], {"settings": {"language": "english", "reply_length": "detailed"}})
    assert got["settings"]["language"] == "english" and got["settings"]["ai_collab"] is False
    for bad in ({"language": "klingon"}, {"nonsense": 1}):
        with pytest.raises(ConversationError):
            store.update(1, c["id"], {"settings": bad})
    with pytest.raises(ConversationError):
        store.update(1, c["id"], {"participants": []})
    assert store.update(1, c["id"], {"participants": ["sathi"]})["participants"] == ["sathi"]


def test_recent_turns_round_trip(store):
    c = store.create(1)
    store.add_turn(c["id"], human("hello there friend of mine"))
    store.add_turn(c["id"], bot("Hey!"))
    turns = store.recent_turns(c["id"])
    assert [t.role for t in turns] == [TurnRole.HUMAN, TurnRole.BOT]
    assert turns[1].bot_id is BotId.DOST and turns[0].conversation_id == c["id"]


def test_export_formats(store):
    c = store.create(1)
    store.add_turn(c["id"], human("Explain machine learning in simple Hindi"))
    store.add_turn(c["id"], bot("Machine learning matlab examples se seekhna."))
    name, mime, body = store.export(1, c["id"], "md")
    assert name.endswith(".md") and body.startswith("# Machine Learning in Hindi") and "**AI Dost**" in body
    _, _, txt = store.export(1, c["id"], "txt")
    assert "Anshu:" in txt and "AI participants: Dost, Sathi" in txt
    _, mime, js = store.export(1, c["id"], "json")
    assert mime == "application/json" and '"messages"' in js
    with pytest.raises(ConversationError):
        store.export(1, c["id"], "pdf")
