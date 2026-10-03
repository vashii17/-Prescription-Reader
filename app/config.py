"""Settings read from the environment (and a .env file in the project root)."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    gemma_model: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_model: str
    temporal_address: str
    demo_mode: bool
    caregiver_notify_url: str
    data_dir: Path
    app_password: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def voice_enabled(self) -> bool:
        return bool(self.elevenlabs_api_key and self.elevenlabs_voice_id)


@lru_cache
def get_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    env = os.environ.get
    return Settings(
        ollama_base_url=env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        gemma_model=env("GEMMA_MODEL", "gemma4:e2b"),
        elevenlabs_api_key=env("ELEVENLABS_API_KEY", "").strip(),
        elevenlabs_voice_id=env("ELEVENLABS_VOICE_ID", "").strip(),
        elevenlabs_model=env("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip(),
        temporal_address=env("TEMPORAL_ADDRESS", "localhost:7233"),
        demo_mode=_flag(env("DEMO_MODE", "0")),
        caregiver_notify_url=env("CAREGIVER_NOTIFY_URL", "").strip(),
        data_dir=Path(env("DATA_DIR", str(ROOT / "data"))),
        app_password=env("APP_PASSWORD", ""),
    )
