"""Live end-to-end demo: missed call, one real message, then confirmation.

Runs the real app (Groq parser + Sheets when configured) in-process:

    uv run python scripts/live_demo.py
    uv run python scripts/live_demo.py --text "kal shaam ko cleaning chahiye"

Prints the sheet row afterwards when SHEET_ID is set.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings, load_config  # noqa: E402
from app.main import app  # noqa: E402
from app.sheets import LEAD_HEADERS  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_TEXT = "Hi, I'm Rahul Verma. I need a root canal on 4 Nov in the morning."


def main() -> int:
    cli = argparse.ArgumentParser(description="Run a live DocCaller conversation.")
    cli.add_argument("--phone", required=True, help="Caller number in E.164 or 10-digit form")
    cli.add_argument("--text", default=DEFAULT_TEXT)
    cli.add_argument("--confirm", default="yes")
    args = cli.parse_args()

    settings = get_settings()
    config = load_config()
    zone = ZoneInfo(config.clinic.timezone)
    stamp = datetime.now(zone).isoformat()

    print(f"Groq key: {'set' if settings.groq_api_key else 'MISSING'}")
    print(f"Sheets:   {'configured' if settings.sheet_id else 'not configured'}\n")

    with TestClient(app) as client:
        health = client.get("/health").json()
        print(f"Health:   {health}\n")

        missed = client.post(
            "/missed-call",
            json={
                "event": "missed_call",
                "caller_phone": args.phone,
                "timestamp": stamp,
            },
        )
        print(f"POST /missed-call -> {missed.status_code}")
        print(f"  {missed.json()}\n")
        if missed.status_code != 200:
            return 1

        message_id = f"live-{int(datetime.now(zone).timestamp())}"
        chat = client.post(
            "/chat",
            json={"phone": args.phone, "message_id": message_id, "text": args.text},
        )
        print(f"POST /chat ({args.text!r}) -> {chat.status_code}")
        print(f"  {chat.json()}\n")
        if chat.status_code != 200:
            return 1

        reply = client.post(
            "/chat",
            json={
                "phone": args.phone,
                "message_id": f"{message_id}-confirm",
                "text": args.confirm,
            },
        )
        print(f"POST /chat ({args.confirm!r}) -> {reply.status_code}")
        body = reply.json()
        print(f"  {body}\n")

        if settings.sheet_id and body.get("sheet_row"):
            _print_sheet_row(settings, body["sheet_row"])
    return 0


def _print_sheet_row(settings: object, row_number: int) -> None:
    from app.config import Settings
    from app.sheets import create_gspread_client

    assert isinstance(settings, Settings)
    client = create_gspread_client(settings)
    worksheet = client.open_by_key(settings.sheet_id).worksheet(settings.sheet_tab)
    values = worksheet.row_values(row_number)
    padded = values + [""] * (len(LEAD_HEADERS) - len(values))
    print(f"Sheet row {row_number}:")
    for header, value in zip(LEAD_HEADERS, padded, strict=False):
        print(f"  {header:<22} {value}")


if __name__ == "__main__":
    raise SystemExit(main())
