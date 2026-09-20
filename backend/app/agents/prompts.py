"""Personas and per-turn style instructions for the two AI participants.

Design notes
------------
The personas are written *in* Hinglish, with concrete do/don't examples. That
matters far more than adjectives: telling a model "speak natural Hinglish"
produces textbook Hindi, while showing it "Ye kaafi simple hai" vs
"यह अत्यंत सरल है" reliably does not.

Replies are produced in **Roman script Hinglish**, not Devanagari, because:
  * it is how the target users actually write and read Hinglish, so the room
    transcript looks natural;
  * the spec's own expected outputs are romanized;
  * ElevenLabs' multilingual models handle romanized Hindi acceptably.
Devanagari does give slightly better TTS pronunciation - that trade-off and
how to flip it are documented in docs/provider-decisions.md.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from app.models.conversation import RoutingReason
from app.pipeline.language import Language

_PERSONAS_PATH = Path(__file__).resolve().parents[1] / "config" / "personas.toml"


def _load_personas() -> dict:
    with _PERSONAS_PATH.open("rb") as fh:
        return tomllib.load(fh)


_PERSONAS = _load_personas()
# Templates keep their {ai_name}/{other_ai}/{user_name} placeholders until a
# reply is built (see render_persona), so edits to personas.toml take effect
# on the next worker start without touching code.
DOST_PERSONA = _PERSONAS["common"] + "\n\n" + _PERSONAS["dost"]["persona"]
SATHI_PERSONA = _PERSONAS["common"] + "\n\n" + _PERSONAS["sathi"]["persona"]

_AI_NAME = {"dost": "AI Dost", "sathi": "AI Sathi"}


def render_persona(template: str, *, bot_value: str, user_name: str) -> str:
    """Fill the persona placeholders for one reply."""
    other = "sathi" if bot_value == "dost" else "dost"
    return (
        template.replace("{ai_name}", _AI_NAME.get(bot_value, "AI Dost"))
        .replace("{other_ai}", _AI_NAME[other])
        .replace("{user_name}", user_name or "user")
    )


# --- Per-turn style instruction -------------------------------------------

_LANGUAGE_INSTRUCTION = {
    Language.HINDI: (
        "User ne Hindi (Devanagari) mein poocha hai. Tum Roman script Hinglish "
        "mein jawab do - natural bolchaal wali, jaise Hindi bol rahe ho lekin "
        "English letters mein likh rahe ho."
    ),
    Language.ROMAN_HINDI: (
        "User ne Roman Hindi mein poocha hai. Usi style mein jawab do - Roman "
        "Hinglish, simple aur natural."
    ),
    Language.HINGLISH: (
        "User Hinglish bol raha hai. Tum bhi wahi natural Hinglish mix rakho - "
        "Hindi grammar, English technical words."
    ),
    Language.ENGLISH: (
        "User English mein baat kar raha hai. Simple, casual English mein "
        "jawab do."
    ),
    Language.UNKNOWN: "Natural Hinglish mein jawab do.",
}

_ENGLISH_ONLY_INSTRUCTION = (
    "User has explicitly asked for English. Reply in clear, simple, "
    "conversational Indian English. Keep it short - 2 to 3 sentences, this is "
    "a voice room. No Hindi words this time."
)

_HINDI_ONLY_INSTRUCTION = (
    "User ne Hindi maangi hai. Natural bolchaal wali Hindi mein jawab do "
    "(Roman script). Technical English words English mein hi rehne do."
)

# Extra nudges keyed on why the router picked this bot.
_REASON_INSTRUCTION: dict[RoutingReason, str] = {
    RoutingReason.EXPLICIT_ADDRESS: (
        "User ne tumhe naam se address kiya hai. Seedha jawab do - "
        "'haan boliye' type acknowledgement waste mat karo."
    ),
    RoutingReason.AI_CHAIN: (
        "AI-AI mode on hai: user ne tum dono ko aapas mein baat karne ki ijazat di hai. "
        "Doosre AI ne abhi jo kaha uska seedha, chhota jawab do aur baat aage badhao. "
        "User ke sawaal se juda raho."
    ),
    RoutingReason.EXPLICIT_MULTI_ADDRESS: (
        "User ne tumhe pehle answer karne ko kaha hai, aur doosre bot ko baad "
        "mein. Tum sirf apna hissa karo, doosre bot ke liye bol kar mat jao."
    ),
    RoutingReason.DIRECT_FOLLOWUP: (
        "Ye tumhare hi pichhle jawab ka follow-up hai. Dobara shuru se mat "
        "samjhao - jahan chhoda tha wahin se, aur simpler bolo."
    ),
    RoutingReason.CONVERSATION_CONTINUITY: (
        "Ye chal rahi baat ka continuation hai, possibly kisi doosre person "
        "se. Pichhle topic ko hi aage badhao - naya topic mat samjho. "
        "Pronouns (unki, iska, woh) pichhle topic se resolve karo."
    ),
    RoutingReason.INTERRUPTION_REDIRECT: (
        "User ne tumhe beech mein tok diya hai kyunki wo kuch alag chahta hai. "
        "Pichhli baat chhod do, jo ab poocha hai sirf wahi do - aur chhota rakho."
    ),
    RoutingReason.MEMORY_QUERY: (
        "User apne baare mein pooch raha hai. Sirf usi speaker ki di hui "
        "information use karo jo context mein hai. Agar kuch nahi pata to "
        "saaf bol do ki abhi tak kuch bataya nahi gaya - invent mat karo."
    ),
    RoutingReason.SELF_DISCLOSURE: (
        "User ne apne baare mein kuch bataya hai. Ek chhota, warm "
        "acknowledgement do (ek line) aur baat aage badhne do. Lecture mat do."
    ),
    RoutingReason.PERSONA_TOPIC: (
        "Ye topic tumhare comfort zone mein hai. Confident aur concise raho."
    ),
    RoutingReason.TURN_TAKING: (
        "Naya sawaal hai aur tumhari turn hai. Seedha, concise jawab do."
    ),
}

_BREVITY = "Ye bola jayega: 1-3 chhote sentences, seedha jawab, koi filler opening nahi."
_DETAIL = (
    "User ne detail maangi hai, to thoda vistar se samjhao (6-8 sentences tak), "
    "par bolchaal ke andaaz mein, bina lists ke."
)

_DETAIL_CUES = re.compile(
    r"\b(?:detail|detailed|vistar|explain|elaborate|step\s*by\s*step|"
    r"in\s*depth|lamba|poora|puri|samjhao|samjhaiye|deeply)\b",
    re.I,
)


def wants_detail(text: str) -> bool:
    """Did the user explicitly ask for a longer answer?"""
    return bool(_DETAIL_CUES.search(text or ""))


def max_sentences(bot_value: str) -> int:
    """Sentence cap for a normal (non-detail) reply, from personas.toml."""
    return int(_PERSONAS.get(bot_value, {}).get("max_sentences", 3))


def persona_for(bot_value: str) -> str:
    """System prompt for a bot, by :class:`BotId` value."""
    return DOST_PERSONA if bot_value == "dost" else SATHI_PERSONA


def style_instruction(
    *,
    reply_language: Language,
    wants_english: bool = False,
    wants_hindi: bool = False,
    detail: bool = False,
) -> str:
    """Language + length instruction for this specific turn."""
    if detail:
        base = style_instruction(
            reply_language=reply_language, wants_english=wants_english, wants_hindi=wants_hindi
        )
        return f"{base} {_DETAIL}"
    if wants_english:
        return _ENGLISH_ONLY_INSTRUCTION
    if wants_hindi:
        return f"{_HINDI_ONLY_INSTRUCTION} {_BREVITY}"
    base = _LANGUAGE_INSTRUCTION.get(reply_language, _LANGUAGE_INSTRUCTION[Language.UNKNOWN])
    return f"{base} {_BREVITY}"


def delivery_instruction(*, voice: bool) -> str:
    """Tell the model how its reply is delivered so it never claims it cannot speak.

    The text is always written by the model; audio is added by the system (TTS) when the
    user wants it. Without this, models sometimes answer "voice available nahi hai".
    """
    if voice:
        return (
            "Delivery: tumhara jawab text ke saath awaaz mein bhi sunaya jayega (system khud bolta hai). "
            "Kabhi mat kaho ki tum bol nahi sakte ya voice available nahi hai, aur user ko mic par bolne ya kuch aur karne "
            "ko mat kaho - tum khud bolke jawab de rahe ho. Agar user ne bas voice mein baat karne ko kaha, to chhote "
            "'theek hai' jaise ack ke saath seedha baat shuru karo. Natural bolchaal ka jawab do."
        )
    return (
        "Delivery: yeh jawab text mein dikhega. Agar user awaaz mein sunna chahe to system khud bol deta hai, "
        "isliye kabhi mat kaho ki voice available nahi hai. Agar user sirf text/voice badalne ko kahe "
        "to ek chhote acknowledgement ke saath uske sawaal ka jawab do."
    )


def reason_instruction(reason: RoutingReason) -> str:
    """Turn-shaping nudge based on why this bot was selected."""
    return _REASON_INSTRUCTION.get(reason, "")


# --- Fallbacks -------------------------------------------------------------
# Spoken when a provider fails. In-character, no jargon, no stack traces.

LLM_FAILURE_FALLBACK = {
    "dost": "Ek second, response generate karne mein issue aa gaya. Please dobara poochho.",
    "sathi": "Arre, ek second - mujhe response banane mein dikkat aa gayi. Phir se poochho na.",
}

TTS_FAILURE_NOTICE = {
    "dost": "(Awaaz mein dikkat aa rahi hai, jawab chat mein bhej diya hai.)",
    "sathi": "(Voice mein problem aa gayi, maine chat mein likh diya hai.)",
}

STT_FAILURE_NOTICE = (
    "Mic se aawaz samajhne mein dikkat aa rahi hai. Tab tak chat mein type kar sakte ho."
)


def failure_reply(bot_value: str) -> str:
    return LLM_FAILURE_FALLBACK.get(bot_value, LLM_FAILURE_FALLBACK["dost"])


def tts_failure_notice(bot_value: str) -> str:
    return TTS_FAILURE_NOTICE.get(bot_value, TTS_FAILURE_NOTICE["dost"])
