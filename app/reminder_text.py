"""Gemma phrases one short reminder sentence from confirmed fields only.

The sentence is checked before use: it must name every medicine and may not
contain a number that is not in the confirmed schedule. Anything else falls
back to a plain sentence built directly from the confirmed fields.
"""

import hashlib
import json
import re
import unicodedata
from typing import List, Set

import httpx

from app import db
from app.config import Settings
from app.shared import LANGUAGES, ReminderItem, TextInput, describe_item, fallback_sentence

MAX_SENTENCE_LENGTH = 400

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _ascii_digits(text: str) -> str:
    """Replace digits from any script (for example Devanagari) with 0-9."""
    out = []
    for char in text:
        if char.isdigit():
            try:
                out.append(str(unicodedata.digit(char)))
                continue
            except ValueError:
                pass
        out.append(char)
    return "".join(out)


def _numbers(text: str) -> Set[str]:
    return {match.replace(",", ".") for match in _NUMBER.findall(_ascii_digits(text))}


def clean_sentence(raw: str) -> str:
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    return " ".join(lines).strip().strip('"“”').strip()


def is_faithful(sentence: str, items: List[ReminderItem]) -> bool:
    """True if the sentence names every medicine and invents no numbers."""
    if not sentence or len(sentence) > MAX_SENTENCE_LENGTH:
        return False
    folded = sentence.casefold()
    if any(item.name.casefold() not in folded for item in items):
        return False
    allowed: Set[str] = set()
    for item in items:
        allowed |= _numbers(f"{item.name} {item.strength} {item.dose_amount}")
    return _numbers(sentence) <= allowed


def build_prompt(inp: TextInput) -> str:
    language = LANGUAGES.get(inp.language, LANGUAGES["en"])[0]
    medicines = "\n".join(f"- {describe_item(item)}" for item in inp.items)
    example = fallback_sentence(inp.patient_first_name, inp.label, inp.items)
    return (
        "Write one short spoken reminder to take medicine.\n"
        f"Language: {language}. Write the sentence in {language}, but keep every medicine name "
        "and every number exactly as written below, in the same letters and digits.\n"
        "Use only the facts below. Add nothing: no advice, no warnings, no reasons, no clock times, "
        "no greeting beyond the first name.\n"
        f"First name: {inp.patient_first_name}\n"
        f"Time of day: {inp.label}\n"
        f"Medicines:\n{medicines}\n"
        f'The same reminder in English would be: "{example}"\n'
        "Reply with the sentence only."
    )


def cache_key(settings: Settings, inp: TextInput) -> str:
    payload = json.dumps(
        [
            settings.gemma_model,
            inp.language,
            inp.patient_first_name,
            inp.label,
            [[i.name, i.strength, i.dose_amount, i.with_food] for i in inp.items],
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def ask_gemma(settings: Settings, prompt: str) -> str:
    body = {
        "model": settings.gemma_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(100, connect=5)) as client:
        response = await client.post(f"{settings.ollama_base_url}/api/chat", json=body)
        response.raise_for_status()
        return response.json()["message"]["content"]


async def write_sentence(settings: Settings, inp: TextInput) -> str:
    """Return the reminder sentence. Never raises: the fallback is always available."""
    key = cache_key(settings, inp)
    conn = db.connect()
    try:
        row = conn.execute("SELECT text FROM reminder_text_cache WHERE hash = ?", (key,)).fetchone()
        if row:
            return row["text"]
        try:
            sentence = clean_sentence(await ask_gemma(settings, build_prompt(inp)))
        except (httpx.HTTPError, KeyError, ValueError):
            # Ollama is down or slow. Do not cache, so a later reminder tries again.
            return fallback_sentence(inp.patient_first_name, inp.label, inp.items)
        if not is_faithful(sentence, inp.items):
            sentence = fallback_sentence(inp.patient_first_name, inp.label, inp.items)
        conn.execute(
            "INSERT OR IGNORE INTO reminder_text_cache (hash, text, created_at) VALUES (?, ?, ?)",
            (key, sentence, db.utc_now()),
        )
        return sentence
    finally:
        conn.close()
