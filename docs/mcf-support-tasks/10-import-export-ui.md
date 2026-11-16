# MCF-10 - Build import, export, and badge UI

Status: **DONE**

Dependencies: MCF-09. Deliverable: source-complete Import dialog, Export menu/preflight,
independent EDL/MCF badges/provenance, and format-specific management/deletion.

## Scope

1. Replace `Import EDL` with `Import`. The existing modal gains format (`EDL`, `MCF`) and
   transport (`Upload`, `Scanner path`) segmented controls, required review name, format-
   specific extension/MIME hints, labels, validation, busy/error states, and focus restore.
2. Wire MCF import for Jellyfin, Radarr, Sonarr, and Other Files. On success open the
   returned review, refresh scans/history/status, identify source version, and show safe
   metadata comparison notices. Preserve dirty-draft blocking and EDL behavior.
3. Replace `Export EDL` with an `Export` button/menu listing EDL and MCF. Use semantic menu
   keyboard behavior, Escape/outside close, focus restore, current/outdated labels, and
   disabled explanations. Choosing a format triggers only that flow.
4. Run MCF preflight against the saved review. Render all metadata/filter issues in a
   concise actionable panel and focus/open the corresponding metadata or stable filter
   editor. Never auto-apply defaults, omit filters, or export a dirty draft.
5. Update only the selected format's state after success. Refresh the authoritative review
   and media/version status without clearing the other format's badge.
6. Replace every EDL-only badge surface with independent concise EDL/MCF Current/Outdated
   badges: library tables/posters, movie/series/season/episode rows, media versions/detail,
   Other Files, Saved Reviews, and provenance. Dense surfaces use accessible tooltips/text.
7. Replace the single Delete EDL action with `Manage exports` whenever either sidecar is
   active. List each format, timestamp, review/current state, scan provenance, and an
   independent confirmed Delete action. Success/failure changes only that format.
8. Update scan deletion confirmation/conflict UI to list all saved-review, EDL, and MCF
   references with stable disambiguation.
9. Add API/UI tests and end-to-end flows for both import transports, both formats, success,
   invalid/oversized/version errors, metadata notices, preflight issue navigation,
   keyboard menu/dialog behavior, coexistence, independent outdated updates, review
   deletion attribution clearing, independent deletion, all source types, and responsive
   badge layout.

## UI constraints

- The Export format chooser is a popover/menu, not a modal.
- Import remains a modal because it collects name, format, transport, and source input.
- Badges always name the format and state; color is supplementary.
- Do not expose scanner-host paths after import/export/delete.
- Catalog and Other Files use the same components and semantics.

## Verification

- Focused Vitest for API helpers and all affected App surfaces.
- `pnpm build` in `web/`.
- Playwright desktop/320px catalog and Other Files cross-format journeys.
- Screenshot/canvas/layout checks for badge/menu/dialog visibility and no overlap.
- Axe and manual keyboard checks.
- `git diff --check`.

## Acceptance criteria

- [x] Import offers both formats/transports and opens one atomic imported review/scan.
- [x] Export menu triggers only EDL or MCF and MCF preflight is actionable.
- [x] Every required surface displays independent accurate badges/provenance.
- [x] Manage exports deletes one format without affecting the other.
- [x] Cross-format and all-source browser flows pass at desktop and 320px.
- [x] Focus, announcements, safe errors, and non-color status meet accessibility rules.

## Handoff

Status: DONE
Last completed substep: Completed desktop and 320px Chromium validation, fixed mobile
review-action clipping and keyboard access to result scrollers, and updated the full E2E
suite to the generic import/export UI and canonical MCF fixtures.
Files changed: `scanner/api.py`, `scanner/models.py`, `tests/test_editarr_integration.py`,
`web/src/api.ts`, `web/src/App.tsx`, `web/src/App.test.tsx`, `web/src/styles.css`,
`web/e2e/app.spec.ts`, `web/e2e/mcf10.spec.ts`, and this handoff/README.
Schema/API/contract changes: Arr catalog `ArrItem` responses now include the existing
format-keyed `ExportStatus`, populated with one batched repository lookup. No schema or
database change.
Verification commands and results: `uv run ruff check scanner/api.py scanner/models.py
tests/test_editarr_integration.py` passed; `uv run pytest -q` passed 1,058 tests; `pnpm
build` passed; `pnpm test` passed 111 tests in 12 files; `pnpm exec playwright test
--project=chromium --reporter=line` passed 35 tests; `git diff --check` passed.
Manual or artifact evidence: Focused Playwright ran catalog and Other Files at 1440x1000
and 320x900 with zero WCAG 2 A/AA/2.1 AA axe violations and no root horizontal overflow.
Screenshots are `web/test-results/mcf10-{catalog,manual}-{desktop,mobile}.png`; visual
inspection confirmed visible wrapped controls, badges, and no incoherent overlap.
Known failures or risks: None recorded. The VS Code shared-page CDP bridge timed out, so
browser validation used the repository's installed Playwright Chromium directly against
the same local frontend server.
Acceptance criteria still unmet: None.
Next exact action: Begin MCF-11 final integration.
Next eligible task: MCF-11.
Decisions or deviations from the parent plan: Arr catalog rows required adding the
already-established `ExportStatus` response field so Radarr/Sonarr poster and list badges
have authoritative state.
User approval required: None.
