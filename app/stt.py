"""Groq Whisper speech-to-text for voice notes."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from app.config import Settings
from app.groq import GroqError, auth_headers, post_with_retries

# Short vocabulary hint improves dental terms and relative-date words.
STT_PROMPT = (
    "dental clinic appointment: scaling, root canal, braces, extraction, crown, "
    "kal, parso, subah, shaam, dard, सूजन, दर्द"
)

# Map inbound media types to the extension Groq should see. Unknown types fall
# back to WebM so a bad client header cannot leak into the multipart filename.
_MEDIA_FORMATS: dict[str, str] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
}


class TranscriptionError(RuntimeError):
    """Raised when an audio note cannot be transcribed."""


class Transcriber:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleep

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        media_type: str | None = None,
        filename: str | None = None,
        language: str | None = None,
    ) -> str:
        """Return the transcript, or an empty string when Whisper hears nothing."""
        if not audio_bytes:
            raise TranscriptionError("empty audio payload")

        resolved_type, resolved_filename = _audio_format(media_type, filename)
        headers = auth_headers(self._settings.groq_api_key)
        data: dict[str, str] = {
            "model": self._settings.groq_stt_model,
            "prompt": STT_PROMPT,
            "response_format": "json",
        }
        if language is not None:
            data["language"] = language

        try:
            with httpx.Client(timeout=60, transport=self._transport) as client:
                response = post_with_retries(
                    client,
                    "/audio/transcriptions",
                    headers=headers,
                    files={"file": (resolved_filename, audio_bytes, resolved_type)},
                    data=data,
                    sleep=self._sleep,
                )
        except GroqError as exc:
            raise TranscriptionError(str(exc)) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise TranscriptionError("invalid transcription response") from exc

        text = payload.get("text")
        if not isinstance(text, str):
            raise TranscriptionError("missing text in transcription response")
        return text.strip()


def _audio_format(media_type: str | None, filename: str | None) -> tuple[str, str]:
    base = (media_type or "audio/webm").split(";", 1)[0].strip().lower()
    extension = _MEDIA_FORMATS.get(base)
    if extension is None:
        return "audio/webm", filename or "voice.webm"
    return base, filename or f"voice.{extension}"


__all__ = ["STT_PROMPT", "Transcriber", "TranscriptionError"]
