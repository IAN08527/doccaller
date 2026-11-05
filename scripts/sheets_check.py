"""Live Google Sheets check: find the staff sheet and verify its structure.

    uv run python scripts/sheets_check.py            # discover shared sheets
    uv run python scripts/sheets_check.py --sheet-id <id>

Without ``SHEET_ID`` the script lists every spreadsheet the service account can
see, which is the quickest way to confirm the sheet was shared with it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import gspread  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.sheets import (  # noqa: E402
    LEAD_HEADERS,
    SheetError,
    create_gspread_client,
    get_service_account_email,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    cli = argparse.ArgumentParser(description="Check the Google Sheets handoff target.")
    cli.add_argument("--sheet-id", default="", help="override SHEET_ID")
    args = cli.parse_args()

    settings = get_settings()
    try:
        client = create_gspread_client(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not authenticate: {exc}")
        return 1

    email = get_service_account_email(settings)

    sheet_id = args.sheet_id or settings.sheet_id
    if not sheet_id:
        print("Service account connected (no SHEET_ID set)\n")
        try:
            files = client.list_spreadsheet_files()
        except Exception as exc:  # noqa: BLE001 - Drive API is often disabled
            print(f"Could not list sheets: {exc}")
            print("\nListing needs the Drive API; writing does not.")
            if email:
                print(f"Open the clinic sheet and share it with {email}")
            print("as Editor, then set SHEET_ID to the id in its URL.")
            return 1
        if not files:
            print("No spreadsheets are shared with this service account.")
            if email:
                print(f"Share the clinic sheet with {email} as Editor.")
            return 1
            return 1
        print("Spreadsheets visible to the service account:")
        for entry in files:
            print(f"  {entry['name']}  id={entry['id']}")
        print("\nSet SHEET_ID to one of the ids above (or pass --sheet-id).")
        return 0

    try:
        spreadsheet = client.open_by_key(sheet_id)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not open sheet {sheet_id}: {exc}")
        return 1

    print(f"Spreadsheet: {spreadsheet.title} ({sheet_id})")
    titles = [worksheet.title for worksheet in spreadsheet.worksheets()]
    print(f"Tabs: {', '.join(titles)}")

    try:
        worksheet = spreadsheet.worksheet(settings.sheet_tab)
    except gspread.WorksheetNotFound:
        print(f"Tab {settings.sheet_tab!r} not found; it is created on first write.")
        return 0

    header = worksheet.row_values(1)
    rows = len(worksheet.col_values(1))
    print(f"Tab {worksheet.title!r}: {max(rows - 1, 0)} data row(s)")
    print(f"Header: {header}")
    if tuple(header[: len(LEAD_HEADERS)]) != LEAD_HEADERS:
        print("Header does not match this app's layout (safe: app writes A-H only).")
        return 0
    print("Header matches the app layout.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SheetError as exc:
        print(f"Sheets error: {exc}")
        raise SystemExit(1) from exc
