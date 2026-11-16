# CSS-18 — Activate restored state with crash recovery

Status: **DONE** (September 27, 2026). Confirmed offline activation uses a
recoverable journal and retains the prior generation. Community reconciliation
and CLI/UI flows remain CSS-19/20 work.

- Prerequisites: [CSS-02](02-maintenance-coordination.md), [CSS-05](05-adopt-upgrade-database.md), [CSS-17](17-restore-validation.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §3 restore steps 4–8.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Replacing DB/config/key files independently is not atomic. Restore also must neutralize stale process state and avoid sidecar writes or automatic rotation.

## Starting points

scanner/backup/ activation/recovery; scanner/runtime/; queue/auth services.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Use the validated plan and exclusive maintenance to journal/switch one complete generation with rollback.
2. Expire sessions; clear PIDs/PGIDs/tokens/leases, suppress automatic sharing/queue resume until reconciliation, and preserve durable community operation IDs.
3. Validate target paths/deployment binding and restored key identity; expose pending online verification without re-enrollment or rotation.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Tested pre-journal, partial replacement, journal, switch, hold, and post-commit crash boundaries; incomplete switches roll back and committed ones retain the new generation.
- [x] Exclusive maintenance drains checked-out users and dedicated worker leases, pooled SQLite handles reconnect on inode changes, old sessions are removed, and queue/worker admission remains paused until reconciliation.
- [x] Only allowlisted DB/settings/key files switch; sidecars are untouched. Target auth is retained by default and cannot be weakened even on explicit replacement. No sharing is started or revoked key revived; online key verification remains pending.

## Verification

- Fault-inject process termination around every switch/journal boundary on real files.
- Test full/settings mode, missing paths, active workers, retired keys and recovery after restart.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide activation journal protocol, explicit confirmation contract, rollback commands and reconciliation hooks to CSS-19/20/66.

## Handoff summary for the next task

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

Status / date / implementer: DONE / September 27, 2026 / GitHub Copilot.

Implemented outcome: `scanner.backup.activation.activate_restore(preview,
database_path, data_dir, *, choices, confirmed=True, timeout=10)` accepts the
live CSS-17 context, all six explicit boolean choices, and exact previewed
target file generations. It takes `DatabaseMaintenance.exclusive()`, verifies
schema and community identity, copies a recoverable target SQLite snapshot and
allowlisted private files, builds a separate new generation, merges supported
settings tables only when chosen, validates deployment JSON and authentication,
expires all sessions, removes worker PID/PGID/token fields and orphans restored
queued/running work. It checkpoints WAL and refuses unmanaged DB connections
before switching. The restored identity is never rotated or enrolled. Unknown
or already revoked key state is not promoted. Sidecars and media are not copied.

Files changed and relevant revision: `scanner/backup/activation.py`,
`scanner/backup/activation_state.py`,
`scanner/backup/restore.py`, `scanner/persistence/migrations/preflight.py`,
`scanner/db.py`, `scanner/cli.py`, `scanner/http/api_lifecycle.py`,
`scanner/http/api_queue.py`, `tests/test_backup_activation.py`,
`scanner/runtime/maintenance.py`, `tests/test_database_maintenance.py`,
`tests/test_startup_gate.py`, this brief
and the master checklist. No database or archive version changed; uncommitted.

Contracts / journal / reconciliation: Private `restore-generation/` has a
fixed marker and `before/` and `after/` trees. Synced
`restore-activation.json` version 1 records the resolved DB path, fixed member
names, random operation ID and SHA-256 inventory of the complete prior
generation before the first replacement. That inventory is also synced inside
the generation and retained after commit. Each target replacement is an
owner-private copy, atomic rename and directory sync. Before any rollback,
`recover_restore(database_path, data_dir)` verifies all prior digests and
schema and removes only operation-named partial files; unresolved journals
refuse normal DB admission, including checkouts from already-running API
processes. Server launch, CLI database preflight and API
startup call recovery before using deployment/DB state. A committed switch
writes `restore-reconciliation.json` with queue pause, online key check and
redacted path/binding review flags before removing the journal. The old and
new staged trees are retained under private `restore-recovery/` for offline
inspection/rollback; a crash after journal removal but before this rename
finishes retention on restart. Another activation refuses to overwrite either
the hold or retained generation. CSS-19 must expose offline recovery rather
than deleting an unknown journal or backup generation. The six consent fields
remain as in CSS-17; `replace_local_work` must be true for full restores,
`create_target_safety_backup` must remain true. No migration introduced.

Decisions used: CSS-17 G-BACKUP format/identity rules, CSS-02 exclusive lease,
CSS-05 exact migration support; no new policy gate selected.

Verification: `.venv/bin/python -m pytest -q
tests/test_backup_activation.py tests/test_backup_restore.py
tests/test_portable_backup.py tests/test_database_maintenance.py
tests/test_startup_gate.py tests/test_queue_scheduler.py
tests/test_existing_migrations.py tests/test_fresh_migrations.py
tests/test_cli_admin.py tests/test_cli_server.py` passed **138 tests**.
`ruff check` and `ruff format --check` passed for all 12 touched Python files;
`git diff --check` passed. Tests
cover full/settings x plain/age, WAL, worker leases and pooled connections,
admin/session safety, missing mappings, revoked key state, stale previews,
corrupt recovery and injected hard exits at switch boundaries.

Known limits/recovery: A durable completed restore intentionally keeps the
queue paused until an explicit reconciliation workflow validates paths,
deployment binding, pending community operations and online key status. CSS-19
must not remove the hold merely because the SQLite file is valid. If startup
reports an unresolved journal, stop service/workers and invoke offline
`recover_restore(database_path, data_dir)` with the actual configured paths;
it restores the complete pre-switch generation or refuses a damaged one for
manual inspection. Once committed, `restore-recovery/before/` is a private
verified snapshot for operator-guided rollback; do not replace individual
live files while the service is running. Pi 3 age unlock timing and Windows/
Linux native execution remain unverified. Concurrent uncoordinated manual
file edits are not covered by the application's maintenance lease.

Remaining work in this task: None. Next task ID and exact first action:
CSS-19; read this journal/hold contract and expose an offline confirmation,
preview and journal-recovery CLI that never clears reconciliation implicitly.

Direct dependent tasks: [CSS-19](19-backup-cli.md), [CSS-71](71-initial-setup-restore.md).
