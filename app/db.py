"""SQLite persistence for conversation sessions and message dedupe."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.models import Session, SessionState

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    phone TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    fields TEXT NOT NULL,
    followup_rounds INTEGER NOT NULL DEFAULT 0,
    sheet_row INTEGER,
    last_message_at TEXT,
    transcripts TEXT NOT NULL DEFAULT '[]',
    last_outreach_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_messages (
    message_id TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_messages_phone ON seen_messages(phone);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db(db_path: str | Path) -> None:
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)


@contextmanager
def db_session(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def save_session(connection: sqlite3.Connection, session: Session) -> None:
    connection.execute(
        """
        INSERT INTO sessions (
            phone, state, fields, followup_rounds, sheet_row,
            last_message_at, transcripts, last_outreach_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            state = excluded.state,
            fields = excluded.fields,
            followup_rounds = excluded.followup_rounds,
            sheet_row = excluded.sheet_row,
            last_message_at = excluded.last_message_at,
            transcripts = excluded.transcripts,
            last_outreach_at = excluded.last_outreach_at,
            updated_at = excluded.updated_at
        """,
        (
            session.phone,
            session.state.value,
            session.fields.model_dump_json(),
            session.followup_rounds,
            session.sheet_row,
            _iso(session.last_message_at),
            json.dumps(session.transcripts, ensure_ascii=False),
            _iso(session.last_outreach_at),
            session.created_at.isoformat(),
            session.updated_at.isoformat(),
        ),
    )


def get_session(connection: sqlite3.Connection, phone: str) -> Session | None:
    row = connection.execute("SELECT * FROM sessions WHERE phone = ?", (phone,)).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def list_sessions(
    connection: sqlite3.Connection,
    states: Collection[SessionState] | None = None,
) -> list[Session]:
    """Load sessions, optionally filtered to the given states."""
    if states is None:
        rows = connection.execute("SELECT * FROM sessions").fetchall()
    else:
        placeholders = ", ".join("?" for _ in states)
        rows = connection.execute(
            f"SELECT * FROM sessions WHERE state IN ({placeholders})",
            [state.value for state in states],
        ).fetchall()
    return [_row_to_session(row) for row in rows]


def purge_transcripts(connection: sqlite3.Connection, cutoff: datetime) -> list[Session]:
    """Return sessions last updated before ``cutoff`` (retention candidates)."""
    rows = connection.execute(
        "SELECT * FROM sessions WHERE updated_at < ?", (cutoff.isoformat(),)
    ).fetchall()
    return [_row_to_session(row) for row in rows]


def clear_transcripts(
    connection: sqlite3.Connection,
    phones: Collection[str],
    purged_at: datetime,
    cutoff: datetime,
) -> None:
    """Clear transcripts for sessions that are still expired.

    The ``updated_at`` guard avoids deleting transcripts written by a late
    message between the retention query and this update.
    """
    if not phones:
        return
    connection.executemany(
        "UPDATE sessions SET transcripts = '[]', updated_at = ? "
        "WHERE phone = ? AND updated_at < ?",
        [
            (purged_at.isoformat(), phone, cutoff.isoformat())
            for phone in phones
        ],
    )


def delete_seen_before(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """Delete message-dedupe records older than ``cutoff``; return the count."""
    cursor = connection.execute(
        "DELETE FROM seen_messages WHERE seen_at < ?", (cutoff.isoformat(),)
    )
    return int(cursor.rowcount)


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session.model_validate(
        {
            "phone": row["phone"],
            "state": row["state"],
            "fields": json.loads(row["fields"]),
            "followup_rounds": row["followup_rounds"],
            "sheet_row": row["sheet_row"],
            "last_message_at": row["last_message_at"],
            "transcripts": json.loads(row["transcripts"]),
            "last_outreach_at": row["last_outreach_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def mark_seen(
    connection: sqlite3.Connection,
    message_id: str,
    phone: str,
    seen_at: datetime,
) -> bool:
    """Record a message id. Returns False when it was already seen (duplicate)."""
    try:
        connection.execute(
            "INSERT INTO seen_messages (message_id, phone, seen_at) VALUES (?, ?, ?)",
            (message_id, phone, seen_at.isoformat()),
        )
    except sqlite3.IntegrityError:
        return False
    return True


def count_outreach_since(connection: sqlite3.Connection, since: datetime) -> int:
    """Number of sessions that received outreach at or after ``since``."""
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM sessions "
        "WHERE last_outreach_at IS NOT NULL AND last_outreach_at >= ?",
        (since.isoformat(),),
    ).fetchone()
    return int(row["count"])


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
