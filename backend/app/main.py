"""FastAPI token + introspection server.

Endpoints
---------
``GET  /health``            liveness probe: {"status": "ok"}
``GET  /health/details``    which providers are configured (no secrets)
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

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import AuthError, AuthService, User
from app.config.settings import get_settings
from app.conversations import ConversationError, ConversationStore
from app.history import build_history
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
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # the UI reads the export filename
)

# LiveKit identities must be stable and safe to embed in a JWT.
_IDENTITY_SAFE = re.compile(r"[^A-Za-z0-9_\-]")
_NAME_MAX = 40


auth = AuthService(settings)
history = build_history(settings, settings.room_name)
conversations = ConversationStore(settings.history_db_path, settings.room_name)


class SignupRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=_NAME_MAX)]
    email: Annotated[str, Field(max_length=200)]
    password: Annotated[str, Field(min_length=1, max_length=200)]


class LoginRequest(BaseModel):
    email: Annotated[str, Field(max_length=200)]
    password: Annotated[str, Field(min_length=1, max_length=200)]


class AuthResponse(BaseModel):
    token: str
    user: dict[str, object]


def _auth_response(user: User) -> AuthResponse:
    return AuthResponse(
        token=auth.issue_token(user), user={"id": user.id, "name": user.name, "email": user.email}
    )


def _bearer_user(authorization: str | None) -> User | None:
    """The logged-in user for this request, or None when auth is off/absent."""
    if authorization and authorization.lower().startswith("bearer "):
        try:
            return auth.user_from_token(authorization[7:].strip())
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    if settings.auth_required:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Pehle login karo.")
    return None


class TokenRequest(BaseModel):
    display_name: Annotated[str | None, Field(default=None, max_length=_NAME_MAX)] = None
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


def _make_identity(display_name: str, requested: str | None, user: User | None = None) -> str:
    if user is not None:
        # Prefix binds the identity to the account: a client cannot pose as
        # another user; the suffix lets one account hold several tabs.
        suffix = _IDENTITY_SAFE.sub("", requested or "")[-8:] or uuid.uuid4().hex[:6]
        return f"u{user.id}-{suffix}"
    if requested:
        cleaned = _IDENTITY_SAFE.sub("-", requested).strip("-")
        if cleaned:
            return cleaned[:64]
    slug = _IDENTITY_SAFE.sub("-", display_name.strip().lower()).strip("-") or "guest"
    return f"{slug[:24]}-{uuid.uuid4().hex[:6]}"


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe (Render health check). No login, no database, nothing configuration-related."""
    return {"status": "ok"}


@app.get("/health/details")
async def health_details() -> dict[str, object]:
    """Liveness plus a truthful report of what is actually configured (names only, never values)."""
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


@app.post("/auth/signup", response_model=AuthResponse)
def signup(req: SignupRequest) -> AuthResponse:
    try:
        user = auth.signup(name=req.name, email=req.email, password=req.password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    log.stage(TAG_API, event="signup", user_id=user.id)
    return _auth_response(user)


@app.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest) -> AuthResponse:
    try:
        user = auth.login(email=req.email, password=req.password)
    except AuthError as exc:
        log.warning(tag=TAG_API, event="login_failed")
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return _auth_response(user)


@app.get("/auth/me")
def me(authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
    user = _bearer_user(authorization)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Pehle login karo.")
    return {"id": user.id, "name": user.name, "email": user.email}


@app.get("/history")
def get_history(
    limit: int = 100, authorization: Annotated[str | None, Header()] = None
) -> dict[str, object]:
    """The room's saved conversation (oldest first), so a reload keeps the chat."""
    _bearer_user(authorization)
    try:
        messages = history.recent(limit)
    except Exception as exc:
        log.error(tag=TAG_API, event="history_read_failed", error=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Chat history abhi available nahi hai."
        ) from exc
    return {"backend": history.kind, "messages": messages}


# ---- conversations (saved chats) ---------------------------------------------
# Every route is scoped to the logged-in user; another user's id is a 404.


@app.exception_handler(ConversationError)
async def _conversation_error(_: Request, exc: ConversationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


def _uid(authorization: str | None) -> int:
    user = _bearer_user(authorization)
    return user.id if user else 0  # 0 = the shared guest account when auth is off


class NewConversation(BaseModel):
    kind: str = "text"  # text | voice | collab
    participants: list[str] | None = None
    settings: dict[str, object] | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    mode: str | None = None
    participants: list[str] | None = None
    settings: dict[str, object] | None = None


@app.get("/conversations")
def list_conversations(
    archived: bool = False, limit: int = 30, offset: int = 0,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    return conversations.list_conversations(_uid(authorization), archived=archived, limit=limit, offset=offset)


@app.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    req: NewConversation, authorization: Annotated[str | None, Header()] = None
) -> dict[str, object]:
    conv = conversations.create(
        _uid(authorization), req.kind, participants=req.participants, settings=req.settings
    )
    log.stage(TAG_API, event="conversation_created", id=conv["id"], kind=req.kind)
    return conv


@app.get("/conversations/search")
def search_conversations(
    q: str = "", limit: int = 30, authorization: Annotated[str | None, Header()] = None
) -> dict[str, object]:
    return {"results": conversations.search(_uid(authorization), q, max(1, min(limit, 50)))}


@app.get("/conversations/{cid}")
def get_conversation(cid: str, authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
    return conversations.get(_uid(authorization), cid)


@app.patch("/conversations/{cid}")
def patch_conversation(
    cid: str, req: ConversationPatch, authorization: Annotated[str | None, Header()] = None
) -> dict[str, object]:
    return conversations.update(_uid(authorization), cid, req.model_dump(exclude_unset=True))


@app.delete("/conversations/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(cid: str, authorization: Annotated[str | None, Header()] = None) -> Response:
    conversations.delete(_uid(authorization), cid)
    log.stage(TAG_API, event="conversation_deleted", id=cid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/conversations/{cid}/clear")
def clear_conversation(cid: str, authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
    return conversations.clear(_uid(authorization), cid)


@app.get("/conversations/{cid}/messages")
def conversation_messages(
    cid: str, limit: int = 50, before: float | None = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    return conversations.messages(_uid(authorization), cid, limit=limit, before=before)


@app.get("/conversations/{cid}/export")
def export_conversation(
    cid: str, format: str = "md", authorization: Annotated[str | None, Header()] = None
) -> Response:
    filename, media, body = conversations.export(_uid(authorization), cid, format)
    return Response(
        content=body, media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/token", response_model=TokenResponse)
def create_token(
    req: TokenRequest, authorization: Annotated[str | None, Header()] = None
) -> TokenResponse:
    """Mint a join token for one human participant (must be logged in)."""
    user = _bearer_user(authorization)
    display_name = (user.name if user else (req.display_name or "").strip()) or "Guest"
    # The agent worker only joins ROOM_NAME, so a token for any other room
    # would put the human in a room where AI Dost / AI Sathi never appear.
    room_name = settings.room_name
    if req.room and req.room.strip() != room_name:
        log.warning(tag=TAG_API, event="room_override_ignored", requested=req.room.strip())
    identity = _make_identity(display_name, req.identity, user)
    try:
        token = mint_human_token(
            identity=identity,
            display_name=display_name,
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
        display_name=display_name,
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
