"""Sarvam adapter against a mocked HTTP transport (no network, no credentials)."""

import base64
import io
import json
import wave

import httpx
import pytest

from app.config.settings import Settings
from app.models.participant import BotId
from app.pipeline.tts import SilentTTSProvider, TTSError, build_tts_provider
from app.pipeline.tts_sarvam import SarvamTTSProvider, normalize_language

RATE = 22050


def wav_b64(seconds: float = 0.1, rate: int = RATE) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds))
    return base64.b64encode(buf.getvalue()).decode()


def provider(handler, **kw) -> SarvamTTSProvider:
    return SarvamTTSProvider(
        api_key="sk-secret-key", speaker="spk", model="bulbul-test", transport=httpx.MockTransport(handler), **kw
    )


@pytest.mark.asyncio
async def test_synthesizes_frames_and_sends_documented_fields():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"audios": [wav_b64(0.1)]})

    p = provider(handler, language="hi")
    frames = [f async for f in p.synthesize("Namaste dost").frames()]
    assert len(frames) == 5 and all(f.sample_rate == RATE and f.num_channels == 1 for f in frames)  # 100 ms in 20 ms frames
    assert seen["headers"]["api-subscription-key"] == "sk-secret-key"
    assert seen["body"] == {
        "text": "Namaste dost", "target_language_code": "hi-IN", "speaker": "spk", "model": "bulbul-test",
        "speech_sample_rate": RATE, "pace": 1.0,
    }
    await p.aclose()


@pytest.mark.asyncio
async def test_http_error_becomes_tts_error_without_leaking_the_key():
    p = provider(lambda req: httpx.Response(429, json={"error": "rate limited", "echo": "sk-secret-key"}))
    with pytest.raises(TTSError) as exc:
        [f async for f in p.synthesize("hi").frames()]
    assert "429" in str(exc.value) and "sk-secret-key" not in str(exc.value)


@pytest.mark.asyncio
async def test_bad_audio_payloads_are_tts_errors():
    for body in ({"audios": []}, {"nope": 1}, {"audios": [base64.b64encode(b"not a wav").decode()]}):
        p = provider(lambda req, b=body: httpx.Response(200, json=b))
        with pytest.raises(TTSError):
            [f async for f in p.synthesize("hi").frames()]


@pytest.mark.asyncio
async def test_wrong_sample_rate_is_rejected_not_played_at_the_wrong_speed():
    p = provider(lambda req: httpx.Response(200, json={"audios": [wav_b64(0.1, rate=16000)]}))
    with pytest.raises(TTSError):
        [f async for f in p.synthesize("hi").frames()]


@pytest.mark.asyncio
async def test_cancel_stops_output():
    p = provider(lambda req: httpx.Response(200, json={"audios": [wav_b64(1.0)]}))
    handle = p.synthesize("hi")
    out = []
    async for f in handle.frames():
        out.append(f)
        if len(out) == 2:
            await handle.cancel()
    assert len(out) == 2 and handle.cancelled


def test_config_validation_needs_real_values_and_never_invents_defaults():
    with pytest.raises(ValueError):
        SarvamTTSProvider(api_key="", speaker="s", model="m")
    with pytest.raises(ValueError):
        SarvamTTSProvider(api_key="k", speaker="", model="m")
    with pytest.raises(ValueError):
        SarvamTTSProvider(api_key="k", speaker="s", model="eleven_turbo_v2_5")  # the ElevenLabs default is not a Sarvam model
    assert normalize_language("hi") == "hi-IN" and normalize_language("en-IN") == "en-IN"


def test_factory_selects_sarvam_only_when_fully_configured(tmp_path):
    base = dict(livekit_url="wss://x", livekit_api_key="k", livekit_api_secret="s" * 32, tts_provider="sarvam")
    ok = Settings(**base, tts_api_key="k", tts_model="bulbul-test", dost_voice_id="a", sathi_voice_id="b")
    assert isinstance(build_tts_provider(BotId.DOST, ok), SarvamTTSProvider)
    assert (ok.tts_configured and not ok.missing_required()) or "OPENROUTER_API_KEY" in ok.missing_required()
    incomplete = Settings(**base, tts_api_key="k", dost_voice_id="a", sathi_voice_id="b")  # model left at the ElevenLabs default
    assert not incomplete.tts_configured
    assert any("TTS_MODEL" in m for m in incomplete.missing_required())
    assert isinstance(build_tts_provider(BotId.DOST, incomplete), SilentTTSProvider)  # degrades to text, never crashes
