"""Participant registry.

Tracks who is in the room from the agent's point of view, so the pipeline can
attribute every transcript to a real LiveKit identity (the basis of
speaker-specific memory) and the UI can be told the truth about who is present.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from livekit import rtc

from app.models.participant import ParticipantRole, Speaker
from app.utils.logging import TAG_ROOM, get_logger

log = get_logger(__name__)


@dataclass
class ParticipantRegistry:
    """Humans and bots currently connected."""

    bot_identities: frozenset[str] = frozenset()
    speakers: dict[str, Speaker] = field(default_factory=dict)
    # Audio track sid -> owning identity, so we can tear down STT on unpublish.
    track_owner: dict[str, str] = field(default_factory=dict)

    def is_bot(self, identity: str) -> bool:
        return identity in self.bot_identities

    def role_of(self, participant: rtc.Participant | rtc.RemoteParticipant) -> ParticipantRole:
        """Prefer LiveKit's own signals over name matching.

        ``attributes['role']`` is set by the token we mint, and the token role attribute
        bots are also tagged by the token role attribute. Identity matching
        is only the last resort.
        """
        attrs = getattr(participant, "attributes", None) or {}
        if attrs.get("role") == "bot":
            return ParticipantRole.BOT
        if self.is_bot(participant.identity):
            return ParticipantRole.BOT
        return ParticipantRole.HUMAN

    def add(self, participant: rtc.RemoteParticipant) -> Speaker:
        role = self.role_of(participant)
        speaker = Speaker(
            identity=participant.identity,
            name=participant.name or participant.identity,
            role=role,
        )
        self.speakers[participant.identity] = speaker
        log.stage(
            TAG_ROOM,
            event="participant_joined",
            identity=speaker.identity,
            name=speaker.name,
            role=role.value,
            humans=self.human_count,
        )
        return speaker

    def remove(self, identity: str) -> Speaker | None:
        speaker = self.speakers.pop(identity, None)
        for sid, owner in list(self.track_owner.items()):
            if owner == identity:
                del self.track_owner[sid]
        if speaker:
            log.stage(
                TAG_ROOM,
                event="participant_left",
                identity=identity,
                role=speaker.role.value,
                humans=self.human_count,
            )
        return speaker

    def get(self, identity: str) -> Speaker | None:
        return self.speakers.get(identity)

    def name_of(self, identity: str) -> str:
        speaker = self.speakers.get(identity)
        return speaker.name if speaker else identity

    @property
    def humans(self) -> list[Speaker]:
        return [s for s in self.speakers.values() if not s.is_bot]

    @property
    def human_count(self) -> int:
        return len(self.humans)

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "identity": s.identity,
                "name": s.name,
                "role": s.role.value,
                "joined_at": s.joined_at,
            }
            for s in self.speakers.values()
        ]
