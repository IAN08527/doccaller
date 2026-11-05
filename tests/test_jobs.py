"""Maintenance job tests: no-reply sweep and transcript retention."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import AppConfig
from app.db import (
    clear_transcripts,
    db_session,
    get_session,
    init_db,
    mark_seen,
    purge_transcripts,
    save_session,
)
from app.jobs import purge_expired, run_maintenance, sweep_no_replies
from app.models import ExtractedFields, LeadStatus, Session, SessionState
from app.sheets import SheetError

IST = ZoneInfo("Asia/Kolkata")


class RecordingSheets:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.cleared: list[tuple[str, int | None]] = []

    def upsert_lead(self, session: Session, status: LeadStatus) -> None:
        return None

    def clear_transcript(self, session: Session) -> None:
        if self.fail:
            raise SheetError("sheets unavailable")
        self.cleared.append((session.phone, session.sheet_row))


def make_session(
    now: datetime,
    phone: str,
    state: SessionState,
    *,
    updated_ago: timedelta = timedelta(0),
    outreach_ago: timedelta | None = None,
    message_ago: timedelta | None = None,
    transcripts: tuple[str, ...] = (),
    sheet_row: int | None = None,
) -> Session:
    updated_at = now - updated_ago
    return Session(
        phone=phone,
        state=state,
        fields=ExtractedFields(),
        transcripts=list(transcripts),
        sheet_row=sheet_row,
        last_outreach_at=now - outreach_ago if outreach_ago is not None else None,
        last_message_at=now - message_ago if message_ago is not None else None,
        created_at=updated_at,
        updated_at=updated_at,
    )


def save_all(db_path: Path, *sessions: Session) -> None:
    with db_session(db_path) as connection:
        for session in sessions:
            save_session(connection, session)


def load(db_path: Path, phone: str) -> Session | None:
    with db_session(db_path) as connection:
        return get_session(connection, phone)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    init_db(path)
    return path


@pytest.fixture()
def now() -> datetime:
    return datetime(2026, 11, 3, 23, 15, tzinfo=IST)


def test_sweep_marks_only_quiet_open_sessions(
    db_path: Path, config: AppConfig, now: datetime
) -> None:
    old_outreach = make_session(
        now,
        "+919820000001",
        SessionState.OUTREACH_SENT,
        outreach_ago=timedelta(hours=3),
        updated_ago=timedelta(hours=3),
    )
    recent_outreach = make_session(
        now,
        "+919820000002",
        SessionState.OUTREACH_SENT,
        outreach_ago=timedelta(hours=1),
        updated_ago=timedelta(hours=1),
    )
    quiet_collecting = make_session(
        now,
        "+919820000003",
        SessionState.COLLECTING,
        outreach_ago=timedelta(hours=5),
        message_ago=timedelta(hours=2),
        updated_ago=timedelta(hours=2),
    )
    confirmed = make_session(
        now,
        "+919820000004",
        SessionState.CONFIRMED,
        outreach_ago=timedelta(hours=10),
        updated_ago=timedelta(hours=10),
    )
    save_all(db_path, old_outreach, recent_outreach, quiet_collecting, confirmed)

    report = sweep_no_replies(str(db_path), config, now)

    assert report.checked == 3
    assert set(report.marked) == {"+919820000001", "+919820000003"}
    old_loaded = load(db_path, "+919820000001")
    recent_loaded = load(db_path, "+919820000002")
    collecting_loaded = load(db_path, "+919820000003")
    confirmed_loaded = load(db_path, "+919820000004")
    assert old_loaded is not None and old_loaded.state == SessionState.NO_REPLY
    assert recent_loaded is not None and recent_loaded.state == SessionState.OUTREACH_SENT
    assert collecting_loaded is not None
    assert collecting_loaded.state == SessionState.NO_REPLY
    assert confirmed_loaded is not None and confirmed_loaded.state == SessionState.CONFIRMED


def test_sweep_is_idempotent(db_path: Path, config: AppConfig, now: datetime) -> None:
    session = make_session(
        now,
        "+919820000001",
        SessionState.OUTREACH_SENT,
        outreach_ago=timedelta(hours=3),
        updated_ago=timedelta(hours=3),
    )
    save_all(db_path, session)

    first = sweep_no_replies(str(db_path), config, now)
    second = sweep_no_replies(str(db_path), config, now)

    assert first.marked == ("+919820000001",)
    assert second.marked == ()
    assert second.checked == 0


def test_retention_clears_local_and_sheet_transcripts(
    db_path: Path, config: AppConfig, now: datetime
) -> None:
    old = make_session(
        now,
        "+919820000001",
        SessionState.NO_REPLY,
        updated_ago=timedelta(days=91),
        transcripts=("old transcript",),
        sheet_row=42,
    )
    recent = make_session(
        now,
        "+919820000002",
        SessionState.NO_REPLY,
        updated_ago=timedelta(days=89),
        transcripts=("recent transcript",),
        sheet_row=43,
    )
    old_empty = make_session(
        now,
        "+919820000003",
        SessionState.NO_REPLY,
        updated_ago=timedelta(days=91),
        sheet_row=44,
    )
    save_all(db_path, old, recent, old_empty)
    with db_session(db_path) as connection:
        mark_seen(connection, "old-msg", old.phone, now - timedelta(days=91))
        mark_seen(connection, "recent-msg", recent.phone, now - timedelta(days=1))

    sheets = RecordingSheets()
    report = purge_expired(str(db_path), config, sheets, now)

    assert report.expired_sessions == 2
    assert report.transcripts_cleared == 1
    assert report.seen_messages_deleted == 1
    assert report.sheet_clears_failed == 0
    assert set(sheets.cleared) == {(old.phone, 42), (old_empty.phone, 44)}
    old_loaded = load(db_path, old.phone)
    recent_loaded = load(db_path, recent.phone)
    assert old_loaded is not None and old_loaded.transcripts == []
    assert recent_loaded is not None and recent_loaded.transcripts == ["recent transcript"]


def test_retention_is_idempotent(
    db_path: Path, config: AppConfig, now: datetime
) -> None:
    old = make_session(
        now,
        "+919820000001",
        SessionState.NO_REPLY,
        updated_ago=timedelta(days=91),
        transcripts=("old transcript",),
        sheet_row=42,
    )
    save_all(db_path, old)
    sheets = RecordingSheets()

    first = purge_expired(str(db_path), config, sheets, now)
    second = purge_expired(str(db_path), config, sheets, now)

    assert first.transcripts_cleared == 1
    assert second.expired_sessions == 0
    assert second.transcripts_cleared == 0
    assert sheets.cleared == [(old.phone, 42)]


def test_sheet_failure_keeps_transcript_for_the_next_run(
    db_path: Path, config: AppConfig, now: datetime
) -> None:
    old = make_session(
        now,
        "+919820000001",
        SessionState.NO_REPLY,
        updated_ago=timedelta(days=91),
        transcripts=("old transcript",),
        sheet_row=42,
    )
    save_all(db_path, old)
    sheets = RecordingSheets(fail=True)

    failed = purge_expired(str(db_path), config, sheets, now)

    assert failed.sheet_clears_failed == 1
    assert failed.transcripts_cleared == 0
    still_there = load(db_path, old.phone)
    assert still_there is not None and still_there.transcripts == ["old transcript"]

    sheets.fail = False
    recovered = purge_expired(str(db_path), config, sheets, now)

    assert recovered.sheet_clears_failed == 0
    assert recovered.transcripts_cleared == 1
    cleared = load(db_path, old.phone)
    assert cleared is not None and cleared.transcripts == []


def test_retention_update_guard_keeps_late_transcripts(
    db_path: Path, config: AppConfig, now: datetime
) -> None:
    cutoff = now - timedelta(days=config.retention_days)
    old = make_session(
        now,
        "+919820000001",
        SessionState.NO_REPLY,
        updated_ago=timedelta(days=91),
        transcripts=("old transcript",),
    )
    save_all(db_path, old)

    with db_session(db_path) as connection:
        candidates = purge_transcripts(connection, cutoff)
    assert len(candidates) == 1

    late = candidates[0].model_copy(deep=True)
    late.transcripts.append("late message")
    late.updated_at = now
    save_all(db_path, late)

    with db_session(db_path) as connection:
        clear_transcripts(connection, [old.phone], now, cutoff)
        loaded = get_session(connection, old.phone)

    assert loaded is not None
    assert loaded.transcripts == ["old transcript", "late message"]


def test_run_maintenance_reports_both_jobs(
    db_path: Path, config: AppConfig, now: datetime
) -> None:
    quiet = make_session(
        now,
        "+919820000001",
        SessionState.OUTREACH_SENT,
        outreach_ago=timedelta(hours=3),
        updated_ago=timedelta(hours=3),
    )
    expired = make_session(
        now,
        "+919820000002",
        SessionState.NO_REPLY,
        updated_ago=timedelta(days=120),
        transcripts=("old",),
        sheet_row=7,
    )
    save_all(db_path, quiet, expired)

    report = run_maintenance(str(db_path), config, RecordingSheets(), now)

    assert report.ran_at == now
    assert report.sweep.marked == (quiet.phone,)
    assert report.retention.transcripts_cleared == 1
