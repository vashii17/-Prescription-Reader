# Prescription Reader

Reads a photo of a paper prescription, lets a caregiver check and confirm the schedule, then speaks a
reminder aloud in the patient's language at every dose time for the whole course.

> Check with your doctor or pharmacist. This tool only repeats the schedule you confirmed.

Built for the "Build for a Friend" hackathon. Three parts do the real work:

| Part | What it does here |
|---|---|
| **Gemma**, run locally by Ollama | Reads the photo into a draft schedule and phrases each reminder sentence. The photo and the medical details never leave this computer. |
| **Temporal** | Owns the schedule: one workflow per confirmed prescription, with durable timers, retries, repeat reminders and missed-dose escalation. Reminders survive crashes and restarts. |
| **ElevenLabs** | Turns each reminder sentence into speech. Only that one sentence is sent (first name, medicine, amount, food instruction). No surname, address, diagnosis or photo. |

## Safety rules the app enforces

- Nothing is scheduled from the model's reading alone. The workflow starts only when a person presses Confirm.
- The review screen shows the photo beside the extracted fields and highlights anything the model was unsure of or could not read.
- Unreadable fields are left empty for the caregiver. The model is told not to guess, and empty fields are always flagged.
- The app never recommends, changes or interprets a dose. The reminder sentence is checked before it is spoken: it must name every confirmed medicine and may not contain any number that is not in the confirmed schedule. If the check fails, a plain sentence built directly from the confirmed fields is used instead.
- A reminder that could not be delivered for more than two hours (for example the computer was off) is recorded as missed, not spoken late.

## Setup (once)

