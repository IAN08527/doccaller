"""Validate the extraction corpus and its canonical answers."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.config import AppConfig
from app.emergency import is_emergency
from app.validator import normalize_service, normalize_time_window

FIXTURE = Path(__file__).parent / "fixtures" / "extraction_cases.json"
EXPECTED_KEYS = {
    "patient_name",
    "service_needed",
    "preferred_date",
    "preferred_time_window",
    "is_emergency",
}


def load_fixture() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


FIXTURE_DATA = load_fixture()
CASES: list[dict[str, Any]] = FIXTURE_DATA["cases"]
REFERENCE_DATE = date.fromisoformat(str(FIXTURE_DATA["reference_date"]))


def test_corpus_has_at_least_thirty_cases_across_four_languages() -> None:
    assert len(CASES) >= 30
    assert {case["language"] for case in CASES} == {"en", "hinglish", "hi", "mr"}


def test_case_ids_are_unique() -> None:
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))


def test_every_case_has_the_full_expected_schema() -> None:
    for case in CASES:
        assert set(case["expected"]) == EXPECTED_KEYS, case["id"]
        assert case["text"].strip(), case["id"]


def test_expected_services_are_canonical(config: AppConfig) -> None:
    for case in CASES:
        service = case["expected"]["service_needed"]
        if service is None:
            continue
        assert normalize_service(service, config.services) == service, case["id"]


def test_expected_time_windows_are_canonical(config: AppConfig) -> None:
    for case in CASES:
        window = case["expected"]["preferred_time_window"]
        if window is None:
            continue
        assert normalize_time_window(window, config.hours.slot_windows) == window, case["id"]


def test_expected_dates_parse() -> None:
    for case in CASES:
        value = case["expected"]["preferred_date"]
        if value is not None:
            date.fromisoformat(value)


def test_emergency_cases_are_covered_by_the_keyword_backstop() -> None:
    emergency_cases = [
        case for case in CASES if case["expected"]["is_emergency"]
    ]
    assert len(emergency_cases) >= 6
    for case in emergency_cases:
        assert is_emergency(case["text"]), case["id"]


def test_routine_cases_do_not_trigger_the_keyword_backstop() -> None:
    for case in CASES:
        if case["expected"]["is_emergency"]:
            continue
        assert not is_emergency(case["text"]), case["id"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["id"]))
def test_relative_dates_resolve_against_the_reference_date(
    case: dict[str, Any],
) -> None:
    """The corpus is pinned to a fixed date so live evaluation is repeatable."""
    value = case["expected"]["preferred_date"]
    if value is None:
        return
    resolved = date.fromisoformat(value)
    assert abs((resolved - REFERENCE_DATE).days) <= 30
