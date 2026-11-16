# MTS-02 - Synchronize MCF imports

Status: **DONE**

Dependencies: MTS-01. Deliverable: protected upload/path MCF imports that preserve source
evidence and create target-synchronized saved reviews for every supported source.

Read the task-pack README, parent plan, MTS-00 and MTS-01 handoffs, repository `AGENTS.md`,
and the completed MCF import/recovery task handoffs before changing code.

## Scope

1. Extend the exact MTS-00 MCF import request contracts for target START, synchronization
   END, END mode, optional canonical before-credits END where distinct, and an optional
   accepted credits scan/candidate reference. Apply the same contract to upload and path
   transports.
2. Resolve media path and duration server-side exactly as today. Browser values never become
   path, runtime, scan-result, or physical-version authority.
3. Refactor parsing/validation so source MCF syntax and source bounds are parsed without
   comparing source cue seconds to the target file duration. Preserve all existing bounded
   input, encoding, grammar, metadata, payload, and recovery safety.
4. Make missing, invalid, equal, reversed, or recovery-synthesized source bounds fatal when
   synchronization is requested. `ignore_errors` cannot bypass this failure.
5. Use the MTS-01 affine helper to produce mapped review entries, then validate every mapped
   range and selected target bound against the authoritative target runtime.
6. Keep the imported scan document and findings byte-semantically faithful to valid source
   bounds/cue values. Create initial review snapshots from mapped entries only.
7. Persist review synchronization state and canonical MCF bounds according to END mode:
   - Before credits uses the entered/accepted END for both synchronization and MCF metadata;
   - File end uses the physical/corrected END for synchronization and retains a separate
     nullable before-credits END for MCF metadata.
8. If the request carries an accepted completed credits candidate, validate its target,
   physical version, scan status, finding identity, and candidate boundary server-side. Add
   its scan membership, selected finding snapshot, and accepted provenance to the new review
   in the import transaction. Do not mutate or recreate the credits scan.
9. Preserve target timing, END mode, canonical END, and accepted credits reference through
   the existing recoverable-issues response and Continue Anyway retry.
10. Keep scan/review/import creation atomic. Parse, mapping, target, candidate, defaults,
    validation, or commit failure leaves no import-owned scan, document, finding, review,
    membership, or snapshot and changes no active export.
11. Update safe structured errors, OpenAPI, auth/CSRF coverage, and API documentation.

## Out of scope

- Saved-review retiming after import (MTS-03).
- Credits detector execution, queue lifecycle, or clip generation (MTS-04/MTS-05).
- Import dialog controls (MTS-06).
- Mutating immutable source MCF bounds/cues to match the target.

## Verification

- Ruff on changed Python files.
- Pure import mapping tests with exact offset/stretch/compression/outlying/shared-range values.
- Repository rollback/default/provenance tests using temporary databases and generated media.
- Upload/path API tests for Jellyfin, Radarr, Sonarr, and Other Files.
- Strict/recovery tests proving source bounds cannot be ignored and target duration is checked
  only after mapping.
- Accepted credits scan validation and atomic membership/snapshot tests with test-owned rows.
- Existing MCF/EDL import, review, defaults, export-state, OpenAPI, auth, and CSRF tests.
- `git diff --check`.

## Acceptance criteria

- [x] Both transports and every source create identical synchronized review aggregates.
- [x] Original source bounds and cues remain exact on the imported scan.
- [x] Review bounds, mode, and mapped cues match the shared affine helper exactly.
- [x] Before-credits and File-end metadata behavior is distinct and validated.
- [x] Invalid source bounds remain non-bypassable while unrelated recovery behavior remains
      unchanged.
- [x] Accepted credits scans join the review only after authoritative identity validation.
- [x] All failures roll back import-owned state and leave exports untouched.

## Handoff

Status: DONE
Last completed substep: Full backend regression after live DTO/OpenAPI validation,
source-independent recovery parsing, structured timing errors, and accepted-credits rollback
coverage.
Files changed: `scanner/models.py`, `scanner/mcf.py`, `scanner/repository.py`,
`scanner/api.py`, `tests/test_mcf.py`, `tests/test_mcf_import.py`,
`tests/test_mcf_final_integration.py`, `tests/test_mts_contract.py`,
`tests/test_scan_naming.py`, `docs/api-contract.md`, this task, and the task-pack README.
Schema/API/contract changes: No schema change. `McfImportOptions` now exactly requires the
frozen synchronization, canonical material END, and accepted-credits fields. Both MCF
transports parse source timing independently from target runtime and return the frozen safe
timing/acceptance errors. Import persistence maps review snapshots only and can atomically
attach validated detector provenance.
Verification commands and results: Changed-file Ruff passed. Focused parser/import/review,
transport, auth/CSRF, OpenAPI, target-error, END-mode, accepted-credits, and rollback suites
passed. Full backend pytest passed 1,188 tests in 34.28 seconds with no warnings. `git
diff --check` is rerun as the final check.
Manual or artifact evidence: Temporary SQLite databases and generated media paths covered
manual, Jellyfin, Radarr, and Sonarr targets; no user database, media, sidecar, service, or
model artifact was accessed.
Known failures or risks: The current frontend still sends the pre-MTS-02 MCF import shape;
MTS-06 owns timing controls and frontend contract adoption. Until MTS-06 is implemented,
interactive MCF imports receive the intended validation error rather than inferred timing.
Acceptance criteria still unmet: None.
Next exact action: Implement MTS-06 frontend timing and credits workflows.
Next eligible task: MTS-06.
Decisions or deviations from the parent plan: None. Existing valid source timing is never
clamped to target duration; unrelated recoverable metadata/cue issues retain Continue Anyway.
User approval required: None. No database reset or model acquisition was performed.