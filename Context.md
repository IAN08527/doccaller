# Context

## Last Updated
2026-11-04 — Frontend decided: one plain static chat page (`index.html` + `styles.css` + `app.js`) served by the app at `/ui/`. No framework, no build step, no CORS, no new API endpoints. Next: verify the page end to end against the real pipeline.

## Frontend: Single Static Demo Page (decided 2026-11-04)
Purpose is to exercise and demo the backend, not to be a product UI.
- **Location:** `app/static/` in this repo, mounted by FastAPI at `/ui/` (root `/` redirects there). Same origin as the API, so no CORS middleware is needed.
- **Stack:** exactly three files — `index.html`, `styles.css`, `app.js`. No React/Next.js, no bundler, no npm.
- **No hardcoded caller data:** the phone number is typed into an empty input; there is no default or prefilled number anywhere.
- **No typing/loading caption:** the send control is simply disabled while a request is in flight. No "typing…" indicator.
- **Scope explicitly excluded:** `GET /session`, a reset endpoint, conversation history restore, and CORS. Refreshing the page starts a new demo; re-running with the same number hits the backend's 12-hour dedupe by design.
- **Contract used:** `POST /missed-call` for the trigger and outreach copy, `POST /chat` for text and base64 `audio/webm` voice notes; render `reply`, `buttons`, `state`, `lead_status`, `alert`, `sheet_row`, `sheet_sync` as-is.
- **Visual direction:** deliberately minimal WhatsApp-like chat — header, transcript, composer, mic button. No design system or token work.


## Last Completed
- **Phase 2 — Static chat demo page (2026-11-04)**
  - Added `app/static/index.html`, `styles.css`, `app.js` (plain HTML/CSS/JS, no framework or build step).
  - Mounted at `/ui/` in `app/main.py`; `/` redirects there. Same origin, so no CORS and no new API endpoints.
  - Flow: caller phone input (empty, no default) → Simulate missed call → outreach bubble → text or recorded `audio/webm` voice note → reply + Confirm/Edit chips → `state` / `lead_status` / sheet pills → emergency alert banner.
  - Added an origin guard: if the page is opened from a static preview/file server, it explains that it must be opened at `http://127.0.0.1:8000/ui/` instead of showing a bare `404`.
  - Removed default phone numbers from `scripts/live_demo.py` (`--phone` now required), `scripts/mock_missed_call.sh`, and `scripts/mock_chat.sh` (phone is a required argument).
  - Reconciled the frontend contradiction in the docs: `docs/architecture.md` §2/§4/§11, `Context.md`, and `AGENTS.md` now all describe one static page served by the app.
  - Verified: static assets serve 200, `ruff` clean, `mypy` clean, `pytest` 246 passed, `node --check app.js` OK. Version `0.7.0`.
- **Phase 1 — External channel removal (2026-11-04)**
  - Deleted the third-party voice/WhatsApp webhook router and its channel adapter module.
  - Removed the webhook router include and outbound-send block from `app/main.py`; `/missed-call` and `/chat` are the only entrypoints again.
  - Dropped the vendor credentials and tunnel URL from `app/config.py` and the startup banner in `app/logging_config.py`.
  - Cleared the vendor/tunnel keys from `.env`; `.env.example` never had them.
  - Uninstalled the vendor SDK from the environment; `pyproject.toml` / `uv.lock` were already clean.
  - Repo-wide search for the vendor name now returns no code, config, or dependency hits.
  - Version bumped to `0.6.0` (later `0.7.0` with the demo page). `ruff`, `mypy`, and the full pytest suite re-verified after removal.
  - Test data remains reset: `doccaller.db` wiped and Google Sheet `Leads` cleared back to the header row.
- **Live Google Sheets Verification**
  - Connected human-owned spreadsheet (`1bffH5d7Z9GVnXiUbr_xj3uK8dZkEwOk3lzVWUCgbMZM`) with service account `docchill@vibeforge-507713.iam.gserviceaccount.com`.
  - Sheet provisioned with headers + conditional formatting (URGENT red, Completed grey).
  - All credentials (Groq + Google Service Account JSON) consolidated directly into `.env` as `GOOGLE_SERVICE_ACCOUNT_JSON`.
  - Deleted standalone `callagent.json`.
  - Next.js-style route logging and business logging added via `app/logging_config.py`.
- **Phase 0 — Foundation**
  - `pyproject.toml` (uv, FastAPI, Pydantic, PyYAML, tzdata, httpx, gspread; dev: pytest, mypy, ruff), `uv.lock`
  - `.gitignore`, `.env.example`, `config.yaml` (realistic clinic placeholders)
  - `app/config.py`, `app/models.py`, `app/db.py` (SQLite `sessions` + `seen_messages`), `app/channels/base.py`, `app/main.py`
- **Phase 1 — Core engine**
  - `app/emergency.py`, `app/validator.py`, `app/reply_builder.py` (EN/HI/MR), `app/engine.py` state machine
