# CSS-47 — Extract the existing local Find Filters workflow

Status: **DONE** (October 4, 2026), local/mock verification only.

- Prerequisites: [CSS-00](00-repository-preflight.md)
- Decision gates: None beyond the shared rules.
- Parent sections: main §3/7; implementation §5.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Find Filters currently finds and associates local scans inside the catalog view. Refactor that working behavior before adding remote results.

## Starting points

web/src/catalog/media-detail.tsx; new web/src/scans/ find-filters feature; existing API helpers.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Extract dialog/controller/selection behavior into focused reusable components/hooks.
2. Keep local discovery and associations identical, including current saved-review integration.
3. Define a source-neutral result boundary usable by catalog and Other Files without implementing remote matching yet.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Existing local tests and user flow pass unchanged.
- [x] Dialog focus/cancel/selection behavior remains accessible on narrow/wide layouts.
- [x] Large catalog/API files become thinner; no duplicated local control flow is introduced.

## Verification

- Run App.catalog and affected scan/review component tests plus local Find Filters browser journey.
- Verify one reusable entry works for every currently supported local target.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide component/controller interfaces and preserved local behavior tests to CSS-48/49.

## Handoff summary for the next task

Status / date / implementer: DONE, October 4, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Extracted local discovery,
selection and association into one controller and a presentation-only dialog.
Jellyfin, Radarr and Sonarr still use the selected review version's path and
metadata, the same local API, preselected new local matches, checked/disabled
existing associations, explicit association and scan-history reload. Found scans
remain available in existing saved-review workflows; no review or sidecar is
automatically changed. Removed an unused matches overlay that was only ever set
to an empty array. The catalog now delegates instead of owning the workflow;
existing focused API helpers needed no changes.

Files changed and relevant revision: Base revision
`c7ff0257b159d2c7e895770633d31a1d21de571f`, initially clean worktree.
New [controller](../../web/src/scans/use-find-filters.ts),
[dialog](../../web/src/scans/find-filters-dialog.tsx),
[controller tests](../../web/src/scans/use-find-filters.test.tsx),
[catalog integration tests](../../web/src/test/App.find-filters.test.tsx) and
[browser tests](../../web/e2e/find-filters.spec.ts).
Updated [catalog detail](../../web/src/catalog/media-detail.tsx),
[shared modal](../../web/src/ui/ModalDialog.tsx),
[styles](../../web/src/styles/shell.css), this brief, task index, shared context
and parent plan/implementation module references. No commit made.

Contracts / APIs / schemas / migrations / fixtures introduced: Internal frontend
interfaces only; no HTTP, schema, persistence, signing or protocol change.

- `useFindFilters({ target, onMessage, onAssociated })`: `LocalFilterTarget`
  derives from the existing `findFilterCandidates` argument. `onAssociated`
  reloads local history. Returns `finding`, `open`, `candidates`, `selectedIds`,
  `associating`, `find`, `associate`, `close`, and `select(id, checked)`.
- `FilterCandidate`: source-neutral display boundary with `id`, `label`, `origin`,
  `options`, `matchDescription`, `alreadyAssociated`. The controller projects
  existing local `ScanMatch` results; the dialog has no API or catalog dependency.
- `FindFiltersDialog`: candidates/selection/busy state plus `onSelect`,
  `onAssociate`, `onClose`, optional `returnFocusRef`. The caller owns actions and
  selection defaults; do not infer community auto-selection from local defaults.
- `ModalDialog.returnFocusRef` optionally names the original opener when async
  loading/disabled controls have cleared focus. Other callers keep the previous
  captured-focus behavior. Native labels now sit inside valid list-item wrappers;
  scoped candidate grid/wrapping keeps long names inside narrow dialogs.

Decisions used (gate IDs and recorded answers): No new gate or product choice.
Preserved current local matching/association semantics. Other Files has no current
Find Filters entry; the reusable display/controller boundary is available for
downstream adaptation, not a newly added manual target or community lookup.

Verification commands and results:

- `pnpm --dir web test -- src/test/App.catalog.test.tsx`: passed all 194 existing
  tests in 28 files; this script's `--` did not filter Vitest, so it ran the suite.
- `pnpm --dir web exec vitest run src/scans/use-find-filters.test.tsx
  src/test/App.find-filters.test.tsx`: 11 passed. Covers idle/loading, all local
  reason labels, preselection, missing paths, item metadata fallback, selected
  version payloads for all three sources, unknown/existing ID guards, cancel,
  association locks, failures/retry and saved-review evidence reuse.
- `pnpm --dir web exec playwright test e2e/find-filters.spec.ts`: 4 Chromium
  tests passed at 320x900 and 1440x900. Checks focus entry/trap/return,
  Escape/Cancel, locked in-flight association, exact selected IDs, history/review
  reuse, empty/failing/retry states, zero community requests, zero axe violations,
  no page overflow and long-name content fit. Found/empty screenshots generated
  under `web/test-results/css47-*.png`; both found screenshots inspected.
- `pnpm --dir web test`: final 205 tests passed in 30 files, including all catalog,
  scan and review tests. `pnpm --dir web build`: TypeScript and Vite passed.
- Editor diagnostics: no errors in all eight touched frontend/test/style files.
- Node `fs`/`path` link and fence validation: 135 local links in the five edited
  documents passed; `git diff --check` passed. Final module sizes: catalog detail
  328 lines, controller 92 lines, dialog 48 lines.

Known failures, limitations, unavailable checks, and recovery notes: Initial
test failures were repaired and rerun: review fixture incorrectly expected scan
selection in the name-only dialog, browser selector omitted the existing label
suffix, async opener focus was lost, and labels carried invalid list-item roles.
No remaining test failures. Vite reports the 500 kB chunk-size warning
(500.18 kB main JS); bundle splitting is outside this extraction. Tests use mock
local APIs/synthetic metadata, not real providers or personal media. No backend,
Worker, database, migration, secrets, models, live service or deployment touched.
Native/release and other browser engines unverified. Remote cancellation/cache
and changed-target race protection remain CSS-48, not claims of this local
behavior-preserving extraction. No recovery required.

Remaining work in this task: None.

Next task ID and exact first action: CSS-48. Read
[48-local-lookup-adapter.md](48-local-lookup-adapter.md), this interface handoff,
CSS-29/34/35/35A/35B/36/41 and approved matching/privacy decisions. Start at
`useFindFilters` and the selected-version target in `MediaDetail`, then inspect
the existing signed local client and backend identity/fingerprint adapters.
Keep local results usable independently while adding explicitly cancellable,
target-bound remote status/results; never send local paths/alternate-track
inventory or reuse local preselection as community selection policy. CSS-49 owns
the richer result presentation; CSS-50 owns downloads/review reuse.

Direct dependent tasks: [CSS-48](48-local-lookup-adapter.md).

