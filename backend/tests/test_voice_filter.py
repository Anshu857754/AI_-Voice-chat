"""Echo / noise guards: the AI's own voice must never become a user message."""

import pytest

from app.pipeline.voice_filter import parse_languages, voice_ignore_reason

LANGS = parse_languages("en,hi")


def reason(**kw):
    base = dict(
        text="Cloud computing kya hai",
        confidence=0.9,
        language="en",
        ai_speaking=False,
        since_ai_speech_s=None,
        min_confidence=0.65,
        allowed_languages=LANGS,
        tail_s=1.2,
    )
    base.update(kw)
    return voice_ignore_reason(**base)


def test_clear_speech_is_accepted():
    assert reason() is None
    assert reason(language="hi", since_ai_speech_s=5.0) is None
    assert reason(language=None) is None


def test_speech_while_ai_speaks_is_echo():
    assert reason(ai_speaking=True) == "ai_speaking"


def test_speech_right_after_ai_stops_is_still_echo_but_later_is_fine():
    assert reason(since_ai_speech_s=0.5) == "ai_speech_tail"
    assert reason(since_ai_speech_s=1.5) is None


def test_a_fragment_that_names_an_ai_is_kept_despite_low_confidence():
    assert reason(text="Sathi,", confidence=0.5, language="multi", names_an_ai=True) is None
    # ...but never while an AI is speaking (that is still echo)
    assert reason(text="Sathi,", confidence=0.5, names_an_ai=True, ai_speaking=True) == "ai_speaking"


@pytest.mark.parametrize(
    "kw,expected",
    [
        ({"text": "Debo no de ser la sed.", "confidence": 0.6, "language": "es"}, "low_confidence"),
        ({"confidence": 0.5}, "low_confidence"),
        ({"language": "es"}, "language"),
        ({"text": "...", "confidence": 0.99}, "no_words"),
    ],
)
def test_garbage_is_filtered(kw, expected):
    assert reason(**kw).startswith(expected)
