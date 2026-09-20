"""Conversation-engine tests: the real orchestrator pipeline with offline providers.

Everything between "message arrives" and "reply delivered" is the production code
(normalize -> intent -> context -> router -> LLM -> chat -> TTS -> audio source). Only the
edges are faked: the LLM is scripted, audio goes to a counting fake source instead of LiveKit,
and chat/state publishing is captured. "Audio was published" == frames reached the source.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from itertools import pairwise

import pytest
import pytest_asyncio

from app.config.settings import Settings
from app.models.conversation import TurnSource
from app.models.participant import BotId, BotState
from app.orchestrator import RoomOrchestrator
from app.pipeline.llm import FailingLLMProvider, LLMMessage, ScriptedLLMProvider
from app.pipeline.tts import FailingTTSProvider, SilentTTSProvider

REPLY = "Haan bhai, theek hoon. Tum batao kya chal raha hai. Sab badhiya hai na."


class CountingSource:
    def __init__(self) -> None:
        self.frames = 0

    async def capture_frame(self, frame) -> None:
        self.frames += 1

    def clear_queue(self) -> None:
        pass


class CountingTTS(SilentTTSProvider):
    """Silent audio that counts how many synthesis requests were made."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.requests: list[str] = []

    def synthesize(self, text: str):
        self.requests.append(text)
        return super().synthesize(text)


class Harness:
    def __init__(self, o: RoomOrchestrator) -> None:
        self.o = o
        self.chat: list[tuple[str, str]] = []  # (bot, text)
        self.sources = {b: CountingSource() for b in BotId}
        self.states: list[tuple[str, str]] = []
        for bot, agent in o.agents.items():
            agent._source = self.sources[bot]
            agent._tts = CountingTTS(realtime=False)
            agent.state_listener = self._on_state
            agent.send_chat = self._make_chat(bot)  # type: ignore[method-assign]

    def _on_state(self, bot: BotId, state: BotState) -> None:
        self.states.append((bot.value, state.value))
        self.o._on_agent_state(bot, state)

    def _make_chat(self, bot: BotId):
        async def send_chat(text: str) -> None:
            self.chat.append((bot.value, text))

        return send_chat

    def set_llm(self, llm) -> None:
        for agent in self.o.agents.values():
            agent._llm = llm
        self.o.llm = llm

    @property
    def frames(self) -> int:
        return sum(s.frames for s in self.sources.values())

    def synth_requests(self, bot: BotId | None = None) -> int:
        bots = [bot] if bot else list(BotId)
        return sum(len(self.o.agents[b]._tts.requests) for b in bots)  # type: ignore[attr-defined]

    async def settle(self, max_wait: float = 8.0) -> None:
        end = asyncio.get_running_loop().time() + max_wait
        quiet = 0
        while asyncio.get_running_loop().time() < end:
            await asyncio.sleep(0.05)
            quiet = 0 if self.o.turns.busy else quiet + 1
            if quiet >= 6:
                return
        raise AssertionError("pipeline never went idle")

    async def text(self, text: str, who: str = "u1-aaaa", name: str = "Rahul") -> list[tuple[str, str]]:
        n = len(self.chat)
        await self.o._handle_chat(who, name, text)
        await self.settle()
        return self.chat[n:]

    async def voice(self, text: str, who: str = "u1-aaaa", name: str = "Rahul") -> list[tuple[str, str]]:
        n = len(self.chat)
        await self.o._process_utterance(
            text=text, speaker_identity=who, speaker_name=name, source=TurnSource.VOICE,
            speech_end_at=None, stt_final_at=0.0,
        )
        await self.settle()
        return self.chat[n:]


class ReplyLLM(ScriptedLLMProvider):
    def __init__(self, reply: str = REPLY) -> None:
        super().__init__([reply])

    async def generate(self, messages: Sequence[LLMMessage], **kw) -> str:
        self.calls.append(list(messages))
        return self._replies[0]

    def system_of(self, i: int = -1) -> str:
        return "\n".join(m.content for m in self.calls[i] if m.role == "system")


@pytest_asyncio.fixture
async def h(tmp_path):
    s = Settings(
        livekit_url="wss://x", livekit_api_key="k", livekit_api_secret="s" * 32,
        tts_provider="silent", stt_provider="null", openrouter_api_key="",
        history_db_path=tmp_path / "chat.db", auth_db_path=tmp_path / "u.db",
    )
    harness = Harness(RoomOrchestrator(settings=s))
    harness.set_llm(ReplyLLM())
    return harness


