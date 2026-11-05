"""Request/response schemas for the HTTP API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MissedCallRequest(BaseModel):
    event: str = "missed_call"
    caller_phone: str
    timestamp: datetime | None = None


class MissedCallResponse(BaseModel):
    status: str
    reason: str | None = None
    reply: str | None = None
    sheet_row: int | None = None
    sheet_sync: str = "unchanged"


class ChatRequest(BaseModel):
    phone: str
    message_id: str
    text: str | None = None
    audio_base64: str | None = None
    audio_media_type: str = Field(default="audio/webm", max_length=100)
    timestamp: datetime | None = None


class ChatResponse(BaseModel):
    status: str = "ok"
    reply: str | None = None
    buttons: list[str] = Field(default_factory=list)
    state: str
    lead_status: str
    alert: str | None = None
    sheet_row: int | None = None
    sheet_sync: str = "unchanged"
