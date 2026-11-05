"""Maintenance loop scheduling tests."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app import scheduler
from app.config import AppConfig, Settings
from app.jobs import MaintenanceReport, RetentionReport, SweepReport
from app.sheets import LoggingSheetsClient


def test_maintenance_loop_runs_immediately_then_uses_interval(
    monkeypatch: pytest.MonkeyPatch, config: AppConfig
) -> None:
    calls: list[tuple[object, ...]] = []
    report = MaintenanceReport(
        ran_at=datetime.now(),
        sweep=SweepReport(checked=0, marked=()),
        retention=RetentionReport(
            expired_sessions=0,
            transcripts_cleared=0,
            seen_messages_deleted=0,
            sheet_clears_failed=0,
        ),
    )

    def fake_run(*args: object) -> MaintenanceReport:
        calls.append(args)
        return report

    async def fake_sleep(seconds: float) -> None:
        assert seconds == 60
        raise RuntimeError("stop loop")

    monkeypatch.setattr(scheduler, "run_maintenance", fake_run)
    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)

    settings = Settings(job_interval_minutes=1)
    with pytest.raises(RuntimeError, match="stop loop"):
        asyncio.run(
            scheduler.maintenance_loop(settings, config, LoggingSheetsClient())
        )

    assert len(calls) == 1
    assert calls[0][0] == settings.db_path
    assert calls[0][1] is config
