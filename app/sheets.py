"""Google Sheets handoff: one lead row per session, updated in place."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar
from zoneinfo import ZoneInfo

import gspread
from gspread.utils import ValueInputOption

from app.config import AppConfig, Settings
from app.models import LeadStatus, Session

logger = logging.getLogger(__name__)

LEAD_HEADERS: tuple[str, ...] = (
    "Timestamp (IST)",
    "Caller Phone",
    "Patient Name",
    "Service Needed",
    "Requested Slot",
    "Status",
    "Urgent?",
    "Raw Transcript",
    "Staff Checkbox",
    "Staff Notes",
)

_RANGE_ROW = re.compile(r"!A(\d+):")

RETRYABLE_SHEET_STATUS = frozenset({429, 500, 502, 503, 504})

T = TypeVar("T")


class SheetError(RuntimeError):
    """Raised when the staff sheet cannot be read or written."""


class SheetsClient(Protocol):
    def upsert_lead(self, session: Session, status: LeadStatus) -> int | None:
        """Create or update the session's row; return its 1-based row number.

        Returns None when Sheets is not configured (lead stays in SQLite only).
        """

    def clear_transcript(self, session: Session) -> None:
        """Clear the raw-transcript cell for a retained session, if it has a row."""


def call_with_retries(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry a Sheets operation on rate limits and transient server errors."""
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if attempt == attempts - 1 or not _is_retryable(exc):
                raise
            sleep(base_delay * (2**attempt))
    raise RuntimeError("retry loop exhausted")


