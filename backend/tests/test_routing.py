"""pick_responder: every human message gets exactly one AI, chosen in one place."""

from __future__ import annotations

import time

import pytest

from app.models.conversation import RoutingReason
from app.models.participant import BotId
from app.routing.router import RouterState, pick_responder


@pytest.fixture
def now() -> float:
    return time.time()


def pick(text, state=None, **kw):
    return pick_responder(text, state or RouterState(), speaker_identity="rahul", **kw)


class TestExplicitNames:
    @pytest.mark.parametrize("text", ["AI Dost, tum answer karo.", "dost tum batao", "Dost tum batao"])
    def test_dost_named(self, text):
        d = pick(text)
        assert d.selected_bot is BotId.DOST and d.reason is RoutingReason.EXPLICIT_ADDRESS

    @pytest.mark.parametrize("text", ["AI Sathi, ek example do.", "Sathi tum batao AI kya hota hai"])
    def test_sathi_named_overrides_active_ai(self, text):
        d = pick(text, RouterState(last_responder=BotId.DOST, last_responder_at=time.time()))
        assert d.selected_bot is BotId.SATHI

    def test_both_named_first_one_answers_and_other_stays_silent(self):
        d = pick("AI Dost, tum answer karo. AI Sathi, baad mein ek example dena.")
        assert d.selected_bot is BotId.DOST
        assert d.queued_bots == () and d.all_bots == (BotId.DOST,)
        d = pick("Sathi aur Dost dono batao")
        assert d.selected_bot is BotId.SATHI and d.all_bots == (BotId.SATHI,)


class TestNoName:
    def test_default_is_dost(self):
        d = pick("AI kya hota hai?")
        assert d.should_respond and d.selected_bot is BotId.DOST

    def test_active_ai_keeps_the_conversation(self, now):
        state = RouterState(last_responder=BotId.SATHI, last_responder_at=now)
        for text in ["or btao", "kya ker rhe ho", "theek hai", "hii"]:
            d = pick(text, state)
            assert d.should_respond and d.selected_bot is BotId.SATHI, text

    @pytest.mark.parametrize("text", ["hi", "hii", "hello", "haan", "ok", "thanks", "bye", "a"])
    def test_every_message_gets_a_reply(self, text):
        assert pick(text).should_respond

    def test_no_rotation_between_unrelated_questions(self, now):
        state = RouterState()
        picked = []
        for i, q in enumerate(["Photosynthesis kya hai?", "Bank interest kaise decide hota hai?", "Chai kaise banate hain?"]):
            d = pick(q, state, now=now + i)
            picked.append(d.selected_bot)
            state = RouterState(last_responder=d.selected_bot, last_responder_at=now + i)
        assert picked == [BotId.DOST] * 3

    def test_empty_message_gets_nothing(self):
        assert not pick("   ").should_respond


class TestGuards:
    def test_duplicate_voice_final_suppressed_but_typed_text_is_not(self, now):
        state = RouterState(recent_human_utterances=(("rahul", "ai kya hota hai", now - 1.0),))
        assert not pick("AI kya hota hai?", state, now=now).should_respond
        assert pick("AI kya hota hai?", state, now=now, dedupe=False).should_respond

    def test_ai_messages_never_trigger_a_reply(self):
        from app.config.settings import get_settings

        d = pick_responder("kya haal hai?", RouterState(), speaker_identity=get_settings().dost_identity)
        assert not d.should_respond and d.reason is RoutingReason.BOT_SELF_ECHO

    def test_speaking_ai_means_interruption(self):
        d = pick("ruko, ek sawaal", RouterState(speaking_bot=BotId.DOST))
        assert d.is_interruption


def test_active_ai_is_set_at_selection_so_a_split_sentence_stays_with_it():
    from app.context.manager import RoomContextManager

    ctx = RoomContextManager()
    ctx.add_human_turn(text="Sathi, please tell me,", speaker_identity="u", speaker_name="A")
    ctx.mark_active(BotId.SATHI)  # chosen, reply not finished yet
    turn, _ = ctx.add_human_turn(text="what is AI?", speaker_identity="u", speaker_name="A")
    d = pick_responder("what is AI?", ctx.router_state(exclude_turn_id=turn.turn_id), speaker_identity="u")
    assert d.selected_bot is BotId.SATHI
