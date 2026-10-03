"""The Temporal workflow that owns one confirmed prescription's schedule.

Workflow code must be deterministic: time comes from workflow.now(), waiting is
done with durable timers, and all outside work happens in activities.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from app.activities import (
        finish_prescription,
        log_dose,
        make_speech_audio,
        notify_caregiver,
        publish_reminder,
        write_reminder_text,
    )
    from app.shared import (
        CourseInput,
        CourseResult,
        FinishInput,
        LogDoseInput,
        NotifyInput,
        PublishInput,
        Slot,
        TextInput,
        fallback_sentence,
        real_due_at,
    )

MAX_SNOOZES = 3
AUDIO_WAIT_SECONDS = 5

# Database writes: keep retrying until they succeed.
_DB = dict(start_to_close_timeout=timedelta(seconds=30))
# Gemma on a laptop CPU can be slow. The activity itself falls back to a plain sentence.
_TEXT = dict(
    start_to_close_timeout=timedelta(seconds=120),
    retry_policy=RetryPolicy(maximum_attempts=2),
)
# ElevenLabs: retry with backoff, then give up and let the browser speak the text.
_VOICE = dict(
    start_to_close_timeout=timedelta(seconds=30),
    retry_policy=RetryPolicy(
        initial_interval=timedelta(seconds=2),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=4,
    ),
)
_NOTIFY = dict(
    start_to_close_timeout=timedelta(seconds=20),
    retry_policy=RetryPolicy(maximum_attempts=5),
)


class _Prepared:
    """Reminder text and audio, filled in while the workflow waits for the dose time."""

    def __init__(self) -> None:
        self.text: Optional[str] = None
        self.audio_hash: Optional[str] = None
        self.audio_done = False


def _now() -> datetime:
    now = workflow.now()
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


@workflow.defn(name="PrescriptionCourse")
class PrescriptionCourseWorkflow:
    def __init__(self) -> None:
        self._cancelled = False
        self._response: Optional[str] = None
        self._snooze_minutes: Optional[int] = None
        self._current_due: Optional[str] = None
        self._awaiting = False
        self._next_due: Optional[str] = None
        self._taken = 0
        self._skipped = 0
        self._missed = 0

    # ----- signals and queries -------------------------------------------

    @workflow.signal
    def dose_taken(self, due_at: str) -> None:
        if self._awaiting and due_at == self._current_due:
            self._response = "taken"

    @workflow.signal
    def dose_skipped(self, due_at: str) -> None:
        if self._awaiting and due_at == self._current_due:
            self._response = "skipped"

    @workflow.signal
    def snooze(self, minutes: int) -> None:
        if self._awaiting and minutes > 0:
            self._snooze_minutes = minutes

    @workflow.signal
    def cancel(self) -> None:
        self._cancelled = True

    @workflow.query
    def status(self) -> dict:
        return {
            "next_dose_at": self._next_due,
            "awaiting_response": self._awaiting,
            "taken": self._taken,
            "skipped": self._skipped,
            "missed": self._missed,
            "cancelled": self._cancelled,
        }

    # ----- main loop ------------------------------------------------------

    @workflow.run
    async def run(self, course: CourseInput) -> CourseResult:
        self._taken, self._skipped, self._missed = course.taken, course.skipped, course.missed
        index = course.next_index

        while index < len(course.slots) and not self._cancelled:
            slot = course.slots[index]
            self._next_due = slot.due_at
            await self._run_slot(course, slot)
            if self._cancelled:
                break
            index += 1

            # Start a fresh run once a day so a long course keeps a short history.
            if index < len(course.slots) and course.slots[index].day_index != slot.day_index:
                await workflow.wait_condition(workflow.all_handlers_finished)
                if not self._cancelled:
                    workflow.continue_as_new(
                        replace(
                            course,
                            next_index=index,
                            taken=self._taken,
                            skipped=self._skipped,
                            missed=self._missed,
                        )
                    )

        self._next_due = None
        status = "cancelled" if self._cancelled else "finished"
        await workflow.execute_activity(
            finish_prescription, FinishInput(course.prescription_id, status), **_DB
        )
        return CourseResult(status, self._taken, self._skipped, self._missed)

    async def _run_slot(self, course: CourseInput, slot: Slot) -> None:
        self._current_due = slot.due_at
        self._response = None
        self._snooze_minutes = None

        # Write the sentence and the audio ahead of time, so the reminder is on time.
        prepared = _Prepared()
        preparing = asyncio.create_task(self._prepare(course, slot, prepared))
        try:
            fire_at = real_due_at(slot.due_at, course.time_scale, course.anchor_real, course.anchor_virtual)
            delay = (fire_at - _now()).total_seconds()
            if delay > 0:
                await self._wait(lambda: self._cancelled, delay)
                if self._cancelled:
                    return

            reminders_sent = 0
            if -delay > course.grace_seconds:
                # The worker was down for a long time. Saying "morning tablet" hours
                # late could be harmful, so record the dose as missed instead.
                outcome = "missed"
            else:
                outcome, reminders_sent = await self._remind(course, slot, prepared, fire_at)
                if self._cancelled:
                    return

            responded_at = _now().isoformat(timespec="seconds") if outcome != "missed" else None
            await workflow.execute_activity(
                log_dose,
                LogDoseInput(course.prescription_id, slot.due_at, outcome, reminders_sent, responded_at),
                **_DB,
            )
            count = len(slot.items)
            if outcome == "taken":
                self._taken += count
            elif outcome == "skipped":
                self._skipped += count
            else:
                self._missed += count
                try:
                    await workflow.execute_activity(
                        notify_caregiver,
                        NotifyInput(course.prescription_id, course.patient_first_name, slot.due_at),
                        **_NOTIFY,
                    )
                except ActivityError:
                    pass  # the missed dose is still shown in the caregiver view
        finally:
            self._awaiting = False
            if not preparing.done():
                preparing.cancel()

    async def _remind(self, course: CourseInput, slot: Slot, prepared: _Prepared, fire_at: datetime):
        """Publish the reminder, repeat it if ignored. Returns (outcome, reminders sent)."""
        # Give Gemma a little longer if the sentence is not ready, then use the plain one.
        await self._wait(lambda: prepared.text is not None or self._cancelled, course.text_wait_seconds)
        if self._cancelled:
            return "missed", 0
        if prepared.text is None:
            text = fallback_sentence(course.patient_first_name, slot.label, slot.items)
        else:
            text = prepared.text
            await self._wait(lambda: prepared.audio_done, AUDIO_WAIT_SECONDS)

        scheduled_at = fire_at.isoformat(timespec="seconds")
        attempts = 0
        seq = 0
        snoozes = 0
        self._awaiting = True
        while attempts < course.max_reminders:
            attempts += 1
            seq += 1
            audio_hash = prepared.audio_hash if text == prepared.text else None
            await workflow.execute_activity(
                publish_reminder,
                PublishInput(course.prescription_id, slot.due_at, seq, attempts, text, audio_hash, scheduled_at),
                **_DB,
            )
            await self._wait(
                lambda: self._response is not None or self._cancelled or self._snooze_minutes is not None,
                course.followup_seconds,
            )
            if self._cancelled or self._response:
                break
            if self._snooze_minutes is not None:
                minutes, self._snooze_minutes = self._snooze_minutes, None
                if snoozes < MAX_SNOOZES:
                    snoozes += 1
                    seconds = minutes * 60 / course.time_scale
                    if course.time_scale != 1:
                        seconds = max(seconds, course.followup_seconds)
                    await self._wait(lambda: self._response is not None or self._cancelled, seconds)
                    if self._cancelled or self._response:
                        break
                    attempts -= 1  # a snooze does not use up a reminder
        return self._response or "missed", attempts

    async def _prepare(self, course: CourseInput, slot: Slot, prepared: _Prepared) -> None:
        try:
            prepared.text = await workflow.execute_activity(
                write_reminder_text,
                TextInput(course.patient_first_name, course.language, slot.label, slot.items),
                **_TEXT,
            )
        except ActivityError:
            prepared.text = fallback_sentence(course.patient_first_name, slot.label, slot.items)
        try:
            prepared.audio_hash = await workflow.execute_activity(
                make_speech_audio, prepared.text, **_VOICE
            )
        except ActivityError:
            prepared.audio_hash = None  # the reminder screen speaks the text itself
        prepared.audio_done = True

    async def _wait(self, condition: Callable[[], bool], seconds: float) -> bool:
        """Wait on a durable timer until the condition holds or the time is up."""
        try:
            await workflow.wait_condition(condition, timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False
