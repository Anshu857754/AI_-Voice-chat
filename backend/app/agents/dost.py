"""Roxstar AI Dost - male Indian Hinglish voice participant.

Distinct from Sathi in three independent ways, so the difference is audible
and legible, not just a label:
  * voice: ``DOST_VOICE_ID`` (male), steadier delivery settings
  * persona prompt: practical, structured, tech-leaning (agents/prompts.py)
  * router affinity: technical topics lean to Dost (routing/router.py)
"""

from __future__ import annotations

from app.agents.base import BotAgent, BotIdentityConfig
from app.config.settings import Settings, get_settings
from app.models.participant import BotId
from app.pipeline.llm import LLMProvider
from app.pipeline.tts import TTSProvider, build_tts_provider


def dost_config(settings: Settings | None = None) -> BotIdentityConfig:
    s = settings or get_settings()
    return BotIdentityConfig(
        bot_id=BotId.DOST,
        identity=s.dost_identity,
        display_name=s.dost_display_name,
        voice_label="male-indian-hindi",
    )


def build_dost(
    *,
    llm: LLMProvider,
    settings: Settings | None = None,
    tts: TTSProvider | None = None,
) -> BotAgent:
    s = settings or get_settings()
    return BotAgent(
        config=dost_config(s),
        llm=llm,
        tts=tts or build_tts_provider(BotId.DOST, s),
    )
