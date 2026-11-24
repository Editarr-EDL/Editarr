import { createDecisionInspector } from "./replay-inspector.js";

const slots = ["baseline", "candidate"];
const labels = { baseline: "Baseline", candidate: "Candidate" };
const modelNames = {
  whisper: "Faster-Whisper",
  mlx_whisper: "MLX Whisper",
  apple_speech: "Apple Speech",
  "qwen3-vl-8b-instruct-4bit": "Qwen3-VL 8B · 4-bit",
  "qwen3-vl-4b-instruct-4bit": "Qwen3-VL 4B · 4-bit",
  "qwen35-9b-4bit": "Qwen3.5-9B · 4-bit",
};
const incomplete = {
  failed: "Failed",
  no_inputs: "No speech",
  partial: "Partial",
  unsupported: "Unavailable",
};

const settings = document.querySelector("#settings");
const fileInput = document.querySelector("#audio-file");
const audioPicker = document.querySelector("#audio-picker");
const audioName = document.querySelector("#audio-name");
const audioDescription = document.querySelector("#audio-description");
const uploadLabel = document.querySelector("#upload-label");
const audioPreview = document.querySelector("#audio-preview");
const localAudioForm = document.querySelector("#local-audio-form");
const audioPath = document.querySelector("#audio-path");
const audioPathError = document.querySelector("#audio-path-error");
const audioModel = document.querySelector("#audio-model");
const qwenModel = document.querySelector("#qwen-model");
const padding = document.querySelector("#padding");
const denyWords = document.querySelector("#deny-words");
const activity = document.querySelector("#activity");
const activityLabel = document.querySelector("#activity-label");
const progress = document.querySelector("#run-progress");
const pageError = document.querySelector("#page-error");
const comparisonStatus = document.querySelector("#comparison-status");
const comparisonNotes = document.querySelector("#comparison-notes");
const comparisonInspectHelp = document.querySelector("#comparison-inspect-help");
const comparisonTable = document.querySelector("#comparison-table-scroll");
const comparisonRows = document.querySelector("#comparison-rows");
const runCards = Object.fromEntries(slots.map((slot) => {
  const card = document.querySelector(`[data-slot="${slot}"]`);
  return [slot, {
    badge: card.querySelector("[data-run-status]"),
    summary: card.querySelector("[data-run-summary]"),
    button: card.querySelector("[data-run-button]"),
    report: card.querySelector("[data-report-link]"),
    findings: card.querySelector("[data-findings]"),
    list: card.querySelector("[data-finding-list]"),
  }];
}));

let state = null;
let pending = null;
let pollTimer;
let previewUrl;
let previewClip;
let comparing = false;
let inspecting = false;
let comparisonRequest = 0;
const settingsKey = "editarr.qwen-language-replay.settings";
const inspector = createDecisionInspector({
  request,
  formatTimestamp,
  modelLabel: (name) => modelNames[name] ?? name,
  onBusy(busy) {
    inspecting = busy;
    render();
  },
});

function setText(element, value) {
  if (element.textContent !== value) element.textContent = value;
}

function showError(message = "") {
  setText(pageError, message);
  pageError.hidden = !message;
}

function showPathError(message = "") {
  setText(audioPathError, message);
  audioPathError.hidden = !message;
  if (message) audioPath.setAttribute("aria-invalid", "true");
  else audioPath.removeAttribute("aria-invalid");
}

async function request(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const data = await response.json().catch(() => null);
  if (response.ok && data) return data;
  const detail = data?.detail;
  let message = `The replay server could not complete the request (${response.status}).`;
  if (typeof detail === "string") message = detail;
  if (Array.isArray(detail)) message = detail.map(({ msg }) => msg).join(". ");
  const error = new Error(message);
  error.status = response.status;
  throw error;
}

function populate(select, choices, selected) {
  const options = choices.map((value) => new Option(modelNames[value] ?? value, value));
  select.replaceChildren(...options);
  select.value = selected;
}

