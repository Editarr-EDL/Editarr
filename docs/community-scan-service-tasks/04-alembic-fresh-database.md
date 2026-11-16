# CSS-04 — Add packaged Alembic migrations for fresh databases

Status: **DONE** (September 26, 2026). Fresh-only migration chain is packaged;
existing database adoption and startup activation remain CSS-05/06 work.

- Prerequisites: [CSS-01](01-schema-fixtures.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §1.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Runtime schema creation currently calls Base.metadata.create_all and seeds through an after_create listener. Frozen migrations must not import future mutable model definitions to recreate historical schemas.

## Starting points

pyproject.toml; uv.lock; scanner/persistence/migrations/; scanner/db.py; explicit seed module.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add/lock Alembic and a baseline revision matching CSS-01, with explicit table/index/constraint creation and stable seed definitions.
2. Create a fresh-database migration entry point and move seed behavior into idempotent explicit operations.
3. Add migration assets to wheel/sdist/native packaging inputs; preserve thin public facades.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Empty database reaches the baseline with correct constraints/defaults and seeds exactly once.
- [x] Migration files are present when imported from a built package outside the checkout.
- [x] Historical revision behavior is independent of later ORM edits; existing public imports continue working.

## Verification

- Compare migration-created schema/data to CSS-01 and existing schema tests.
- Build and inspect a lightweight package fixture for migration resources; do not deploy.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide migration runner entry point, revision ID, seed API, and packaging changes to CSS-05/06.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Added a frozen Alembic baseline
that constructs all 24 CSS-01 domain tables, 3 named indexes and exactly 1
`filter_settings` singleton plus 97 ordered MCF severity defaults. Alembic adds
`alembic_version`; all other reflected schema and seeds match the pinned
unversioned specification. Future changes to ORM rows/taxonomy cannot alter
this revision: its DDL and seeds are literal packaged JSON, not model imports.

Files changed and relevant revision: `pyproject.toml`, `uv.lock`, `scanner/db.py`,
`scanner/persistence/seeds.py`, `scanner/persistence/migrations/` (runner,
environment, revision, frozen resource), `native_build/editarr.spec`,
`native_build/pyinstaller_collect.py`, `tests/community_migration_support.py`,
`tests/test_fresh_migrations.py`, `tests/test_migration_packaging.py`, this brief,
and the task index. Base revision `c55722f2a6da4a6f574aa8fe8ef1082ffe0e894f`.

Contracts / APIs / schemas / migrations / fixtures introduced: Alembic 1.20.0
locked in `uv.lock` under runtime dependency `alembic>=1.16.0,<2.0.0`.
Revision `0001_current_baseline` loads
[baseline-v1.json](../../scanner/persistence/migrations/versions/baseline-v1.json)
(27 frozen DDL statements, seed rows, CSS-01 fingerprint
`b8c923bc2fcf94e19e484581e67b10aa24108823c0736268e81535bf1bc00e3d`).
`scanner.persistence.migrations.runner.migrate_fresh_database(database_path, data_dir)`
accepts only a nonexistent file and coordinates a CSS-02 exclusive lease;
`upgrade_connection(connection)` applies packaged revisions to an already
coordinated *empty or versioned* SQLite connection, rejecting unversioned
tables pending CSS-05 verified adoption. Revision downgrade refuses destructive
removal. `scanner.persistence.seeds.seed_filter_defaults(connection)` is the
explicit idempotent current-model seed API. The `Base.metadata.after_create`
listener delegates to it for legacy callers/fixtures; CSS-06 must replace
production `init_db()`'s create-all path before automatic upgrades are enabled.

Frozen resource regeneration for *comparison*, never automatic revision rewrites:

```bash
fixture_dir=$(mktemp -d)
.venv/bin/python -m tests.community_migration_support "$fixture_dir/baseline-v1.json"
cmp scanner/persistence/migrations/versions/baseline-v1.json "$fixture_dir/baseline-v1.json"
```

Packaging: Hatch wheel includes `scanner/`; sdist includes `/scanner`;
PyInstaller spec collects the migration directory as source/data on disk and
all native targets explicitly collect Alembic. A lightweight offline wheel
and sdist built in a temporary project contained the resource/revision, and
the installed wheel ran `migrate_fresh_database` outside the checkout.

Decisions used (gate IDs and recorded answers): Confirmed Alembic and D14
(verified current schema only); no CSS-04 gate. No older schema is stamped,
reset, or automatically adopted.

Verification commands and results: `.venv/bin/python -m pytest -q
tests/test_fresh_migrations.py tests/test_migration_packaging.py
tests/test_community_schema_baseline.py tests/test_auth_schema.py
tests/test_review_schema_database.py tests/test_filter_defaults.py
tests/test_safety_snapshots.py tests/test_database_maintenance.py
tests/test_native_lifecycle.py tests/test_process_control_units.py
tests/test_runtime_paths.py` passed 130 tests. After the packaging metadata
assertion, the focused migration/packaging subset passed 9 tests. Ruff passed
on changed Python files and `git diff --check` passed.

Known failures, limitations, unavailable checks, and recovery notes: No
remaining focused failures. The wheel/sdist test uses a lightweight offline
fixture because the production build hook requires frontend assets not staged
in this checkout; an actual production release and native freeze were not run.
No existing database, credentials or media were opened or modified. SQLite DDL
is not assumed to roll back on a failed upgrade. A fresh migration error can
leave a partial *new* file; it must not be treated as ready or silently stamped.
CSS-05 must validate and snapshot the known unversioned source under one lease,
and exercise real-file failed-upgrade recovery before wiring startup in CSS-06.

Remaining work in this task: None.

Next task ID and exact first action: CSS-05; read
[05-adopt-upgrade-database.md](05-adopt-upgrade-database.md), CSS-01's schema
fingerprint, CSS-02's lease, and CSS-03's snapshot handoff. Implement exact-shape
adoption only after integrity/foreign-key checks and a validated snapshot, then
test recoverable failed upgrades on file-backed SQLite. CSS-06 wires the
approved chain into startup, workers and CLI after CSS-05 succeeds.

Direct dependent tasks: [CSS-05](05-adopt-upgrade-database.md).