# ---- the four combinations ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_text_to_text(h):
    out = await h.text("Hi")
    assert len(out) == 1 and h.frames == 0 and h.synth_requests() == 0


@pytest.mark.asyncio
async def test_text_to_voice(h):
    out = await h.text("Hi, talk to me in voice.")
    assert len(out) == 1  # the text reply is always delivered
    assert h.frames > 0 and h.synth_requests() > 0  # ...and real audio frames reached the room
    assert ("dost", "speaking") in h.states or ("sathi", "speaking") in h.states


@pytest.mark.asyncio
async def test_voice_to_voice_when_voice_mode_is_on(h):
    h.o._voice_users.add("u1-aaaa")
    out = await h.voice("How are you?")
    assert len(out) == 1 and h.frames > 0


@pytest.mark.asyncio
async def test_voice_to_text_when_asked_not_to_speak(h):
    h.o._voice_users.add("u1-aaaa")
    out = await h.voice("Don't speak, just type.")
    assert len(out) == 1 and h.frames == 0 and h.synth_requests() == 0
    assert h.o._response_pref == "text"


@pytest.mark.asyncio
async def test_voice_input_without_voice_mode_is_still_answered_in_text(h):
    """inputMode (voice) and responseMode are independent: nobody asked for audio."""
    out = await h.voice("How are you?")
    assert len(out) == 1 and h.frames == 0


# ---- preference lifecycle ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_preference_is_sticky_and_reversible(h):
    await h.text("mujhse baat karo")
    assert h.o._response_pref == "voice" and h.frames > 0
    before = h.frames
    await h.text("aur batao")  # nothing said about output: still voice
    assert h.frames > before
    before = h.frames
    await h.text("Now only text.")
    await h.text("theek hai phir")
    assert h.o._response_pref == "text" and h.frames == before


@pytest.mark.asyncio
async def test_one_shot_request_does_not_change_the_preference(h):
    await h.text("bol ke batao AI kya hai")
    assert h.frames > 0 and h.o._response_pref == "auto"
    before = h.frames
    await h.text("aur ek example")
    assert h.frames == before


@pytest.mark.asyncio
async def test_false_positive_mentions_stay_text(h):
    for msg in ("What is voice AI?", "How does TTS work?", "Why isn't voice working?"):
        await h.text(msg)
    assert h.frames == 0 and h.synth_requests() == 0


@pytest.mark.asyncio
async def test_voice_button_beats_earlier_text_only_and_stop_voice_ends_voice_pref(h):
    await h.text("only text")
    assert h.o._response_pref == "text"
    await h.o._handle_control({"type": "voice_mode", "enabled": True}, "u1-aaaa")
    assert h.o._response_pref == "auto" and "u1-aaaa" in h.o._voice_users
    await h.text("mujhse baat karo")
    assert h.o._response_pref == "voice"
    await h.o._handle_control({"type": "voice_mode", "enabled": False}, "u1-aaaa")
    assert h.o._response_pref == "auto"


# ---- language ---------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reply_language_follows_the_user(h):
    llm = h.o.llm
    await h.text("How are you?")
    assert "English" in llm.system_of()  # type: ignore[attr-defined]
    await h.text("bhai kya haal hai")
    assert "English mein" not in llm.system_of().split("Style")[-1] or "Hinglish" in llm.system_of()  # type: ignore[attr-defined]
    await h.text("Respond in English.")
    assert "explicitly asked for English" in llm.system_of()  # type: ignore[attr-defined]
    await h.text("Hindi mein samjhao.")
    assert "Hindi" in llm.system_of()  # type: ignore[attr-defined]
    assert h.frames == 0  # language requests never turn audio on


# ---- routing ------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_named_bot_answers_alone(h):
    out = await h.text("AI Dost tum answer karo.")
    assert [b for b, _ in out] == ["dost"]
    out = await h.text("AI Sathi example do.")
    assert [b for b, _ in out] == ["sathi"]


