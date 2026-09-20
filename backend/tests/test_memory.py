"""Speaker-specific memory: extraction accuracy and strict per-speaker isolation."""

from __future__ import annotations

from app.context.memory import KEY_INTEREST, KEY_LOCATION, KEY_NAME, MemoryStore, extract_facts


class TestFactExtraction:
    def test_name_and_interest_hinglish(self):
        facts = extract_facts("Mera naam Rahul hai aur mujhe cricket pasand hai.")
        keys = {f.key: f.value for f in facts}
        assert keys[KEY_NAME] == "Rahul"
        assert keys[KEY_INTEREST] == "cricket"

    def test_name_and_interest_english(self):
        facts = extract_facts("My name is Priya and I like music")
        keys = {f.key: f.value for f in facts}
        assert keys[KEY_NAME] == "Priya"
        assert keys[KEY_INTEREST] == "music"

    def test_location_hinglish(self):
        facts = extract_facts("Main Delhi se hoon")
        keys = {f.key: f.value for f in facts}
        assert keys[KEY_LOCATION] == "Delhi"

    def test_plain_question_extracts_nothing(self):
        assert extract_facts("Mera naam kya hai?") == []

    def test_no_facts_in_unrelated_text(self):
        assert extract_facts("AI kya hota hai?") == []


class TestSpeakerIsolation:
    def test_facts_do_not_leak_between_speakers(self):
        store = MemoryStore()
        store.ingest("u_rahul", "Rahul", "Mera naam Rahul hai aur mujhe cricket pasand hai.")
        store.ingest(
            "u_priya", "Priya", "Mera naam Priya hai, mujhe music pasand hai. Main Mumbai se hoon."
        )

        rahul_block = store.render_for("u_rahul")
        priya_block = store.render_for("u_priya")

        assert "cricket" in rahul_block
        assert "music" not in rahul_block
        assert "mumbai" not in rahul_block.lower()

        assert "music" in priya_block
        assert "mumbai" in priya_block.lower()
        assert "cricket" not in priya_block

    def test_unknown_speaker_has_no_facts(self):
        store = MemoryStore()
        assert store.render_for("nobody") == ""

    def test_roster_excludes_the_asking_speaker(self):
        store = MemoryStore()
        store.ingest("u_rahul", "Rahul", "Mera naam Rahul hai.")
        store.ingest("u_priya", "Priya", "Mera naam Priya hai.")
        roster = store.render_roster(exclude="u_rahul")
        assert "Priya" in roster
        assert "Rahul" not in roster

    def test_memory_recall_scenario(self):
        """Scenario 5: 'Maine apne baare mein kya bataya tha?' must answer only
        from Rahul's own facts."""
        store = MemoryStore()
        store.ingest("u_rahul", "Rahul", "Mera naam Rahul hai aur mujhe cricket pasand hai.")
        block = store.render_for("u_rahul")
        assert "Rahul" in block
        assert "cricket" in block


class TestFactUpdates:
    def test_single_value_fact_overwritten_not_accumulated(self):
        store = MemoryStore()
        store.ingest("u_rahul", "Rahul", "Main Delhi se hoon")
        store.ingest("u_rahul", "Rahul", "Actually main Pune se hoon")
        mem = store.for_speaker("u_rahul")
        assert mem.get(KEY_LOCATION) == ["Pune"]

    def test_interests_accumulate(self):
        store = MemoryStore()
        store.ingest("u_rahul", "Rahul", "Mujhe cricket pasand hai")
        store.ingest("u_rahul", "Rahul", "Mujhe music bhi pasand hai")
        mem = store.for_speaker("u_rahul")
        assert set(mem.get(KEY_INTEREST)) == {"cricket", "music"}

    def test_duplicate_fact_not_re_added(self):
        store = MemoryStore()
        added1 = store.ingest("u_rahul", "Rahul", "Mera naam Rahul hai")
        added2 = store.ingest("u_rahul", "Rahul", "Mera naam Rahul hai")
        assert len(added1) == 1
        assert len(added2) == 0

    def test_forget_clears_one_speaker_only(self):
        store = MemoryStore()
        store.ingest("u_rahul", "Rahul", "Mera naam Rahul hai")
        store.ingest("u_priya", "Priya", "Mera naam Priya hai")
        store.forget("u_rahul")
        assert store.render_for("u_rahul") == ""
        assert "Priya" in store.render_for("u_priya")
