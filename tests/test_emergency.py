"""Emergency keyword backstop tests driven by multilingual fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.emergency import is_emergency

FIXTURE = Path(__file__).parent / "fixtures" / "emergency_phrases.json"


def _phrases(kind: str) -> list[str]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [str(entry) for entry in payload[kind]]


EMERGENCY_PHRASES = _phrases("emergency")
ROUTINE_PHRASES = _phrases("routine")


@pytest.mark.parametrize("text", EMERGENCY_PHRASES)
def test_emergencies_are_detected(text: str) -> None:
    assert is_emergency(text)


@pytest.mark.parametrize("text", ROUTINE_PHRASES)
def test_routine_messages_are_not_emergencies(text: str) -> None:
    assert not is_emergency(text)


def test_fixture_covers_english_hindi_marathi_and_hinglish() -> None:
    assert len(EMERGENCY_PHRASES) >= 15
    assert any(
        "\u0900" <= character <= "\u097f"
        for text in EMERGENCY_PHRASES
        for character in text
    )
    assert any("dard" in text.lower() for text in EMERGENCY_PHRASES)
