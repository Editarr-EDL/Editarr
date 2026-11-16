# CSS-43 — Extract reusable import transactions without behavior changes

Status: **DONE** October 3, 2026, in local/isolated-test scope. Focused EDL/MCF
workflows, metadata preparation and shared row staging/rollback helpers preserve
the existing import behavior and stable facades. No community import is added.

- Prerequisites: [CSS-06](06-migration-startup-cli.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §5.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

repository_imports.py is already large. Community imports should reuse focused EDL/MCF workflow services instead of expanding a catch-all.

## Starting points

scanner/persistence/repository/repository_imports.py; scanner/formats/; stable scanner/repository.py facade.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Extract EDL/MCF parsing, metadata preparation and commit/rollback helpers along existing workflow boundaries.
2. Preserve stable public import surfaces and all current externally visible import behavior.
3. Document reusable scan/evidence transaction seam for a future community adapter; add no community feature in this task.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Existing imports retain IDs, naming, metadata, review isolation and rollback behavior.
- [x] Each module stays focused and within repository size guidance.
- [x] No schema or file-format semantics change merely to simplify the refactor.

## Verification

- Run existing MCF/EDL import, rollback, naming and review repository tests.
- Inspect facade imports and installed-package import smoke.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide module map and shared transaction seam to CSS-44; list any tests moved without reducing coverage.

## Handoff summary for the next task

Completed October 3, 2026 by GitHub Copilot; uncommitted workspace changes.
No new decision gate or product policy: CSS-06 admission remains unchanged, and
the user's October 3 instruction requires asking before undefined major choices.

### Module map and preserved surfaces

All implementation paths below are under `scanner/persistence/repository/`:

| Module | Responsibility | Lines |
| --- | --- | --- |
| `repository_imports.py` | Stable re-exports of both public workflows and the two existing private exports | 6 |
| `import_edl.py` | EDL scan/findings, isolated review/defaults, warning and result | 187 |
| `import_mcf.py` | MCF timing/credits validation, immutable source evidence, mapped review copies and result | 326 |
| `import_metadata.py` | EDL catalog preparation, MCF source/review separation, gap filling and mismatch notices | 82 |
| `import_transaction.py` | Uncommitted scan/review staging, scoped rollback and committed-row refresh | 99 |

`scanner.repository` still exposes `import_edl_as_saved_review`,
`import_mcf_as_saved_review`, `_import_mcf_as_saved_review` and
`_mcf_metadata_notices` with unchanged signatures. HTTP/API imports remain intact.
Runtime calls through that facade preserve existing validator/hash/defaults
patch points. UUID allocation, scan numbering, names, timestamps, imported
capability/model labels, EDL unknown evidence and MCF source metadata remain
unchanged. Review defaults and affine mapping affect only editable copies.

Parsing was already isolated in `scanner/formats/edl.py`, `mcf_parser.py` and
`mcf_recovery.py`, with the stable `scanner.mcf` facade. Those codecs and the
HTTP path/upload, size/recovery and target-resolution behavior were reused
unchanged rather than duplicated or relocated.

### Shared transaction boundary for CSS-44

`stage_imported_scan(db, ...)` flushes a scan and optional source MCF document;
`stage_imported_review(db, ...)` flushes the isolated review. Neither commits.
The caller adds immutable findings, memberships, review metadata/filters and
any separately validated provenance rows to the **same Session**, then commits
once. `rollback_on_import_error(db)` rolls back and re-raises any Exception
inside the caller's chosen boundary; it does not commit or open a nested
transaction. `refresh_imported_rows(db, scan, review)` runs after successful
commit, before result construction.

Existing boundaries are deliberately preserved: EDL catches commit failures;
preparation and result failures remain outside that boundary. Public MCF wraps
validation, staging, commit and result construction; the existing private MCF
function does not independently add rollback. A post-commit failure cannot
undo committed rows. The helpers are not an idempotency or retry mechanism.

Do **not** call the committing public EDL/MCF functions and then attach community
provenance in a second transaction. CSS-44 must first verify the exact artifact,
then use the shared staging/rollback boundary with its own evidence projection
and one commit. Legacy staging intentionally retains `origin=imported` and
`imported` capability/model labels; these are not community execution provenance
and must not overwrite verified artifact metadata. No community trust, receipt,
deduplication, retiming or schema policy is selected by this refactor.

### Verification and limitations

No tests were moved, deleted or weakened. Existing tests were extended in
`tests/test_mcf_import_rollback.py` (three file-backed staging/commit cases),
`tests/test_review_repository_import.py` (facade identity), and
`tests/test_migration_packaging.py` (isolated installed-module/facade smoke).
Independent sessions see no staged rows before commit; staging and commit faults
remove all staged scan/document/finding/review/membership rows while retaining
the existing media item.

Actual commands and results:

```sh
.venv/bin/python -m pytest -q tests/test_edl.py tests/test_mcf.py tests/test_mcf_contract.py tests/test_mcf_import_core.py tests/test_mcf_import_metadata.py tests/test_mcf_import_rollback.py tests/test_mcf_final_integration.py tests/test_editarr_reviews.py tests/test_scan_naming_import.py tests/test_scan_naming_review_api.py tests/test_review_repository_create.py tests/test_review_repository_save.py tests/test_review_repository_import.py tests/test_review_repository_integrity.py tests/test_review_repository_export.py tests/test_review_retime.py tests/test_review_retime_provenance.py tests/test_migration_packaging.py
# 254 passed; includes temporary isolated wheel/sdist build, wheel rebuilt from
# sdist, packaged-module/facade imports and installed application startup.
.venv/bin/ruff check scanner/persistence/repository/import_edl.py scanner/persistence/repository/import_mcf.py scanner/persistence/repository/import_metadata.py scanner/persistence/repository/import_transaction.py scanner/persistence/repository/repository_imports.py tests/test_mcf_import_rollback.py tests/test_review_repository_import.py tests/test_migration_packaging.py
# Passed after formatting-only corrections.
.venv/bin/python -m pytest -q tests/test_migration_packaging.py tests/test_review_repository_import.py tests/test_mcf_import_core.py tests/test_mcf_import_metadata.py tests/test_mcf_import_rollback.py tests/test_scan_naming_import.py
# 36 passed after those corrections, including the four new regression cases.
```

Final checks: editor diagnostics found no errors in all eight touched Python
files; the Node documentation check passed 107 local links, balanced fences and
CSS-43 completion status across the three changed documents; `git diff --check`
passed.

No contracts, migrations, schema/format changes or persistent state introduced.
No existing database, user media, sidecar, live service, frontend or deployment
touched. No full-suite, frozen-native or other-platform verification claimed;
these remain separate integration/release gates. No known scoped test failure.

Remaining task work: none. Next exact action: **CSS-44**, read
[44-verified-local-import.md](44-verified-local-import.md), CSS-13/21/29/42
handoffs and this module map, then locate the existing community provenance and
operation transaction before implementing verified artifact projection and
idempotent one-commit import. Ask the user before any undefined trust/receipt or
mapping policy; do not treat this refactor as approval for downstream behavior.

Direct dependent tasks: [CSS-44](44-verified-local-import.md).

