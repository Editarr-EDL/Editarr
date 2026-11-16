# MCF-11 - Finish docs and integration verification

Status: **DONE**

Dependencies: MCF-05, MCF-07, and MCF-10. Deliverable: the documented core MCF/EDL
implementation with compatibility adapters removed and full core release evidence.

## Scope

1. Audit parent-plan outcomes and every prerequisite handoff against live code, schema,
   OpenAPI, frontend, and tests. Reopen incomplete prerequisite tasks rather than masking
   gaps here.
2. Remove temporary EDL-only response/status/default-filter compatibility adapters frozen
   by MCF-00 once all production consumers use format-keyed state and structured defaults.
   Do not remove Simple EDL itself or its public supported workflows.
3. Search for stale EDL-only assumptions in models, route names/copy, catalog decorators,
   scan references, saved-review summaries, default settings, tests, and UI badges. Replace
   only assumptions superseded by the parent plan.
4. Search for the obsolete `ContentCategory`, `mcf_category`, `mcf_severity`, category-
   assignment rules, and dual legacy/MCF taxonomy. None may remain as an authority.
5. Update `docs/feature-design-spec.md` as the authoritative product behavior: ownership,
   MCF fields/metadata, defaults, revisions, import/export, badges, deletion, errors,
   accessibility, and API map.
6. Update `docs/api-contract.md` with exact implemented DTOs/routes/errors and the fresh-
   schema compatibility statement. Update `README.md` with concise user/operator guidance
   for MCF import, defaults, manual editing, export preflight, sidecars, and independent
   deletion/status.
7. Update release/native/runtime smoke coverage where `.mcf` sidecars, module collection,
   filesystem permissions, or production frontend assets require it. Do not add notices or
   dependencies not actually used.
8. Recreate a temporary fresh database and run an end-to-end persistence journey: create
   review from scans, MCF import 1.0/1.1, EDL import, manual MCF filter, apply defaults,
   duplicate/edit isolation, export both formats, independent stale states, restart,
   independent deletion, review deletion attribution clearing, and scan guards.
9. Run the complete verification matrix. Record exact counts, warnings, browser screenshots,
   package/runtime evidence, and any unrelated failures. Do not use a live service, user
   database, real catalog credentials, media, or sidecars.

## Required verification

- `uv run ruff check scanner tests scripts native_build`.
- Complete backend pytest suite using temporary data.
- `pnpm test` and `pnpm build` in `web/`.
- Complete relevant Playwright desktop/mobile/axe suite.
- Release/native smoke checks affected by MCF codec/export imports and frontend assets.
- Fresh temporary file-backed SQLite restart verification.
- Exact MCF golden fixtures plus EDL regression/round-trip tests.
- Live OpenAPI comparison against `docs/api-contract.md`.
- `git diff --check`.

## Acceptance criteria

- [x] Every parent-plan required outcome and decision is implemented without a competing
  authority or undocumented deviation.
- [x] MCF 1.0/1.1 import, 1.1 export, manual data, defaults, UI, badges, and cross-format
  workflows pass for catalog and Other Files.
- [x] EDL remains fully supported and independently tracked.
- [x] Temporary adapters and stale EDL-only assumptions are gone.
- [x] Authoritative docs and live OpenAPI match the implementation.
- [x] Full backend/frontend/browser/runtime verification passes or the task remains blocked.
- [x] No installed/user data, live service, real media, or real sidecar was changed.

## Handoff

Status: DONE
Last completed substep: Completed full backend/frontend/browser/release verification and the live OpenAPI/document comparison.
Files changed: `scanner/{api,db,models,repository,runtime_smoke}.py`; `web/src/{App,App.test,api,api.test,review-draft.test}.ts*`; `web/e2e/{app,mcf10,production}.spec.ts`; `scripts/production_browser_fixture.py`; focused backend integration/schema tests including new `tests/test_mcf_final_integration.py`; `README.md`; `docs/{api-contract,feature-design-spec}.md`; and this task pack.
Schema/API/contract changes: No persistence shape changed. Removed frozen transitional Python/DTO projections (`EdlStatus`, `ReviewEdlExport`, `EdlScanReference`, legacy summary/catalog/reference/default aliases and row synonyms) plus EDL-only status/delete route aliases. Canonical `ExportStatus`, generic format delete/status routes, `ReviewExportResponse`, structured defaults, and format revisions are now the only public authority. OpenAPI has 73 paths and executable documentation comparison coverage.
Verification commands and results: `uv run ruff check scanner tests scripts native_build` passed; full backend `uv run pytest -q` passed 1,060 tests in 30.76s; focused codec/EDL/release-native set passed 174 tests; `pnpm test` passed 111 tests in 12 files; `pnpm build` transformed 40 modules; mocked Chromium Playwright passed 35 tests; disposable Nginx/TLS production Playwright passed 1 test in 5.5s; fresh file-backed SQLite/OpenAPI integration passed 2 tests; `git diff --check` passed.
Manual or artifact evidence: Responsive/axe screenshots are `web/test-results/mcf10-{catalog,manual}-{desktop,mobile}.png`. Disposable frozen runtime `/private/tmp/editarr-mcf11-build.RIbEjk/Editarr/Editarr` passed built-in runtime smoke and has SHA-256 `baca67de3219cac65e11f5cb446883b247f1ef6d37ed5375e42469e8462a93a9`; inventory SHA-256 is `f2aa4dc86147b168076c54ddc550ab28d0bf730eb4b88b39514f1d0669bd4a7a`.
Known failures or risks: Direct `swift test` is unavailable in this host toolchain (`XCTest` framework unresolved; full Xcode/`actool` absent). The affected native/release Python tests, frozen macOS build/runtime smoke, and installed production browser workflow all pass. PyInstaller reported optional missing CUDA/HIP/Windows libraries and hidden imports during macOS analysis; the macOS build and smoke completed.
Acceptance criteria still unmet: None.
Next exact action: Begin MCF-12 only when assigned.
Next eligible task: MCF-12.
Decisions or deviations from the parent plan: None.
User approval required: None.
