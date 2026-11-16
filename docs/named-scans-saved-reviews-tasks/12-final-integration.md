# NSR-12 — Remove legacy paths and run final verification

Status: **DONE**

Dependencies: NSR-06, NSR-07, and NSR-11. Deliverable: one authoritative review model,
complete documentation, full regression/accessibility/browser evidence, and release-ready
handoff.

Read the task-pack README, parent plan, every prior task/handoff, repository `AGENTS.md`,
and the current authoritative docs first.

## Why this task exists

Intermediate tasks may retain old scan-level selection/edit/export paths so the application
continues to build. Shipping both would permit disagreement with saved reviews. This task
removes transitional authorities, audits every requirement, and runs the complete suite.

## Scope

1. Verify all prior handoffs and inspect the aggregate diff before editing. Reopen a prior
   task as `IN PROGRESS`/`BLOCKED` if its focused evidence does not reproduce.
2. Remove or permanently make read-only every obsolete authority identified by NSR-00:
   - scan-level selected state as saved review truth;
   - scan finding content edit/delete endpoints used for review edits;
   - source-finding preview endpoints where the review UI requires snapshot timing;
   - arbitrary scan-array EDL export endpoint;
   - frontend API helpers/hooks/state/tests for ephemeral review persistence;
   - duplicate Other Files review logic superseded by shared saved-review components.
3. Keep source scan findings available as immutable/reusable evidence and for preview
   provenance. Ensure no UI/API can mutate them in a way that changes existing reviews.
4. Audit API authorization/OpenAPI and safe errors for scan rename, review operations,
   export, upload import, and path import. Unknown/legacy routes follow the documented
   authenticated not-found behavior.
5. Audit deletion/reference behavior:
   - scan block lists all reviews plus active EDL dependency;
   - review delete never deletes scans;
   - marked-review delete retains sidecar/scan provenance and clears review provenance;
   - Delete EDL affects only selected version and clears marker;
   - imported scans remain renameable/reusable after review deletion.
6. Update `docs/feature-design-spec.md` as the new authoritative baseline, including
   vocabulary, domain relationships, review snapshot lifecycle, Manual reuse, naming,
   import syntax/comments/warnings/limits, export/outdated state, and deletion rules.
7. Update `docs/api-contract.md`, README user workflows, and OpenAPI examples. Document the
   completed v1 schema reset/no-migration policy without implying installed databases were
   wiped or migrated.
8. Add/finish end-to-end browser coverage for the parent plan's ten-step acceptance flow
   across Jellyfin multi-version, Radarr, Sonarr, and Other Files using fake services and
   temporary media/EDLs.
9. Run full backend/frontend/build/browser regression. Fix only failures caused by this
   feature; report unrelated failures accurately.
10. Complete this task's audit table mapping every parent-plan outcome and definition-of-
    done item to code/tests/docs evidence. Mark the parent plan and README checklist
    complete only when all evidence passes.

## Required full verification

At minimum, using repository-standard isolated fixtures:

- `uv run ruff check scanner tests scripts`
- `uv run pytest -q`
- in `web/`, `pnpm test`
- in `web/`, `pnpm build`
- the repository's relevant Playwright/browser command(s), recording exact projects/counts
- `git diff --check`

Also run a fresh temporary-database startup/restart persistence smoke. Do not start the
user's real instance or use its data.

## Acceptance criteria

- [x] Only saved reviews own editable/selected review state and drive export.
- [x] All legacy competing endpoints/helpers/state are removed or demonstrably read-only.
- [x] Named scans/reviews, snapshot isolation, Manual reuse, import, comments, export marker,
      Outdated, deletion, and source/version parity satisfy the parent plan.
- [x] Security, path trust, atomic filesystem behavior, accessibility, and restart
      persistence have explicit evidence.
- [x] Authoritative feature/API/user docs exactly match implementation.
- [x] Full lint/backend/frontend/build/browser suites pass, or the task is not `DONE`.
- [x] Audit table, task handoff, master checklist, and parent status are current.
- [x] Shared definition of done is satisfied.

## Verifier instructions

This is the final verification task and requires independent review after implementation.
The verifier must inspect the full branch/worktree diff, trace one imported and one AI
filter from source finding through two isolated reviews to EDL output, exercise every
destructive rule, rerun full commands, and compare docs/OpenAPI. Do not accept focused-only
evidence or skipped browser coverage as DONE.

## Handoff requirements

Include aggregate files changed, schema/API removals/additions, exact full test counts and
durations, browser projects, temporary startup/restart evidence, database-reset disclosure,
remaining risks, and the completed requirement-to-evidence audit. `Next eligible task`
must say release/review handoff rather than inventing another feature task.

## Audit table — complete during the task

