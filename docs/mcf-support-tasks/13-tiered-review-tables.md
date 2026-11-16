# MCF-13 - Build tiered Review finding tables

Status: **DONE**

Dependencies: MCF-12. Deliverable: nested, collapsible MCF finding hierarchies with
independently sortable Audio and Video Review tables.

## Scope

1. Replace the current single-level Audio/Video result grouping with three nested,
   collapsible levels: MCF family, subcategory, then the existing finding group such as
   an audio root word. Keep Audio and Video as separate tables.
2. Sort family and subcategory sections alphabetically by displayed name. Sort finding-group
   sections alphabetically for Description and Severity, and by the first row in each group
   for Time, honoring the active direction. Start family sections expanded and
   subcategory/finding-group sections collapsed. Always render the finding-group level. Use
   `Unclassified` for both missing family and missing subcategory on legacy findings.
3. Make each hierarchy header's only descriptive text its section name. Retain its
   disclosure control, bulk-selection checkbox, result count, and selected count, but
   remove repeated family, subcategory, severity, channel, comment, and classification
   summaries.
4. Move severity and channel into visible table columns. Severity is never a grouping
   level. Channel remains visible but is not sortable. Preserve the existing Use, Time,
   Description, Scan, and row-control behavior.
5. Remove the `ABC | Time` segmented control. Make Time, Description, and Severity column
   headers sort buttons with the exact `▲`/`▼` indicator and `aria-sort` behavior used by
   the Saved Reviews table. Keep the active indicator immediately beside its header label.
6. Keep independent sort key/direction state for Audio and Video. Each defaults to Time
   ascending. Clicking an inactive sortable column selects it ascending; clicking the
   active column reverses direction. Apply row sorting only inside each finding group. Time
   and Description also reorder finding-group headers as described above; family and
   subcategory sections never move or split through a column sort.
7. Compare severity semantically. Ascending order is missing, Low, Medium, High;
   descending reverses it. Keep deterministic tie-breakers so equal values do not flicker
   or reorder unpredictably.
8. When a merged result spans multiple family/subcategory paths, repeat the merged row
   under every applicable path. Append a visible `🔗` after each repeated description and
   provide an accessible text equivalent. Every instance represents the same underlying
   result: selecting or deselecting one updates all other instances.
9. Preserve draft edits, preview, delete, Apply Filter Defaults, no-objectionable-content
   visibility, focus-from-MCF-preflight, dirty guards, and existing result-merging semantics
   other than the specified repeated hierarchy views.
10. Add focused interaction tests for hierarchy membership/order, initial disclosure state,
    header content, bulk selection, unclassified findings, linked repeated rows, independent
    sorting, direction reversal, severity order, and synchronized selection.

## Constraints

- This is a frontend presentation/state change. Do not add or change database, API, review,
  scan-finding, or export contracts.
- Consume the existing canonical registry labels for family/subcategory display.
- Keep the existing work-focused visual language and table density; do not introduce cards
  or nested cards around hierarchy levels.
- At 320px, text and controls must remain usable without page-level horizontal overflow.
- Disclosure controls, bulk-selection state, sortable headers, and linked-row indicators
  must have keyboard and screen-reader semantics. Do not rely on glyphs or color alone.

## Verification

- Focused `App.test.tsx` tests for tier construction, disclosure, sorting, and linked rows.
- `pnpm test` and `pnpm build` in `web/`.
- Playwright checks for separate Audio/Video sorting, nested keyboard disclosure, and
  synchronized repeated-row selection.
- Desktop and 320px screenshots plus page-width checks for a review containing classified,
  unclassified, and cross-hierarchy merged findings.
- Axe checks on the populated Review results view.
- `git diff --check`.

## Acceptance criteria

- [x] Audio and Video each render family > subcategory > finding-group disclosure levels,
   with alphabetical family/subcategory order, sort-aware leaf order, families initially
   open, and lower levels closed.
- [x] Headers contain no descriptive text beyond the section name; existing checkbox/count
      state remains functional and classification summaries are removed.
- [x] Severity and channel are visible columns, and severity is not used as a section.
- [x] Time, Description, and Severity sort rows only within leaf groups using independent
      Audio/Video state, Time-ascending defaults, reversible direction, Saved
   Reviews-compatible inline triangles, and correct `aria-sort` values. Description sorts
   leaves alphabetically; Time sorts leaves by each group's first row.
- [x] Missing severity sorts below Low, and descending severity reverses the complete
      missing/Low/Medium/High order.
- [x] Legacy unclassified findings remain actionable under `Unclassified` hierarchy levels.
- [x] Cross-hierarchy merged results repeat under every applicable path, show an accessible
      `🔗` indicator after the description, and synchronize selection across instances.
- [x] Existing Review interactions remain passing with no desktop/mobile overflow or
      accessibility regression.

## Handoff

Status: DONE
Last completed substep: Preserved complete singular `-er` audio roots such as `murder`
instead of displaying synthetic fragments such as `murd`.
Files changed: `web/src/App.tsx`, `web/src/styles.css`, `web/src/App.test.tsx`,
`web/e2e/mcf10.spec.ts`, `docs/feature-design-spec.md`, the parent/task trackers, and this
task file.
Schema/API/contract changes: None.
Verification commands and results: Frontend build passed; 112 Vitest tests passed; all six
shared MCF Playwright tests passed; `git diff --check` passed.
Manual or artifact evidence: Desktop 1440x1000 and mobile 320x900 screenshots are in
`web/test-results/mcf13-review-desktop.png` and `web/test-results/mcf13-review-mobile.png`.
Both populated views had no page-level horizontal overflow and no WCAG 2 A/AA/2.1 AA axe
violations.
Known failures or risks: Narrow tables require their labelled internal horizontal scroller
to reach Severity, Channel, Scan, and row controls.
Acceptance criteria still unmet: None.
Next exact action: None.
Next eligible task: None; the MCF task pack is complete.
Decisions or deviations from the parent plan: None.
User approval required: None.