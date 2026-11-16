# NSR-07 — Import EDLs into saved reviews

Status: **DONE**

Dependencies: NSR-02, NSR-04, and NSR-05. Deliverable: browser-upload and scanner-path EDL
import for every source, creating one named completed scan and one saved review atomically.

Read the task-pack README, parent plan, NSR-00–05, and prerequisite handoffs first.

## Why this task exists

The codec is pure and review persistence is source-neutral; this task safely acquires EDL
content, resolves target media, and orchestrates the all-or-nothing import transaction.

## Scope

1. Implement the frozen protected import endpoints:
   - multipart browser upload carrying review name, source/item/version target, and one
     `.edl` file;
   - JSON scanner-path input carrying the same target/name plus an absolute EDL path.
   Both call one service after content acquisition.
2. Enforce NSR-05 limits while reading, not only after buffering. Accept UTF-8 only.
   Browser filename is untrusted metadata: reduce it to a safe basename and never resolve
   it as a host path. Scanner input must be absolute, exist, be a readable regular file,
   and stay within the bounded size.
3. Resolve the target media item/version exactly as NSR-04 does. Catalog imports never
   accept a client path. Validate the target media is readable and apply known-duration
   bounds to every supported range before committing.
4. Parse once using NSR-05. If there are no supported actions, return the frozen error and
   create nothing. If unsupported actions coexist with supported ones, continue and return
   one warning plus ignored count.
5. In one database transaction create:
   - one completed imported/manual `ScanRow` with next per-item scan number, origin
     `imported`, initial `name` equal to the required review name, source/version display
     equal to the safe EDL basename, import timestamp in the frozen field, 100% completed
     state, and audio/video flags inferred from supported rows;
   - source findings for parsed filters, with confidence/model/manual metadata defined by
     NSR-00 and action-correct source/category/label;
   - one saved review using the requested name, membership role audio/video/both, and
     selected review snapshots of every imported source finding.
6. Do not enqueue, acquire/load models, create worker ownership, or write/replace the
   target media's `.edl` sidecar. Import alone never creates active export state.
7. Return created review detail, created scan, safe source basename/import timestamp, and
   aggregate warning. Do not expose the full scanner EDL path.
8. Apply auth, CSRF, origin, rollback, and safe error behavior to both transports.
9. Add API/repository tests for mute-only, skip-only, mixed, comments/multiple filters,
   no comments, ignored actions, ignored-only, invalid format/UTF-8/limits, duration
   overflow, all sources, unreadable paths, fake upload names, rollback, and no queue/EDL.

## Out of scope

- Import UI (NSR-11).
- Importing action 0/2, frames, clock timestamps, comma format, or non-UTF-8.
- Treating an imported file as the current on-disk sidecar.
- Deleting the imported scan when its review is deleted.

## Verification

- Ruff changed backend/tests.
- Parser unit tests plus focused import API/repository/OpenAPI/auth tests.
- Inspect queue, active EDL, scan, finding, review, and snapshot rows after success/failure.
- Use only temporary media/EDL files and fake source adapters.
- `git diff --check`.

## Acceptance criteria

- [x] Both transports share content limits, parser, target resolver, and transaction.
- [x] All four sources work without trusting client catalog paths.
- [x] Exactly one correctly typed completed named scan and one saved review are created.
- [x] Comment metadata and generic fallbacks create selected reusable source findings and
      isolated review snapshots.
- [x] Unsupported-action warning is aggregated; unsupported-only input is a no-write error.
- [x] Import never queues work or changes a sidecar/active EDL record.
- [x] Any acquisition/validation/persistence failure leaves no partial rows.
- [x] Responses are path-safe and unsafe routes are protected.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Use temporary files to exercise both transports and compare resulting database objects.
Patch a late transaction step to fail and prove rollback. Assert no queue item, worker
call, sidecar, or active EDL row exists. Test a browser filename containing separators and
a host path outside the allowed target. Rerun tests before NSR-08.

## Handoff requirements

Record endpoints/payloads, acquisition limits, transaction/service function, created row
fields, warning/error codes, source coverage, tests/counts, and rollback evidence. Name
NSR-08 as next eligible once NSR-06 is also DONE.

## Handoff

Status: DONE
Last completed substep: Implemented shared bounded path/upload acquisition, trusted target
   resolution, parser orchestration, and one-transaction imported scan/review creation.
Files changed: scanner/api.py, scanner/repository.py, tests/test_scan_naming.py.
Schema/API/contract changes: Added `POST /api/edl-imports/path` and multipart
   `POST /api/edl-imports/upload`; added `import_edl_as_saved_review`. Imports enforce the
   1 MiB content bound, safe EDL basenames, UTF-8/parser errors, duration checks, aggregate
   unsupported-action warnings, and server-owned catalog/manual media paths. Imported scans
   use origin `imported`, complete state, review-name display metadata, source basename, and
   inferred audio/video flags; no worker or active EDL rows are created.
Verification commands and results: `uv run pytest -q tests/test_edl.py tests/test_editarr_integration.py
   tests/test_saved_review_schema.py tests/test_saved_review_repository.py tests/test_scan_naming.py`
   passed 213 tests; `uv run ruff check scanner tests scripts` passed.
   Existing Starlette/httpx and FastAPI `on_event` deprecation warnings remain.
Manual or artifact evidence: Focused API tests verify path and multipart success, authentication,
   Origin and CSRF enforcement, malformed line propagation, extension and unsafe filename
   rejection, alternate Jellyfin-version selection, and Radarr/Sonarr adapter dispatch and file
   identity. Codec tests cover invalid UTF-8, byte/line/action-row limits, comment injection, and
   export/parse range round trips. Injected commit failure leaves no imported aggregate rows.
Known failures or risks: Browser import-dialog coverage remains part of NSR-11/12 rather than this
   backend task.
Acceptance criteria still unmet: None within the implementation scope.
Next exact action: Begin NSR-08 frontend data layer after the import/export backend checkpoint.
Next eligible task: NSR-08 is now eligible; NSR-09 and later UI tasks remain blocked on it.
Decisions or deviations from the parent plan: None.
User approval required: None.

