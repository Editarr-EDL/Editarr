# NSR-03 — Implement saved-review repository

Status: **DONE**

Dependencies: NSR-01. Deliverable: transactional repository aggregate for saved reviews,
isolated filter snapshots, revisions, sorting, duplication, deletion, and scan guards.

Read the task-pack README, parent plan, NSR-00/01, and prerequisite handoffs first.

## Why this task exists

The repository must make a saved review one atomic authority before API and UI work begins.
This task implements persistence behavior without public routes, EDL file writes, or React.

## Scope

1. Implement row converters and repository methods frozen by NSR-00 for:
   - list summaries by media item/version with name/date-modified sort and direction;
   - get detail by review ID;
   - create an empty or populated saved review;
   - save name/membership/review filters/new manual drafts atomically;
   - rename, duplicate, and delete;
   - query review references for a scan.
2. Snapshot only findings applicable to the membership role: audio membership takes audio
   findings, video membership takes visual findings, and both takes each. Validate that
   every scan is complete, reviewable, owned/associated with the target media item, and
   resolves to the same physical version.
3. Preserve source provenance but copy every editable field into `ReviewFilterRow`. Review
   edits never mutate `ScanFindingRow`. Two reviews using one scan must diverge safely.
4. Apply exact revision rules:
   - every successful mutation uses expected resource revision and returns 409-compatible
     stale behavior to the service layer;
   - rename changes resource revision/updated time only;
   - membership, selection, filter fields/timing/action, and manual additions change both
     resource and content revision;
   - export is not handled here and changes neither;
   - no-op behavior follows NSR-00 exactly.
5. Duplicate the current review snapshot into a new review with a required supplied name,
   fresh identity/timestamps/initial revisions, and no active-export marker.
6. Delete only review-owned rows. If the review is referenced by active EDL state, clear
   its review ID/exported content revision while retaining the EDL row and scan links.
7. Extend scan deletion guards to block any referenced scan. Return structured reference
   data containing every review ID, name, and updated timestamp so the API can build the
   required message; do not stop at the first review.
8. Implement the server-side half of remove/re-add semantics: after a saved removal no
   old snapshot remains. A later membership add snapshots current source findings. The
   same-session temporary restore is frontend-only in NSR-08/10.
9. Add exhaustive repository tests, including duplicate names, empty reviews, two review
   isolation, stale revisions, cross-item/version rejection, duplicate/delete, active EDL
   unlink, and all scan-reference messages.

## Manual finding boundary

Define one transaction-capable repository input for staged new manual filters, but do not
expose it publicly until NSR-04. On save it must create/reuse the version's Manual scan,
append source `ScanFindingRow` records, then create review snapshots. If any validation or
write fails, neither source finding nor review change commits. A discarded browser draft
never calls this operation.

## Out of scope

- HTTP routes/error envelopes (NSR-04).
- EDL export/import (NSR-05–07).
- Browser draft cache and UI.
- Removing old scan-level edit endpoints before the frontend transition (NSR-12).

## Verification

- Ruff changed repository/model/tests.
- Focused repository tests covering every operation and rollback.
- Relevant existing deletion, Manual finding, selection, association, and multi-version
  tests in `tests/test_editarr_integration.py`.
- `git diff --check`.

## Acceptance criteria

- [x] CRUD, sorting, duplication, revisions, and empty review behavior are transactional.
- [x] Review snapshots isolate selection and all editable content/timing fields.
- [x] Invalid scan membership cannot cross item/version/status boundaries.
- [x] Manual source finding plus snapshot creation is atomic and reusable.
- [x] Review deletion leaves scans/EDL intact and clears only review provenance.
- [x] Scan deletion reports all referencing review identities and still honors active EDL.
- [x] Focused tests include rollback and stale-revision cases.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Use a temporary database to create two reviews from one scan, edit the same filter
differently, reload both, duplicate one, delete the active-EDL review, and attempt scan
deletion. Inspect rows to prove source findings never changed and no scan was cascaded.
Rerun all named focused tests before NSR-04.

## Handoff requirements

List repository entry points/signatures, revision/no-op behavior, transaction boundaries,
row converters, deletion-reference result shape, and exact tests. Name NSR-04 as next
eligible once NSR-02 is also DONE.

## Handoff

Status: DONE
Last completed substep: Implemented the saved-review aggregate repository and focused
   temporary-database coverage.
Files changed: scanner/repository.py and tests/test_saved_review_repository.py.
Schema/API/contract changes: Added `list_saved_reviews`, `get_saved_review`,
   `create_saved_review`, `save_saved_review`, `rename_saved_review`,
   `duplicate_saved_review`, `delete_saved_review`, and `get_scan_references`, plus
   `ReviewRevisionConflict` and `ReviewMembershipError`. Source findings are copied into
   review-owned rows; staged Manual filters are written in the same transaction.
Verification commands and results: `uv run pytest -q tests/test_saved_review_repository.py
   tests/test_saved_review_schema.py tests/test_scan_naming.py tests/test_editarr_integration.py`
   passed 178 tests; `uv run ruff check scanner/repository.py
   tests/test_saved_review_repository.py` passed; `git diff --check` passed. Existing
   Starlette/httpx and FastAPI `on_event` deprecation warnings remain.
Manual or artifact evidence: Temporary SQLite tests cover empty/source snapshots, isolated
   filter edits, rename/content revision behavior, fresh duplicate filter IDs, references,
   and deletion. Active EDL provenance is reflected in summaries and scan deletion retains
   the legacy active-EDL error when no review reference exists.
Known failures or risks: HTTP error mapping and public routes remain NSR-04 scope.
Acceptance criteria still unmet: None within the repository scope.
Next exact action: Implement NSR-04 saved-review API and Manual save routes.
Next eligible task: NSR-04 is now eligible; NSR-05 remains independently eligible.
Decisions or deviations from the parent plan: None.
User approval required: None.

