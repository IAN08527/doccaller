"use strict";

const els = {
  clinic: document.getElementById("clinic-name"),
  connection: document.getElementById("connection-line"),
  dot: document.getElementById("connection-dot"),
  setup: document.getElementById("setup-panel"),
  phone: document.getElementById("phone"),
  simulate: document.getElementById("simulate"),
  serveHint: document.getElementById("serve-hint"),
  sessionBar: document.getElementById("session-bar"),
  sessionPhone: document.getElementById("session-phone"),
  newCaller: document.getElementById("new-caller"),
  chat: document.getElementById("chat"),
  empty: document.getElementById("empty-state"),
  alert: document.getElementById("alert-banner"),
  alertText: document.getElementById("alert-text"),
  alertClose: document.getElementById("alert-close"),
  meta: document.getElementById("meta"),
  statePill: document.getElementById("state-pill"),
  leadPill: document.getElementById("lead-pill"),
  sheetPill: document.getElementById("sheet-pill"),
  composer: document.getElementById("composer"),
  message: document.getElementById("message"),
  send: document.getElementById("send"),
  mic: document.getElementById("mic"),
  recordTime: document.getElementById("record-time"),
};

const state = {
  phone: null,
  active: false,
  busy: false,
  recording: false,
  recorder: null,
  stream: null,
  chunks: [],
  recordStart: 0,
  timer: null,
};

const STATE_LABELS = {
  OUTREACH_SENT: "Outreach sent",
  COLLECTING: "Collecting",
  AWAIT_CONFIRM: "Awaiting confirm",
  CONFIRMED: "Confirmed",
  EMERGENCY: "Emergency",
  INCOMPLETE: "Incomplete",
  NO_REPLY: "No reply",
  MANUAL_REVIEW: "Manual review",
};

/* ── API ─────────────────────────────────────────────────────────────────── */

async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail =
      data && typeof data.detail === "string"
        ? data.detail
        : `Request failed (${response.status})`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }

  return data ?? {};
}

function errorMessage(error) {
  if (error && error.status === 404) {
    return "Request failed (404). This page is not talking to DocCaller — open http://127.0.0.1:8000/ui/ instead of a static preview.";
  }
  if (error && typeof error.status === "number") {
    return `Backend rejected the request: ${error.message}`;
  }
  return "Cannot reach the backend. Start the app on 127.0.0.1:8000 and try again.";
}

function newMessageId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/* ── Transcript ──────────────────────────────────────────────────────────── */

function scrollToLatest() {
  els.chat.scrollTop = els.chat.scrollHeight;
}

function hideEmptyState() {
  if (els.empty && els.empty.isConnected) {
    els.empty.remove();
  }
}

function appendMessage(node) {
  hideEmptyState();
  els.chat.append(node);
  scrollToLatest();
}

function addBot(text, buttons) {
  if (!text && (!buttons || buttons.length === 0)) return;

  const bubble = document.createElement("div");
  bubble.className = "msg bot";

  if (text) {
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    bubble.append(paragraph);
  }

  if (Array.isArray(buttons) && buttons.length > 0) {
    const row = document.createElement("div");
    row.className = "chips";

    for (const label of buttons) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = label;
      chip.addEventListener("click", () => {
        if (state.busy) return;
        row.querySelectorAll("button").forEach((button) => {
          button.disabled = true;
        });
        addUser(label);
        void sendPayload({ text: label });
      });
      row.append(chip);
    }

    bubble.append(row);
  }

  appendMessage(bubble);
}

function addUser(text) {
  const bubble = document.createElement("div");
  bubble.className = "msg user";
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  bubble.append(paragraph);
  appendMessage(bubble);
}

function addSystem(text, isError = false) {
  const note = document.createElement("p");
  note.className = isError ? "system error" : "system";
  note.textContent = text;
  appendMessage(note);
}

/* ── Status ──────────────────────────────────────────────────────────────── */

function humanizeState(value) {
  return STATE_LABELS[value] || value.replaceAll("_", " ").toLowerCase();
}

