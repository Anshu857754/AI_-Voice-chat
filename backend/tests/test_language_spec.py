"""Language behaviour from the product spec: follow the user's language and explicit requests."""

import pytest

from app.pipeline.language import Language, understand


@pytest.mark.parametrize(
    ("text", "language", "reply"),
    [
        ("How are you?", Language.ENGLISH, Language.ENGLISH),
        ("Hi, talk to me in voice.", Language.ENGLISH, Language.ENGLISH),
        ("Kaise ho?", Language.ROMAN_HINDI, Language.ROMAN_HINDI),
        ("bhai kya haal hai?", Language.ROMAN_HINDI, Language.ROMAN_HINDI),
        ("मुझे समझाओ", Language.HINDI, Language.HINDI),
        ("Explain this mujhe simple mein", Language.HINGLISH, Language.HINGLISH),
        ("bhai simple mein bata", Language.HINGLISH, Language.HINGLISH),
    ],
)
def test_detects_and_follows_the_users_language(text, language, reply):
    u = understand(text)
    assert u.language is language
    assert u.reply_language is reply


@pytest.mark.parametrize(
    ("text", "english", "hindi"),
    [
        ("Respond in English.", True, False),
        ("Respond in English and speak it.", True, False),
        ("English mein bolo.", True, False),
        ("Hindi mein samjhao.", False, True),
        ("Hindi mein bol ke samjhao.", False, True),
    ],
)
def test_explicit_language_requests(text, english, hindi):
    u = understand(text)
    assert u.wants_english is english
    assert u.wants_hindi is hindi


def test_language_can_switch_between_messages():
    assert understand("Explain cloud computing").reply_language is Language.ENGLISH
    assert understand("Ab Hindi mein samjhao").reply_language is Language.HINDI
    assert understand("Respond in English please").reply_language is Language.ENGLISH
