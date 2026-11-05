"""Scheduled maintenance: no-reply sweep and transcript retention."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import AppConfig
from app.db import (
    clear_transcripts,
    db_session,
    delete_seen_before,
    list_sessions,
    purge_transcripts,
    save_session,
)
from app.engine import mark_no_reply
from app.models import SessionState
from app.sheets import SheetError, SheetsClient

logger = logging.getLogger(__name__)

OPEN_STATES = frozenset({SessionState.OUTREACH_SENT, SessionState.COLLECTING})


@dataclass(frozen=True, slots=True)
class SweepReport:
    checked: int
    marked: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetentionReport:
    expired_sessions: int
    transcripts_cleared: int
    seen_messages_deleted: int
    sheet_clears_failed: int


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    ran_at: datetime
    sweep: SweepReport
    retention: RetentionReport


def sweep_no_replies(db_path: str, config: AppConfig, now: datetime) -> SweepReport:
    """Move quiet open sessions to ``NO_REPLY`` after the configured window."""
    marked: list[str] = []
    with db_session(db_path) as connection:
        sessions = list_sessions(connection, OPEN_STATES)
        for session in sessions:
            updated = mark_no_reply(session, now, config)
            if updated is None:
                continue
            save_session(connection, updated)
            marked.append(updated.phone)
            logger.info(
                "No reply after %sh: %s",
                config.no_reply_sweep_hours,
                updated.phone,
            )
    return SweepReport(checked=len(sessions), marked=tuple(marked))


def purge_expired(
    db_path: str,
    config: AppConfig,
    sheets: SheetsClient,
    now: datetime,
) -> RetentionReport:
    """Clear transcripts and dedupe records older than the retention window."""
    cutoff = now - timedelta(days=config.retention_days)
    with db_session(db_path) as connection:
        expired = purge_transcripts(connection, cutoff)

    handled: list[str] = []
    failures = 0
    for session in expired:
        if session.sheet_row is not None:
            try:
                sheets.clear_transcript(session)
            except SheetError as exc:
                failures += 1
                logger.warning(
                    "Could not clear sheet transcript for %s: %s",
                    session.phone,
                    exc,
                )
                continue
        handled.append(session.phone)

    with db_session(db_path) as connection:
        clear_transcripts(connection, handled, now, cutoff)
        deleted = delete_seen_before(connection, cutoff)

    cleared = sum(
        1 for session in expired if session.phone in handled and session.transcripts
    )
    if cleared:
        logger.info("Cleared transcripts for %d expired session(s)", cleared)
    return RetentionReport(
        expired_sessions=len(expired),
        transcripts_cleared=cleared,
        seen_messages_deleted=deleted,
        sheet_clears_failed=failures,
    )


def run_maintenance(
    db_path: str,
    config: AppConfig,
    sheets: SheetsClient,
    now: datetime,
) -> MaintenanceReport:
    """Run both maintenance jobs once. Safe to call repeatedly."""
    sweep = sweep_no_replies(db_path, config, now)
    retention = purge_expired(db_path, config, sheets, now)
    return MaintenanceReport(ran_at=now, sweep=sweep, retention=retention)