You need Python 3.11 or newer, [Ollama](https://ollama.com/download) and the
[Temporal CLI](https://docs.temporal.io/cli). On Windows:

```powershell
winget install Python.Python.3.12 Ollama.Ollama Temporal.TemporalCLI
```

Then, in this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
ollama pull gemma4:e2b
copy .env.example .env
```

`.env` works as copied. Add `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` for ElevenLabs voices; without
them the reminder screen uses the browser's built-in speech.

## Run: three commands, one terminal each

Ollama must be running (it starts with Windows after installation; otherwise run `ollama serve`).

```powershell
temporal server start-dev --db-filename data\temporal.db
```

```powershell
.\.venv\Scripts\python -m app.worker
```

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

- Caregiver: <http://localhost:8000>
- Reminder screen: <http://localhost:8000/remind>, press **Start reminders** once and leave it open.
- Temporal's own UI: <http://localhost:8233>

`--db-filename` keeps Temporal's state in a file, so schedules also survive a restart of the Temporal
server or the whole computer, as long as the three commands are started again.

To use the reminder screen on a phone or tablet on the same Wi-Fi, add `--host 0.0.0.0` to the last
command and open `http://<this computer's address>:8000/remind`. There are no accounts, so only do this
on a home network you trust.

## Demo: a three-day course in three minutes

1. Set `DEMO_MODE=1` in `.env` and start the three commands. One day now passes in one minute.
2. Open <http://localhost:8000/remind> in one window and press **Start reminders**.
3. Open <http://localhost:8000> in another. Upload `samples/sample_1_simple.png`, with first name `Asha`.
4. Check the table against the photo, then press **Confirm**. The first reminder is spoken about three seconds later.
5. Press **I took it** on the first reminder. Ignore a later one: it repeats twice, ten seconds apart, and is then marked missed.
6. When the last dose has passed, the summary page shows the taken, skipped and missed counts.

In demo mode the wait before a repeat reminder is 10 seconds (a scaled 20 minutes would be under one second,
too short to press a button). In real time it is 20 minutes.

Other samples: `sample_2_pattern.png` uses the 1-0-1 notation, and `sample_3_incomplete.png` has missing
durations and a missing strength, to show empty fields being flagged. All names on them are made up.
`python samples/make_samples.py` redraws them.

## Durability check (the evidence for Temporal)

This shows that the schedule lives in Temporal, not in the Python process.

1. Start a demo course as above (`DEMO_MODE=1`, sample 1) and answer the first reminder.
2. In the worker terminal press **Ctrl+C**. The worker is now gone. The summary page says the worker is not answering.
3. Wait ten seconds, then start it again: `.\.venv\Scripts\python -m app.worker`
4. The next reminder is spoken at its planned time.
5. Repeat, but this time leave the worker stopped for about 30 seconds, so that a dose time passes while it is down.
   When it starts again that reminder goes out once, straight away.
6. On the summary page, the **Reminder log** lists every reminder with the time it was planned for and the time
   it went out. Each dose has exactly one "Reminder 1": nothing was lost and nothing was sent twice.

You can also press **I took it** while the worker is stopped. Temporal keeps the signal and the dose is
recorded as taken as soon as the worker is back.

What makes this work:

- The workflow sleeps on Temporal timers, which are stored by the Temporal server. A restarted worker replays
  the workflow history and carries on from the same point.
- The workflow reads time only from `workflow.now()`.
- Publishing a reminder is idempotent: the outbox table has a unique key per dose and reminder number, so a
  retried activity cannot produce a second reminder.
- Each day ends with continue-as-new, so a 90-day course keeps a short history. In <http://localhost:8233>
  a three-day course shows as three runs of the same workflow id.

## How it works

```
Caregiver's browser  ->  FastAPI  ->  Ollama (Gemma): photo -> draft schedule
                            |
                            |  Confirm
                            v
                      Temporal workflow (id = prescription id)
                            |  for each dose time:
                            |    write_reminder_text  -> Ollama (Gemma)
                            |    make_speech_audio    -> ElevenLabs (cached by sentence hash)
                            |    durable timer until the dose time
                            |    publish_reminder     -> SQLite outbox -> server-sent events
                            |    wait for dose_taken / dose_skipped / snooze, or 20 minutes
                            |    repeat at most twice, then log_dose(missed) + notify_caregiver
                            v
Patient's reminder screen  <-  /api/reminders (SSE);  "I took it"  ->  /api/doses  ->  signal
```

- Medicines due at the same time share one reminder and one "I took it".
- The sentence and the audio are prepared before the dose time, so a slow model or a slow network does not
  delay the reminder. If ElevenLabs still fails after four attempts with backoff, the reminder goes out as
  text and the browser speaks it. A failed voice call cannot cause a missed reminder.
- Reminder sentences and audio are cached, because the same sentence repeats every day.
- Workflow signals: `dose_taken`, `dose_skipped`, `snooze(minutes)`, `cancel`. Query: `status`.
- Missed doses always show on the summary page. If `CAREGIVER_NOTIFY_URL` is set, it also receives a JSON
  POST: `{"event": "dose_missed", "prescription_id", "patient_first_name", "due_at"}`.

| File | Purpose |
|---|---|
| `app/main.py` | Web app and API |
| `app/extraction.py` | Photo to validated JSON, with one retry |
| `app/schedule.py` | Dose times across days and time zones |
| `app/workflows.py` | The Temporal workflow |
| `app/activities.py` | Activities: text, audio, publish, log, notify |
| `app/reminder_text.py`, `app/tts.py` | Reminder sentence and its check; ElevenLabs with cache |
| `app/db.py` | SQLite schema |
| `app/static/` | The four screens, plain HTML, CSS and JavaScript |

## Hosting a copy on Render

The app is designed to run at home, where the photo never leaves the computer. A hosted copy is useful
for a demo link, but the prescriptions then live on the hosting server, so use made-up data there.

`render.yaml` and `Dockerfile` run everything (Ollama with Gemma, Temporal, the worker and the web app)
in one service with a persistent disk.

1. Push this repository to GitHub.
2. In Render: **New > Blueprint**, pick the repository. Render reads `render.yaml`.
3. When asked, enter `APP_PASSWORD` (choose one), `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`.
4. Wait for the first deploy. The web app answers within a few minutes; the Gemma model (about 5 GB)
   then downloads to the disk in the background. Until the log says `Model gemma4:e2b is ready`,
   the review screen says the model could not be reached.
5. Open the service's address. The browser asks for a user name (anything) and the password.

The plan must have 8 GB of RAM. It is billed for as long as the service exists, so delete the service
(and its disk) when the demo is over. `DEMO_MODE` is set to `1` in `render.yaml`; change it in the
service's Environment tab for real-time reminders.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```

They cover the schedule calculation (days, time zones, daylight saving, demo-mode time), the validation of
the model's JSON, the retry-once rule and the reminder sentence check.

## Data

Everything the app stores is in `data/` (SQLite database, uploaded photos, cached audio, Temporal's file).
`data/`, `.venv/` and `.env` are in `.gitignore`. Delete `data/` to start clean.
