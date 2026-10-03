"""Reads a prescription photo with Gemma (through Ollama) and validates the result.

The output of this module is only ever a draft for the caregiver to review.
"""

import base64
import io
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Literal, Optional

import httpx
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.config import Settings
from app.shared import SLOT_ORDER

Confidence = Literal["high", "medium", "low"]
TimeOfDay = Literal["morning", "afternoon", "evening", "night"]
WithFood = Literal["before", "after", "any", "unknown"]

FIELD_NAMES = (
    "name",
    "strength",
    "dose_amount",
    "frequency_per_day",
    "times_of_day",
    "with_food",
    "duration_days",
    "notes",
)

# Words a model writes when it has nothing to report. They become empty fields.
_PLACEHOLDERS = {"", "unknown", "n/a", "na", "none", "null", "unreadable", "illegible", "not specified", "?", "-"}

MAX_IMAGE_SIDE = 1600


class ExtractionError(ValueError):
    """The model's reply was not valid JSON of the expected shape."""


class FieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Confidence
    strength: Confidence
    dose_amount: Confidence
    frequency_per_day: Confidence
    times_of_day: Confidence
    with_food: Confidence
    duration_days: Confidence
    notes: Confidence


class ExtractedMedicine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str]
    strength: Optional[str]
    dose_amount: Optional[str]
    frequency_per_day: Optional[int] = Field(ge=1, le=12)
    times_of_day: List[TimeOfDay]
    with_food: WithFood
    duration_days: Optional[int] = Field(ge=1, le=365)
    notes: Optional[str]
    confidence: FieldConfidence

    @field_validator("name", "strength", "dose_amount", "notes", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.lower() in _PLACEHOLDERS:
                return None
        return value

    @field_validator("frequency_per_day", "duration_days", mode="before")
    @classmethod
    def _no_booleans(cls, value):
        if isinstance(value, bool):
            raise ValueError("must be a whole number")
        return value

    @field_validator("times_of_day")
    @classmethod
    def _ordered_unique(cls, value):
        return [slot for slot in SLOT_ORDER if slot in value]

    @model_validator(mode="after")
    def _empty_fields_are_low_confidence(self):
        for field_name in FIELD_NAMES:
            value = getattr(self, field_name)
            if value is None or value == [] or (field_name == "with_food" and value == "unknown"):
                setattr(self.confidence, field_name, "low")
        return self


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    medicines: List[ExtractedMedicine] = Field(max_length=20)


@dataclass
class ExtractionOutcome:
    status: str  # ok, invalid (model output rejected twice) or unavailable (Ollama not reachable)
    medicines: List[dict]
    detail: str = ""

    def to_dict(self) -> dict:
        return {"status": self.status, "medicines": self.medicines, "detail": self.detail}


def parse_extraction(raw: str) -> Extraction:
    """Validate the model's reply. Raises ExtractionError on anything unexpected."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ExtractionError(f"not JSON: {error}") from error
    try:
        return Extraction.model_validate(data)
    except ValidationError as error:
        raise ExtractionError(str(error)) from error


def prepare_image(data: bytes) -> bytes:
    """Fix rotation, drop metadata and shrink the photo. Returns JPEG bytes."""
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


def build_prompt() -> str:
    schema = json.dumps(Extraction.model_json_schema())
    return (
        "You are reading a photo of a medical prescription. List every medicine written on it.\n"
        "Rules:\n"
        "- Copy only what is written on the paper. Never guess and never fill in a typical value.\n"
        "- If a value is missing or you cannot read it, use null (or [] for times_of_day, "
        '"unknown" for with_food) and set that field\'s confidence to "low".\n'
        "- name: the medicine name as written, without the strength.\n"
        '- strength: for example "500 mg".\n'
        '- dose_amount: how much is taken each time, for example "1 tablet".\n'
        "- frequency_per_day: how many times a day, as a whole number.\n"
        "- times_of_day: only morning, afternoon, evening, night, and only when the prescription "
        "states them in words or as a morning-afternoon-night pattern such as 1-0-1. If only a "
        "frequency such as 'twice daily' is written, leave times_of_day empty.\n"
        "- with_food: before, after, any, or unknown if not written.\n"
        "- duration_days: number of days as a whole number.\n"
        "- notes: any other instruction written for that medicine, kept short. "
        "Do not include the patient's name, address or diagnosis.\n"
        '- confidence: for each field, "high", "medium" or "low" according to how clearly it is written.\n'
        "Reply with JSON only, matching this schema:\n" + schema
    )


async def call_gemma(settings: Settings, image_jpeg: bytes) -> str:
    """Send the photo to the local Ollama server and return the raw reply text."""
    body = {
        "model": settings.gemma_model,
        "messages": [
            {
                "role": "user",
                "content": build_prompt(),
                "images": [base64.b64encode(image_jpeg).decode("ascii")],
            }
        ],
        "stream": False,
        "format": Extraction.model_json_schema(),
        "options": {"temperature": 0},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as client:
        response = await client.post(f"{settings.ollama_base_url}/api/chat", json=body)
        response.raise_for_status()
        return response.json()["message"]["content"]


async def extract_medicines(
    image_jpeg: bytes,
    call_model: Callable[[bytes], Awaitable[str]],
) -> ExtractionOutcome:
    """Ask the model, retry once on invalid output, then give up with an empty form."""
    detail = ""
    for _attempt in range(2):
        try:
            raw = await call_model(image_jpeg)
        except (httpx.HTTPError, KeyError, ValueError) as error:
            return ExtractionOutcome("unavailable", [], f"{type(error).__name__}: {error}")
        try:
            extraction = parse_extraction(raw)
        except ExtractionError as error:
            detail = str(error)
            continue
        return ExtractionOutcome("ok", [m.model_dump() for m in extraction.medicines])
    return ExtractionOutcome("invalid", [], detail)
