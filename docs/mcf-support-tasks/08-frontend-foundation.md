# MCF-08 - Add frontend contracts and settings

Status: **DONE**

Dependencies: MCF-03 through MCF-07. Deliverable: TypeScript contracts, draft behavior,
protected helpers, and complete structured-default Settings UI.

## Scope

1. Mirror the implemented backend DTOs in `web/src/api.ts`: MCF registry/options,
   canonical finding/filter fields, source/review metadata, revisions, default rules,
   import/preflight/export responses, and generic format-keyed status/provenance.
2. Add protected request helpers for settings, default application if server-backed,
   MCF upload/path import, preflight/export, status, and format-specific deletion. Preserve
   application base-path, CSRF, multipart filename, and safe-error behavior.
3. Extend review draft creation, dirty comparison, save replacement, duplicate/import
   replacement, removed snapshots, and metadata-source selection with all MCF fields and
   exact revision semantics. An MCF-only draft edit must be dirty without making EDL state
   appear outdated until saved.
4. Replace the Filters Settings Audio/Video list UI with:
   - an ordered compact selection-rule table with Allow/Deny, optional clauses, reorder,
     duplicate, add, and delete;
    - grouped per-subcategory severity defaults.
5. Provide stable field labels, tooltips for unfamiliar controls, validation, unsaved
   settings state, Save/Discard, optimistic conflict handling, loading/empty/error states,
   keyboard reordering alternatives, and 320px responsive layouts.
6. Explain through concise field help that populated clauses are ANDed, subcategory is
   constrained by family, and Deny overrides Allow.
7. Add API helper, draft-state, settings interaction, keyboard, conflict, responsive, and
   accessibility tests. Update old free-text defaults tests to the new approved contract;
   do not retain two settings authorities.

## Out of scope

- Review metadata/filter controls (MCF-09).
- Import/export menu, badges, or deletion UI (MCF-10).
- Removing backend EDL compatibility adapters (MCF-11).

## Verification

- Focused Vitest for API, drafts, and Settings.
- `pnpm build` in `web/`.
- Targeted browser screenshot/keyboard/axe checks for Settings at desktop and 320px.
- `git diff --check`.

## Acceptance criteria

- [x] TypeScript contracts exactly match live OpenAPI responses and errors.
- [x] Draft equality/save logic includes every MCF field, metadata value, and source choice.
- [x] Structured settings are fully editable without a parallel legacy list UI.
- [x] Ordering/precedence/validation/conflict behavior is visible and accessible.
- [x] Desktop/mobile layouts avoid nested cards, overlap, and page-level overflow.

## Handoff

Status: DONE
Last completed substep: Completed desktop/320px browser, keyboard, overflow, and axe
verification after passing full frontend and backend suites.
Files changed: `scanner/models.py`, `scanner/api.py`, `scanner/auth_policy.py`,
`tests/test_mcf_contract.py`, `web/src/api.ts`, `web/src/review-draft.ts`,
`web/src/review-draft.test.ts`, `web/src/filter-defaults-settings.tsx`,
`web/src/filter-defaults-settings.test.tsx`, `web/src/App.tsx`,
`web/src/App.test.tsx`, `web/src/styles.css`, this task record, and the task-pack README.
Schema/API/contract changes: Added protected read-only `GET /api/mcf-registry`, derived
directly from the canonical backend registry, so frontend labels/options do not duplicate
97 tokens. TypeScript now mirrors canonical classifications, MCF metadata/import/preflight/
export, structured defaults, format revisions/status, provenance, and safe error fields.
Review drafts include MCF document/source choice and both format revisions; full save sends
the required document. Apply Defaults uses the protected backend evaluator.
Verification commands and results: Focused API/draft/Settings/App tests passed; final
`pnpm build` succeeded with 39 modules and final `pnpm test` passed 105 tests in 11 files.
Backend Ruff passed; focused registry/OpenAPI/auth verification passed 98 tests; final
backend `uv run pytest -q` passed 1,057 tests in 35.06 seconds without warnings. VS Code
reported no diagnostics in changed backend/frontend files.
Manual or artifact evidence: Playwright verified the real Filters view with intercepted
canonical responses at 1440x1000 and 320x900. Both measured `scrollWidth == clientWidth`;
no visible element exceeded the mobile viewport. Keyboard Enter added a rule and activated
Move Up with visible focus. axe-core 4.13.0 reported zero WCAG A/AA/2.1AA violations and
19 passing rules. Desktop and mobile screenshots showed no overlap or clipped control text.
Known failures or risks: Review MCF editing and metadata-source choice UI are intentionally
deferred to MCF-09; import/export/status UI is deferred to MCF-10.
Acceptance criteria still unmet: None.
Next exact action: Implement MCF-09 review metadata/filter/manual controls and provenance UI
using the registry and draft contracts completed here.
Next eligible task: MCF-09 after this task is DONE.
Decisions or deviations from the parent plan: Added `/api/mcf-registry` because the frozen
contract requires frontend options to consume the backend-owned labels/tokens but no prior
route exposed that data. The legacy frontend Audio/Video list authority and client-side
defaults evaluator were removed; backend compatibility projections remain until MCF-11.
User approval required: None.
