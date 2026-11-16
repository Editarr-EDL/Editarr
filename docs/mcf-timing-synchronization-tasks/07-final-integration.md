# MTS-07 - Finish integration and release evidence

Status: **DONE**

Dependencies: MTS-06. Deliverable: audited end-to-end timing/credits behavior, current public
documentation/contracts, full regression and packaging evidence, and no transitional adapter.

Read the task-pack README, parent plan, every prerequisite handoff, repository `AGENTS.md`,
release/native documentation, and current CI/release verification scripts before changing
code.

## Scope

1. Audit the implementation against every parent-plan decision and MTS-00 contract. Remove
   temporary aliases, duplicated timing formulas, stale response fields, debug detector
   controls, unversioned cache data, and transitional frontend/backend adapters that are not
   part of the frozen contract.
2. Add full source-to-target integration coverage proving:
   - MCF upload and path imports for Jellyfin, Radarr, Sonarr, and Other Files;
   - exact immutable source bounds/cues beside affine-mapped initial review snapshots;
   - both END modes, blank Before-credits default, editable File-end duration, recovery retry,
     accepted credits scan attachment, and atomic rollback;
   - repeated all-cue retiming from current review state with manual edits preserved;
   - save/reload/duplicate/delete, revision conflicts, EDL/MCF staleness, preflight, export,
     and sidecar coexistence.
3. Add full credits journeys proving normal scan history, queue ordering, progress,
   cancellation, restart/error recovery, cached rerun, low-confidence warning, Qwen decline
   and consent, preview clip, exact and edited acceptance, immutable source candidate,
   accepted provenance, automatic review membership/snapshot, and scan deletion guards.
4. Verify emitted MCF bytes retain canonical before-credits START/END and export End Credits
   as `dispensable=<severity>=video # End Credits`. Verify EDL includes the selected Skip from
   accepted credits start through physical file END.
5. Update `docs/feature-design-spec.md`, `docs/api-contract.md`, model-development and
   artifact notices where applicable, user-facing README/setup/release documentation, OpenAPI
   expectations, and this task pack. Describe platform capability and optional Qwen behavior
   without claiming unsupported detector accuracy.
6. Audit model/runtime packaging. Any new runtime dependency, executable, data file, prompt,
   or cache directory must be explicitly collected, licensed, located through runtime paths,
   and covered by installed-release verification. Do not add a dedicated model artifact
   without separate approval.
7. Run frozen-runtime and installed-release smoke coverage with generated media. Verify
   controller/worker ownership, restart cleanup, clip delivery, model consent, and no browser
   launch or write outside approved runtime/test roots.
8. Update native/shared API models or compatibility fixtures if the public scan/review
   contracts require it. Record any platform UI intentionally deferred; do not leave decode
   failures hidden.
9. Perform desktop and 320px Chromium journeys for catalog and Other Files. Record screenshots,
   clip pixel/playback evidence, no root overflow, keyboard-only completion, focus behavior,
   and axe results.
10. Run the complete backend/frontend/browser/release suite available in the environment.
    Report unrelated failures without weakening tests or expanding scope.

## Out of scope

- New detector product behavior, taxonomy, models, or export semantics.
- Preservation migrations for development data.
- Wiping any database; the approved checkout-owned reset belongs only to MTS-01.
- Real user media, credentials, sidecars, services, or installed databases.

## Verification

- `uv run ruff check scanner tests scripts native_build`.
- Focused and full `uv run pytest -q` with temporary databases/media.
- Focused and full Vitest plus `pnpm build` in `web/`.
- Full mocked Chromium Playwright suite plus dedicated desktop/mobile timing/credits journeys.
- Frozen-runtime smoke and installed-release verification relevant to changed runtime files.
- Native shared tests when the environment provides the required toolchain; otherwise record
  the exact unavailable prerequisite and run fixture/decode alternatives.
- Detector evaluation report for non-Qwen and each real Qwen configuration actually tested.
- `git diff --check` and final `git status --short` review.

## Acceptance criteria

- [x] Every parent-plan requirement and confirmed decision has implementation and evidence.
- [x] Import, repeated review retiming, credits scans, acceptance, and both exports work
      together for all sources without mutating source evidence.
- [x] Qwen remains optional, consented, truthfully advertised, and packaging-safe.
- [x] Canonical End Credits findings and before-credits MCF bounds survive complete journeys.
- [x] No transitional contract/formula/cache authority remains.
- [x] Public API, feature, model, setup, and release documentation is current.
- [x] Backend, frontend, browser, accessibility, runtime, and packaging gates pass or have an
      explicit environment-only limitation.
- [x] No installed/user database, running user service, real media, credential, or sidecar was
      touched.
- [x] All MTS tasks and the README checklist have final handoffs and reproducible evidence.

## Handoff

Status: DONE
Last completed substep: Audited timing/credits authorities and completed documentation,
cross-feature export, browser media, packaging, and full regression evidence.
Files changed: `README.md`, `docs/api-contract.md`, `docs/feature-design-spec.md`,
`docs/release-build.md`, `docs/mcf-timing-synchronization-plan.md`, this task, the task-pack
README, `scripts/hatch_build.py`, `tests/test_mcf_final_integration.py`,
`web/e2e/app.spec.ts`, and `web/e2e/mcf10.spec.ts`.
Schema/API/contract changes: No schema or API change. The public API route table now includes
the implemented review-retime and credits-detection routes; the final integration test checks
those documented routes against live OpenAPI.
Verification commands and results: `uv run ruff check scanner tests scripts native_build`
passed; full `uv run pytest -q` passed 1,189 tests without warnings; focused final integration
passed 3 tests; full serial Vitest passed 123 tests; `pnpm build` passed; full mocked Chromium
passed 39 tests; native/runtime fixture alternatives passed 68 tests; detector policy and
rendered-tail tracks each passed 7/7 with zero false positives and PySceneDetect passed 6/7
with zero false positives; documentation links and `git diff --check` passed.
Manual or artifact evidence: Desktop and 320px generated-clip journeys verified playback,
time advancement, nonblank sampled pixels, edited acceptance, focus restoration, no root
overflow, and zero axe violations. Screenshots are
`web/test-results/mts07-credits-desktop.png` and
`web/test-results/mts07-credits-mobile.png`. Offline build produced wheel SHA-256
`2a1111abae03ee97b788f7c057ab0df6842a7d8dd022ed9f350302bed20733b3` and sdist SHA-256
`9f326d5058ac51d83b75fad1272073503ae2a44049d36e8e876fd715f48f3e01`; isolated legacy
wheel verification returned HTTP 200 with no failures.
Known failures or risks: No Qwen configuration or real-film corpus was exercised, so detector
evidence is intentionally synthetic-only. The native macOS build could not start without the
required tracked relocatable `ffprobe`; the wheel verifier reports `legacy-unverified` rather
than native readiness. Swift shared tests could not compile because XCTest is unavailable in
the active Command Line Tools environment. Python native/runtime fixture alternatives passed.
Acceptance criteria still unmet: None; unavailable native/Swift gates are recorded as explicit
environment-only limitations allowed by this task.
Next exact action: None; MTS-07 closes the MCF timing synchronization task pack.
Next eligible task: None.
Decisions or deviations from the parent plan: None. No detector behavior, taxonomy, model,
artifact, dependency, or export semantics changed.
User approval required: None. No model was acquired and no installed/user database, running
user service, real media, credentials, or sidecar was touched.