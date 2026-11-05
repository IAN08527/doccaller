"""Phone number normalisation for missed-call webhooks."""

from __future__ import annotations

import re

_WITHHELD = {
    "anonymous",
    "withheld",
    "private",
    "unknown",
    "blocked",
    "restricted",
    "unavailable",
}
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_SEPARATORS = re.compile(r"[\s\-().]+")


def normalize_phone(raw: str, default_country_code: str = "+91") -> str | None:
    """Return an E.164 number, or None for withheld/invalid input."""
    cleaned = _SEPARATORS.sub("", raw.strip())
    if not cleaned or cleaned.lower() in _WITHHELD:
        return None

    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"
    elif not cleaned.startswith("+"):
        if cleaned.isdigit() and len(cleaned) == 10:
            cleaned = f"{default_country_code}{cleaned}"
        elif cleaned.isdigit() and len(cleaned) == 11 and cleaned.startswith("0"):
            cleaned = f"{default_country_code}{cleaned[1:]}"
        else:
            cleaned = f"+{cleaned}"

    if not _E164.match(cleaned):
        return None
    return cleaned
