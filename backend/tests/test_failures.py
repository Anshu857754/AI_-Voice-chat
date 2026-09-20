"""Failure handling: STT, LLM and TTS outages must degrade gracefully, never crash.

Requirement (spec section 17): no stack traces to the user, structured logs
instead, and an in-character spoken fallback for LLM failures.
"""

from __future__ import annotations

import pytest
from livekit import rtc

from app.agents.base import BotAgent, BotIdentityConfig
from app.agents.prompts import failure_reply
from app.context.manager import ContextBundle
from app.models.conversation import LatencyTrace, TurnSource
from app.models.participant import BotId
from app.pipeline.llm import FailingLLMProvider, LLMMessage, ScriptedLLMProvider
from app.pipeline.stt import FailingSTTProvider, STTError
from app.pipeline.tts import FailingTTSProvider, SilentTTSProvider
from app.routing.turn_manager import CancellationToken


def _bundle(text: str = "AI kya hota hai?") -> ContextBundle:
    messages = [
        LLMMessage(role="system", content="persona"),
        LLMMessage(role="user", content=text),
    ]
    return ContextBundle(
        messages=messages, recent_turns_used=1, summary_used=False,
        speaker_facts_used=False, total_chars=sum(len(m.content) for m in messages),
    )


def _agent(llm, tts) -> BotAgent:
    config = BotIdentityConfig(
        bot_id=BotId.DOST, identity="roxstar-ai-dost", display_name="Roxstar AI Dost",
        voice_label="male-indian-hindi",
    )
    return BotAgent(config=config, llm=llm, tts=tts, room=rtc.Room())


class _FakeAudioSource:
    """Stands in for rtc.AudioSource so TTS output can be exercised without a
    real LiveKit connection."""

    def __init__(self) -> None:
        self.frames_captured = 0
        self.cleared = False

    async def capture_frame(self, frame) -> None:
        self.frames_captured += 1

    def clear_queue(self) -> None:
        self.cleared = True


class TestLLMFailure:
    @pytest.mark.asyncio
    async def test_llm_failure_is_contained_not_raised(self):
        agent = _agent(llm=FailingLLMProvider(), tts=SilentTTSProvider(realtime=False))
        trace = LatencyTrace(turn_id="t1", source=TurnSource.VOICE, bot_id=BotId.DOST)
        token = CancellationToken()

        result = await agent.respond(bundle=_bundle(), trace=trace, token=token)

        assert result.llm_failed is True
        assert result.interrupted is False

    @pytest.mark.asyncio
    async def test_llm_failure_has_an_in_character_fallback(self):
        """The user never sees a stack trace; they hear/read a polite retry ask."""
        text = failure_reply("dost")
        assert "issue" in text.lower() or "dobara" in text.lower()
        assert "Traceback" not in text
        assert "Exception" not in text

    @pytest.mark.asyncio
    async def test_agent_recovers_and_speaks_the_fallback(self):
        """Simulates the orchestrator's failure path: LLM fails, the agent is
        asked to speak the canned fallback instead, and that succeeds."""
        agent = _agent(llm=FailingLLMProvider(), tts=SilentTTSProvider(realtime=False))
        trace = LatencyTrace(turn_id="t1", bot_id=BotId.DOST)
        token = CancellationToken()

        first = await agent.respond(bundle=_bundle(), trace=trace, token=token)
        assert first.llm_failed

        fallback_result = await agent.speak_text(
            failure_reply("dost"), trace=trace, token=CancellationToken()
        )
        assert fallback_result.interrupted is False
        assert fallback_result.text_spoken


class TestTTSFailure:
    @pytest.mark.asyncio
    async def test_tts_failure_still_yields_generated_text(self):
        """When synthesis breaks mid-reply, the text itself must survive so the
        caller can still deliver it via chat."""
        agent = _agent(
            llm=ScriptedLLMProvider(["Ye ek chhota jawab hai jo bola jayega poore tarike se."]),
            tts=FailingTTSProvider(frames_before_failure=0),
        )
        agent._source = _FakeAudioSource()  # exercise the TTS path without a real room
        trace = LatencyTrace(turn_id="t2", bot_id=BotId.DOST)
        token = CancellationToken()

        result = await agent.respond(bundle=_bundle(), trace=trace, token=token)

        assert result.tts_failed is True
        assert result.text_generated  # the reply text itself was not lost

    @pytest.mark.asyncio
    async def test_tts_failure_does_not_propagate_as_exception(self):
        agent = _agent(
            llm=ScriptedLLMProvider(["Chhota jawab yahan hai poora."]),
            tts=FailingTTSProvider(frames_before_failure=1),
        )
        agent._source = _FakeAudioSource()
        trace = LatencyTrace(turn_id="t3", bot_id=BotId.DOST)
        token = CancellationToken()

        # Must not raise.
        result = await agent.respond(bundle=_bundle(), trace=trace, token=token)
        assert result.tts_failed is True


class TestSTTFailureIsolation:
    @pytest.mark.asyncio
    async def test_stt_stream_failure_raises_a_typed_error_not_a_crash(self):
        provider = FailingSTTProvider()
        stream = provider.stream(speaker_identity="rahul", speaker_name="Rahul")
        with pytest.raises(STTError):
            async for _ in stream.events():
                pass

    @pytest.mark.asyncio
    async def test_one_speakers_stt_failure_does_not_affect_transcript_shape(self):
        """A downstream consumer sees a clean, typed failure it can catch and
        log, not an unhandled exception that would kill the whole ingress."""
        provider = FailingSTTProvider()
        stream = provider.stream(speaker_identity="rahul", speaker_name="Rahul")
        caught = None
        try:
            async for _ in stream.events():
                pass
        except STTError as exc:
            caught = exc
        assert caught is not None


class TestBargeInDuringFailureDoesNotDeadlock:
    @pytest.mark.asyncio
    async def test_cancellation_during_llm_failure_completes_cleanly(self):
        agent = _agent(llm=FailingLLMProvider(), tts=SilentTTSProvider(realtime=False))
        trace = LatencyTrace(turn_id="t4", bot_id=BotId.DOST)
        token = CancellationToken()
        token.cancel("test")
        result = await agent.respond(bundle=_bundle(), trace=trace, token=token)
        # Either flag may be set depending on race, but it must complete.
        assert result.interrupted or result.llm_failed
