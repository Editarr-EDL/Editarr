# CSS-49 — Display scan candidates, provenance and compatibility

Status: **DONE locally, October 4, 2026**. Frontend and mocked Chromium verification
passed. Live community activation and artifact downloads are not part of this task.

- Prerequisites: [CSS-35C](35c-audio-track-selection-ui.md), [CSS-48](48-local-lookup-adapter.md)
- Decision gates: None beyond the shared rules.
- Parent sections: main §7; D05/D12.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Users need enough detail to judge scans without confusing compatibility with popularity. This task renders results; download orchestration follows separately.

## Starting points

web/src/scans/find-filters/; shared result primitives; styles and feature tests.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Build desktop rows/mobile cards with name/type/uploader/date, versions/models/settings, coverage and audio/video/category counts.
2. Separate uncertain results unselected; show match reasons and expandable provenance/lineage.
3. Add type/language/coverage/band filters and approved sorting controls within compatibility bands; render unavailable reputation honestly until implemented.
4. Display independent audio/video bands and timing conflicts, with contributed analyzed-track language and description/title compared to the selected local target. Reuse CSS-35C's track metadata primitives. A strong video match cannot imply strong audio or vice versa; description differences alone are not a conflict. Unknown-track evidence stays explicitly unknown/review-required. An explicit strong-match action must not silently include an incompatible/unknown channel of a mixed-channel contribution.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Uncertain results cannot become selected through a generic default selection.
- [x] Strong-match selection is explicit, empty/failed/stale states differ, and no fabricated vote/model data appears.
- [x] Keyboard selection, focus, screen-reader announcements and narrow layouts work.
- [x] Opposite-channel compatibility and unknown analyzed-track metadata remain visible; no generic whole-contribution label masks audio/video conflicts.

## Verification

- Component fixtures for legacy/partial/derived/zero findings and long untrusted text.
- Browser accessibility/responsive tests for filters, sorts and uncertainty segregation.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide result list/detail components, selection semantics and rank extension seam to CSS-50/54.

## Handoff summary for the next task

Status / date / implementer: DONE locally / 2026-10-04 / GitHub Copilot.

Implemented outcome and last completed substep: Shared Find Filters dialog now
renders local evidence first and a separate community workflow for the exact
selected Jellyfin/Radarr/Sonarr version. The reused CSS-35C selector loads only
while Find Filters is active; public lookup is explicit, after the user can see
or change the selected track. Local discovery/association remains independently
usable and never accepts community IDs. Desktop rows become bounded mobile cards;
long names have compact previews and complete expandable details. Final responsive
refinement prevents native filter text clipping at 320px.

Files changed and relevant revision: Initially clean base
`311b55ea155e120c3376179bd8147205ae0d6a32`; no commit made.
New [result state/helpers](../../web/src/scans/community-result-state.ts),
[state tests](../../web/src/scans/community-result-state.test.tsx),
[community result section](../../web/src/scans/community-filter-results.tsx),
[component tests](../../web/src/scans/community-filter-results.test.tsx),
[result row/details](../../web/src/scans/community-filter-row.tsx),
[shared synthetic fixture](../../web/src/test/community-lookup-fixtures.ts) and
[community browser journeys](../../web/e2e/community-results.spec.ts).
Updated [dialog](../../web/src/scans/find-filters-dialog.tsx),
[controller](../../web/src/scans/use-find-filters.ts),
[catalog integration](../../web/src/catalog/media-detail.tsx),
[scoped styles](../../web/src/styles/shell.css),
[existing local browser tests](../../web/e2e/find-filters.spec.ts), this brief,
task index, shared context, parent plan/implementation and current API documentation.
No backend files changed, including intervening CSS-48 edits.

Contracts / APIs / schemas / migrations / fixtures introduced:

- `FindFiltersDialog` accepts optional raw `results: FilterResult[]` and
  `community: CommunityResultsProps`; existing local presentation/actions remain
  compatible. Local expandable provenance uses actual stored stage/model/settings
  and analyzed-track snapshots, never a newly selected/default track.
- `CommunityFilterResults({lookup,tracks?,onSelectionChange?})` uses the CSS-48
  hook DTO and CSS-35C controlled `AudioTrackField` unchanged. Displays name/type/
  uploader/date, versions, coverage, counts, analyzed-track identity and separate
  video/audio bands, conflicts, missing evidence and exact rational timing.
  Expandable rows retain artifact revision/digest/schema, source revision/states
  and category counts. Missing catalog model/settings/full parent lineage are
  labeled not supplied, not inferred or fetched from artifacts prematurely.
- `RemoteSelection = {revisionId,artifactDigest,targetKey,channels,reviewRequired}`.
  `channels` and `reviewRequired` contain explicit audio/video choices. Optional
  `onSelectionChange` emits current validated-target choices and an empty array
  when unavailable; it performs no download/import/association. CSS-50 must
  consume this callback and revalidate target/artifact evidence before import.
- `useCommunitySelection` starts empty. Individual channel checks are explicit;
  unknown/incompatible channels carry `reviewRequired`. Bulk Select strong
  channels replaces selection with only eligible channels from currently visible
  candidates. A mixed strong-video/unknown-audio result stays in Review Required;
  its audio cannot be selected by the bulk action. Target key, stale/loading/error/
  cancelled state and changed artifact bytes invalidate choices. Filtering does
  not hide that previous explicit selections may include hidden results.
