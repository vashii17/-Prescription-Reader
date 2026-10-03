from datetime import date, datetime, timedelta, timezone

import pytest

from app.schedule import DEFAULT_SLOT_TIMES, build_slots, dose_times, parse_clock, parse_slot_times
from app.shared import DEMO_TIME_SCALE, real_due_at


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def medicine(name="Metformin", times=("morning", "night"), days=3, **extra):
    return {
        "name": name,
        "strength": "500 mg",
        "dose_amount": "1 tablet",
        "times_of_day": list(times),
        "with_food": "after",
        "duration_days": days,
        **extra,
    }


def test_dose_times_cover_every_day_in_order():
    times = list(dose_times(["night", "morning"], 3, DEFAULT_SLOT_TIMES, date(2026, 10, 3), "UTC"))
    assert [(day, slot) for day, slot, _ in times] == [
        (0, "morning"), (0, "night"), (1, "morning"), (1, "night"), (2, "morning"), (2, "night"),
    ]
    assert times[0][2] == utc(2026, 10, 3, 8)
    assert times[-1][2] == utc(2026, 10, 5, 21)


def test_times_are_converted_from_the_patient_time_zone():
    # India is UTC+5:30 all year.
    times = [due for _, _, due in dose_times(["morning", "night"], 1, DEFAULT_SLOT_TIMES, date(2026, 10, 3), "Asia/Kolkata")]
    assert times == [utc(2026, 10, 3, 2, 30), utc(2026, 10, 3, 15, 30)]


def test_evening_dose_can_fall_on_the_next_utc_day():
    times = [due for _, _, due in dose_times(["night"], 1, DEFAULT_SLOT_TIMES, date(2026, 10, 3), "America/Los_Angeles")]
    assert times == [utc(2026, 10, 4, 4)]  # 21:00 PDT


def test_local_clock_time_is_kept_across_a_daylight_saving_change():
    # US clocks go back on 1 November 2026: 08:00 local moves from 12:00 to 13:00 UTC.
    times = [due for _, _, due in dose_times(["morning"], 3, DEFAULT_SLOT_TIMES, date(2026, 10, 31), "America/New_York")]
    assert times == [utc(2026, 10, 31, 12), utc(2026, 11, 1, 13), utc(2026, 11, 2, 13)]
    # So that "day" is 25 hours long, not 24.
    assert times[1] - times[0] == timedelta(hours=25)


def test_medicines_due_together_share_one_slot():
    slots = build_slots(
        [medicine("Metformin", ("morning", "night"), 2), medicine("Amlodipine", ("morning",), 1)],
        DEFAULT_SLOT_TIMES, date(2026, 10, 3), "UTC",
    )
    assert [(slot.day_index, slot.label, [item.name for item in slot.items]) for slot in slots] == [
        (0, "morning", ["Metformin", "Amlodipine"]),
        (0, "night", ["Metformin"]),
        (1, "morning", ["Metformin"]),
        (1, "night", ["Metformin"]),
    ]
    assert slots[0].due_at == "2026-10-03T08:00:00+00:00"


def test_slots_are_sorted_when_courses_have_different_lengths():
    slots = build_slots(
        [medicine("A", ("night",), 1), medicine("B", ("morning",), 2)],
        DEFAULT_SLOT_TIMES, date(2026, 10, 3), "Asia/Kolkata",
    )
    assert [slot.due_at for slot in slots] == sorted(slot.due_at for slot in slots)
    assert len(slots) == 3


def test_custom_clock_times_are_used():
    custom = {"morning": "07:15", "afternoon": "13:00", "evening": "17:30", "night": "22:45"}
    times = [due for _, _, due in dose_times(["morning", "night"], 1, custom, date(2026, 10, 3), "UTC")]
    assert times == [utc(2026, 10, 3, 7, 15), utc(2026, 10, 3, 22, 45)]


@pytest.mark.parametrize("bad", ["8:00", "24:00", "08:60", "morning", ""])
def test_bad_clock_times_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_clock(bad)


def test_slot_times_must_be_complete_and_in_order():
    with pytest.raises(ValueError):
        parse_slot_times({"morning": "08:00", "afternoon": "14:00", "evening": "18:00"})
    with pytest.raises(ValueError):
        parse_slot_times({"morning": "08:00", "afternoon": "14:00", "evening": "21:00", "night": "21:00"})


def test_real_time_is_unchanged_outside_demo_mode():
    assert real_due_at("2026-10-03T08:00:00+00:00", 1.0, "", "") == utc(2026, 10, 3, 8)


def test_demo_mode_compresses_one_day_into_one_minute():
    anchor_real = "2026-10-03T12:00:00+00:00"
    anchor_virtual = "2026-10-03T07:00:00+00:00"
    first = real_due_at("2026-10-03T08:00:00+00:00", DEMO_TIME_SCALE, anchor_real, anchor_virtual)
    next_day = real_due_at("2026-10-04T08:00:00+00:00", DEMO_TIME_SCALE, anchor_real, anchor_virtual)
    assert first == datetime(2026, 10, 3, 12, 0, 2, 500000, tzinfo=timezone.utc)
    assert next_day - first == timedelta(minutes=1)
