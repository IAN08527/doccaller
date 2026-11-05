"""Provision the staff tab (headers, freeze, conditional formatting).

    uv run python scripts/provision_sheet.py --sheet-id <id>

Service accounts have no Drive storage quota, so the spreadsheet itself must be
created by a human Google account and shared with the service account as Editor.
This script then prepares the ``Leads`` tab the app writes to.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import gspread  # noqa: E402
from gspread.utils import ValueInputOption  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.sheets import (  # noqa: E402
    LEAD_HEADERS,
    SheetError,
    conditional_format_requests,
    create_gspread_client,
    get_service_account_email,
    sheet_id_from_metadata,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    cli = argparse.ArgumentParser(description="Prepare the Leads tab.")
    cli.add_argument("--sheet-id", default="", help="override SHEET_ID")
    args = cli.parse_args()

    settings = get_settings()
    sheet_id = args.sheet_id or settings.sheet_id
    if not sheet_id:
        print("No SHEET_ID set.")
        print("Create a Google Sheet, share it with the service account as Editor,")
        print("then put its id in .env (SHEET_ID=...) or pass --sheet-id.")
        return 2

    try:
        client = create_gspread_client(settings)
    except Exception as exc:
        print(f"Credentials error: {exc}")
        return 1

    email = get_service_account_email(settings)
    try:
        spreadsheet = client.open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001 - show the real Google error
        print(f"Could not open {sheet_id}: {exc}")
        if email:
            print(f"Share the sheet with {email} as Editor.")
        return 1

    try:
        worksheet = spreadsheet.worksheet(settings.sheet_tab)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=settings.sheet_tab, rows=1000, cols=len(LEAD_HEADERS)
        )
        print(f"Created tab {settings.sheet_tab!r}.")

    worksheet.update(
        range_name="A1:J1",
        values=[list(LEAD_HEADERS)],
        value_input_option=ValueInputOption.raw,
    )
    worksheet.freeze(rows=1)

    metadata = spreadsheet.fetch_sheet_metadata()
    tab_id = sheet_id_from_metadata(metadata, settings.sheet_tab)
    if tab_id is not None:
        spreadsheet.batch_update({"requests": conditional_format_requests(tab_id)})
        print("Applied URGENT/Completed conditional formatting.")

    print(f"Ready: {spreadsheet.title} -> {settings.sheet_tab}")
    print(f"Headers: {', '.join(LEAD_HEADERS[:8])} (A-H owned by the app)")
    print(f"URL: https://docs.google.com/spreadsheets/d/{spreadsheet.id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SheetError as exc:
        print(f"Sheets error: {exc}")
        raise SystemExit(1) from exc
