# CSS-06 — Wire migration preflight into startup, workers, and CLI

Status: **DONE** (September 26, 2026). Migration admission is wired into local startup,
offline commands, worker entry and cold-start cleanup.

- Prerequisites: [CSS-02](02-maintenance-coordination.md), [CSS-05](05-adopt-upgrade-database.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §1.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Schema validation must precede auth queries, queue recovery, and worker writes. Existing init_db call sites include HTTP lifecycle and CLI paths.

## Starting points

scanner/db.py; scanner/http/api_lifecycle.py; scanner/cli.py; focused scanner/commands/ modules; worker entry points.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Audit all initialization paths and route one owner through preflight/snapshot/upgrade before readiness.
2. Make workers check supported revision without migrating; expose offline inspect/upgrade/recovery commands using the same services.
3. Show maintenance/failure diagnostics and preserve schema assets in packaged startup paths.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] No ordinary query, queue recovery, or worker launch occurs before schema compatibility succeeds.
- [x] CLI/HTTP concurrent starts cannot race migrations; failed upgrades remain in maintenance.
- [x] Installed app startup uses packaged migrations without a source-checkout path;
  a disposable wheel with the existing built frontend passed real HTTP startup.

## Verification

- Run startup-gate, CLI, dedicated-worker and restart tests plus packaged migration smoke.
- Verify future/unknown database fixtures produce useful errors before any application write.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide initialization call-site inventory, CLI usage, readiness ordering, and safe upgrade flow for all subsequent tasks.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: `scanner.db.init_db()` now performs
preflight and coordinated fresh migration or exact-baseline adoption, never
`create_all()`. HTTP lifespan calls it before CSRF key creation, auth queries and
scan-queue recovery. Both admin/auth CLI paths call it before queries. Dedicated
workers check the supported revision read-only under their worker lease before
their first queue query; cold-start Quit cleanup checks it before opening a session.
HTTP/CLI competing upgrades use the same CSS-02 exclusive lease, with another
creator's fresh-file race handled by inspecting the winning file. Pending/failed
journals prevent readiness even if the database otherwise looks current.

Files changed and relevant revision: `scanner/db.py`, `scanner/http/api_lifecycle.py`,
`scanner/cli.py`, `scanner/worker.py`, `scanner/runtime/restart_service.py`,
`scanner/persistence/migrations/preflight.py`, `scanner/commands/migration.py`,
`tests/test_migration_preflight.py`, `tests/test_migration_packaging.py`,
`tests/test_dedicated_worker.py`, `README.md`,
this task brief and task index. Supported head remains `0001_current_baseline`.

Contracts / APIs / schemas / migrations / fixtures introduced: Offline
`editarr --data-dir DIR migration inspect|upgrade|recover-export DEST`; inspection
reports missing, unversioned-v1 or the pinned baseline. Recovery export requires
a destination outside the active data directory. No new schema revision or new
production persistent state. Tests use temporary real SQLite files only.

Decisions used (gate IDs and recorded answers): D14 exact unversioned baseline,
confirmed Alembic adoption and recoverable snapshot requirements; no new gate.

Verification commands and results: `.venv/bin/python -m pytest -q
tests/test_migration_preflight.py tests/test_existing_migrations.py
tests/test_fresh_migrations.py tests/test_migration_packaging.py
tests/test_cli_admin.py tests/test_dedicated_worker.py
tests/test_restart_controller.py` (70 passed, including installed-wheel HTTP
startup and symlink refusal);
`.venv/bin/python -m pytest -q tests/test_migration_packaging.py` (2 passed,
including isolated-wheel real HTTP startup); `.venv/bin/ruff check` on all
changed Python files (passed); `git diff --check` (passed). Full
`.venv/bin/python -m pytest -q tests`: 1,296 passed, one unrelated failure
`test_qwen_language_batches_unique_contexts_and_preserves_finding_fields`
(missing expected prompt phrase).

Known failures, limitations, unavailable checks, and recovery notes: An isolated
wheel with a staged copy of the existing built frontend passed fresh HTTP
startup; native frozen executable startup and Windows lease semantics remain
unverified on macOS. A failed
upgrade leaves its journal and snapshot; stop the app, inspect the journal,
export the verified prior generation to a separate data directory and upgrade
that copy before explicitly switching configuration. Never delete the original,
WAL files, journal or snapshot. Worker refusal leaves its queue row for later
normal recovery. No live database or running service was touched.

Remaining work in this task: None; native installed-release verification remains
a release checkpoint.

Next task ID and exact first action: CSS-07; read
[07-version-authority.md](07-version-authority.md) and confirm its dependency
handoff before unifying release and scanner version authorities. CSS-08 can
follow after CSS-07 is complete; migration authors must update supported
revisions and associated upgrade/snapshot tests before deploying new schemas.

Direct dependent tasks: [CSS-08](08-provenance-schema.md), [CSS-14](14-local-key-storage.md), [CSS-21](21-local-community-state.md), [CSS-43](43-import-repository-refactor.md), [CSS-67](67-packaging-and-user-docs.md).

