"""Text normalization, language detection and lightweight intent extraction.

This sits between STT/chat and the router. It is deliberately rule-based and
synchronous: the router must decide in single-digit milliseconds, before any
LLM call, so nothing here may touch the network.

What it produces per utterance:
  * a normalized matching form (case/punctuation folded, Devanagari preserved)
  * a language label covering Hindi, Roman Hindi, Hinglish and English
  * whether the speaker explicitly asked for English
  * whether the utterance is a question / request at all
  * whether it is pure small talk that needs no AI answer
  * which bots, if any, were addressed by name
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from app.models.participant import BotId

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_PUNCT = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)
_WS = re.compile(r"\s+")


class Language(str, Enum):
    """Observed language style of an utterance."""

    HINDI = "hindi"  # Devanagari script
    ROMAN_HINDI = "roman_hindi"  # Hindi written in Latin script
    HINGLISH = "hinglish"  # Hindi grammar + English technical words
    ENGLISH = "english"
    UNKNOWN = "unknown"

    @property
    def is_hindi_family(self) -> bool:
        return self in (Language.HINDI, Language.ROMAN_HINDI, Language.HINGLISH)


# Hindi function words in Latin script. Function words (not content words) are
# the reliable signal for Roman Hindi, because they survive code-switching:
# a sentence can be full of English nouns and still be Hindi grammatically.
_ROMAN_HINDI_MARKERS = frozenset(
    """
    kya kyu kyun kaise kaisa kaisi kab kahan kaha kaun kitna kitni kitne
    hai hain ho hota hoti hote hua hui huye tha thi the hoga hogi
    ka ki ke ko se me mein par pe tak liye lie
    mera meri mere tera teri tere uska uski uske unka unki unke
    apna apni apne hamara hamari hamare tumhara tumhari tumhare
    main mai hum tum tu aap wo woh ye yeh is us in un
    nahi nahin na haan han ji bilkul acha achha accha theek thik sahi
    batao bata bataya samjhao samjha samjhaao sunao dedo dena do diya
    karo kar karna karta karti karte kiya karenge karunga
    chahiye chahta chahti mil milta jana jaana aana raha rahi rahe
    thoda thodi zara sirf bas aur ya lekin par kyunki agar toh to
    matlab yaani waise abhi phir fir bhi hi sab kuch kuchh koi
    ek do teen bahut kaafi zyada kam
    ruko ruk rukiye suno sunno dekho dekh chalo arre arey
    """.split()
)

# English words so common in Indian tech conversation that their presence does
# NOT make an utterance English. Used to avoid misclassifying Hinglish.
_TECH_ENGLISH = frozenset(
    """
    ai ml technology tech machine learning deep neural network data database
    cloud computing server servers storage internet software hardware model
    models api code coding program programming algorithm algorithms
    computer laptop mobile phone app application website web
    room mic microphone audio video call meeting chat message
    decision decisions example examples simple basic advanced
    python javascript react node docker kubernetes git
    gpu cpu memory ram disk file files backup security
    training dataset prompt token latency streaming
    """.split()
)

_ENGLISH_FUNCTION_WORDS = frozenset(
    """
    the a an is are was were be been being am do does did done
    can could will would shall should may might must
    what why how when where who which whose whom
    i you he she it we they me him her us them my your his their our
    of in on at to for with from by about into over under
    and or but if then than that this these those there here
    not no yes please tell explain give show say said
    have has had get got make made take took come came go went
    some any all more most much many few little
    """.split()
)

# Tokens that are valid words in BOTH languages: Hindi "is/us/in/un" (this/that
# /these/those), "me" (in), "to" (so), "do" (two), "the" (were), "no"/"na".
# Counting these as Hindi evidence misclassifies plain English sentences such as
# "Explain it in English", so they only reinforce a Hindi reading that some
# unambiguous marker already established.
_AMBIGUOUS_MARKERS = _ROMAN_HINDI_MARKERS & _ENGLISH_FUNCTION_WORDS
_STRONG_HINDI_MARKERS = _ROMAN_HINDI_MARKERS - _ENGLISH_FUNCTION_WORDS

# Explicit "answer in English" requests. Honoured over the detected language.
_ENGLISH_REQUEST_PATTERNS = (
    re.compile(r"\bin\s+english\b", re.I),
    re.compile(r"\benglish\s+m(?:e|ein|ain)\b", re.I),
    re.compile(r"\benglish\s+(?:me|mein)\s+(?:bol|bata|samjha|answer|reply)", re.I),
    re.compile(r"\b(?:answer|reply|respond|speak|explain|talk)\s+(?:me\s+)?in\s+english\b", re.I),
    re.compile(r"\bswitch\s+to\s+english\b", re.I),
)

_HINDI_REQUEST_PATTERNS = (
    re.compile(r"\b(?:hindi|हिंदी|हिन्दी)\s*(?:m(?:e|ein|ain)|में)\b", re.I),
    re.compile(r"\bin\s+hindi\b", re.I),
    re.compile(r"\bswitch\s+to\s+hindi\b", re.I),
)

# Question / request intent.
_QUESTION_WORDS = frozenset(
    """
    kya kyu kyun kaise kaisa kaisi kab kahan kaha kaun kitna kitni kitne konsa kaunsa
    what why how when where who which whose whom
    क्या क्यों कैसे कब कहां कहाँ कौन कितना कितनी कितने
    """.split()
)

_REQUEST_VERBS = frozenset(
    """
    batao bata btao bta bataiye bataye samjhao samjha samjhaao samjhaiye sunao suna
    dedo dena do dijiye de bolo bol boliye likho likh
    explain tell describe define show give list compare summarize
    help answer elaborate clarify translate
    बताओ बताइए समझाओ समझाइए दो बोलो
    """.split()
)

# Utterances that are conversational filler between humans. A bot answering
# these is what makes a room feel like a chatbot rather than a meeting.
# Ack words may be doubled up in natural speech ("ok theek hai", "haan ji"),
# so the pattern accepts a run of them, not just a single exact phrase.
_ACK_WORD = r"(?:haan|han|hmm+|hm+|ok+|okay|acha+|achha|theek\s+hai|thik\s+hai|ji|yes|yeah|yup|no|nope|nahi|nahin)"
_SMALL_TALK_PATTERNS = (
    re.compile(rf"^{_ACK_WORD}(?:[\s,]+{_ACK_WORD})*[\s!.]*$", re.I),
    re.compile(r"^(?:hello|hi|hey|namaste|namaskar|arre|arey|oye)[\s!.]*$", re.I),
    re.compile(r"^(?:thanks|thank you|thx|shukriya|dhanyavad|great|nice|cool|wow|awesome|badhiya|mast|sahi hai)[\s!.]*$", re.I),
    re.compile(r"^(?:bye|byee|goodbye|good night|gn|see you|chalta hu|chalta hoon)[\s!.]*$", re.I),
    # Weather / mood remarks addressed at the room, not at a bot.
    re.compile(r"^waise\b.*\b(?:weather|mausam|achha hai|acha hai)\b.*$", re.I),
)

# Follow-up cues: short utterances that only make sense as a continuation of
# whatever was just said. These drive continuity routing.
_FOLLOWUP_CUES = (
    re.compile(r"\b(?:aur|or)\s+(?:simple|easy|detail|batao|btao|bata|bta|bolo|thoda|kuch)\b", re.I),
    re.compile(r"\bthoda\s+(?:aur|or|and)?\s*(?:simple|easy|detail|slow|clear)\b", re.I),
    re.compile(r"\b(?:simple|easy)\s+(?:words?\s+)?(?:m(?:e|ein|ain)|में)\b", re.I),
    re.compile(r"\b(?:iska|uska|iski|uski|inka|unka|inki|unki|iske|uske|inke|unke)\b", re.I),
    re.compile(r"\b(?:ye|yeh|wo|woh|is|us|isko|usko|ismein|usmein)\s+(?:kya|kaise|kyun|matlab)\b", re.I),
    re.compile(r"\b(?:example|udaharan)\b.*\b(?:do|dedo|dena|de|batao|samjhao|samjha)\b", re.I),
    re.compile(r"\b(?:example|udaharan)\s+se\b", re.I),
    re.compile(r"^(?:ek|एक)?\s*(?:example|udaharan)\b", re.I),
    re.compile(r"\b(?:phir|fir|then|uske baad|iske baad)\b", re.I),
    re.compile(r"\b(?:matlab|meaning|mtlb)\b", re.I),
    re.compile(r"\b(?:continue|carry on|aage|agey)\b", re.I),
    re.compile(r"\b(?:repeat|dobara|dubara|firse|phir se)\b", re.I),
    re.compile(r"\b(?:elaborate|detail\s+m(?:e|ein)|expand)\b", re.I),
    # Pronoun-only references ("unki", "uski") already covered above; also
    # bare short comparatives such as "aur?" / "and?".
    re.compile(r"^(?:aur|or|and)\??$", re.I),
)

# Self-disclosure: the speaker is telling the room something about themselves.
# Not a question, but it feeds speaker-specific memory and deserves a short
# acknowledgement - total silence after "mera naam Rahul hai" feels broken.
_SELF_DISCLOSURE_PATTERNS = (
    re.compile(r"\bmer[aei]\s+naam\b", re.I),
    re.compile(r"\bmy\s+name\s+is\b", re.I),
    re.compile(r"\bmain\s+\w+\s+(?:hoon|hun|hu|ho)\b", re.I),
    re.compile(r"\bmujhe\s+.+\s+(?:pasand|achha|acha|accha)\b", re.I),
    re.compile(r"\bi\s+(?:like|love|enjoy|prefer|work|study|live)\b", re.I),
    re.compile(r"\bi\s?.?m\s+(?:from|a|an)\b", re.I),
    re.compile(r"\bmain\s+.+\s+se\s+(?:hoon|hun|hu)\b", re.I),
    re.compile(r"\bमेर[ाीे]\s+नाम\b"),
    re.compile(r"\bमुझे\s+.+\s+पसंद\b"),
)

# Memory recall: the speaker asks what the room knows about *them*.
# Routed like a question, but answered from that speaker's own memory only.
_MEMORY_QUERY_PATTERNS = (
    re.compile(r"\b(?:maine|mai ne|main ne)\s+(?:apne\s+)?(?:baare\s+m(?:e|ein)|bare\s+m(?:e|ein))\b", re.I),
    re.compile(r"\bmere\s+(?:baare|bare)\s+m(?:e|ein)\b", re.I),
    re.compile(r"\bmer[aei]\s+naam\s+kya\b", re.I),
    re.compile(r"\bmujhe\s+kya\s+(?:pasand|achha|acha)\b", re.I),
    re.compile(r"\bwhat\s+did\s+i\s+(?:tell|say)\b", re.I),
    re.compile(r"\bwhat\s+do\s+you\s+(?:know|remember)\s+about\s+me\b", re.I),
    re.compile(r"\bwhat\s?.?s\s+my\s+name\b", re.I),
    re.compile(r"\b(?:yaad|remember)\s+(?:hai|h|karo)\b.*\bm(?:e|ein)r[aei]\b", re.I),
    re.compile(r"\bमेरे\s+बारे\s+में\b"),
)

# Barge-in phrases: a human cutting a bot off. Recognised from *interim*
# transcripts so we can cancel audio before the sentence even ends.
_INTERRUPT_CUES = (
    re.compile(r"\b(?:ruko|ruk|rukiye|rukho|stop|wait|hold on|thehro|theharo)\b", re.I),
    re.compile(r"\b(?:ek second|ek sec|one second|one sec|sun|suno|listen)\b", re.I),
    re.compile(r"\b(?:nahi nahi|no no|arre nahi|not that)\b", re.I),
    re.compile(r"\b(?:chup|bas|enough|that.s enough)\b", re.I),
)

# --- Bot addressing --------------------------------------------------------
# Includes plausible STT variants: Deepgram will sometimes render "dost" as
# "dosth"/"those" and "sathi" as "saathi"/"sathee", and Devanagari output is
# possible when the speaker uses Hindi script.
_DOST_TERMS = (
    "ai dost", "a i dost", "roxstar dost", "roxstar ai dost",
    "dost", "dosth", "dhost", "dostji", "dost ji",
    "एआई दोस्त", "दोस्त", "रॉकस्टार दोस्त",
)
_SATHI_TERMS = (
    "ai sathi", "a i sathi", "roxstar sathi", "roxstar ai sathi",
    "sathi", "saathi", "sathee", "saathee", "sathiji", "sathi ji",
    "एआई साथी", "साथी", "साथी", "रॉकस्टार साथी",
)

_BOT_TERMS: dict[BotId, tuple[str, ...]] = {
    BotId.DOST: _DOST_TERMS,
    BotId.SATHI: _SATHI_TERMS,
}

# Generic address that means "one of you bots", not a specific one.
_GENERIC_BOT_TERMS = ("roxstar ai", "roxstar", "ai bot", "bot", "assistant")


@dataclass
class Utterance:
    """A normalized, understood utterance ready for routing."""

    raw: str
    normalized: str
    language: Language
    is_question: bool
    is_small_talk: bool
    is_followup: bool
    is_interrupt_cue: bool
    is_self_disclosure: bool
    is_memory_query: bool
    wants_english: bool
    wants_hindi: bool
    addressed_bots: tuple[BotId, ...] = ()
    addressed_generic: bool = False
    matched_terms: tuple[str, ...] = field(default_factory=tuple)
    word_count: int = 0

    @property
    def addresses_single_bot(self) -> bool:
        return len(self.addressed_bots) == 1

    @property
    def addresses_multiple_bots(self) -> bool:
        return len(self.addressed_bots) > 1

    @property
    def reply_language(self) -> Language:
        """Language the bot should answer in."""
        if self.wants_english:
            return Language.ENGLISH
        if self.wants_hindi:
            return Language.HINDI
        if self.language is Language.ENGLISH:
            # English question -> reply in Hinglish, per the product brief:
            # "comfortable with English questions" but still an Indian voice room.
            return Language.HINGLISH
        if self.language is Language.UNKNOWN:
            return Language.HINGLISH
        return self.language


def normalize(text: str) -> str:
    """Fold case, strip punctuation and collapse whitespace.

    Unicode is NFC-normalized first so Devanagari written with combining marks
    compares equal to its precomposed form.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFC", text).strip().lower()
    folded = _PUNCT.sub(" ", folded)
    return _WS.sub(" ", folded).strip()


