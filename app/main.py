"""FastAPI app: upload, review and confirm, the reminder stream and dose responses."""

import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator
from temporalio.client import Client
from temporalio.service import RPCError

from app import db
from app.config import ROOT, get_settings
from app.extraction import TimeOfDay, WithFood, call_gemma, extract_medicines, prepare_image
from app.schedule import DEFAULT_SLOT_TIMES, build_slots, dose_times, iso, parse_slot_times
from app.shared import DEMO_TIME_SCALE, LANGUAGES, TASK_QUEUE, CourseInput
from app.workflows import PrescriptionCourseWorkflow

STATIC_DIR = ROOT / "app" / "static"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# Demo mode keeps the response window long enough for a person to tap a button.
DEMO_FOLLOWUP_SECONDS = 10
DEMO_GRACE_SECONDS = 60
DEMO_TEXT_WAIT_SECONDS = 20

_AUDIO_HASH = re.compile(r"^[0-9a-f]{64}$")

settings = get_settings()
_temporal: Optional[Client] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Prescription Reader", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


async def temporal_client() -> Client:
    """Connect on first use, so the app starts even when Temporal is not up yet."""
    global _temporal
    if _temporal is None:
        try:
            _temporal = await asyncio.wait_for(Client.connect(settings.temporal_address), timeout=5)
        except Exception as error:
            raise HTTPException(
                503, f"Cannot reach Temporal at {settings.temporal_address}. Is the dev server running?"
            ) from error
    return _temporal


