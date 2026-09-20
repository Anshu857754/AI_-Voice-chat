"""Live pipeline demo — no LiveKit/STT/TTS required.

Runs the REAL router, REAL shared context/memory, and REAL OpenRouter LLM
calls (using OPENROUTER_API_KEY from .env) against the 7 demo scenarios from
the spec. This exercises every stage except the LiveKit transport and
audio I/O, which need LIVEKIT_URL/STT_API_KEY/TTS_API_KEY to be set.

    cd backend
    python -m scripts.demo_pipeline
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.prompts import persona_for, reason_instruction, style_instruction
from app.config.settings import get_settings
from app.context.manager import RoomContextManager
from app.context.summarizer import ConversationSummarizer
from app.models.participant import BotId, ParticipantRole, Speaker
from app.pipeline.llm import LLMError, build_llm_provider
from app.routing.router import BotRouter
from app.utils.logging import configure_logging

configure_logging(level="WARNING")  # keep the demo output readable

BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"

BOT_COLOR = {BotId.DOST: BLUE, BotId.SATHI: MAGENTA}


async def speak(ctx, router, llm, *, speaker_identity, speaker_name, text, speaking_bot=None):
    """Exactly the shared pipeline the real orchestrator runs, minus LiveKit/TTS."""
    print(f"\n{BOLD}{speaker_name}:{RESET} {text}")

    turn, utterance = ctx.add_human_turn(
        text=text, speaker_identity=speaker_identity, speaker_name=speaker_name
    )
    state = ctx.router_state(speaking_bot=speaking_bot, exclude_turn_id=turn.turn_id)
    decision = router.route(text, speaker_identity=speaker_identity, state=state)

    tag = f"{DIM}[router] should_respond={decision.should_respond}"
    if decision.selected_bot:
        tag += f" bot={decision.selected_bot.value} reason={decision.reason.value} conf={decision.confidence:.2f}"
    tag += RESET
    print(tag)

    if not decision.should_respond or decision.selected_bot is None:
        print(f"{DIM}  (silence — not addressed to a bot / not relevant){RESET}")
        return

    for bot in decision.all_bots:
        bundle = ctx.build_messages(
            bot=bot,
            system_prompt=persona_for(bot.value),
            asking_identity=speaker_identity,
            style_instruction=style_instruction(reply_language=utterance.reply_language),
            extra_instruction=reason_instruction(decision.reason),
        )
        started = time.perf_counter()
        try:
            reply = await llm.generate(bundle.messages, max_tokens=180, temperature=0.7)
        except LLMError as exc:
            reply = f"(LLM failed: {exc} — would speak the in-character fallback here)"
        latency_ms = (time.perf_counter() - started) * 1000
        color = BOT_COLOR[bot]
        label = "Roxstar AI Dost" if bot is BotId.DOST else "Roxstar AI Sathi"
        print(f"{color}{BOLD}{label}:{RESET}{color} {reply}{RESET}")
        print(f"{DIM}  ({latency_ms:.0f}ms, {bundle.recent_turns_used} recent turns, "
              f"speaker_facts={bundle.speaker_facts_used}){RESET}")
        ctx.add_bot_turn(text=reply, bot=bot)


async def main() -> None:
    settings = get_settings()
    if not settings.llm_configured:
        print("OPENROUTER_API_KEY is not set — see .env.example. Aborting.")
        return

    llm = build_llm_provider(settings)
    print(f"{GREEN}LLM provider: {llm.name} ({getattr(llm, 'model', '?')}){RESET}")

    ctx = RoomContextManager(
        settings=settings,
        summarizer=ConversationSummarizer(llm, max_chars=settings.context_max_summary_chars),
    )
    router = BotRouter(
        min_question_chars=settings.router_min_question_chars,
        followup_window_s=settings.router_followup_window_s,
    )

    ctx.register_speaker(Speaker(identity="u_rahul", name="Rahul", role=ParticipantRole.HUMAN))
    ctx.register_speaker(Speaker(identity="u_priya", name="Priya", role=ParticipantRole.HUMAN))

    print(f"\n{YELLOW}{'=' * 70}\nScenario 1 — basic question\n{'=' * 70}{RESET}")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="AI kya hota hai?")

    print(f"\n{YELLOW}{'=' * 70}\nScenario 2 — English question, Hinglish room voice\n{'=' * 70}{RESET}")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="Can you explain cloud computing?")

    print(f"\n{YELLOW}{'=' * 70}\nScenario 3 — pronoun follow-up resolves to earlier topic\n{'=' * 70}{RESET}")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="Shah Rukh Khan ke baare mein batao.")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="Unki koi famous movie batao.")

    print(f"\n{YELLOW}{'=' * 70}\nScenario 4 — different human continues the same bot's thread\n{'=' * 70}{RESET}")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="Machine learning kya hota hai?")
    await speak(ctx, router, llm, speaker_identity="u_priya", speaker_name="Priya",
                text="Thoda aur simple batao.")

    print(f"\n{YELLOW}{'=' * 70}\nScenario 5 — speaker-specific memory recall\n{'=' * 70}{RESET}")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="Mera naam Rahul hai aur mujhe cricket pasand hai.")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="Maine apne baare mein kya bataya tha?")

    print(f"\n{YELLOW}{'=' * 70}\nScenario 6 — barge-in redirect (bot mid-speech, same bot answers)\n{'=' * 70}{RESET}")
    print(f"{DIM}  (simulating: AI Dost is mid-sentence when Rahul interrupts){RESET}")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="Ruko, simple example se samjhao.", speaking_bot=BotId.DOST)

    print(f"\n{YELLOW}{'=' * 70}\nScenario 7 — explicit two-bot sequencing, no overlap\n{'=' * 70}{RESET}")
    await speak(ctx, router, llm, speaker_identity="u_rahul", speaker_name="Rahul",
                text="AI Dost, tum answer karo. AI Sathi, baad mein ek example dena.")

    print(f"\n{YELLOW}{'=' * 70}\nBonus — irrelevant small talk is correctly silenced\n{'=' * 70}{RESET}")
    await speak(ctx, router, llm, speaker_identity="u_priya", speaker_name="Priya",
                text="Waise aaj weather kaafi achha hai.")

    await llm.aclose()
    print(f"\n{GREEN}Done.{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