def _tokens(normalized: str) -> list[str]:
    return [t for t in normalized.split(" ") if t]


def detect_language(text: str, normalized: str | None = None) -> Language:
    """Classify Hindi / Roman Hindi / Hinglish / English.

    The order matters:
      1. Devanagari anywhere -> HINDI.
      2. Unambiguous Hindi function words present -> Roman Hindi, upgraded to
         HINGLISH if non-trivial English content words also appear.
      3. Otherwise English if English function words appear.
    """
    norm = normalized if normalized is not None else normalize(text)
    if not norm:
        return Language.UNKNOWN
    if _DEVANAGARI.search(text):
        return Language.HINDI

    toks = _tokens(norm)
    if not toks:
        return Language.UNKNOWN

    strong_hindi = sum(1 for t in toks if t in _STRONG_HINDI_MARKERS)
    weak_hindi = sum(1 for t in toks if t in _AMBIGUOUS_MARKERS)
    english_hits = sum(1 for t in toks if t in _ENGLISH_FUNCTION_WORDS)
    # Content words that are neither Hindi markers nor generic English glue.
    english_content = sum(
        1
        for t in toks
        if t not in _ROMAN_HINDI_MARKERS and t not in _ENGLISH_FUNCTION_WORDS and len(t) > 2
    )
    tech_hits = sum(1 for t in toks if t in _TECH_ENGLISH)

    # Ambiguous tokens only count once a real Hindi marker is present.
    hindi_hits = strong_hindi + (weak_hindi if strong_hindi else 0)

    if hindi_hits:
        # Hindi grammar present. If meaningful English content rides along
        # (technical vocabulary or plain English words), call it Hinglish.
        if tech_hits or english_hits or english_content > hindi_hits:
            return Language.HINGLISH
        return Language.ROMAN_HINDI

    if english_hits or tech_hits or english_content:
        return Language.ENGLISH
    return Language.UNKNOWN


