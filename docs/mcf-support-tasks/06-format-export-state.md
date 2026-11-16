# MCF-06 - Generalize export state

Status: **DONE**

Dependencies: MCF-01 and MCF-04. Deliverable: format-keyed EDL/MCF status, provenance,
deletion, scan guards, and EDL lifecycle conversion with no EDL behavior regression.

## Scope

1. Implement generic repository/service operations over the format-keyed export rows from
   MCF-01. Logical identity is media item, normalized physical version, and `edl | mcf`.
2. Refactor the existing successful EDL export transaction to record its EDL format
   revision and generic scan provenance while preserving byte output, destination,
   validation, atomic replacement, rollback backup, and error behavior.
3. Return generic export summaries/statuses on media items, versions, saved reviews, and
   scan references. Keep the exact MCF-00 EDL compatibility adapter until MCF-11.
4. Compute current/outdated independently from each format's saved review revision. An MCF
   row may exist before MCF-07 only in focused repository tests; no placeholder file or
   public fake export is created.
5. Make deletion format-specific for Jellyfin, Radarr, Sonarr, and Other Files. Deleting
   EDL cannot unlink/delete MCF and vice versa. Filesystem failure retains the matching
   database row; a missing state uses the frozen not-found behavior.
6. On saved-review deletion, preserve every sidecar and its scan provenance while clearing
   that review's attribution/revision from each matching format row.
7. Block scan deletion when referenced by a saved review or any active format. Return all
   export dependencies and review references in one structured result; do not stop at the
   first format.
8. Ensure media/version decoration and list filtering remain efficient and do not multiply
   catalog rows when both formats exist.
9. Add repository/API/OpenAPI/auth/CSRF and atomic file tests covering EDL conversion,
   coexistence, independent state/deletion, review deletion, scan guards, failed commit/
   restoration, multiple versions, and compatibility responses.

## Out of scope

- Rendering or writing MCF content (MCF-07).
- Frontend badges/delete controls (MCF-10).
- Removing EDL compatibility fields (MCF-11).

## Verification

- Ruff changed Python files.
- Focused generic export-state, EDL export/delete, scan-reference, catalog-decoration,
  API/auth/OpenAPI, and filesystem rollback tests.
- Existing EDL golden/round-trip tests.
- Temporary media and SQLite only.
- `git diff --check`.

## Acceptance criteria

- [x] Existing EDL behavior uses format-keyed state without output/lifecycle regression.
- [x] EDL and MCF rows coexist and never clear or stale one another incorrectly.
- [x] Format-specific revisions produce exact independent current/outdated state.
- [x] Deletion/review deletion/scan guards report and change only approved state.
- [x] Catalog and Other Files expose complete generic status with temporary compatibility.

## Handoff

Status: DONE
Last completed substep: Batched catalog version-status decoration, completed generic
source routes and deletion rollback handling, and ran the full backend suite.
Files changed: `scanner/models.py`, `scanner/repository.py`, `scanner/api.py`,
`scanner/auth_policy.py`, `tests/test_export_state.py`,
`tests/test_editarr_integration.py`, `tests/test_scan_naming.py`,
`tests/test_saved_review_schema.py`, this task record, and the task-pack README.
Schema/API/contract changes: Added `exports: ExportStatus` to catalog item/version DTOs;
added generic `ReviewExportResponse` fields to the EDL response while retaining its legacy
alias; added GET and format-specific DELETE export routes for Jellyfin, Radarr/Sonarr, and
Other Files. Internal export writes, status, attribution clearing, provenance, and scan
guards now use generic format-keyed rows and revisions.
Verification commands and results: Dedicated coexistence/deletion/version/catalog tests
passed 6 tests; generic HTTP/OpenAPI/auth tests passed 6 tests; complete focused
export-state/EDL/review/catalog/auth regression passed 301 tests. Final `uv run ruff check
scanner tests scripts native_build` passed; final `uv run pytest -q` passed 1,044 tests in
30.04 seconds without warnings. VS Code reported no diagnostics in changed source/test
files.
Manual or artifact evidence: Temporary media/database tests prove independent EDL/MCF
current/outdated state, per-format deletion, filesystem and commit failure preservation,
review deletion attribution clearing with file/provenance retention, all-format scan guards,
and physical-version isolation. Catalog decoration batches version statuses and does not
duplicate result rows.
Known failures or risks: MCF rows are created only by focused repository tests until
MCF-07 implements real MCF rendering/export. Legacy EDL fields/routes remain compatibility
projections until MCF-11.
Acceptance criteria still unmet: None.
Next exact action: Implement MCF-07 strict preflight and atomic MCF sidecar export over the
generic state authority.
Next eligible task: MCF-07.
Decisions or deviations from the parent plan: Successful format deletion stages the
matching sidecar with an atomic rename, commits row deletion, then best-effort removes the
temporary backup; commit failure restores the matching file. No fake MCF public export was
introduced.
User approval required: None.
