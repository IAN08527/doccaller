"""Parser tests using httpx.MockTransport (no network, no API key)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime

import httpx
import pytest

from app.config import AppConfig, Settings
from app.groq import GroqError
from app.models import ExtractedFields
from app.parser import GroqParser, ParserError


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def make_parser(
    config: AppConfig,
    handler: Callable[[httpx.Request], httpx.Response],
) -> GroqParser:
    settings = Settings(groq_api_key="test-key")
    return GroqParser(
        settings,
        config,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


def test_parser_extracts_and_normalises_fields(config: AppConfig, now: datetime) -> None:
    payload = {
        "patient_name": "Rahul Verma",
        "service_needed": "Root Canal Treatment",
        "preferred_date": "2026-11-04",
        "preferred_time_window": "10:00-12:00",
        "is_emergency": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-oss-20b"
        assert body["temperature"] == 0
        assert body["response_format"] == {"type": "json_object"}
        system = body["messages"][0]["content"]
        assert "Tuesday, 2026-11-03" in system
        assert "known" in system.lower()
        assert body["messages"][1]["content"] == "I need a root canal on 4 Nov morning"
        return completion(json.dumps(payload))

    parser = make_parser(config, handler)
    result = parser.parse("I need a root canal on 4 Nov morning", ExtractedFields(), now)

    assert result.patient_name == "Rahul Verma"
    assert result.service_needed == "Root Canal Treatment"
    assert result.preferred_date == date(2026, 11, 4)
    assert result.preferred_time_window == "10:00-12:00"
    assert result.is_emergency is False


def test_parser_null_strings_become_none(config: AppConfig, now: datetime) -> None:
    payload = {
        "patient_name": "null",
        "service_needed": "",
        "preferred_date": None,
        "preferred_time_window": "none",
        "is_emergency": False,
    }
    parser = make_parser(config, lambda _: completion(json.dumps(payload)))
    result = parser.parse("hello", ExtractedFields(), now)
    assert result.patient_name is None
    assert result.service_needed is None
    assert result.preferred_date is None
    assert result.preferred_time_window is None


def test_parser_treats_null_emergency_flag_as_routine(
    config: AppConfig, now: datetime
) -> None:
    payload = {
        "patient_name": "Priya Sharma",
        "service_needed": "Scaling & Cleaning",
        "preferred_date": "2026-11-05",
        "preferred_time_window": "12:00-14:00",
        "is_emergency": None,
    }
    parser = make_parser(config, lambda _: completion(json.dumps(payload)))
    result = parser.parse("Priya Sharma, cleaning 5 Nov afternoon", ExtractedFields(), now)
    assert result.patient_name == "Priya Sharma"
    assert result.is_emergency is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [("yes", True), ("TRUE", True), ("1", True), ("no", False), ("", False)],
)
def test_parser_accepts_verbal_emergency_flags(
    config: AppConfig, now: datetime, value: str, expected: bool
) -> None:
    payload = {
        "patient_name": None,
        "service_needed": None,
        "preferred_date": None,
        "preferred_time_window": None,
        "is_emergency": value,
    }
    parser = make_parser(config, lambda _: completion(json.dumps(payload)))
    result = parser.parse("hello", ExtractedFields(), now)
    assert result.is_emergency is expected


def test_parser_corrects_a_misresolved_relative_date(
    config: AppConfig, now: datetime
) -> None:
    payload = {
        "patient_name": "Priya",
        "service_needed": "Scaling & Cleaning",
        "preferred_date": "2026-11-01",
        "preferred_time_window": "10:00-12:00",
        "is_emergency": False,
    }
    parser = make_parser(config, lambda _: completion(json.dumps(payload)))
    result = parser.parse("Priya, parso subah cleaning chahiye", ExtractedFields(), now)
    assert result.preferred_date == date(2026, 11, 5)


def test_parser_does_not_invent_a_missing_date(config: AppConfig, now: datetime) -> None:
    payload = {
        "patient_name": "Priya",
        "service_needed": "Scaling & Cleaning",
        "preferred_date": None,
        "preferred_time_window": None,
        "is_emergency": False,
    }
    parser = make_parser(config, lambda _: completion(json.dumps(payload)))
    result = parser.parse("Priya, parso cleaning chahiye", ExtractedFields(), now)
    assert result.preferred_date is None


def test_parser_replaces_other_with_a_named_service(
    config: AppConfig, now: datetime
) -> None:
    payload = {
        "patient_name": None,
        "service_needed": "Other",
        "preferred_date": "2026-11-02",
        "preferred_time_window": "18:00-20:00",
        "is_emergency": False,
    }
    parser = make_parser(config, lambda _: completion(json.dumps(payload)))
    result = parser.parse("I want a checkup yesterday evening", ExtractedFields(), now)
    assert result.service_needed == "Pain Check-up"


def test_parser_retries_invalid_json_once(config: AppConfig, now: datetime) -> None:
    calls = {"count": 0}
    payload = {
        "patient_name": "Priya Sharma",
        "service_needed": "Scaling & Cleaning",
        "preferred_date": "2026-11-05",
        "preferred_time_window": "12:00-14:00",
        "is_emergency": False,
    }

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return completion("not valid json")
        return completion(json.dumps(payload))

    parser = make_parser(config, handler)
    result = parser.parse("Priya Sharma, cleaning, 5 Nov afternoon", ExtractedFields(), now)

    assert calls["count"] == 2
    assert result.patient_name == "Priya Sharma"


def test_parser_gives_up_after_retry(config: AppConfig, now: datetime) -> None:
    calls = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return completion("not valid json")

    parser = make_parser(config, handler)
    with pytest.raises(ParserError):
        parser.parse("hello", ExtractedFields(), now)
    assert calls["count"] == 2


def test_parser_retries_rate_limit_then_succeeds(config: AppConfig, now: datetime) -> None:
    calls = {"count": 0}
    payload = {
        "patient_name": "Rahul Verma",
        "service_needed": None,
        "preferred_date": None,
        "preferred_time_window": None,
        "is_emergency": False,
    }

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return completion(json.dumps(payload))

    parser = make_parser(config, handler)
    result = parser.parse("Rahul Verma", ExtractedFields(), now)

    assert calls["count"] == 2
    assert result.patient_name == "Rahul Verma"


def test_parser_non_retryable_error_raises(config: AppConfig, now: datetime) -> None:
    parser = make_parser(
        config, lambda _: httpx.Response(400, json={"error": "model not available"})
    )
    with pytest.raises(GroqError):
        parser.parse("hello", ExtractedFields(), now)


def test_parser_invalid_date_triggers_retry(config: AppConfig, now: datetime) -> None:
    calls = {"count": 0}
    bad = {
        "patient_name": "Rahul Verma",
        "service_needed": None,
        "preferred_date": "next Tuesday",
        "preferred_time_window": None,
        "is_emergency": False,
    }

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return completion(json.dumps(bad))

    parser = make_parser(config, handler)
    with pytest.raises(ParserError):
        parser.parse("next Tuesday", ExtractedFields(), now)
    assert calls["count"] == 2
