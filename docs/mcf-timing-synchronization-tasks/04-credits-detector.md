# MTS-04 - Evaluate and build credits detection

Status: **DONE**

Dependencies: MTS-00. Deliverable: an evaluation-backed, deterministic hybrid detector core
that returns ranked closing-credits boundary candidates and works without Qwen.

Read the task-pack README, parent plan, MTS-00 handoff/contract record, repository
`AGENTS.md`, model-development documentation, current scene/frame/cache/Qwen code, and
model-artifact policy before changing code.

## Scope

1. Build a redistributable or locally generated evaluation corpus/manifest containing short
   media tails with exact expected closing-credits boundaries and tolerances. Include at
   least scrolling credits, static title cards, credits over moving imagery, a post-credits
   scene, chapter metadata present/absent/misleading, no credits, non-English credits,
   intermittent black frames, and ambiguous low-confidence material.
2. Add an evaluation runner/report that measures boundary error, candidate recall, false
   positives, confidence calibration, runtime, and platform/backend used. Commit only legal,
   bounded fixtures or deterministic generators; do not add copyrighted film material.
3. Implement a dedicated credits detector module with versioned structured output. It must
   gather and rank evidence from:
   - trusted container chapter metadata;
   - cached or newly detected scene boundaries near the media tail;
   - bounded sampled frames and cross-platform visual/text-pattern evidence;
   - optional Qwen assessment of candidate frame groups when explicitly enabled and
     available.
4. Keep credits detection independent from safety-model findings. Existing Qwen enrichment
   only sees safety-flagged segments and is not a valid credits detector entry path.
5. Return the best candidate even below the frozen confidence threshold, with an explicit
   low-confidence state and ordered evidence. Do not return certainty unsupported by the
   evaluated corpus.
6. Handle post-credits scenes by identifying the first credible closing-credits boundary,
   not merely the beginning of the final scene or final black frame.
7. Version every algorithm, prompt, threshold set, and model identity used in cache keys.
   Reuse video-hash scene/frame artifacts where safe; stale detector versions must miss cache.
8. Integrate existing Qwen model implementations without adding an unpinned model. The pure
   detector receives an explicit Qwen-enabled/model choice; acquisition consent and queued
   process lifecycle belong to MTS-05.
9. Freeze production thresholds only from recorded evaluation evidence. If the corpus cannot
   justify a reliable threshold or required method, record the limitation and mark the task
   BLOCKED instead of presenting an unevaluated guess as reliable.

## Out of scope

- Database schema beyond contracts already implemented by MTS-01.
- Queue/API/status/cancellation/model-consent wiring, scan/finding persistence, or clips
  (MTS-05).
- Import/review/frontend behavior (MTS-02, MTS-03, MTS-06).
- A new dedicated external model artifact without separate user approval and manifest work.

## Verification

- Ruff on changed Python files.
- Deterministic chapter, scene, visual/text, evidence ranking, confidence, post-credits,
  low-confidence, cancellation hook, and cache-version unit tests.
- Evaluation runner against the complete checked-in/generated corpus with report artifact.
- Qwen mocked-response tests plus Qwen unavailable/disabled behavior on every platform.
- Existing scene/frame/cache/Qwen/safety detector tests.
- Optional real-Qwen evaluation only when already available or separately approved; record
  model/revision/platform without making it a deterministic suite dependency.
- `git diff --check`.

## Acceptance criteria

- [x] Detector output and confidence are grounded in a documented legal fixture corpus.
- [x] Cross-platform non-Qwen detection produces ranked evidence and a best candidate.
- [x] Low-confidence, no-credits, misleading-chapter, and post-credits cases are explicit.
- [x] Optional Qwen improves or annotates candidates without becoming a platform requirement.
- [x] Cache identity invalidates every behavior-affecting detector/model change.
- [x] The detector has no database, queue, API, UI, or source-finding side effects.
- [x] No model is downloaded and no real user media is accessed by automated verification.

## Handoff

Status: DONE
Last completed substep: Full backend regression after focused detector, evaluator, media,
cache, Qwen, scene, frame, and visual-model verification.
Files changed: `scanner/credits_detector.py`, `scanner/credits_media.py`,
`scanner/credits_eval.py`, `scanner/credits_eval_cli.py`,
`scanner/credits_fixture_generator.py`, `scanner/pipeline/cache.py`, `pyproject.toml`,
`tests/fixtures/mts/credits-evaluation-v1.json`, `tests/test_credits_detector.py`,
`tests/test_credits_media.py`, `tests/test_credits_eval.py`,
`tests/test_credits_fixture_generator.py`, `tests/test_frames.py`,
`docs/credits-detection-evaluation.md`, `docs/model-development.md`, this task, and the
task-pack README.
Schema/API/contract changes: None. Detector output uses the MTS-00 DTOs. The new CLI entry
point is `editarr-evaluate-credits`.
Verification commands and results: Changed-file Ruff passed. Focused MTS-04 plus existing
scene/frame/cache/Qwen/visual/model capability regression passed 117 tests. Full backend
pytest passed 1,158 tests with no warnings. The evaluator reported 7/7 candidates within
tolerance and 0/1 false positives for both policy vectors and generated rendered tails.
`git diff --check` passed.
Manual or artifact evidence: `docs/credits-detection-evaluation.md` records corpus,
thresholds, per-band calibration, runtime/platform/backend, reproduction command, and
limitations. Generated fixtures are CC0 and temporary.
Known failures or risks: The corpus is synthetic and intentionally does not support a
real-film population accuracy claim. Real-media and real-Qwen quality need separately
licensed, human-reviewed evidence. Qwen remained mocked and no artifact was acquired.
Acceptance criteria still unmet: None within MTS-04.
Next exact action: Implement MTS-04A's third-party library/model comparison before selecting
the detector that MTS-05 will integrate.
Next eligible task: MTS-04A; MTS-04 is DONE.
Decisions or deviations from the parent plan: None. Confidence bands remain presentation
labels rather than probability claims, as frozen by MTS-00.
User approval required: None. Adding or acquiring a new dedicated model still requires
separate approval; MTS-04 added and acquired none.