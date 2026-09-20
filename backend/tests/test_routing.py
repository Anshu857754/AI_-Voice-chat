"""Bot router: exactly one bot selected, deterministic rule priority.

Mirrors the required demo scenarios in the spec (section 20) plus the
duplicate-response and self-echo guards from section 8/15.
"""

from __future__ import annotations

import time

import pytest

from app.models.conversation import RoutingReason
from app.models.participant import BotId
from app.routing.router import BotRouter, RouterState


@pytest.fixture
def router() -> BotRouter:
    return BotRouter(min_question_chars=3, followup_window_s=45.0)


@pytest.fixture
def now() -> float:
    return time.time()


class TestExplicitAddressing:
    def test_explicit_dost(self, router, now):
        d = router.route("AI Dost, tum answer karo.", speaker_identity="rahul", state=RouterState(), now=now)
        assert d.should_respond and d.selected_bot is BotId.DOST
        assert d.reason is RoutingReason.EXPLICIT_ADDRESS
        assert d.queued_bots == ()

    def test_explicit_sathi(self, router, now):
        d = router.route("AI Sathi, ek example do.", speaker_identity="rahul", state=RouterState(), now=now)
        assert d.should_respond and d.selected_bot is BotId.SATHI
        assert d.reason is RoutingReason.EXPLICIT_ADDRESS

    def test_explicit_multi_address_sequential_no_overlap(self, router, now):
        """'AI Dost, tum answer karo. AI Sathi, baad mein ek example dena.'
        Dost answers first; Sathi is queued, never concurrent."""
        d = router.route(
            "AI Dost, tum answer karo. AI Sathi, baad mein ek example dena.",
            speaker_identity="rahul",
            state=RouterState(),
            now=now,
        )
        assert d.selected_bot is BotId.DOST
        assert d.queued_bots == (BotId.SATHI,)
        assert d.reason is RoutingReason.EXPLICIT_MULTI_ADDRESS
        # Exactly one bot is "selected" for immediate generation.
        assert d.all_bots == (BotId.DOST, BotId.SATHI)


class TestRelevanceGate:
    def test_fresh_question_gets_a_single_bot(self, router, now):
        d = router.route("AI kya hota hai?", speaker_identity="rahul", state=RouterState(), now=now)
        assert d.should_respond
        assert d.selected_bot in (BotId.DOST, BotId.SATHI)

    @pytest.mark.parametrize(
        "text", ["Waise aaj weather kaafi achha hai.", "haan", "thanks"]
    )
    def test_irrelevant_sentences_suppressed(self, router, now, text):
        d = router.route(text, speaker_identity="rahul", state=RouterState(), now=now)
        assert d.should_respond is False
        assert d.selected_bot is None


class TestContinuityAndFollowup:
    def test_followup_continues_with_thread_owner_even_for_different_speaker(self, router, now):
        """Rahul asks, Dost answers. Priya's 'thoda aur simple batao' continues
        with Dost - multi-user shared context, not per-speaker isolation."""
        state = RouterState(
            last_responder=BotId.DOST, last_responder_at=now, has_active_topic=True
        )
        d = router.route("Thoda aur simple batao", speaker_identity="priya", state=state, now=now)
        assert d.selected_bot is BotId.DOST
        assert d.reason is RoutingReason.CONVERSATION_CONTINUITY

    def test_pronoun_followup_resolves_to_thread_owner(self, router, now):
        state = RouterState(
            last_responder=BotId.SATHI, last_responder_at=now, has_active_topic=True
        )
        d = router.route("Unki koi famous movie batao.", speaker_identity="rahul", state=state, now=now)
        assert d.selected_bot is BotId.SATHI
        assert d.reason is RoutingReason.CONVERSATION_CONTINUITY

    def test_followup_outside_window_falls_back(self, router):
        old = time.time() - 1000
        state = RouterState(last_responder=BotId.DOST, last_responder_at=old, has_active_topic=True)
        d = router.route("Thoda aur simple batao", speaker_identity="rahul", state=state, now=old + 2000)
        # Stale thread: continuity rule should not fire (falls through to
        # turn-taking, which still answers because "batao" is a request).
        assert d.reason is not RoutingReason.CONVERSATION_CONTINUITY


