"""Relative-date resolution tests (English, Hinglish, Hindi, Marathi)."""

from __future__ import annotations

from datetime import date

import pytest

from app.dates import resolve_relative_date

# Tuesday, 3 November 2026.
TODAY = date(2026, 11, 3)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("kal shaam ko cleaning chahiye", date(2026, 11, 4)),
        ("कल सुबह रूट कैनाल करवाना है", date(2026, 11, 4)),
        ("उद्या सकाळी तपासणी हवी आहे", date(2026, 11, 4)),
        ("tomorrow afternoon please", date(2026, 11, 4)),
        ("parso subah cleaning", date(2026, 11, 5)),
        ("परसों दोपहर में सफाई", date(2026, 11, 5)),
        ("परवा दुपारी सफाई", date(2026, 11, 5)),
        ("day after tomorrow evening", date(2026, 11, 5)),
        ("aaj shaam", date(2026, 11, 3)),
        ("आज शाम", date(2026, 11, 3)),
        ("today 4 pm", date(2026, 11, 3)),
        ("yesterday evening", date(2026, 11, 2)),
    ],
)
def test_relative_day_phrases(text: str, expected: date) -> None:
    assert resolve_relative_date(text, TODAY) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Monday ko appointment", date(2026, 11, 9)),
        ("सोमवार को अपॉइंटमेंट", date(2026, 11, 9)),
        ("सोमवारी अपॉइंटमेंट हवी आहे", date(2026, 11, 9)),
        ("Friday evening please", date(2026, 11, 6)),
        ("शुक्रवार संध्याकाळी", date(2026, 11, 6)),
        ("Wednesday morning", date(2026, 11, 4)),
        ("रविवार सकाळी", date(2026, 11, 8)),
    ],
)
def test_weekday_names_point_forward(text: str, expected: date) -> None:
    assert resolve_relative_date(text, TODAY) == expected


def test_weekday_on_the_same_day_means_next_week() -> None:
    monday = date(2026, 11, 9)
    assert resolve_relative_date("Monday ko", monday) == date(2026, 11, 16)


@pytest.mark.parametrize(
    "text",
    [
        "4 Nov morning",
        "7 November, 12:00-14:00",
        "मुझे दांत निकलवाना है",
        "what are your clinic hours?",
        "",
    ],
)
def test_absolute_or_missing_dates_are_left_alone(text: str) -> None:
    assert resolve_relative_date(text, TODAY) is None


def test_kal_does_not_match_inside_another_word() -> None:
    assert resolve_relative_date("kalash decoration", TODAY) is None
