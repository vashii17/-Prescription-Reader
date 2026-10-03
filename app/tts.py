"""ElevenLabs text-to-speech with an on-disk cache.

Only the reminder sentence is sent. Audio is cached by a hash of the sentence,
the voice and the model, because the same sentences repeat every day.
"""

import hashlib
import os
from typing import Optional

import httpx

from app import db
from app.config import Settings

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
OUTPUT_FORMAT = "mp3_44100_128"

# Wrong key, exhausted quota, unknown voice or rejected text: retrying will not help.
_PERMANENT_STATUS = {400, 401, 402, 403, 404, 422}


class TTSPermanentError(Exception):
    pass


class TTSTemporaryError(Exception):
    pass


def audio_hash(text: str, voice_id: str, model: str) -> str:
    return hashlib.sha256(f"{text}\x00{voice_id}\x00{model}".encode("utf-8")).hexdigest()


async def synthesize(settings: Settings, text: str) -> Optional[str]:
    """Return the cache hash of the audio for this sentence, or None if voice is not configured."""
    if not settings.voice_enabled:
        return None
    key = audio_hash(text, settings.elevenlabs_voice_id, settings.elevenlabs_model)
    path = settings.audio_dir / f"{key}.mp3"

    conn = db.connect()
    try:
        cached = conn.execute("SELECT 1 FROM audio_cache WHERE hash = ?", (key,)).fetchone()
        if cached and path.exists():
            return key

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5)) as client:
                response = await client.post(
                    API_URL.format(voice_id=settings.elevenlabs_voice_id),
                    params={"output_format": OUTPUT_FORMAT},
                    headers={"xi-api-key": settings.elevenlabs_api_key},
                    json={"text": text, "model_id": settings.elevenlabs_model},
                )
        except httpx.HTTPError as error:
            raise TTSTemporaryError(f"{type(error).__name__}: {error}") from error

        if response.status_code in _PERMANENT_STATUS:
            raise TTSPermanentError(f"ElevenLabs returned {response.status_code}: {response.text[:200]}")
        if response.status_code != 200 or not response.content:
            raise TTSTemporaryError(f"ElevenLabs returned {response.status_code}")

        settings.audio_dir.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        partial.write_bytes(response.content)
        os.replace(partial, path)
        conn.execute(
            "INSERT OR REPLACE INTO audio_cache (hash, file_path, created_at) VALUES (?, ?, ?)",
            (key, path.name, db.utc_now()),
        )
        return key
    finally:
        conn.close()
