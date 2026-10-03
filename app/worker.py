"""Temporal worker. Run with: python -m app.worker"""

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from app import db
from app.activities import ALL_ACTIVITIES
from app.config import get_settings
from app.shared import TASK_QUEUE
from app.workflows import PrescriptionCourseWorkflow


async def main() -> None:
    settings = get_settings()
    db.init_db()
    client = await Client.connect(settings.temporal_address)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[PrescriptionCourseWorkflow],
        activities=ALL_ACTIVITIES,
    )
    voice = "ElevenLabs" if settings.voice_enabled else "browser speech (no ElevenLabs key)"
    print(f"Worker running. Temporal: {settings.temporal_address}  Model: {settings.gemma_model}  Voice: {voice}")
    print("Press Ctrl+C to stop.")
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Worker stopped.")
