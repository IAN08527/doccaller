"""FastAPI entrypoint: missed-call webhook, chat, and health."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api_models import (
    ChatRequest,
    ChatResponse,
    MissedCallRequest,
    MissedCallResponse,
)
from app.config import AppConfig, get_settings, load_config, now_in
from app.db import (
    count_outreach_since,
    db_session,
    get_session,
    init_db,
    mark_seen,
    save_session,
)
from app.engine import start_missed_call, status_for
from app.logging_config import (
    RequestLoggingMiddleware,
    log_startup_banner,
    setup_logging,
)
from app.models import InboundMessage, InputType, Session
from app.phone import normalize_phone
from app.pipeline import ConversationPipeline
from app.scheduler import maintenance_loop
from app.sheets import SheetError, SheetsClient, build_sheets_client

logger = logging.getLogger("doccaller.api")

MAX_AUDIO_BYTES = 25 * 1024 * 1024


def config_dependency() -> AppConfig:
    return load_config()


@lru_cache(maxsize=1)
def _sheets_client() -> SheetsClient:
    """One client per process so the worksheet handle is reused."""
    return build_sheets_client(load_config(), get_settings())


def sheets_dependency() -> SheetsClient:
    return _sheets_client()


def pipeline_dependency() -> ConversationPipeline:
    config = load_config()
    return ConversationPipeline(config, get_settings())


ConfigDep = Annotated[AppConfig, Depends(config_dependency)]
SheetsDep = Annotated[SheetsClient, Depends(sheets_dependency)]
PipelineDep = Annotated[ConversationPipeline, Depends(pipeline_dependency)]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()
    log_startup_banner(settings)

    init_db(settings.db_path)

    task: asyncio.Task[None] | None = None
    if settings.jobs_enabled:
        task = asyncio.create_task(
            maintenance_loop(settings, load_config(), _sheets_client())
        )
        logger.info("Maintenance scheduler started (every %dm)", settings.job_interval_minutes)

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        logger.info("Shutdown complete")


app = FastAPI(title="DocCaller", version="0.7.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    config = load_config()
    with db_session(settings.db_path) as connection:
        connection.execute("SELECT 1").fetchone()
    return {
        "status": "ok",
        "clinic": config.clinic.name,
        "db": "ok",
        "sheets": "configured" if settings.sheet_id else "not_configured",
        "parser_model": settings.groq_parser_model,
        "groq_key": "configured" if settings.groq_api_key else "not_configured",
        "jobs": "enabled" if settings.jobs_enabled else "disabled",
    }


@app.post("/missed-call", response_model=MissedCallResponse)
def missed_call(
    payload: MissedCallRequest,
    config: ConfigDep,
    sheets: SheetsDep,
    x_webhook_secret: Annotated[str | None, Header(alias="X-Webhook-Secret")] = None,
) -> MissedCallResponse:
    settings = get_settings()
    secret = settings.missed_call_shared_secret or config.missed_call.shared_secret
    if secret and x_webhook_secret != secret:
        raise HTTPException(status_code=401, detail="invalid shared secret")
    if payload.event != "missed_call":
        raise HTTPException(status_code=422, detail="unsupported event")

    phone = normalize_phone(payload.caller_phone, config.clinic.phone_country_code)
    if phone is None:
        logger.warning(
            "Missed call ignored — invalid/withheld number: %r",
            payload.caller_phone,
        )
        return MissedCallResponse(status="ignored", reason="invalid_or_withheld_number")

    logger.info("Missed call from %s", phone)

    now = _resolve_time(payload.timestamp, config)
    with db_session(get_settings().db_path) as connection:
        existing = get_session(connection, phone)
        if existing is not None and existing.last_outreach_at is not None:
            elapsed = now - existing.last_outreach_at
            if elapsed < timedelta(hours=config.missed_call.dedupe_hours):
                logger.info("  ↳ duplicate (last outreach %s ago), skipped", elapsed)
                return MissedCallResponse(status="duplicate", sheet_row=existing.sheet_row)

        timezone = ZoneInfo(config.clinic.timezone)
        start_of_day = now.astimezone(timezone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if count_outreach_since(connection, start_of_day) >= config.daily_outreach_cap:
            logger.warning("  ↳ daily outreach cap (%d) reached", config.daily_outreach_cap)
            return MissedCallResponse(status="capped", reason="daily_outreach_cap")

        result = start_missed_call(phone, config, now)
        save_session(connection, result.session)

        row, sync = _sync_sheet(sheets, result.session)
        if row is not None:
            result.session.sheet_row = row
            save_session(connection, result.session)

    logger.info("  ↳ outreach sent, sheet=%s row=%s", sync, row)

    return MissedCallResponse(
        status="outreach_sent",
        reply=result.reply,
        sheet_row=row,
        sheet_sync=sync,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    config: ConfigDep,
    sheets: SheetsDep,
    pipeline: PipelineDep,
) -> ChatResponse:
    phone = normalize_phone(payload.phone, config.clinic.phone_country_code)
    if phone is None:
        raise HTTPException(status_code=422, detail="invalid phone number")

    parts = _message_parts(payload)
    if parts is None:
        raise HTTPException(status_code=422, detail="provide text or audio_base64")

    logger.info("Chat from %s [%s] msg=%s", phone, parts.type.value, payload.message_id)

    now = _resolve_time(payload.timestamp, config)
    with db_session(get_settings().db_path) as connection:
        session = get_session(connection, phone)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail="no session for this phone; POST /missed-call first",
            )

        if not mark_seen(connection, payload.message_id, phone, now):
            logger.info("  ↳ duplicate message, skipped")
            return ChatResponse(
                status="duplicate",
                state=session.state.value,
                lead_status=status_for(session).value,
                sheet_row=session.sheet_row,
            )

        message = InboundMessage(
            user_id=phone,
            message_id=payload.message_id,
            type=parts.type,
            text=parts.text,
            audio_bytes=parts.audio,
            audio_media_type=parts.media_type,
            timestamp=now,
        )
        result = pipeline.handle(session, message, now)
        save_session(connection, result.session)

        row, sync = _sync_sheet(sheets, result.session)
        if row is not None:
            result.session.sheet_row = row
            save_session(connection, result.session)

    logger.info(
        "  ↳ state=%s lead=%s sheet=%s",
        result.session.state.value,
        result.status.value,
        sync,
    )
    if result.alert:
        logger.warning("  ⚠ ALERT: %s", result.alert)

    return ChatResponse(
        reply=result.reply,
        buttons=result.buttons,
        state=result.session.state.value,
        lead_status=result.status.value,
        alert=result.alert,
        sheet_row=row,
        sheet_sync=sync,
    )


def _sync_sheet(client: SheetsClient, session: Session) -> tuple[int | None, str]:
    try:
        row = client.upsert_lead(session, status_for(session))
    except SheetError as exc:
        logger.warning("Sheet sync failed for %s: %s", session.phone, exc)
        return session.sheet_row, "pending"
    if row is None:
        return None, "unconfigured"
    return row, "ok"


def _resolve_time(value: datetime | None, config: AppConfig) -> datetime:
    timezone = ZoneInfo(config.clinic.timezone)
    if value is None:
        return now_in(config.clinic.timezone)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


@dataclass(frozen=True, slots=True)
class MessageParts:
    type: InputType
    text: str | None = None
    audio: bytes | None = None
    media_type: str | None = None


def _message_parts(payload: ChatRequest) -> MessageParts | None:
    if payload.audio_base64:
        try:
            audio = base64.b64decode(payload.audio_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="audio_base64 is not valid base64"
            ) from exc
        if not audio:
            raise HTTPException(status_code=422, detail="audio payload is empty")
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="audio payload too large")
        return MessageParts(
            type=InputType.AUDIO,
            audio=audio,
            media_type=payload.audio_media_type,
        )

    if payload.text is not None:
        stripped = payload.text.strip()
        if stripped:
            return MessageParts(type=InputType.TEXT, text=stripped)
        return MessageParts(type=InputType.OTHER)

    return None
