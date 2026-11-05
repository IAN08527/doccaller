# PRD: Out-of-Hours Missed-Call Booking Assistant (Localhost Build)

**Client:** Mumbai dental clinic
**Version:** 2.0 (draft) | **Date:** 3 Nov 2026
**Scope:** Localhost prototype / demo. No paid messaging API. Budget: ~₹0 (only free-tier AI usage).
**Change from v1.0:** WhatsApp Cloud API removed. Messaging now goes through a pluggable channel adapter, with a local chat simulator as the default.

---

## 1. Problem

Patients who call the clinic after hours (or when no one picks up) hang up and often book elsewhere. The clinic has no way to capture those leads, and staff have no structured list to work from the next morning.

## 2. Goal

Show that a missed or after-hours call can become a structured, staff-ready booking request with no human involvement, running entirely on a local machine.

**Non-goals (v1):**
- Real phone-network integration, WhatsApp, or SMS delivery
- Real-time calendar booking or double-booking prevention
- Payments, reminders, patient records
- Clinical advice
- Replacing the receptionist

## 3. Users

| User | Need |
|---|---|
| **Patient (simulated)** | Ask for an appointment by text or voice note, in English, Hindi, Marathi or Hinglish. |
| **Receptionist** | Open one Google Sheet in the morning, see who to call back, for what, and when. |
| **Clinic owner / demo audience** | See the full flow work end to end, including emergencies and corrections. |

## 4. User Journey

1. A missed-call event is triggered (a webhook call from a mock/test script, or a real provider later).
2. The system "sends" an outreach message to the patient through the channel (in the demo, it appears in the local chat window).
3. Patient replies by text or voice note (voice is recorded in the browser, or sent as a Telegram voice message if that adapter is enabled).
4. The system extracts the details and asks only for what is missing.
5. When complete, the system shows a summary with Confirm / Edit.
6. On confirmation, a row is written to the Google Sheet as "Needs Morning Call".
7. Receptionist calls the patient back and ticks the checkbox.

## 5. Functional Requirements

### 5.1 Missed-call capture (P0)
- Expose a `POST /missed-call` endpoint taking caller number and timestamp.
- Ship a mock trigger (curl script and a "Simulate missed call" button in the local UI).
- Ignore withheld/invalid numbers (log them instead).
- De-duplicate: one outreach per number per 12 hours.

### 5.2 Outreach message (P0)
- Sent immediately through the active channel adapter.
- States who we are, that the clinic is closed, what we need (name, service, preferred time), that text or voice both work, and that a human will confirm.
- Must not promise a confirmed booking. Use "request" language.
- Includes a one-line privacy notice.

### 5.3 Channels (P0)
The conversation logic is channel-independent. Supported adapters:

| Adapter | Status | Notes |
|---|---|---|
| **Local web chat simulator** | Default, required | Browser page on localhost, text input plus microphone recording, shows bot replies and tap buttons |
| **Telegram bot** | Out of scope | Dropped for this build. The channel adapter keeps the door open for a later adapter |
| WhatsApp Cloud API / SMS | Out of scope | Documented as the production path only |

### 5.4 Input handling (P0)
- Accept text and voice. Voice is transcribed to text before processing.
- Support English, Hindi, Marathi, and mixed Hinglish.
- Other input types: reply once asking for text or voice, and log the attempt.

### 5.5 Information extraction (P0)

| Field | Rule |
|---|---|
| Patient name | Must be explicitly stated. Never guessed. May differ from the caller. |
| Service / issue | Free text mapped to a clinic service category. |
| Preferred date | Resolved to an absolute IST date. Not in the past, not a closed day. |
| Preferred time window | Within clinic hours. |

- Details accumulate across messages. A reply of "Rahul Verma" adds to earlier details, never replaces them.
- Missing fields trigger one consolidated follow-up question.
- Maximum 3 follow-up rounds, then save as `Incomplete - Call Back`.

### 5.6 Emergency handling (P0)
- Severe pain, bleeding, swelling, facial trauma, or knocked-out tooth are emergencies.
- The assistant immediately shows the on-call doctor's number and hospital guidance, **and** still logs the lead as URGENT and alerts staff (for the demo: a visible alert in the UI and a highlighted sheet row).
- Detection uses the AI model plus a keyword backstop (English/Hindi/Marathi).
- No diagnosis or treatment advice.

