"""State machine tests driven by a stub parser (no Groq calls)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.config import AppConfig
from app.engine import (
    confirmation_intent,
    handle_inbound,
    mark_no_reply,
    should_mark_no_reply,
    start_missed_call,
)
from app.models import ExtractedFields, InboundMessage, InputType, LeadStatus, SessionState

PHONE = "+919820012345"


def make_message(text: str, now: datetime, message_id: str = "msg-1") -> InboundMessage:
    return InboundMessage(
        user_id=PHONE,
        message_id=message_id,
        type=InputType.TEXT,
        text=text,
        timestamp=now,
    )


def full_fields() -> ExtractedFields:
    return ExtractedFields(
        patient_name="Rahul Verma",
        service_needed="root canal",
        preferred_date=date(2026, 11, 4),
        preferred_time_window="morning",
    )


def test_happy_path_missed_call_to_confirmation(config: AppConfig, now: datetime) -> None:
    started = start_missed_call(PHONE, config, now)
    assert started.session.state == SessionState.OUTREACH_SENT
    assert started.status == LeadStatus.NO_REPLY
    assert "Smile Care Dental Clinic" in started.reply

    first = handle_inbound(
        started.session,
        make_message("Hi, I'm Rahul Verma. I need a root canal on 4 Nov in the morning.", now),
        config,
        now,
        extracted=full_fields(),
    )
    assert first.session.state == SessionState.AWAIT_CONFIRM
    assert first.buttons == ["Confirm", "Edit"]
    assert "Rahul Verma" in first.reply
    assert "Root Canal Treatment" in first.reply
    assert "04 Nov 2026" in first.reply

    confirmed = handle_inbound(first.session, make_message("yes", now, "msg-2"), config, now)
    assert confirmed.session.state == SessionState.CONFIRMED
    assert confirmed.status == LeadStatus.NEEDS_MORNING_CALL
    assert "call you to confirm" in confirmed.reply


def test_details_accumulate_across_messages(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session

    first = handle_inbound(
        session,
        make_message("Rahul Verma", now, "msg-1"),
        config,
        now,
        extracted=ExtractedFields(patient_name="Rahul Verma"),
    )
    assert first.session.state == SessionState.COLLECTING
    assert first.session.followup_rounds == 1
    assert "service" in first.reply.lower()

    second = handle_inbound(
        first.session,
        make_message("Root canal please", now, "msg-2"),
        config,
        now,
        extracted=ExtractedFields(service_needed="root canal"),
    )
    assert second.session.fields.patient_name == "Rahul Verma"
    assert second.session.fields.service_needed == "Root Canal Treatment"
    assert second.session.followup_rounds == 2

    third = handle_inbound(
        second.session,
        make_message("4 Nov, morning", now, "msg-3"),
        config,
        now,
        extracted=ExtractedFields(
            preferred_date=date(2026, 11, 4),
            preferred_time_window="morning",
        ),
    )
    assert third.session.state == SessionState.AWAIT_CONFIRM
    assert third.session.followup_rounds == 2


def test_three_followups_then_incomplete(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session

    for index in range(3):
        result = handle_inbound(
            session, make_message("hello", now, f"msg-{index}"), config, now
        )
        assert result.session.state == SessionState.COLLECTING
        session = result.session

    assert session.followup_rounds == 3

    final = handle_inbound(session, make_message("hello again", now, "msg-4"), config, now)
    assert final.session.state == SessionState.INCOMPLETE
    assert final.status == LeadStatus.INCOMPLETE
    assert "saved" in final.reply.lower()


def test_emergency_keyword_flags_urgent(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session
    result = handle_inbound(
        session,
        make_message("I have severe pain and bleeding", now),
        config,
        now,
    )
    assert result.session.state == SessionState.EMERGENCY
    assert result.status == LeadStatus.URGENT
    assert result.alert is not None
    assert "+91 98200 12345" in result.reply
    assert "Holy Family Hospital" in result.reply


def test_emergency_status_survives_confirmation(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session
    flagged = handle_inbound(
        session, make_message("मुझे बहुत दर्द है", now, "msg-1"), config, now
    )
    assert flagged.status == LeadStatus.URGENT

    completed = handle_inbound(
        flagged.session,
        make_message("Rahul Verma, root canal, 4 Nov morning", now, "msg-2"),
        config,
        now,
        extracted=full_fields(),
    )
    assert completed.session.state == SessionState.AWAIT_CONFIRM
    assert completed.status == LeadStatus.URGENT

    confirmed = handle_inbound(
        completed.session, make_message("haan", now, "msg-3"), config, now
    )
    assert confirmed.session.state == SessionState.CONFIRMED
    assert confirmed.status == LeadStatus.URGENT


def test_parse_error_goes_to_manual_review(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session
    result = handle_inbound(
        session,
        make_message("unparseable noise", now),
        config,
        now,
        parse_error="invalid JSON from parser",
    )
    assert result.session.state == SessionState.MANUAL_REVIEW
    assert result.status == LeadStatus.MANUAL_REVIEW
    assert result.alert is not None
    assert result.error == "invalid JSON from parser"


def test_unsupported_input_gets_one_prompt(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session
    sticker = InboundMessage(
        user_id=PHONE,
        message_id="msg-1",
        type=InputType.OTHER,
        timestamp=now,
    )
    result = handle_inbound(session, sticker, config, now)
    assert result.session.state == SessionState.OUTREACH_SENT
    assert "text or a voice note" in result.reply


def test_correction_updates_summary_without_spending_a_round(
    config: AppConfig, now: datetime
) -> None:
    session = start_missed_call(PHONE, config, now).session
    confirmed_view = handle_inbound(
        session,
        make_message("Rahul Verma, root canal, 4 Nov morning", now, "msg-1"),
        config,
        now,
        extracted=full_fields(),
    )
    assert confirmed_view.session.state == SessionState.AWAIT_CONFIRM

    corrected = handle_inbound(
        confirmed_view.session,
        make_message("Actually make it 5 Nov in the afternoon", now, "msg-2"),
        config,
        now,
        extracted=ExtractedFields(
            preferred_date=date(2026, 11, 5),
            preferred_time_window="afternoon",
        ),
    )
    assert corrected.session.state == SessionState.AWAIT_CONFIRM
    assert "05 Nov 2026" in corrected.reply
    assert "12:00-14:00" in corrected.reply
    assert corrected.session.followup_rounds == confirmed_view.session.followup_rounds


def test_edit_request_returns_to_collecting(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session
    confirmed_view = handle_inbound(
        session,
        make_message("Rahul Verma, root canal, 4 Nov morning", now, "msg-1"),
        config,
        now,
        extracted=full_fields(),
    )
    result = handle_inbound(
        confirmed_view.session, make_message("edit", now, "msg-2"), config, now
    )
    assert result.session.state == SessionState.COLLECTING
    assert "change" in result.reply.lower()


def test_closed_day_date_is_rejected(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session
    result = handle_inbound(
        session,
        make_message("Rahul Verma, cleaning, 8 Nov morning", now),
        config,
        now,
        extracted=ExtractedFields(
            patient_name="Rahul Verma",
            service_needed="cleaning",
            preferred_date=date(2026, 11, 8),  # Sunday
            preferred_time_window="morning",
        ),
    )
    assert result.session.state == SessionState.COLLECTING
    assert "Sunday" in result.reply


def test_no_reply_sweep(config: AppConfig, now: datetime) -> None:
    session = start_missed_call(PHONE, config, now).session
    assert not should_mark_no_reply(session, now + timedelta(hours=1), config)
    assert should_mark_no_reply(session, now + timedelta(hours=3), config)

    swept = mark_no_reply(session, now + timedelta(hours=3), config)
    assert swept is not None
    assert swept.state == SessionState.NO_REPLY

    confirmed = session.model_copy(deep=True)
    confirmed.state = SessionState.CONFIRMED
    assert not should_mark_no_reply(confirmed, now + timedelta(hours=3), config)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", "confirm"),
        ("YES!", "confirm"),
        ("haan", "confirm"),
        ("sahi hai", "confirm"),
        ("no changes", "confirm"),
        ("no", "edit"),
        ("nahi", "edit"),
        ("edit", "edit"),
        ("बदलें", "edit"),
        ("maybe later", "other"),
        ("", "other"),
    ],
)
def test_confirmation_intent(text: str, expected: str) -> None:
    assert confirmation_intent(text) == expected
