"""Structured request logging — Next.js-style route + status + timing."""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# ── ANSI colours (Windows Terminal / modern terminals) ────────────────────────

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_WHITE = "\033[97m"


def _status_colour(status: int) -> str:
    if status < 300:
        return _GREEN
    if status < 400:
        return _CYAN
    if status < 500:
        return _YELLOW
    return _RED


def _method_colour(method: str) -> str:
    return {
        "GET": _GREEN,
        "POST": _MAGENTA,
        "PUT": _YELLOW,
        "PATCH": _YELLOW,
        "DELETE": _RED,
    }.get(method.upper(), _WHITE)


# ── Formatter ─────────────────────────────────────────────────────────────────


class PrettyFormatter(logging.Formatter):
    """Human-friendly log lines with colour-coded level and timestamp."""

    LEVEL_COLOURS: dict[int, str] = {
        logging.DEBUG: _DIM,
        logging.INFO: _GREEN,
        logging.WARNING: _YELLOW,
        logging.ERROR: _RED,
        logging.CRITICAL: f"{_RED}{_BOLD}",
    }

    def format(self, record: logging.LogRecord) -> str:
        colour = self.LEVEL_COLOURS.get(record.levelno, _WHITE)
        ts = self.formatTime(record, "%H:%M:%S")
        level = f"{colour}{record.levelname:<7}{_RESET}"
        name = f"{_DIM}{record.name}{_RESET}"
        msg = record.getMessage()
        base = f"  {_DIM}{ts}{_RESET}  {level} {name}  {msg}"
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            base += f"\n{record.exc_text}"
        return base


# ── Middleware ─────────────────────────────────────────────────────────────────


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request like:

        21:30:05  INFO  POST /chat 200 — 342ms
        21:30:06  WARN  POST /missed-call 422 — 2ms
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        method = request.method
        path = request.url.path
        status = 500  # default if an unhandled exception escapes

        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            logger = logging.getLogger("doccaller.http")
            logger.exception("Unhandled error in %s %s", method, path)
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            log = logging.getLogger("doccaller.http")

            mc = _method_colour(method)
            sc = _status_colour(status)
            timing = f"{elapsed_ms:.0f}ms" if elapsed_ms >= 1 else f"{elapsed_ms:.1f}ms"

            line = (
                f"{mc}{method:<6}{_RESET} {path}  "
                f"{sc}{status}{_RESET}  "
                f"{_DIM}{timing}{_RESET}"
            )

            if status >= 500:
                log.error(line)
            elif status >= 400:
                log.warning(line)
            else:
                log.info(line)


# ── Bootstrap ─────────────────────────────────────────────────────────────────


def setup_logging(*, level: int = logging.INFO) -> None:
    """Call once at startup to wire up the pretty formatter."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(PrettyFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    # Remove any existing handlers (uvicorn adds its own)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet down noisy libraries but keep important messages
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("gspread").setLevel(logging.WARNING)


def log_startup_banner(settings: Any) -> None:
    """Print a clear startup summary."""
    log = logging.getLogger("doccaller")
    log.info(f"{_BOLD}{_CYAN}DocCaller v0.7.0{_RESET}")
    log.info(f"  Listening on  {_WHITE}http://{settings.app_host}:{settings.app_port}{_RESET}")
    log.info(f"  Groq key      {'✓ configured' if settings.groq_api_key else '✗ missing'}")
    sheets_desc = (
        f"✓ {settings.sheet_id[:12]}…"
        if settings.sheet_id
        else "✗ not configured (leads stay in SQLite)"
    )
    log.info(f"  Sheets        {sheets_desc}")
    log.info(f"  Parser model  {settings.groq_parser_model}")
    log.info(f"  STT model     {settings.groq_stt_model}")
    log.info(f"  Jobs          {'✓ enabled' if settings.jobs_enabled else '✗ disabled'}")
    log.info(f"  DB            {settings.db_path}")

