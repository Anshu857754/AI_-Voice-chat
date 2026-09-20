"""Barge-in: detection, debouncing, and that cancellation actually frees the floor."""

from __future__ import annotations

import asyncio

import pytest

from app.models.participant import BotId, BotState
from app.pipeline.interruption import InterruptionController, InterruptSignal
from app.routing.turn_manager import TurnManager


class TestDetectionRules:
    def test_vad_requires_a_speaking_bot(self):
        ctrl = InterruptionController(cancel_hook=_noop_cancel)
        assert ctrl.should_interrupt_on_vad(speaking_bot=None, speech_duration_s=1.0) is False
        assert ctrl.should_interrupt_on_vad(speaking_bot=BotId.DOST, speech_duration_s=1.0) is True

    def test_vad_below_threshold_ignored(self):
        ctrl = InterruptionController(cancel_hook=_noop_cancel, min_speech_ms=300)
        assert ctrl.should_interrupt_on_vad(speaking_bot=BotId.DOST, speech_duration_s=0.05) is False

    def test_final_transcript_always_interrupts_a_speaking_bot(self):
        ctrl = InterruptionController(cancel_hook=_noop_cancel)
        assert ctrl.should_interrupt_on_text(
            speaking_bot=BotId.DOST, text="ruko", is_final=True
        )

    def test_single_word_interim_is_not_enough(self):
        ctrl = InterruptionController(cancel_hook=_noop_cancel)
        assert ctrl.should_interrupt_on_text(speaking_bot=BotId.DOST, text="haan", is_final=False) is False

    def test_multiword_interim_counts(self):
        ctrl = InterruptionController(cancel_hook=_noop_cancel)
        assert ctrl.should_interrupt_on_text(
            speaking_bot=BotId.DOST, text="ruko simple", is_final=False
        )

    def test_no_bot_speaking_never_interrupts(self):
        ctrl = InterruptionController(cancel_hook=_noop_cancel)
        assert ctrl.should_interrupt_on_text(speaking_bot=None, text="ruko", is_final=True) is False


class TestTriggerAndDebounce:
    @pytest.mark.asyncio
    async def test_trigger_calls_the_cancel_hook(self):
        calls = []

        async def hook(reason: str) -> bool:
            calls.append(reason)
            return True

        ctrl = InterruptionController(cancel_hook=hook, debounce_s=0.05)
        ev = await ctrl.trigger(
            signal=InterruptSignal.EXPLICIT_CUE,
            speaker_identity="rahul",
            speaking_bot=BotId.DOST,
            text="ruko",
        )
        assert ev is not None
        assert ev.tts_cancelled is True
        assert calls == ["explicit_cue"]
        assert ctrl.interruption_count == 1

    @pytest.mark.asyncio
    async def test_rapid_repeated_triggers_are_debounced(self):
        """Three detection signals firing for one real interruption must
        cancel the floor once, not three times."""
        calls = []

        async def hook(reason: str) -> bool:
            calls.append(reason)
            return True

        ctrl = InterruptionController(cancel_hook=hook, debounce_s=1.0)
        for _ in range(3):
            await ctrl.trigger(
                signal=InterruptSignal.INTERIM_TRANSCRIPT,
                speaker_identity="rahul",
                speaking_bot=BotId.DOST,
                text="ruko ek second",
            )
        assert len(calls) == 1
        assert ctrl.interruption_count == 1


class TestEndToEndBargeIn:
    @pytest.mark.asyncio
    async def test_interrupt_stops_bot_and_frees_floor_for_new_request(self):
        """Full loop: bot is mid-answer, InterruptionController cancels via
        TurnManager, the floor is free immediately after, and a new turn for a
        DIFFERENT bot can start right away with no overlap."""
        tm = TurnManager()
        spoken: list[str] = []

        async def slow_runner(bot, token):
            tm.set_state(bot, BotState.SPEAKING)
            for _ in range(50):
                token.raise_if_cancelled()
                await asyncio.sleep(0.01)
            spoken.append(bot.value)

        async def cancel_hook(reason: str) -> bool:
            return await tm.interrupt(reason=reason)

        ctrl = InterruptionController(cancel_hook=cancel_hook, debounce_s=0.05)

        await tm.submit(turn_id="t1", bots=(BotId.DOST,), runner=slow_runner)
        await asyncio.sleep(0.05)
        assert tm.speaking_bot is BotId.DOST

        await ctrl.trigger(
            signal=InterruptSignal.FINAL_TRANSCRIPT,
            speaker_identity="rahul",
            speaking_bot=BotId.DOST,
            text="ruko, simple example se samjhao",
        )

        assert tm.busy is False
        assert spoken == []  # Dost never finished - genuinely cut off
        assert tm.state_of(BotId.DOST) is BotState.INTERRUPTED

        # The room is immediately usable for the redirected request.
        started = await tm.submit(turn_id="t2", bots=(BotId.DOST,), runner=slow_runner, interrupt=True)
        assert started is True

    @pytest.mark.asyncio
    async def test_no_overlap_between_bots_during_interruption(self):
        """While one bot is being cancelled, the other must never start
        speaking concurrently - only the router's queued sequence may do that,
        and never as a side effect of an interruption."""
        tm = TurnManager()
        concurrent_speakers: set[str] = set()
        max_concurrent = 0

        async def runner(bot, token):
            tm.set_state(bot, BotState.SPEAKING)
            concurrent_speakers.add(bot.value)
            nonlocal max_concurrent
            max_concurrent = max(max_concurrent, len(concurrent_speakers))
            try:
                for _ in range(20):
                    token.raise_if_cancelled()
                    await asyncio.sleep(0.005)
            finally:
                # Must run on cancellation too, or a stale entry from an
                # interrupted bot would make a later bot look "concurrent".
                concurrent_speakers.discard(bot.value)

        await tm.submit(turn_id="a", bots=(BotId.DOST,), runner=runner)
        await asyncio.sleep(0.02)
        await tm.interrupt(reason="barge_in")
        await tm.submit(turn_id="b", bots=(BotId.SATHI,), runner=runner)
        await asyncio.sleep(0.15)

        assert max_concurrent == 1


async def _noop_cancel(reason: str) -> bool:
    return True
