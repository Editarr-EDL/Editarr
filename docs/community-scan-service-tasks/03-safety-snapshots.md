# CSS-03 — Create recoverable pre-upgrade database snapshots

Status: **DONE** (September 26, 2026). Local migration safety snapshots are
implemented; portable backups with optional encryption remain downstream work.

- Prerequisites: [CSS-01](01-schema-fixtures.md), [CSS-02](02-maintenance-coordination.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §1 and §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

A live SQLite main-file copy misses WAL state. This task supplies local migration safety snapshots, distinct from portable exports (encryption optional and off by default under updated D06). Historical handoff references to encrypted portable backups below predate that policy update; the implemented local snapshots are unchanged.

## Starting points

new scanner/backup/snapshot modules; scanner/runtime/runtime_paths.py; tests/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Use SQLite online backup into a private temporary generation under the maintenance/snapshot contract.
2. Validate integrity and foreign keys, write a versioned recovery manifest/checksums, flush and publish atomically.
3. Implement recovery selection and cleanup of incomplete generations without overwriting the active database; leave retention configurable pending its gate.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Snapshot includes committed WAL data and matches the recorded generation.
- [x] Disk-full/interruption/invalid source never replaces the original or advertises a valid snapshot.
- [x] Snapshots use owner-only permissions and exclude secret contents from logs.

## Verification

- Exercise file-backed WAL/concurrent writer, interruption, corruption, and disk-error fixtures.
- Verify a snapshot can be opened independently and its data matches the baseline fixture.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide snapshot/validation API, recovery manifest layout, and failure guarantees to CSS-05/16/18.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Added local, unencrypted,
owner-private migration snapshots in [snapshots.py](../../scanner/backup/snapshots.py).
SQLite's online backup includes committed WAL rows, omits uncommitted writes,
and converts the copy to a self-contained DELETE-journal database. Integrity
and foreign keys are checked before publication. The database and manifest
are flushed, then their private staging directory is atomically renamed and
the parent directory synced. Neither failure nor recovery selection writes to
the active database.

Files changed and relevant revision: `scanner/backup/__init__.py`,
`scanner/backup/snapshots.py`, `scanner/runtime/maintenance.py`,
`tests/test_safety_snapshots.py`, this brief and the master checklist; based on
the CSS-01/02 handoffs. No application schema, migration or live data changed.

Contracts / APIs / schemas / migrations / fixtures introduced:
`create_safety_snapshot(database_path, data_dir, timeout=10)` returns a published
generation path. It acquires `DatabaseMaintenance(data_dir).exclusive()` unless
the calling thread already holds that exclusive lease; CSS-05 can therefore
snapshot and migrate under one continuous lease. `validate_safety_snapshot(path)`
checks a published generation's manifest, owner-only permissions, byte count,
SHA-256, SQLite integrity and foreign keys. `select_recovery_snapshot(data_dir)`
returns the newest verified path or `None`; it raises `SnapshotError` on any
damaged published generation instead of silently selecting an older one.
`cleanup_incomplete_snapshots(data_dir)` removes only `.pending-*` directories
under an exclusive lease, leaving published generations untouched.

Layout: `<data_dir>/migration-snapshots/snapshot-<random-generation>/` contains
`snapshot.sqlite3` and `manifest.json`. Manifest version 1 records
`format_version`, `generation`, UTC `created_at`, literal database filename,
byte `size`, and SHA-256 `sha256`. No path, credentials, contents or passphrase
is recorded in the manifest or logged. On POSIX, root and generation directories
must be owner-only, and both files are created mode 0600. Each stage is a
private `.pending-*` sibling. Snapshot generations are intentionally retained;
no automatic retention count or deletion policy is assumed before its decision.

Decisions used (gate IDs and recorded answers): Data-preserving migration and
recoverable backup requirements; no CSS-03 policy gate. These plaintext safety
snapshots are local and distinct from CSS-16's portable encrypted export.

Verification commands and results: `.venv/bin/python -m pytest -q
tests/test_safety_snapshots.py tests/test_database_maintenance.py
tests/test_community_schema_baseline.py tests/test_auth_schema.py
tests/test_review_schema_database.py tests/test_dedicated_worker.py
tests/test_queue_workers.py tests/test_queue_scheduler.py tests/test_runtime_paths.py`
passed 101 tests. `.venv/bin/ruff check scanner/backup
scanner/runtime/maintenance.py tests/test_safety_snapshots.py` and
`git diff --check` passed. Tests exercise WAL, an uncommitted writer, the pinned
CSS-01 fixture and identifiers, invalid/foreign-key-broken sources, simulated
disk exhaustion before/after publication, interruption, tampered published
bytes, owner-only permissions, incomplete cleanup, and maintenance timeouts.

Known failures, limitations, unavailable checks, and recovery notes: No focused
failures remain. Windows ACL and directory-flush behavior were not verified on
this macOS host. Simulated failures and interruption use fixture injection;
there was no real disk-full event or production database. After an abrupt crash,
the next lease holder may call `cleanup_incomplete_snapshots`; validated published
generations remain available. The snapshot API does not select a supported
schema or perform an upgrade, restore, automatic cleanup, or encrypted export.
Callers must retain the exclusive lease across snapshot and migration and stop
on failed validation or unknown schema. Do not silently discard a damaged
published generation or auto-activate a snapshot.

Remaining work in this task: None.

Next task ID and exact first action: CSS-05 reads
[05-adopt-upgrade-database.md](05-adopt-upgrade-database.md), the CSS-01 pinned
schema and CSS-02 lease handoffs, then calls `create_safety_snapshot` within
one exclusive upgrade lease before adoption/migration. CSS-16 reads
[16-encrypted-backup-export.md](16-encrypted-backup-export.md) and this manifest
contract before implementing portable exports in both protection modes. CSS-18 reads
[18-restore-activation.md](18-restore-activation.md) before using the lease and
validated recovery selection for a recoverable restore.

Direct dependent tasks: [CSS-05](05-adopt-upgrade-database.md), [CSS-15](15-backup-format-evaluation.md), [CSS-16](16-encrypted-backup-export.md).
