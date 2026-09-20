"""FastAPI token + introspection server.

Endpoints
---------
``GET  /health``            liveness + which providers are configured
``GET  /config``            non-secret config the frontend needs
``POST /token``             mint a LiveKit join token for a browser participant
``GET  /metrics``           observed latency stats (real measurements only)

Credentials never leave this process: the browser receives only a short-lived,
room-scoped JWT. The ``/metrics`` payload is aggregate timing data, no
transcripts.

The AI agents run in a separate process (``python -m app.agent_worker``) so a
crash or restart of the web API never disconnects the bots, and the two scale
independently.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.livekit_rt.room import LiveKitNotConfigured, bot_identity, mint_human_token
from app.models.participant import BotId
from app.utils.logging import TAG_API, configure_logging, get_logger
from app.utils.metrics import METRICS

configure_logging()
log = get_logger(__name__)

settings = get_settings()

app = FastAPI(
    title="Roxstar AI Voice Room",
    version="1.0.0",
    description="Token service and introspection API for the Roxstar AI voice room.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# LiveKit identities must be stable and safe to embed in a JWT.
_IDENTITY_SAFE = re.compile(r"[^A-Za-z0-9_\-]")
_NAME_MAX = 40


class TokenRequest(BaseModel):
    display_name: Annotated[str, Field(min_length=1, max_length=_NAME_MAX)]
    room: str | None = Field(default=None, max_length=64)
    # Optional: reuse a stable identity so a page refresh keeps the same
    # speaker memory instead of being treated as a new person.
    identity: str | None = Field(default=None, max_length=64)


class TokenResponse(BaseModel):
    token: str
    url: str
    room: str
    identity: str
    display_name: str


def _make_identity(display_name: str, requested: str | None) -> str:
    if requested:
        cleaned = _IDENTITY_SAFE.sub("-", requested).strip("-")
        if cleaned:
            return cleaned[:64]
    slug = _IDENTITY_SAFE.sub("-", display_name.strip().lower()).strip("-") or "guest"
    return f"{slug[:24]}-{uuid.uuid4().hex[:6]}"


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus a truthful report of what is actually configured."""
    return {
        "status": "ok",
        "room": settings.room_name,
        "configured": {
            "livekit": settings.livekit_configured,
            "llm": settings.llm_configured,
            "stt": settings.stt_configured,
            "tts": settings.tts_configured,
        },
        "providers": {
            "llm": "openrouter" if settings.llm_configured else "scripted (offline)",
            "llm_model": settings.openrouter_model if settings.llm_configured else None,
            "stt": settings.stt_provider,
            "tts": settings.tts_provider,
        },
        "missing_env": settings.missing_required(),
    }


@app.get("/config")
async def config() -> dict[str, object]:
    """Non-secret configuration for the frontend (bot identities, room name)."""
    dost_id, dost_name = bot_identity(BotId.DOST, settings)
    sathi_id, sathi_name = bot_identity(BotId.SATHI, settings)
    return {
        "livekit_url": settings.livekit_url,
        "room": settings.room_name,
        "bots": [
            {
                "bot_id": BotId.DOST.value,
                "identity": dost_id,
                "display_name": dost_name,
                "voice": "male-indian-hindi",
                "persona": "Practical, structured, tech-leaning",
            },
            {
                "bot_id": BotId.SATHI.value,
                "identity": sathi_id,
                "display_name": sathi_name,
                "voice": "female-indian-hindi",
                "persona": "Warm, example-driven, daily-life leaning",
            },
        ],
        "topics": {
            "chat": "lk.chat",
            "transcript": "roxstar.transcript",
            "state": "roxstar.state",
        },
    }


@app.post("/token", response_model=TokenResponse)
async def create_token(req: TokenRequest) -> TokenResponse:
    """Mint a join token for one human participant."""
    # The agent worker only joins ROOM_NAME, so a token for any other room
    # would put the human in a room where AI Dost / AI Sathi never appear.
    room_name = settings.room_name
    if req.room and req.room.strip() != room_name:
        log.warning(tag=TAG_API, event="room_override_ignored", requested=req.room.strip())
    identity = _make_identity(req.display_name, req.identity)
    try:
        token = mint_human_token(
            identity=identity,
            display_name=req.display_name.strip(),
            room_name=room_name,
            settings=settings,
        )
    except LiveKitNotConfigured as exc:
        # A configuration problem, not a client error - and no secrets in the
        # message.
        log.error(tag=TAG_API, event="token_denied", reason="livekit_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiveKit is not configured on the server. See .env.example.",
        ) from exc
    log.stage(TAG_API, event="token_issued", identity=identity, room=room_name)
    return TokenResponse(
        token=token,
        url=settings.livekit_url,
        room=room_name,
        identity=identity,
        display_name=req.display_name.strip(),
    )


@app.get("/metrics")
async def metrics() -> dict[str, object]:
    """Observed latency, measured from real pipeline stage timestamps.

    Note: the agent worker is a separate process, so these are the API
    process's own metrics. The authoritative per-turn numbers are in the agent
    worker logs under ``[LATENCY]`` and in the UI debug panel, which reads them
    from the ``roxstar.state`` data channel.
    """
    return {
        "note": "Agent-side metrics are published on the roxstar.state topic; see docs/latency.md",
        **METRICS.snapshot(),
        "recent": METRICS.recent(20),
    }


def main() -> None:
    import sys

    import uvicorn

    if not settings.livekit_configured:
        log.error(
            tag=TAG_API,
            event="cannot_start",
            detail="missing LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in .env (see .env.example)",
        )
        sys.exit(1)
    if settings.missing_required():
        log.warning(
            tag=TAG_API, event="agent_env_incomplete", missing=",".join(settings.missing_required())
        )

    log.stage(
        TAG_API, event="starting", host=settings.api_host, port=settings.api_port,
        cors=",".join(settings.cors_origin_list),
    )
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
