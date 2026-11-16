# MCF-09 - Build review and manual-filter UI

Status: **DONE**

Dependencies: MCF-08. Deliverable: complete scan/review MCF visibility, editable document
metadata, filter editing, manual entry, default application, and metadata-source choice.

## Scope

1. Add a compact collapsible `MCF document metadata` section below the current review
   title. Its collapsed summary and expanded controls cover title, year, type,
   season/episode conditions, source/IMDb URLs, release, comment, material start/end, and
   imported/catalog provenance.
2. Show imported-vs-catalog differences as a non-blocking comparison on the same physical
   file. Use `MCF metadata`/`Catalog metadata`, not a second physical-version label.
3. When a new review has distinct imported metadata candidates, require a source selector
   listing each scan label/version and catalog/runtime defaults before save. Duplication
   displays the copied review metadata and does not ask again.
4. Show family/subcategory labels, severity, and channel in grouped Audio/Video results
   without fixed columns that cause page overflow. Expose exact tokens/comments in details.
5. Extend inline editing with a family selector, constrained searchable subcategory
   selector, Low/Medium/High segmented control, channel select, and spec-valid comment.
   Preserve label, description, group, timing, action, selection, preview, and delete.
6. Require family/subcategory in Add Manual Filter. Unclassified legacy EDL filters show
   an explicit `Needs classification` state and can be classified in the same editor.
7. Wire Apply Filter Defaults to the shared evaluator/API. State that it recomputes
   selection, severity, and channel but never category/subcategory. Show change counts;
   keep results draft-only with Save/Discard.
8. Show exact MCF fields and imported source version/metadata on applicable scan/finding
   provenance surfaces. Hide empty MCF panels for EDL-only/generated evidence.
9. Add focused interaction tests for metadata validation, candidate choice, category
   search, severity/channel/comment, clear, manual staging, Apply overwrite, dirty guards,
   Save/Discard, source isolation, responsive wrapping, and keyboard/axe behavior.

## UI constraints

- Use the existing work-focused visual language; no marketing layout or nested cards.
- Subcategory search results are constrained/grouped by official family and support keyboard use.
- Severity/channel/export state use visible text and semantics, not color alone.
- Long category labels/comments wrap within result/editor bounds at 320px.
- Existing audio censorship and preview behavior remains unchanged for legacy label/details.

## Verification

- Focused `App.test.tsx` and draft/API tests.
- `pnpm build` in `web/`.
- Playwright desktop/320px screenshots and interaction checks for catalog and Other Files.
- Axe and manual keyboard checks for selectors, disclosure, editor, and source choice.
- `git diff --check`.

## Acceptance criteria

- [x] Every relevant MCF field is visible and editable in a saved review/manual flow.
- [x] Metadata variants remain attached to the selected physical file and preserve provenance.
- [x] Multiple source candidates cannot be saved without an explicit choice.
- [x] Apply behavior is accurately disclosed, draft-only, countable, and discardable.
- [x] Unclassified legacy EDL filters remain usable and clearly actionable.
- [x] Desktop/mobile/accessibility checks pass without overlap or overflow.

## Handoff

Status: DONE
Last completed substep: Verified shared catalog and Other Files review controls at desktop
and 320px, including keyboard access and axe, after the full frontend gate passed.
Files changed: `web/src/review-mcf.tsx`, `web/src/review-mcf.test.tsx`,
`web/src/App.tsx`, `web/src/App.test.tsx`, `web/src/styles.css`, this task record, and the
task-pack README.
Schema/API/contract changes: None. The UI consumes the MCF-08 registry, review document,
metadata-source candidate, classification, defaults, and revision contracts. Saved-review
validation now blocks malformed classification/comment data and unresolved metadata-source
choices before sending existing create/save requests.
Verification commands and results: Focused review-MCF and App tests passed during
implementation. Final `pnpm build` succeeded, and final `pnpm test` passed 109 tests in 12
files. `git diff --check` passed.
Manual or artifact evidence: Playwright exercised an imported MCF saved review with editable
document metadata, a classified visual filter, an unclassified legacy audio filter, and a
long exact comment in both catalog and Other Files flows. At 320x900 each route measured
`documentElement.scrollWidth == clientWidth == 320`; all MCF controls stayed within the
viewport. Keyboard Tab reached the labeled Media files scroller. axe-core 4.13.0 reported
zero WCAG 2 A/AA/2.1AA violations on both routes. Desktop and mobile screenshots captured
the metadata and inline classification editors without overlap or clipping.
Known failures or risks: MCF import/export menus, format badges/status, and deletion remain
intentionally deferred to MCF-10.
Acceptance criteria still unmet: None.
Next exact action: Implement MCF-10 import, export, format-status, badge, and deletion UI on
top of these shared review controls.
Next eligible task: MCF-10 after this task is DONE.
Decisions or deviations from the parent plan: None. Existing audio censorship and preview,
selection, timing, action, dirty-guard, Save, and Discard behavior were preserved.
User approval required: None.
