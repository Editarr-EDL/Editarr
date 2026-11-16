# CSS-02 — Coordinate exclusive maintenance with live workers

Status: **DONE** (September 26, 2026). Exclusive maintenance is available to
later migration, snapshot and restore tasks; none of those operations is wired yet.

- Prerequisites: [CSS-00](00-repository-preflight.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §1 and §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Dedicated scan workers survive HTTP shutdown today. Migrations and restores must prevent surviving processes from reopening or writing the old database.

## Starting points

scanner/runtime/native_lifecycle.py; scanner/runtime/process_control.py; scanner/http/api_lifecycle.py; scanner/pipeline/; queue repository.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Inventory every DB user and instance-lock path; extend existing coordination rather than inventing a disconnected second lock.
2. Implement a reusable maintenance lease/state machine: stop admission, request cooperative checkpoint/finish, verify all users closed, then acquire exclusive access.
3. Define timeout/interruption recovery; an inability to establish exclusivity fails safely and does not kill unrelated processes.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Two processes cannot become maintenance owners; workers cannot start/reopen data during exclusive maintenance.
- [x] Normal reload preserves its existing behavior; maintenance is a distinct explicit operation.
- [x] Timeout/crash produces an actionable state and leaves the original data intact.

## Verification

- Use real child-process tests for competing owners, surviving workers, timeout, and owner crash.
- Run native lifecycle, queue-worker, and process-control regression tests relevant to changed paths.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Document acquire/release API, lock lifetime/order, worker handshake, and recovery states for CSS-03/06/18.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Explicit maintenance pauses new
database connections and worker starts, waits for checked-out connections and
whole worker lifetimes to drain, then provides exclusive access. A surviving
worker notices pending maintenance at its ordinary cancellation checks, leaves
its persisted queue/checkpoints for existing startup recovery, and closes its
session without attempting a new write. HTTP reload still does not request
maintenance or terminate dedicated workers.

Files changed and relevant revision: `scanner/runtime/maintenance.py`,
`scanner/db.py`, `scanner/worker.py`, `tests/test_database_maintenance.py`,
`tests/test_dedicated_worker.py`, this task brief and the master checklist;
base revision `a84aae65dd27fb325ca26cadd84bdf0a83a34324` (CSS-00 handoff).

Contracts / APIs / schemas / migrations / fixtures introduced: No schema or
public API change. Call `DatabaseMaintenance(settings.data_dir).exclusive(timeout=10)`
as a context manager in the thread performing a future snapshot, migration or
restore. Its thread may use `get_engine()` after acquisition; other threads and
processes cannot. `get_engine()` holds a shared kernel lock for each SQLAlchemy
connection checkout; `worker.run_scan_worker()` holds a second shared lease for
the full worker lifetime, even between checkouts. `DatabaseMaintenance.requested()`
is the worker's cooperative checkpoint/exit signal. The owner locks the persistent
private `maintenance/owner.lock` first, publishes `maintenance/pending`, then
waits for an exclusive `maintenance/users.lock`. Neither lock file is deleted.
Native `native-instance/instance.lock` still governs the single native launcher;
authenticated worker-control endpoints still prove process identities. The DB
barrier is shared by HTTP, CLI and dedicated workers through the common engine.

Decisions used (gate IDs and recorded answers): No CSS-02 gate. Uses the confirmed
data-preserving migration requirement; normal reload retains worker survival.

Verification commands and results: `.venv/bin/python -m pytest -q
tests/test_database_maintenance.py tests/test_dedicated_worker.py
tests/test_queue_workers.py tests/test_queue_scheduler.py tests/test_queue_credits.py
tests/test_native_lifecycle.py tests/test_process_control_units.py
tests/test_process_control_integration.py tests/test_startup_gate.py
tests/test_runtime_paths.py` passed 84 tests in 5.30s. Rechecked the 3
subprocess maintenance tests after adding explicit worker-start denial. Ruff
passed on all five changed Python files; `git diff --check` passed.

Known failures, limitations, unavailable checks, and recovery notes: No
remaining focused failures. Windows lock semantics were not exercised on this
macOS host. A timed-out lease raises `MaintenanceUnavailable`, clears pending,
and leaves the original DB untouched; the caller must not run any destructive
maintenance after that error. A crashed owner releases its kernel locks; the next
request/checkout clears the stale pending marker under the owner lock. A blocked
worker remains in the queue for startup recovery. Future operations must acquire
the lease before touching or replacing DB files and release it only after closing
all maintenance DB connections; CSS-06 must gate startup before ordinary queries.
No snapshot, migration, restore, or production CLI action was added here.

Remaining work in this task: None.

Next task ID and exact first action: CSS-03; read
[03-safety-snapshots.md](03-safety-snapshots.md), the CSS-01 pinned schema handoff,
and this lease contract, then implement consistent file-backed pre-upgrade snapshots
under `DatabaseMaintenance.exclusive()`. CSS-06 must integrate the same lease with
startup/CLI migration preflight; CSS-18 must use it for restore activation.

Direct dependent tasks: [CSS-03](03-safety-snapshots.md), [CSS-06](06-migration-startup-cli.md), [CSS-18](18-restore-activation.md).

