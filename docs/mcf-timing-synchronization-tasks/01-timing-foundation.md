# MTS-01 - Build timing persistence and affine core

Status: **DONE**

Dependencies: MTS-00. Deliverable: fresh-schema review timing/provenance authorities, exact
cross-layer models/converters, and one pure affine retiming implementation.

Read the task-pack README, parent plan, MTS-00 handoff/contract record, repository
`AGENTS.md`, and the existing MCF/review persistence tests before changing code.

## Scope

1. Implement the MTS-00 fresh-schema fields/rows for review synchronization state, END mode,
   accepted credits provenance, and credits scan detector metadata. Keep imported source MCF
   document/finding authorities unchanged.
2. Update SQLAlchemy constraints, relationships, cascade ownership, row converters, Pydantic
   models, TypeScript contract fixtures where owned by this task, and fresh-schema tests.
3. Implement one pure affine timing helper used later by import and review retiming. It must:
   - validate finite ordered source and target intervals;
   - map individual endpoints and complete ranges with full internal precision;
   - preserve order and equal shared ranges;
   - avoid clamping endpoints outside the source interval;
   - reject non-finite, reversed, empty, negative, or target-runtime-overflow results;
   - leave all non-timing fields untouched when mapping structured entries.
4. Add exact unit/property-style vectors for identity, offset, stretch, compression, outlying
   endpoints, shared ranges, repeated current-basis mapping, and invalid denominators/results.
5. Extend review create/read/save/duplicate/delete primitives only enough to round-trip the
   new timing and accepted-provenance state according to the contract. Do not add the retime
   operation or import behavior owned by later tasks.
6. Implement the complete revision signatures frozen by MTS-00 and prove output-neutral
   provenance does not mark sidecars outdated.
7. If a checkout-owned development database must be recreated, this task alone may do so.
   Resolve and print the exact path, verify it is disposable and inside the checkout, verify
   no process is using it, cite the parent-plan approval, and then recreate it. Never touch
   the installed/user database or any path outside the checkout.

## Out of scope

- MCF import target fields or mapping service behavior (MTS-02).
- Saved-review retime route/operation (MTS-03).
- Credits detector, queue mode, finding creation, clips, or UI (MTS-04 through MTS-06).
- Preservation migrations for development data.

## Verification

- Ruff on changed Python files.
- Focused affine helper, schema constraint, row conversion, review lifecycle, duplication,
  deletion, revision, and OpenAPI/model tests using temporary databases.
- Existing MCF codec/import/export, EDL, defaults, and saved-review regression tests.
- `git diff --check`.

## Acceptance criteria

- [x] Fresh databases contain one clear authority for synchronization and accepted detector
      provenance with all MTS-00 constraints.
- [x] Imported scan evidence remains structurally and behaviorally immutable.
- [x] One tested pure helper implements every approved affine behavior and invalid case.
- [x] Review lifecycle operations preserve or remove new state according to ownership.
- [x] Revision/staleness behavior exactly matches the frozen matrix.
- [x] No migration exists solely to preserve v1 development data.
- [x] Any approved development reset is narrowly verified and recorded; installed/user data
      is untouched.

## Handoff

Status: DONE
Last completed substep: Hardened non-numeric/overflow affine validation and completed full
backend plus focused frontend verification.
Files changed: `scanner/models.py`, `scanner/db.py`, `scanner/mcf.py`,
`scanner/repository.py`, `scanner/api.py`, `tests/test_mts_timing.py`,
`tests/test_saved_review_schema.py`, `tests/test_saved_review_repository.py`,
`tests/test_mcf_export.py`, `tests/test_mcf_final_integration.py`,
`tests/test_export_state.py`, `tests/test_editarr_integration.py`,
`tests/test_scan_naming.py`, and this task file.
Schema/API/contract changes: Fresh schema adds `scans.scan_mode`,
`credits_detection_runs`, `review_synchronizations`, and `review_accepted_credits`.
Python/OpenAPI models add synchronization, scan-mode, detector-result, and accepted-credit
contracts. `SavedReview` and `ReviewSave` now require synchronization; older save clients
receive the frozen path-safe 422 response. Ordinary review creation uses authoritative
physical runtime in File-end mode without copying it into canonical MCF END. Existing MCF
imports retain their source timing basis until MTS-02.
Verification commands and results: `uv run ruff check scanner tests` passed; full backend
`uv run pytest -q --tb=short` passed 1,122 tests in 32.40 seconds; focused frontend Vitest
passed 2 files/25 tests; `pnpm build` passed; `git diff --check` passed.
Manual or artifact evidence: MTS-00 affine vectors drive `tests/test_mts_timing.py`; fresh
temporary SQLite tests cover constraints, cascades, conversion, duplication, deletion,
runtime rejection, and output-neutral synchronization/provenance revisions.
Known failures or risks: Frontend draft/save adoption remains intentionally assigned to
MTS-06. MTS-02 must replace the transitional source-basis MCF import initialization with the
submitted target synchronization contract.
Acceptance criteria still unmet: None.
Next exact action: Implement MTS-02 import synchronization or MTS-03 saved-review retiming
from the completed foundation; MTS-04 remains independently eligible.
Next eligible task: MTS-02, MTS-03, and MTS-04.
Decisions or deviations from the parent plan: No product deviations. No preservation
migration was added.
User approval required: None. No database reset was needed or performed; no installed/user
database was accessed.