class TestInterruptionRouting:
    def test_barge_in_redirects_to_speaking_bot(self, router, now):
        state = RouterState(
            last_responder=BotId.DOST,
            last_responder_at=now,
            speaking_bot=BotId.DOST,
            has_active_topic=True,
        )
        d = router.route("Ruko, simple example se samjhao.", speaker_identity="rahul", state=state, now=now)
        assert d.selected_bot is BotId.DOST
        assert d.is_interruption is True
        assert d.reason is RoutingReason.INTERRUPTION_REDIRECT


class TestMemoryAndSelfDisclosureRouting:
    def test_memory_query_goes_to_thread_owner(self, router, now):
        state = RouterState(last_responder=BotId.SATHI, last_responder_at=now)
        d = router.route(
            "Maine apne baare mein kya bataya tha?", speaker_identity="rahul", state=state, now=now
        )
        assert d.should_respond
        assert d.reason is RoutingReason.MEMORY_QUERY

    def test_self_disclosure_gets_acknowledged(self, router, now):
        d = router.route(
            "Mera naam Rahul hai aur mujhe cricket pasand hai.",
            speaker_identity="rahul",
            state=RouterState(),
            now=now,
        )
        assert d.should_respond
        assert d.reason is RoutingReason.SELF_DISCLOSURE


class TestPersonaRouting:
    def test_tech_topic_leans_dost(self, router, now):
        d = router.route(
            "kubernetes aur docker server pe kaise deploy karte hain?",
            speaker_identity="rahul",
            state=RouterState(),
            now=now,
        )
        assert d.selected_bot is BotId.DOST
        assert d.reason is RoutingReason.PERSONA_TOPIC

    def test_lifestyle_topic_leans_sathi(self, router, now):
        d = router.route(
            "koi achhi bollywood movie aur song batao",
            speaker_identity="rahul",
            state=RouterState(),
            now=now,
        )
        assert d.selected_bot is BotId.SATHI
        assert d.reason is RoutingReason.PERSONA_TOPIC


class TestTurnTakingFallback:
    def test_sticks_with_one_bot_across_unrelated_fresh_questions(self, router):
        """One voice per conversation: default Dost, then whoever spoke last."""
        questions = [
            "Photosynthesis kya hai?",
            "Bank interest rate kaise decide hota hai?",
            "Traffic signal kaun banata hai?",
        ]
        state = RouterState()
        selected = []
        base = time.time()
        for i, q in enumerate(questions):
            d = router.route(q, speaker_identity="rahul", state=state, now=base + i * 120)
            selected.append(d.selected_bot)
            state = RouterState(
                last_responder=d.selected_bot, last_responder_at=base + i * 120, turn_counter=i + 1
            )
        assert selected == [BotId.DOST] * 3

    def test_greeting_gets_exactly_one_reply(self, router, now):
        d = router.route("hello", speaker_identity="rahul", state=RouterState(), now=now)
        assert d.should_respond and d.selected_bot is BotId.DOST and not d.queued_bots


class TestDuplicateAndSelfEchoGuards:
    def test_duplicate_within_window_suppressed(self, router, now):
        state = RouterState(recent_human_utterances=(("rahul", "ai kya hota hai", now - 1.0),))
        d = router.route("AI kya hota hai?", speaker_identity="rahul", state=state, now=now)
        assert d.should_respond is False
        assert d.reason is RoutingReason.DUPLICATE_SUPPRESSED

    def test_bot_transcript_never_routes(self, router, now):
        """A bot's own audio must never re-enter routing (would cause bots
        to talk to each other forever)."""
        d = router.route(
            "AI ek technology hai", speaker_identity="roxstar-ai-dost", state=RouterState(), now=now
        )
        assert d.should_respond is False
        assert d.reason is RoutingReason.BOT_SELF_ECHO

    def test_too_short_ignored(self, router, now):
        d = router.route("hm", speaker_identity="rahul", state=RouterState(), now=now)
        assert d.should_respond is False


class TestInvariant:
    def test_no_response_means_no_bot_and_no_queue(self):
        from app.models.conversation import RoutingDecision

        d = RoutingDecision(
            should_respond=False, selected_bot=BotId.DOST, reason=RoutingReason.SMALL_TALK
        )
        assert d.selected_bot is None
        assert d.queued_bots == ()
        assert d.all_bots == ()
