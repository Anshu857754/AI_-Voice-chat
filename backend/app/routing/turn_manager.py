"""Turn manager: the response lock that stops the two bots overlapping.

Concurrency model
-----------------
There is exactly one *floor* in the room. A bot may only generate and speak
while it holds the floor. The manager owns:

  * the active turn (bot + cancellation token + task)
  * a depth-1 pending queue, so a question asked while a bot is mid-answer is
    honoured after it finishes instead of being lost or spoken over
  * sequential fan-out for multi-bot decisions ("Dost tum answer karo, Sathi
    baad mein example dena") - Sathi starts only once Dost's audio has stopped
  * barge-in: cancelling the active turn and, by default, discarding whatever
    was queued, because a human interrupting has just changed the subject

State machine per bot:

    OFFLINE -> IDLE -> LISTENING -> THINKING -> SPEAKING -> IDLE
                          ^                        |
                          +--- INTERRUPTED <-------+
                          +--- CANCELLED  <--------+
                          +--- ERROR      <--------+

Nothing here talks to LiveKit or an LLM; the orchestrator supplies a ``runner``
coroutine. That keeps the concurrency rules unit-testable in isolation.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.models.participant import BotId, BotState
from app.utils.logging import TAG_TURN, get_logger

log = get_logger(__name__)


class CancellationToken:
    """Cooperative cancellation shared by the LLM and TTS stages of one turn.

    Cooperative rather than ``Task.cancel()`` because a cancelled TTS stream
    must still be closed cleanly (to stop vendor billing) and the partial reply
    must still be recorded in context as an interrupted turn.
    """

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str = "cancelled") -> bool:
        """Request cancellation. Returns False if already cancelled."""
        if self._event.is_set():
            return False
        self._reason = reason
        self._event.set()
        return True

    async def wait(self) -> str:
        await self._event.wait()
        return self._reason or "cancelled"

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TurnCancelled(self._reason or "cancelled")


class TurnCancelled(Exception):
    """Raised inside a runner when its turn was superseded or interrupted."""


class TurnRunner(Protocol):
    """Produces one bot's reply. Must poll ``token`` and stop promptly."""

    async def __call__(self, bot: BotId, token: CancellationToken) -> None: ...


StateListener = Callable[[BotId, BotState], None] | Callable[[BotId, BotState], Awaitable[None]]


@dataclass
class ActiveTurn:
    turn_id: str
    bots: tuple[BotId, ...]
    token: CancellationToken
    task: asyncio.Task[None]
    started_at: float = field(default_factory=time.monotonic)
    current_bot: BotId | None = None


@dataclass
class PendingTurn:
    turn_id: str
    bots: tuple[BotId, ...]
    runner: TurnRunner


