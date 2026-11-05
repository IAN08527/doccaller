"""Live Groq smoke test. Requires GROQ_API_KEY in .env or the environment."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Windows consoles default to cp1252, which cannot print Devanagari samples.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_settings, load_config  # noqa: E402
from app.engine import merge_fields  # noqa: E402
from app.models import ExtractedFields  # noqa: E402
from app.parser import GroqParser, ParserError  # noqa: E402
from app.stt import Transcriber, TranscriptionError  # noqa: E402

SAMPLES: tuple[str, ...] = (
    "Hi, I'm Rahul Verma. I need a root canal on 4 Nov in the morning.",
    "kal shaam ko cleaning karwani hai",
    "मुझे दांत में बहुत दर्द है, कल सुबह appointment चाहिए",
    "मला उद्या सकाळी तपासणी हवी आहे",
    "Actually make it 5 Nov afternoon, name is Priya Sharma",
)


def main() -> int:
    cli = argparse.ArgumentParser(description="Smoke-test Groq parser and Whisper.")
    cli.add_argument("--audio", type=Path, help="optional audio file to transcribe")
    args = cli.parse_args()

    settings = get_settings()
    if not settings.groq_api_key:
        print("GROQ_API_KEY is not set. Add it to .env and retry.")
        return 2

    config = load_config()
    groq_parser = GroqParser(settings, config)
    now = datetime.now(ZoneInfo(config.clinic.timezone))

    merged = ExtractedFields()
    text_failures = 0
    for sample in SAMPLES:
        try:
            extracted = groq_parser.parse(sample, merged, now)
        except ParserError as exc:
            text_failures += 1
            print(f"FAIL  {sample}\n      {exc}")
            continue
        merged = merge_fields(merged, extracted)
        print(f"OK    {sample}\n      {extracted.model_dump_json()}")

    audio_failed = False
    if args.audio is not None:
        try:
            transcript = Transcriber(settings).transcribe(args.audio.read_bytes())
            print(f"STT   {args.audio.name}\n      {transcript or '(empty transcript)'}")
        except TranscriptionError as exc:
            audio_failed = True
            print(f"STT FAIL  {args.audio}: {exc}")

    print(f"\nParser: {len(SAMPLES) - text_failures}/{len(SAMPLES)} samples OK.")
    return 1 if text_failures or audio_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
