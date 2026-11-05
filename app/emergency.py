"""Emergency keyword backstop. Runs before the LLM on every inbound message."""

from __future__ import annotations

import re
import unicodedata

# Multilingual phrases. Deliberately errs toward flagging: a missed emergency is
# worse than a false alarm that staff can dismiss.
_EMERGENCY_PHRASES: tuple[str, ...] = (
    # English
    "severe pain",
    "unbearable pain",
    "extreme pain",
    "heavy bleeding",
    "bleeding",
    "swelling",
    "swollen",
    "knocked out tooth",
    "tooth knocked out",
    "facial trauma",
    "face trauma",
    "trauma",
    "accident",
    "abscess",
    # Hinglish (romanised)
    "bahut dard",
    "bohot dard",
    "tez dard",
    "khoon",
    "sujan",
    "soojan",
    "daant toot",
    "daant toota",
    "tooth toot",
    "tooth tut",
    "chot lagi",
    # Hindi (Devanagari)
    "बहुत दर्द",
    "तेज़ दर्द",
    "तेज दर्द",
    "असहनीय दर्द",
    "खून",
    "रक्तस्राव",
    "सूजन",
    "दांत टूट",
    "दांत गिर",
    "चोट",
    "मवाद",
    # Marathi (Devanagari)
    "खूप वेदना",
    "खूप दुखत",
    "असह्य वेदना",
    "रक्त",
    "रक्तस्राव",
    "सूज",
    "दात तुट",
    "दात पड",
    "इजा",
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcan'?t stop bleeding\b"),
    re.compile(r"\bbleeding (a lot|heavily|profusely)\b"),
    re.compile(r"\b(tooth|teeth) (was |got )?(knocked|fell) out\b"),
    re.compile(r"\bswell(ing|en) (up|badly)\b"),
    re.compile(r"\bsevere (pain|toothache)\b"),
)


def normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", normalized).strip()


def is_emergency(text: str) -> bool:
    normalized = normalize(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _EMERGENCY_PHRASES):
        return True
    return any(pattern.search(normalized) for pattern in _PATTERNS)
