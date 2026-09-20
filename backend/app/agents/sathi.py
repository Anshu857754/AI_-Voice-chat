"""Roxstar AI Sathi - female Indian Hinglish voice participant.

Distinct from Dost in three independent ways:
  * voice: ``SATHI_VOICE_ID`` (female), brighter/more expressive settings and a
    slightly faster speaking rate (pipeline/tts.py)
  * persona prompt: warm, analogy-driven, culture/daily-life leaning
  * router affinity: movies, music, food, health and daily-life topics
"""

from __future__ import annotations

from app.agents.base import BotAgent, BotIdentityConfig
from app.config.settings import Settings, get_settings
from app.models.participant import BotId
from app.pipeline.llm import LLMProvider
from app.pipeline.tts import TTSProvider, build_tts_provider


def sathi_config(settings: Settings | None = None) -> BotIdentityConfig:
    s = settings or get_settings()
    return BotIdentityConfig(
        bot_id=BotId.SATHI,
        identity=s.sathi_identity,
        display_name=s.sathi_display_name,
        voice_label="female-indian-hindi",
    )


def build_sathi(
    *,
    llm: LLMProvider,
    settings: Settings | None = None,
    tts: TTSProvider | None = None,
) -> BotAgent:
    s = settings or get_settings()
    return BotAgent(
        config=sathi_config(s),
        llm=llm,
        tts=tts or build_tts_provider(BotId.SATHI, s),
    )
