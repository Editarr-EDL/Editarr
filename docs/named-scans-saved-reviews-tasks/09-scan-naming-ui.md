# NSR-09 — Add scan naming UI

Status: **DONE**

Dependencies: NSR-08. Deliverable: optional name entry, consistent named display, and
rename/clear UX for generated, Manual, and imported scans across the web application.

Read the task-pack README, parent plan, NSR-00–08, and prerequisite handoffs first.

## Why this task exists

Backend naming is already available, but names must be usable and recognizable everywhere
without displacing stable scan numbers or obscuring scan type/origin.

## Scope

1. Add optional Scan name input to every scan-creation form:
   - Jellyfin media detail;
   - Radarr movie and Sonarr episode detail through the shared path;
   - Other Files/manual path scan.
   Enforce the shared 100-code-point/control/trim contract client-side for immediate
   feedback while preserving server validation as authoritative.
2. Include the name in every enqueue request and clear form state predictably after
   success/cancel. Existing unnamed creation remains unchanged.
3. Build one shared scan label formatter/component that always retains scan number and
   displays origin/type. Use it in:
   - global Scans queue/history panel and tables;
   - item/version scan selector;
   - Other Files list/detail;
   - Find Filters candidates and scan attribution in review;
   - scan/EDL deletion confirmations and EDL scan provenance.
4. Add accessible rename action/dialog to scan rows/details. It loads current name, permits
   duplicate names and clearing, reports server errors without losing input, disables
   duplicate submission, and returns focus to its trigger.
5. Manual and imported scans use the same rename API. Do not change origin, source/version
   display, timestamps, status, or options while renaming.
6. Update accessible labels so duplicate display names remain unambiguous through scan
   number and context; color is not the only type/origin indicator.
7. Add/adjust `App.test.tsx` coverage for all creation paths, validation, display surfaces,
   rename/clear, duplicate names, focus, error recovery, and no queue identity changes.

## Out of scope

- Saved-review list/editor behavior (NSR-10).
- Import dialog (NSR-11), though imported fixtures must render correctly.
- Renaming source media/version names.

## Verification

- Focused Vitest for scan creation/list/rename/accessibility cases.
- Full `pnpm test` and `pnpm build` in `web/`.
- `git diff --check`.

## Acceptance criteria

- [x] Every New Scan flow accepts an optional name and submits it correctly.
- [x] One consistent label keeps name, scan number, type/origin, and necessary context.
- [x] Every scan origin can be renamed or cleared and duplicate names work.
- [x] Rename focus/error/loading states are accessible and robust.
- [x] Existing unnamed scans remain clear and backward-compatible in the UI.
- [x] Frontend tests/build pass and shared definition of done is satisfied.

## Verifier instructions

Search all user-visible `Scan #` formatting and confirm each intended surface uses the
shared formatter or deliberately equivalent accessible copy. Exercise unnamed, duplicate,
Manual, imported, queued, and completed cases. Verify rename does not cause queue reorder,
review dirtiness, or origin/timestamp changes.

## Handoff requirements

List components/helpers and every migrated surface, request fields, accessibility/focus
behavior, tests/counts/build, and remaining review-specific legacy UI owned by NSR-10.
Name NSR-10 as next eligible.

## Handoff

Status: DONE
Last completed substep: Added optional naming to catalog and Other Files enqueue forms, shared
   named scan labels for selection surfaces, and an accessible reusable rename action.
Files changed: web/src/api.ts, web/src/App.tsx, web/src/api.test.ts, web/src/App.test.tsx.
Schema/API/contract changes: Added `ScanOptions.name` and `renameScan`; visible labels retain
   legacy unnamed/manual wording while named scans include name, number, and origin. Catalog
   and Other Files forms submit trimmed optional names with the server's 100-code-point limit.
   Rename supports clear/duplicate names, loading/error recovery, and trigger focus restoration.
Verification commands and results: `pnpm test` passed 132 tests; `pnpm build` passed;
   `git diff --check` passed.
Manual or artifact evidence: Other Files name input submits through `enqueueManualScan` and
   clears after success. Scan selection rows use the shared formatter and rename control.
Known failures or risks: At this task boundary App.tsx retained transitional scan-level review
   helpers. NSR-10/11 migrated editing/export to persisted reviews and NSR-12 removed those
   helpers.
Acceptance criteria still unmet: None within NSR-09.
Next exact action: Implement NSR-10 saved-review UI.
Next eligible task: NSR-10 is now eligible.
Decisions or deviations from the parent plan: None.
User approval required: None.
