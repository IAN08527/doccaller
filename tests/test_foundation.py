"""Phase 0 foundation tests: config, models, persistence, health."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings, load_config
from app.db import db_session, get_session, init_db, mark_seen, save_session
from app.main import app
from app.models import ExtractedFields, Session, SessionState


def make_session(now: datetime) -> Session:
    return Session(
        phone="+919820012345",
        state=SessionState.COLLECTING,
        fields=ExtractedFields(
            patient_name="Rahul Verma",
            service_needed="Root Canal Treatment",
        ),
        followup_rounds=1,
        created_at=now,
        updated_at=now,
    )


def test_config_loads() -> None:
    config = load_config()
    assert config.clinic.name == "Smile Care Dental Clinic"
    assert config.clinic.timezone == "Asia/Kolkata"
    assert config.hours.open == "09:00"
    assert config.hours.closed_days == ["Sunday"]
    assert "Root Canal Treatment" in config.services
    assert config.followup.max_rounds == 3
    assert config.missed_call.dedupe_hours == 12


def test_session_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    now = datetime(2026, 11, 3, 23, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    session = make_session(now)

    with db_session(db_path) as connection:
        save_session(connection, session)
        loaded = get_session(connection, session.phone)

    assert loaded == session


def test_session_upsert_updates_state(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    now = datetime(2026, 11, 3, 23, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    session = make_session(now)

    with db_session(db_path) as connection:
        save_session(connection, session)
        session.state = SessionState.AWAIT_CONFIRM
        session.followup_rounds = 2
        save_session(connection, session)
        loaded = get_session(connection, session.phone)

    assert loaded is not None
    assert loaded.state == SessionState.AWAIT_CONFIRM
    assert loaded.followup_rounds == 2
    assert len(loaded.transcripts) == 0


def test_duplicate_message_ids_are_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    now = datetime(2026, 11, 3, 23, 15, tzinfo=ZoneInfo("Asia/Kolkata"))

    with db_session(db_path) as connection:
        assert mark_seen(connection, "msg-1", "+919820012345", now) is True
        assert mark_seen(connection, "msg-1", "+919820012345", now) is False


def test_health_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "health.db"))
    monkeypatch.setenv("JOBS_ENABLED", "false")
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["clinic"] == "Smile Care Dental Clinic"
    assert get_settings().db_path == str(tmp_path / "health.db")
