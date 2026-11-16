# CSS-07 — Unify release and scanner version metadata

Status: **DONE** (September 26, 2026). Release drift is checked at build time and
the running app exposes bounded, independent version identities.

- Prerequisites: [CSS-00](00-repository-preflight.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §2.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

0.1.0 exists in Python and frontend metadata. Release, scanner behavior, schema, and build versions have different meanings and must not be conflated.

## Starting points

pyproject.toml; scanner/__init__.py; scanner/modeling/; web/package.json; build scripts; settings About UI.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement the approved release authority and deterministic propagation/checking across Python, frontend and native build metadata.
2. Add explicit scanner behavior/build metadata and a bounded version/capability endpoint plus About display.
3. Document when each version changes and expose unknown/dirty development build states honestly.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Build fails on version drift; installed frontend/backend report the same release.
- [x] DB/API/artifact versions are independent of release/scanner behavior.
- [x] No existing scan is relabeled with current versions.

## Verification

- Run metadata/build consistency tests and endpoint/UI tests.
- Verify packaged metadata without relying on a .git directory at runtime.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide authoritative source, bump procedure, endpoint types, and compatibility version constants to CSS-08/09/11.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: `pyproject.toml` remains the
release authority. Direct Hatch wheel/sdist builds and the native release script
verify the literal Python facade and web package release before packaging; the
API/OpenAPI version now uses the Python release. The wheel embeds build identity
at build time, and native inventory includes revision and dirty state; absent
Git yields explicit unknown values and runtime never consults Git. Settings >
General > About displays the backend and bundled frontend releases, flags any
mismatch, and reports scanner behavior, supported schema and local API versions.

Files changed and relevant revision: `scanner/api.py`,
`scanner/http/api_system.py`, `scanner/security/auth_policy.py`,
`scanner/settings/version_info.py`, `scripts/release_assets.py`,
`scripts/hatch_build.py`, `scripts/build_release.py`, `pyproject.toml`,
`web/src/api/api-service.ts`, `web/src/api/api-system-types.ts`,
`web/src/settings/about.tsx`, `web/src/settings/settings-view.tsx`,
`web/src/styles/shell.css`, `tests/test_version_info.py`,
`tests/test_migration_packaging.py`, `web/src/settings/about.test.tsx`,
`README.md`, `docs/api-contract.md`, this task and the index. Release remains
`0.1.0`; no migration or existing scan row changed.

Contracts / APIs / schemas / migrations / fixtures introduced: Protected bounded
`GET /api/system/version` returns `release`, nullable `build_revision` and
`build_dirty`, `scanner_behavior`, `database_schema`, `local_api`, nullable
`community_api` and `artifact_schema`. Values currently include `scanner-1`,
`0001_current_baseline`, local API `1`; community/artifact are null because not
implemented. `scanner/settings/version_info.py` owns the scanner/local API
constants; the supported schema comes from the pinned migration revision.
Wheel and sdist build paths include checked version metadata and embedded
`scanner/build-info.json`; native inventory has `source_dirty` alongside
`source_revision`. No persistence, protocol schema or historical backfill.

Decisions used (gate IDs and recorded answers): Confirmed D16 separate release,
scanner, schema and protocol identities; no new gate.

Verification commands and results: Focused `.venv/bin/python -m pytest -q
tests/test_native_runtime_build.py tests/test_version_info.py
tests/test_migration_packaging.py tests/test_auth_policy.py tests/test_auth_api.py
tests/test_migration_preflight.py` passed 69; real Hatch build drift refusal,
sdist-to-wheel rebuild and protected route inventory are covered.
Full `.venv/bin/python -m pytest -q tests` passed 1,304 tests with one
pre-existing Qwen prompt assertion failure (see below). `pnpm --dir web test`
passed 138 tests and `pnpm --dir web build` passed. Ruff checks on changed
Python files and `git diff --check` passed after implementation. An isolated
mocked-auth Vite browser check rendered About at 320x900 and 1440x900:
document widths were 320 and 1440 respectively, with the About details
inside both viewports and no horizontal page overflow. The preview server
was stopped after inspection.

Known failures, limitations, unavailable checks, and recovery notes: Full
backend suite still fails
`tests/test_qwen_language.py::test_qwen_language_batches_unique_contexts_and_preserves_finding_fields`
because its expected prompt phrase is absent, as in CSS-06. No production
native artifact was built; isolated installed-wheel startup and packaged
build-info behavior were exercised with disposable files. The first isolated
browser mock stalled at the auth gate; a fresh fetch-stubbed preview passed
the About viewport check. Builds
from a source copy without its own Git root report unknown provenance. Unknown
historical scan provenance stays unknown; CSS-09 owns execution-time capture.

Remaining work in this task: None.

Next task ID and exact first action: CSS-08; read
[08-provenance-schema.md](08-provenance-schema.md), then design a new Alembic
revision from the CSS-06 pinned baseline and add nullable provenance fields
without retroactively assigning this binary's versions to prior scans. CSS-09
must use the explicit scanner version at actual stage execution time. CSS-11
must define artifact schema independently once its codec is selected.

Direct dependent tasks: [CSS-08](08-provenance-schema.md), [CSS-67](67-packaging-and-user-docs.md).

