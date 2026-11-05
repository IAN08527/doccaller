"""Domain models shared by the engine, persistence, and channel adapters."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SessionState(StrEnum):
    NEW = "NEW"
    OUTREACH_SENT = "OUTREACH_SENT"
    COLLECTING = "COLLECTING"
    AWAIT_CONFIRM = "AWAIT_CONFIRM"
    CONFIRMED = "CONFIRMED"
    INCOMPLETE = "INCOMPLETE"
    EMERGENCY = "EMERGENCY"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NO_REPLY = "NO_REPLY"


class LeadStatus(StrEnum):
    NO_REPLY = "No Reply"
    INCOMPLETE = "Incomplete - Call Back"
    NEEDS_MORNING_CALL = "Needs Morning Call"
    URGENT = "URGENT"
    MANUAL_REVIEW = "Needs Manual Review"
    COMPLETED = "Completed"


class InputType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    OTHER = "other"


class InboundMessage(BaseModel):
    """Normalised inbound message produced by a channel adapter."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    message_id: str
    type: InputType
    text: str | None = None
    audio_bytes: bytes | None = None
    audio_media_type: str | None = None
    timestamp: datetime


class ExtractedFields(BaseModel):
    """What the LLM parser may fill in. Everything else is decided in code."""

    patient_name: str | None = None
    service_needed: str | None = None
    preferred_date: date | None = None
    preferred_time_window: str | None = None
    is_emergency: bool = False


class Session(BaseModel):
    phone: str
    state: SessionState = SessionState.NEW
    fields: ExtractedFields = Field(default_factory=ExtractedFields)
    followup_rounds: int = 0
    sheet_row: int | None = None
    last_message_at: datetime | None = None
    transcripts: list[str] = Field(default_factory=list)
    last_outreach_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LeadRow(BaseModel):
    """One row in the staff Google Sheet (columns A-H are automation-owned)."""

    timestamp: datetime
    caller_phone: str
    patient_name: str | None = None
    service_needed: str | None = None
    requested_slot: str | None = None
    status: LeadStatus = LeadStatus.NO_REPLY
    urgent: bool = False
    raw_transcript: str = ""
