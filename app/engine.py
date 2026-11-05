"""Conversation state machine: deterministic branching around LLM extraction."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app import emergency
from app.config import AppConfig
from app.models import (
    ExtractedFields,
    InboundMessage,
    InputType,
    LeadStatus,
    Session,
    SessionState,
)
from app.reply_builder import Lang, ReplyBuilder, detect_language
from app.validator import FIELD_ORDER, ValidationResult, validate_fields

_CONFIRM_EXACT = {
    "yes",
    "yeah",
    "yep",
    "y",
    "ok",
    "okay",
    "confirm",
    "confirmed",
    "sure",
    "correct",
    "haan",
    "han",
    "ha",
    "sahi hai",
    "sahi",
    "theek hai",
    "thik hai",
    "हाँ",
    "हां",
    "ठीक है",
    "होय",
    "बरं",
    "no changes",
    "no change",
}
_CONFIRM_PREFIX = {
    "yes",
    "yeah",
    "yep",
    "ok",
    "okay",
    "confirm",
    "haan",
    "sahi",
    "theek",
    "thik",
    "हाँ",
    "हां",
    "होय",
    "बरं",
}
_EDIT_EXACT = {
    "edit",
    "change",
    "no",
    "nahi",
    "नहीं",
    "बदल",
    "बदलें",
    "बदला",
    "wrong",
    "not correct",
}
_EDIT_PREFIX = {"edit", "change", "no", "nahi", "नहीं", "बदल", "बदलें", "बदला"}


@dataclass(slots=True)
class EngineResult:
    session: Session
    reply: str
    buttons: list[str] = field(default_factory=list)
    status: LeadStatus = LeadStatus.NO_REPLY
    alert: str | None = None
    error: str | None = None


def start_missed_call(phone: str, config: AppConfig, now: datetime) -> EngineResult:
    """Create the session and the outreach message for a missed call."""
    session = Session(
        phone=phone,
        state=SessionState.OUTREACH_SENT,
        last_outreach_at=now,
        created_at=now,
        updated_at=now,
    )
    builder = ReplyBuilder(config)
    return EngineResult(session=session, reply=builder.outreach("en"), status=LeadStatus.NO_REPLY)


def handle_inbound(
    session: Session,
    message: InboundMessage,
    config: AppConfig,
    now: datetime,
    extracted: ExtractedFields | None = None,
    parse_error: str | None = None,
) -> EngineResult:
    """Advance the conversation by one inbound message.

    ``extracted`` is the parser output (a stub in Phase 1, Groq in Phase 2).
    ``parse_error`` marks a pipeline failure and forces manual review.
    """
    current = session.model_copy(deep=True)
    current.last_message_at = now
    current.updated_at = now

    text = (message.text or "").strip()
    if text:
        entry = f"[voice] {text}" if message.type == InputType.AUDIO else text
        current.transcripts.append(entry)

    builder = ReplyBuilder(config)
    lang: Lang = detect_language(text) if text else "en"

    if parse_error is not None:
        current.state = SessionState.MANUAL_REVIEW
        return EngineResult(
            session=current,
            reply=builder.error(lang),
            status=LeadStatus.MANUAL_REVIEW,
            alert=f"Pipeline failure for {current.phone}: {parse_error}",
            error=parse_error,
        )

    if message.type == InputType.OTHER or (message.type == InputType.AUDIO and not text):
        return EngineResult(
            session=current,
            reply=builder.unsupported(lang),
            status=status_for(current),
        )

    new_fields = extracted or ExtractedFields()
    prev_validation = validate_fields(session.fields, config, today=now.date())

    emergency_detected = emergency.is_emergency(text) or new_fields.is_emergency
    if emergency_detected and not current.fields.is_emergency:
        current.fields = merge_fields(current.fields, new_fields)
        current.fields.is_emergency = True
        current.state = SessionState.EMERGENCY
        return EngineResult(
            session=current,
            reply=builder.emergency(lang),
            status=LeadStatus.URGENT,
            alert=f"URGENT lead: {current.phone}",
        )

    current.fields = merge_fields(current.fields, new_fields)

    if current.state == SessionState.AWAIT_CONFIRM:
        intent = confirmation_intent(text)
        if intent == "confirm":
            current.state = SessionState.CONFIRMED
            return EngineResult(
                session=current,
                reply=builder.final_ack(lang),
                status=status_for(current),
            )
        if intent == "edit":
            current.state = SessionState.COLLECTING
            current.followup_rounds = 0
            return EngineResult(
                session=current,
                reply=builder.edit_prompt(lang),
                status=status_for(current),
            )
        # Anything else is treated as a correction: the caller-supplied extraction
        # has already been merged, so re-validate and re-confirm.

    validation = validate_fields(current.fields, config, today=now.date())
    current.fields = validation.normalized

    if validation.is_valid:
        current.state = SessionState.AWAIT_CONFIRM
        current.followup_rounds = 0
        return EngineResult(
            session=current,
            reply=builder.confirmation(validation.normalized, lang),
            buttons=["Confirm", "Edit"],
            status=status_for(current),
        )

    if current.state == SessionState.AWAIT_CONFIRM:
        # A correction left the request incomplete; ask again without spending a round.
        current.state = SessionState.COLLECTING
        current.followup_rounds = 0
        return EngineResult(
            session=current,
            reply=builder.ask_missing(validation, lang),
            status=status_for(current),
        )

    if _has_field_progress(prev_validation, validation, session.fields, current.fields):
        current.followup_rounds = 0
    else:
        if current.followup_rounds >= config.followup.max_rounds:
            current.state = SessionState.INCOMPLETE
            return EngineResult(
                session=current,
                reply=builder.incomplete(lang),
                status=status_for(current),
            )
        current.followup_rounds += 1

    current.state = SessionState.COLLECTING
    return EngineResult(
        session=current,
        reply=builder.ask_missing(validation, lang),
        status=status_for(current),
    )


def _has_field_progress(
    prev_validation: ValidationResult,
    curr_validation: ValidationResult,
    prev_fields: ExtractedFields,
    curr_fields: ExtractedFields,
) -> bool:
    """Return True if newly valid or updated information was provided in this round."""
    for field_name in FIELD_ORDER:
        was_invalid_or_missing = (
            field_name in prev_validation.missing or field_name in prev_validation.errors
        )
        is_now_valid = (
            field_name not in curr_validation.missing and field_name not in curr_validation.errors
        )
        if was_invalid_or_missing and is_now_valid:
            return True

        if is_now_valid and not was_invalid_or_missing:
            prev_val = getattr(prev_fields, field_name)
            curr_val = getattr(curr_fields, field_name)
            if curr_val is not None and curr_val != prev_val:
                return True

    return False


def merge_fields(existing: ExtractedFields, new: ExtractedFields) -> ExtractedFields:
    """Non-null values overwrite; old values are never dropped."""
    merged = existing.model_copy(deep=True)
    if new.patient_name is not None:
        merged.patient_name = new.patient_name
    if new.service_needed is not None:
        merged.service_needed = new.service_needed
    if new.preferred_date is not None:
        merged.preferred_date = new.preferred_date
    if new.preferred_time_window is not None:
        merged.preferred_time_window = new.preferred_time_window
    merged.is_emergency = existing.is_emergency or new.is_emergency
    return merged


def confirmation_intent(text: str) -> str:
    normalized = _normalize_intent(text)
    if not normalized:
        return "other"
    if normalized in _CONFIRM_EXACT:
        return "confirm"
    if normalized in _EDIT_EXACT:
        return "edit"
    first = normalized.split(" ", 1)[0]
    if first in _EDIT_PREFIX:
        return "edit"
    if first in _CONFIRM_PREFIX:
        return "confirm"
    return "other"


def should_mark_no_reply(session: Session, now: datetime, config: AppConfig) -> bool:
    if session.state not in (SessionState.OUTREACH_SENT, SessionState.COLLECTING):
        return False
    reference = session.last_message_at or session.last_outreach_at
    if reference is None:
        return False
    return now - reference >= timedelta(hours=config.no_reply_sweep_hours)


def mark_no_reply(session: Session, now: datetime, config: AppConfig) -> Session | None:
    """Return an updated copy when the 2-hour sweep applies, otherwise None."""
    if not should_mark_no_reply(session, now, config):
        return None
    updated = session.model_copy(deep=True)
    updated.state = SessionState.NO_REPLY
    updated.updated_at = now
    return updated


def status_for(session: Session) -> LeadStatus:
    if session.state == SessionState.MANUAL_REVIEW:
        return LeadStatus.MANUAL_REVIEW
    if session.state == SessionState.EMERGENCY or session.fields.is_emergency:
        return LeadStatus.URGENT
    if session.state == SessionState.INCOMPLETE:
        return LeadStatus.INCOMPLETE
    if session.state == SessionState.CONFIRMED:
        return LeadStatus.NEEDS_MORNING_CALL
    return LeadStatus.NO_REPLY


def _normalize_intent(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower().strip()
    normalized = re.sub(r"[^\w\s\u0900-\u097F]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()
