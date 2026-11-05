"""LLM extraction: turns a message into JSON fields, never into decisions."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from app.config import AppConfig, Settings
from app.dates import resolve_relative_date
from app.groq import GroqError, auth_headers, post_with_retries
from app.models import ExtractedFields
from app.validator import service_from_text


class ParserError(RuntimeError):
    """Raised when the parser cannot produce valid output after one retry."""


class ParsedPayload(BaseModel):
    """Shape the model is asked to return. Everything else is recomputed in code."""

    patient_name: str | None = None
    service_needed: str | None = None
    preferred_date: str | None = None
    preferred_time_window: str | None = None
    is_emergency: bool = False

    @field_validator("is_emergency", mode="before")
    @classmethod
    def _coerce_emergency(cls, value: object) -> object:
        # Models sometimes emit null or "yes"/"no" for this flag. Treat a missing
        # flag as routine: the keyword backstop still runs before the parser.
        if value is None:
            return False
        if isinstance(value, str):
            cleaned = value.strip().lower()
            if cleaned in {"true", "yes", "y", "1", "urgent"}:
                return True
            if cleaned in {"false", "no", "n", "0", "", "none", "null"}:
                return False
        return value


class GroqParser:
    def __init__(
        self,
        settings: Settings,
        config: AppConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._config = config
        self._transport = transport
        self._sleep = sleep

    def parse(self, text: str, current: ExtractedFields, now: datetime) -> ExtractedFields:
        """Extract fields from one message. Raises ``ParserError`` on bad output."""
        headers = auth_headers(self._settings.groq_api_key)
        body: dict[str, Any] = {
            "model": self._settings.groq_parser_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": self._messages(text, current, now),
        }

        last_error = "unknown error"
        with httpx.Client(timeout=30, transport=self._transport) as client:
            for _ in range(2):
                try:
                    response = post_with_retries(
                        client,
                        "/chat/completions",
                        headers=headers,
                        json_body=body,
                        sleep=self._sleep,
                    )
                    payload = ParsedPayload.model_validate(json.loads(_content(response)))
                    fields = _to_fields(payload)
                    return self._apply_text_overrides(fields, text, now.date())
                except (ValueError, ValidationError, TypeError) as exc:
                    last_error = str(exc)

        raise ParserError(f"Parser returned unusable output after retry: {last_error}")

    def _apply_text_overrides(
        self, fields: ExtractedFields, text: str, today: date
    ) -> ExtractedFields:
        """Correct model mistakes that deterministic code can settle.

        Dates come from the message text, never from the model's arithmetic.
        A date the model did not find stays missing rather than being invented.
        """
        updates: dict[str, object] = {}

        relative = resolve_relative_date(text, today)
        if relative is not None and fields.preferred_date is not None:
            updates["preferred_date"] = relative

        if fields.service_needed is None or fields.service_needed.casefold() == "other":
            fallback = service_from_text(text, self._config.services)
            if fallback is not None:
                updates["service_needed"] = fallback

        return fields.model_copy(update=updates) if updates else fields

    def _messages(
        self, text: str, current: ExtractedFields, now: datetime
    ) -> list[dict[str, str]]:
        windows = ", ".join(self._config.hours.slot_windows)
        services = ", ".join(self._config.services)
        system = (
            "You are the intake parser for a Mumbai dental clinic.\n"
            f"Today is {now.strftime('%A')}, {now.date().isoformat()} (IST).\n"
            f"Known so far: {current.model_dump_json()}\n"
            "From the new message, extract: patient_name, service_needed, "
            "preferred_date (YYYY-MM-DD), preferred_time_window, is_emergency.\n"
            "Rules:\n"
            "- Never guess a name; use null for anything not stated.\n"
            "- Copy patient_name exactly as written; never transliterate "
            "between scripts (राहुल stays राहुल, not Rahul).\n"
            "- Resolve relative dates (kal, parso, Monday ko) against today.\n"
            f"- preferred_time_window must be one of: {windows}.\n"
            f"- service_needed should map to one of: {services}.\n"
            "- is_emergency is true for severe pain, bleeding, swelling, "
            "knocked-out tooth, or facial trauma.\n"
            "Return ONLY a JSON object with those keys."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ]


def _content(response: httpx.Response) -> str:
    payload = response.json()
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("missing choices[0].message.content") from exc
    return str(content)


def _to_fields(payload: ParsedPayload) -> ExtractedFields:
    return ExtractedFields(
        patient_name=_clean(payload.patient_name),
        service_needed=_clean(payload.service_needed),
        preferred_date=_parse_date(payload.preferred_date),
        preferred_time_window=_clean(payload.preferred_time_window),
        is_emergency=payload.is_emergency,
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"null", "none", "n/a"}:
        return None
    return cleaned


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"null", "none"}:
        return None
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError as exc:
        raise ValueError(f"invalid preferred_date: {value!r}") from exc


__all__ = ["GroqError", "GroqParser", "ParserError"]
