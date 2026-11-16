# CSS-20 — Build backup and restore settings with optional encryption

Status: **DONE** (September 27, 2026). Protected, bounded backup jobs and the
General Settings backup/restore workflow are implemented.

- Prerequisites: [CSS-19](19-backup-cli.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §3; main D06.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The user requires both backup modes and a safe transfer UI. Existing local administrator/CSRF protections must govern all privileged actions.

## Starting points

scanner/http/ backup routes; web/src/settings/ backup feature; web/src/api/; web/e2e/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Expose bounded job/status/download/preflight/confirm endpoints using the backup services; do not buffer huge archives in a request.
2. Build backup-kind/secret inventory controls and an Encrypt backup option, off by default. Show/require passphrase input only when enabled. Show the selected protection mode and that plain output includes any selected credentials/keys in the existing summary; retain progress/cancel/errors and identity/path/auth restore choices.
3. Explain copy versus move and show a backup reminder after key changes; never auto-rotate during restore.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Both modes work in narrow and wide layouts with keyboard focus and accessible errors.
- [x] Restore requires an explicit confirmation of concrete replacements; secret values are never in previews or browser persistence.
- [x] Success/download failures are distinguishable; unauthorized/CSRF requests fail.
- [x] Full/settings exports default to plain output; users can enable encryption. Restore detects protection mode and requests a passphrase only for encrypted input. No extra confirmation barrier is required merely to choose unencrypted output.

## Verification

- Run feature component/API tests, accessible browser full/settings × plain/encrypted journeys, protection-mode/password visibility and corrupt-restore paths.
- Verify a cross-directory transfer retains identity while preserving target safety choices.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide UI routes/job contracts and supported user backup path; ordinary community enrollment may be exposed only after this checkpoint.

## Handoff summary for the next task

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

Status / date / implementer: DONE / September 27, 2026 / GitHub Copilot.

Implemented outcome: Settings > General > Backup and restore exposes full or
settings/identity-only export with optional age encryption (off by default),
redacted validated inventory, native streamed download, file upload, detected
protection/passphrase, preview and five explicit replacement choices. Full
restore requires local-work consent and all restores require an affirmative
review checkbox; the target safety generation is mandatory. No signing-key
rotation is initiated. The UI explains copy/move and reminds users to back up
after key or credential changes. Error, busy, ready, cancellation and
non-cancellable activation states are visible and accessible.

Files changed and revision: `scanner/http/backup_api.py`, `scanner/api.py`,
`scanner/security/auth_policy.py`, `scanner/backup/activation.py`,
`tests/test_backup_api.py`, `tests/test_auth_policy.py`,
`web/src/api/api-backups.ts`, `web/src/settings/backup-settings.tsx`,
`web/src/settings/backup-settings.test.tsx`, `web/src/settings/settings-view.tsx`,
`web/src/styles/shell.css`, `web/src/styles/responsive.css`,
`web/e2e/backup-settings.spec.ts`, repository `README.md`,
`docs/api-contract.md`, this brief and master checklist; uncommitted.

Contracts: protected process-local `POST /api/backups/jobs` starts export or
restore; `GET` reports redacted status/validated inventory/restore plan; `PUT
/api/backups/jobs/{id}/archive` streams a bounded upload (80 GiB ceiling plus
container overhead); `POST /preflight` validates it; `POST /activate` requires
confirmed explicit choices and rechecks the target generation; `HEAD` and
`GET /download` check and stream an owner-private archive; `DELETE /{id}`
cancels non-activating work. Four jobs per process, 30-minute idle lifetime;
passphrases exist only in memory while used, never in status or browser storage.
The current authorization DB checkout closes before activation takes exclusive
maintenance. A missing secret parent no longer causes activation to sync a
nonexistent directory. No schema, archive or protocol version changed; private
temporary job archives are not portable backup inventory.

Decisions used: G-BACKUP optional age encryption, plain default, explicit
key/identity portability and no automatic rotation; no new policy choice.

Verification: `.venv/bin/python -m pytest -q tests/test_backup_api.py
tests/test_backup_cli.py tests/test_backup_activation.py tests/test_backup_restore.py
tests/test_portable_backup.py tests/test_auth_policy.py tests/test_startup_gate.py`
passed 113; after the exact-state preflight fix, focused API tests passed 7.
Ruff check passed all touched Python; Ruff format checked new backend and
activation files (existing auth-policy file has unrelated format differences).
Full `pnpm --dir web test -- src/settings/backup-settings.test.tsx` ran 20
files / 144 tests; `pnpm --dir web build` passed. Scoped
`pnpm --dir web exec playwright test e2e/backup-settings.spec.ts --workers=2`
passed 8 cases across 320px/1440px and all four modes with keyboard focus,
axe and horizontal overflow checks. File-backed HTTP tests cover both kinds,
plain and encrypted export/restore, wrong passphrase, corrupt upload,
cancelled upload, transferred signing identity, auth/CSRF and download bytes.

Known limits and recovery: A mocked Playwright download event does not prove
download completion in Chromium's Vite proxy; real HTTP FileResponse bytes,
HEAD readiness and browser error handling were separately tested. Browser
jobs are process-local: do not deploy multiple API workers for this UI; use
the CLI in such deployments. After abrupt shutdown, private `backup-ui-jobs/`
files may remain; stop the service before removing only abandoned `job-*`
stages. Activation expires sessions and keeps `restore-reconciliation.json`
until downstream review; the browser may require a fresh sign-in before it
can poll the final status. If a journal is unresolved, stop the service and
use offline `backup recover`; never clear the journal/hold by deleting files.
Pi 3 unlock timing and Windows/Linux native release smoke remain unverified.

Remaining work in this task: None. Next task ID and first action: CSS-30;
read the CSS-20 UI/job contract and CSS-28/29 enrollment handoffs before
exposing ordinary connection settings, without starting sharing on restore.

Direct dependent tasks: [CSS-30](30-connection-settings.md), [CSS-65](65-optional-scheduled-backups.md), [CSS-66](66-integration-and-abuse.md), [CSS-71](71-initial-setup-restore.md).