function updateMeta(data) {
  if (!data) return;

  let changed = false;

  if (data.state) {
    els.statePill.textContent = humanizeState(data.state);
    els.statePill.hidden = false;
    changed = true;
  }

  if (data.lead_status) {
    els.leadPill.textContent = `Lead: ${data.lead_status}`;
    els.leadPill.hidden = false;
    changed = true;
  }

  if (typeof data.sheet_row === "number") {
    els.sheetPill.textContent = `Sheet row ${data.sheet_row}`;
    els.sheetPill.hidden = false;
    changed = true;
  } else if (data.sheet_sync === "unconfigured") {
    els.sheetPill.textContent = "Sheet not configured";
    els.sheetPill.hidden = false;
    changed = true;
  } else if (data.sheet_sync === "pending") {
    els.sheetPill.textContent = "Sheet sync pending";
    els.sheetPill.hidden = false;
    changed = true;
  }

  if (changed) {
    els.meta.hidden = false;
  }
}

function showAlert(text) {
  if (!text) return;
  els.alertText.textContent = text;
  els.alert.hidden = false;
  scrollToLatest();
}

function hideAlert() {
  els.alert.hidden = true;
  els.alertText.textContent = "";
}

/* ── Session lifecycle ───────────────────────────────────────────────────── */

function enterSession(phone) {
  state.phone = phone;
  state.active = true;
  els.sessionPhone.textContent = phone;
  els.setup.hidden = true;
  els.sessionBar.hidden = false;
  els.message.disabled = false;
  els.send.disabled = false;
  els.mic.disabled = false;
  els.message.focus();
}

function resetSession() {
  stopRecording();
  hideAlert();
  state.phone = null;
  state.active = false;
  state.chunks = [];

  els.chat.replaceChildren();
  if (els.empty) {
    els.chat.append(els.empty);
  }
  els.meta.hidden = true;
  els.statePill.hidden = true;
  els.leadPill.hidden = true;
  els.sheetPill.hidden = true;
  els.sessionBar.hidden = true;
  els.setup.hidden = false;
  els.phone.value = "";
  els.message.value = "";
  els.message.style.height = "auto";
  els.message.disabled = true;
  els.send.disabled = true;
  els.mic.disabled = true;
  els.phone.focus();
}

function setBusy(busy) {
  state.busy = busy;
  els.simulate.disabled = busy;
  els.message.disabled = busy || !state.active;
  els.send.disabled = busy || !state.active;
  els.mic.disabled = busy || !state.active || state.recording;
  els.composer.setAttribute("aria-busy", String(busy));
}

/* ── Missed call ─────────────────────────────────────────────────────────── */

async function simulateMissedCall() {
  if (state.busy) return;

  const phone = els.phone.value.trim();
  if (!phone) {
    addSystem("Enter a caller phone number first.", true);
    els.phone.focus();
    return;
  }

  setBusy(true);
  try {
    const data = await postJson("/missed-call", {
      event: "missed_call",
      caller_phone: phone,
    });

    if (data.status === "outreach_sent") {
      enterSession(phone);
      addBot(data.reply || "Outreach sent.");
      updateMeta(data);
      return;
    }

    if (data.status === "duplicate") {
      addSystem(
        "This number already received an outreach in the last 12 hours. Use a different caller number for a new demo run."
      );
      return;
    }

    if (data.status === "capped") {
      addSystem(
        "The daily outreach cap is reached. Raise daily_outreach_cap in config.yaml or try again tomorrow.",
        true
      );
      return;
    }

    if (data.status === "ignored") {
      addSystem("That number is invalid or withheld, so no outreach was sent.", true);
      return;
    }

    addSystem(`Unexpected backend status: ${data.status}`, true);
  } catch (error) {
    addSystem(errorMessage(error), true);
  } finally {
    setBusy(false);
  }
}

/* ── Chat ────────────────────────────────────────────────────────────────── */

async function sendPayload(payload) {
  if (!state.phone || state.busy) return;

  setBusy(true);
  try {
    const data = await postJson("/chat", {
      phone: state.phone,
      message_id: newMessageId(),
      ...payload,
    });

    if (data.status === "duplicate") {
      addSystem("That message was already processed.");
    } else {
      addBot(data.reply, data.buttons);
    }

    if (data.alert) {
      showAlert(data.alert);
    }

    updateMeta(data);
  } catch (error) {
    if (error && error.status === 404) {
      resetSession();
      addSystem("No open session for this number. Start again with a missed call.", true);
    } else {
      addSystem(errorMessage(error), true);
    }
  } finally {
    setBusy(false);
  }
}

async function sendTypedMessage() {
  if (!state.active || state.busy) return;

  const text = els.message.value.trim();
  if (!text) return;

  els.message.value = "";
  autoGrow();
  addUser(text);
  await sendPayload({ text });
}

/* ── Voice notes ─────────────────────────────────────────────────────────── */

