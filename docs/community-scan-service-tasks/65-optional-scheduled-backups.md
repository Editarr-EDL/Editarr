# CSS-65 — Add unattended backups only if scheduling is selected

Status: **DONE** locally, October 6, 2026. Approved optional local-app scheduling,
protected configuration/status, verified export reuse and owned-file retention are
implemented and tested. No real schedule was activated. Encrypted scheduling has
POSIX permission validation; Windows ACL support and native release checks remain
unavailable, with encrypted configuration failing closed on unsupported platforms.

- Prerequisites: [CSS-19](19-backup-cli.md), [CSS-20](20-backup-settings-ui.md)
- Decision gates: [G-SCHEDULING](decisions.md#g-scheduling)
- Parent sections: implementation §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Scheduled backups were explicitly left optional. Saving an export passphrase without consent is not an acceptable way to make them unattended.

## Approved scheduling policy

October 6, 2026: User selected configurable intervals in hours, one catch-up after
downtime rather than a backlog, and failure retry at the next interval. Scheduling
is disabled until explicitly configured and enabled by an administrator; no OS
wakeup or launch daemon is selected.

Require an existing writable administrator-selected destination and retain a
configurable number of successful backups, oldest scheduler-owned files first.
Prune only after a new verified success; never prune manual backups or previous
backups after a failed export. Interval and retention count are required inputs,
not inferred defaults. Preserve the existing full/settings-only content choices.

Support plain and encrypted archives. Encrypted runs read a separately provisioned
owner-only passphrase file outside backup contents on each run, failing closed if
unavailable; never persist a manual-export passphrase or return secret contents.
Tests use temporary synthetic data and credentials. Existing database changes,
real scheduling activation and Docker-service backup scheduling are not authorized.

This task covers local Editarr backups under CSS-19/20, not community Docker service
backups. The Docker pivot does not select either schedule. Any service schedule,
offline stop/start coordination, independent destination or key-custody policy needs
an explicit G-SERVICE-BACKUP decision and its own scoped work; CSS-67/68 currently
cover manual service recovery and its verification.

## Starting points

scanner/backup/ scheduler adapter; settings UI; approved secret-store integration.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. If selected, implement the approved schedule, destination, retention and explicit protection mode. Plain scheduled backups need no passphrase; encrypted schedules need the approved recipient/credential store. Do not silently save an export passphrase.
2. Reuse backup snapshot/packaging/optional-encryption/verification with overlap prevention and bounded retry; expose last success/failure. A missing encryption credential fails that encrypted run rather than downgrading it.
3. If deferred or declined, record that decision and close this optional task without adding scheduling code.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] No saved export passphrase or new recurring job appears without approved configuration.
- [x] Secret-unavailable/destination-full failures are visible and preserve prior backups.
- [x] Scheduled jobs obey maintenance/key-generation locking and do not retain excluded media/cache data.

## Verification

- Fake-clock overlap/restart/missed-run tests plus safe secret-store failure.
- Verify scheduling consent and disabled/default behavior in UI.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide chosen scheduling scope and restore-ready sample or explicit deferral record to CSS-67/69.

## Handoff summary for the next task

Status / date / implementer: DONE locally / October 6, 2026 / GitHub Copilot.

Outcome: General Settings adds a default-disabled schedule with required integer
hours (1-8760), retained count (1-1000), existing writable destination, full/settings
contents and plain/age-scrypt protection. Real administrator sessions and mutation
Origin/CSRF are required even with local/external bypass. The API accepts only a
protected credential file path, never its contents or a manual-export passphrase.

