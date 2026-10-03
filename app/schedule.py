"""Turns a confirmed schedule into exact dose times. Pure functions, no I/O."""

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterator, List, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

from app.shared import SLOT_ORDER, ReminderItem, Slot

DEFAULT_SLOT_TIMES = {"morning": "08:00", "afternoon": "14:00", "evening": "18:00", "night": "21:00"}

_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_clock(value: str) -> time:
    match = _CLOCK.match(value.strip())
    if not match:
        raise ValueError(f"'{value}' is not a time in HH:MM form")
    return time(int(match.group(1)), int(match.group(2)))


def parse_slot_times(slot_times: Mapping[str, str]) -> Dict[str, time]:
    """Check that all four slots have a clock time and that they are in order."""
    missing = [slot for slot in SLOT_ORDER if slot not in slot_times]
    if missing:
        raise ValueError(f"Missing time for: {', '.join(missing)}")
    parsed = {slot: parse_clock(slot_times[slot]) for slot in SLOT_ORDER}
    for earlier, later in zip(SLOT_ORDER, SLOT_ORDER[1:]):
        if parsed[earlier] >= parsed[later]:
            raise ValueError(f"The {later} time must be later than the {earlier} time")
    return parsed


def to_utc(day: date, clock: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, clock, tzinfo=tz).astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def dose_times(
    times_of_day: Sequence[str],
    duration_days: int,
    slot_times: Mapping[str, str],
    start_date: date,
    tz_name: str,
) -> Iterator[Tuple[int, str, datetime]]:
    """Yield (day_index, slot, due time in UTC) for one medicine, in time order."""
    clocks = parse_slot_times(slot_times)
    tz = ZoneInfo(tz_name)
    wanted = [slot for slot in SLOT_ORDER if slot in set(times_of_day)]
    for day_index in range(duration_days):
        day = start_date + timedelta(days=day_index)
        for slot in wanted:
            yield day_index, slot, to_utc(day, clocks[slot], tz)


def build_slots(
    medicines: Sequence[Mapping],
    slot_times: Mapping[str, str],
    start_date: date,
    tz_name: str,
) -> List[Slot]:
    """Group every dose of every medicine by due time, earliest first."""
    by_due: Dict[datetime, Slot] = {}
    for medicine in medicines:
        item = ReminderItem(
            name=medicine["name"],
            strength=medicine.get("strength") or "",
            dose_amount=medicine["dose_amount"],
            with_food=medicine.get("with_food") or "unknown",
        )
        for day_index, label, due in dose_times(
            medicine["times_of_day"], medicine["duration_days"], slot_times, start_date, tz_name
        ):
            slot = by_due.get(due)
            if slot is None:
                slot = by_due[due] = Slot(due_at=iso(due), label=label, day_index=day_index)
            slot.items.append(item)
    return [by_due[due] for due in sorted(by_due)]
