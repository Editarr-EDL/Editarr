# CSS-05 — Adopt known databases and recover failed upgrades

Status: **DONE** (September 26, 2026). Exact CSS-01 adoption and explicit recovery
are implemented; startup and CLI activation remain CSS-06 work.

- Prerequisites: [CSS-03](03-safety-snapshots.md), [CSS-04](04-alembic-fresh-database.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §1.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Adoption must preserve existing data, reject unsupported older shapes, and recover after partial SQLite DDL failures. Blindly stamping head is forbidden.

## Starting points

scanner/persistence/migrations/; schema inspection/upgrade services; tests/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement exact known-baseline adoption only after integrity validation and successful safety snapshot.
2. Apply supported revisions under exclusive maintenance; reject future versions and unknown unversioned shapes with diagnostics.
3. Persist upgrade intent/result and expose deliberate recovery from the prior generation after interrupted or failed DDL.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] IDs, reviews, findings, selections, settings, credentials, and associations survive known-baseline adoption.
- [x] Unknown/future/corrupt database remains unmodified; no automatic downgrade/reset.
- [x] Injected failure after a table rewrite has a tested recoverable original; reruns do not double-apply seeds or revisions.

## Verification

- Upgrade every supported fixture and compare final shape/data with fresh creation.
- Fault-inject before/after stamping and SQLite table recreation; test two competing upgrades.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide supported revisions, adoption diagnostics, backup-before-write evidence, and recovery instructions to CSS-06 and every later schema task.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Read-only SQLite integrity and
foreign-key preflight rejects empty, corrupt, future, unknown and altered schemas.
The full CSS-01 table/constraint/index/required-seed specification must match a
reconstructed, packaged frozen baseline, including its pinned fingerprint.
Under an exclusive CSS-02 lease, a CSS-03 snapshot is created and validated
before recording a durable intent and stamping `0001_current_baseline`. The
upgrade records `complete` or `failed`; an unresolved intent/failure blocks
reruns even if SQLite retained some DDL. A versioned current baseline is
validated and accepted idempotently. No legacy older revision is supported.

Files changed and relevant revision: `scanner/persistence/migrations/schema.py`,
`scanner/persistence/migrations/adoption.py`, `tests/test_existing_migrations.py`,
this brief and the task index. Current packaged head: `0001_current_baseline`.
No new schema revision or user data fixture was added.

Contracts / APIs / schemas / migrations / fixtures introduced:
`scanner.persistence.migrations.adoption.upgrade_existing_database(database_path,
data_dir, timeout=10.0)` returns the prior snapshot generation for a successful
adoption, `None` for an already-versioned current database. It is explicit,
not wired to startup. `export_upgrade_recovery(database_path, data_dir,
destination)` exports the journal's validated pre-upgrade snapshot to a *new*
file under an exclusive lease, refusing an existing destination and never
replacing the active database. `migration-upgrade.json` in the data directory
holds the absolute database and generation paths, source/target revisions,
timestamp, and `intent`/`failed`/`complete` status. Intent is atomically
written and synced before database writes. The snapshot remains separate
and immutable. The CSS-01 synthetic file fixture supplies relational data
for test preservation and WAL/recovery checks.

Decisions used (gate IDs and recorded answers): D14 exact current unversioned
schema only; no unapproved older-shape conversion, downgrade or reset.

Verification commands and results: `.venv/bin/pytest -q
tests/test_existing_migrations.py tests/test_fresh_migrations.py
tests/test_safety_snapshots.py` passed 35 tests, including failures before/after
stamp and after table DDL, unknown/future/corrupt input, concurrent upgrades,
committed WAL content, idempotence and a separate-directory recovery upgrade.
`.venv/bin/ruff check scanner/persistence/migrations/schema.py
scanner/persistence/migrations/adoption.py tests/test_existing_migrations.py`
passed; `git diff --check` passed before this handoff.

Known failures, limitations, unavailable checks, and recovery notes: No
production database was opened. For a failed or interrupted migration, stop
the application and inspect `migration-upgrade.json`, then export using
`export_upgrade_recovery(original_path, original_data_dir, new_path)` where
`new_path` is in a **new data directory**. Validate and migrate that exported
copy using `upgrade_existing_database(new_path, new_data_dir)`; only after
operator inspection change the application configuration to use that new
database/data directory. Do not delete the old active file, its WAL files,
the journal, or the snapshot. A complete journal can also export its prior
generation. No in-place replacement or automatic resolution of failed intent
is provided; CSS-06 must expose a deliberate CLI recovery workflow and must
block normal startup when an unresolved journal exists. No post-baseline
revision exists yet; later revisions must expand the supported versioned
revision set and its upgrade tests before they can run.

Remaining work in this task: None.

Next task ID and exact first action: CSS-06; read
[06-migration-startup-cli.md](06-migration-startup-cli.md), then wire explicit
preflight/migration and unresolved-journal refusal into startup/worker/CLI
before any ordinary database queries, preserving the exclusive lease and
exposing recovery export as an operator command.

Direct dependent tasks: [CSS-06](06-migration-startup-cli.md), [CSS-18](18-restore-activation.md).

