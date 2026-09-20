"""Responder claims: at most ONE AI may answer any given message_id.

The router already picks a single AI, but the claim is a second, independent
guard: if two handlers (or, by mistake, two worker processes sharing a store)
try to answer the same message, only the first ``claim`` wins. Everyone else
gets ``False`` and must stay silent.
"""

from __future__ import annotations

from collections import OrderedDict

from app.models.participant import BotId

_MAX_CLAIMS = 500


class ResponderClaims:
    def __init__(self) -> None:
        self._owners: OrderedDict[str, BotId] = OrderedDict()

    def claim(self, message_id: str, bot: BotId) -> bool:
        """True if ``bot`` now owns the reply to ``message_id`` (idempotent for the owner)."""
        owner = self._owners.get(message_id)
        if owner is not None:
            return owner is bot
        self._owners[message_id] = bot
        while len(self._owners) > _MAX_CLAIMS:
            self._owners.popitem(last=False)
        return True

    def owner(self, message_id: str) -> BotId | None:
        return self._owners.get(message_id)
