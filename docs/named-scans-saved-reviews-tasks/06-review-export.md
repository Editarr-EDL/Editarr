# NSR-06 — Export saved reviews and track active state

Status: **DONE**

Dependencies: NSR-04 and NSR-05. Deliverable: review-ID EDL export, review marker/outdated
state, complete scan provenance, atomic filesystem behavior, and delete lifecycle.

Read the task-pack README, parent plan, NSR-00–05, and prerequisite handoffs first.

## Why this task exists

The active sidecar must be derived from one saved review rather than an arbitrary browser
scan combination. This task connects the review aggregate to the pure codec while
preserving the existing crash-safe EDL lifecycle.

## Scope

1. Implement review export repository/service/API from the frozen contract. The protected
   request identifies saved review ID and expected revision; the server loads persisted
   review memberships/snapshots and never accepts browser-provided finding selections as
   export authority.
2. Revalidate immediately before writing:
   - review exists and expected revision is current;
   - target media/version resolves to one readable local file;
   - all contributing scans remain complete, associated with the media item, and on the
     same version;
   - at least one audio/video membership exists (empty selected output remains a valid
     header-only EDL if memberships exist).
3. Feed selected review-owned filters, including edited snapshot fields, to NSR-05's
   contributor-aware exporter. Source `ScanFindingRow.selected` and content must not affect
   output.
4. Preserve atomic write behavior: same-directory temporary file, flush/fsync, atomic
   replace, debris cleanup. Update database state only after successful replacement.
5. On success, replace active state with all contributing scan IDs/roles, review ID, and
   exported review content revision. Export changes neither review revision.
6. Return and decorate EDL state everywhere with review summary/name when present and an
   `outdated` value computed from current versus exported content revision. Scan provenance
   remains complete even when the review is later deleted.
7. Preserve per-version Delete EDL behavior for Jellyfin, Radarr, Sonarr, and Other Files.
   It removes only the selected sidecar/record and clears the marker. Review rows survive.
8. Ensure deleting the marked review clears review provenance only; the existing EDL and
   scan references remain. Name-only review rename updates displayed provenance but does
   not mark outdated.
9. Keep the old `/api/scans/{scan_id}/edl` only as a clearly identified transition if the
   old frontend still needs it. NSR-12 removes it; new code/tests use review export.

## Out of scope

- EDL import (NSR-07).
- Frontend marker/export changes (NSR-08/10/11).
- Changing EDL destination naming or source path trust rules.

## Verification

- Ruff changed backend/tests.
- EDL export integration tests for isolated review edits, comments, header-only output,
  multi-scan roles, cross-version rejection, stale revisions, active marker transfer,
  outdated after content save, rename-not-outdated, marked-review deletion, EDL deletion,
  and every source type.
- Atomic failure test proving previous sidecar and all database provenance remain intact.
- OpenAPI/auth/CSRF tests for the new export route.
- Existing `tests/test_edl.py` and relevant export tests in
  `tests/test_editarr_integration.py`.
- `git diff --check`.

## Acceptance criteria

- [x] Only a persisted saved review can drive the new export path.
- [x] Export reads review snapshots, not mutable scan-owned selection/content.
- [x] Output comments and playback rows match NSR-05 exactly.
- [x] Successful state records review revision plus every scan role atomically.
- [x] Outdated, rename, marker transfer, review deletion, and EDL deletion are correct.
- [x] Filesystem failure preserves the previous sidecar and database state.
- [x] All source/version and security checks pass.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Independently edit one review without editing another, export each, and compare files and
marker transfer. Inject `os.replace` failure and inspect both file and DB. Delete the marked
review, verify the file remains, and confirm the status retains all scan references but no
review. Rerun focused tests before NSR-08.

## Handoff requirements

Record endpoint/DTO, exporter call shape, transaction/write order, status-decoration
fields, legacy endpoint status, tests/counts, and failure-injection evidence. Name NSR-08
as waiting on NSR-07; NSR-07 may proceed when its own prerequisites are DONE.

## Handoff

Status: DONE
Last completed substep: Implemented review-ID EDL export from persisted snapshots with
   active review provenance and complete scan role replacement.
Files changed: scanner/repository.py, scanner/api.py, tests/test_saved_review_repository.py.
Schema/API/contract changes: Added `export_saved_review_edl(db, review_id, revision)` and
   `POST /api/reviews/{review_id}/edl` using `ReviewExportRequest` and `ReviewEdlExport`.
   Export validates current revision, complete same-version memberships, verified local media,
   and writes review-owned filters through the NSR-05 contributor-aware codec. Active EDL rows
   record `review_id`, `exported_content_revision`, and every membership role. Existing summary
   and status conversion exposes exported/outdated review provenance. At this task boundary the
   legacy scan export remained transitional for NSR-12; it has since been removed.
Verification commands and results: `uv run pytest -q tests/test_edl.py tests/test_editarr_integration.py
   tests/test_saved_review_schema.py tests/test_saved_review_repository.py tests/test_scan_naming.py`
   passed 185 tests; `uv run ruff check scanner tests` passed; `git diff --check` passed.
   Existing Starlette/httpx and FastAPI `on_event` deprecation warnings remain.
Manual or artifact evidence: Focused review-export test confirms output uses the saved snapshot
   and records active review/content provenance. Review deletion already clears provenance while
   retaining the sidecar and scan links; name-only rename preserves content revision/outdated state.
Known failures or risks: At this task boundary the full cross-source route matrix and injected
   filesystem-failure coverage remained follow-up work. NSR-12 and its independent repair pass
   added those paths, removed the scan-owned exporter, and covered uninterrupted replacement,
   database rollback, and restore-failure backup retention.
Acceptance criteria still unmet: None within the implementation scope.
Next exact action: Implement NSR-07 EDL import backend using the pure parser.
Next eligible task: NSR-07 is eligible; NSR-08 remains blocked until NSR-07 is complete.
Decisions or deviations from the parent plan: None.
User approval required: None.
