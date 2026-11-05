"""In-process maintenance scheduler for the local prototype."""

from __future__ import annotations

import asyncio
import logging

from app.config import AppConfig, Settings, now_in
from app.jobs import run_maintenance
from app.sheets import SheetsClient

logger = logging.getLogger(__name__)


async def maintenance_loop(
    settings: Settings,
    config: AppConfig,
    sheets: SheetsClient,
) -> None:
    """Run maintenance immediately, then once per configured interval."""
    interval_seconds = max(1, settings.job_interval_minutes) * 60
    while True:
        try:
            report = await asyncio.to_thread(
                run_maintenance,
                settings.db_path,
                config,
                sheets,
                now_in(config.clinic.timezone),
            )
        except Exception:
            logger.exception("Maintenance job failed")
        else:
            logger.info(
                "Maintenance: swept %d session(s), purged %d transcript(s)",
                len(report.sweep.marked),
                report.retention.transcripts_cleared,
            )
        await asyncio.sleep(interval_seconds)
