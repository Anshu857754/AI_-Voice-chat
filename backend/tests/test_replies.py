"""Reply quality plumbing: post-processing, history ordering, length hints."""

from app.agents.base import clean_reply, strip_opening
from app.agents.prompts import DOST_PERSONA, SATHI_PERSONA, max_sentences, render_persona, wants_detail
from app.context.manager import RoomContextManager
from app.models.participant import BotId


def test_filler_opening_and_label_stripped():
    assert strip_opening("Dekho, basically, AI ek technology hai.") == "AI ek technology hai."
    assert strip_opening("AI Dost: Haan bolo.") == "Haan bolo."
    assert strip_opening("Bilkul, main hoon.") == "Bilkul, main hoon."


def test_markdown_and_emoji_removed():
    assert clean_reply("**Cloud** matlab server 😀\n- ek\n- do") == "Cloud matlab server ek do"


def test_personas_are_distinct_and_rendered_with_names():
    dost = render_persona(DOST_PERSONA, bot_value="dost", user_name="Anshu")
    sathi = render_persona(SATHI_PERSONA, bot_value="sathi", user_name="Anshu")
    assert "AI Dost" in dost and "Anshu" in dost and "{" not in dost
    assert "AI Sathi" in sathi and dost != sathi
    assert max_sentences("sathi") >= max_sentences("dost")


def test_detail_cue():
    assert wants_detail("thoda detail mein samjhao")
    assert not wants_detail("kya hal h")


def test_history_ends_with_latest_human_turn_even_if_bot_reply_lands_late():
    ctx = RoomContextManager()
    ctx.add_human_turn(text="hello", speaker_identity="u", speaker_name="Anshu")
    ctx.add_human_turn(text="kya hal h", speaker_identity="u", speaker_name="Anshu")
    ctx.add_bot_turn(text="Hey!", bot=BotId.DOST)  # committed after the 2nd human turn
    msgs = ctx.build_messages(bot=BotId.DOST, system_prompt=DOST_PERSONA, asking_identity="u").messages
    assert msgs[0].role == "system" and len(msgs[0].content) > 0
    assert msgs[-1].role == "user" and msgs[-1].content.endswith("kya hal h")
    assert sum(m.role == "system" for m in msgs) == 1


def test_internal_background_never_uses_room_or_topic_words_outside_the_private_marker():
    ctx = RoomContextManager()
    ctx.add_human_turn(text="cloud computing kya hai", speaker_identity="u", speaker_name="Anshu")
    system = ctx.build_messages(bot=BotId.SATHI, system_prompt=SATHI_PERSONA, asking_identity="u").messages[0].content
    assert "[ROOM CONTEXT]" not in system and "Room ka current topic" not in system
    assert "PRIVATE BACKGROUND" in system
