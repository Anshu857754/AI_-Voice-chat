"""Voice state machine: "speaking" is only ever reported while audio is really being published."""

import pytest
from livekit import rtc

from app.agents.base import BotAgent, BotIdentityConfig
from app.context.manager import ContextBundle
from app.models.conversation import LatencyTrace
from app.models.participant import BotId, BotState
from app.pipeline.llm import LLMMessage, ScriptedLLMProvider
from app.pipeline.tts import SilentTTSProvider
from app.routing.turn_manager import CancellationToken


class Source:
    def __init__(self) -> None:
        self.frames = 0

    async def capture_frame(self, frame) -> None:
        self.frames += 1

    def clear_queue(self) -> None:
        pass


def _bundle() -> ContextBundle:
    msgs = [LLMMessage(role="system", content="p"), LLMMessage(role="user", content="hi")]
    return ContextBundle(messages=msgs, recent_turns_used=1, summary_used=False, speaker_facts_used=False, total_chars=3)


def _agent(source: Source | None) -> tuple[BotAgent, list[tuple[BotState, int]]]:
    cfg = BotIdentityConfig(bot_id=BotId.DOST, identity="d", display_name="Dost", voice_label="m")
    agent = BotAgent(
        config=cfg, llm=ScriptedLLMProvider(["Pehla jumla yahan hai. Doosra jumla bhi aa gaya."]),
        tts=SilentTTSProvider(realtime=False), room=rtc.Room(),
    )
    agent._source = source
    seen: list[tuple[BotState, int]] = []
    agent.state_listener = lambda _b, st: seen.append((st, source.frames if source else 0))
    return agent, seen


@pytest.mark.asyncio
async def test_speaking_is_claimed_only_after_the_first_frame_is_published():
    src = Source()
    agent, seen = _agent(src)
    await agent.respond(bundle=_bundle(), trace=LatencyTrace(turn_id="t", bot_id=BotId.DOST), token=CancellationToken(), speak=True)
    order = [s for s, _ in seen]
    assert order.index(BotState.THINKING) < order.index(BotState.GENERATING) < order.index(BotState.SYNTHESIZING) < order.index(BotState.SPEAKING)
    frames_when_speaking = next(f for s, f in seen if s is BotState.SPEAKING)
    frames_when_synthesizing = next(f for s, f in seen if s is BotState.SYNTHESIZING)
    assert frames_when_synthesizing == 0  # "Preparing voice": nothing published yet
    assert frames_when_speaking >= 1  # "Speaking": audio really reached the room


@pytest.mark.asyncio
async def test_text_only_reply_never_reports_synthesizing_or_speaking():
    src = Source()
    agent, seen = _agent(src)
    result = await agent.respond(bundle=_bundle(), trace=LatencyTrace(turn_id="t", bot_id=BotId.DOST), token=CancellationToken(), speak=False)
    states = {s for s, _ in seen}
    assert BotState.SPEAKING not in states and BotState.SYNTHESIZING not in states
    assert src.frames == 0 and result.text_generated


@pytest.mark.asyncio
async def test_no_audio_path_never_fakes_speaking():
    agent, seen = _agent(None)  # e.g. not connected to a room
    await agent.respond(bundle=_bundle(), trace=LatencyTrace(turn_id="t", bot_id=BotId.DOST), token=CancellationToken(), speak=True)
    assert BotState.SPEAKING not in {s for s, _ in seen}


@pytest.mark.asyncio
async def test_muting_mid_speech_stops_audio_but_keeps_the_whole_text():
    class MutingSource(Source):
        agent: BotAgent | None = None

        async def capture_frame(self, frame) -> None:
            await super().capture_frame(frame)
            if self.frames == 3 and self.agent is not None:
                await self.agent.mute_audio()  # the user pressed Stop Voice while it was speaking

    src = MutingSource()
    agent, _ = _agent(src)
    src.agent = agent
    result = await agent.respond(bundle=_bundle(), trace=LatencyTrace(turn_id="t", bot_id=BotId.DOST), token=CancellationToken(), speak=True)
    assert src.frames < 10  # audio stopped almost at once (a full clip is ~180 frames)
    assert result.text_generated == "Pehla jumla yahan hai. Doosra jumla bhi aa gaya."  # nothing lost from the text
    assert result.interrupted is False
