"""Phone normalisation tests."""

from __future__ import annotations

import pytest

from app.phone import normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9820012345", "+919820012345"),
        ("09820012345", "+919820012345"),
        ("+91 98200 12345", "+919820012345"),
        ("+91-98200-12345", "+919820012345"),
        ("0091 9820012345", "+919820012345"),
        ("+1 (415) 555-0100", "+14155550100"),
        ("  +44 20 7946 0958  ", "+442079460958"),
    ],
)
def test_valid_numbers_are_e164(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "withheld", "WITHHELD", "anonymous", "private", "abc", "+", "12345"],
)
def test_withheld_and_invalid_numbers_are_rejected(raw: str) -> None:
    assert normalize_phone(raw) is None
