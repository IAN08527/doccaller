"""HTTP endpoint tests with dependency overrides (no Groq/Sheets credentials)."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, Settings
from app.db import connect, get_session
from app.groq import GroqError
from app.main import (
    app,
    config_dependency,
    pipeline_dependency,
    sheets_dependency,
)
from app.models import ExtractedFields, LeadStatus, Session, SessionState
from app.parser import ParserError
from app.pipeline import ConversationPipeline
from app.sheets import SheetError
from app.stt import TranscriptionError

PHONE_RAW = "9820012345"
PHONE_E164 = "+919820012345"


class FakeParser:
    def __init__(self, fields: ExtractedFields | None = None, error: str | None = None) -> None:
        self.fields = fields if fields is not None else ExtractedFields()
        self.error = error
        self.calls = 0

    def parse(self, text: str, current: ExtractedFields, now: datetime) -> ExtractedFields:
        self.calls += 1
        if self.error is not None:
            raise ParserError(self.error)
        return self.fields


class UnavailableParser:
    def parse(self, text: str, current: ExtractedFields, now: datetime) -> ExtractedFields:
        raise GroqError("GROQ_API_KEY is not set")


class FakeTranscriber:
    def __init__(self, text: str = "", error: str | None = None) -> None:
        self.text = text
        self.error = error
        self.media_types: list[str | None] = []

    def transcribe(self, audio_bytes: bytes, *, media_type: str | None = None) -> str:
        self.media_types.append(media_type)
        if self.error is not None:
            raise TranscriptionError(self.error)
        return self.text


@dataclass
class FakeSheets:
    """Records syncs and reuses one row per phone, like the real upsert."""

    fail: bool = False
    unconfigured: bool = False
    rows: dict[str, int] = field(default_factory=dict)
    calls: list[tuple[str, LeadStatus]] = field(default_factory=list)

    def upsert_lead(self, session: Session, status: LeadStatus) -> int | None:
        if self.fail:
            raise SheetError("sheets unavailable")
        if self.unconfigured:
            return None
        self.calls.append((session.phone, status))
        if session.phone not in self.rows:
            self.rows[session.phone] = len(self.rows) + 1
        return self.rows[session.phone]


def full_fields() -> ExtractedFields:
    return ExtractedFields(
        patient_name="Rahul Verma",
        service_needed="root canal",
        preferred_date=date(2026, 11, 4),
        preferred_time_window="morning",
    )


def make_pipeline(
    config: AppConfig,
    *,
    parser: FakeParser | None = None,
    transcriber: FakeTranscriber | None = None,
) -> ConversationPipeline:
    return ConversationPipeline(
        config,
        Settings(groq_api_key="test-key"),
        parser=parser or FakeParser(full_fields()),
        transcriber=transcriber
        or FakeTranscriber("Rahul Verma, root canal, 4 Nov morning"),
    )


@dataclass
class ApiHarness:
    client: TestClient
    db_path: Path
    config: AppConfig
    sheets: FakeSheets
    pipeline: ConversationPipeline

    def set_config(self, config: AppConfig) -> None:
        self.config = config

    def set_sheets(self, sheets: FakeSheets) -> None:
        self.sheets = sheets

    def set_pipeline(self, pipeline: ConversationPipeline) -> None:
        self.pipeline = pipeline

    def restart(self) -> None:
        """Simulate a process restart while keeping the SQLite file."""
        self.client.__exit__(None, None, None)
        self.client = TestClient(app)
        self.client.__enter__()

    def missed_call(
        self,
        phone: str = PHONE_RAW,
        *,
        timestamp: datetime | None = None,
        secret: str | None = None,
        event: str = "missed_call",
    ) -> httpx.Response:
        headers = {"X-Webhook-Secret": secret} if secret is not None else {}
        body: dict[str, object] = {"event": event, "caller_phone": phone}
        if timestamp is not None:
            body["timestamp"] = timestamp.isoformat()
        return self.client.post("/missed-call", json=body, headers=headers)

    def chat(
        self,
        message_id: str = "msg-1",
        *,
        phone: str = PHONE_RAW,
        text: str | None = None,
        audio: bytes | None = None,
        audio_base64: str | None = None,
        audio_media_type: str | None = None,
        timestamp: datetime | None = None,
    ) -> httpx.Response:
        body: dict[str, object] = {"phone": phone, "message_id": message_id}
        if text is not None:
            body["text"] = text
        if audio is not None:
            body["audio_base64"] = base64.b64encode(audio).decode("ascii")
        if audio_base64 is not None:
            body["audio_base64"] = audio_base64
        if audio_media_type is not None:
            body["audio_media_type"] = audio_media_type
        if timestamp is not None:
            body["timestamp"] = timestamp.isoformat()
        return self.client.post("/chat", json=body)

    def load_session(self, phone: str = PHONE_E164) -> Session | None:
        connection = connect(self.db_path)
        try:
            return get_session(connection, phone)
        finally:
            connection.close()


@pytest.fixture()
def api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: AppConfig,
) -> Iterator[ApiHarness]:
    db_path = tmp_path / "api.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("JOBS_ENABLED", "false")
    monkeypatch.delenv("SHEET_ID", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("MISSED_CALL_SHARED_SECRET", raising=False)

    client = TestClient(app)
    harness = ApiHarness(
        client=client,
        db_path=db_path,
        config=config,
        sheets=FakeSheets(),
        pipeline=make_pipeline(config),
    )

    app.dependency_overrides.clear()
    app.dependency_overrides[config_dependency] = lambda: harness.config
    app.dependency_overrides[sheets_dependency] = lambda: harness.sheets
    app.dependency_overrides[pipeline_dependency] = lambda: harness.pipeline

    client.__enter__()
    try:
        yield harness
    finally:
        harness.client.__exit__(None, None, None)
        app.dependency_overrides.clear()

def test_health_reports_configuration_state(api: ApiHarness) -> None:
    response = api.client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["clinic"] == "Smile Care Dental Clinic"
    assert body["db"] == "ok"
    assert body["sheets"] == "not_configured"
    assert body["groq_key"] == "not_configured"
    assert body["parser_model"] == "openai/gpt-oss-20b"


def test_missed_call_creates_session_and_outreach(
    api: ApiHarness, now: datetime
) -> None:
    response = api.missed_call(phone="98200 12345", timestamp=now)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "outreach_sent"
    assert body["sheet_sync"] == "ok"
    assert body["sheet_row"] == 1
    assert "Smile Care Dental Clinic" in body["reply"]
    assert api.sheets.calls == [(PHONE_E164, LeadStatus.NO_REPLY)]

    session = api.load_session()
    assert session is not None
    assert session.state == SessionState.OUTREACH_SENT
    assert session.last_outreach_at == now
    assert session.sheet_row == 1


@pytest.mark.parametrize("caller", ["withheld", "anonymous", "12345", ""])
def test_missed_call_ignores_invalid_or_withheld_numbers(
    api: ApiHarness, now: datetime, caller: str
) -> None:
    response = api.missed_call(phone=caller, timestamp=now)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "reason": "invalid_or_withheld_number",
        "reply": None,
        "sheet_row": None,
        "sheet_sync": "unchanged",
    }
    assert api.sheets.calls == []
    assert api.load_session() is None


def test_second_missed_call_inside_dedupe_window_is_duplicate(
    api: ApiHarness, now: datetime
) -> None:
    first = api.missed_call(timestamp=now)
    second = api.missed_call(timestamp=now + timedelta(hours=1))

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["sheet_row"] == 1
    assert len(api.sheets.calls) == 1


def test_daily_cap_blocks_further_outreach(api: ApiHarness, now: datetime) -> None:
    api.set_config(api.config.model_copy(update={"daily_outreach_cap": 1}))

    first = api.missed_call(phone="9820011111", timestamp=now)
    second = api.missed_call(phone="9820022222", timestamp=now)

    assert first.json()["status"] == "outreach_sent"
    assert second.json()["status"] == "capped"
    assert second.json()["reason"] == "daily_outreach_cap"
    assert api.load_session("+919820022222") is None


def test_shared_secret_is_enforced_when_configured(
    api: ApiHarness, now: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISSED_CALL_SHARED_SECRET", "s3cr3t")

    rejected = api.missed_call(timestamp=now)
    accepted = api.missed_call(timestamp=now, secret="s3cr3t")

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "outreach_sent"
    assert len(api.sheets.calls) == 1


def test_unknown_event_is_rejected(api: ApiHarness, now: datetime) -> None:
    response = api.missed_call(timestamp=now, event="call_ended")
    assert response.status_code == 422


def test_chat_happy_path_confirms_and_reuses_sheet_row(
    api: ApiHarness, now: datetime
) -> None:
    missed = api.missed_call(timestamp=now)
    assert missed.json()["sheet_row"] == 1

    collecting = api.chat(
        "msg-1",
        text="Hi, I'm Rahul Verma. I need a root canal on 4 Nov in the morning.",
        timestamp=now + timedelta(minutes=2),
    )

    assert collecting.status_code == 200
    body = collecting.json()
    assert body["status"] == "ok"
    assert body["state"] == SessionState.AWAIT_CONFIRM
    assert body["lead_status"] == LeadStatus.NO_REPLY
    assert body["buttons"] == ["Confirm", "Edit"]
    assert body["sheet_row"] == 1
    assert body["sheet_sync"] == "ok"
    assert "Rahul Verma" in body["reply"]

    confirmed = api.chat("msg-2", text="yes", timestamp=now + timedelta(minutes=3))

    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert confirmed_body["state"] == SessionState.CONFIRMED
    assert confirmed_body["lead_status"] == LeadStatus.NEEDS_MORNING_CALL
    assert confirmed_body["sheet_row"] == 1
    assert "call you to confirm" in confirmed_body["reply"]

    session = api.load_session()
    assert session is not None
    assert session.transcripts == [
        "Hi, I'm Rahul Verma. I need a root canal on 4 Nov in the morning.",
        "yes",
    ]
    assert api.sheets.rows == {PHONE_E164: 1}
    assert [status for _, status in api.sheets.calls] == [
        LeadStatus.NO_REPLY,
        LeadStatus.NO_REPLY,
        LeadStatus.NEEDS_MORNING_CALL,
    ]


def test_duplicate_chat_message_id_is_ignored(api: ApiHarness, now: datetime) -> None:
    api.missed_call(timestamp=now)
    text = "Rahul Verma, root canal, 4 Nov morning"

    first = api.chat("msg-1", text=text, timestamp=now)
    duplicate = api.chat("msg-1", text=text, timestamp=now)

    assert first.json()["state"] == SessionState.AWAIT_CONFIRM
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["state"] == SessionState.AWAIT_CONFIRM
    assert duplicate.json()["lead_status"] == LeadStatus.NO_REPLY
    assert len(api.sheets.calls) == 2  # missed call + first chat only
    session = api.load_session()
    assert session is not None
    assert session.transcripts == [text]


def test_chat_without_session_returns_404(api: ApiHarness, now: datetime) -> None:
    response = api.chat("msg-1", text="hello", timestamp=now)
    assert response.status_code == 404


def test_chat_with_invalid_phone_returns_422(api: ApiHarness, now: datetime) -> None:
    response = api.chat("msg-1", phone="private", text="hello", timestamp=now)
    assert response.status_code == 422


def test_chat_without_text_or_audio_returns_422(api: ApiHarness, now: datetime) -> None:
    response = api.chat("msg-1", timestamp=now)
    assert response.status_code == 422
    assert response.json()["detail"] == "provide text or audio_base64"


def test_chat_with_invalid_base64_returns_422(api: ApiHarness, now: datetime) -> None:
    response = api.chat("msg-1", audio_base64="not-base64!!", timestamp=now)
    assert response.status_code == 422
    assert response.json()["detail"] == "audio_base64 is not valid base64"


def test_empty_text_is_treated_as_unsupported(api: ApiHarness, now: datetime) -> None:
    api.missed_call(timestamp=now)

    response = api.chat("msg-1", text="   ", timestamp=now)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == SessionState.OUTREACH_SENT
    assert "text or a voice note" in body["reply"]


def test_audio_message_is_transcribed_and_tagged(api: ApiHarness, now: datetime) -> None:
    api.missed_call(timestamp=now)

    response = api.chat("voice-1", audio=b"fake-audio", timestamp=now)

    assert response.status_code == 200
    assert response.json()["state"] == SessionState.AWAIT_CONFIRM
    session = api.load_session()
    assert session is not None
    assert session.transcripts == ["[voice] Rahul Verma, root canal, 4 Nov morning"]


def test_audio_media_type_reaches_transcriber(api: ApiHarness, now: datetime) -> None:
    transcriber = FakeTranscriber("Rahul Verma, root canal, 4 Nov morning")
    api.set_pipeline(make_pipeline(api.config, transcriber=transcriber))
    api.missed_call(timestamp=now)

    response = api.chat(
        "voice-1",
        audio=b"fake-audio",
        audio_media_type="audio/ogg",
        timestamp=now,
    )

    assert response.status_code == 200
    assert transcriber.media_types == ["audio/ogg"]


def test_session_resumes_after_app_restart(api: ApiHarness, now: datetime) -> None:
    api.missed_call(timestamp=now)
    first = api.chat(
        "msg-1",
        text="Rahul Verma, root canal, 4 Nov morning",
        timestamp=now,
    )
    assert first.json()["state"] == SessionState.AWAIT_CONFIRM

    api.restart()

    second = api.chat("msg-2", text="yes", timestamp=now + timedelta(minutes=1))

    assert second.status_code == 200
    assert second.json()["state"] == SessionState.CONFIRMED
    assert second.json()["lead_status"] == LeadStatus.NEEDS_MORNING_CALL
    assert second.json()["sheet_row"] == 1
    session = api.load_session()
    assert session is not None
    assert session.state == SessionState.CONFIRMED


def test_malformed_timestamp_returns_422(api: ApiHarness) -> None:
    response = api.client.post(
        "/missed-call",
        json={
            "event": "missed_call",
            "caller_phone": PHONE_RAW,
            "timestamp": "yesterday evening",
        },
    )
    assert response.status_code == 422


def test_naive_timestamp_is_read_as_clinic_time(api: ApiHarness) -> None:
    response = api.missed_call(timestamp=datetime(2026, 11, 3, 23, 15))

    assert response.status_code == 200
    session = api.load_session()
    assert session is not None
    assert session.last_outreach_at is not None
    assert session.last_outreach_at.utcoffset() == timedelta(hours=5, minutes=30)


def test_parser_failure_routes_to_manual_review(api: ApiHarness, now: datetime) -> None:
    api.set_pipeline(make_pipeline(api.config, parser=FakeParser(error="invalid JSON")))
    api.missed_call(timestamp=now)

    response = api.chat("msg-1", text="gibberish", timestamp=now)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == SessionState.MANUAL_REVIEW
    assert body["lead_status"] == LeadStatus.MANUAL_REVIEW
    assert body["alert"] is not None
    assert "parser failed" in body["alert"]


def test_unavailable_groq_returns_manual_review(api: ApiHarness, now: datetime) -> None:
    api.set_pipeline(make_pipeline(api.config, parser=UnavailableParser()))
    api.missed_call(timestamp=now)

    response = api.chat("msg-1", text="Rahul Verma, root canal", timestamp=now)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == SessionState.MANUAL_REVIEW
    assert body["lead_status"] == LeadStatus.MANUAL_REVIEW
    assert body["sheet_sync"] == "ok"
    assert body["alert"] is not None
    assert "GROQ_API_KEY is not set" in body["alert"]


def test_keyword_emergency_is_urgent_without_parser(
    api: ApiHarness, now: datetime
) -> None:
    parser = FakeParser(error="should not be called")
    api.set_pipeline(make_pipeline(api.config, parser=parser))
    api.missed_call(timestamp=now)

    response = api.chat("em-1", text="I have severe bleeding", timestamp=now)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == SessionState.EMERGENCY
    assert body["lead_status"] == LeadStatus.URGENT
    assert body["alert"] is not None
    assert parser.calls == 0
    session = api.load_session()
    assert session is not None
    assert session.fields.is_emergency is True
    assert api.sheets.calls[-1][1] == LeadStatus.URGENT


def test_sheet_failure_keeps_lead_and_recovers_on_next_sync(
    api: ApiHarness, now: datetime
) -> None:
    api.set_sheets(FakeSheets(fail=True))

    missed = api.missed_call(timestamp=now)

    assert missed.status_code == 200
    body = missed.json()
    assert body["status"] == "outreach_sent"
    assert body["sheet_sync"] == "pending"
    assert body["sheet_row"] is None
    session = api.load_session()
    assert session is not None
    assert session.state == SessionState.OUTREACH_SENT

    api.set_sheets(FakeSheets())
    chat = api.chat("msg-1", text="Rahul Verma, root canal, 4 Nov morning", timestamp=now)

    assert chat.json()["sheet_sync"] == "ok"
    assert chat.json()["sheet_row"] == 1


def test_unconfigured_sheets_keep_lead_local(api: ApiHarness, now: datetime) -> None:
    api.set_sheets(FakeSheets(unconfigured=True))

    response = api.missed_call(timestamp=now)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "outreach_sent"
    assert body["sheet_sync"] == "unconfigured"
    assert body["sheet_row"] is None
    assert api.load_session() is not None
