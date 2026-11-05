# Architecture: Out-of-Hours Missed-Call Booking Assistant (Localhost Build)

**Version:** 2.0 (draft) | **Date:** 3 Nov 2026
Companion to `PRD.md`. This covers how the system is built, not what it must do.
**Change from v1.0:** WhatsApp Cloud API removed. A channel adapter layer isolates messaging, with a local chat simulator as the default.

---

## 1. Design Principles

1. **LLM extracts, code decides.** The model only turns messy text into JSON. Branching, replies, dates, and validation are deterministic code.
2. **Never lose a lead.** Write a row at the missed call, then update it. Every failure ends in a row a human can see.
3. **Channel-agnostic core.** The conversation engine never knows whether it is talking to the browser simulator, Telegram, or (later) WhatsApp/SMS.
4. **Stateless logic, stateful store.** Conversation state lives in SQLite keyed by phone number.
5. **Everything runs on localhost.** No public URL, no paid services.

## 2. Stack

| Layer | Choice | Notes |
|---|---|---|
| Missed-call trigger | `POST /missed-call` endpoint + mock script/UI button | Real telephony provider is a later swap |
| Channel (default) | Local demo chat page: one static `index.html` + `styles.css` + `app.js`, no framework or build step, served by FastAPI at `/ui/` | Text input, mic recording via browser `MediaRecorder`, tap buttons |
| Channel (optional) | ~~Telegram bot~~ | Out of scope for this build (dropped). WhatsApp/SMS remain the production path only |
| STT | Groq `whisper-large-v3-turbo` | Test Hindi/Marathi. Fall back to `whisper-large-v3` if weak |
| Parser | Groq `openai/gpt-oss-20b`, JSON mode + JSON schema, temp 0 | Extraction only. Official replacement for Llama 3.1 8B, which left Groq's free tier on 2026-08-16 |
| App / orchestrator | **Option A:** small Python service (FastAPI). **Option B:** n8n in Docker | Recommended: Python for easier testing and version control. Logic below is identical for both |
| State store | SQLite (one file) | Sessions and dedupe records |
| Staff UI | Google Sheet via service account | Only interface staff see |
| Alerts | Log output + visible banner in simulator UI | Email/WhatsApp alerts are production items |

## 3. System Diagram

```text
 [Mock trigger: curl / UI button]
            │  POST /missed-call
            ▼
┌──────────────────────────── App (localhost) ─────────────────────────────────┐
│ [A] Missed-call handler                                                      │
│     validate number → dedupe (12h) → daily cap → upsert Session + Sheet row  │
│     (status "No Reply") → send outreach via Channel Adapter                  │
│                                                                              │
│ [B] Inbound handler  ◀── Channel Adapter (web chat | Telegram)               │
│     dedupe message id → load Session                                         │
│        ├─ text ─────────────────────────────┐                                │
│        ├─ audio → Groq Whisper ─────────────┤                                │
│        └─ other → "please send text/voice"  │                                │
│                                             ▼                                │
│     [C] Emergency keyword check ──yes──▶ emergency reply + URGENT row + alert│
│                    │no                                                       │
│                    ▼                                                         │
│     [D] Groq Llama parser (today's date injected at runtime)                 │
│                    ▼                                                         │
│     [E] Merge into Session (new non-null overwrite, old kept)                │
│                    ▼                                                         │
│     [F] Deterministic validator (date, closed day, hours, name)              │
│                    ▼                                                         │
│     [G] State machine ──▶ ask missing / confirm buttons / save               │
│                                                                              │
│ [H] Error branch: any failure → row "Needs Manual Review" + alert            │
│ [I] Scheduled: 2h no-reply sweep, optional morning digest                    │
└───────────────┬──────────────────────┬───────────────────────┬───────────────┘
                ▼                      ▼                       ▼
        Channel Adapter           Groq API              Google Sheets API
   (web chat / Telegram)       (STT + parser)             (Leads tab)
```

## 4. Channel Adapter Interface

All channels implement the same small contract, so swapping to WhatsApp or SMS later touches only one file.

```python
class ChannelAdapter:
    def send_text(self, user_id: str, text: str) -> None: ...
    def send_buttons(self, user_id: str, text: str, buttons: list[str]) -> None: ...
    # Inbound: adapter calls engine.handle_message(msg)

# Normalised inbound message
{
  "user_id": "+9198XXXXXXXX",     # phone number (web chat: chosen in the UI; Telegram: mapped chat id)
  "message_id": "unique-id",
  "type": "text | audio | other",
  "text": "…",                    # when type == text
  "audio_bytes": b"…",            # when type == audio (ogg/opus from Telegram, webm from browser)
  "timestamp": "2026-11-03T23:20:00+05:30"
}
```

