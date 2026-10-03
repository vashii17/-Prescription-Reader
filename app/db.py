"""SQLite storage. Both the web app and the Temporal worker open this file."""

import sqlite3
from datetime import datetime, timezone

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS prescriptions (
    id                 TEXT PRIMARY KEY,
    patient_first_name TEXT NOT NULL,
    language           TEXT NOT NULL,
    timezone           TEXT NOT NULL,
    image_path         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'confirmed', 'finished', 'cancelled')),
    start_date         TEXT,
    created_at         TEXT NOT NULL,
    slot_times         TEXT,              -- JSON, set on confirm
    extraction         TEXT,              -- JSON, the model's unconfirmed reading
    demo_mode          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS medicines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id),
    name            TEXT NOT NULL,
    strength        TEXT NOT NULL DEFAULT '',
    dose_amount     TEXT NOT NULL,
    times_of_day    TEXT NOT NULL,        -- JSON list
    with_food       TEXT NOT NULL,
    duration_days   INTEGER NOT NULL,
    notes           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS doses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id    INTEGER NOT NULL REFERENCES medicines(id),
    due_at         TEXT NOT NULL,         -- UTC, ISO 8601
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'taken', 'skipped', 'missed')),
    responded_at   TEXT,
    reminders_sent INTEGER NOT NULL DEFAULT 0,
    UNIQUE (medicine_id, due_at)
);

CREATE TABLE IF NOT EXISTS audio_cache (
    hash       TEXT PRIMARY KEY,
    file_path  TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminder_text_cache (
    hash       TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Outbox read by the server-sent events stream. The unique key makes a
-- retried publish activity a no-op, so a reminder is never delivered twice.
CREATE TABLE IF NOT EXISTS reminder_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id TEXT NOT NULL REFERENCES prescriptions(id),
    due_at          TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('reminder', 'resolved')),
    seq             INTEGER NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 0,
    text            TEXT,
    audio_hash      TEXT,
    outcome         TEXT,
    scheduled_at    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (prescription_id, due_at, kind, seq)
);

CREATE INDEX IF NOT EXISTS idx_doses_due ON doses(due_at);
CREATE INDEX IF NOT EXISTS idx_medicines_prescription ON medicines(prescription_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
