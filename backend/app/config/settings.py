"""Environment-driven configuration.

Every provider, model, voice and tuning knob is read from the environment so
that nothing is hardcoded and providers can be swapped without code changes.
Secrets are only ever read from here; they are never logged (see
``utils.logging.redact``).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root == parent of backend/, so a single .env at the monorepo root serves
# the token server, the agent worker and (via VITE_*) the frontend.
_REPO_ROOT = Path(__file__).resolve().parents[3]

DEV_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")  # used only when CORS_ORIGINS is empty
STTProviderName = Literal["deepgram", "null"]
TTSProviderName = Literal["elevenlabs", "sarvam", "silent"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- LiveKit ----------------------------------------------------------
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # ---- LLM (OpenRouter) -------------------------------------------------
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_s: float = 20.0
    openrouter_max_retries: int = 2
    # Sampling for spoken replies. Short by default (voice); the longer cap is
    # used only when the user explicitly asks for detail.
    llm_temperature: float = 0.7
    llm_max_tokens: int = 150
    llm_max_tokens_detail: int = 400
    # Reasoning ("thinking") models burn max_tokens on hidden thoughts and add
    # seconds of latency; a spoken chat reply does not need them.
    llm_disable_reasoning: bool = True
    openrouter_app_url: str = "http://localhost:5173"
    openrouter_app_name: str = "Roxstar AI Voice Room"

    # ---- STT --------------------------------------------------------------
    stt_provider: STTProviderName = "deepgram"
    # DEEPGRAM_API_KEY is accepted as an alias for STT_API_KEY.
    stt_api_key: str = Field(default="", validation_alias=AliasChoices("stt_api_key", "deepgram_api_key"))
    stt_model: str = "nova-2"
    stt_language: str = "multi"
    stt_interim_results: bool = True
    stt_endpointing_ms: int = 250

    # ---- TTS --------------------------------------------------------------
    tts_provider: TTSProviderName = "elevenlabs"
    tts_api_key: str = ""
    tts_model: str = "eleven_turbo_v2_5"
    tts_language: str = "hi"
    dost_voice_id: str = ""
    sathi_voice_id: str = ""

    # ---- Room / identities ------------------------------------------------
    room_name: str = "roxstar-room"
    dost_identity: str = "roxstar-ai-dost"
    dost_display_name: str = "Roxstar AI Dost"
    sathi_identity: str = "roxstar-ai-sathi"
    sathi_display_name: str = "Roxstar AI Sathi"

    # ---- Context window ---------------------------------------------------
    context_recent_turns: int = 14
    context_summary_trigger: int = 24
    context_summary_keep: int = 14
    context_max_summary_chars: int = 1200

    # ---- Routing ----------------------------------------------------------
    router_min_question_chars: int = 3
    router_followup_window_s: float = 45.0
    turn_response_timeout_s: float = 120.0

    # ---- Echo / noise guards (voice input only) ---------------------------
    # Speech heard while an AI speaks (+ tail) is treated as the AI's own echo.
    echo_guard_tail_s: float = 1.2
    stt_min_confidence: float = 0.65
    stt_allowed_languages: str = "en,hi"

    # ---- Barge-in ---------------------------------------------------------
    interrupt_min_speech_ms: int = 200

    # ---- Privacy ----------------------------------------------------------
    persist_transcripts: bool = False
    transcript_dir: Path = Path("./transcripts")

    # ---- Observability ----------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False

    # ---- Token server -----------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    token_ttl_minutes: int = 120
    # Comma-separated browser origins allowed to call the API (e.g. your Vercel URL).
    # Empty = development: only the local Vite dev server is allowed.
    cors_origins: str = ""

    # ---- Accounts (login / signup) ------------------------------------------
    auth_required: bool = True
    auth_secret: str = ""  # falls back to a key derived from LIVEKIT_API_SECRET
    auth_db_path: Path = Path("./data/users.db")
    auth_token_ttl_hours: int = 168

    # ---- Chat history (survives reloads and worker restarts) ------------------
    mongodb_uri: str = ""  # MongoDB Atlas connection string; empty -> local SQLite
    mongodb_db: str = "roxstar"
    history_db_path: Path = Path("./data/chat.db")
    history_restore_turns: int = 20  # turns reloaded into the bots' context on start

    @field_validator("stt_provider", mode="before")
    @classmethod
    def _norm_stt(cls, v: object) -> object:
        return str(v).strip().lower() if v else "deepgram"

    @field_validator("tts_provider", mode="before")
    @classmethod
    def _norm_tts(cls, v: object) -> object:
        return str(v).strip().lower() if v else "elevenlabs"

    # ---- Derived helpers --------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]
        return origins or list(DEV_CORS_ORIGINS)

    @property
    def livekit_configured(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)

    @property
    def llm_configured(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def stt_configured(self) -> bool:
        if self.stt_provider == "null":
            return True
        return bool(self.stt_api_key)

    @property
    def tts_configured(self) -> bool:
        if self.tts_provider == "silent":
            return True
        if self.tts_provider == "sarvam" and self.tts_model.startswith("eleven"):
            return False  # TTS_MODEL must be a Sarvam model id
        return bool(self.tts_api_key and self.dost_voice_id and self.sathi_voice_id)

    def missing_required(self) -> list[str]:
        """Names of env vars that must be set before the agent can run for real."""
        missing: list[str] = []
        if not self.livekit_url:
            missing.append("LIVEKIT_URL")
        if not self.livekit_api_key:
            missing.append("LIVEKIT_API_KEY")
        if not self.livekit_api_secret:
            missing.append("LIVEKIT_API_SECRET")
        if not self.openrouter_api_key:
            missing.append("OPENROUTER_API_KEY")
        if self.stt_provider == "deepgram" and not self.stt_api_key:
            missing.append("STT_API_KEY")
        if self.tts_provider in ("elevenlabs", "sarvam"):
            if self.tts_provider == "sarvam" and self.tts_model.startswith("eleven"):
                missing.append("TTS_MODEL (a Sarvam model id)")
            if not self.tts_api_key:
                missing.append("TTS_API_KEY")
            if not self.dost_voice_id:
                missing.append("DOST_VOICE_ID")
            if not self.sathi_voice_id:
                missing.append("SATHI_VOICE_ID")
        return missing


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()


def reload_settings() -> Settings:
    """Drop the cache — used by tests that patch the environment."""
    get_settings.cache_clear()
    return get_settings()
