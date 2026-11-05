"""Validator tests: names, services, dates, and time windows."""

from __future__ import annotations

from datetime import date

import pytest

from app.config import AppConfig
from app.models import ExtractedFields
from app.validator import (
    normalize_service,
    normalize_time_window,
    validate_date,
    validate_fields,
)


def complete_fields(**overrides: object) -> ExtractedFields:
    data: dict[str, object] = {
        "patient_name": "Rahul Verma",
        "service_needed": "root canal",
        "preferred_date": date(2026, 11, 4),
        "preferred_time_window": "morning",
    }
    data.update(overrides)
    return ExtractedFields.model_validate(data)


def test_complete_fields_are_valid_and_normalised(config: AppConfig) -> None:
    result = validate_fields(complete_fields(), config, today=date(2026, 11, 3))
    assert result.is_valid
    assert result.missing == []
    assert result.errors == {}
    assert result.normalized.service_needed == "Root Canal Treatment"
    assert result.normalized.preferred_time_window == "10:00-12:00"


def test_missing_fields_are_reported_in_fixed_order(config: AppConfig) -> None:
    result = validate_fields(ExtractedFields(), config, today=date(2026, 11, 3))
    assert not result.is_valid
    assert result.missing == [
        "patient_name",
        "service_needed",
        "preferred_date",
        "preferred_time_window",
    ]


@pytest.mark.parametrize("name", ["unknown", "N/A", "test", "Patient", "12345", "A"])
def test_invalid_names_are_rejected(config: AppConfig, name: str) -> None:
    result = validate_fields(complete_fields(patient_name=name), config, today=date(2026, 11, 3))
    assert not result.is_valid
    assert "patient_name" in result.errors


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cleaning", "Scaling & Cleaning"),
        ("Scaling", "Scaling & Cleaning"),
        ("rct", "Root Canal Treatment"),
        ("Root-Canal", "Root Canal Treatment"),
        ("extraction", "Tooth Extraction"),
        ("braces", "Braces / Orthodontics"),
        ("pain checkup", "Pain Check-up"),
        ("Other", "Other"),
    ],
)
def test_service_synonyms_map_to_categories(
    config: AppConfig, raw: str, expected: str
) -> None:
    assert normalize_service(raw, config.services) == expected


def test_unknown_service_is_flagged(config: AppConfig) -> None:
    result = validate_fields(
        complete_fields(service_needed="laser whitening deluxe"),
        config,
        today=date(2026, 11, 3),
    )
    assert result.errors["service_needed"] == "service_unrecognised"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("morning", "10:00-12:00"),
        ("10:00 AM - 12:00 PM", "10:00-12:00"),
        ("12:00-14:00", "12:00-14:00"),
        ("6pm to 8pm", "18:00-20:00"),
        ("shaam", "18:00-20:00"),
    ],
)
def test_time_windows_are_normalised(raw: str, expected: str, config: AppConfig) -> None:
    assert normalize_time_window(raw, config.hours.slot_windows) == expected


@pytest.mark.parametrize("raw", ["21:00-22:00", "09:00-10:00", "whenever", "12:00-11:00"])
def test_out_of_hours_windows_are_rejected(raw: str, config: AppConfig) -> None:
    assert normalize_time_window(raw, config.hours.slot_windows) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 11, 2), "date_past"),
        (date(2026, 12, 31), "date_too_far"),
        (date(2026, 11, 8), "date_closed"),
        (date(2026, 11, 4), None),
    ],
)
def test_date_rules(value: date, expected: str | None, config: AppConfig) -> None:
    assert validate_date(value, config, today=date(2026, 11, 3)) == expected
