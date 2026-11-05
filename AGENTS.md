# Project Rules & Agent Context

## Core Architecture & Stack
<!-- Auto-populated tech stack, directory conventions, and design tokens -->

**Project:** DocCaller — Out-of-Hours Missed-Call Booking Assistant (localhost prototype for a Mumbai dental clinic). Captures after-hours missed calls, runs a multilingual (English / Hindi / Marathi / Hinglish) intake conversation over text or voice, and hands staff a structured lead in Google Sheets.

**Canonical docs:**
- `docs/PRD.md` — requirements, business rules, success criteria
- `docs/architecture.md` — stack, state machine, data model, repo layout, test checklist

**Stack (planned, per `docs/architecture.md`):**
- Runtime: Python 3 + FastAPI, bound to `127.0.0.1` only
- Channels: channel-adapter layer (`app/channels/`) — local chat channel via HTTP, plus one plain static demo page in `app/static/` served at `/ui/`
- STT: Groq `whisper-large-v3-turbo` (fallback `whisper-large-v3`)
- Parser: Groq `openai/gpt-oss-20b`, JSON mode, temperature 0 (Llama 3.1 8B left Groq's free tier on 2026-08-16)
- State: SQLite (`sessions`, `seen_messages`)
- Staff handoff: Google Sheets API via service account (`Leads` tab)
- Config: `config.yaml` (clinic hours, closed day, services, emergency contacts)
- Tests: pytest fixtures (~30 multilingual messages + emergency phrases) — installed; 139 tests green as of Phase 3

**Target repo layout:**
```text
app/
  main.py            # FastAPI: POST /missed-call, chat endpoint
  engine.py          # state machine, merge, validator, reply builder
  parser.py          # Groq Llama call + JSON schema validation
  stt.py             # Groq Whisper call
  emergency.py       # keyword lists + check (EN/HI/MR)
  sheets.py          # Google Sheets upsert
  db.py              # SQLite sessions + seen_messages
  channels/
    base.py          # ChannelAdapter interface
    web.py           # local chat channel backend
  static/            # demo chat page: index.html, styles.css, app.js
config.yaml
tests/               # message fixtures + emergency phrases
scripts/mock_missed_call.sh
.env.example
```

**Current state:** Phases 0-4 complete. Backend runs on FastAPI + SQLite with strict mypy + pytest (246 tests). Live Groq verified: parser smoke 5/5, corpus 99-100% field accuracy with 100% emergency recall, and a full missed-call → confirm conversation. Google Sheets handoff is code-complete and unit-tested but not live-verified: a service account has no Drive storage quota, so a human-owned spreadsheet must be shared with `docchill@vibeforge-507713.iam.gserviceaccount.com` and its id put in `SHEET_ID`. Maintenance jobs (2h no-reply sweep, 90-day retention) run in-process via the app lifespan.

**Conventions:**
- LLM extracts JSON only; all branching, validation, dates, and replies are deterministic code.
- Validate every external payload (parser output) against a schema in code; recompute `missing_fields` from data.
- Keep `.env` and service-account JSON out of the repo; never commit secrets.
- One lead row per session, updated in place via stored `sheet_row`; automation writes columns A–H only.
- Any pipeline failure ends in a visible `Needs Manual Review` row — never lose a lead.

## Global Development Directives
- Always enforce strict TypeScript types; no `any`.
- Implement all component states: loading, empty, error, active.
- Never leave mock static JSON or hardcoded arrays inside views.

Python equivalents for this repo: strict type hints everywhere, `mypy --strict`/`pyright` clean, no `Any` except at narrowed integration boundaries; every endpoint/handler covers idle, loading, empty, error, and active states in its responses; handlers must not serve hardcoded mock data.

## Learned Project Rules (Continuous Memory)
<!-- CRITICAL: OpenCode MUST append new user directives here -->
- **Permanent (2026-11-03):** Whenever the user gives a behavioral correction, preference, or "do/don't" during ANY conversation session in this project, immediately append or update that rule in this `## Learned Project Rules` section of `AGENTS.md`.
- **Only UI is the static demo page (2026-11-04):** The sole frontend is `app/static/` (plain `index.html` + `styles.css` + `app.js`, served by FastAPI at `/ui/`). No frameworks, bundlers, npm, design systems, or `docs/design.md`. It exists to exercise and demo the backend, not as a product surface.
- **No prefilled caller numbers (2026-11-04):** Never hardcode or default a phone number in the UI or scripts; the caller number is always user-supplied (scripts require it as an argument). Test fixtures are the only exception.
- **No extra API surface for the demo (2026-11-04):** Do not add CORS middleware, `GET /session`, reset endpoints, or history restore just to serve the page; it is same-origin and refresh starts a new demo.
- **No Telegram (2026-11-03):** The Telegram adapter is out of scope; do not scaffold it. WhatsApp/SMS remain documented production paths only.
- **Secrets never go in templates (2026-11-03):** `.env.example` must contain placeholders only. A real `gsk_` key was once pasted there; the rule is to keep all live keys in `.env` (gitignored) and service-account files out of the repo (`callagent.json` is ignored).
- **Dates are deterministic (2026-11-03):** The LLM must not do calendar arithmetic. Relative phrases (`kal`, `parso`, `परवा`, weekday names) resolve in `app/dates.py`, and `app/parser.py` corrects model dates from the message text. A missing date is never invented.
- **Google Sheets ownership (2026-11-03):** Service accounts have no Drive storage quota and cannot create spreadsheets (403 `The caller does not have permission`). The sheet must be created by a human account and shared with the service-account email as Editor; the app only writes to it.
- **Local HTTP only (2026-11-04):** The prototype is exercised through `POST /missed-call` + `POST /chat` on `127.0.0.1:8000`. Do not reintroduce telecom webhook adapters, vendor SDKs, or public tunnels — the third-party voice/WhatsApp integration was deliberately removed.
