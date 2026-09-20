"""Agent worker entrypoint: joins the room as AI Dost and AI Sathi.

    python -m app.agent_worker                 # join the configured room
    python -m app.agent_worker --room my-room   # join a specific room
    python -m app.agent_worker --check          # validate config and exit

Run this alongside the token server. Both bots live in this one process (two
LiveKit connections, one shared router/context), so routing decisions never
have to cross a network boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys

from app.config.settings import get_settings, reload_settings
from app.orchestrator import RoomOrchestrator
from app.utils.logging import TAG_AGENT, configure_logging, get_logger, redact

log = get_logger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="app.agent_worker",
        description="Run Roxstar AI Dost and AI Sathi as LiveKit participants.",
    )
    parser.add_argument("--room", default=None, help="room name (default: ROOM_NAME)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="print a configuration report and exit without connecting",
    )
    return parser.parse_args(argv)


def _report_config() -> int:
    """Print what is configured. Returns a process exit code."""
    s = reload_settings()
    lines = [
        "Roxstar AI Voice Room - configuration check",
        "",
        f"  LIVEKIT_URL          {s.livekit_url or '<unset>'}",
        f"  LIVEKIT_API_KEY      {redact(s.livekit_api_key)}",
        f"  LIVEKIT_API_SECRET   {redact(s.livekit_api_secret)}",
        f"  ROOM_NAME            {s.room_name}",
        "",
        f"  LLM                  openrouter model={s.openrouter_model}",
        f"  OPENROUTER_API_KEY   {redact(s.openrouter_api_key)}",
        "",
        f"  STT                  {s.stt_provider} model={s.stt_model} language={s.stt_language}",
        f"  STT_API_KEY          {redact(s.stt_api_key)}",
        "",
        f"  TTS                  {s.tts_provider} model={s.tts_model} language={s.tts_language}",
        f"  TTS_API_KEY          {redact(s.tts_api_key)}",
        f"  DOST_VOICE_ID        {s.dost_voice_id or '<unset>'}  (male)",
        f"  SATHI_VOICE_ID       {s.sathi_voice_id or '<unset>'}  (female)",
        "",
        f"  Context window       {s.context_recent_turns} recent turns, "
        f"summary at {s.context_summary_trigger}",
        f"  Response timeout     {s.turn_response_timeout_s}s",
        f"  Barge-in threshold   {s.interrupt_min_speech_ms}ms",
        f"  Persist transcripts  {s.persist_transcripts}",
        "",
    ]
    missing = s.missing_required()
    if missing:
        lines.append("  MISSING (the worker will refuse to start):")
        lines.extend(f"    - {name}" for name in missing)
    else:
        lines.append("  All required variables are set.")
    print("\n".join(lines))
    return 1 if missing else 0


async def _run(room: str | None) -> None:
    settings = get_settings()
    orchestrator = RoomOrchestrator(settings=settings, room_name=room or settings.room_name)

    stop = asyncio.Event()

    def _request_stop(*_: object) -> None:
        log.stage(TAG_AGENT, event="shutdown_requested")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError, ValueError):
            loop.add_signal_handler(sig, _request_stop)

    # The Deepgram/ElevenLabs LiveKit plugins fetch their aiohttp session from
    # this context. Outside `livekit-agents`' own worker it must be opened by
    # hand, otherwise every STT/TTS call fails with "http session outside of a
    # job context" (and the bot silently falls back to chat-only).
    from livekit.agents.utils import http_context

    async with http_context.open():
        await orchestrator.start()
        try:
            waiters = [
                asyncio.ensure_future(stop.wait()),
                asyncio.ensure_future(orchestrator.fatal.wait()),
            ]
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            for w in waiters:
                w.cancel()
        except asyncio.CancelledError:
            pass
        finally:
            await orchestrator.stop()
    if orchestrator.fatal.is_set():
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    if args.check:
        return _report_config()

    settings = get_settings()
    missing = settings.missing_required()
    if missing:
        log.error(
            tag=TAG_AGENT,
            event="cannot_start",
            detail="missing required environment variables: " + ", ".join(missing),
        )
        print(
            "\nSet the variables above in .env (see .env.example), then retry.\n"
            "Run `python -m app.agent_worker --check` for a full report.",
            file=sys.stderr,
        )
        return 1
    log.stage(TAG_AGENT, event="worker_registered", room=args.room or settings.room_name)

    try:
        asyncio.run(_run(args.room))
    except KeyboardInterrupt:
        log.stage(TAG_AGENT, event="interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
