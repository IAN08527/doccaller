"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import AppConfig, load_config


@pytest.fixture(scope="session")
def config() -> AppConfig:
    return load_config()


@pytest.fixture()
def now() -> datetime:
    # A Tuesday evening, after clinic hours.
    return datetime(2026, 11, 3, 23, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