def _is_retryable(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return isinstance(status, int) and status in RETRYABLE_SHEET_STATUS


def build_lead_row(session: Session, status: LeadStatus, config: AppConfig) -> list[str]:
    """Columns A-H only; I-J belong to staff."""
    timezone = ZoneInfo(config.clinic.timezone)
    timestamp = session.created_at.astimezone(timezone).strftime("%d/%m/%Y %H:%M:%S")

    fields = session.fields
    slot = ""
    if fields.preferred_date is not None:
        slot = fields.preferred_date.isoformat()
        if fields.preferred_time_window:
            slot = f"{slot} ({fields.preferred_time_window})"
    elif fields.preferred_time_window:
        slot = fields.preferred_time_window

    urgent = "Yes" if status == LeadStatus.URGENT or fields.is_emergency else "No"
    transcript = "\n---\n".join(session.transcripts)

    return [
        timestamp,
        session.phone,
        fields.patient_name or "",
        fields.service_needed or "",
        slot,
        status.value,
        urgent,
        transcript,
    ]


def row_from_append_result(result: Any) -> int | None:
    if not isinstance(result, dict):
        return None
    updates = result.get("updates")
    if not isinstance(updates, dict):
        return None
    match = _RANGE_ROW.search(str(updates.get("updatedRange", "")))
    return int(match.group(1)) if match else None


def conditional_format_requests(sheet_id: int) -> list[dict[str, Any]]:
    """Batch-update payload: URGENT rows red, Completed rows grey."""
    return [
        _conditional_rule(sheet_id, "URGENT", {"red": 0.96, "green": 0.8, "blue": 0.8}),
        _conditional_rule(sheet_id, "Completed", {"red": 0.85, "green": 0.85, "blue": 0.85}),
    ]


def sheet_id_from_metadata(metadata: Any, title: str) -> int | None:
    if not isinstance(metadata, dict):
        return None
    sheets = metadata.get("sheets")
    if not isinstance(sheets, list):
        return None
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        properties = sheet.get("properties")
        if isinstance(properties, dict) and properties.get("title") == title:
            sheet_id = properties.get("sheetId")
            return int(sheet_id) if sheet_id is not None else None
    return None


def create_gspread_client(settings: Settings) -> gspread.Client:
    """Create a gspread client using inline JSON or a service account file."""
    if settings.google_service_account_json:
        try:
            creds = json.loads(settings.google_service_account_json)
        except json.JSONDecodeError as exc:
            raise SheetError(f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {exc}") from exc
        return gspread.service_account_from_dict(creds)
    if settings.google_service_account_file:
        path = Path(settings.google_service_account_file)
        if path.is_file():
            return gspread.service_account(filename=str(path))
        raise SheetError(f"Service-account file not found: {path}")
    raise SheetError(
        "No Google service account configured (set GOOGLE_SERVICE_ACCOUNT_JSON in .env)"
    )


def get_service_account_email(settings: Settings) -> str:
    """Extract client_email from settings JSON or file."""
    if settings.google_service_account_json:
        try:
            creds: dict[str, Any] = json.loads(settings.google_service_account_json)
            return str(creds.get("client_email", ""))
        except Exception:
            return ""
    if settings.google_service_account_file:
        path = Path(settings.google_service_account_file)
        if path.is_file():
            try:
                creds = json.loads(path.read_text(encoding="utf-8"))
                return str(creds.get("client_email", ""))
            except Exception:
                return ""
    return ""


class GoogleSheetsClient:
    def __init__(
        self,
        config: AppConfig,
        settings: Settings,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._settings = settings
        self._sleep = sleep
        self._worksheet: Any | None = None

    def upsert_lead(self, session: Session, status: LeadStatus) -> int:
        try:
            return call_with_retries(
                lambda: self._upsert_once(session, status), sleep=self._sleep
            )
        except SheetError:
            raise
        except Exception as exc:
            raise SheetError(f"Sheets upsert failed: {exc}") from exc

    def clear_transcript(self, session: Session) -> None:
        if session.sheet_row is None:
            return
        try:
            call_with_retries(
                lambda: self._clear_transcript_once(session), sleep=self._sleep
            )
        except SheetError:
            raise
        except Exception as exc:
            raise SheetError(f"Sheets transcript clear failed: {exc}") from exc

    def _upsert_once(self, session: Session, status: LeadStatus) -> int:
        worksheet = self._get_worksheet()
        row = build_lead_row(session, status, self._config)
        if session.sheet_row is None:
            result = worksheet.append_row(row, value_input_option=ValueInputOption.raw)
            row_number = row_from_append_result(result)
            if row_number is None:
                row_number = len(worksheet.col_values(1))
            return int(row_number)
        worksheet.update(
            range_name=f"A{session.sheet_row}:H{session.sheet_row}",
            values=[row],
            value_input_option=ValueInputOption.raw,
        )
        return session.sheet_row

    def _clear_transcript_once(self, session: Session) -> None:
        if session.sheet_row is None:
            return
        worksheet = self._get_worksheet()
        worksheet.update(
            range_name=f"H{session.sheet_row}",
            values=[[""]],
            value_input_option=ValueInputOption.raw,
        )

    def _get_worksheet(self) -> Any:
        if self._worksheet is not None:
            return self._worksheet

        if not self._settings.sheet_id:
            raise SheetError("SHEET_ID is not configured")

        client = create_gspread_client(self._settings)
        spreadsheet = client.open_by_key(self._settings.sheet_id)
        try:
            worksheet = spreadsheet.worksheet(self._settings.sheet_tab)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=self._settings.sheet_tab, rows=1000, cols=len(LEAD_HEADERS)
            )
            worksheet.update(
                range_name="A1:J1",
                values=[list(LEAD_HEADERS)],
                value_input_option=ValueInputOption.raw,
            )
            self._apply_formatting(spreadsheet)
        else:
            if not worksheet.row_values(1):
                worksheet.update(
                    range_name="A1:J1",
                    values=[list(LEAD_HEADERS)],
                    value_input_option=ValueInputOption.raw,
                )

        self._worksheet = worksheet
        return worksheet

    def _apply_formatting(self, spreadsheet: Any) -> None:
        try:
            metadata = spreadsheet.fetch_sheet_metadata()
            sheet_id = sheet_id_from_metadata(metadata, self._settings.sheet_tab)
            if sheet_id is None:
                return
            spreadsheet.batch_update({"requests": conditional_format_requests(sheet_id)})
        except Exception:
            logger.warning("Could not apply conditional formatting", exc_info=True)


class LoggingSheetsClient:
    """Local fallback when SHEET_ID is unset: never loses the lead, never fakes a row."""

    def upsert_lead(self, session: Session, status: LeadStatus) -> None:
        logger.warning(
            "Sheets not configured; lead %s (%s) kept in SQLite only",
            session.phone,
            status.value,
        )
        return None

    def clear_transcript(self, session: Session) -> None:
        logger.debug(
            "Sheets not configured; transcript for %s purged locally only",
            session.phone,
        )


def build_sheets_client(config: AppConfig, settings: Settings) -> SheetsClient:
    if not settings.sheet_id:
        return LoggingSheetsClient()
    return GoogleSheetsClient(config, settings)


def _conditional_rule(
    sheet_id: int, value: str, background: dict[str, float]
) -> dict[str, Any]:
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 1000,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(LEAD_HEADERS),
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "TEXT_EQ",
                        "values": [{"userEnteredValue": value}],
                    },
                    "format": {"backgroundColor": background},
                },
            },
            "index": 0,
        }
    }
