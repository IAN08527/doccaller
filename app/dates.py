"""Deterministic resolution of relative date phrases.

The LLM is good at names and services but unreliable at calendar arithmetic
(``parso`` became yesterday in live testing). Parser output is corrected here so
date maths never depends on the model.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

_DAY_AFTER_TOMORROW: tuple[str, ...] = (
    "day after tomorrow",
    "parso",
    "parson",
    "परसों",
    "परवा",
)

_TOMORROW: tuple[str, ...] = (
    "tomorrow",
    "kal",
    "कल",
    "उद्या",
)

_TODAY: tuple[str, ...] = (
    "today",
    "aaj",
    "आज",
)

_YESTERDAY: tuple[str, ...] = (
    "yesterday",
    "काल",
)

# Weekday tokens in English, Hinglish, Hindi, and Marathi (0 = Monday).
_WEEKDAYS: tuple[tuple[str, int], ...] = (
    ("monday", 0),
    ("somvar", 0),
    ("सोमवार", 0),
    ("tuesday", 1),
    ("mangalvar", 1),
    ("मंगलवार", 1),
    ("मंगळवार", 1),
    ("wednesday", 2),
    ("budhvar", 2),
    ("बुधवार", 2),
    ("thursday", 3),
    ("guruvar", 3),
    ("गुरुवार", 3),
    ("friday", 4),
    ("shukravar", 4),
    ("शुक्रवार", 4),
    ("saturday", 5),
    ("shanivar", 5),
    ("शनिवार", 5),
    ("sunday", 6),
    ("ravivar", 6),
    ("itvar", 6),
    ("रविवार", 6),
)


def resolve_relative_date(text: str, today: date) -> date | None:
    """Return the date named by a relative phrase, or ``None`` if there is none.

    Weekday names always point forward: "Monday" on a Monday means next week.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()

    if _contains(normalized, _DAY_AFTER_TOMORROW):
        return today + timedelta(days=2)

    weekday = _weekday(normalized)
    if weekday is not None:
        ahead = (weekday - today.weekday()) % 7
        return today + timedelta(days=ahead or 7)

    if _contains(normalized, _TOMORROW):
        return today + timedelta(days=1)
    if _contains(normalized, _TODAY):
        return today
    if _contains(normalized, _YESTERDAY):
        return today - timedelta(days=1)
    return None


def _weekday(text: str) -> int | None:
    for token, index in _WEEKDAYS:
        if _token_present(text, token):
            return index
    return None


def _contains(text: str, tokens: tuple[str, ...]) -> bool:
    return any(_token_present(text, token) for token in tokens)


def _token_present(text: str, token: str) -> bool:
    escaped = re.escape(token)
    if token.isascii():
        return re.search(rf"(?<![a-z]){escaped}(?![a-z])", text) is not None
    # Devanagari words take suffixes (सोमवार -> सोमवारी), so only guard the
    # character before the token: "निकलवाना" must not match "कल".
    return re.search(rf"(?<![\u0900-\u097F]){escaped}", text) is not None
