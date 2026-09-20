import pytest

from app.titles import derive_title


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Explain machine learning in simple Hindi", "Machine Learning in Hindi"),
        ("AI kya hai?", "What Is AI"),
        ("Sathi tum batao AI kya hota hai", "What Is AI"),
        ("cloud computing simple mein samjhao", "Cloud Computing"),
        ("Help me plan my React project", "Plan my React Project"),
        ("Hello Dost, mujhe Python ke decorators samjhao", "Python ke Decorators"),
    ],
)
def test_meaningful_messages_get_short_titles(text, expected):
    title = derive_title(text)
    assert title is not None
    assert 1 <= len(title.split()) <= 7
    assert title == expected


@pytest.mark.parametrize("text", ["hi", "hii", "Hello", "kaise ho?", "or btao", "ok", "Dost, kaise ho", "", "   ", "haan"])
def test_greetings_and_small_talk_have_no_title(text):
    assert derive_title(text) is None


def test_title_is_capped():
    t = derive_title("I need a detailed comparison between kubernetes docker swarm nomad and mesos for production workloads at scale")
    assert t is not None
    assert len(t.split()) <= 7
    assert len(t) <= 48