| Requirement | Code evidence | Test evidence | Documentation evidence | Result |
| --- | --- | --- | --- | --- |
| Optional mutable scan names | `scanner/repository.py`, `web/src/App.tsx` | scan naming and frontend suites | NSR-02/09 handoffs; feature spec baseline | PASS |
| Saved review CRUD/list/sort/default open | `scanner/repository.py`, `scanner/api.py`, `SavedReviewPanel` | saved-review repository/API tests; frontend suite | NSR-03/04/10 handoffs | PASS |
| Review-owned isolated snapshots | review filter rows and `review-draft.ts` | repository isolation + draft tests | feature spec baseline | PASS |
| Manual scan reuse and draft atomicity | Manual repository/API paths | integration and import tests | NSR-03/04/11 handoffs | PASS |
| Review-driven export and Outdated marker | `export_saved_review_edl`, review export route | EDL/integration tests | NSR-06/10 handoffs | PASS |
| Upload/path EDL import for all sources | shared import service/dialog | EDL/import and frontend suites | NSR-07/11 handoffs | PASS |
| Comment round trip and accepted range loss | `scanner/edl.py` codec | `tests/test_edl.py` and integration tests | NSR-05 handoff | PASS |
| Reference-aware deletion | review/scan repository guards and shared reference-aware web confirmation | integration/repository and production-component tests | NSR-03/06 handoffs | PASS |
| Security/path/atomicity/accessibility | auth policy, bounded acquisition, atomic writes | backend import security/limit/rollback tests; Chromium 31/31 including saved-review accessibility audits | API/feature specs | PASS |
| Restart persistence and legacy removal | production App renders only saved-review authority; legacy helpers and backend routes are absent | file-backed SQLite dispose/reopen test; cohesive review/import and all-source browser acceptance | feature/API specs and NSR-12 handoff | PASS |

An independent post-implementation review then found and closed the remaining backend and
documentation gaps: nested validation paths are redacted, duplicate source provenance maps to
`invalid_membership`, scan-owned export/preview and immediate Manual-finding DTOs/repository
helpers are absent, failed Manual staging self-rolls back, name sorting uses exact Unicode casefolding, EDL
status timestamps are UTC-aware, OpenAPI declares structured review/import errors, and the
sidecar replacement has no rename-away gap. Database rollback restores a previous sidecar; if
restoration itself fails, the known-good backup is retained for recovery. Production-path tests
cover each behavior.

## Handoff

Status: DONE
Last completed substep: Independently re-audited the production and test paths. Fixed complete
   catalog EDL provenance synchronization, immediate Other Files scan refresh after import/review
   save/rename, reference-aware scan deletion confirmation, original filter-order preservation in
   merged EDL comments, unsupported-action classification, stale legacy route inventory, and
   export-response consistency when a review advances during export.
Files changed: `README.md`, `docs/api-contract.md`, `docs/feature-design-spec.md`, the parent
   plan, task-pack/task handoffs, this audit file, `scanner/api.py`, `scanner/auth_policy.py`,
   `scanner/edl.py`, `scanner/models.py`, `scanner/repository.py`, backend review/schema/
   integration/naming/EDL tests, `web/src/App.tsx`, `web/src/api.ts`, `web/src/api.test.ts`,
   `web/src/review-draft.ts`, `web/src/review-draft.test.ts`, and `web/e2e/app.spec.ts`.
Schema/API/contract changes: No NSR-12 schema migration was added. The approved v1 fresh-schema
   policy is documented without claiming an installed database wipe. Removed obsolete web DTOs
   and helpers for scan finding mutation/selection/preview and scan-array export; the protected
   route inventory and docs now expose saved reviews/imports as the sole writable authority.
Verification commands and results: Ruff passed. Full pytest passed 845 tests without warnings in
   26.33 seconds. The focused EDL/saved-review/schema/naming/
   integration suite passed 217 tests. Vitest passed 100 tests across 10 files. The production
   build passed. Playwright Chromium passed 31 tests with no skips. `git diff --check` passed.
Manual or artifact evidence: The cohesive browser flow verifies that membership saves contain
   explicit immutable source-finding snapshots, creates isolated reviews, transfers the active
   marker across exports, imports/edits/re-exports an EDL, and proves invalid-import rollback plus
   dirty route, hash, version, and import guards. A second browser flow verifies Radarr movie,
   Sonarr episode, and Other Files create/import target payloads, dirty file switching, and
   imported-review opening. Repository coverage proves that later reviews snapshot the same
   Manual source finding with distinct review-local IDs and that editing one snapshot changes
   neither the source finding nor another review. The restart test
   creates import/review/export state in a temporary SQLite file, disposes the engine, reopens
   the same file, and verifies names, memberships, filter selection, import provenance, and
   active export state. No user instance was started and no user/development database was wiped.
Known failures or risks: None within NSR-12. Browser tests use route-level fake APIs while backend
   filesystem, transaction, source-adapter, and restart behavior is covered by real repository/API
   integration tests.
Acceptance criteria still unmet: None.
Next exact action: Independent release/review handoff using this audit and the aggregate diff.
Next eligible task: Release/review handoff.
Decisions or deviations from the parent plan: Browser acceptance is split into one cohesive
   Jellyfin multi-version lifecycle and one shared-authority source-parity flow; backend tests
   provide exact EDL/deletion/security/atomicity assertions that are not duplicated in browser UI.
User approval required: None.
