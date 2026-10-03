"""Temporal activities: everything the workflow does that touches the outside world."""

from typing import Optional

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app import db
from app.config import get_settings
from app.reminder_text import write_sentence
from app.shared import FinishInput, LogDoseInput, NotifyInput, PublishInput, TextInput
from app.tts import TTSPermanentError, synthesize

_DOSES_OF_SLOT = """
    SELECT d.id FROM doses d JOIN medicines m ON m.id = d.medicine_id
    WHERE m.prescription_id = ? AND d.due_at = ?
"""


@activity.defn
async def write_reminder_text(inp: TextInput) -> str:
    """Gemma phrases the reminder sentence in the patient's language."""
    return await write_sentence(get_settings(), inp)


@activity.defn
async def make_speech_audio(text: str) -> Optional[str]:
    """ElevenLabs turns the sentence into speech. Returns the audio cache hash."""
    try:
        return await synthesize(get_settings(), text)
    except TTSPermanentError as error:
        raise ApplicationError(str(error), type="TTSPermanentError", non_retryable=True) from error


@activity.defn
async def publish_reminder(inp: PublishInput) -> None:
    """Put the reminder in the outbox that the reminder screen streams from."""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT OR IGNORE INTO reminder_events
               (prescription_id, due_at, kind, seq, attempt, text, audio_hash, scheduled_at, created_at)
               VALUES (?, ?, 'reminder', ?, ?, ?, ?, ?, ?)""",
            (
                inp.prescription_id,
                inp.due_at,
                inp.seq,
                inp.attempt,
                inp.text,
                inp.audio_hash,
                inp.scheduled_at,
                db.utc_now(),
            ),
        )
        conn.execute(
            f"UPDATE doses SET reminders_sent = ? WHERE id IN ({_DOSES_OF_SLOT})",
            (inp.attempt, inp.prescription_id, inp.due_at),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


@activity.defn
async def log_dose(inp: LogDoseInput) -> None:
    """Record the outcome of a dose and tell the reminder screen to clear it."""
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"""UPDATE doses SET status = ?, responded_at = ?, reminders_sent = ?
                WHERE status = 'pending' AND id IN ({_DOSES_OF_SLOT})""",
            (inp.outcome, inp.responded_at, inp.reminders_sent, inp.prescription_id, inp.due_at),
        )
        conn.execute(
            """INSERT OR IGNORE INTO reminder_events
               (prescription_id, due_at, kind, seq, outcome, created_at)
               VALUES (?, ?, 'resolved', 0, ?, ?)""",
            (inp.prescription_id, inp.due_at, inp.outcome, db.utc_now()),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


@activity.defn
async def notify_caregiver(inp: NotifyInput) -> None:
    """Call the optional webhook. The missed dose is already in the caregiver view."""
    url = get_settings().caregiver_notify_url
    if not url:
        return
    payload = {
        "event": "dose_missed",
        "prescription_id": inp.prescription_id,
        "patient_first_name": inp.patient_first_name,
        "due_at": inp.due_at,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


@activity.defn
async def finish_prescription(inp: FinishInput) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE prescriptions SET status = ? WHERE id = ? AND status = 'confirmed'",
            (inp.status, inp.prescription_id),
        )
    finally:
        conn.close()


ALL_ACTIVITIES = [
    write_reminder_text,
    make_speech_audio,
    publish_reminder,
    log_dose,
    notify_caregiver,
    finish_prescription,
]
