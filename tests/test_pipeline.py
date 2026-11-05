"""Pipeline tests with fake parser/transcriber (no Groq calls)."""

from __future__ import annotations

from datetime import date, datetime

from app.config import AppConfig, Settings
from app.engine import start_missed_call
from app.groq import GroqError
from app.models import (
    ExtractedFields,
    InboundMessage,
    InputType,
    LeadStatus,
    SessionState,
)
from app.parser import ParserError
from app.pipeline import ConversationPipeline
from app.stt import TranscriptionError

PHONE = "+919820012345"


class FakeParser:
    def __init__(self, fields: ExtractedFields | None = None, error: str | None = None) -> None:
        self.fields = fields or ExtractedFields()
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


def full_fields() -> ExtractedFields:
    return ExtractedFields(
        patient_name="Rahul Verma",
        service_needed="root canal",
        preferred_date=date(2026, 11, 4),
        preferred_time_window="morning",
    )


def text_message(text: str, now: datetime) -> InboundMessage:
    return InboundMessage(
        user_id=PHONE,
        message_id="msg-1",
        type=InputType.TEXT,
        text=text,
        timestamp=now,
    )


def audio_message(now: datetime) -> InboundMessage:
    return InboundMessage(
        user_id=PHONE,
        message_id="msg-1",
        type=InputType.AUDIO,
        audio_bytes=b"fake-audio",
        timestamp=now,
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
        parser=parser or FakeParser(),
        transcriber=transcriber or FakeTranscriber(),
    )


def test_text_message_reaches_confirmation(config: AppConfig, now: datetime) -> None:
    pipeline = make_pipeline(config, parser=FakeParser(full_fields()))
    session = start_missed_call(PHONE, config, now).session

    result = pipeline.handle(
        session,
        text_message("Rahul Verma, root canal, 4 Nov morning", now),
        now,
    )

    assert result.session.state == SessionState.AWAIT_CONFIRM
    assert result.status == LeadStatus.NO_REPLY
    assert result.buttons == ["Confirm", "Edit"]


def test_audio_is_transcribed_and_tagged_in_transcript(
    config: AppConfig, now: datetime
) -> None:
    transcriber = FakeTranscriber("Rahul Verma, root canal, 4 Nov morning")
    pipeline = make_pipeline(
        config,
        parser=FakeParser(full_fields()),
        transcriber=transcriber,
    )
    session = start_missed_call(PHONE, config, now).session

    result = pipeline.handle(session, audio_message(now), now)

    assert result.session.state == SessionState.AWAIT_CONFIRM
    assert result.session.transcripts[0].startswith("[voice] Rahul Verma")
    assert transcriber.media_types == [None]


def test_parser_failure_goes_to_manual_review(config: AppConfig, now: datetime) -> None:
    pipeline = make_pipeline(config, parser=FakeParser(error="invalid JSON"))
    session = start_missed_call(PHONE, config, now).session

    result = pipeline.handle(session, text_message("gibberish", now), now)

    assert result.session.state == SessionState.MANUAL_REVIEW
    assert result.status == LeadStatus.MANUAL_REVIEW
    assert result.alert is not None
    assert "parser failed" in result.alert


def test_unavailable_groq_goes_to_manual_review(
    config: AppConfig, now: datetime
) -> None:
    pipeline = make_pipeline(config, parser=UnavailableParser())
    session = start_missed_call(PHONE, config, now).session

    result = pipeline.handle(session, text_message("Rahul Verma", now), now)

    assert result.session.state == SessionState.MANUAL_REVIEW
    assert result.status == LeadStatus.MANUAL_REVIEW
    assert result.alert is not None
    assert "GROQ_API_KEY is not set" in result.alert


def test_keyword_emergency_skips_parser(config: AppConfig, now: datetime) -> None:
    parser = FakeParser(error="should not be called")
    pipeline = make_pipeline(config, parser=parser)
    session = start_missed_call(PHONE, config, now).session

    result = pipeline.handle(session, text_message("I have severe bleeding", now), now)

    assert parser.calls == 0
    assert result.session.state == SessionState.EMERGENCY
    assert result.status == LeadStatus.URGENT
    assert result.alert is not None
    assert result.session.fields.is_emergency is True


def test_transcription_failure_goes_to_manual_review(
    config: AppConfig, now: datetime
) -> None:
    pipeline = make_pipeline(
        config, transcriber=FakeTranscriber(error="audio rejected")
    )
    session = start_missed_call(PHONE, config, now).session

    result = pipeline.handle(session, audio_message(now), now)

    assert result.session.state == SessionState.MANUAL_REVIEW
    assert result.status == LeadStatus.MANUAL_REVIEW
    assert result.alert is not None
    assert "transcription failed" in result.alert


def test_empty_transcript_asks_for_text_or_voice(config: AppConfig, now: datetime) -> None:
    pipeline = make_pipeline(config, transcriber=FakeTranscriber(""))
    session = start_missed_call(PHONE, config, now).session

    result = pipeline.handle(session, audio_message(now), now)

    assert result.session.state == SessionState.OUTREACH_SENT
    assert "text or a voice note" in result.reply


def test_other_input_skips_parser_and_asks_for_text(config: AppConfig, now: datetime) -> None:
    parser = FakeParser(error="should not be called")
    pipeline = make_pipeline(config, parser=parser)
    session = start_missed_call(PHONE, config, now).session
    sticker = InboundMessage(
        user_id=PHONE,
        message_id="msg-1",
        type=InputType.OTHER,
        timestamp=now,
    )

    result = pipeline.handle(session, sticker, now)

    assert result.session.state == SessionState.OUTREACH_SENT
    assert "text or a voice note" in result.reply
