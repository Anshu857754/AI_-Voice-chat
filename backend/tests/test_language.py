"""Language understanding: Hindi, Hinglish, Roman Hindi, English, bot addressing."""

from __future__ import annotations

from app.models.participant import BotId
from app.pipeline.language import Language, strip_bot_address, understand


class TestLanguageDetection:
    def test_hindi_devanagari(self):
        u = understand("मशीन लर्निंग क्या है?")
        assert u.language is Language.HINDI

    def test_hinglish_mixed(self):
        u = understand("AI basically kaise kaam karta hai?")
        assert u.language is Language.HINGLISH
        assert u.is_question

    def test_roman_hindi(self):
        u = understand("Waise aaj weather kaafi achha hai.")
        assert u.language is Language.ROMAN_HINDI

    def test_plain_english(self):
        u = understand("Can you explain cloud computing?")
        assert u.language is Language.ENGLISH
        assert u.is_question

    def test_english_with_hindi_grammar_is_hinglish(self):
        u = understand("Cloud mein data actually kaha store hota hai?")
        assert u.language is Language.HINGLISH

    def test_english_function_words_dont_force_hindi(self):
        """'is'/'in'/'to' are valid Hindi tokens too; a plain English sentence
        using them must not be misread as Hinglish."""
        u = understand("What is this in the room?")
        assert u.language is Language.ENGLISH

    def test_reply_language_for_english_question_is_hinglish(self):
        """Spec: comfortable with English questions, but this is a Hinglish room."""
        u = understand("Can you explain cloud computing?")
        assert u.reply_language is Language.HINGLISH

    def test_explicit_english_request_honoured(self):
        u = understand("Explain it in English please")
        assert u.wants_english
        assert u.reply_language is Language.ENGLISH

    def test_explicit_hindi_request_honoured(self):
        u = understand("Isko Hindi mein samjhao")
        assert u.wants_hindi
        assert u.reply_language is Language.HINDI


class TestSmallTalkAndQuestions:
    def test_small_talk_suppressed(self):
        for text in ("haan", "thanks", "hello", "ok theek hai", "bye"):
            u = understand(text)
            assert u.is_small_talk, f"expected small talk: {text}"

    def test_question_detected(self):
        u = understand("AI kya hota hai?")
        assert u.is_question

    def test_request_verb_detected_as_question(self):
        u = understand("Machine learning simple words mein batao.")
        assert u.is_question


class TestFollowupAndMemory:
    def test_pronoun_followup(self):
        u = understand("Unki koi famous movie batao.")
        assert u.is_followup

    def test_simple_followup_phrase(self):
        u = understand("Thoda aur simple batao")
        assert u.is_followup

    def test_memory_query_detected(self):
        u = understand("Maine apne baare mein kya bataya tha?")
        assert u.is_memory_query
        assert u.is_question  # memory queries always warrant a response

    def test_self_disclosure_detected(self):
        u = understand("Mera naam Rahul hai aur mujhe cricket pasand hai.")
        assert u.is_self_disclosure

    def test_self_disclosure_not_confused_with_memory_query(self):
        disclosure = understand("Mera naam Rahul hai.")
        query = understand("Mera naam kya hai?")
        assert disclosure.is_self_disclosure and not disclosure.is_memory_query
        assert query.is_memory_query and not query.is_self_disclosure


class TestBotAddressing:
    def test_dost_addressed(self):
        u = understand("AI Dost, tum answer karo.")
        assert u.addressed_bots == (BotId.DOST,)

    def test_sathi_addressed(self):
        u = understand("AI Sathi, ek example do.")
        assert u.addressed_bots == (BotId.SATHI,)

    def test_both_addressed_in_mention_order(self):
        u = understand("AI Dost, tum answer karo. AI Sathi, baad mein ek example dena.")
        assert u.addressed_bots == (BotId.DOST, BotId.SATHI)
        assert u.addresses_multiple_bots

    def test_reversed_mention_order_preserved(self):
        u = understand("Sathi pehle bolo, phir Dost tum batana.")
        assert u.addressed_bots[0] is BotId.SATHI

    def test_no_false_positive_on_similar_words(self):
        # "dostana" contains "dost" as a substring but is not addressing the bot.
        u = understand("Dostana ek movie ka naam hai.")
        assert BotId.DOST not in u.addressed_bots

    def test_devanagari_address(self):
        u = understand("एआई दोस्त, तुम बताओ।")
        assert u.addressed_bots == (BotId.DOST,)

    def test_strip_bot_address_removes_vocative(self):
        assert strip_bot_address("AI Dost, ek example do") == "ek example do"
        assert strip_bot_address("AI Sathi: thoda simple batao") == "thoda simple batao"

    def test_strip_bot_address_noop_without_vocative(self):
        assert strip_bot_address("kya hota hai") == "kya hota hai"
