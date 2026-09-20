"""RoomContextManager: shared transcript, prompt assembly, window management.

Covers: voice+text landing in one context, per-speaker memory scoping inside
the assembled prompt, pronoun/topic continuity, and the recent-window +
rolling-summary strategy from docs/context-management.md.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config.settings import Settings
from app.context.manager import RoomContextManager
from app.context.summarizer import ConversationSummarizer
from app.models.conversation import TurnSource
from app.models.participant import BotId, ParticipantRole, Speaker
from app.pipeline.llm import ScriptedLLMProvider


def _settings(**overrides) -> Settings:
    base = dict(
        livekit_url="wss://test.invalid",
        livekit_api_key="k",
        livekit_api_secret="s",
        context_recent_turns=6,
        context_summary_trigger=8,
        context_summary_keep=4,
    )
    base.update(overrides)
    return Settings(**base)


def _manager(**overrides) -> RoomContextManager:
    return RoomContextManager(settings=_settings(**overrides))


class TestSharedTranscript:
    def test_voice_and_text_share_one_context(self):
        """Requirement: no separate context system for text vs voice."""
        ctx = _manager()
        ctx.add_human_turn(
            text="AI kya hota hai?", speaker_identity="rahul", speaker_name="Rahul",
            source=TurnSource.VOICE,
        )
        ctx.add_human_turn(
            text="Thoda aur simple batao", speaker_identity="rahul", speaker_name="Rahul",
            source=TurnSource.TEXT,
        )
        recent = ctx.recent_turns()
        assert len(recent) == 2
        assert {t.source for t in recent} == {TurnSource.VOICE, TurnSource.TEXT}

    def test_bot_turn_recorded_with_bot_id(self):
        ctx = _manager()
        ctx.add_bot_turn(text="Bilkul, ye simple hai.", bot=BotId.DOST)
        turn = ctx.recent_turns()[0]
        assert turn.bot_id is BotId.DOST
        assert turn.speaker_name == "Roxstar AI Dost"


class TestPromptAssembly:
    def test_only_asking_speakers_memory_is_included(self):
        """The injected 'known facts about X' block must be scoped to the
        asking speaker only. Note this is distinct from the shared transcript
        window, which legitimately includes what every human actually said -
        that is the multi-user context requirement, not a leak."""
        ctx = _manager()
        ctx.add_human_turn(
            text="Mera naam Rahul hai aur mujhe cricket pasand hai.",
            speaker_identity="u_rahul", speaker_name="Rahul",
        )
        ctx.add_human_turn(
            text="Mera naam Priya hai aur mujhe music pasand hai.",
            speaker_identity="u_priya", speaker_name="Priya",
        )
        bundle = ctx.build_messages(
            bot=BotId.DOST,
            system_prompt="persona",
            asking_identity="u_rahul",
        )
        system = bundle.messages[0].content
        facts_block = system[system.index("jo pata hai") :]
        assert "cricket" in facts_block
        assert "music" not in facts_block
        assert "Priya" not in facts_block
        assert bundle.speaker_facts_used is True

    def test_other_bots_replies_are_not_this_bots_assistant_voice(self):
        """Sathi's prior turns must appear as context, not as Dost's own
        assistant history - otherwise Dost would "remember" saying Sathi's words."""
        ctx = _manager()
        ctx.add_human_turn(text="AI kya hota hai?", speaker_identity="rahul", speaker_name="Rahul")
        ctx.add_bot_turn(text="Ye Sathi ka jawab hai.", bot=BotId.SATHI)
        bundle = ctx.build_messages(bot=BotId.DOST, system_prompt="p", asking_identity="rahul")
        assistant_msgs = [m for m in bundle.messages if m.role == "assistant"]
        assert not any("Sathi ka jawab" in m.content for m in assistant_msgs)
        user_msgs = [m for m in bundle.messages if m.role == "user"]
        assert any("Sathi ka jawab" in m.content for m in user_msgs)

    def test_own_prior_replies_are_assistant_turns(self):
        ctx = _manager()
        ctx.add_human_turn(text="AI kya hota hai?", speaker_identity="rahul", speaker_name="Rahul")
        ctx.add_bot_turn(text="AI ek technology hai.", bot=BotId.DOST)
        bundle = ctx.build_messages(bot=BotId.DOST, system_prompt="p", asking_identity="rahul")
        assistant_msgs = [m for m in bundle.messages if m.role == "assistant"]
        assert any("AI ek technology hai" in m.content for m in assistant_msgs)


