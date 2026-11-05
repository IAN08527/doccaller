"""Whisper transcription tests using httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.stt import Transcriber, TranscriptionError


def make_transcriber(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Transcriber:
    return Transcriber(
        Settings(groq_api_key="test-key"),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


def test_transcribe_sends_audio_and_returns_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openai/v1/audio/transcriptions"
        assert b"whisper-large-v3-turbo" in request.content
        assert b"root canal" in request.content  # vocabulary prompt
        return httpx.Response(200, json={"text": " Kal subah cleaning chahiye "})

    transcriber = make_transcriber(handler)
    assert transcriber.transcribe(b"fake-audio") == "Kal subah cleaning chahiye"


def test_transcribe_uses_requested_media_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        assert b'filename="voice.ogg"' in body
        assert b"Content-Type: audio/ogg" in body
        return httpx.Response(200, json={"text": "Kal subah cleaning chahiye"})

    transcriber = make_transcriber(handler)
    assert (
        transcriber.transcribe(b"fake-audio", media_type="audio/ogg")
        == "Kal subah cleaning chahiye"
    )


def test_unknown_media_type_falls_back_to_webm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        assert b'filename="voice.webm"' in body
        assert b"Content-Type: audio/webm" in body
        return httpx.Response(200, json={"text": "ok"})

    transcriber = make_transcriber(handler)
    assert transcriber.transcribe(b"fake-audio", media_type="application/x-unknown") == "ok"


def test_empty_audio_is_rejected_before_http() -> None:
    transcriber = Transcriber(Settings(groq_api_key=""))
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(b"")


def test_empty_transcript_returns_empty_string() -> None:
    transcriber = make_transcriber(lambda _: httpx.Response(200, json={"text": "   "}))
    assert transcriber.transcribe(b"fake-audio") == ""


def test_transcribe_retries_server_error_then_succeeds() -> None:
    calls = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"text": "Root canal kal"})

    transcriber = make_transcriber(handler)
    assert transcriber.transcribe(b"fake-audio") == "Root canal kal"
    assert calls["count"] == 2


def test_non_retryable_error_becomes_transcription_error() -> None:
    transcriber = make_transcriber(
        lambda _: httpx.Response(400, json={"error": "unsupported audio"})
    )
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(b"fake-audio")


def test_missing_text_field_becomes_transcription_error() -> None:
    transcriber = make_transcriber(lambda _: httpx.Response(200, json={"ok": True}))
    with pytest.raises(TranscriptionError):
        transcriber.transcribe(b"fake-audio")