function restoreRunSettings(status) {
  const options = status.options;
  const previousRun = status.reports.candidate?.options ?? status.reports.baseline?.options ?? {};
  let draft;
  try {
    draft = JSON.parse(window.sessionStorage.getItem(settingsKey));
  } catch {
    // The page also works when browser storage is unavailable.
  }
  const preferred = { ...previousRun, ...draft };
  const preferredAudio = options.audio_models.includes(preferred.audio_model)
    ? preferred.audio_model : options.audio_model;
  const preferredQwen = options.qwen_models.includes(preferred.qwen_model)
    ? preferred.qwen_model : options.qwen_model;
  populate(audioModel, options.audio_models, preferredAudio);
  populate(qwenModel, options.qwen_models, preferredQwen);
  padding.value = preferred.audio_padding_seconds ?? "";
  denyWords.value = Array.isArray(preferred.deny_words)
    ? preferred.deny_words.join("\n") : preferred.deny_words ?? "";
}

function saveRunSettings() {
  try {
    window.sessionStorage.setItem(settingsKey, JSON.stringify({
      audio_model: audioModel.value,
      qwen_model: qwenModel.value,
      audio_padding_seconds: padding.value,
      deny_words: denyWords.value,
    }));
  } catch {
    // Selecting run options still works without browser storage.
  }
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} bytes`;
  const [size, unit] = bytes >= 1024 ** 2 ? [bytes / 1024 ** 2, "MiB"] : [bytes / 1024, "KiB"];
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(size)} ${unit}`;
}

function findingRange(finding) {
  const start = `${finding.start_seconds.toFixed(3)}s`;
  return finding.end_seconds == null ? start : `${start}–${finding.end_seconds.toFixed(3)}s`;
}

function renderFindings(card, findings) {
  const entries = findings.map((finding) => {
    const item = document.createElement("li");
    const title = document.createElement("b");
    title.textContent = finding.label || finding.subcategory;
    const metadata = document.createElement("span");
    const category = [finding.category, finding.subcategory].filter(Boolean).join("/");
    metadata.textContent = [findingRange(finding), category, finding.severity].filter(Boolean).join(" · ");
    item.append(title, metadata);
    return item;
  });
  card.list.replaceChildren(...entries);
  card.findings.hidden = entries.length === 0;
  if (!entries.length) card.findings.open = false;
}

function renderRun(slot, controlsDisabled) {
  const card = runCards[slot];
  const report = state?.reports[slot];
  const running = state?.active?.slot === slot;
  const error = state?.error?.slot === slot ? state.error.message : "";
  const clipReady = Boolean(state?.audio);
  const findings = report?.findings ?? [];
  const count = `${findings.length} ${findings.length === 1 ? "finding" : "findings"}`;
  let status = clipReady ? "Ready" : "Waiting";
  let summary = clipReady ? "Ready to run on this extract." : "Choose an audio extract to start.";
  let tone = clipReady ? "ready" : "waiting";
  if (running) {
    [status, summary, tone] = ["Running", "Replay in progress.", "running"];
  } else if (error || (report && report.status !== "complete")) {
    [status, summary, tone] = [incomplete[report?.status] ?? "Failed", error || "The run did not complete.", "error"];
    if (error && report?.status === "complete") summary += " The previous successful report is still shown.";
  } else if (report) {
    [status, summary, tone] = ["Complete", count, "complete"];
  }
  card.badge.dataset.state = tone;
  setText(card.badge, status);
  setText(card.summary, summary);
  card.button.disabled = controlsDisabled || !clipReady;
  card.report.hidden = running || !report;
  renderFindings(card, running ? [] : findings);
}

