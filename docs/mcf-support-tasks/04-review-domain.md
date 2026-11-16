# MCF-04 - Extend the review domain

Status: **DONE**

Dependencies: MCF-01 and MCF-03. Deliverable: saved reviews and manual filters carry the
canonical taxonomy/metadata, apply defaults, and maintain exact format revisions through repository
and public review APIs.

## Scope

1. Extend source-finding, review-filter, manual-filter, review detail/summary, and save DTOs
   with canonical category/subcategory, severity, channel/comment, and metadata shapes.
   Unclassified legacy EDL snapshots may remain null; new manual filters require a valid
   registry pair.
2. Update converters, snapshot paths, duplication, full replacement, and staged Manual
   transactions to copy/validate classification without mutating source evidence.
3. Add editable review MCF document metadata to create/save/duplicate. Seed a new review
   from the explicitly chosen imported-scan metadata candidate, then trusted catalog/runtime
   gaps; default material bounds to zero/runtime. Duplicate copies the current review copy.
4. When distinct included imported scans offer conflicting metadata, expose stable safe
   candidate identities and require the MCF-00 source choice. Never merge or pick by order.
5. Invoke the shared defaults evaluator in the confirmed modes:
    - source snapshots: preserve classification, fill severity/channel, evaluate selection;
    - staged Manual filters: require classification, fill severity/channel, evaluate selection;
    - explicit Apply: recompute severity/channel and selection without saving automatically.
6. Implement the exhaustive resource/aggregate/EDL/MCF revision matrix. A save increments
   each affected revision once, combined edits do not double-increment, normalized no-ops
   retain timestamps/revisions, and stale requests change nothing.
7. Return format-specific export summaries on review list/detail without implementing MCF
   file export. Preserve the MCF-00 EDL compatibility adapter.
8. Ensure previews remain governed by Editarr source/action/range, not MCF channel. MCF-only
   edits must not change which preview endpoint is valid.
9. Add exhaustive repository/API/OpenAPI/auth/CSRF tests for optionality, validation,
   source isolation, manual atomicity, metadata seeding/conflicts, Apply behavior,
   duplication, revisions, no-ops, and stale writes.

## Out of scope

- MCF parsing/import orchestration (MCF-05).
- Generic sidecar lifecycle behavior (MCF-06) or MCF export (MCF-07).
- React UI.

## Verification

- Ruff changed Python files.
- Focused saved-review repository/schema/API/manual/default/revision tests.
- Existing EDL review export tests to ensure DTO compatibility.
- Temporary-database isolation and rollback tests.
- `git diff --check`.

## Acceptance criteria

- [x] Unclassified legacy EDL filters remain valid and EDL-capable.
- [x] New/manual/MCF classification pairs validate and survive all review round trips.
- [x] Review metadata is editable/isolated; scan source metadata remains immutable.
- [x] Conflicting metadata sources require a user choice and never silently merge.
- [x] New-review and Apply defaults match the confirmed overwrite/fill modes.
- [x] Every editable field has proven format-specific revision behavior.

## Handoff

Status: DONE
Last completed substep: Hardened review mutations with atomic revision claims, verified
manual/catalog runtime seeding, and ran the complete backend suite.
Files changed: `scanner/models.py`, `scanner/repository.py`, `scanner/api.py`,
`tests/test_saved_review_repository.py`, `tests/test_saved_review_schema.py`,
`tests/test_editarr_integration.py`, `tests/test_scan_naming.py`, this task record, and the
task-pack README.
Schema/API/contract changes: Added discriminated MCF metadata seed requests and safe
metadata candidates; made `ReviewSave.mcf_document` required; expanded safe review errors;
and carried trusted catalog/runtime metadata into review creation and EDL import. Existing
MCF-01 metadata and format-revision columns remain the persistence authority. Review save,
rename, duplicate, and delete now use persisted optimistic revision guards.
Verification commands and results: Exhaustive field/structural revision matrix passed 25
tests. Complete review/default/EDL/API/auth focused verification passed 297 tests without
warnings. Final `uv run ruff check scanner tests scripts native_build` passed; final `uv
run pytest -q` passed 1,019 tests in 29.08 seconds without warnings. VS Code reported no
diagnostics in changed source/test files.
Manual or artifact evidence: Temporary-database tests prove source snapshot isolation,
manual default persistence, editable/source metadata separation, ordered imported metadata
candidates plus catalog, explicit source choice, rollback on conflicts, duplicate metadata
isolation, and EDL current state after MCF-only edits. A file-backed two-session test proves
stale changed and stale unchanged full saves cannot replace the winner.
Known failures or risks: The React review client does not yet send/edit the required MCF
metadata or canonical fields; MCF-08/09 own that transitional frontend update.
Acceptance criteria still unmet: None.
Next exact action: MCF-05 may implement strict MCF import using the metadata candidate and
defaulted review-domain contracts; MCF-06 may independently generalize sidecar lifecycle.
Next eligible task: MCF-05 and MCF-06.
Decisions or deviations from the parent plan: Catalog metadata is passed into repository
creation as a trusted internal `ReviewMcfDocument`; repository fallback uses persisted
title/type and selected-scan runtime. No migration or installed/user database operation was
performed.
User approval required: None.
