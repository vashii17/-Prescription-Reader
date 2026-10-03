import asyncio
import json

import httpx
import pytest

from app.extraction import ExtractionError, extract_medicines, parse_extraction
from app.reminder_text import clean_sentence, is_faithful
from app.shared import ReminderItem, fallback_sentence

HIGH = {
    field: "high"
    for field in ("name", "strength", "dose_amount", "frequency_per_day", "times_of_day", "with_food", "duration_days", "notes")
}


def med(**overrides):
    base = {
        "name": "Metformin",
        "strength": "500 mg",
        "dose_amount": "1 tablet",
        "frequency_per_day": 2,
        "times_of_day": ["morning", "night"],
        "with_food": "after",
        "duration_days": 5,
        "notes": "swallow whole",
        "confidence": dict(HIGH),
    }
    base.update(overrides)
    return base


def reply(*medicines):
    return json.dumps({"medicines": list(medicines)})


def test_valid_reply_is_accepted():
    extraction = parse_extraction(reply(med()))
    medicine = extraction.medicines[0]
    assert medicine.name == "Metformin"
    assert medicine.times_of_day == ["morning", "night"]
    assert medicine.confidence.name == "high"


def test_empty_list_is_valid():
    assert parse_extraction(reply()).medicines == []


@pytest.mark.parametrize(
    "raw",
    [
        "Here are the medicines: Metformin",  # not JSON
        "",
        '{"medicines": "Metformin"}',  # wrong type
        '{"drugs": []}',  # wrong key
        "[]",  # wrong top level
    ],
)
def test_malformed_replies_are_rejected(raw):
    with pytest.raises(ExtractionError):
        parse_extraction(raw)


@pytest.mark.parametrize(
    "overrides",
    [
        {"times_of_day": ["breakfast"]},
        {"with_food": "with milk"},
        {"duration_days": 0},
        {"duration_days": "a week"},
        {"frequency_per_day": True},
        {"frequency_per_day": 40},
        {"confidence": {**HIGH, "name": "certain"}},
        {"confidence": {"name": "high"}},  # other fields missing
        {"dosage": "1"},  # unknown field
    ],
)
def test_out_of_range_values_are_rejected(overrides):
    with pytest.raises(ExtractionError):
        parse_extraction(reply(med(**overrides)))


def test_missing_field_is_rejected():
    medicine = med()
    del medicine["duration_days"]
    with pytest.raises(ExtractionError):
        parse_extraction(reply(medicine))


def test_unreadable_values_become_empty_and_low_confidence():
    extraction = parse_extraction(
        reply(med(name="  ", strength="unknown", duration_days=None, times_of_day=[], with_food="unknown"))
    )
    medicine = extraction.medicines[0]
    assert medicine.name is None
    assert medicine.strength is None
    assert medicine.duration_days is None
    # The model said "high", but an empty field is always flagged for the caregiver.
    for field in ("name", "strength", "duration_days", "times_of_day", "with_food"):
        assert getattr(medicine.confidence, field) == "low"
    assert medicine.confidence.dose_amount == "high"


def test_times_of_day_are_deduplicated_and_ordered():
    extraction = parse_extraction(reply(med(times_of_day=["night", "morning", "night"])))
    assert extraction.medicines[0].times_of_day == ["morning", "night"]


def run(replies):
    """Run extract_medicines against a fake model that returns the given replies in turn."""
    calls = []

    async def fake_model(_image):
        calls.append(1)
        item = replies[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    outcome = asyncio.run(extract_medicines(b"jpeg", fake_model))
    return outcome, len(calls)


def test_good_first_reply_needs_one_call():
    outcome, calls = run([reply(med())])
    assert (outcome.status, calls, len(outcome.medicines)) == ("ok", 1, 1)


def test_invalid_reply_is_retried_once():
    outcome, calls = run(["not json", reply(med())])
    assert (outcome.status, calls, len(outcome.medicines)) == ("ok", 2, 1)


def test_two_invalid_replies_give_an_empty_form():
    outcome, calls = run(["not json", '{"medicines": 3}', reply(med())])
    assert (outcome.status, calls, outcome.medicines) == ("invalid", 2, [])


def test_unreachable_model_gives_an_empty_form():
    outcome, calls = run([httpx.ConnectError("refused")])
    assert (outcome.status, calls, outcome.medicines) == ("unavailable", 1, [])


# ----- reminder sentence guard ---------------------------------------------

ITEMS = [ReminderItem("Metformin", "500 mg", "1 tablet", "after")]


def test_fallback_sentence_uses_only_confirmed_fields():
    assert fallback_sentence("Asha", "evening", ITEMS) == (
        "Asha, it is time for your evening medicine: 1 tablet Metformin 500 mg, after food."
    )


def test_faithful_sentence_is_accepted():
    assert is_faithful("Asha, it is time for your evening tablet: 1 Metformin 500 mg, after food.", ITEMS)
    assert is_faithful("आशा, शाम की दवा का समय हो गया है: Metformin 500 mg की 1 गोली, खाने के बाद।", ITEMS)


def test_sentence_with_an_invented_number_is_rejected():
    assert not is_faithful("Asha, take 2 tablets of Metformin 500 mg after food.", ITEMS)
    assert not is_faithful("Asha, take 1 Metformin 500 mg at 8:00.", ITEMS)
    assert not is_faithful("आशा, Metformin 500 mg की २ गोली लें।", ITEMS)  # Devanagari 2


def test_sentence_missing_a_medicine_is_rejected():
    assert not is_faithful("Asha, it is time for your evening tablet.", ITEMS)
    assert not is_faithful("", ITEMS)


def test_clean_sentence_strips_quotes_and_line_breaks():
    assert clean_sentence('"Asha, take\n your tablet."\n') == "Asha, take your tablet."