Notes:
- **Web chat:** the page is served by the app itself (same origin, so no CORS). It has a "caller phone" field with no prefilled number and a "Simulate missed call" button, then behaves like a chat window. Recorded audio is uploaded as `audio/webm`; Groq's Whisper endpoint accepts webm.
- **Telegram:** run with long polling, so there is no webhook or public URL. A phone number is not known from a Telegram chat, so the demo maps the chat to a test phone number (typed once, or via Telegram's "share contact" button).
- **Buttons:** on channels without buttons, the adapter falls back to text ("Reply YES to confirm").

## 5. Conversation State Machine

```text
NEW ──missed call──▶ OUTREACH_SENT ──reply──▶ COLLECTING ──all valid──▶ AWAIT_CONFIRM
                          │                      │  ▲                        │
                          │ 2h silence           │  └── correction ◀─────────┤
                          ▼                      │ 3 rounds exhausted         │ confirm
                       NO_REPLY                  ▼                            ▼
                                            INCOMPLETE                   CONFIRMED
 Any state ──emergency──▶ EMERGENCY (reply sent, lead flagged URGENT)
 Any state ──error──────▶ MANUAL_REVIEW
```

SQLite `sessions` table:

| Column | Example |
|---|---|
| phone (PK) | `+9198XXXXXXXX` |
| state | `COLLECTING` |
| fields (JSON) | `{"patient_name": null, "service_needed": "root canal", "preferred_date": "2026-11-04", "preferred_time_window": null}` |
| followup_rounds | `1` |
| sheet_row | `42` |
| last_message_at | `2026-11-03T23:20:00+05:30` |
| transcripts (JSON) | `["…"]` |
| last_outreach_at | for the 12h dedupe |

Plus a `seen_messages` table (message_id) for idempotency.

## 6. Component Specs

### 6.1 Missed-call endpoint
```json
POST /missed-call
{"event": "missed_call", "caller_phone": "+9198XXXXXXXX", "timestamp": "2026-11-03T23:15:00+05:30"}
```
Checks: E.164 normalisation, reject withheld numbers, 12-hour dedupe, daily outreach cap. A shared-secret header is optional on localhost, but keep the check in code so it is ready for production.

### 6.2 Whisper step
- `POST https://api.groq.com/openai/v1/audio/transcriptions`, model `whisper-large-v3-turbo`
- `language` unset for auto-detect (mixed Hindi/Marathi/English)
- `prompt`: short vocabulary hint (dental terms, "kal", "parso", "subah", "shaam")
- Empty or very short transcript: ask the user to resend or type.

### 6.3 Parser step
- Model `openai/gpt-oss-20b`, temp 0, `response_format: json_object` (JSON schema where supported).
- **Inject today's date and weekday at runtime.** Pass current session fields so the model only fills gaps.
- Output keys: `patient_name`, `service_needed`, `preferred_date`, `preferred_time_window`, `missing_fields`, `is_emergency`.
- Validate against a JSON schema in code. On failure, retry once, then error branch.
- Recompute `missing_fields` in code from null values; do not trust the model's list.
- If relative dates ("kal", "parso", "Monday ko") prove unreliable in testing, have the model return a hint and resolve the date in code.

System prompt core:
```text
You are the intake parser for a Mumbai dental clinic.
Today is {weekday}, {date} (IST).
Known so far: {current_fields_json}
From the new message, extract: patient_name, service_needed, preferred_date (YYYY-MM-DD), preferred_time_window, is_emergency.
Rules: never guess a name; use null for anything not stated; emergencies = severe pain, bleeding, swelling, knocked-out tooth.
Return ONLY a JSON object.
```

### 6.4 Validator
- Date is today or later, within 30 days, not the weekly off day.
- Time window inside clinic hours.
- Name non-empty and not a placeholder.
- Failures produce a targeted re-ask.

### 6.5 Reply builder
Pure string templates (English plus Hindi variants if detected): emergency, ask-missing (one consolidated question), confirmation summary with buttons, unsupported input, final acknowledgement ("Your request is noted. Our team will call you to confirm.").

### 6.6 Emergency path
1. Keyword/regex list (English/Hindi/Marathi) runs **before** the LLM. The LLM flag is a second signal.
2. Reply with the on-call number and hospital guidance.
3. Upsert the sheet row as `URGENT`.
4. Raise an alert (log line plus red banner in the simulator).
5. Keep the session open so the patient can still give booking details.

## 7. Data Model: Google Sheet

Tab `Leads`:

| Col | Header | Sample |
|---|---|---|
| A | Timestamp (IST) | `03/11/2026 23:42:15` |
| B | Caller Phone | `+91XXXXXXXXXX` |
| C | Patient Name | `Rahul Verma` |
| D | Service Needed | `Root Canal / Pain Checkup` |
| E | Requested Slot | `2026-11-04 (10:00 AM - 12:00 PM)` |
| F | Status | `Needs Morning Call` |
| G | Urgent? | `Yes / No` |
| H | Raw Transcript | audit text |
| I | Staff Checkbox | `[ ] Callback Completed` |
| J | Staff Notes | never overwritten by automation |

Status values: `No Reply`, `Incomplete - Call Back`, `Needs Morning Call`, `URGENT`, `Needs Manual Review`, `Completed`.

Implementation notes:
- One row per session. Update in place using the stored `sheet_row`; never append twice.
- Conditional formatting: URGENT red, Completed grey.
- Automation writes only columns A-H; staff own I-J.
- Use fake data for demos and share the sheet only with named accounts.

## 8. Security & Privacy (localhost scope)

- Keep `.env` (Groq key, Telegram token, service account JSON path) out of git; add to `.gitignore`.
- Bind the app to `127.0.0.1` only.
- Fake patient data in demos. For real use, see PRD section 8.
- Retention job clears transcripts after the configured period.

## 9. Reliability & Error Handling

| Failure | Behaviour |
|---|---|
| Groq rate limit / 5xx | Retry with backoff (3 attempts), then error branch |
| Whisper empty/garbled | Ask user to resend or type |
| Parser invalid JSON | Retry once, then error branch |
| Sheets API failure | Retry; keep payload in SQLite; alert if still failing |
| Duplicate inbound message | Ignored via `seen_messages` |
| App crash/restart | State is in SQLite, so sessions resume |

Error branch always: write raw input to the sheet, set `Needs Manual Review`, raise an alert.

## 10. Cost

| Item | Cost |
|---|---|
| Hosting | ₹0 (your machine) |
| Groq Whisper + Llama | ₹0 on free tier. Paid is roughly ₹0.02 per lead (original estimate) |
| Channels (web chat, Telegram) | ₹0 |
| Google Sheets API | ₹0 |
| **Total for the prototype** | **~₹0** |

Production adds telephony (~₹750/month), WhatsApp or SMS fees, and a VPS (~₹750/month). Re-verify all prices at that time.

## 11. Suggested Repo Layout (Python option)

```text
dental-intake/
├── app/
│   ├── main.py            # FastAPI: /missed-call, /chat, /health, static /ui mount
│   ├── engine.py          # state machine, merge, validator, reply builder
│   ├── parser.py          # Groq Llama call + schema validation
│   ├── stt.py             # Groq Whisper call
│   ├── emergency.py       # keyword lists + check
│   ├── sheets.py          # Google Sheets upsert
│   ├── db.py              # SQLite sessions + seen_messages
│   ├── static/            # demo chat page: index.html, styles.css, app.js
│   └── channels/
│       ├── base.py        # ChannelAdapter interface
│       └── web.py         # local chat channel backend (HTTP)
├── config.yaml            # hours, closed days, services, emergency contact
├── tests/                 # ~30 message fixtures + emergency phrases
├── scripts/mock_missed_call.sh
└── .env.example
```

## 12. Test & Go-Live Checklist

**Setup**
- [ ] Groq key works for both Whisper and Llama
- [ ] Service account can write to the sheet
- [ ] Config file holds hours, closed day, services, emergency contact

**Functional tests**
- [ ] Missed-call mock produces outreach in the simulator
- [ ] Hindi, Marathi, Hinglish text and voice notes
- [ ] Relative dates ("kal", "parso", "Monday ko") and past/closed-day dates
- [ ] Name given first, name missing, patient name different from caller
- [ ] Emergency phrases in all three languages
- [ ] User edits details after the confirmation prompt
- [ ] Repeated missed calls (dedupe), silent user (`No Reply`), image/sticker input
- [ ] Duplicate message delivery
- [ ] Kill and restart the app mid-conversation, session resumes

**Demo readiness**
- [ ] Demo script covers happy path, missing info, emergency, correction
- [ ] Sheet shows formatted rows for each case
- [ ] ~~Optional: Telegram adapter working from a phone~~ (dropped from scope)
