# NSR-02 — Implement scan naming backend

Status: **DONE**

Dependencies: NSR-01. Deliverable: validated scan names across every creation/read path
and a protected rename API.

Read the task-pack README, parent plan, NSR-00/01, and prerequisite handoffs first.

## Why this task exists

Scan names must work consistently before saved-review/import services begin creating and
returning named scans. This task owns scan name validation, persistence plumbing, rename,
and all backend presentation/provenance DTOs; it does not build review CRUD or web UI.

## Scope

1. Implement one shared scan/review name canonicalizer according to the frozen contract:
   Unicode-safe outer trim, display-case preservation, control-character rejection, and
   100-code-point maximum. Scan callers may normalize blank to `None`; review callers must
   reject blank in later tasks.
2. Thread optional `name` through `create_scan` and all scan creation/admission paths:
   Jellyfin, Radarr, Sonarr episode, Other Files, Manual scan creation/reuse, test helpers,
   and any worker/queue row conversion. Do not duplicate validation by source.
3. Return `name` and origin in `Scan`, `ScanQueueItem`, history, item scan lists, candidate
   matches, Manual finding results, and EDL scan references if NSR-00 includes it there.
4. Add the frozen protected scan partial-update endpoint for rename. It must:
   - return 404 for an unknown scan;
   - accept duplicate names;
   - clear to unnamed on null/approved blank input;
   - rename queued, running, completed, Manual, and imported scans without changing worker
     identity, options, revision, status, or finding content;
   - update the scan's ordinary `updated_at` only as frozen by NSR-00.
5. Ensure the Manual scan can be named on creation and renamed later. Reusing a Manual
   scan must not silently overwrite its existing name unless the request explicitly
   performs rename.
6. Add repository/API/OpenAPI tests for every state/source, validation boundary, duplicate
   names, clearing, Unicode length, and no worker-side effect.

## Out of scope

- Frontend name controls or display (NSR-09).
- Saved-review names or CRUD (NSR-03/04).
- Import creation (NSR-07), though it will reuse this API/repository contract.
- Renumbering scans or replacing stable scan IDs with names.

## Required invariants

- A name is display metadata only. Queue/worker ownership continues using UUID and scan
  number.
- Names are never used in filesystem paths, EDL filenames, lookup identity, or uniqueness
  checks.
- Every source produces the same validation/error behavior.
- Error responses do not expose media paths or internal model state.

## Verification

- Run Ruff on changed Python/tests.
- Run focused naming tests plus relevant scan creation, queue, Manual finding, candidate,
  and OpenAPI tests from `tests/test_editarr_integration.py` and queue suites.
- Run `git diff --check`.

## Acceptance criteria

- [x] Every scan creation route accepts an optional valid name and returns it.
- [x] Existing clients omitting the name still work and receive `name: null`.
- [x] Rename/clear works for every scan state/origin and accepts duplicates.
- [x] Manual scan reuse does not unexpectedly change its name.
- [x] Queue/worker identity and execution behavior are unchanged.
- [x] OpenAPI and focused tests cover boundary/error cases.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Review every call to `create_scan`, search for Scan/ScanQueueItem constructors, and confirm
none omit the field or reimplement validation. Exercise rename during queued and completed
states and verify queue identity/options are byte-for-byte equivalent apart from display
metadata/timestamp. Rerun the focused tests before NSR-04 or NSR-07 uses the contract.

## Handoff requirements

Name the canonicalizer, exact request/response fields and route, all creation paths
updated, test commands/counts, and any transitional compatibility retained. Name NSR-04
as next eligible once NSR-03 is also DONE; NSR-03 may proceed independently.

## Handoff

Status: DONE
Last completed substep: Completed focused and broader verification for naming creation/read
  plumbing and protected rename.
Files changed: scanner/api.py, scanner/models.py, scanner/repository.py,
  tests/test_scan_naming.py, docs/api-contract.md, NSR-00 clarification, this task file.
Schema/API/contract changes: Optional name on shared scan options and the then-transitional
  Manual finding requests; PATCH /api/scans/{scan_id}; explicit origin read conversion.
  NSR-12 subsequently removed the immediate Manual-finding DTOs/routes; Manual filters now
  enter through saved-review create/save only.
Verification commands and results: `uv run pytest -q tests/test_scan_naming.py` passed 58
  tests; `uv run pytest -q tests/test_scan_naming.py tests/test_queue_lifecycle.py
  tests/test_editarr_integration.py` passed 128 tests; `uv run ruff check scanner tests`
  passed; `git diff --check` passed. Existing Starlette/httpx and FastAPI `on_event`
  deprecation warnings remain.
Manual or artifact evidence: All creation/conversion call sites inspected; temporary
  database test fixtures and mocked source/media/model access only.
Known failures or risks: No NSR-02 failures remain. The existing framework deprecation
  warnings are outside this task.
Acceptance criteria still unmet: None.
Next exact action: Implement NSR-03, the saved-review repository.
Next eligible task: NSR-03 may proceed independently; NSR-04 becomes eligible after
  NSR-03 is also DONE.
Decisions or deviations from the parent plan: User clarified that scan rename leaves
  updated_at unchanged, preserving the existing derived execution duration. No schema change.
User approval required: None; timestamp clarification explicitly received during NSR-02.
