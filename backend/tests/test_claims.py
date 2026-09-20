"""One responder per message_id, no matter how many handlers try."""

from app.models.participant import BotId
from app.routing.claims import ResponderClaims


def test_first_claim_wins_and_second_ai_is_denied():
    c = ResponderClaims()
    assert c.claim("m1", BotId.DOST)
    assert not c.claim("m1", BotId.SATHI)
    assert c.owner("m1") is BotId.DOST


def test_claim_is_idempotent_for_the_owner_only():
    c = ResponderClaims()
    assert c.claim("m1", BotId.SATHI)
    assert c.claim("m1", BotId.SATHI)
    assert not c.claim("m1", BotId.DOST)


def test_messages_are_independent_and_memory_is_bounded():
    c = ResponderClaims()
    assert c.claim("a", BotId.DOST) and c.claim("b", BotId.SATHI)
    for i in range(600):
        c.claim(f"x{i}", BotId.DOST)
    assert c.owner("a") is None  # oldest evicted
    assert c.owner("x599") is BotId.DOST
