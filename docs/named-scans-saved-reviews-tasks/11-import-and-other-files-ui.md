# NSR-11 — Add import UI and Other Files parity

Status: **DONE**

Dependencies: NSR-10. Deliverable: EDL upload/path dialogs for every source and full saved-
review/import/export parity in Other Files.

Read the task-pack README, parent plan, NSR-00–10, and prerequisite handoffs first.

## Why this task exists

Catalog review UI is stable after NSR-10. This task reuses it for Other Files and adds the
two import transports without duplicating parsing or review state.

## Scope

1. Add `Import EDL` to Jellyfin, Radarr, Sonarr, and Other Files detail/review actions.
   The target item and physical version must be selected before the dialog opens/submits.
2. Build one accessible import dialog with:
   - required review name using the shared validation;
   - mutually exclusive Upload and Scanner path modes;
   - `.edl` file picker in Upload mode and absolute-path field/help in path mode;
   - selected target/version and expected created scan name shown clearly;
   - cancel, loading, focus trap/restore, safe validation/server errors, and no duplicate
     submission.
3. On success, add/open the returned saved review, refresh scans/reviews, display imported
   scan name/number/origin, safe EDL basename as version/source name, and import timestamp
   as a timestamp. Never show the full scanner path returned from path mode; the server
   should not return it either.
4. If unsupported actions were ignored, show exactly one warning that some unsupported
   actions were not imported. Do not show one warning per row. A no-supported-actions error
   keeps the dialog/input and creates/opens nothing.
5. Render imported commented filters and generic `Imported mute`/`Imported skip` fallbacks.
   The review can edit/delete/select them and export a commented EDL through NSR-10's path.
6. Replace the Other Files `ManualScanWorkspace` ephemeral review flow with the same saved-
   review selector/draft/actions/export marker/outdated/delete behavior used for catalog
   sources. Preserve file selection and EDL delete semantics.
7. Ensure source/path mode switching and switching media/version follows dirty guards.
   Import is a new saved review and must not overwrite the currently open dirty review;
   require discard/cancel through the established guard before opening the result.
8. Add test fixtures for upload `FormData`, path JSON, mixed/mute/skip imports, warning,
   no-supported error, server validation, source basename/timestamp, imported rename,
   round-trip comments, and Other Files parity.

## Out of scope

- Supporting more EDL syntax/actions.
- Client-side parsing or reading scanner-host files.
- Treating import as an active export marker.
- Final backend/legacy cleanup and broad release verification (NSR-12).

## Verification

- Focused Vitest for import dialog, all source entry points, warning/error, and Other Files
  saved-review lifecycle.
- Accessibility checks for dialog labels, keyboard/focus, warning/status announcements,
  duplicate names, and non-color marker states.
- Full `pnpm test` and `pnpm build`.
- `git diff --check`.

## Acceptance criteria

- [x] Both import modes work from all four source workflows through one shared dialog.
- [x] Import requires review name/target and opens exactly the created saved review.
- [x] Aggregate warning and no-supported-actions error have correct, accessible behavior.
- [x] Basename/import time are displayed safely and not confused with duration.
- [x] Imported filters can be edited and re-exported with comments.
- [x] Other Files has saved-review, Outdated, export/delete, and dirty-guard parity.
- [x] No client-side parser or full host-path leak exists.
- [x] Tests/build/accessibility and shared definition of done pass.

## Verifier instructions

Inspect network requests for all sources and both modes. Use a fake filename/path with
separators to verify safe display. Exercise a dirty current review before import, warning
with many unsupported rows, unsupported-only error, and Other Files restart/reload state.
Confirm imported scan timestamp never appears in duration UI. Rerun tests before NSR-12.

## Handoff requirements

List UI entry points/components, source coverage, warning/error/focus behavior, path-safety
evidence, Other Files legacy calls removed, tests/counts/build, and any remaining obsolete
surface for NSR-12. Name NSR-12 as next eligible.

## Handoff

Status: DONE
Last completed substep: Independent review completed the shared upload/scanner-path dialog and
   Other Files parity by using the common focus-trapped modal, surfacing the aggregate warning,
   guarding stale review loads, and displaying the sanitized import basename and authoritative
   import timestamp separately from duration.
Files changed: `web/src/App.tsx`, `web/src/App.test.tsx`, `web/e2e/app.spec.ts`, and
   `web/src/styles.css`.
Schema/API/contract changes: Catalog detail and Other Files now expose the same Import EDL
   dialog, using `importEdlUpload` for FormData and `importEdlFromPath` for JSON path imports.
   Imported results refresh the owning workflow and expose the returned named scan/review through
   the existing saved-review panel; no client-side parsing or host-path response is introduced.
Verification commands and results: In `web/`, `pnpm test` passed 89 tests across 10 files,
   `pnpm build` passed, and Playwright Chromium passed 31 tests with no browser errors.
   Existing test coverage exercises the shared API/FormData boundary, import race handling,
   warning status, focus trapping, and source-parity workflows.
Manual or artifact evidence: Import targets carry selected catalog version IDs or the selected
   manual item/path identity. The shared dialog keeps input on validation/server failure, blocks
   duplicate submission, and restores trigger focus on cancel/success.
Known failures or risks: Final legacy scan-workspace cleanup and broad release verification were
   NSR-12 scope at this task boundary and were subsequently completed. The original handoff
   overstated import presentation coverage; independent review closed that gap.
Acceptance criteria still unmet: None within NSR-11.
Next exact action: Implement NSR-12 final integration cleanup and full verification.
Next eligible task: NSR-12 is now eligible.
Decisions or deviations from the parent plan: None.
User approval required: None.
