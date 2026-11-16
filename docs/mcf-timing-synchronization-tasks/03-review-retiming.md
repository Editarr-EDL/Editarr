# MTS-03 - Add saved-review retiming

Status: **DONE**

Dependencies: MTS-01. Deliverable: an authoritative non-persistent draft retiming operation,
saved-review timing lifecycle, and correct EDL/MCF revision/preflight behavior.

Read the task-pack README, parent plan, MTS-00 and MTS-01 handoffs, repository `AGENTS.md`,
and existing review/default-apply/export tests before changing code.

## Scope

1. Add the exact MTS-00 protected retiming operation following the existing Apply Filter
   Defaults pattern: accept complete current draft timing/document state, persisted review
   identity/revision as required by contract, all review filter inputs, and staged manual
   filters; return transformed draft state without writing it.
2. Resolve the review's target physical file and authoritative duration server-side. Reject
   missing/stale reviews, physical-version mismatch, missing current basis, invalid source or
   target intervals, invalid draft provenance, malformed filters, and mapped runtime overflow
   with safe structured errors.
3. Map all filters in draft order, including selected, disabled, source-linked, and staged
   manual filters. Preserve every non-timing field and provenance identifier exactly.
   If accepted credits are present, map the accepted boundary with its review snapshot while
   preserving the detector's original detected boundary and immutable evidence.
4. Map from the current review synchronization interval and current draft cue times. Never
   reload original imported findings as the timing source.
5. Return new synchronization START/END/mode and canonical MCF bounds:
   - Before credits sets canonical START/END from the new interval;
   - File end stores the new synchronization END separately and preserves or accepts the
     distinct canonical before-credits END.
6. Extend normal review save/read/duplicate behavior as needed so returned draft state
   round-trips through optimistic save. No retiming endpoint writes rows or revisions.
7. Apply the frozen revision matrix on save. Cue timing changes make both EDL and MCF exports
   outdated. Canonical MCF-only metadata changes affect MCF only. Detection diagnostics alone
   do not affect sidecar currency.
8. Update MCF preflight/export so a valid canonical before-credits END is required regardless
   of synchronization mode. Never substitute physical file duration silently. Preserve EDL
   export eligibility for otherwise valid selected actions.
9. Add API/OpenAPI, auth/CSRF, stale-revision, no-op, validation, and error-detail tests.

## Out of scope

- Import-time synchronization (MTS-02).
- Credits detection/scan execution or preview (MTS-04/MTS-05).
- Adjust Cue Times UI or frontend draft application (MTS-06).
- Persisting a retimed draft before the user invokes normal Save.

## Verification

- Ruff on changed Python files.
- Focused operation tests for identity, offset, stretch, compression, repeat adjustment from
  current basis, outlying cues, every selection/source type, staged manual filters, and
  invalid mapped ranges.
- Review save/no-op/stale/duplicate/revision and format-specific staleness tests.
- MCF preflight/export and EDL export regression tests for both END modes.
- Live OpenAPI plus authentication/CSRF coverage.
- `git diff --check`.

## Acceptance criteria

- [x] One non-persistent backend authority retimes the complete current draft.
- [x] Repeat adjustment preserves intervening manual edits and never reads source cue times.
- [x] Accepted credits retiming updates review-owned timing without rewriting detection evidence.
- [x] All cue and non-cue fields follow the parent plan exactly.
- [x] Normal Save/Discard remains the only persistence boundary.
- [x] EDL/MCF revision and outdated state match the frozen matrix.
- [x] MCF export cannot use File-end synchronization END as canonical material END.
- [x] Existing reviews, defaults, previews, imports, and exports remain passing.

## Handoff

Status: DONE
Last completed substep: Added precise current/proposed runtime validation and completed the
full backend regression suite.
Files changed: `scanner/models.py`, `scanner/repository.py`, `scanner/api.py`,
`scanner/auth_policy.py`, `tests/test_review_retime.py`, `tests/test_scan_naming.py`,
`tests/test_saved_review_schema.py`, `docs/api-contract.md`, and this task file.
Schema/API/contract changes: Added strict `ReviewRetimeRequest`/`ReviewRetimeResult` and
protected `POST /api/reviews/{review_id}/retime`. The operation probes the trusted physical
file, validates current draft provenance and timing, maps source-linked and staged manual
filters through the shared affine authority, returns mode-correct MCF bounds, and performs no
write.
Verification commands and results: Ruff passed; focused retime repository/API/OpenAPI/auth
tests passed; full backend `uv run pytest -q --tb=short` passed 1,131 tests in 32.58 seconds;
`git diff --check` passed before handoff update.
Manual or artifact evidence: Tests cover identity, offset, stretch, compression, repeated
unsaved retiming after a manual cue edit, disabled/source-linked/manual entries, Save
round-trip, both format revisions, stale revision, forged provenance, current/proposed
runtime overflow, mapped outliers, unavailable media, authentication, CSRF, and no
persistence before Save.
Known failures or risks: The frontend does not call the route until MTS-06. MTS-02 remains
responsible for replacing transitional source-basis MCF import initialization.
Acceptance criteria still unmet: None.
Next exact action: Implement MTS-02 or MTS-04; MTS-06 remains blocked on MTS-02 and MTS-05.
Next eligible task: MTS-02 and MTS-04.
Decisions or deviations from the parent plan: None.
User approval required: None.
