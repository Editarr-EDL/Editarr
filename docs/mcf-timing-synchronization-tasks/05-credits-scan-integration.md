# MTS-05 - Integrate credits scans and previews

Status: **DONE**

Dependencies: MTS-01 and MTS-04A. Deliverable: a normal cancellable credits scan lifecycle,
protected detection/preview APIs, immutable End Credits findings, and accepted review/import
integration.

Read the task-pack README, parent plan, MTS-00, MTS-01, MTS-04, and MTS-04A handoffs,
repository `AGENTS.md`, and existing queue/worker/cancellation/model-acquisition/preview
tests before changing code.

## Scope

1. Add the MTS-00 credits scan mode/options to normal scan creation, queue rows, history,
   capabilities, worker dispatch, progress, cancellation, restart/error handling, and safe
   serialization for catalog/Arr/Other Files targets.
2. Resolve the selected physical media and duration server-side, compute/reuse the video hash,
   and run the MTS-04 detector in the isolated worker/process-control lifecycle. Do not route
   credits through an existing safety model or create unrelated content findings.
3. Implement Qwen behavior exactly as frozen:
   - use cross-platform evidence without Qwen when disabled/unavailable;
   - if the selected Qwen artifact is absent, return a consent-required state containing safe
     capability/download disclosure before acquisition;
   - resume/retry only after explicit authorization;
   - support acquisition progress, cancellation, cleanup, and failure without corrupting scan
     state or cached detector output.
4. Persist detector configuration, version, candidate timestamp/range, confidence,
   low-confidence state, ordered safe evidence/provenance, and completion/error state on the
   normal scan according to MTS-00.
5. On successful detection, create one immutable source finding from detected credits start to
   effective physical file END with:
   - `dispensable_scenes/dispensable` classification;
   - description and MCF comment `End Credits`;
   - source visual, MCF channel video, Skip action, selected true;
   - configured `dispensable` severity default and detector confidence.
6. Keep source finding timing immutable. Acceptance validates the scan/candidate against the
   same media/version and returns or stages a review membership/snapshot. An edited accepted
   timestamp changes only the review snapshot; accepted provenance records detected and
   accepted timestamps, confidence, method, scan ID, and acceptance time.
7. Add protected endpoints for start/reconnect, status/result, cancellation, optional model
   consent/resume, candidate acceptance as frozen, and a bounded short video clip around the
   detected or edited boundary.
8. Generate clips with the trusted resolved path and existing media process controls. Enforce
   fixed maximum duration/size, safe MIME/content disposition, cleanup, cancellation, and no
   path disclosure. A clip failure does not destroy an otherwise valid candidate.
9. Support both integration contexts:
   - an import flow may reference the completed accepted scan/candidate for MTS-02 to attach
     atomically when the import is submitted;
   - an existing review receives membership, snapshot, and provenance in its local draft,
     with persistence deferred to normal Save.
10. Preserve the credits scan/history entry if import is canceled or review changes are
    discarded. Normal scan deletion/reference guards apply after a review is saved.
11. Add live OpenAPI, auth/CSRF, safe-error, target-isolation, queue ordering, and scan naming
    coverage.

## Out of scope

- Detector heuristics, prompts, thresholds, or evaluation (MTS-04).
- MCF import mapping and review retiming implementation (MTS-02/MTS-03).
- User-facing dialogs and clip player (MTS-06).
- Rewriting detector source evidence after user correction.

## Verification

- Ruff on changed Python files.
- Queue/worker tests for success, low confidence, cancel, restart, process cleanup, cached
  result, stale cache version, unavailable media, and detector failure.
- Qwen installed, unavailable, consent-required, declined, acquisition progress, canceled,
  failed, and resumed paths with fake/test artifacts only.
- Repository tests for exact canonical finding fields, configured severity, immutable
  candidate, edited review snapshot, accepted provenance, membership, discard/cancel survival,
  reference guards, and rollback.
- Bounded clip content/MIME/range/cleanup/error/path-redaction tests using generated media.
- Catalog/Arr/Other Files API, OpenAPI, authentication, CSRF, and physical-version isolation.
- Existing scan, worker, restart, model acquisition, review, preview, MCF, and EDL regressions.
- `git diff --check`.

## Acceptance criteria

- [x] Credits detection behaves as a normal visible scan with truthful progress/cancellation.
- [x] Qwen is optional and cannot be acquired without explicit disclosed authorization.
- [x] Completion persists one exact immutable canonical End Credits finding and evidence.
- [x] Correcting a suggestion changes only the review snapshot and preserves both timestamps.
- [x] Import/review contexts can attach the accepted scan without trusting browser evidence.
- [x] Preview clips are bounded, protected, playable, cleaned up, and path-safe.
- [x] Cancellation/failure leaves no stuck process, partial finding, or corrupt cache.

## Handoff

Status: DONE
Last completed substep: Full backend regression after target-isolated reconnect hardening,
restart recovery, fake-artifact Qwen lifecycle coverage, and atomic completion tests.
Files changed: `scanner/api.py`, `scanner/auth_policy.py`, `scanner/credits_scan.py`,
`scanner/models.py`, `scanner/repository.py`, `scanner/worker.py`, `tests/test_credits_api.py`,
`tests/test_credits_scan.py`, `tests/test_dedicated_worker.py`,
`tests/test_queue_lifecycle.py`, `tests/test_startup_gate.py`, `docs/api-contract.md`, this
task, and the task-pack README.
Schema/API/contract changes: No schema change. Implemented the frozen credits start/status,
model-consent, acceptance, and clip routes/DTOs; scan/queue serialization now carries the
MTS-01 credits run. Review Save revalidates and persists accepted provenance atomically.
Verification commands and results: Changed-file Ruff passed. Focused credits, queue, worker,
review, preview, auth, OpenAPI, model-acquisition, and integration regressions passed 380
tests. Full backend pytest passed 1,176 tests with no warnings. `git diff --check` passed
before handoff and is rerun as the final check.
Manual or artifact evidence: All media was generated temporary test data. Qwen ready,
missing, declined, progress/resume, cancellation, and failure paths used fake artifact
services; no package, model, user media, sidecar, running service, or user database was used.
Known failures or risks: Detector quality remains bounded by the synthetic MTS-04/MTS-04A
evidence. Clip size is enforced after bounded encoding, so an oversized result is discarded
rather than streamed. Interrupted process-local model acquisition returns to explicit consent
on startup.
Acceptance criteria still unmet: None.
Next exact action: Implement MTS-02 import synchronization; MTS-06 remains blocked on it.
Next eligible task: MTS-02.
Decisions or deviations from the parent plan: None. Credits use unchanged
`credits-detector-v1`; no safety-model alias or automatic model acquisition was introduced.
User approval required: None for this task. Runtime Qwen acquisition requires the new
per-action disclosed consent route; no real artifact was acquired during implementation.