Module map: `scanner/backup/schedule_store.py` owns strict machine-local version-1
configuration/state and protected-file checks; `schedule.py` owns OS-locked attempts,
redacted status and retention; `schedule_runner.py` owns the application-lifetime
30-second runner. `scanner/http/backup_schedule_api.py` provides GET/PUT/DELETE
`/api/backups/schedule`, wired through `scanner/api.py`, lifecycle and auth policy.
The existing exporter adds opt-in no-overwrite publication and a restore-hold check
inside the existing exclusive database/key lock; manual callers retain their defaults.
Frontend API/settings/CSS stay in the matching web feature folders. Tests extend
backup/auth/settings fixtures and the existing backup browser suite. Changes are
uncommitted; no schema migration or dependency was introduced.

Durability and retention: Private atomic/fsynced `backup-schedule.json` records the
attempt and next interval before export. Cross-process locking blocks overlap and
configuration changes while running. Missed runs collapse into one catch-up; failed
or interrupted attempts wait for their next interval, with safe visible outcomes.
Shutdown drains active work. Restore journals/holds pause execution and are rechecked
under the snapshot lock. A verified success is durably recorded before oldest-first
deletion. Only recorded regular single-link files with unchanged size/hash/generation
in the owned private directory may be pruned. Manual backups and safety generations
are never included. No cleanup cursor is needed: the bounded ordered inventory is
the resumable deletion journal; it holds at most 1001 records and refuses additional
exports if retained inventory requires review rather than dropping ownership facts.

Recovery: Changing destinations preserves the previous directory. A crash between
publication and outcome recording can leave an untracked archive; retain and inspect
it manually, never report it as a verified success or delete it by filename pattern.
Changed owned files also require review. Missing destination/credential or failed
export never prunes prior files. An unavailable state file fails closed. The schedule,
inventory, lock and external passphrase are excluded from both portable backup kinds;
new installations require explicit configuration. Existing-target restore holds
pause its independently configured schedule. No automatic key rotation is added.

Decisions: G-SCHEDULING uses the explicit October 6 answers above. CSS-64 remains
BLOCKED by user deferral, with G-RETENTION still open; none of these local settings
approve community-service retention, Docker scheduling or public activation.

Verification:
- `.venv/bin/python -m pytest -q tests/test_backup_schedule.py
  tests/test_portable_backup.py tests/test_backup_api.py tests/test_backup_cli.py
  tests/test_backup_restore.py tests/test_backup_activation.py
  tests/test_database_maintenance.py tests/test_community_rotation_backup.py
  tests/test_auth_policy.py tests/test_startup_gate.py`: **205 passed**.
- The scheduler's 27 cases cover fake-clock boundaries/catch-up/retry, overlap,
  real child-process exit/restart, restore races, no-overwrite publication, altered
  retention files, missing credentials, API consent and four real full/settings x
  plain/encrypted archives with successful restore previews and excluded state.
- `pnpm --dir web exec vitest run --maxWorkers=1`: **297 passed, 43 files**;
  `pnpm --dir web build`: passed, existing >500 kB chunk warning remains (545.43 kB).
- `pnpm --dir web exec playwright test e2e/backup-settings.spec.ts --workers=2`:
  **12 passed**, 320/1440px, both protection modes, manual regression flows,
  keyboard/focus, zero accessibility violations and no horizontal overflow. Viewed
  final screenshots and added checkbox geometry checks after correcting inherited
  full-width input styling.
- Changed Python Ruff checks and new/changed backup module formatting passed;
  editor diagnostics, documentation relative links/fences and diff whitespace passed.

Limits: No live activation, actual credential, existing-database write/migration,
media/model operation, Docker-service change or backend restart. Full unrelated
Python, native packaging, Windows/Linux execution and off-device recovery were not
run. POSIX protected-file checks refuse unsupported Windows ACL validation, not
silently weaken it. The local preview is frontend-only at `http://127.0.0.1:5186/`.

Next task: CSS-66. Read its current Docker/Node integration brief and CSS-25
remaining audits, then extend the real Node HTTP/SQLite API matrix using disposable
fixtures. Do not reopen deferred CSS-64 or infer live provider/ingress activation.

Direct dependent tasks: the user-approved rollout or remaining backlog.