### 5.7 Confirmation (P0)
- Show collected details with Confirm and Edit buttons.
- Also accept typed "yes", "haan", "ok", "sahi hai", and similar.
- Corrections re-run extraction on top of existing data.

### 5.8 Staff handoff (P0)
- Leads go to a Google Sheet (schema in Architecture doc).
- Status values: `No Reply`, `Incomplete - Call Back`, `Needs Morning Call`, `URGENT`, `Needs Manual Review`, `Completed`.
- A row is written at the missed call (`No Reply`) and updated as the chat progresses, so no caller is lost.
- Morning digest (P2): one summary of counts and urgent items.

### 5.9 Fallbacks (P1)
- Any pipeline failure: save raw input with `Needs Manual Review` and show an owner alert in the logs/UI.
- No reply after 2 hours: row stays `No Reply`, no further messages.

## 6. Business Rules

- **Working hours / closed days:** configurable in one config file (default: open 9:00 AM, closed 8:00 PM; confirm weekly off day with clinic).
- **Language:** reply in the patient's language where practical; default English.
- **Appointment status:** every request is *unconfirmed* until staff call back. The assistant never says "booked".
- **Rate limit:** daily cap on outreach messages (protects against runaway loops and free-tier exhaustion).
- **Retention:** raw transcripts cleared after 90 days (confirm with clinic).

## 7. Success Criteria (demo)

| Metric | Target |
|---|---|
| Simulated missed call produces an outreach message | < 5 s |
| Complete flow from missed call to sheet row (happy path) | Works end to end |
| Extraction accuracy on test set of ~30 messages (name, service, slot) | ≥ 90% |
| Emergency test phrases in 3 languages | 100% flagged |
| Context merging across turns | Works in all multi-turn test cases |
| Cost of a full test run | ~₹0 (Groq free tier) |

## 8. Compliance & Risk

- **Privacy:** even as a prototype, use fake patient data in demos. For any real deployment, health data falls under India's DPDP Act 2023: disclose collection purpose, restrict sheet access, and note that voice/text is processed by Groq outside India.
- **Medical liability:** no clinical advice. Emergency message points to a human.
- **Misparse risk:** mitigated by the confirmation step and by storing the raw transcript.
- **Groq free-tier limits:** rate limits apply. Retry with backoff and keep test volume modest.
- **Prototype gap:** the demo does not prove deliverability to a real caller. A real deployment still needs a way to reach callers (see 10).

## 9. Assumptions & Dependencies

| # | Item | Needed for |
|---|---|---|
| 1 | Groq API key (free tier) | STT and parsing |
| 2 | Google account and a service account with access to one sheet | Staff handoff |
| 3 | Local machine able to run the orchestrator (n8n via Docker, or a small Python service) | Everything |
| 4 | Clinic details: name, hours, weekly off day, service list, on-call number, nearest hospital (placeholders are fine for the demo) | Reply copy and validation |
| 5 | Optional: Telegram bot token from BotFather | Phone demo |

## 10. Path to Production (out of scope, for reference)

To go live with real callers, three things replace the simulator:
1. **Telephony:** a virtual number with a missed-call webhook that includes the caller's number, plus call forwarding from the clinic's line.
2. **Outbound channel:** WhatsApp Cloud API (needs Meta verification and template approval, and a number not already on WhatsApp) or SMS (needs Indian DLT registration).
3. **Hosting:** a small public VPS with HTTPS so webhooks can reach it.

Because the logic sits behind a channel adapter, this swap changes the adapter and the hosting, not the conversation logic.

## 11. Build Plan (48 hours)

| When | Milestone |
|---|---|
| Hour 0-4 | Repo, config file, Groq keys, Google Sheet and service account, adapter interface |
| Hour 4-16 | Parser, merge logic, validator, state machine, emergency check |
| Hour 16-28 | Local chat channel (backend) with voice transcription, missed-call mock, sheet writer |
| Hour 28-40 | Test set (Hinglish, voice, relative dates, emergencies), error branch |
| Hour 40-48 | Demo script, documentation, polish |

## 12. Open Questions

1. Orchestrator: n8n (visual, Docker) or a small Python service (easier to test and version)?
2. Is the Telegram adapter wanted for a phone demo, or is the browser simulator enough?
3. Who is the on-call emergency contact shown in demos (placeholder number is fine)?
4. Service categories: scaling, root canal, braces, extraction, check-up, other?
