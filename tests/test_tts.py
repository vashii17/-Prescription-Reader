import asyncio
from dataclasses import replace

import httpx
import pytest

from app import db, tts
from app.config import get_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    test_settings = replace(
        get_settings(),
        data_dir=tmp_path,
        elevenlabs_api_key="test-key",
        elevenlabs_voice_id="voice-1",
        elevenlabs_model="eleven_multilingual_v2",
    )
    monkeypatch.setattr(db, "get_settings", lambda: test_settings)
    db.init_db()
    return test_settings


def fake_elevenlabs(monkeypatch, handler):
    """Route the module's HTTP calls to a local handler and record the requests."""
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        tts.httpx, "AsyncClient", lambda **_kwargs: real_client(transport=httpx.MockTransport(record))
    )
    return requests


def test_audio_is_fetched_once_and_then_served_from_the_cache(settings, monkeypatch):
    requests = fake_elevenlabs(monkeypatch, lambda _request: httpx.Response(200, content=b"mp3-bytes"))

    first = asyncio.run(tts.synthesize(settings, "Asha, it is time."))
    second = asyncio.run(tts.synthesize(settings, "Asha, it is time."))

    assert first == second == tts.audio_hash("Asha, it is time.", "voice-1", "eleven_multilingual_v2")
    assert len(requests) == 1
    assert (settings.audio_dir / f"{first}.mp3").read_bytes() == b"mp3-bytes"
    request = requests[0]
    assert request.url.path == "/v1/text-to-speech/voice-1"
    assert request.headers["xi-api-key"] == "test-key"
    assert b'"model_id":"eleven_multilingual_v2"' in request.content.replace(b" ", b"")


def test_hash_changes_with_sentence_voice_and_model():
    base = tts.audio_hash("a", "v", "m")
    assert len({base, tts.audio_hash("b", "v", "m"), tts.audio_hash("a", "w", "m"), tts.audio_hash("a", "v", "n")}) == 4


def test_no_key_means_no_request(settings, monkeypatch):
    requests = fake_elevenlabs(monkeypatch, lambda _request: httpx.Response(200, content=b"x"))
    assert asyncio.run(tts.synthesize(replace(settings, elevenlabs_api_key=""), "hello")) is None
    assert requests == []


def test_bad_key_is_a_permanent_error(settings, monkeypatch):
    fake_elevenlabs(monkeypatch, lambda _request: httpx.Response(401, json={"detail": "invalid_api_key"}))
    with pytest.raises(tts.TTSPermanentError):
        asyncio.run(tts.synthesize(settings, "hello"))


def test_server_error_is_temporary_and_nothing_is_cached(settings, monkeypatch):
    fake_elevenlabs(monkeypatch, lambda _request: httpx.Response(503))
    with pytest.raises(tts.TTSTemporaryError):
        asyncio.run(tts.synthesize(settings, "hello"))
    assert list(settings.audio_dir.glob("*")) == []