def _wants_english(text: str) -> bool:
    return any(p.search(text) for p in _ENGLISH_REQUEST_PATTERNS)


def _wants_hindi(text: str) -> bool:
    return any(p.search(text) for p in _HINDI_REQUEST_PATTERNS)


def is_question(normalized: str) -> bool:
    """Question or explicit request for information."""
    if not normalized:
        return False
    toks = set(_tokens(normalized))
    if toks & _QUESTION_WORDS:
        return True
    if toks & _REQUEST_VERBS:
        return True
    return False


def is_small_talk(raw: str, normalized: str) -> bool:
    """Utterances a bot should stay out of."""
    if not normalized:
        return True
    return any(p.match(normalized) or p.match(raw.strip()) for p in _SMALL_TALK_PATTERNS)


def is_followup(text: str) -> bool:
    return any(p.search(text) for p in _FOLLOWUP_CUES)


def is_interrupt_cue(text: str) -> bool:
    """True when the text looks like a human cutting the bot off."""
    return any(p.search(text) for p in _INTERRUPT_CUES)


def is_self_disclosure(text: str) -> bool:
    return any(p.search(text) for p in _SELF_DISCLOSURE_PATTERNS)


def is_memory_query(text: str) -> bool:
    return any(p.search(text) for p in _MEMORY_QUERY_PATTERNS)


