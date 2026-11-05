"""Shared Groq HTTP plumbing: auth header, retries, and error mapping."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

GROQ_API_BASE = "https://api.groq.com/openai/v1"
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class GroqError(RuntimeError):
    """Raised when a Groq API call fails."""


def auth_headers(api_key: str) -> dict[str, str]:
    if not api_key.strip():
        raise GroqError("GROQ_API_KEY is not set")
    return {"Authorization": f"Bearer {api_key}"}


def post_with_retries(
    client: httpx.Client,
    path: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    files: Mapping[str, tuple[str, bytes, str]] | None = None,
    data: Mapping[str, str] | None = None,
    attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """POST to Groq with exponential backoff on 429/5xx and network errors."""
    url = f"{GROQ_API_BASE}{path}"
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = client.post(
                url,
                headers=headers,
                json=json_body,
                files=files,
                data=data,
            )
        except httpx.HTTPError as exc:
            last_error = exc
        else:
            if response.status_code < 400:
                return response
            if response.status_code not in RETRY_STATUS_CODES:
                raise GroqError(
                    f"Groq API error {response.status_code}: {response.text[:300]}"
                )
            last_error = GroqError(f"Groq API error {response.status_code}")
        if attempt < attempts - 1:
            sleep(base_delay * (2**attempt))

    raise GroqError(f"Groq request failed after {attempts} attempts: {last_error}")
