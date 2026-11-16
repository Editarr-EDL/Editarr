# MTS-04A - Evaluate third-party credits detectors

Status: **DONE**

Dependencies: MTS-04. Deliverable: a documented, reproducible comparison of suitable
licensed credits-detection libraries and pretrained models against the MTS corpus, followed
by an explicit keep, replace, or hybrid selection before scan-lifecycle integration.

Read the task-pack README, parent plan, MTS-00 and MTS-04 handoffs, repository `AGENTS.md`,
model-development documentation, model-artifact policy, and current detector/evaluator code
before changing code.

## Scope

1. Survey maintained third-party libraries and pretrained models that specifically detect
   closing credits or provide directly useful text/sequence evidence. Record project URL,
   release activity, license, model/data provenance, supported platforms and accelerators,
   runtime dependencies, artifact size, offline behavior, and whether immutable revisions
   and integrity values are available.
2. Reject candidates that cannot meet commercial redistribution requirements, disclose
   training/model licensing, run within Editarr's supported platforms, remain bounded to a
   media tail, avoid uploading user media, or produce a reviewable boundary and confidence.
3. Define a shortlist and evaluation protocol before installing packages or acquiring
   weights. Compare each viable candidate with the MTS-04 baseline using the same CC0 corpus,
   generated rendered tails, boundary tolerances, false-positive cases, runtime, memory, and
   platform/backend reporting.
4. Expand the legal corpus where the current synthetic cases cannot distinguish candidate
   quality. New fixtures must be authored, generated, or redistributable with recorded
   licenses and exact reviewed boundaries. Do not use copyrighted film clips or user media.
5. Evaluate the current non-Qwen detector, each approved third-party candidate, and the
   existing pinned Qwen choices where already available. Record candidate recall, null-case
   false positives, boundary error, confidence behavior, runtime, peak memory, artifact
   size, and failure behavior. Do not present synthetic results as population accuracy.
6. Produce a written decision record selecting one of:
   - keep the MTS-04 extractor because no candidate is demonstrably better;
   - replace it behind the existing `FrameObservation`/detector contract;
   - use a hybrid in which a candidate adds evidence without becoming the sole authority.
7. A replacement or hybrid must preserve the MTS-00 DTOs, evidence ordering, low-confidence
   result, first credible boundary behavior, cancellation hook, optional-Qwen operation, and
   complete behavior-versioned cache identity. Remove superseded custom extraction code when
   it no longer has a justified fallback role.
8. Add focused deterministic tests for the selected adapter and update the evaluator, model
   notices, dependency manifests, packaging collection, and cache versions when applicable.
9. If a candidate requires a new package, model artifact, network download, or license
   acceptance, stop after the metadata-only comparison and request explicit user approval
   before installation, acquisition, manifest changes, or execution. Approval is
   candidate-specific and does not authorize unrelated artifacts.

## Out of scope

- Queue, API, persistence, finding creation, acceptance, or preview clips (MTS-05).
- Frontend behavior (MTS-06).
- Real user media, credentials, sidecars, installed databases, or running services.
- Silent selection of a model whose license, provenance, integrity, or platform support is
  unresolved.

## Verification

- Reproducible metadata/source citations for every surveyed candidate.
- Deterministic evaluator runs for the baseline and every candidate actually tested.
- License, artifact integrity, platform, runtime, memory, and failure-mode report.
- Focused adapter/cache/cancellation tests for any selected implementation change.
- Existing MTS-04 detector, scene, frame, cache, Qwen, and visual-model regressions.
- Ruff on changed Python files and `git diff --check`.
- No model acquisition or new dependency installation without recorded explicit approval.

## Acceptance criteria

- [x] The survey covers credible maintained options and records why each is accepted or
      rejected.
- [x] Every executed candidate has adequate license/provenance and immutable artifact data.
- [x] The comparison uses common legal fixtures and reports quality and resource costs.
- [x] A keep, replace, or hybrid decision is justified by recorded evidence.
- [x] Any selected implementation preserves MTS contracts, cancellation, cache invalidation,
      and cross-platform non-Qwen behavior.
- [x] Superseded custom extraction is removed unless evidence justifies it as a fallback.
- [x] No unapproved package/model is installed or downloaded and no real user media is used.

## Handoff

Status: DONE
Last completed substep: Full backend regression after the metadata survey, common-corpus
PySceneDetect comparison, resource reporting, and keep decision.
Files changed: `scanner/credits_eval.py`, `tests/test_credits_eval.py`,
`docs/credits-third-party-detector-evaluation.md`, `docs/credits-detection-evaluation.md`,
`docs/model-development.md`, this task, and the task-pack README.
Schema/API/contract changes: None. The evaluation JSON adds rendered-analysis memory/runtime,
boundary-error data, and a `third_party_comparison` section; production DTOs are unchanged.
Verification commands and results: Changed-file Ruff passed. Focused evaluator tests passed
5 tests. Credits/scene/frame/cache/Qwen regressions passed 76 tests. Visual-model,
capability, acquisition, and evaluator regressions passed 64 tests. Full backend pytest
passed 1,160 tests with no warnings. `git diff --check` passed before handoff completion and
is rerun as the final check.
Manual or artifact evidence: `docs/credits-third-party-detector-evaluation.md` records the
survey, source URLs, licenses, revisions, integrity values, protocol, results, and decision.
The evaluator reported baseline rendered recall 7/7 and 0/1 false positives versus
PySceneDetect 6/7 and 0/1 on `macOS-27.0-arm64-arm-64bit`; no user media was used.
Known failures or risks: The corpus is synthetic and supports regression comparison, not
real-film population accuracy. Python peak-memory metrics exclude native OpenCV allocation.
RapidOCR/PaddleOCR model-specific training provenance was insufficient for execution. Qwen
remained mocked because no artifact was acquired for this task.
Acceptance criteria still unmet: None.
Next exact action: Implement MTS-05 using the unchanged MTS-04 detector and its existing
PySceneDetect supporting evidence path.
Next eligible task: MTS-05; MTS-04A is DONE.
Decisions or deviations from the parent plan: Keep `credits-detector-v1`; do not replace its
OpenCV fallback or add an OCR hybrid. No product behavior changed.
User approval required: None for the selected keep decision. Any future OCR package or model
evaluation still requires candidate-specific approval before installation or acquisition.