# RP-01 — Persist and resolve deployment configuration

Dependencies: RP-00. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

Base URL and listening port are UI-owned saved overrides in a separate file. Base
URL's environment variable is a seed, unlike existing authentication settings whose
environment variables remain authoritative. A saved URL without a path explicitly
selects root even if the environment contains /editarr. No schema migration is needed.

## Read and expected touch points

Read scanner/config.py, scanner/auth_origin.py, scanner/auth_settings.py and
tests/test_auth_config.py. Add a focused deployment-configuration module/test file;
avoid turning auth_settings.py into the deployment store. Use RP-00's chosen filename,
schema, unset representation, and fallback rules.

## Implementation steps

1. Implement pure URL parsing and canonicalization using a URL parser. Separate
   normalized full Base URL, serialized public origin, and canonical prefix.
   Reuse origin validation on the extracted origin without changing Origin-header grammar.
2. Cover no path, /, trailing slash, nested paths, explicit public ports and bracketed
   IPv6. Reject credentials, query/fragment, invalid ports, traversal/backslashes,
   controls, and ambiguous encoded separators per the frozen contract.
3. Implement reading optional saved fields from the approved data-directory file.
   Absent file means no overrides; malformed/unreadable files must give actionable
   errors and must not be silently reset to defaults.
4. Implement partial writes: saving Port must not freeze a displayed Base URL seed.
   Explicit Base URL save must persist even if equal to the displayed environment seed.
   Do not serialize effective fallback values as user overrides accidentally.
5. Validate a complete proposed update before writing. Use a temporary file in the
   same directory and atomic replace; clean up failed temporary writes. Protect
   concurrent updates from losing another field. Do not let API clients choose paths.
6. Implement resolver precedence and source metadata. Keep persisted, active, and
   next-start values distinct; do not mutate environment objects to simulate saving.
7. Ensure lower-priority invalid/obsolete values do not wrongly override a valid saved
   Base URL. Validate selected values at the right stage while retaining security rules.
8. Preserve all other auth configuration semantics and expose safe diagnostics only.

## Localhost requirement update

Absent saved Base URL and seed is valid: derive http://localhost:<selected port>/,
prefix /, source local_default. Do not persist this derived URL. Malformed explicit
URLs remain errors. Test absent file/URL, saved/CLI ports, and Port-only saves.

## Confirmed environment and transition contract

Remove EDITARR_AUTH_PUBLIC_ORIGIN entirely from configuration resolution and validation;
there is no legacy seed/source. Verify it has no effect even if set to an invalid value.
Store the approved persistent monotonic transition cutoff in deployment configuration,
but do not activate it when saving pending Base URL edits. RP-08 coordinates activation
and failure recovery; do not add a database schema change or erase session records.

## Acceptance criteria

- [x] All parent-plan URL examples resolve exactly, without doubled separators.
- [x] Saved prefixed and saved root URLs override changed environment seeds after reload.
- [x] Absent override, explicit save of the seed, and unrelated-field save stay distinct.
- [x] A public URL port never becomes the listening port implicitly.
- [x] Invalid input or I/O failure preserves the previous valid file and reports failure.
- [x] Partial/concurrent writes cannot silently discard the other deployment field.
- [x] Corrupt files are reported, not overwritten; config location stays under data_dir.
- [x] Existing authentication environment precedence and strict Origin parsing are unchanged.

## Verification

Run the new parser/store/resolver tests with temporary directories and injected
write failures, plus tests/test_auth_config.py and origin cases in test_auth_policy.py.
Use fixtures for environment changes between loads. Tests must assert behavior after
re-reading the file, not only inspect serialized text.

## Definition of done

Shared DoD applies. The module exports RP-00's parser, store, and resolver contract;
focused persistence/error/precedence tests pass. RP-02 and RP-06 can consume it without
inventing field meanings. No database or user configuration was modified.

## Handoff

Status: DONE
Audit September 4, 2026: Fixed explicit `environment={}` to ignore process seeds.
  Implemented the user's newly approved ASCII-only path-segment restriction (RP-00)
  to reject encoded/non-ASCII/reserved paths that could not round-trip through routing
  and browser URLs; empty query/fragment delimiters are also rejected. Added parser
  and environment-isolation regressions. Final focused backend audit command listed
  in the task-pack README passed 83 tests; focused Ruff passed.
Last completed substep: Added the URL parser, file-backed override store, immutable
  next-start resolver, and focused persistence/precedence/error coverage.
Files changed: scanner/deployment_config.py; scanner/config.py;
  tests/test_deployment_config.py; tests/test_auth_config.py;
  docs/reverse-proxy-production-tasks/01-deployment-config.md;
  docs/reverse-proxy-production-tasks/README.md (RP-01 checklist only).
Contract/API changes: `scanner.deployment_config` exports `parse_base_url()` →
  `ParsedBaseURL`, `DeploymentStore(data_dir)` with `read()` and field-preserving
  `update()`, and `resolve_deployment_snapshot()` → immutable `DeploymentSnapshot`.
  `deployment.json` is versioned and atomically replaced under `${data_dir}`; it
  persists only explicit `base_url`, `port`, and non-secret `CookieScope` metadata.
  The resolver applies saved → EDITARR_BASE_URL → localhost precedence and CLI →
  saved → default port precedence. `Settings` no longer has or validates the obsolete
  `EDITARR_AUTH_PUBLIC_ORIGIN` source.
Verification commands and results: `uv run ruff check scanner/deployment_config.py
  scanner/config.py tests/test_deployment_config.py tests/test_auth_config.py` → passed;
  `uv run pytest -q tests/test_deployment_config.py tests/test_auth_config.py` →
  51 passed; `uv run pytest -q tests/test_auth_policy.py::test_public_origin_normalizes_only_approved_equivalences
  tests/test_auth_policy.py::test_public_origin_rejects_ambiguous_values
  tests/test_auth_policy.py::test_source_origin_rejects_trailing_slash_and_preserves_exact_tuple
  tests/test_auth_policy.py::test_unsafe_source_origin_headless_exception_is_narrow` →
  13 passed (one existing Starlette/httpx deprecation warning); `git diff --check` → passed.
Acceptance criteria still unmet: None.
Approvals received or outstanding: RP-00's approved saved/environment/local precedence,
  old-variable removal, local HTTP policy, and persistent monotonic cutoff are implemented;
  no further approval is required.
Known failures/limitations: The current HTTP app and several broad authentication tests
  still construct/read `Settings.auth_public_origin` and call
  `require_auth_public_origin()`. That removed legacy boundary must be replaced by the
  active `DeploymentSnapshot` during RP-02/RP-03; it is intentionally not retained as
  a compatibility source. No database, real deployment configuration, or scan worker
  was changed.
Next exact action: RP-02 should instantiate `DeploymentStore(settings.data_dir)` before
  binding, pass parsed optional CLI port plus bind host and Settings cookie-override
  metadata to `resolve_deployment_snapshot()`, and retain the resulting active snapshot
  for the service generation. It must not persist CLI/default/local fallback values.
  RP-06 should use `DeploymentStore.update()` with omitted fields left omitted and expose
  saved versus next-start state. When it saves a pending URL-scope change it must retain
  the active prefix as `CookieScope.pending_previous_path`; only RP-08 may advance the
  cutoff, append de-duplicated legacy paths, and clear that pending metadata after the
  new child is known to be active.
