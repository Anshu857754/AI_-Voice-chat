"""LiveKit access-token minting and room helpers.

Tokens are always minted server-side. The API key/secret never leave the
backend; the browser only ever receives a short-lived JWT scoped to one room
and one identity.
"""

from __future__ import annotations

from datetime import timedelta

from livekit import api

from app.config.settings import Settings, get_settings
from app.models.participant import BotId
from app.utils.logging import TAG_ROOM, get_logger

log = get_logger(__name__)


class LiveKitNotConfigured(RuntimeError):
    """Raised when LIVEKIT_* credentials are missing."""


def _require(settings: Settings) -> None:
    if not settings.livekit_configured:
        raise LiveKitNotConfigured(
            "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set"
        )


def mint_human_token(
    *,
    identity: str,
    display_name: str,
    room_name: str,
    settings: Settings | None = None,
    ttl_minutes: int | None = None,
) -> str:
    """Token for a browser participant: publish mic, subscribe, send data."""
    s = settings or get_settings()
    _require(s)
    ttl = ttl_minutes if ttl_minutes is not None else s.token_ttl_minutes
    token = (
        api.AccessToken(s.livekit_api_key, s.livekit_api_secret)
        .with_identity(identity)
        .with_name(display_name or identity)
        .with_attributes({"role": "human"})
        .with_ttl(timedelta(minutes=ttl))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                can_update_own_metadata=True,
            )
        )
    )
    log.stage(
        TAG_ROOM, event="token_minted", role="human", identity=identity,
        room=room_name, ttl_minutes=ttl,
    )
    return token.to_jwt()


def mint_bot_token(
    bot: BotId,
    *,
    room_name: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Token for an AI participant.

    Deliberately NOT granted ``agent=True``: LiveKit does not count agent
    participants toward keeping a room open, so with it the room was closed
    ~20s after the last human left and both bots were kicked out for good.
    Bots are identified by the ``role=bot`` attribute instead.
    """
    s = settings or get_settings()
    _require(s)
    identity, display = bot_identity(bot, s)
    token = (
        api.AccessToken(s.livekit_api_key, s.livekit_api_secret)
        .with_identity(identity)
        .with_name(display)
        .with_attributes({"role": "bot", "bot_id": bot.value})
        # Agents are long-lived; give them a generous TTL.
        .with_ttl(timedelta(hours=12))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name or s.room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                can_update_own_metadata=True,
            )
        )
    )
    log.stage(TAG_ROOM, event="token_minted", role="bot", identity=identity, room=room_name or s.room_name)
    return token.to_jwt()


def bot_identity(bot: BotId, settings: Settings | None = None) -> tuple[str, str]:
    """(identity, display_name) for a bot, from configuration."""
    s = settings or get_settings()
    if bot is BotId.DOST:
        return s.dost_identity, s.dost_display_name
    return s.sathi_identity, s.sathi_display_name


def bot_identities(settings: Settings | None = None) -> frozenset[str]:
    s = settings or get_settings()
    return frozenset({s.dost_identity, s.sathi_identity})
