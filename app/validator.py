"""Deterministic validation and normalisation of extracted fields."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.config import AppConfig
from app.models import ExtractedFields

FIELD_ORDER: tuple[str, ...] = (
    "patient_name",
    "service_needed",
    "preferred_date",
    "preferred_time_window",
)

_NAME_PLACEHOLDERS = {
    "unknown",
    "n/a",
    "na",
    "none",
    "null",
    "patient",
    "test",
    "name",
    "-",
}

_SERVICE_SYNONYMS: dict[str, str] = {
    "cleaning": "Scaling & Cleaning",
    "scaling": "Scaling & Cleaning",
    "rct": "Root Canal Treatment",
    "root canal": "Root Canal Treatment",
    "root canal treatment": "Root Canal Treatment",
    "extraction": "Tooth Extraction",
    "extract": "Tooth Extraction",
    "braces": "Braces / Orthodontics",
    "orthodontics": "Braces / Orthodontics",
    "crown": "Crowns & Bridges",
    "bridge": "Crowns & Bridges",
    "checkup": "Pain Check-up",
    "check up": "Pain Check-up",
    "consultation": "Pain Check-up",
    "pain": "Pain Check-up",
    "hurt": "Pain Check-up",
    "toothache": "Pain Check-up",
}

_TIME_ALIASES: dict[str, str] = {
    "morning": "10:00-12:00",
    "subah": "10:00-12:00",
    "सुबह": "10:00-12:00",
    "सकाळ": "10:00-12:00",
    "afternoon": "12:00-14:00",
    "dopahar": "12:00-14:00",
    "दोपहर": "12:00-14:00",
    "दुपार": "12:00-14:00",
    "evening": "18:00-20:00",
    "shaam": "18:00-20:00",
    "शाम": "18:00-20:00",
    "संध्याकाळ": "18:00-20:00",
}

_WINDOW_SPLIT = re.compile(r"\s*(?:-|–|—|to)\s*")


@dataclass(slots=True)
class ValidationResult:
    missing: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    normalized: ExtractedFields = field(default_factory=ExtractedFields)

    @property
    def is_valid(self) -> bool:
        return not self.missing and not self.errors


def normalize_service(raw: str, services: list[str]) -> str | None:
    text = unicodedata.normalize("NFKC", raw).strip()
    if not text:
        return None
    key = _service_key(text)
    by_key = {_service_key(service): service for service in services}

    if key in by_key:
        return by_key[key]

    synonym = _SERVICE_SYNONYMS.get(text.lower()) or _SERVICE_SYNONYMS.get(key)
    if synonym is not None and synonym in services:
        return synonym

    for service in services:
        service_key = _service_key(service)
        if key in service_key or service_key in key:
            return service
    return None


def service_from_text(text: str, services: list[str]) -> str | None:
    """Find a known service by keyword, used when the model says ``Other``."""
    lowered = unicodedata.normalize("NFKC", text).casefold()

    for phrase in sorted(_SERVICE_SYNONYMS, key=len, reverse=True):
        canonical = _SERVICE_SYNONYMS[phrase]
        if canonical in services and _token_present(lowered, phrase):
            return canonical

    for service in services:
        if _token_present(lowered, service.casefold()):
            return service
    return None


def normalize_time_window(raw: str, windows: list[str]) -> str | None:
    text = unicodedata.normalize("NFKC", raw).strip().lower()
    alias = _TIME_ALIASES.get(text)
    if alias is not None:
        return alias if alias in windows else None

    parts = _WINDOW_SPLIT.split(re.sub(r"\s+", " ", text))
    if len(parts) != 2:
        return None
    start = _parse_clock(parts[0])
    end = _parse_clock(parts[1])
    if start is None or end is None or start >= end:
        return None
    canonical = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
    return canonical if canonical in windows else None


def validate_date(value: date, config: AppConfig, today: date) -> str | None:
    if value < today:
        return "date_past"
    if value > today + timedelta(days=30):
        return "date_too_far"
    closed = {day.strip().lower() for day in config.hours.closed_days}
    if value.strftime("%A").lower() in closed:
        return "date_closed"
    return None


def validate_name(name: str) -> str | None:
    cleaned = name.strip()
    if len(cleaned) < 2:
        return "name_too_short"
    if cleaned.lower() in _NAME_PLACEHOLDERS:
        return "name_placeholder"
    if any(character.isdigit() for character in cleaned):
        return "name_has_digits"
    return None


def validate_fields(fields: ExtractedFields, config: AppConfig, today: date) -> ValidationResult:
    missing: list[str] = []
    errors: dict[str, str] = {}
    normalized = fields.model_copy(deep=True)

    if not fields.patient_name:
        missing.append("patient_name")
    else:
        normalized.patient_name = fields.patient_name.strip()
        name_error = validate_name(normalized.patient_name)
        if name_error is not None:
            errors["patient_name"] = name_error

    if not fields.service_needed:
        missing.append("service_needed")
    else:
        canonical_service = normalize_service(fields.service_needed, config.services)
        if canonical_service is None:
            errors["service_needed"] = "service_unrecognised"
        else:
            normalized.service_needed = canonical_service

    if not fields.preferred_date:
        missing.append("preferred_date")
    else:
        date_error = validate_date(fields.preferred_date, config, today)
        if date_error is not None:
            errors["preferred_date"] = date_error

    if not fields.preferred_time_window:
        missing.append("preferred_time_window")
    else:
        canonical_window = normalize_time_window(
            fields.preferred_time_window, config.hours.slot_windows
        )
        if canonical_window is None:
            errors["preferred_time_window"] = "time_outside_hours"
        else:
            normalized.preferred_time_window = canonical_window

    return ValidationResult(missing=missing, errors=errors, normalized=normalized)


def _service_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u0900-\u097F]+", "", value.lower())


def _token_present(text: str, token: str) -> bool:
    escaped = re.escape(token)
    if token.isascii():
        return re.search(rf"(?<![a-z]){escaped}(?![a-z])", text) is not None
    return re.search(rf"(?<![\u0900-\u097F]){escaped}", text) is not None


def _parse_clock(value: str) -> time | None:
    cleaned = value.strip().lower().replace(".", "").replace(" ", "")
    for fmt in ("%I:%M%p", "%I%p", "%H:%M"):
        try:
            return datetime.strptime(cleaned.upper(), fmt).time()
        except ValueError:
            continue
    return None