def find_addressed_bots(normalized: str) -> tuple[tuple[BotId, ...], tuple[str, ...], bool]:
    """Detect bot names, preserving the order they were addressed in.

    Ordering matters for "AI Dost, tum answer karo. AI Sathi, baad mein ek
    example dena." - Dost must speak first because Dost was named first.
    Returns ``(bots_in_mention_order, matched_terms, generic_address)``.
    """
    if not normalized:
        return (), (), False

    padded = f" {normalized} "
    hits: list[tuple[int, BotId, str]] = []
    for bot, terms in _BOT_TERMS.items():
        best: tuple[int, str] | None = None
        for term in terms:
            # Word-boundary match so "dostana" or "sathiya" do not trigger.
            idx = padded.find(f" {term} ")
            if idx == -1:
                # Also allow the term at the very start/end adjacent to padding.
                continue
            if best is None or idx < best[0]:
                best = (idx, term)
        if best is not None:
            hits.append((best[0], bot, best[1]))

    hits.sort(key=lambda h: h[0])
    bots = tuple(h[1] for h in hits)
    matched = tuple(h[2] for h in hits)
    generic = not bots and any(f" {t} " in padded for t in _GENERIC_BOT_TERMS)
    return bots, matched, generic


def understand(text: str) -> Utterance:
    """Full analysis of one utterance. Pure function, no I/O."""
    raw = (text or "").strip()
    norm = normalize(raw)
    lang = detect_language(raw, norm)
    bots, matched, generic = find_addressed_bots(norm)
    memory_query = is_memory_query(norm)
    return Utterance(
        raw=raw,
        normalized=norm,
        language=lang,
        is_question=is_question(norm) or memory_query,
        is_small_talk=is_small_talk(raw, norm),
        is_followup=is_followup(norm),
        is_interrupt_cue=is_interrupt_cue(norm),
        # A memory query mentions the speaker's own details, so it also matches
        # the disclosure patterns; the query reading wins.
        is_self_disclosure=is_self_disclosure(norm) and not memory_query,
        is_memory_query=memory_query,
        wants_english=_wants_english(raw),
        wants_hindi=_wants_hindi(raw),
        addressed_bots=bots,
        addressed_generic=generic,
        matched_terms=matched,
        word_count=len(_tokens(norm)),
    )


def strip_bot_address(text: str) -> str:
    """Remove a leading bot name so the LLM sees the request, not the vocative.

    "AI Dost, ek example do" -> "ek example do"
    """
    cleaned = text.strip()
    for terms in _BOT_TERMS.values():
        for term in sorted(terms, key=len, reverse=True):
            pattern = re.compile(rf"^\s*{re.escape(term)}\s*[,:!\-]*\s*", re.I)
            new = pattern.sub("", cleaned, count=1)
            if new != cleaned:
                return new.strip() or cleaned
    return cleaned
