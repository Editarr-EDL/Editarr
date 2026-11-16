# NSR-10 — Build saved-review UI

Status: **DONE**

Dependencies: NSR-08 and NSR-09. Deliverable: complete named saved-review workflow for the
shared Jellyfin/Radarr/Sonarr media detail experience, including review export state.

Read the task-pack README, parent plan, NSR-00–09, and prerequisite handoffs first.

## Why this task exists

This task replaces the current ephemeral scan combination on catalog-backed detail pages
with the saved-review aggregate and draft state built in NSR-08. Other Files parity and
import dialogs follow in NSR-11.

## Scope

1. Add a saved-review section/switcher to shared media detail, scoped to the selected
   physical version. It must:
   - list name and modified date with duplicate-name-safe identity;
   - offer sort by name/date modified and direction;
   - mark the active EDL review and `Outdated` explicitly, not by color alone;
   - load the most recently saved review on initial version open;
   - show an empty unsaved review when none exists.
2. Wire the NSR-08 draft model into scan membership, filter selection, linked results,
   timeline, defaults, edits/deletes, and Add Filter. All edits are review-local.
3. Implement explicit save:
   - first save asks for a required valid non-unique review name;
   - subsequent Save Changes sends expected revision and the full draft;
   - successful save replaces baseline/revisions and clears dirty state;
   - stale/error retains draft and offers reload/recovery without overwrite;
   - staged Manual filters are created only by successful save.
4. Implement rename, duplicate with requested name, and delete confirmations. Deleting the
   active EDL review explains that the sidecar remains and review attribution will clear;
   it does not offer/perform scan deletion.
5. Preserve/extend dirty guards for switching review, version, scan membership, file,
   route, or leaving detail. Cancel keeps the current draft. Confirmed discard clears the
   transient removed-snapshot cache as appropriate.
6. Implement remove/re-add behavior using NSR-08: restore the session snapshot before
   save; after a successful saved removal, a later add snapshots current source findings.
7. Export through the saved-review endpoint only. Dirty drafts cannot export. On success
   refresh active review/scan provenance and clear `Outdated`; another review's marker
   transfers correctly.
8. Update all review finding actions/previews to use review-local fields/IDs. Call the
   review-filter preview routes so rendering uses the review snapshot's edited range while
   media resolution still comes from verified source scan provenance. Do not call old
   source-finding preview/edit endpoints.
9. Cover Jellyfin single/multi-version, Radarr movie, and Sonarr episode paths using fake
   source responses. Shared components must not rely on one source's route shape.

## Out of scope

- Import controls and upload/path dialogs (NSR-11).
- Other Files saved-review integration (NSR-11).
- Final removal of every legacy API/helper (NSR-12), though this UI must stop calling them.

## Verification

- Focused `App.test.tsx` coverage for empty/most-recent load, sort, duplicates, save,
  rename, duplicate, delete, isolated reviews, Manual staging, remove/re-add, stale/error,
  dirty guards, export/outdated/marker transfer, preview attribution, and all catalog
  sources/versions.
- Automated accessibility assertions already used by the project for dialogs/primary flows.
- Full `pnpm test` and `pnpm build`.
- `git diff --check`.

## Acceptance criteria

- [x] Catalog-backed versions open/manage the correct saved review and empty state.
- [x] Review snapshots isolate selection/content/timing across reviews.
- [x] Explicit save, Manual staging, and discard baseline behavior are wired.
- [x] Rename/duplicate/delete accept duplicate names and preserve scans.
- [x] Remove/re-add behavior changes precisely at successful save.
- [x] Review export/active/Outdated states use server authority and saved content only.
- [x] Jellyfin/Radarr/Sonarr and accessibility tests/build pass for the current panel.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Use two same-named reviews sharing one scan and edit identical source findings differently.
Exercise remove/re-add on both sides of a save, stage then discard a Manual filter, inject a
stale save, and delete the exported review. Inspect network calls to ensure no legacy
scan-selection/edit/export endpoint is used. Rerun focused tests before NSR-11.

## Handoff requirements

Record component/state integration, source routes covered, legacy calls eliminated from
catalog detail, tests/counts/build/accessibility evidence, and exact gaps left for Other
Files/import. Name NSR-11 as next eligible.

## Handoff

Status: DONE
Last completed substep: Independent review restored the result-level saved-review experience
   omitted by the initial implementation: linked result grouping, selected/total and skipped-time
   summaries, a normalized timeline, category/root-word groups, alphabetical/chronological sort,
   scan attribution, tri-state selection, and explicit application of filter defaults.
Files changed: `web/src/App.tsx`, `web/src/App.test.tsx`, and `web/src/styles.css`.
Schema/API/contract changes: Consumes NSR-08 typed review CRUD/export helpers and draft state;
   active review/outdated display comes from server summary state. Result presentation and filter
   defaults operate only on the local review draft; active-review deletion explains that the
   sidecar remains and attribution clears. Dirty drafts cannot call review-preview endpoints.
Verification commands and results: `pnpm test` passed 89 tests across 10 files; `pnpm build`
   passed; Playwright Chromium passed 31 tests.
Manual or artifact evidence: The panel is scoped by catalog source/item/version, uses no legacy
   scan-selection/edit/export endpoint, and production-component coverage exercises linked
   selection, grouping, summary/timeline calculations, sorting, defaults, and dirty preview state.
Known failures or risks: Other Files/import UI remained NSR-11 scope at this task boundary.
   NSR-11 completed that workflow and NSR-12 removed the transitional scan-review controls.
Acceptance criteria still unmet: None within the catalog saved-review scope.
Next exact action: Implement NSR-11 import controls and Other Files saved-review parity.
Next eligible task: NSR-11 is now eligible.
Decisions or deviations from the parent plan: None.
User approval required: None.
