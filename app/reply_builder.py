"""Reply templates. Pure strings: no model calls, no side effects."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from app.config import AppConfig
from app.models import ExtractedFields
from app.validator import ValidationResult

Lang = Literal["en", "hi", "mr"]

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_MARATHI_HINTS = ("आहे", "नाही", "वेदना", "दात", "कृपया", "मला", "तुम्ही", "करा")
_HINDI_HINTS = ("है", "नहीं", "दर्द", "दांत", "कृपया", "मुझे", "आप", "करें")

_OUTREACH: dict[Lang, str] = {
    "en": (
        "Hi, this is {clinic}. We missed your call and the clinic is closed right now.\n"
        "To request an appointment, reply with your name, the service you need, and your "
        "preferred date and time.\n"
        "You can send text or a voice note. Our team will call you back to confirm - this is "
        "not a confirmed booking.\n"
        "{privacy}"
    ),
    "hi": (
        "नमस्ते, यह {clinic} है। आपका कॉल हमसे छूट गया और क्लिनिक अभी बंद है।\n"
        "अपॉइंटमेंट के लिए कृपया अपना नाम, ज़रूरी सेवा और पसंदीदा तारीख़ व समय भेजें।\n"
        "आप टेक्स्ट या वॉइस नोट भेज सकते हैं। हमारी टीम कॉल करके पुष्टि करेगी - यह बुकिंग "
        "पक्की नहीं है।\n"
        "{privacy}"
    ),
    "mr": (
        "नमस्कार, हे {clinic} आहे. तुमचा कॉल आम्हाला मिळाला नाही आणि क्लिनिक आत्ता बंद आहे.\n"
        "अपॉइंटमेंटसाठी कृपया तुमचे नाव, आवश्यक सेवा आणि पसंतीची तारीख व वेळ पाठवा.\n"
        "तुम्ही मजकूर किंवा व्हॉइस नोट पाठवू शकता. आमची टीम फोन करून पुष्टी करेल - हे बुकिंग "
        "निश्चित नाही.\n"
        "{privacy}"
    ),
}

_EMERGENCY: dict[Lang, str] = {
    "en": (
        "This sounds like a dental emergency. Please call our on-call doctor now: {number}.\n"
        "If you cannot reach them, go to {hospital}.\n"
        "I have flagged this as urgent for the clinic team. You can still reply with your "
        "name, service, and preferred time for a follow-up request."
    ),
    "hi": (
        "यह दांत की आपात स्थिति लगती है। कृपया अभी हमारे ऑन-कॉल डॉक्टर को कॉल करें: {number}।\n"
        "उनसे संपर्क न हो पाए तो {hospital} जाएं।\n"
        "मैंने इसे क्लिनिक टीम के लिए अर्जेंट चिह्नित कर दिया है। आप अपना नाम, सेवा और "
        "पसंदीदा समय भेजकर फॉलो-अप अनुरोध दे सकते हैं।"
    ),
    "mr": (
        "हे दातांची आपत्कालीन स्थिती वाटते. कृपया आत्ताच आमच्या ऑन-कॉल डॉक्टरांना कॉल करा: "
        "{number}.\n"
        "ते उपलब्ध नसल्यास {hospital} येथे जा.\n"
        "मी हे क्लिनिक टीमसाठी तातडीचे म्हणून नोंदवले आहे. तुम्ही तुमचे नाव, सेवा आणि "
        "पसंतीची वेळ पाठवून फॉलो-अप विनंती देऊ शकता."
    ),
}

_SIMPLE: dict[str, dict[Lang, str]] = {
    "unsupported": {
        "en": "Please send your details as text or a voice note.",
        "hi": "कृपया अपनी जानकारी टेक्स्ट या वॉइस नोट में भेजें।",
        "mr": "कृपया तुमची माहिती मजकूर किंवा व्हॉइस नोटमध्ये पाठवा.",
    },
    "incomplete": {
        "en": "I have saved what you shared. Our team will call you to complete the request.",
        "hi": "आपने जो जानकारी दी है वह सुरक्षित है। हमारी टीम अनुरोध पूरा करने के लिए कॉल करेगी।",
        "mr": "तुम्ही दिलेली माहिती जतन केली आहे. आमची टीम विनंती पूर्ण करण्यासाठी कॉल करेल.",
    },
    "final_ack": {
        "en": "Your request is noted. Our team will call you to confirm.",
        "hi": "आपका अनुरोध नोट कर लिया गया है। हमारी टीम पुष्टि के लिए कॉल करेगी।",
        "mr": "तुमची विनंती नोंदवली आहे. आमची टीम पुष्टीसाठी कॉल करेल.",
    },
    "error": {
        "en": "Sorry, something went wrong on our side. Your details are saved and our team "
        "will follow up.",
        "hi": "क्षमा करें, हमारी ओर से कुछ गड़बड़ हुई। आपकी जानकारी सुरक्षित है और हमारी टीम "
        "संपर्क करेगी।",
        "mr": "क्षमस्व, आमच्याकडून काहीतरी चूक झाली. तुमची माहिती जतन आहे आणि आमची टीम "
        "संपर्क करेल.",
    },
    "edit_prompt": {
        "en": "Sure - tell me what you would like to change.",
        "hi": "ठीक है - बताइए क्या बदलना है।",
        "mr": "ठीक आहे - काय बदलायचे ते सांगा.",
    },
}


def detect_language(text: str) -> Lang:
    if not _DEVANAGARI.search(text):
        return "en"
    lowered = text.lower()
    marathi_score = sum(hint in lowered for hint in _MARATHI_HINTS)
    hindi_score = sum(hint in lowered for hint in _HINDI_HINTS)
    return "mr" if marathi_score > hindi_score else "hi"


class ReplyBuilder:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def outreach(self, lang: Lang = "en") -> str:
        return _OUTREACH[lang].format(
            clinic=self._config.clinic.name,
            privacy=self._config.privacy_notice,
        )

    def ask_missing(self, validation: ValidationResult, lang: Lang = "en") -> str:
        joined = "; ".join(self._ask_items(validation, lang))
        name = validation.normalized.patient_name
        if "patient_name" in validation.errors:
            name = None
        greeting_name = name.split()[0] if name else None

        if lang == "hi":
            prefix = f"धन्यवाद, {greeting_name}।" if greeting_name else "धन्यवाद।"
            return f"{prefix} मुझे अभी यह चाहिए: {joined}।"
        if lang == "mr":
            prefix = f"धन्यवाद, {greeting_name}." if greeting_name else "धन्यवाद."
            return f"{prefix} मला अजून हे हवे: {joined}."
        prefix = f"Thanks, {greeting_name}." if greeting_name else "Thanks."
        return f"{prefix} I still need: {joined}."

    def confirmation(self, fields: ExtractedFields, lang: Lang = "en") -> str:
        name = fields.patient_name or "-"
        service = fields.service_needed or "-"
        window = fields.preferred_time_window or "-"
        date_text = self._format_date(fields.preferred_date, lang)
        if lang == "hi":
            return (
                "कृपया अपना अनुरोध पुष्टि करें:\n"
                f"- नाम: {name}\n"
                f"- सेवा: {service}\n"
                f"- पसंदीदा समय: {date_text} ({window})\n"
                "पुष्टि के लिए YES लिखें, बदलने के लिए EDIT लिखें।"
            )
        if lang == "mr":
            return (
                "कृपया तुमची विनंती पुष्टी करा:\n"
                f"- नाव: {name}\n"
                f"- सेवा: {service}\n"
                f"- पसंतीची वेळ: {date_text} ({window})\n"
                "पुष्टीसाठी YES लिहा, बदलण्यासाठी EDIT लिहा."
            )
        return (
            "Please confirm your request:\n"
            f"- Name: {name}\n"
            f"- Service: {service}\n"
            f"- Preferred slot: {date_text} ({window})\n"
            "Reply YES to confirm or EDIT to change anything."
        )

    def emergency(self, lang: Lang = "en") -> str:
        return _EMERGENCY[lang].format(
            number=self._config.emergency.on_call_number,
            hospital=self._config.emergency.hospital,
        )

    def unsupported(self, lang: Lang = "en") -> str:
        return _SIMPLE["unsupported"][lang]

    def incomplete(self, lang: Lang = "en") -> str:
        return _SIMPLE["incomplete"][lang]

    def final_ack(self, lang: Lang = "en") -> str:
        return _SIMPLE["final_ack"][lang]

    def error(self, lang: Lang = "en") -> str:
        return _SIMPLE["error"][lang]

    def edit_prompt(self, lang: Lang = "en") -> str:
        return _SIMPLE["edit_prompt"][lang]

    def _ask_items(self, validation: ValidationResult, lang: Lang) -> list[str]:
        services = ", ".join(self._config.services)
        windows = ", ".join(self._config.hours.slot_windows)
        closed = ", ".join(self._config.hours.closed_days)

        items: list[str] = []
        for field_name in validation.missing:
            items.append(self._field_label(field_name, lang, services, windows, closed))
        for field_name, error in validation.errors.items():
            items.append(self._error_label(field_name, error, lang, services, windows, closed))
        return _dedupe(items)

    def _field_label(
        self, field_name: str, lang: Lang, services: str, windows: str, closed: str
    ) -> str:
        closed_suffix_en = f" ({closed} is closed)" if closed else ""
        closed_suffix_hi = f" ({closed} को क्लिनिक बंद है)" if closed else ""
        closed_suffix_mr = f" ({closed} रोजी क्लिनिक बंद आहे)" if closed else ""
        labels: dict[str, dict[Lang, str]] = {
            "patient_name": {
                "en": "your full name",
                "hi": "आपका पूरा नाम",
                "mr": "तुमचे पूर्ण नाव",
            },
            "service_needed": {
                "en": f"the service you need ({services})",
                "hi": f"ज़रूरी सेवा ({services})",
                "mr": f"आवश्यक सेवा ({services})",
            },
            "preferred_date": {
                "en": f"your preferred date{closed_suffix_en}",
                "hi": f"पसंदीदा तारीख़{closed_suffix_hi}",
                "mr": f"पसंतीची तारीख{closed_suffix_mr}",
            },
            "preferred_time_window": {
                "en": f"your preferred time ({windows})",
                "hi": f"पसंदीदा समय ({windows})",
                "mr": f"पसंतीची वेळ ({windows})",
            },
        }
        return labels[field_name][lang]

    def _error_label(
        self,
        field_name: str,
        error: str,
        lang: Lang,
        services: str,
        windows: str,
        closed: str,
    ) -> str:
        if error in {"name_placeholder", "name_too_short", "name_has_digits"}:
            return self._field_label("patient_name", lang, services, windows, closed)
        if error == "service_unrecognised":
            return self._field_label("service_needed", lang, services, windows, closed)
        if error == "time_outside_hours":
            return self._field_label("preferred_time_window", lang, services, windows, closed)
        if error == "date_past":
            return {
                "en": "a preferred date from today onwards",
                "hi": "आज या उसके बाद की तारीख़",
                "mr": "आज किंवा त्यानंतरची तारीख",
            }[lang]
        if error == "date_too_far":
            return {
                "en": "a preferred date within the next 30 days",
                "hi": "अगले 30 दिनों के भीतर की तारीख़",
                "mr": "पुढील ३० दिवसांतली तारीख",
            }[lang]
        if error == "date_closed":
            return {
                "en": f"another date ({closed} is closed)",
                "hi": f"कोई और तारीख़ ({closed} को क्लिनिक बंद है)",
                "mr": f"दुसरी तारीख ({closed} रोजी क्लिनिक बंद आहे)",
            }[lang]
        return self._field_label(field_name, lang, services, windows, closed)

    @staticmethod
    def _format_date(value: date | None, lang: Lang) -> str:
        if value is None:
            return "-"
        if lang == "en":
            return value.strftime("%a, %d %b %Y")
        return value.strftime("%d/%m/%Y")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique
