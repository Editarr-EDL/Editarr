# NSR-08 — Add frontend contracts and draft state

Status: **DONE**

Dependencies: NSR-06 and NSR-07. Deliverable: complete TypeScript API client plus tested,
UI-independent saved-review draft state and transient snapshot restoration.

Read the task-pack README, parent plan, NSR-00–07, and prerequisite handoffs first.

## Why this task exists

The current `App.tsx` stores scan IDs and finding IDs across many hooks. Introducing review
UI directly there would mix transport, identity, dirty detection, and remove/re-add rules.
This task first creates typed API helpers and a pure/testable draft model.

## Scope

1. Update `web/src/api.ts` to mirror the final backend contract exactly:
   - scan `name`/origin/import metadata and named EDL scan references;
   - review summary/detail, membership, filter snapshot, revision/content revision, and
     active/outdated state;
   - source-aware list/create and review get/save/rename/duplicate/delete;
   - review-filter audio/video preview;
   - review-ID export;
   - multipart upload and JSON path import plus aggregate warning/error responses;
   - scan rename.
2. Add/expand `web/src/api.test.ts` to assert methods, prefix-safe URLs, encoding, query
   sorting, JSON/multipart bodies, omission versus null, CSRF path through `apiRequest`,
   response errors, and no manual `Content-Type` for `FormData`.
3. Extract review draft logic from `App.tsx` into a focused module (name frozen in NSR-00,
   e.g. `review-draft.ts`) with immutable operations for:
   - create from saved detail or empty target;
   - add/remove scan membership by audio/video role;
   - snapshot source filters into review-local stable IDs;
   - toggle/edit/delete filters and stage new Manual filters;
   - dirty comparison against last saved state;
   - discard/replace after save;
   - transient removed-snapshot cache: remove then re-add before save restores; successful
     save of the removal or ending/switching editing session forgets; later add is fresh;
   - duplicate draft creation without inheriting export marker.
4. Ensure filter identity is review-local. Do not use a source finding ID alone as a React
   key or draft identity.
5. Add pure unit tests for two-review isolation, combined roles, overlapping source IDs,
   dirty/no-op behavior, remove/re-add before and after save, Manual staging/discard, and
   revision replacement after server save.
6. Keep old frontend helpers working only where required until NSR-10/11 migrate UI. Do not
   add a second network authority or silently fall back to scan-level selection saves.

## Out of scope

- Visible scan naming controls (NSR-09).
- Saved-review list/editor UI (NSR-10).
- Import dialogs and Other Files integration (NSR-11).
- Removal of all legacy helpers (NSR-12).

## Required invariants

- API URLs use runtime Base URL helpers and safely encode IDs/queries.
- Multipart auth/CSRF behaves like other unsafe browser requests.
- Dirty state includes name, membership, selection, filter edits/deletes, and staged Manual
  filters; local sort preference does not dirty a review.
- An EDL marker is server state, not draft-owned or forged by the client.
- Successful server save replaces revisions/baseline; failed/stale save preserves the
  user's draft for recovery.

## Verification

- Focused Vitest for `api.test.ts` and new draft-state tests.
- Full `pnpm test` and `pnpm build` in `web/` because production TypeScript changes.
- `git diff --check`.

## Acceptance criteria

- [x] Every final backend route/DTO has a typed, tested frontend helper.
- [x] Review-local identity and two-review isolation are explicit.
- [x] Dirty, discard, save replacement, and transient remove/re-add semantics pass tests.
- [x] Manual filters remain staged and disappear on discard.
- [x] Upload/path import clients produce identical logical request options.
- [x] Existing UI still compiles before its migration.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Compare TypeScript fields against live OpenAPI and backend handoffs. Rerun the API request
spies and pure draft tests, especially remove/save/re-add and stale-save preservation.
Search for source finding IDs used as sole review-filter identity. Confirm `FormData` does
not set its own content-type header.

## Handoff requirements

Record all exported types/functions, draft reducer/helper API and invariants, transitional
legacy helpers, test counts/build result, and any App.tsx compile adjustments. Name NSR-09
as next eligible.

## Handoff

Status: DONE
Last completed substep: Added the typed review/EDL API client and UI-independent immutable
   review draft state module.
Files changed: web/src/api.ts, web/src/api.test.ts, web/src/review-draft.ts,
   web/src/review-draft.test.ts.
Schema/API/contract changes: Added typed scan provenance, saved-review summaries/details,
   memberships, review-local filters, CRUD/preview/export helpers, source-aware list/create,
   upload/path import, and scan metadata. `review-draft.ts` provides create, membership,
   snapshot, edit/delete, Manual staging, dirty, discard, save replacement, duplicate, and
   transient remove/re-add operations without using source finding IDs as identity.
Verification commands and results: In `web/`, `pnpm test` passed 131 tests and `pnpm build`
   passed. `git diff --check` passed. FormData helpers omit manual Content-Type and flow through
   the existing CSRF-aware `apiRequest` boundary.
Manual or artifact evidence: API tests cover encoded source URLs, sorting queries, CSRF, and
   browser-managed multipart headers. Draft tests cover two-review isolation, combined state
   operations, fresh duplicate IDs, immutable edits, and baseline replacement.
Known failures or risks: At this task boundary App.tsx retained transitional scan-level helpers
   and optional provenance fields. NSR-10/11 migrated those consumers and NSR-12 removed the
   obsolete helpers; no scan-owned writable review authority remains.
Acceptance criteria still unmet: None within the data-layer scope.
Next exact action: Implement NSR-09 scan naming UI.
Next eligible task: NSR-09 is now eligible; NSR-10 and NSR-11 remain blocked on later UI tasks.
Decisions or deviations from the parent plan: None.
User approval required: None.
