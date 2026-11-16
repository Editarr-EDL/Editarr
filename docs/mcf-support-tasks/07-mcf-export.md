# MCF-07 - Export saved reviews to MCF

Status: **DONE**

Dependencies: MCF-02, MCF-04, and MCF-06. Deliverable: strict persisted-review preflight,
atomic MCF 1.1 sidecar export, independent provenance, and format-specific deletion/status.

## Scope

1. Implement MCF preflight over one persisted saved review and expected resource revision.
   Revalidate membership, completed scans, associations, physical path/version, media
   readability, review metadata, material bounds, and every selected filter.
2. Require a registry-valid category/subcategory pair and severity for each selected
   filter. Apply the MCF-00 channel rule and strict cue-comment validation. Return all
   stable filter/field issues in one safe result so the UI can navigate to them.
3. Do not classify, apply defaults, mutate the review, omit invalid selected filters, or
   derive severity from confidence. Dirty drafts remain a frontend block; server revision
   checking protects persisted authority.
4. Convert selected review snapshots and editable review metadata into the pure codec
   input. Preserve exact ranges/order and group only equal start/end ranges. Do not call
   EDL normalization.
5. Write `<media-stem>.mcf` as strict deterministic 1.1.0 UTF-8 without BOM using the
   established sibling temporary file, fsync, atomic replace, rollback-copy, and recovery
   guarantees.
6. Only after replacement succeeds, record MCF format revision, review attribution,
   timestamp, and all contributing scan roles in generic export state. A commit failure
   restores only the prior MCF sidecar/state and leaves EDL untouched.
7. Expose the MCF-00 review-ID preflight/export responses and integrate generic
   format-specific status/delete routes for each source. Preserve auth, same-origin, CSRF,
   OpenAPI, and safe errors.
8. Add exact-byte integration tests plus preflight, empty-selection behavior frozen by
   MCF-00, metadata, equal-range grouping, incomplete filters, stale revision, all sources,
   coexistence, re-export, rollback, deletion, and MCF-only/EDL-only outdated tests.

## Out of scope

- Export menu/preflight repair UI (MCF-10).
- MCF 1.0 export.
- Generic WebVTT extensions or EDL normalization.

## Verification

- Ruff changed Python files.
- Focused codec/export/preflight/generic-status/API/auth/OpenAPI tests.
- Existing complete EDL export/delete/round-trip regression set.
- Temporary media/database atomicity and recovery tests.
- `git diff --check`.

## Acceptance criteria

- [x] Only a valid saved review produces deterministic MCF 1.1.0 bytes.
- [x] Preflight reports every actionable issue and export performs no hidden completion.
- [x] Equal ranges group; overlap alone does not merge; EDL rules never affect MCF.
- [x] Successful export records only MCF provenance/revision after atomic replacement.
- [x] Every failure preserves prior MCF and all EDL state.
- [x] Cross-format import/export workflows pass focused integration tests.

## Handoff

Status: DONE
Last completed substep: Completed all-source/cross-format integration, exhaustive safe
preflight coverage, atomic recovery tests, and the full backend verification run.
Files changed: `scanner/models.py`, `scanner/repository.py`, `scanner/api.py`,
`scanner/auth_policy.py`, `tests/test_mcf_export.py`, `tests/test_scan_naming.py`,
`tests/test_saved_review_schema.py`, this task record, and the task-pack README.
Schema/API/contract changes: Added `McfPreflightIssue` and `McfPreflight`; added protected
`GET /api/reviews/{review_id}/mcf/preflight` and `POST /api/reviews/{review_id}/mcf`;
the export returns generic `ReviewExportResponse` and persists only MCF format revision,
attribution, timestamp, and scan-role provenance. Incomplete export errors include ordered
filter IDs and document fields without values.
Verification commands and results: Complete MCF preflight/export module passed 11 tests;
focused API/all-source/cross-format checks passed 7 tests; broad codec/import/export/state/
review/EDL/API/auth regression passed 441 tests. Final `uv run ruff check scanner tests
scripts native_build` passed; final `uv run pytest -q` passed 1,056 tests in 29.91 seconds
without warnings. VS Code reported no diagnostics in changed source/test files.
Manual or artifact evidence: Exact-byte tests prove 1.1.0 output, canonical `=both`,
equal-range grouping in review order, overlap separation, and bounds-only output for empty
selection. Temporary sidecar/database tests prove replacement and commit failure recovery,
recovery-backup retention, EDL isolation, independent stale/current transitions, and real
MCF-to-EDL coexistence. Jellyfin, Radarr, Sonarr, and Other Files reviews export through the
same protected review route.
Known failures or risks: None recorded.
Acceptance criteria still unmet: None.
Next exact action: Implement MCF-08 frontend contracts/settings now that MCF-03 through
MCF-07 are DONE.
Next eligible task: MCF-08 after MCF-03 through MCF-07 are DONE.
Decisions or deviations from the parent plan: Empty selection exports a valid MCF 1.1.0
bounds-only document, consistent with the frozen zero-cue codec contract. Null review
channel serializes canonically as `=both`; export never writes defaults back to the review.
User approval required: None.
