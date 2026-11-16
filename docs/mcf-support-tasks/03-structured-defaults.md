# MCF-03 - Implement structured defaults

Status: **DONE**

Dependencies: MCF-01. Deliverable: revisioned default settings, shared evaluator, and
protected settings/application APIs; no React UI.

## Scope

1. Replace the persisted Audio/Video string lists with the exact MCF-00 structured DTOs:
   ordered Allow/Deny selection rules, per-subcategory severity defaults, and settings
   revision.
2. Preserve audio word/root normalization and implement canonical family, subcategory,
   severity, channel, evidence type, and label clauses with wildcard/AND semantics, stable
   ordering, and Deny wins.
3. Fill channel from evidence source and severity from the filter's subcategory default.
   Never derive severity from confidence and never reclassify category/subcategory.
4. Expose pure evaluator operations for:
   - new-review severity/channel fill plus selection evaluation;
   - MCF/EDL import severity/channel fill plus selection evaluation;
   - manual-filter severity/channel fill plus selection evaluation; and
   - explicit Apply behavior that recomputes severity/channel and selection.
5. Ensure evaluator results are copies/patches; they never mutate `ScanFindingRow`.
6. Add revision-guarded protected GET/PUT settings routes and, if frozen by MCF-00, a
   review-draft apply/preview route. Map stale revisions to the approved safe conflict.
7. Decide the one-time fresh-schema seed representation only as frozen in MCF-00. Do not
   silently import obsolete development settings through a migration.
8. Add repository, evaluator, API/OpenAPI, auth, CSRF, normalization, ordering, concurrency,
   and source-immutability tests.

## Out of scope

- Applying defaults inside review/import transactions (MCF-04/05).
- Frontend settings editor (MCF-08).
- Export-time completion; it is prohibited.

## Verification

- Ruff changed Python files.
- Focused settings repository/evaluator/API/auth/OpenAPI tests.
- Existing default-filter and saved-review tests updated to the frozen contract without
  weakening behavior.
- `git diff --check`.

## Acceptance criteria

- [x] One deterministic evaluator implements every automatic and explicit mode.
- [x] Selection rules match canonical fields with Deny precedence.
- [x] Severity seeds are exact and confidence is ignored.
- [x] Apply can overwrite draft severity/channel but never classification or source evidence.
- [x] Concurrent settings writes cannot silently overwrite each other.
- [x] Existing Audio/Video matching behavior is representable in the unified rules.

## Handoff

Status: DONE
Last completed substep: Added typed manual-filter automatic filling through the shared
evaluator, hardened stale no-op detection across sessions, and ran the full backend suite.
Files changed: `scanner/models.py`, `scanner/filter_defaults.py`, `scanner/repository.py`,
`scanner/api.py`, `scanner/auth_policy.py`, `tests/test_filter_defaults.py`,
`tests/test_editarr_integration.py`, this task record, and the task-pack README.
Schema/API/contract changes: Added `FilterDefaultsApplyRequest` and
`FilterDefaultsApplyResult`; replaced the settings GET/PUT response/write contract with
revisioned `FilterDefaultsSettings`/`FilterDefaultsUpdate`; added protected `POST
/api/settings/filters/apply`; and added safe 409 `stale_settings_revision` handling. The
MCF-01 normalized tables remain the sole persistence authority.
Verification commands and results: Dedicated evaluator/repository/concurrency/OpenAPI
suite passed 11 tests. Focused evaluator, API, compatibility, scan-evidence, and auth-route
checks passed 10 tests. Focused MCF-03 regression set passed 187 tests. Final `uv run ruff
check scanner tests scripts native_build` passed; final `uv run pytest -q` passed 986 tests
in 28.12 seconds. VS Code reported no diagnostics in changed source/test files.
Manual or artifact evidence: A temporary file-backed SQLite test opened two independent
sessions at revision zero, committed one update, and proved both stale unchanged and stale
changed writes were rejected without replacing the winner. All tests used in-memory or
test-owned temporary databases; no installed/user database was accessed.
Known failures or risks: The React settings client still uses the transitional Audio/Video
shape until MCF-08. A read-only legacy projection remains for compatibility, but there is
no legacy mutation path.
Acceptance criteria still unmet: None.
Next exact action: Implement MCF-04 review-domain integration using
`fill_filter_defaults`, `fill_manual_filter_defaults`, and the persisted settings snapshot.
Next eligible task: MCF-04 after this task is DONE.
Decisions or deviations from the parent plan: Automatic new-review/import paths share
`fill_filter_defaults`; manual input has a typed wrapper over the same evaluator. The
read-only legacy settings projection is retained until MCF-08/11, while all writes use the
structured revision-guarded authority.
User approval required: None.
