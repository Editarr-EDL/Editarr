const runLabels = { baseline: "Baseline", candidate: "Candidate" };

function write(element, text) {
  element.textContent = text;
}

function listItems(list, items) {
  list.replaceChildren(...items.map((text) => {
    const item = document.createElement("li");
    write(item, text);
    return item;
  }));
}

function post(request, path, payload) {
  return request(`/api/inspect/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function describeFinding(finding) {
  const description = finding.label || finding.subcategory || "Unlabeled finding";
  const metadata = [finding.subcategory, finding.severity].filter(Boolean);
  return metadata.length ? `${description} · ${metadata.join(" · ")}` : description;
}

function describeInput(input, timedWords, formatTimestamp) {
  const text = Array.isArray(input.words)
    ? input.words.map((word) => word.text).join("").trim()
    : [input.word, input.context].filter(Boolean).join(" · ");
  if (!timedWords?.length) return `${input.id + 1}. ${text}`;
  const start = formatTimestamp(Math.min(...timedWords.map((word) => word.start)));
  const end = formatTimestamp(Math.max(...timedWords.map((word) => word.end)));
  return `${start}–${end} · ${text}`;
}

/** Inspect one comparison row without altering the two saved reports. */
export function createDecisionInspector({ request, formatTimestamp, modelLabel, onBusy }) {
  const dialog = document.querySelector("#decision-inspector");
  const sideButtons = [...dialog.querySelectorAll("[data-inspector-slot]")];
  const find = (name) => dialog.querySelector(`#inspector-${name}`);
  const timestamp = find("timestamp");
  const observation = find("observation");
  const status = find("status");
  const error = find("error");
  const evidencePanel = find("context");
  const evidence = find("evidence");
  const transcript = find("transcript");
  const inputs = find("inputs");
  const response = find("response");
  const attempts = find("attempts");
  const askForm = find("ask");
  const askEvidence = find("ask-evidence");
  const question = find("question");
  const answerPanel = find("answer-panel");
  const answer = find("answer");
  const suggestionPanel = find("suggestion-panel");
  const suggestion = find("suggestion");
  const apply = find("apply");
  const rerunForm = find("rerun");
  const instructions = find("instructions");
  const reset = find("reset");
  const resultPanel = find("result");
  const resultStatus = find("result-status");
  const resultOriginal = find("result-original");
  const resultFindings = find("findings");
  const resultResponse = find("result-response");

  let row;
  let slot;
  let target;
  let context;
  let requestNumber = 0;
  let working = false;
  let suggestedEdit = "";
  let testedRerunId = null;
  let defaultQuestion;

  function showStatus(message = "") {
    write(status, message);
    status.hidden = !message;
  }

  function showError(message = "") {
    write(error, message);
    error.hidden = !message;
    if (message) error.scrollIntoView?.({ block: "nearest" });
  }

  function syncControls() {
    for (const button of sideButtons) button.disabled = working;
    for (const control of [...askForm.elements, ...rerunForm.elements]) {
      control.disabled = working || !context;
    }
    apply.disabled ||= !suggestedEdit || instructions.value.includes(suggestedEdit);
    reset.disabled ||= !context || instructions.value === context.instructions;
  }

  function clearAnswer() {
    suggestedEdit = "";
    answerPanel.hidden = true;
    suggestionPanel.hidden = true;
  }

  function originalDecision() {
    if (context.flagged && context.finding) return `Flagged ${describeFinding(context.finding)}`;
    return target.label ? `No finding for “${target.label}”` : "No finding at this timestamp";
  }

  function renderContext(loaded) {
    context = loaded;
    const source = loaded.evidence === "recorded"
      ? "Recorded evidence"
      : "Reconstructed from the cached transcript and available prompt · Original response unavailable";
    const kind = loaded.kind === "classification" ? "Classification" : "Discovery";
    const bounds = `${formatTimestamp(loaded.batch_start_seconds)}–${formatTimestamp(loaded.batch_end_seconds)}`;
    const validation = loaded.accepted === true ? "Original response accepted"
      : loaded.accepted === false ? "Original response rejected" : null;
    write(evidence, [source, kind, modelLabel(loaded.qwen_model), bounds, validation].filter(Boolean).join(" · "));
    write(observation, originalDecision());
    listItems(transcript, loaded.inputs.map((input, index) => (
      describeInput(input, loaded.windows?.[index], formatTimestamp)
    )));
    write(inputs, JSON.stringify(loaded.inputs, null, 2));
    write(response, loaded.response ?? "Original model response unavailable.");
    const earlierAttempts = (loaded.attempts ?? []).filter((attempt) => (
      attempt.error || attempt.response !== loaded.response
    )).map((attempt) => {
      const outcome = attempt.accepted ? "Accepted" : "Rejected";
      const count = attempt.window_count === 1 ? "1 input" : `${attempt.window_count} inputs`;
      return [outcome, count, attempt.error, attempt.response].filter(Boolean).join(" · ");
    });
    listItems(attempts, earlierAttempts);
    attempts.hidden = earlierAttempts.length === 0;
    defaultQuestion = loaded.flagged
      ? "Why might this run have flagged the phrase? What small prompt change would correct it?"
      : "Why might this run have missed the phrase? What small prompt change would catch it?";
    question.value = defaultQuestion;
    instructions.value = loaded.latest_rerun?.instructions ?? loaded.instructions;
    write(askEvidence, "Questions use evidence from the saved run.");
    if (loaded.latest_rerun) renderRerun(loaded.latest_rerun);
    evidencePanel.hidden = false;
    syncControls();
  }

  async function selectSide(side) {
    const requestId = ++requestNumber;
    slot = side;
    context = null;
    testedRerunId = null;
    clearAnswer();
    evidencePanel.hidden = true;
    resultPanel.hidden = true;
    const reference = row[side] ?? row[side === "baseline" ? "candidate" : "baseline"];
    target = {
      slot: side,
      start_seconds: reference?.start_seconds ?? row.timestamp_seconds,
      end_seconds: reference?.end_seconds ?? null,
      label: reference?.label ?? null,
    };
    for (const button of sideButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.inspectorSlot === slot));
    }
    write(observation, row[side]
      ? `Flagged ${describeFinding(row[side])}`
      : target.label ? `No finding for “${target.label}”` : "No finding at this timestamp");
    showError();
    showStatus(`Loading ${runLabels[slot].toLowerCase()} evidence…`);
    syncControls();
    try {
      const loaded = await post(request, "context", target);
      if (requestId === requestNumber) renderContext(loaded);
    } catch (failure) {
      if (requestId === requestNumber) showError(failure.message);
    } finally {
      if (requestId === requestNumber) showStatus();
    }
  }

  async function modelAction(path, extra, message, display) {
    if (!context || working) return;
    const requestId = requestNumber;
    working = true;
    onBusy(true);
    showError();
    showStatus(message);
    syncControls();
    try {
      const result = await post(request, path, { ...target, context_id: context.context_id, ...extra });
      if (requestId === requestNumber) display(result);
    } catch (failure) {
      if (requestId === requestNumber) showError(failure.message);
    } finally {
      working = false;
      onBusy(false);
      if (requestId === requestNumber) showStatus();
      syncControls();
    }
  }

  function renderAnswer(reply) {
    write(answer, reply.answer);
    suggestedEdit = reply.suggested_edit?.trim() ?? "";
    write(suggestion, suggestedEdit);
    suggestionPanel.hidden = !suggestedEdit;
    answerPanel.hidden = false;
    answerPanel.scrollIntoView?.({ block: "nearest" });
  }

  function renderRerun(result) {
    testedRerunId = result.rerun_id ?? null;
    if (testedRerunId) {
      const nextQuestion = result.accepted
        ? "How did the latest batch test affect this phrase? What small prompt change should I try next?"
        : "Why was the latest batch test response rejected? What prompt change could fix it?";
      if (!question.value.trim() || question.value === defaultQuestion) question.value = nextQuestion;
      defaultQuestion = nextQuestion;
      write(askEvidence, "Questions use the latest batch test and the instructions it ran with.");
      clearAnswer();
    }
    const count = result.findings.length;
    const findings = count === 1 ? "1 finding" : `${count} findings`;
    write(resultStatus, result.accepted
      ? `Test response accepted · ${findings} in this batch`
      : "Editarr rejected the test response. Review the raw output below.");
    write(resultOriginal, `Original decision · ${originalDecision()}`);
    listItems(resultFindings, result.findings.map((finding) => (
      `${formatTimestamp(finding.start_seconds)} · ${describeFinding(finding)}`
    )));
    resultFindings.hidden = count === 0;
    write(resultResponse, result.response);
    resultPanel.hidden = false;
  }

  for (const button of sideButtons) {
    button.addEventListener("click", () => void selectSide(button.dataset.inspectorSlot));
  }
  askForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = question.value.trim();
    if (!value) {
      showError("Enter a question about this decision.");
      question.focus();
      return;
    }
    void modelAction("ask", { question: value, rerun_id: testedRerunId }, "Qwen is reviewing this decision…", renderAnswer);
  });
  rerunForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!instructions.value.trim()) {
      showError("Enter prompt instructions for the batch.");
      instructions.focus();
      return;
    }
    void modelAction("rerun", { instructions: instructions.value }, "Running this batch…", (result) => {
      renderRerun(result);
      resultPanel.scrollIntoView?.({ block: "nearest" });
    });
  });
  instructions.addEventListener("input", syncControls);
  apply.addEventListener("click", () => {
    instructions.value = `${instructions.value.trimEnd()}\n\n${suggestedEdit}`;
    syncControls();
    instructions.focus();
  });
  reset.addEventListener("click", () => {
    instructions.value = context.instructions;
    syncControls();
    instructions.focus();
  });
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    requestNumber += 1;
    context = null;
  });

  return {
    open(selectedRow) {
      if (working || dialog.open) return;
      row = selectedRow;
      write(timestamp, formatTimestamp(row.timestamp_seconds));
      dialog.showModal();
      void selectSide("candidate");
    },
  };
}
