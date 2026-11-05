"""Application configuration and environment settings."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


class ClinicConfig(BaseModel):
    name: str
    address: str
    timezone: str = "Asia/Kolkata"
    phone_country_code: str = "+91"
    languages: list[str] = Field(default_factory=lambda: ["en"])


class HoursConfig(BaseModel):
    open: str
    close: str
    closed_days: list[str] = Field(default_factory=list)
    slot_windows: list[str] = Field(default_factory=list)


class EmergencyConfig(BaseModel):
    on_call_number: str
    hospital: str


class MissedCallConfig(BaseModel):
    dedupe_hours: int = 12
    shared_secret: str = ""


class FollowupConfig(BaseModel):
    max_rounds: int = 3


class AppConfig(BaseModel):
    clinic: ClinicConfig
    hours: HoursConfig
    services: list[str]
    emergency: EmergencyConfig
    privacy_notice: str
    retention_days: int = 90
    daily_outreach_cap: int = 150
    missed_call: MissedCallConfig = Field(default_factory=MissedCallConfig)
    followup: FollowupConfig = Field(default_factory=FollowupConfig)
    no_reply_sweep_hours: int = 2


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load and validate ``config.yaml``.

    The path can be overridden with the ``CONFIG_PATH`` environment variable.
    """
    if path is None:
        configured = os.getenv("CONFIG_PATH")
        path = configured if configured else PROJECT_ROOT / "config.yaml"
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(raw)


def now_in(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


class Settings(BaseModel):
    """Secrets and runtime knobs. Never commit the values."""

    groq_api_key: str = ""
    groq_parser_model: str = "openai/gpt-oss-20b"
    groq_stt_model: str = "whisper-large-v3-turbo"
    google_service_account_json: str = ""
    google_service_account_file: str = ""
    sheet_id: str = ""
    sheet_tab: str = "Leads"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    db_path: str = "./doccaller.db"
    missed_call_shared_secret: str = ""
    jobs_enabled: bool = True
    job_interval_minutes: int = 15


def get_settings() -> Settings:
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        groq_parser_model=os.getenv("GROQ_PARSER_MODEL", "openai/gpt-oss-20b"),
        groq_stt_model=os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
        google_service_account_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
        google_service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", ""),
        sheet_id=os.getenv("SHEET_ID", ""),
        sheet_tab=os.getenv("SHEET_TAB", "Leads"),
        app_host=os.getenv("APP_HOST", "0.0.0.0" if os.getenv("RENDER") else "127.0.0.1"),
        app_port=int(os.getenv("PORT", os.getenv("APP_PORT", "8000"))),
        db_path=os.getenv("DB_PATH", "./doccaller.db"),
        missed_call_shared_secret=os.getenv("MISSED_CALL_SHARED_SECRET", ""),
        jobs_enabled=os.getenv("JOBS_ENABLED", "true").strip().lower()
        not in {"0", "false", "no"},
        job_interval_minutes=int(os.getenv("JOB_INTERVAL_MINUTES", "15")),
    )
