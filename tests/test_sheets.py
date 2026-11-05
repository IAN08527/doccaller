"""Sheets payload tests (no Google credentials required)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.channels.web import WebChannel
from app.config import AppConfig
from app.models import ExtractedFields, LeadStatus, Session
from app.sheets import (
    LEAD_HEADERS,
    LoggingSheetsClient,
    build_lead_row,
    build_sheets_client,
    call_with_retries,
    conditional_format_requests,
    row_from_append_result,
    sheet_id_from_metadata,
)

PHONE = "+919820012345"


def make_session(now: datetime, *, urgent: bool = False) -> Session:
    return Session(
        phone=PHONE,
        fields=ExtractedFields(
            patient_name="Rahul Verma",
            service_needed="Root Canal Treatment",
            preferred_date=now.date(),
            preferred_time_window="10:00-12:00",
            is_emergency=urgent,
        ),
        transcripts=["Rahul Verma, root canal"],
        created_at=now,
        updated_at=now,
    )


def test_lead_row_has_only_automation_columns(config: AppConfig, now: datetime) -> None:
    row = build_lead_row(make_session(now), LeadStatus.NEEDS_MORNING_CALL, config)

    assert len(row) == 8
    assert row[0] == "03/11/2026 23:15:00"
    assert row[1] == PHONE
    assert row[2] == "Rahul Verma"
    assert row[3] == "Root Canal Treatment"
    assert row[4] == "2026-11-03 (10:00-12:00)"
    assert row[5] == "Needs Morning Call"
    assert row[6] == "No"
    assert row[7] == "Rahul Verma, root canal"


def test_urgent_lead_is_marked(config: AppConfig, now: datetime) -> None:
    row = build_lead_row(make_session(now, urgent=True), LeadStatus.URGENT, config)
    assert row[5] == "URGENT"
    assert row[6] == "Yes"


def test_row_number_is_parsed_from_append_response() -> None:
    result = {"updates": {"updatedRange": "Leads!A42:J42"}}
    assert row_from_append_result(result) == 42
    assert row_from_append_result({"updates": {}}) is None
    assert row_from_append_result("nonsense") is None


def test_sheet_id_lookup() -> None:
    metadata = {"sheets": [{"properties": {"title": "Leads", "sheetId": 7}}]}
    assert sheet_id_from_metadata(metadata, "Leads") == 7
    assert sheet_id_from_metadata(metadata, "Missing") is None
    assert sheet_id_from_metadata({}, "Leads") is None


def test_conditional_formatting_payload() -> None:
    requests = conditional_format_requests(7)
    assert len(requests) == 2
    for request in requests:
        rule = request["addConditionalFormatRule"]["rule"]
        assert rule["ranges"][0]["sheetId"] == 7
        assert rule["ranges"][0]["startRowIndex"] == 1
    values = [
        request["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0][
            "userEnteredValue"
        ]
        for request in requests
    ]
    assert values == ["URGENT", "Completed"]


def test_logging_fallback_returns_no_row(config: AppConfig) -> None:
    client = LoggingSheetsClient()
    session = make_session(datetime.now(ZoneInfo("Asia/Kolkata")))
    assert client.upsert_lead(session, LeadStatus.NO_REPLY) is None


def test_sheets_client_selection(config: AppConfig, now: datetime) -> None:
    from app.config import Settings

    settings = Settings(sheet_id="")
    assert isinstance(build_sheets_client(config, settings), LoggingSheetsClient)

    settings_with_sheet = Settings(sheet_id="sheet-123", groq_api_key="")
    client = build_sheets_client(config, settings_with_sheet)
    assert isinstance(client, LoggingSheetsClient) is False


def test_web_channel_records_and_drains() -> None:
    channel = WebChannel()
    channel.send_text(PHONE, "hello")
    channel.send_buttons(PHONE, "confirm?", ["Confirm", "Edit"])

    messages = channel.drain(PHONE)
    assert [message.text for message in messages] == ["hello", "confirm?"]
    assert messages[1].buttons == ["Confirm", "Edit"]
    assert channel.drain(PHONE) == []


def test_headers_cover_sheet_columns() -> None:
    assert len(LEAD_HEADERS) == 10
    assert LEAD_HEADERS[5] == "Status"
    assert LEAD_HEADERS[8] == "Staff Checkbox"


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeApiError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"API error {status_code}")
        self.response = FakeResponse(status_code)


def test_sheet_retries_rate_limit_then_succeeds() -> None:
    calls = {"count": 0}
    delays: list[float] = []

    def operation() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise FakeApiError(429)
        return "ok"

    assert call_with_retries(operation, sleep=delays.append) == "ok"
    assert calls["count"] == 2
    assert delays == [0.5]


def test_sheet_non_retryable_error_is_raised_immediately() -> None:
    calls = {"count": 0}

    def operation() -> str:
        calls["count"] += 1
        raise FakeApiError(403)

    with pytest.raises(FakeApiError):
        call_with_retries(operation, sleep=lambda _: None)
    assert calls["count"] == 1


def test_sheet_retries_stop_after_attempts() -> None:
    calls = {"count": 0}
    delays: list[float] = []

    def operation() -> str:
        calls["count"] += 1
        raise FakeApiError(503)

    with pytest.raises(FakeApiError):
        call_with_retries(operation, attempts=3, sleep=delays.append)
    assert calls["count"] == 3
    assert delays == [0.5, 1.0]


def test_logging_client_clear_transcript_is_a_noop(config: AppConfig, now: datetime) -> None:
    client = LoggingSheetsClient()
    session = make_session(now)
    assert client.clear_transcript(session) is None
