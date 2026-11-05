"""Live extraction evaluation against the multilingual corpus.

Run after adding GROQ_API_KEY to .env:

    uv run python scripts/groq_eval.py

The corpus lives in tests/fixtures/extraction_cases.json. Accuracy is computed
over name, service, date, and time window for non-emergency cases; emergency
recall is reported separately (the keyword backstop runs before the LLM).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Windows consoles default to cp1252, which cannot print Devanagari cases.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_settings, load_config  # noqa: E402
from app.emergency import is_emergency  # noqa: E402
from app.groq import GroqError  # noqa: E402
from app.models import ExtractedFields  # noqa: E402
from app.parser import GroqParser, ParserError  # noqa: E402
from app.validator import validate_fields  # noqa: E402

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "extraction_cases.json"


class Expected(BaseModel):
    patient_name: str | None
    service_needed: str | None
    preferred_date: date | None
    preferred_time_window: str | None
    is_emergency: bool


class ExtractionCase(BaseModel):
    id: str
    language: str
    text: str
    expected: Expected


class ExtractionFixture(BaseModel):
    reference_date: date
    cases: list[ExtractionCase]


def main() -> int:
    cli = argparse.ArgumentParser(description="Evaluate Groq extraction accuracy.")
    cli.add_argument("--min-accuracy", type=float, default=0.90)
    cli.add_argument("--delay", type=float, default=2.0, help="seconds between API calls")
    cli.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="ID",
        help="run only these case ids (e.g. --only mr-01)",
    )
    args = cli.parse_args()

    settings = get_settings()
    if not settings.groq_api_key:
        print("GROQ_API_KEY is not set. Add it to .env and retry.")
        return 2

    config = load_config()
    fixture = ExtractionFixture.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    now = datetime.combine(
        fixture.reference_date,
        clock_time(23, 15),
        tzinfo=ZoneInfo(config.clinic.timezone),
    )
    parser = GroqParser(settings, config)
    cases = [
        case for case in fixture.cases if not args.only or case.id in set(args.only)
    ]
    if not cases:
        print("No cases matched --only.")
        return 2

    field_matches = 0
    field_total = 0
    emergency_matches = 0
    emergency_total = 0
    failures = 0

    for index, case in enumerate(cases):
        if case.expected.is_emergency:
            emergency_total += 1
            if is_emergency(case.text):
                emergency_matches += 1
                print(f"OK    {case.id:<14} emergency keyword")
            else:
                failures += 1
                print(f"FAIL  {case.id:<14} emergency keyword missed")
            continue

        try:
            extracted = parser.parse(case.text, ExtractedFields(), now)
        except (ParserError, GroqError) as exc:
            field_total += 4
            failures += 1
            print(f"FAIL  {case.id:<14} parser error: {exc}")
            continue

        normalized = validate_fields(
            extracted, config, today=fixture.reference_date
        ).normalized
        mismatches = _compare(case.expected, normalized)
        field_total += 4
        field_matches += 4 - len(mismatches)
        if mismatches:
            failures += 1
            print(f"FAIL  {case.id:<14} " + "; ".join(mismatches))
        else:
            print(f"OK    {case.id:<14} {case.language}")

        if args.delay > 0 and index < len(cases) - 1:
            time.sleep(args.delay)

    accuracy = field_matches / field_total if field_total else 0.0
    recall = emergency_matches / emergency_total if emergency_total else 1.0
    print()
    print(
        f"Extraction: {field_matches}/{field_total} fields "
        f"({accuracy:.1%}) across {field_total // 4} cases"
    )
    print(f"Emergency:  {emergency_matches}/{emergency_total} ({recall:.1%})")
    passed = accuracy >= args.min_accuracy and recall >= 1.0
    print(f"Threshold:  {args.min_accuracy:.0%} extraction, 100% emergency -> "
          f"{'PASS' if passed else 'FAIL'}")
    if failures:
        print(f"Failed cases: {failures}")
    return 0 if passed else 1


def _compare(expected: Expected, actual: ExtractedFields) -> list[str]:
    mismatches: list[str] = []
    if not _same_text(expected.patient_name, actual.patient_name):
        mismatches.append(
            f"name: expected {expected.patient_name!r}, got {actual.patient_name!r}"
        )
    if not _same_text(expected.service_needed, actual.service_needed):
        mismatches.append(
            "service: expected "
            f"{expected.service_needed!r}, got {actual.service_needed!r}"
        )
    if expected.preferred_date != actual.preferred_date:
        mismatches.append(
            "date: expected "
            f"{expected.preferred_date!r}, got {actual.preferred_date!r}"
        )
    if not _same_text(
        expected.preferred_time_window, actual.preferred_time_window
    ):
        mismatches.append(
            "time: expected "
            f"{expected.preferred_time_window!r}, got {actual.preferred_time_window!r}"
        )
    return mismatches


def _same_text(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left == right
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


if __name__ == "__main__":
    raise SystemExit(main())
