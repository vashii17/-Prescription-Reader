"""Types and pure helpers shared by the web app, the workflow and the activities.

This module is imported inside the Temporal workflow sandbox, so it must stay
deterministic: standard library only, no clock, no I/O.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

TASK_QUEUE = "prescription-reminders"

SLOT_ORDER = ("morning", "afternoon", "evening", "night")

# code -> (name given to Gemma, BCP 47 tag for the browser speech fallback)
LANGUAGES = {
    "en": ("English", "en-US"),
    "hi": ("Hindi", "hi-IN"),
    "ta": ("Tamil", "ta-IN"),
    "es": ("Spanish", "es-ES"),
    "fr": ("French", "fr-FR"),
    "de": ("German", "de-DE"),
    "pt": ("Portuguese", "pt-BR"),
    "it": ("Italian", "it-IT"),
    "ar": ("Arabic", "ar-SA"),
    "zh": ("Chinese", "zh-CN"),
}

FOOD_PHRASES = {"before": "before food", "after": "after food"}

# Real-time settings. Demo mode replaces them when the workflow is started.
FOLLOWUP_SECONDS = 20 * 60
MAX_REMINDERS = 3  # the first reminder plus at most two repeats
GRACE_SECONDS = 2 * 60 * 60
TEXT_WAIT_SECONDS = 90
DEMO_TIME_SCALE = 24 * 60  # one day passes in one minute


@dataclass
class ReminderItem:
    name: str
    strength: str
    dose_amount: str
    with_food: str


@dataclass
class Slot:
    """All medicines due at one moment. They share one reminder."""

    due_at: str  # UTC, ISO 8601
    label: str  # morning, afternoon, evening or night
    day_index: int
    items: List[ReminderItem] = field(default_factory=list)


@dataclass
class CourseInput:
    prescription_id: str
    patient_first_name: str
    language: str
    slots: List[Slot]
    # Demo mode: real time = anchor_real + (due - anchor_virtual) / time_scale.
    time_scale: float = 1.0
    anchor_real: str = ""
    anchor_virtual: str = ""
    followup_seconds: float = FOLLOWUP_SECONDS
    max_reminders: int = MAX_REMINDERS
    # A dose this late when the workflow reaches it is recorded as missed
    # instead of being spoken at the wrong time of day.
    grace_seconds: float = GRACE_SECONDS
    text_wait_seconds: float = TEXT_WAIT_SECONDS
    # Carried across continue-as-new.
    next_index: int = 0
    taken: int = 0
    skipped: int = 0
    missed: int = 0


@dataclass
class CourseResult:
    status: str
    taken: int
    skipped: int
    missed: int


@dataclass
class TextInput:
    patient_first_name: str
    language: str
    label: str
    items: List[ReminderItem]


@dataclass
class PublishInput:
    prescription_id: str
    due_at: str
    seq: int
    attempt: int
    text: str
    audio_hash: Optional[str]
    scheduled_at: str


@dataclass
class LogDoseInput:
    prescription_id: str
    due_at: str
    outcome: str  # taken, skipped or missed
    reminders_sent: int
    responded_at: Optional[str]


@dataclass
class NotifyInput:
    prescription_id: str
    patient_first_name: str
    due_at: str


@dataclass
class FinishInput:
    prescription_id: str
    status: str  # finished or cancelled


def real_due_at(due_at: str, time_scale: float, anchor_real: str, anchor_virtual: str) -> datetime:
    """When a scheduled dose fires on the real clock."""
    due = datetime.fromisoformat(due_at)
    if time_scale == 1 or not anchor_real:
        return due
    offset = due - datetime.fromisoformat(anchor_virtual)
    return datetime.fromisoformat(anchor_real) + offset / time_scale


def describe_item(item: ReminderItem) -> str:
    text = " ".join(part for part in (item.dose_amount, item.name, item.strength) if part)
    food = FOOD_PHRASES.get(item.with_food)
    return f"{text}, {food}" if food else text


def fallback_sentence(patient_first_name: str, label: str, items: List[ReminderItem]) -> str:
    """Plain reminder built only from confirmed fields, used when Gemma is unavailable."""
    listing = "; ".join(describe_item(item) for item in items)
    return f"{patient_first_name}, it is time for your {label} medicine: {listing}."