@pytest.mark.asyncio
async def test_generic_message_gets_exactly_one_bot(h):
    out = await h.text("cloud computing kya hota hai")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_participants_setting_limits_who_can_answer(h):
    conv = h.o.conversations.create(1, participants=["sathi"])
    await h.o._activate_conversation("u1-aaaa", conv["id"])
    out = await h.text("AI Dost tum answer karo.")
    assert [b for b, _ in out] == ["sathi"]


@pytest.mark.asyncio
async def test_ai_to_ai_is_bounded_and_off_by_default(h):
    out = await h.text("Dost, Sathi se poocho AI kya hota hai")
    assert len(out) == 1  # collaboration is off: nobody answers an AI
    h.o.ai_mode = True
    out = await h.text("Dost, Sathi se poocho cloud kya hota hai")
    assert 2 <= len(out) <= 6
    assert all(a[0] != b[0] for a, b in pairwise(out))  # strictly alternating
    assert h.frames == 0  # ...and text mode means the chain is silent too


@pytest.mark.asyncio
async def test_ai_to_ai_speaks_only_for_a_user_who_wants_voice(h):
    h.o.ai_mode = True
    await h.text("mujhse baat karo, Dost Sathi se poocho AI kya hai")
    assert h.frames > 0
    assert h.synth_requests(BotId.DOST) > 0 and h.synth_requests(BotId.SATHI) > 0


# ---- barge-in --------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_new_text_message_interrupts_speech_and_replaces_it(h):
    for bot in BotId:
        h.o.agents[bot]._tts = CountingTTS(realtime=True, words_per_minute=60)  # slow: stays speaking
    h.set_llm(ReplyLLM("Machine learning ek bada topic hai jismein bahut kuch aata hai. " * 3))
    await h.o._handle_chat("u1-aaaa", "Rahul", "Dost, bol ke batao machine learning kya hai")
    for _ in range(100):
        await asyncio.sleep(0.05)
        if h.frames > 3:
            break
    assert h.frames > 3
    await h.o._handle_chat("u1-aaaa", "Rahul", "Dost, ruko. Simple example do. likh ke batao")
    await h.settle(15)
    assert any(s == "interrupted" for _, s in h.states)
    turns = h.o.context.recent_turns()
    assert any(t.interrupted for t in turns if t.bot_id)  # the cut-off reply is marked INTERRUPTED
    assert h.chat[-1][0] == "dost"  # ...and the new request got its answer


# ---- failures ---------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tts_failure_keeps_the_text_and_records_a_retryable_error(h):
    for bot in BotId:
        h.o.agents[bot]._tts = FailingTTSProvider(frames_before_failure=0)
    out = await h.text("Hi, talk to me in voice.")
    assert any(text == REPLY for _, text in out)  # the answer was not lost
    assert h.o._last_error and h.o._last_error["kind"] == "tts"


@pytest.mark.asyncio
async def test_text_mode_never_touches_tts_so_it_cannot_report_tts_errors(h):
    for bot in BotId:
        h.o.agents[bot]._tts = FailingTTSProvider(frames_before_failure=0)
    await h.text("Hi")
    assert h.o._last_error is None


@pytest.mark.asyncio
async def test_retry_voice_respeaks_the_last_reply(h):
    await h.text("Hi")
    assert h.frames == 0
    await h.o._handle_control({"type": "retry_voice"}, "u1-aaaa")  # not in voice mode: ignored
    await h.settle()
    assert h.frames == 0
    h.o._voice_users.add("u1-aaaa")
    await h.o._handle_control({"type": "retry_voice"}, "u1-aaaa")
    await h.settle()
    assert h.frames > 0


@pytest.mark.asyncio
async def test_llm_failure_ends_in_a_visible_error_not_silence(h):
    h.set_llm(FailingLLMProvider())
    out = await h.text("Hi")
    assert len(out) == 1  # in-character fallback reply is delivered
    assert h.o._last_error and h.o._last_error["kind"] == "llm"


@pytest.mark.asyncio
async def test_stt_failure_is_reported_to_the_ui_and_text_chat_keeps_working(h):
    await h.o._handle_stt_failure("u1-aaaa", "stt_stream_failed")
    assert h.o._last_error and h.o._last_error["kind"] == "stt"
    assert len(await h.text("Hi")) == 1