class TestContinuity:
    def test_active_topic_survives_followup_turns(self):
        """Scenario 3: 'Unki famous movie batao' must resolve against the
        earlier 'Shah Rukh Khan' topic, which a follow-up must not overwrite."""
        ctx = _manager()
        ctx.add_human_turn(
            text="Shah Rukh Khan ke baare mein batao.",
            speaker_identity="rahul", speaker_name="Rahul",
        )
        ctx.add_bot_turn(text="Shah Rukh Khan ek famous actor hain.", bot=BotId.DOST)
        ctx.add_human_turn(
            text="Unki koi famous movie batao.", speaker_identity="rahul", speaker_name="Rahul",
        )
        bundle = ctx.build_messages(bot=BotId.DOST, system_prompt="p", asking_identity="rahul")
        blob = "\n".join(m.content for m in bundle.messages)
        assert "Shah Rukh Khan" in blob

    def test_memory_query_does_not_overwrite_the_active_topic(self):
        """Regression test: a memory-recall question ('Maine apne baare mein
        kya bataya tha?') has is_question forced True regardless of phrasing,
        but it is about the conversation's own history, not a new subject.
        Letting it overwrite _current_topic derailed a later 'give an
        example' request onto the asker's own facts instead of the actual
        topic being discussed (caught by running the live demo pipeline)."""
        ctx = _manager()
        ctx.add_human_turn(
            text="Machine learning kya hota hai?", speaker_identity="rahul", speaker_name="Rahul"
        )
        ctx.add_bot_turn(text="ML matlab data se seekhna.", bot=BotId.SATHI)
        ctx.add_human_turn(
            text="Mera naam Rahul hai aur mujhe cricket pasand hai.",
            speaker_identity="rahul", speaker_name="Rahul",
        )
        ctx.add_human_turn(
            text="Maine apne baare mein kya bataya tha?",
            speaker_identity="rahul", speaker_name="Rahul",
        )
        state = ctx.router_state()
        assert state.has_active_topic is True
        bundle = ctx.build_messages(bot=BotId.SATHI, system_prompt="p", asking_identity="rahul")
        topic_msg = next(
            (m for m in bundle.messages if "abhi ki baat" in m.content.lower()), None
        )
        assert topic_msg is not None
        assert "Machine learning" in topic_msg.content
        assert "Maine apne baare" not in topic_msg.content

    def test_router_state_reflects_last_responder(self):
        ctx = _manager()
        ctx.add_human_turn(text="AI kya hota hai?", speaker_identity="rahul", speaker_name="Rahul")
        ctx.add_bot_turn(text="AI ek technology hai.", bot=BotId.DOST)
        state = ctx.router_state()
        assert state.last_responder is BotId.DOST
        assert state.has_active_topic is True

    def test_router_state_excludes_the_turn_currently_being_routed(self):
        """Regression test for a real bug: the orchestrator's call order is
        always add_human_turn() THEN router_state() THEN router.route() for
        that same turn. Without excluding the just-added turn by id,
        recent_human_utterances would contain the current utterance itself -
        same speaker, same normalized text, ~0s elapsed - and the router's
        duplicate-suppression rule would flag every single turn as a
        duplicate of itself, silencing the room entirely. This exercises the
        exact call sequence app/orchestrator.py uses."""
        ctx = _manager()
        turn, _utterance = ctx.add_human_turn(
            text="AI kya hota hai?", speaker_identity="rahul", speaker_name="Rahul"
        )
        state = ctx.router_state(exclude_turn_id=turn.turn_id)
        assert state.recent_human_utterances == ()

        from app.routing.router import BotRouter

        router = BotRouter()
        decision = router.route("AI kya hota hai?", speaker_identity="rahul", state=state)
        assert decision.should_respond is True
        assert decision.reason.value != "duplicate_suppressed"

    def test_a_genuinely_repeated_utterance_is_still_suppressed(self):
        """The exclude_turn_id fix must not defeat duplicate suppression for
        an actual repeat - only the self-comparison bug is fixed."""
        ctx = _manager()
        ctx.add_human_turn(
            text="AI kya hota hai?", speaker_identity="rahul", speaker_name="Rahul"
        )
        t2, _ = ctx.add_human_turn(
            text="AI kya hota hai?", speaker_identity="rahul", speaker_name="Rahul"
        )
        state = ctx.router_state(exclude_turn_id=t2.turn_id)
        assert len(state.recent_human_utterances) == 1
        assert state.recent_human_utterances[0][0] == "rahul"


class TestWindowManagement:
    def test_recent_window_is_bounded(self):
        ctx = _manager(context_recent_turns=4)
        for i in range(10):
            ctx.add_human_turn(
                text=f"Ye sawaal number {i} hai kya", speaker_identity="rahul", speaker_name="Rahul"
            )
        assert len(ctx.recent_turns()) == 4

    def test_full_transcript_is_retained_beyond_the_window(self):
        ctx = _manager(context_recent_turns=4)
        for i in range(10):
            ctx.add_human_turn(
                text=f"Ye sawaal number {i} hai kya", speaker_identity="rahul", speaker_name="Rahul"
            )
        assert len(ctx.transcript(limit=100)) == 10


class TestRollingSummary:
    @pytest.mark.asyncio
    async def test_old_turns_get_folded_into_a_summary(self):
        llm = ScriptedLLMProvider(replies=["Rahul ne AI ke baare mein poocha."])
        summarizer = ConversationSummarizer(llm, max_chars=500)
        ctx = _manager(context_recent_turns=3, context_summary_trigger=5, context_summary_keep=2)
        ctx._summarizer = summarizer  # inject after construction (test-only)

        for i in range(7):
            ctx.add_human_turn(
                text=f"Sawaal number {i} kya hai", speaker_identity="rahul", speaker_name="Rahul"
            )
            await asyncio.sleep(0)  # let the background summarization task run

        await ctx.flush_summary()
        assert ctx.summary != ""
        assert len(llm.calls) >= 1

    @pytest.mark.asyncio
    async def test_summary_survives_llm_failure_via_extractive_fallback(self):
        from app.pipeline.llm import FailingLLMProvider

        summarizer = ConversationSummarizer(FailingLLMProvider(), timeout_s=2.0)
        ctx = _manager(context_recent_turns=3, context_summary_trigger=5, context_summary_keep=2)
        ctx._summarizer = summarizer

        for i in range(7):
            ctx.add_human_turn(
                text=f"Sawaal number {i} kya hai", speaker_identity="rahul", speaker_name="Rahul"
            )
            await asyncio.sleep(0)

        await ctx.flush_summary()
        # Extractive fallback still produces something usable, never a crash.
        assert ctx.summary != ""


class TestParticipantRegistration:
    def test_register_and_lookup(self):
        ctx = _manager()
        ctx.register_speaker(Speaker(identity="u_rahul", name="Rahul", role=ParticipantRole.HUMAN))
        assert ctx.speaker_name("u_rahul") == "Rahul"
        assert len(ctx.human_speakers) == 1