class TurnManager:
    """Serializes AI responses and implements barge-in."""

    def __init__(
        self,
        *,
        response_timeout_s: float = 30.0,
        on_state_change: StateListener | None = None,
        queue_depth: int = 1,
    ) -> None:
        self._timeout_s = response_timeout_s
        self._on_state_change = on_state_change
        self._queue_depth = max(0, queue_depth)
        self._active: ActiveTurn | None = None
        self._pending: list[PendingTurn] = []
        self._lock = asyncio.Lock()
        self._states: dict[BotId, BotState] = {b: BotState.OFFLINE for b in BotId}

    # ---- state ------------------------------------------------------------
    def state_of(self, bot: BotId) -> BotState:
        return self._states[bot]

    def states(self) -> dict[str, str]:
        return {b.value: s.value for b, s in self._states.items()}

    def set_state(self, bot: BotId, state: BotState) -> None:
        if self._states[bot] is state:
            return
        self._states[bot] = state
        log.stage(TAG_TURN, event="state", bot=bot.value, state=state.value)
        if self._on_state_change is None:
            return
        result = self._on_state_change(bot, state)
        if asyncio.iscoroutine(result):
            # Fire-and-forget: publishing UI state must never block the pipeline.
            task = asyncio.create_task(result)
            task.add_done_callback(_log_task_error)

    @property
    def speaking_bot(self) -> BotId | None:
        """Bot currently producing audio, used by the router for barge-in."""
        for bot, state in self._states.items():
            if state is BotState.SPEAKING:
                return bot
        return None

    @property
    def busy(self) -> bool:
        return self._active is not None and not self._active.task.done()

    @property
    def active_turn_id(self) -> str | None:
        return self._active.turn_id if self._active else None

    # ---- submission -------------------------------------------------------
    async def submit(
        self,
        *,
        turn_id: str,
        bots: tuple[BotId, ...],
        runner: TurnRunner,
        interrupt: bool = False,
    ) -> bool:
        """Schedule a response.

        Returns True if the turn started or was queued, False if it was dropped.
        ``interrupt=True`` cancels whatever is on the floor first - that is the
        barge-in path.
        """
        if not bots:
            return False

        if interrupt and self.busy:
            # Cancel and wait for the floor to actually free up BEFORE taking
            # the lock again below. interrupt() must not be called while
            # holding self._lock: the cancelled task's own cleanup (_finish)
            # needs that same lock to release the floor, so awaiting it here
            # while locked would deadlock until the force-cancel timeout.
            await self.interrupt(reason="barge_in", drop_pending=True)

        async with self._lock:
            if self.busy:
                if interrupt:
                    # Another turn grabbed the floor in the gap above (rare
                    # race) - fall through to queuing rather than clobbering it.
                    pass
                if len(self._pending) >= self._queue_depth:
                    log.stage(
                        TAG_TURN,
                        event="dropped",
                        turn_id=turn_id,
                        reason="queue_full",
                        queued=len(self._pending),
                    )
                    return False
                self._pending.append(PendingTurn(turn_id, bots, runner))
                log.stage(
                    TAG_TURN, event="queued", turn_id=turn_id,
                    bots=",".join(b.value for b in bots), depth=len(self._pending),
                )
                return True

            self._start_locked(turn_id, bots, runner)
            return True

    def _start_locked(self, turn_id: str, bots: tuple[BotId, ...], runner: TurnRunner) -> None:
        token = CancellationToken()
        task = asyncio.create_task(self._run_sequence(turn_id, bots, runner, token))
        self._active = ActiveTurn(turn_id=turn_id, bots=bots, token=token, task=task)
        log.stage(
            TAG_TURN, event="started", turn_id=turn_id,
            bots=",".join(b.value for b in bots),
        )

    # ---- execution --------------------------------------------------------
    async def _run_sequence(
        self,
        turn_id: str,
        bots: tuple[BotId, ...],
        runner: TurnRunner,
        token: CancellationToken,
    ) -> None:
        """Run each selected bot strictly one after another."""
        try:
            for bot in bots:
                if token.cancelled:
                    self.set_state(bot, BotState.CANCELLED)
                    break
                if self._active is not None:
                    self._active.current_bot = bot
                self.set_state(bot, BotState.THINKING)
                try:
                    await asyncio.wait_for(runner(bot, token), timeout=self._timeout_s)
                except TimeoutError:
                    # A stuck provider must not hold the floor forever.
                    token.cancel("timeout")
                    self.set_state(bot, BotState.ERROR)
                    log.warning(
                        tag=TAG_TURN, event="timeout", turn_id=turn_id, bot=bot.value,
                        timeout_s=self._timeout_s,
                    )
                except TurnCancelled as exc:
                    self.set_state(bot, BotState.INTERRUPTED)
                    log.stage(
                        TAG_TURN, event="cancelled", turn_id=turn_id, bot=bot.value,
                        reason=str(exc),
                    )
                    break
                except asyncio.CancelledError:
                    self.set_state(bot, BotState.CANCELLED)
                    raise
                except Exception as exc:
                    # One bot failing must not poison the room or the queue.
                    self.set_state(bot, BotState.ERROR)
                    log.exception(
                        tag=TAG_TURN, event="runner_failed", turn_id=turn_id,
                        bot=bot.value, error=repr(exc),
                    )
                else:
                    self.set_state(bot, BotState.IDLE)
        finally:
            await self._finish(turn_id)

    async def _finish(self, turn_id: str) -> None:
        """Release the floor and start whatever was queued."""
        async with self._lock:
            if self._active is not None and self._active.turn_id != turn_id:
                return  # Already superseded by a newer turn.
            self._active = None
            for bot, state in list(self._states.items()):
                if state in (BotState.THINKING, BotState.GENERATING, BotState.SYNTHESIZING, BotState.SPEAKING):
                    self.set_state(bot, BotState.IDLE)
            if not self._pending:
                log.stage(TAG_TURN, event="floor_free", turn_id=turn_id)
                return
            nxt = self._pending.pop(0)
        # Started outside the lock guard above via a fresh acquisition to keep
        # _start_locked's invariant (called while holding the lock).
        async with self._lock:
            if self.busy:
                self._pending.insert(0, nxt)
                return
            self._start_locked(nxt.turn_id, nxt.bots, nxt.runner)

    # ---- interruption -----------------------------------------------------
    async def interrupt(self, *, reason: str = "barge_in", drop_pending: bool = True) -> bool:
        """Cancel the active turn. Returns True if something was cancelled.

        The cancellation request is made under the lock, but waiting for the
        task to actually unwind happens OUTSIDE it. The cancelled runner's own
        ``finally`` clause calls ``_finish``, which needs ``self._lock`` to
        release the floor - holding the lock across that wait would deadlock
        every barge-in until the 1.5s force-cancel fallback below.
        """
        async with self._lock:
            if drop_pending and self._pending:
                log.stage(TAG_TURN, event="pending_dropped", count=len(self._pending))
                self._pending.clear()
            active = self._active
            if active is None or active.task.done():
                return False
            cancelled = active.token.cancel(reason)
            bot = active.current_bot
            if bot is not None:
                self.set_state(bot, BotState.INTERRUPTED)
            log.stage(
                TAG_TURN, event="interrupt", turn_id=active.turn_id,
                bot=bot.value if bot else None, reason=reason, first_cancel=cancelled,
            )

        # Give the runner a short grace period to unwind (close the TTS socket,
        # record the partial turn) - lock-free, so _finish can always proceed.
        try:
            await asyncio.wait_for(asyncio.shield(active.task), timeout=1.5)
        except TimeoutError:
            active.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await active.task
        except (asyncio.CancelledError, Exception):
            pass
        return True

    async def shutdown(self) -> None:
        await self.interrupt(reason="shutdown")
        async with self._lock:
            self._pending.clear()
        for bot in BotId:
            self.set_state(bot, BotState.OFFLINE)


def _log_task_error(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    if exc := task.exception():
        log.error(tag=TAG_TURN, event="state_listener_failed", error=repr(exc))
