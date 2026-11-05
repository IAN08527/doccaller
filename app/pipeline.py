"""Compose STT + parser + engine for one inbound message."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from app.config import AppConfig, Settings
from app.emergency import is_emergency
from app.engine import EngineResult, handle_inbound
from app.groq import GroqError
from app.models import ExtractedFields, InboundMessage, InputType, Session
from app.parser import GroqParser, ParserError
from app.stt import Transcriber, TranscriptionError

logger = logging.getLogger("doccaller.pipeline")


class TextParser(Protocol):
    def parse(self, text: str, current: ExtractedFields, now: datetime) -> ExtractedFields: ...


class SpeechTranscriber(Protocol):
    def transcribe(
        self, audio_bytes: bytes, *, media_type: str | None = None
    ) -> str: ...


class ConversationPipeline:
    """Runs transcription (voice only) then extraction, then deterministic logic."""

    def __init__(
        self,
        config: AppConfig,
        settings: Settings,
        *,
        parser: TextParser | None = None,
        transcriber: SpeechTranscriber | None = None,
    ) -> None:
        self._config = config
        self._parser: TextParser = parser or GroqParser(settings, config)
        self._transcriber: SpeechTranscriber = transcriber or Transcriber(settings)

    def handle(
        self,
        session: Session,
        message: InboundMessage,
        now: datetime,
    ) -> EngineResult:
        if message.type == InputType.OTHER:
            return handle_inbound(session, message, self._config, now)

        text = message.text or ""
        if message.type == InputType.AUDIO:
            if not message.audio_bytes:
                return handle_inbound(session, message, self._config, now)
            try:
                logger.info("  ↳ STT: transcribing %d bytes…", len(message.audio_bytes))
                text = self._transcriber.transcribe(
                    message.audio_bytes, media_type=message.audio_media_type
                )
                logger.info("  ↳ STT result: %r", text[:120] if text else "(empty)")
            except TranscriptionError as exc:
                logger.error("  ↳ STT failed: %s", exc)
                return handle_inbound(
                    session,
                    message,
                    self._config,
                    now,
                    parse_error=f"transcription failed: {exc}",
                )

        updated = message.model_copy(update={"text": text})
        if not text:
            return handle_inbound(session, updated, self._config, now)

        # The keyword backstop runs before the LLM: emergencies get guidance
        # immediately, even when the parser is unavailable.
        if is_emergency(text):
            logger.warning("  ↳ Emergency keyword detected!")
            return handle_inbound(
                session,
                updated,
                self._config,
                now,
                extracted=ExtractedFields(is_emergency=True),
            )

        try:
            logger.info("  ↳ Parser: extracting fields…")
            extracted = self._parser.parse(text, session.fields, now)
            logger.info(
                "  ↳ Extracted: name=%s service=%s date=%s time=%s emergency=%s",
                extracted.patient_name,
                extracted.service_needed,
                extracted.preferred_date,
                extracted.preferred_time_window,
                extracted.is_emergency,
            )
        except (ParserError, GroqError) as exc:
            logger.error("  ↳ Parser failed: %s", exc)
            return handle_inbound(
                session,
                updated,
                self._config,
                now,
                parse_error=f"parser failed: {exc}",
            )

        return handle_inbound(session, updated, self._config, now, extracted=extracted)
