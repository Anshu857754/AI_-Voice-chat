"""Shared pytest fixtures.

Nothing here touches the network or real credentials: providers under test are
either the deterministic pipeline modules themselves, or the Scripted/Failing
test doubles defined alongside each real provider (``ScriptedLLMProvider``,
``FailingSTTProvider``, ``SilentTTSProvider``, etc).
"""

from __future__ import annotations

import pytest

# Force a clean, known configuration for every test regardless of any local
# .env the developer has for manual runs. Individual tests override as needed.
_TEST_ENV = {
    "LIVEKIT_URL": "wss://test.invalid",
    "LIVEKIT_API_KEY": "test-key",
    "LIVEKIT_API_SECRET": "test-secret",
    "OPENROUTER_API_KEY": "",  # never a real key in tests
    "STT_API_KEY": "",
    "TTS_API_KEY": "",
    "DOST_VOICE_ID": "",
    "SATHI_VOICE_ID": "",
    "DOST_IDENTITY": "roxstar-ai-dost",
    "SATHI_IDENTITY": "roxstar-ai-sathi",
    "LOG_LEVEL": "WARNING",
}


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch: pytest.MonkeyPatch):
    from app.config.settings import reload_settings

    for key, value in _TEST_ENV.items():
        monkeypatch.setenv(key, value)
    reload_settings()
    yield
    reload_settings()


@pytest.fixture
def settings():
    from app.config.settings import get_settings

    return get_settings()