function render() {
  const clip = state?.audio;
  const running = state?.active;
  const uploadLimit = state?.options.upload_limit_bytes;
  const choosingAudio = pending === "audio";
  const controlsDisabled = !state || !!pending || !!running || inspecting;
  settings.disabled = controlsDisabled;
  audioPicker.classList.toggle("has-audio", !!clip);
  setText(audioName, clip?.name ?? "Choose an audio file");
  const emptyDescription = uploadLimit ? `Up to ${formatSize(uploadLimit)}` : "A short WAV works best";
  const description = clip ? formatSize(clip.size_bytes) : emptyDescription;
  const source = clip?.source === "local" ? "Local file · " : "";
  setText(audioDescription, choosingAudio ? "Loading extract…" : source + description);
  setText(uploadLabel, clip ? "Replace" : "Browse");
  fileInput.setAttribute("aria-label", clip ? "Replace audio file" : "Browse for an audio file");
  for (const slot of slots) renderRun(slot, controlsDisabled);

  activity.hidden = !running;
  if (running) {
    const match = running.message.match(/^\s*(\d+(?:\.\d+)?)%\s*(.+)/);
    const message = match ? `${match[2]} · ${match[1]}%` : running.message;
    setText(activityLabel, `${labels[running.slot]} · ${message}`);
    if (match) progress.value = Number(match[1]);
    else progress.removeAttribute("value");
  }
}

function formatTimestamp(seconds) {
  const milliseconds = Math.round(seconds * 1000);
  const hours = Math.floor(milliseconds / 3600000);
  const minutes = Math.floor(milliseconds / 60000) % 60;
  const wholeSeconds = Math.floor(milliseconds / 1000) % 60;
  const clock = [hours, minutes, wholeSeconds].map((part) => String(part).padStart(2, "0"));
  return `${clock.join(":")}.${String(milliseconds % 1000).padStart(3, "0")}`;
}

function renderComparison({ rows, notes, baseline_count, candidate_count }) {
  if (!Array.isArray(rows)) {
    throw new Error("Restart the replay server to load the comparison table.");
  }
  setText(comparisonStatus, rows.length
    ? `${baseline_count} baseline · ${candidate_count} candidate findings`
    : "Neither run flagged any phrases.");
  comparisonNotes.replaceChildren(...notes.map((message) => {
    const note = document.createElement("li");
    note.textContent = message;
    return note;
  }));
  comparisonNotes.hidden = notes.length === 0;

  const findings = document.createDocumentFragment();
  for (const findingRow of rows) {
    const { timestamp_seconds, baseline, candidate } = findingRow;
    const row = document.createElement("tr");
    const timestamp = document.createElement("th");
    timestamp.scope = "row";
    const inspect = document.createElement("button");
    inspect.type = "button";
    inspect.className = "comparison-inspect";
    inspect.textContent = formatTimestamp(timestamp_seconds);
    inspect.setAttribute("aria-label", `Inspect decision at ${inspect.textContent}`);
    inspect.addEventListener("click", () => inspector.open(findingRow));
    timestamp.append(inspect);
    row.append(timestamp);
    for (const finding of [baseline, candidate]) {
      const cell = document.createElement("td");
      if (finding) cell.textContent = finding.label || finding.subcategory;
      row.append(cell);
    }
    if (!baseline) row.dataset.only = "candidate";
    if (!candidate) row.dataset.only = "baseline";
    findings.append(row);
  }
  comparisonRows.replaceChildren(findings);
  comparisonTable.hidden = rows.length === 0;
  comparisonInspectHelp.hidden = rows.length === 0;
}

async function updateComparison(refresh = false) {
  const complete = slots.every((slot) => state?.reports[slot]?.status === "complete");
  if (!complete) {
    comparing = false;
    comparisonRequest += 1;
    comparisonNotes.hidden = true;
    comparisonTable.hidden = true;
    comparisonInspectHelp.hidden = true;
    setText(comparisonStatus, "Complete both runs to see what changed.");
    return;
  }
  if (comparing && !refresh) return;
  comparing = true;
  const requestId = ++comparisonRequest;
  setText(comparisonStatus, "Comparing runs…");
  try {
    const comparison = await request("/api/compare");
    if (requestId === comparisonRequest) renderComparison(comparison);
  } catch (error) {
    if (requestId === comparisonRequest) {
      comparisonNotes.hidden = true;
      comparisonTable.hidden = true;
      comparisonInspectHelp.hidden = true;
      setText(comparisonStatus, error.message);
    }
  }
}

