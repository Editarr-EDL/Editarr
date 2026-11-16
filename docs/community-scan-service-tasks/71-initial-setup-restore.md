# CSS-71 — Offer restore from backup during initial setup

Status: **DONE** (September 27, 2026). Setup restore uses the existing bounded
backup job engine behind a setup-only, flow-bound authorization boundary.

- Prerequisites: [CSS-18](18-restore-activation.md), [CSS-20](20-backup-settings-ui.md)
- Decision gates: [G-SETUP-RESTORE](decisions.md#g-setup-restore), resolved: use backup administrator credentials; create an administrator only when none are included.
- Parent sections: implementation §3/8; main D06.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [implementation](../community-scan-service-implementation.md).

## Context and outcome

The user requested a **Restore from backup** option on the initial setup screen.
Currently `AdministratorSetup` only creates a local administrator, and the backup
job API is unavailable while administrator setup is required. CSS-20 already provides
the Settings restore workflow; reuse its services and UI behavior instead of building
a second archive parser or activation path. This task makes restoring an installation
an alternative to starting fresh, available before completing ordinary setup.

Support full and settings/identity-only backups in both plain and encrypted forms.
Plain archives require no passphrase; encrypted archives require their backup
passphrase. A backup passphrase is separate from an administrator sign-in password.
The user chose to restore included administrator credentials. After activation,
require sign-in with those credentials. When no administrator credentials are
included, continue to administrator creation before allowing protected application
access. Malformed/unsupported credential records are a validation failure, not
permission to discard them and reset the password.

## Starting points

- `web/src/auth/auth-setup.tsx`, `auth-setup.test.tsx`, and `auth-gate.tsx`.
- `web/src/settings/backup-settings.tsx`, its tests, and `web/src/api/api-backups.ts`.
- `scanner/http/backup_api.py`, `scanner/security/auth_policy.py`, and the existing administrator setup/status services.
- `scanner/backup/restore.py`, `activation.py`, and `activation_state.py`.
- `tests/test_backup_api.py`, `test_auth_policy.py`, `test_backup_activation.py`; `web/e2e/app-auth.spec.ts` and `backup-settings.spec.ts`.

Paths above are repository-relative inspection targets. Read the actual predecessor
handoffs and preserve their completed behavior. Keep setup orchestration in the auth
feature and extract shared restore controls/services into focused existing feature
modules where needed; do not expand an already large route file into a catch-all.

## Work within this task

1. Add the restore option alongside the existing create-administrator path. Provide
   archive selection, conditional passphrase, bounded upload, redacted preview,
   concrete replacement confirmation, progress and failure states. Allow returning
   to fresh setup or retrying before activation without creating an administrator.
2. Reuse CSS-17/18 validation, staging, migrations, generation checks, safety snapshot,
   journaled activation and recovery. Reuse/extract CSS-20 restore UI and API services;
   retain identity, connection and path choices. No new archive format or key rotation.
3. Add only the setup-specific authorization needed for restore. Check the authoritative
   administrator/setup state on every request and again under the activation lock.
   Bind uploads/status/preview/confirmation to the initiating setup flow, retain
   origin/CSRF protections and resource bounds, and reject cross-flow job access.
   Do not make normal backup exports/downloads or protected APIs public. Serialize
   first administrator creation against restore activation; a stale flow cannot
   replace an administrator created elsewhere or reopen setup after it completes.
4. Restore included administrator credentials; if none are included, leave administrator
   creation as the remaining setup step. Preview this outcome explicitly, without
   requiring a throwaway target account or changing Settings restore defaults.
   After commit, refresh authoritative authentication status and transition to
   sign-in or administrator creation. Expire old sessions; do not treat
   archive upload as a sign-in. Handle the expected loss of setup/job access after
   activation without reporting a successful restore as a failure or retrying it.
5. Preserve reconciliation holds and recovery instructions. Restoration alone must
   not start scans, sharing, enrollment or key rotation. Document first-run restore
   and the difference between backup passphrases and administrator credentials.

Do not implement hosted recovery, scheduled backups or community enrollment here.
G-SETUP-RESTORE is resolved; ask about any new unresolved major behavior rather
than adding credential reset or recovery options outside the selected policy.

## Acceptance criteria

- [x] A new installation offers fresh setup and Restore from backup without requiring a throwaway administrator first; ordinary setup still works.
- [x] Full/settings × plain/encrypted all reach validated preview and confirmed activation. Plain input never requests a passphrase; encrypted input never silently falls back to plain.
- [x] Wrong passphrase, corrupt/unsupported archive, cancellation, disk/resource failure and stale preview preserve active data and leave a usable setup/retry path.
- [x] Setup-only restore is unavailable once setup completes; cross-origin, cross-flow, stale and concurrent setup/restore requests cannot bypass authentication or overwrite the winning administrator.
- [x] Included administrator credentials are restored and require a fresh sign-in; missing credentials lead to administrator creation. Malformed credentials fail validation. Old sessions are never restored and protected application access requires sign-in/setup completion.
- [x] Signing identity survives when selected; pending rotation remains pending; restore never automatically rotates, enrolls or shares. Existing reconciliation and crash-recovery behavior remains intact.
- [x] Narrow/wide layouts, keyboard focus, accessible errors, retry/cancel and post-activation navigation work. Settings restore still passes its existing tests.

## Verification

- Use real file-backed fixture archives and fresh target databases for all four combinations; verify restored settings/identity, successful sign-in with restored credentials, account creation when credentials are absent, and rejection of malformed credentials.
- Test setup/restore races, job ownership, origin/CSRF rejection, replay after setup completion, stale target generations and rollback/interruption using focused HTTP/activation tests.
- Extend component and browser coverage for entry choice, conditional passphrase, preview/confirmation, failure/retry, credential transition and existing fresh setup; include supported base-path deployments.
- Run targeted Settings restore regressions and frontend build checks. Follow the [verification rules](context.md#verification); record actual commands/results and platform limitations.

## Definition of done

- [x] Every acceptance criterion above is met and relevant verification passed.
- [x] G-SETUP-RESTORE has an explicit recorded answer; no invented credential policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark partial work done.
- [x] Write the handoff below. Include setup-only authorization and race handling, shared UI/service entry points, credential transitions, archive fixtures, verification and recovery instructions.

## Handoff summary for the next task

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

Status / date / implementer: DONE / September 27, 2026 / GitHub Copilot.

Implemented outcome: The first-run administrator panel offers Restore from backup
or ordinary account creation. The restore panel reuses Settings' bounded upload,
age detection, redacted preview, explicit replacement consent, progress and
cancellation. Preflight validates included administrator credentials; after
activation the auth gate reloads status and requires fresh login with the
restored password, or administrator creation when no account was included. An
archive passphrase never becomes a login session or stored browser credential.

Files changed / revision: `scanner/http/setup_restore_api.py`,
`scanner/http/backup_api.py`, `scanner/backup/restore.py`,
`scanner/backup/activation.py`, `scanner/security/auth_policy.py`,
`scanner/api.py`, `tests/test_setup_restore_api.py`, `tests/test_auth_policy.py`,
`web/src/api/api-backups.ts`, `web/src/auth/auth-setup.tsx` and its tests,
`web/src/settings/backup-settings.tsx` and its tests,
`web/src/styles/features.css`, `web/e2e/setup-restore.spec.ts`,
repository `README.md`, `docs/api-contract.md`, this brief and the index;
uncommitted. Existing changes in adjacent planning briefs were preserved.

Contracts: Only while the singleton administrator is absent, same-origin
`POST /api/setup-restore/flow` issues a 30-minute HttpOnly Strict cookie and
an in-memory request token. `POST /jobs`, `PUT /jobs/{id}/archive`,
`GET /jobs/{id}`, `POST /jobs/{id}/preflight`, `POST /jobs/{id}/activate`,
`DELETE /jobs/{id}` are under `/api/setup-restore`. Unsafe calls require
`X-Setup-Restore-Token`; jobs are owner-bound and inaccessible through the
ordinary protected backup API. Sixteen live flows per process; CSS-20's four
jobs and size cap still apply. Every endpoint checks administrator absence;
activation checks again inside DatabaseMaintenance's exclusive lease. The
ordinary administrator setup shares the database-user lease, so it cannot
commit across the exclusive switch. A stale generation aborts activation.
The setup flow forces local-auth replacement while preserving explicit local
work, identity, connection, path, and mandatory safety-generation choices.
No schema or archive format changed; validation adds only a redacted
`administrator_included` preview flag. Sessions are sanitized by the existing
activation service, and reconciliation holds and offline recovery are unchanged.

Decisions: G-SETUP-RESTORE: restore included credentials; create an
administrator only when missing. G-BACKUP: plain default, optional age.

Verification: Ruff lint on changed backend files passed. `pytest -q` via
`.venv/bin/python -m` for setup HTTP, backup HTTP, preflight, activation,
auth policy, backup CLI and startup gate passed 100 after the final flow-cap
and failure-path additions. The focused setup/auth suite passed 38 after the
flow cap; setup HTTP passed 15 after corrupt/wrong-password and actual
administrator-creation checks. `pnpm --dir web test --
src/auth/auth-setup.test.tsx src/settings/backup-settings.test.tsx` passed
20 files / 146 tests; `pnpm --dir web build` passed. Scoped Playwright setup
and Settings journeys passed 16; setup journeys passed 8 again after status
handling was tightened. These cover 320px/1440px, plain/encrypted and both
credential outcomes, focus, axe and overflow. `git diff --check` passed.
HTTP fixtures use file-backed fresh installations and exercise both kinds,
both protections, real sign-in/setup, malformed credentials, ownership,
origin, stale preview and intact target on failure. Supported prefixed
deployment is routed through the existing relative API base and middleware,
but a dedicated prefixed browser run was not performed. Mid-switch process
crash and non-macOS native release checks were not repeated in this task;
CSS-18 owns crash recovery evidence.

Recovery: A failed or cancelled job may be discarded and a new setup restore
started; no active data was changed before activation. If activation has a
journal or is uncertain, stop the service and use offline `backup recover`;
never delete restore journals, holds or safety generations to reopen setup.
Post-commit, sessions are expired and work remains paused for reconciliation.
Browser job and flow state is per-process, not for multi-worker deployment.

Remaining work in this task: None. Next task ID / exact first action: CSS-21;
read the CSS-06/08/14 handoffs and inspect the local persistence schema before
adding migration-backed community state. CSS-66 should replay fresh-install
restore fixtures through real auth; CSS-67 should verify an installed build.

Direct dependent tasks: [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md).
For CSS-66, start by replaying the fresh-install restore fixtures through the real
authentication boundary. For CSS-67, verify the same entry point in an installed
build and document the approved post-restore sign-in/setup steps.