function pickMimeType() {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function updateRecordTimer() {
  const seconds = Math.floor((Date.now() - state.recordStart) / 1000);
  els.recordTime.textContent = formatDuration(seconds);
}

function formatDuration(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

async function startRecording() {
  if (!navigator.mediaDevices || typeof MediaRecorder === "undefined") {
    addSystem("This browser cannot record audio. Please type instead.", true);
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = pickMimeType();
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

    state.stream = stream;
    state.recorder = recorder;
    state.chunks = [];
    state.recordStart = Date.now();
    state.recording = true;

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) {
        state.chunks.push(event.data);
      }
    });
    recorder.addEventListener("stop", () => {
      void finishRecording(stream);
    });

    recorder.start();
    els.mic.classList.add("recording");
    els.mic.setAttribute("aria-label", "Stop recording");
    els.mic.disabled = false;
    els.recordTime.hidden = false;
    updateRecordTimer();
    state.timer = window.setInterval(updateRecordTimer, 250);
  } catch {
    addSystem("Microphone access was blocked. Allow it in the browser and try again.", true);
    cleanupRecorder();
  }
}

function stopRecording() {
  if (state.recorder && state.recorder.state !== "inactive") {
    state.recorder.stop();
  }
}

function cleanupRecorder() {
  state.recording = false;
  if (state.timer !== null) {
    window.clearInterval(state.timer);
    state.timer = null;
  }
  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
  }
  els.mic.classList.remove("recording");
  els.mic.setAttribute("aria-label", "Record a voice note");
  els.recordTime.hidden = true;
  els.mic.disabled = state.busy || !state.active;
}

async function finishRecording(stream) {
  const recorder = state.recorder;
  const seconds = Math.max(1, Math.round((Date.now() - state.recordStart) / 1000));
  const mimeType = recorder?.mimeType || "audio/webm";
  const blob = new Blob(state.chunks, { type: mimeType });

  cleanupRecorder();
  state.recorder = null;
  state.chunks = [];
  stream.getTracks().forEach((track) => track.stop());

  if (!state.active) {
    return;
  }

  if (blob.size === 0) {
    addSystem("The voice note was empty. Please record again.", true);
    return;
  }

  let base64;
  try {
    base64 = await blobToBase64(blob);
  } catch {
    addSystem("Could not read the recording. Please try again.", true);
    return;
  }

  addUser(`Voice note · ${formatDuration(seconds)}`);
  await sendPayload({
    audio_base64: base64,
    audio_media_type: mimeType.split(";")[0],
  });
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const result = String(reader.result || "");
      resolve(result.slice(result.indexOf(",") + 1));
    });
    reader.addEventListener("error", () => {
      reject(reader.error || new Error("read failed"));
    });
    reader.readAsDataURL(blob);
  });
}

/* ── Composer ────────────────────────────────────────────────────────────── */

function autoGrow() {
  els.message.style.height = "auto";
  els.message.style.height = `${Math.min(els.message.scrollHeight, 120)}px`;
}

/* ── Health ──────────────────────────────────────────────────────────────── */

async function checkHealth() {
  try {
    const response = await fetch("/health", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("health failed");
    const data = await response.json();

    if (typeof data.clinic === "string" && data.clinic.trim()) {
      els.clinic.textContent = data.clinic;
      document.title = `${data.clinic} — Missed-call chat`;
    }
    els.dot.className = "dot ok";
    els.connection.textContent = "Connected · local demo";
    els.serveHint.hidden = true;
  } catch {
    els.dot.className = "dot bad";
    els.connection.textContent = "Backend unreachable";
    els.serveHint.hidden = false;
  }
}

/* ── Wiring ──────────────────────────────────────────────────────────────── */

els.simulate.addEventListener("click", () => {
  void simulateMissedCall();
});

els.phone.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    void simulateMissedCall();
  }
});

els.newCaller.addEventListener("click", resetSession);
els.alertClose.addEventListener("click", hideAlert);

els.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  void sendTypedMessage();
});

els.message.addEventListener("input", autoGrow);
els.message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void sendTypedMessage();
  }
});

els.mic.addEventListener("click", () => {
  if (state.recording) {
    stopRecording();
  } else {
    void startRecording();
  }
});

window.addEventListener("pagehide", () => {
  if (state.recorder && state.recorder.state !== "inactive") {
    state.recorder.stop();
  }
});

void checkHealth();
window.setInterval(() => {
  void checkHealth();
}, 20000);