def get_prescription(conn, prescription_id: str):
    row = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (prescription_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Prescription not found")
    return row


# ----- pages --------------------------------------------------------------


@app.get("/", include_in_schema=False)
def page_home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/review", include_in_schema=False)
def page_review():
    return FileResponse(STATIC_DIR / "review.html")


@app.get("/summary", include_in_schema=False)
def page_summary():
    return FileResponse(STATIC_DIR / "summary.html")


@app.get("/remind", include_in_schema=False)
def page_remind():
    return FileResponse(STATIC_DIR / "remind.html")


# ----- caregiver API ------------------------------------------------------


@app.get("/api/config")
def api_config():
    return {
        "languages": [{"code": code, "name": name} for code, (name, _tag) in LANGUAGES.items()],
        "default_slot_times": DEFAULT_SLOT_TIMES,
        "demo_mode": settings.demo_mode,
        "voice": "elevenlabs" if settings.voice_enabled else "browser",
        "model": settings.gemma_model,
    }


@app.get("/api/prescriptions")
def list_prescriptions():
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT id, patient_first_name, language, timezone, status, start_date, created_at, demo_mode
               FROM prescriptions ORDER BY created_at DESC"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.post("/api/prescriptions")
async def create_prescription(
    photo: UploadFile = File(...),
    patient_first_name: str = Form(...),
    language: str = Form(...),
    timezone_name: str = Form(..., alias="timezone"),
):
    """Save the photo as a draft and ask Gemma to read it. Nothing is scheduled here."""
    first_name = patient_first_name.strip()
    if not 1 <= len(first_name) <= 40:
        raise HTTPException(400, "Enter the patient's first name")
    if language not in LANGUAGES:
        raise HTTPException(400, "Unknown language")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise HTTPException(400, "Unknown time zone") from error

    data = await photo.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The photo is larger than 15 MB")
    try:
        image = await asyncio.to_thread(prepare_image, data)
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(400, "That file is not a photo this app can open") from error

    prescription_id = uuid.uuid4().hex
    image_name = f"{prescription_id}.jpg"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    (settings.upload_dir / image_name).write_bytes(image)

    outcome = await extract_medicines(image, lambda jpeg: call_gemma(settings, jpeg))

    conn = db.connect()
    try:
        conn.execute(
            """INSERT INTO prescriptions
               (id, patient_first_name, language, timezone, image_path, status, created_at, extraction)
               VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)""",
            (
                prescription_id,
                first_name,
                language,
                timezone_name,
                image_name,
                db.utc_now(),
                json.dumps(outcome.to_dict()),
            ),
        )
    finally:
        conn.close()
    return {"id": prescription_id, "extraction_status": outcome.status}


@app.get("/api/prescriptions/{prescription_id}")
async def read_prescription(prescription_id: str):
    """Everything the review and summary screens need."""
    conn = db.connect()
    try:
        prescription = dict(get_prescription(conn, prescription_id))
        extraction = json.loads(prescription.pop("extraction") or "{}")
        prescription["slot_times"] = json.loads(prescription["slot_times"] or "null")
        prescription.pop("image_path")
        result = {"prescription": prescription}

        if prescription["status"] == "draft":
            result["extraction"] = extraction
            return result

        medicines = [
            {**dict(row), "times_of_day": json.loads(row["times_of_day"])}
            for row in conn.execute(
                "SELECT * FROM medicines WHERE prescription_id = ? ORDER BY id", (prescription_id,)
            )
        ]
        doses = [
            dict(row)
            for row in conn.execute(
                """SELECT d.id, d.due_at, d.status, d.responded_at, d.reminders_sent,
                          m.name, m.strength, m.dose_amount
                   FROM doses d JOIN medicines m ON m.id = d.medicine_id
                   WHERE m.prescription_id = ? ORDER BY d.due_at, m.id""",
                (prescription_id,),
            )
        ]
        events = [
            dict(row)
            for row in conn.execute(
                """SELECT due_at, kind, attempt, outcome, audio_hash IS NOT NULL AS has_audio,
                          scheduled_at, created_at
                   FROM reminder_events WHERE prescription_id = ? ORDER BY id""",
                (prescription_id,),
            )
        ]
    finally:
        conn.close()

    counts = {status: 0 for status in ("pending", "taken", "skipped", "missed")}
    for dose in doses:
        counts[dose["status"]] += 1
    result.update(medicines=medicines, doses=doses, events=events, counts=counts)
    result["workflow"] = await _workflow_status(prescription_id) if prescription["status"] == "confirmed" else None
    return result


async def _workflow_status(prescription_id: str) -> Optional[dict]:
    """Ask the workflow for its status. None if Temporal or the worker is not answering."""
    try:
        client = await temporal_client()
        handle = client.get_workflow_handle(prescription_id)
        return await handle.query(PrescriptionCourseWorkflow.status, rpc_timeout=timedelta(seconds=2))
    except (HTTPException, RPCError):
        return None


@app.get("/api/prescriptions/{prescription_id}/image", include_in_schema=False)
def read_image(prescription_id: str):
    conn = db.connect()
    try:
        image_name = get_prescription(conn, prescription_id)["image_path"]
    finally:
        conn.close()
    return FileResponse(settings.upload_dir / image_name, media_type="image/jpeg")


class ConfirmedMedicine(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    strength: str = Field(default="", max_length=40)
    dose_amount: str = Field(min_length=1, max_length=40)
    times_of_day: List[TimeOfDay] = Field(min_length=1)
    with_food: WithFood = "unknown"
    duration_days: int = Field(ge=1, le=365)
    notes: str = Field(default="", max_length=300)

    @field_validator("name", "strength", "dose_amount", "notes", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


class ConfirmRequest(BaseModel):
    medicines: List[ConfirmedMedicine] = Field(min_length=1, max_length=20)
    slot_times: Dict[TimeOfDay, str]
    start_date: date


@app.post("/api/prescriptions/{prescription_id}/confirm")
async def confirm_prescription(prescription_id: str, body: ConfirmRequest):
    """The caregiver has checked the schedule. Save it and start the workflow."""
    try:
        parse_slot_times(body.slot_times)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    conn = db.connect()
    try:
        prescription = get_prescription(conn, prescription_id)
        if prescription["status"] != "draft":
            raise HTTPException(409, "This prescription has already been confirmed")

        medicines = [medicine.model_dump() for medicine in body.medicines]
        tz_name = prescription["timezone"]
        now = datetime.now(timezone.utc)
        slots = build_slots(medicines, body.slot_times, body.start_date, tz_name)
        already_passed = 0
        if not settings.demo_mode:
            # Never remind for a time that has already gone by.
            already_passed = sum(1 for slot in slots if datetime.fromisoformat(slot.due_at) <= now)
            slots = slots[already_passed:]
        if not slots:
            raise HTTPException(400, "Every dose time in this schedule has already passed. Check the start date.")
        first_due = datetime.fromisoformat(slots[0].due_at)

        course = CourseInput(
            prescription_id=prescription_id,
            patient_first_name=prescription["patient_first_name"],
            language=prescription["language"],
            slots=slots,
        )
        if settings.demo_mode:
            course.time_scale = DEMO_TIME_SCALE
            course.anchor_real = iso(now)
            course.anchor_virtual = iso(first_due - timedelta(hours=1))
            course.followup_seconds = DEMO_FOLLOWUP_SECONDS
            course.grace_seconds = DEMO_GRACE_SECONDS
            course.text_wait_seconds = DEMO_TEXT_WAIT_SECONDS

        client = await temporal_client()

        conn.execute("BEGIN IMMEDIATE")
        try:
            for medicine in medicines:
                cursor = conn.execute(
                    """INSERT INTO medicines
                       (prescription_id, name, strength, dose_amount, times_of_day, with_food, duration_days, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        prescription_id,
                        medicine["name"],
                        medicine["strength"],
                        medicine["dose_amount"],
                        json.dumps(medicine["times_of_day"]),
                        medicine["with_food"],
                        medicine["duration_days"],
                        medicine["notes"],
                    ),
                )
                for _day, _slot, due in dose_times(
                    medicine["times_of_day"], medicine["duration_days"], body.slot_times, body.start_date, tz_name
                ):
                    if due >= first_due:
                        conn.execute(
                            "INSERT INTO doses (medicine_id, due_at) VALUES (?, ?)", (cursor.lastrowid, iso(due))
                        )
            conn.execute(
                """UPDATE prescriptions
                   SET status = 'confirmed', start_date = ?, slot_times = ?, demo_mode = ? WHERE id = ?""",
                (body.start_date.isoformat(), json.dumps(body.slot_times), int(settings.demo_mode), prescription_id),
            )
            # The schedule only counts as confirmed if the workflow really started.
            await client.start_workflow(
                PrescriptionCourseWorkflow.run, course, id=prescription_id, task_queue=TASK_QUEUE
            )
            conn.execute("COMMIT")
        except Exception as error:
            conn.execute("ROLLBACK")
            if isinstance(error, RPCError):
                raise HTTPException(503, f"Could not start the reminder schedule: {error}") from error
            raise
    finally:
        conn.close()
    return {"id": prescription_id, "reminders": len(slots), "already_passed": already_passed}


@app.post("/api/prescriptions/{prescription_id}/cancel")
async def cancel_prescription(prescription_id: str):
    conn = db.connect()
    try:
        if get_prescription(conn, prescription_id)["status"] != "confirmed":
            raise HTTPException(409, "This prescription is not running")
    finally:
        conn.close()
    client = await temporal_client()
    try:
        await client.get_workflow_handle(prescription_id).signal(PrescriptionCourseWorkflow.cancel)
    except RPCError as error:
        raise HTTPException(409, f"Could not cancel: {error}") from error
    return {"ok": True}


# ----- patient API --------------------------------------------------------


class DoseResponse(BaseModel):
    prescription_id: str
    due_at: str
    action: Literal["taken", "skipped", "snooze"]
    minutes: int = Field(default=10, ge=1, le=120)


@app.post("/api/doses")
async def respond_to_dose(body: DoseResponse):
    """Pass the patient's answer to the workflow as a signal."""
    client = await temporal_client()
    handle = client.get_workflow_handle(body.prescription_id)
    try:
        if body.action == "taken":
            await handle.signal(PrescriptionCourseWorkflow.dose_taken, body.due_at)
        elif body.action == "skipped":
            await handle.signal(PrescriptionCourseWorkflow.dose_skipped, body.due_at)
        else:
            await handle.signal(PrescriptionCourseWorkflow.snooze, body.minutes)
    except RPCError as error:
        raise HTTPException(409, "This schedule is no longer running") from error
    return {"ok": True}


_EVENT_COLUMNS = """
    e.id, e.prescription_id, e.due_at, e.kind, e.attempt, e.text, e.audio_hash, e.outcome,
    p.patient_first_name, p.language
    FROM reminder_events e JOIN prescriptions p ON p.id = e.prescription_id
"""


def _sse(row) -> str:
    data = {
        "prescription_id": row["prescription_id"],
        "due_at": row["due_at"],
        "patient_first_name": row["patient_first_name"],
    }
    if row["kind"] == "reminder":
        data.update(
            attempt=row["attempt"],
            text=row["text"],
            audio_url=f"/api/audio/{row['audio_hash']}" if row["audio_hash"] else None,
            lang=LANGUAGES.get(row["language"], LANGUAGES["en"])[1],
        )
    else:
        data["outcome"] = row["outcome"]
    return f"id: {row['id']}\nevent: {row['kind']}\ndata: {json.dumps(data)}\n\n"


def _open_reminders(conn):
    """The latest reminder of every dose that is still waiting for an answer."""
    return conn.execute(
        f"""SELECT {_EVENT_COLUMNS}
            WHERE e.kind = 'reminder' AND p.status = 'confirmed'
              AND e.id = (SELECT MAX(id) FROM reminder_events
                          WHERE prescription_id = e.prescription_id AND due_at = e.due_at AND kind = 'reminder')
              AND NOT EXISTS (SELECT 1 FROM reminder_events r
                              WHERE r.prescription_id = e.prescription_id AND r.due_at = e.due_at
                                AND r.kind = 'resolved')
            ORDER BY e.id"""
    ).fetchall()


@app.get("/api/reminders")
async def reminder_stream(request: Request):
    """Server-sent events for the reminder screen."""

    async def stream():
        conn = db.connect()
        try:
            # Every connection (including a reconnect) starts with what is still waiting
            # for an answer, then follows new events. The screen ignores ids it has shown.
            cursor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM reminder_events").fetchone()[0]
            waiting = _open_reminders(conn)
            keys = [[row["prescription_id"], row["due_at"]] for row in waiting]
            yield f"event: sync\ndata: {json.dumps({'open': keys})}\n\n"
            for row in waiting:
                yield _sse(row)
            idle = 0
            while not await request.is_disconnected():
                rows = conn.execute(
                    f"SELECT {_EVENT_COLUMNS} WHERE e.id > ? ORDER BY e.id", (cursor,)
                ).fetchall()
                for row in rows:
                    cursor = row["id"]
                    yield _sse(row)
                idle = 0 if rows else idle + 1
                if idle >= 15:
                    idle = 0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)
        finally:
            conn.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/audio/{audio_hash}")
def read_audio(audio_hash: str):
    path = settings.audio_dir / f"{audio_hash}.mp3"
    if not _AUDIO_HASH.match(audio_hash) or not path.exists():
        raise HTTPException(404, "Audio not found")
    return FileResponse(path, media_type="audio/mpeg")