function applyState(next) {
  if (!state) restoreRunSettings(next);
  const runFinished = Boolean(state?.active && !next.active);
  if (previewClip && (
    next.audio?.source === "local" || previewClip.name !== next.audio?.name ||
    previewClip.size_bytes !== next.audio?.size_bytes
  )) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = undefined;
    previewClip = undefined;
    audioPreview.removeAttribute("src");
    audioPreview.hidden = true;
  }
  state = next;
  render();
  void updateComparison(runFinished);
  clearTimeout(pollTimer);
  if (state.active) pollTimer = setTimeout(refreshStatus, 700);
}

async function refreshStatus() {
  try {
    applyState(await request("/api/status"));
    showError();
  } catch (error) {
    showError(`Could not load replay status. ${error.message}`);
    if (!state || state.active) pollTimer = setTimeout(refreshStatus, 2000);
  }
}

async function showActionError(error, display = showError) {
  if (error.status === 409) await refreshStatus();
  display(error.message);
}

function upload(file) {
  if (!file || settings.disabled) return;
  const uploadLimit = state.options.upload_limit_bytes;
  if (uploadLimit && file.size > uploadLimit) {
    showError(`Audio clips must be no larger than ${formatSize(uploadLimit)}.`);
    return;
  }
  void chooseAudio("/api/audio", { headers: { "X-Audio-Name": encodeURIComponent(file.name) }, body: file }, file);
}

async function chooseAudio(endpoint, options, previewFile) {
  if (settings.disabled) return;
  const hasReports = slots.some((slot) => state.reports[slot]);
  if (hasReports && !window.confirm("Replacing the audio extract clears both run reports. Replace it?")) return;
  pending = "audio";
  render();
  showError();
  showPathError();
  try {
    const next = await request(endpoint, { method: "PUT", ...options });
    if (previewFile) {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(previewFile);
      previewClip = next.audio;
      audioPreview.src = previewUrl;
      audioPreview.hidden = false;
    }
    applyState(next);
  } catch (error) {
    await showActionError(error, previewFile ? showError : showPathError);
  } finally {
    pending = null;
    render();
  }
}

async function run(slot) {
  if (runCards[slot].button.disabled) return;
  if (!padding.checkValidity()) {
    padding.closest("details").open = true;
    padding.reportValidity();
    return;
  }

  pending = "run";
  render();
  showError();
  const options = {
    slot,
    audio_model: audioModel.value,
    qwen_model: qwenModel.value,
    audio_padding_seconds: padding.value === "" ? undefined : padding.valueAsNumber,
    deny_words: denyWords.value.split(/[,\n]/).map((word) => word.trim()).filter(Boolean),
  };
  try {
    applyState(await request("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    }));
  } catch (error) {
    await showActionError(error);
  } finally {
    pending = null;
    render();
  }
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  fileInput.value = "";
  upload(file);
});

audioPath.addEventListener("input", () => showPathError());
localAudioForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!audioPath.value.trim()) {
    showPathError("Enter the path to an audio file.");
    return;
  }
  void chooseAudio("/api/audio-path", {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: audioPath.value }),
  });
});

audioPicker.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (!settings.disabled) audioPicker.classList.add("is-over");
});
audioPicker.addEventListener("dragleave", (event) => {
  if (!audioPicker.contains(event.relatedTarget)) audioPicker.classList.remove("is-over");
});
audioPicker.addEventListener("drop", (event) => {
  event.preventDefault();
  audioPicker.classList.remove("is-over");
  upload(event.dataTransfer.files[0]);
});

for (const slot of slots) runCards[slot].button.addEventListener("click", () => void run(slot));
for (const field of [audioModel, qwenModel, padding, denyWords]) {
  field.addEventListener("input", saveRunSettings);
}
void refreshStatus();