- **Phase 2 — Groq AI integration**
  - `app/groq.py` — shared auth headers, exponential backoff on 429/5xx, error mapping
  - `app/parser.py` — `openai/gpt-oss-20b` JSON-mode extraction, runtime date injection, schema validation, one JSON retry
  - `app/stt.py` — `whisper-large-v3-turbo` transcription with dental/relative-date prompt
  - `app/pipeline.py` — composes STT → parser → engine; failures route to `Needs Manual Review`
- **Phase 3 — Endpoints + Sheets**
  - `app/main.py` — `POST /missed-call`, `POST /chat`, `GET /health`; overridable dependencies
  - `app/api_models.py`, `app/phone.py` (E.164), `app/sheets.py` (upsert + formatting), `app/channels/web.py`
  - `scripts/mock_missed_call.sh`, `scripts/mock_chat.sh`
- **Phase 4 — Fixtures, jobs, hardening**
  - `tests/fixtures/extraction_cases.json` — 33 cases (EN/Hinglish/HI/MR + 7 emergencies)
  - `tests/fixtures/emergency_phrases.json` — 17 emergency + 9 routine phrases
  - `app/jobs.py` — 2h `No Reply` sweep, 90-day retention (SQLite + sheet column H), idempotent
  - `app/scheduler.py` — in-process maintenance loop, started from the FastAPI lifespan
  - `app/db.py` — `list_sessions`, `purge_transcripts`, `clear_transcripts` (late-message guard), `delete_seen_before`
  - `app/sheets.py` — retry on 429/5xx, `clear_transcript`, cached worksheet
  - `app/main.py` — cached Sheets client, `jobs` health field, media-type plumbing, version 0.4.0
  - `app/dates.py` — **deterministic relative-date resolution** (kal/parso/parवा/weekdays, EN/HI/MR/Hinglish)
  - `app/parser.py` — coerces `is_emergency: null`, keeps names in their original script, corrects dates from text, recovers `Other` services
  - `scripts/groq_eval.py` — live corpus eval with `--only` filter; `scripts/groq_smoke.py`; `scripts/live_demo.py`
  - `scripts/sheets_check.py`, `scripts/provision_sheet.py` — Sheets diagnostics/provisioning
- **Live verification (2026-11-03)**
  - `scripts/groq_smoke.py` → 5/5 samples OK
  - `scripts/groq_eval.py` → **104/104 fields (100.0%)**, 6/6 emergency (100%), PASS across 26 extraction cases + 7 emergency cases
  - `scripts/live_demo.py` → full missed-call → confirm conversation over real Groq, state `CONFIRMED`, lead `Needs Morning Call`
- **Offline verification:** `uv run pytest` → **246 passed**; `ruff` clean; `mypy app scripts` clean (28 files, post-cleanup).

## In Progress
- Manual browser verification of `/ui/` against the live pipeline (text, voice note, Confirm/Edit, emergency alert, sheet row).
- 2026-11-04: Phase 1 (webhook removal) and Phase 2 (static demo page) are code-complete and offline-verified. No other changes in flight.

## Next Steps
1. Open `http://127.0.0.1:8000/ui/` and run the happy path with a fresh caller number.
2. Test a voice note (mic permission), a correction via Edit, and an emergency phrase (red banner + URGENT row).
3. Confirm the Google Sheet updates in place and the demo can be re-run with a different number.
4. Final internship demo rehearsal and packaging.

## Blockers
- None. Backend is fully verified, operational, and database is clean.

## File Pointers
- `docs/PRD.md` — requirements, business rules, success criteria
- `docs/architecture.md` — stack, state machine, data model, test checklist
- `app/main.py` — HTTP endpoints (`/missed-call`, `/chat`, `/health`), `/ui` mount, sheet sync
- `app/static/` — static demo chat page (`index.html`, `styles.css`, `app.js`)
- `app/channels/web.py` — in-memory channel adapter for local testing
- `app/pipeline.py` — STT + parser + engine composition (start here for message flow)
- `app/reply_builder.py` — All multilingual humanized message templates (EN/HI/MR)
- `app/parser.py` — Groq extraction + deterministic text overrides
- `app/dates.py` — relative-date resolution (kal/parso/weekdays)
- `app/engine.py` — conversation state machine
- `app/jobs.py` / `app/scheduler.py` — sweep, retention, maintenance loop
- `app/sheets.py` — lead-row payload, upsert, formatting, transcript clearing
- `app/validator.py` — date/time/name/service rules
- `config.yaml` — clinic hours, services, emergency contacts, dedupe/cap
- `scripts/` — `groq_smoke`, `groq_eval`, `live_demo`, `sheets_check`, `provision_sheet`
- `tests/` — 246 passing tests, fixtures in `tests/fixtures/`
- `Context.md` — this file