# ---- duplicates -------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_voice_transcript_gets_one_reply(h):
    h.o._voice_users.add("u1-aaaa")
    first = await h.voice("How are you?")
    second = await h.voice("How are you?")
    assert len(first) == 1 and len(second) == 0  # the repeated transcript is suppressed
    reqs = h.o.agents[BotId.DOST]._tts.requests + h.o.agents[BotId.SATHI]._tts.requests  # type: ignore[attr-defined]
    assert reqs and len(reqs) == len(set(reqs))  # no chunk was synthesized twice


@pytest.mark.asyncio
async def test_each_sentence_is_synthesized_once(h):
    await h.text("bol ke batao hi")
    reqs = h.o.agents[BotId.DOST]._tts.requests + h.o.agents[BotId.SATHI]._tts.requests  # type: ignore[attr-defined]
    assert reqs and len(reqs) == len(set(reqs))  # no duplicate TTS for the same text


# ---- multi-user context ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_speaker_facts_are_not_attributed_to_other_humans(h):
    """Rahul's stated facts live in HIS speaker-memory block, given only when he asks about himself;
    Priya's prompt never carries them, and the shared history always names who said what."""
    llm = h.o.llm

    def private_block(call_index: int) -> str:
        system = "\n".join(m.content for m in llm.calls[call_index] if m.role == "system")  # type: ignore[attr-defined]
        return system.split("ke baare mein jo pata hai - private]")[1] if "ke baare mein jo pata hai - private]" in system else ""

    await h.text("Mera naam Rahul hai aur mujhe cricket pasand hai.", who="u1-aaaa", name="Rahul")
    await h.text("Aaj mausam kaisa hai?", who="u2-bbbb", name="Priya")
    assert "cricket" not in private_block(-1) and "Rahul" not in private_block(-1)
    history = "\n".join(m.content for m in llm.calls[-1] if m.role != "system")  # type: ignore[attr-defined]
    assert "Rahul: Mera naam Rahul" in history and "Priya: Aaj mausam" in history  # attributed by name
    await h.text("Maine apne baare mein kya bataya tha?", who="u1-aaaa", name="Rahul")
    assert "cricket" in private_block(-1)


# ---- conversations: reopen safety & persistence ----------------------------------------------------------
@pytest.mark.asyncio
async def test_reopening_a_voice_conversation_never_speaks(h):
    conv = h.o.conversations.create(1, "voice")
    h.o.conversations.merge_settings(conv["id"], {"response_pref": "voice"})  # was a "talk to me in voice" chat
    h.o._voice_users.add("u1-aaaa")
    await h.o._activate_conversation("u1-aaaa", conv["id"])
    assert h.o._voice_users == set() and h.o._response_pref == "auto"
    out = await h.text("hello again")
    assert len(out) == 1 and h.frames == 0


@pytest.mark.asyncio
async def test_messages_are_persisted_with_correlation_ids(h):
    conv = h.o.conversations.create(1)
    await h.o._activate_conversation("u1-aaaa", conv["id"])
    await h.text("Explain machine learning in simple Hindi")
    await asyncio.sleep(0.3)
    saved = h.o.conversations.messages(1, conv["id"])["messages"]
    assert [m["role"] for m in saved] == ["human", "bot"]
    turns = h.o.context.recent_turns()
    bot_turn = next(t for t in turns if t.bot_id)
    human_turn = next(t for t in turns if t.is_human)
    assert bot_turn.request_id == human_turn.turn_id  # requestId -> responseId correlation
    assert bot_turn.response_mode == "text"
    assert h.o.conversations.get(1, conv["id"])["title"] != "New Chat"


@pytest.mark.asyncio
async def test_cannot_activate_another_users_conversation(h):
    conv = h.o.conversations.create(2)
    await h.o._activate_conversation("u1-aaaa", conv["id"])
    assert h.o.active_conversation is None


@pytest.mark.asyncio
async def test_the_model_is_told_how_its_reply_is_delivered_so_it_never_denies_voice(h):
    llm = h.o.llm
    await h.text("Hindi mein samjhao.")
    assert "text mein dikhega" in llm.system_of() and "voice available nahi hai" in llm.system_of()  # type: ignore[attr-defined]
    await h.text("Hi, talk to me in voice.")
    assert "awaaz mein bhi sunaya jayega" in llm.system_of()  # type: ignore[attr-defined]
