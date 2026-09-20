"""Response mode = f(explicit message intent, conversation preference, Voice button)."""

import pytest

from app.routing.voice_policy import (
    detect_output_intent,
    has_explicit_voice_intent,
    resolve_response,
    should_speak,
)


@pytest.mark.parametrize(
    "text",
    [
        "hello", "Hi", "kaise ho?", "AI kya hai?", "Tell me about AI", "How are you?",
        "ek kahani sunao",  # "tell a story" - not a request for audio
        "Sathi tum batao AI kya hota hai", "or btao", "bhai kya haal hai", "",
        # the word "voice" / "speak" / TTS by themselves are NOT requests
        "What is voice AI?", "How does TTS work?", "Why isn't voice working?",
        "voice room kya hota hai", "Explain the speak function in Python",
        "how do I say it politely in an email?", "voice message kaise bhejte hain",
        "Hindi mein samjhao", "Explain this mujhe simple mein", "English mein samjhao",
    ],
)
def test_no_voice_request_is_detected(text):
    assert not has_explicit_voice_intent(text)
    assert should_speak(voice_mode=False, user_text=text) is False


@pytest.mark.parametrize(
    "text",
    [
        # English
        "Talk to me in voice.", "Hi, talk to me in voice.", "speak to me", "say it", "please say it",
        "tell me verbally", "answer with voice", "respond by voice", "How are you? Speak your answer.",
        "say it out loud", "read it aloud", "Respond in English and speak it.",
        # Hindi
        "mujhse baat karo", "bol ke batao", "awaaz mein batao", "voice mein batao", "bolkar samjhao",
        "Hindi mein bol ke samjhao", "mujhe sunao",
        # Hinglish
        "bhai voice mein bata", "voice mein bol", "bol ke samjha", "mujhse voice mein baat kar",
        "bhai bol kar bata na", "bhai voice mein baat kar", "English mein bolo",
    ],
)
def test_voice_requests_are_detected(text):
    assert has_explicit_voice_intent(text), text
    assert resolve_response(text=text, voice_mode=False).mode == "voice"


@pytest.mark.parametrize(
    "text",
    [
        "only text", "just type", "reply in text", "don't speak", "Don't speak, just type.", "Now only text.",
        "text mein batao", "likh ke batao", "chat mein reply karo", "sirf text", "no voice please",
    ],
)
def test_text_requests_are_detected_and_beat_voice(text):
    assert detect_output_intent(text).mode == "text", text
    assert resolve_response(text=text, voice_mode=True, pref="voice").mode == "text"


def test_sticky_vs_one_shot():
    assert resolve_response(text="Hi, talk to me in voice.", voice_mode=False).new_pref == "voice"
    assert resolve_response(text="Now only text.", voice_mode=False, pref="voice").new_pref == "text"
    assert resolve_response(text="mujhse baat karo", voice_mode=False).new_pref == "voice"
    # one-shot: this reply only, the preference is untouched
    assert resolve_response(text="voice mein batao", voice_mode=False).new_pref is None
    assert resolve_response(text="likh ke batao", voice_mode=True).new_pref is None


def test_four_way_matrix():
    # TEXT -> TEXT
    assert resolve_response(text="How are you?", voice_mode=False).mode == "text"
    # TEXT -> VOICE
    assert resolve_response(text="How are you? Speak your answer.", voice_mode=False).mode == "voice"
    # VOICE -> VOICE (voice mode on, nothing said about output)
    assert resolve_response(text="How are you?", voice_mode=True).mode == "voice"
    # VOICE -> TEXT
    assert resolve_response(text="Don't speak, just type.", voice_mode=True).mode == "text"


def test_preference_rules():
    assert resolve_response(text="hello", voice_mode=False, pref="voice").mode == "voice"
    assert resolve_response(text="hello", voice_mode=True, pref="text").mode == "text"  # explicit text beats the button
    assert resolve_response(text="hello", voice_mode=True, pref="auto").mode == "voice"
    assert resolve_response(text="hello", voice_mode=False, pref="auto").mode == "text"


def test_default_is_silent():
    assert should_speak(voice_mode=False) is False
    assert resolve_response(text="", voice_mode=False).reason == "default"