- `filterResults` is the ranking extension boundary for CSS-54. Video band then
  audio band always compare first. Catalog order is the default; newest, uploader
  and actual import-count sorts operate only inside equal band pairs. No vote,
  reputation or confidence formula is invented. Rating/most-downloaded controls
  are explicitly unavailable, because those workflows are downstream; imports
  are not relabeled downloads. CSS-54 can extend the secondary comparator, not
  override compatibility or channel safety.
- Type, language, channel, category, coverage and independent audio/video band
  filters use actual returned metadata. Ready-empty, filtered-empty, loading,
  cancelled, disabled, failed, stale and truncated lower-bound availability are
  distinct. Failed/stale results never become zero-match success or selectable.
  Refresh/pagination and cancellation retain CSS-48's generation/cache rules.
- `useFindFilters.find(includeCommunity = true)` preserves the CSS-48 default;
  catalog calls `find(false)` for prompt local discovery, then the explicit
  community action uses the current selected target/track. Failed local retries
  cannot silently trigger public lookup. No lookup on mount, OAuth enrollment,
  whole-library polling, artifact fetch, import, vote or automatic selection.
- No HTTP/protocol/schema/migration/persistent/secret-state change. Tests use the
  shared synthetic DTO fixture and mocked local APIs only.

Decisions used (gate IDs and recorded answers): D05 explicit uncertain selection,
D12 compatibility-first ranking with statistics not trust, CSS-35C file-bound
track metadata and approved CSS-36 independent-channel/timing rules. No new gate,
ranking formula, alignment tolerance, retiming, enrollment or launch policy.

Verification commands and results:

- `pnpm --dir web exec vitest run src/scans/community-filter-results.test.tsx
  src/scans/community-result-state.test.tsx`: final 8 passed. Covers explicit
  mixed-channel selection, target/stale invalidation, every filter, within-band
  secondary sorts, honest unavailable fields, failed/stale/cancelled/empty states,
  partial/legacy/derived/zero-findings and escaped long untrusted labels.
- Focused catalog/controller/component regression: 21 tests passed across the
  four corresponding files (19 before the final two filter/sort cases); existing
  local associations and selected-version behavior were retained.
- `pnpm --dir web test`: final 229 tests passed in 34 files.
  `pnpm --dir web build`: TypeScript and Vite passed; existing main-chunk warning
  remains, 520.34 kB. No additional dependency was needed.
- `pnpm --dir web exec playwright test e2e/community-results.spec.ts
  e2e/find-filters.spec.ts`: final 8 Chromium journeys passed at 320x900/1440x900.
  Covers explicit lookup/CSRF/exact selected-version and track bodies, no alternate
  inventory/caller evidence, strong video with incompatible audio, unknown metadata,
  filtering/sorting, native track changes, generation/selection invalidation,
  keyboard checkbox activation/Escape/focus return, local association/review reuse,
  outages/stale selection lock, zero axe violations and no page/dialog overflow.
  Dialog bounds stay inside the viewport. Updated overview/results screenshots
  under `web/test-results/css49-*.png`; desktop/mobile overviews and mobile rows
  inspected directly.
- Editor diagnostics: no errors in all twelve touched source/test/style files.
  Documentation links/fences/status and `git diff --check` validated at handoff.
  Largest touched code files: catalog 337 lines, styles 330; focused result modules
  82/91/66 lines, dialog 66.

Known failures, limitations, unavailable checks, and recovery notes: No remaining
test failures. Initial checks exposed unsupported `replaceAll`, native select
names, provenance stealing first-checkbox focus, and a focus test racing inventory
loading; each repaired and rerun. Existing Vite bundle warning remains. Catalog
summaries do not include full models/settings/parent lineage, feedback rank or raw
download counts; UI labels these unavailable rather than fabricating them. Downloads/
reuse are CSS-50 and feedback/ranking CSS-54. Other Files has no new Find Filters
entry in this task; shared components and CSS-48 manual target API remain reusable.
No backend/Worker tests required or run, because neither changed. No live provider,
community account, personal media/model, database, migration or deployment touched.
Other browser engines/native packaging/live compatibility are unverified.
Frontend-only preview: http://127.0.0.1:5183/; backend configuration/enrollment are
still required for real community lookup. No recovery action required.

Remaining work in this task: None.

Next task ID and exact first action: CSS-50. Read
[50-find-filters-downloads.md](50-find-filters-downloads.md), this channel-selection
handoff and [CSS-44 verified import](44-verified-local-import.md). Start at
`CommunityResultsProps.onSelectionChange` and the existing protected verified
download/import boundary. Connect explicit selected channels/review requirements
to multi-import progress, partial failures and idempotent local reuse; keep local
association separate. Revalidate the selected target/file and exact immutable
artifact bytes before allowing review reuse. Do not treat strong video as approval
of a conflicting/unknown audio channel or add feedback/ranking ahead of CSS-54.

Direct dependent tasks: [CSS-50](50-find-filters-downloads.md).

