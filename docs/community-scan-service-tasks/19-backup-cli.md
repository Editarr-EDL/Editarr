# CSS-19 — Expose safe backup and restore CLI workflows

Status: **DONE** (September 27, 2026). Offline CLI export, preflight, confirmed
activation and journal recovery use the existing backup services.

- Prerequisites: [CSS-16](16-encrypted-backup-export.md), [CSS-17](17-restore-validation.md), [CSS-18](18-restore-activation.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Headless/container users need the same optional encryption and restore validation as the UI. Both full and settings-only output default to unencrypted; encrypt only when explicitly selected. CLI is allowed for backups, not as an independent contributor-key rotation trigger.

## Starting points

scanner/commands/ focused backup commands; scanner/cli.py thin dispatch.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add full/settings export, inspect/preflight and confirmed restore commands delegating to existing services.
2. Expose an explicit encryption option. Read passphrases through safe prompts/input channels only for encrypted export/restore, without command-line/log exposure; plain operations need no password. Provide useful exit codes and progress.
3. Document filesystem authority, maintenance requirements, rollback and external-secret restoration.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] CLI full/settings workflows preserve IDs/keys and enforce the same consent and failure boundaries.
- [x] Secrets are absent from output/process arguments; cancellation leaves current data intact.
- [x] No contributor rotate-key command is added.
- [x] Default exports are unencrypted; explicit encryption failures do not silently produce plain output. Both kinds and protection modes work in headless use.

## Verification

- Run subprocess CLI tests with temporary state, wrong password, confirmation cancellation and interrupted restore.
- Check help/error output and native/headless entry-point packaging.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide command examples, exit codes and recovery recipe to CSS-20/67 and users without a browser.

## Handoff summary for the next task

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

Status / date / implementer: DONE / September 27, 2026 / GitHub Copilot.

Implemented outcome: `editarr --data-dir DIR backup export DEST [--kind full|settings]
[--encrypt] [--password-stdin]`, `backup inspect ARCHIVE [--password-stdin]`,
`backup restore ARCHIVE [--password-stdin] [--replace-local-work]
[--move-community-identity] [--replace-local-auth] [--replace-connections]
[--replace-paths] [--confirm]`, and `backup recover`. Export defaults to
plain/full. Only encrypted input requests a passphrase; interactive export
confirms it, and non-interactive input reads a single line from stdin. Restore
prints redacted plan plus effective consent, then requires an interactive
`RESTORE` confirmation or explicit `--confirm`; full replacement additionally
requires `--replace-local-work`. The target safety generation is mandatory.
Recovery verifies and rolls back an incomplete switch and never clears a
committed reconciliation hold. No contributor-key rotation command exists.

Files changed and revision: `scanner/commands/backup.py`, `scanner/cli.py`,
`tests/test_backup_cli.py`, repository `README.md`, this brief and the task
index; uncommitted. No schema, archive format or API changes.

Decisions used: confirmed G-BACKUP optional age encryption, plain default,
preserved signing identity, no automatic key rotation. No new policy gate.

Verification: `.venv/bin/python -m pytest -q tests/test_backup_cli.py
tests/test_backup_activation.py tests/test_backup_restore.py
tests/test_portable_backup.py tests/test_cli_admin.py tests/test_cli_server.py`
passed 104 tests. The focused CLI suite passed 6 after the printed-consent
follow-up. `ruff check` and `ruff format --check` passed on the three touched
Python files; `git diff --check` passed. `python -m scanner.cli backup --help`
exposed all four commands; `pyproject.toml` maps the installed `editarr`
entrypoint to `scanner.cli:main`. Disposable subprocess cases cover all four
kind/protection combinations, wrong passphrase, cancellation with unchanged
target, argument-secret rejection, non-interactive encryption refusal without
`--password-stdin`, printed consent and journal crash recovery.

Exit codes and recovery: 0 success, 1 operational failure, 2 cancelled restore
or invalid CLI syntax. Stop service/workers and run `editarr --data-dir DIR
backup recover` for an interrupted activation. A committed restore retains
`restore-recovery/before/` and `restore-reconciliation.json`; never clear the
hold or overwrite individual live files to resume. Review path/deployment
binding, pending operations and online key status before reconciliation.
Restore omitted environment credentials separately. Plain backups contain
secrets, so limit filesystem access; passphrases must not be put in arguments.

Known limits: Native Windows/Linux release smoke and Pi 3 encrypted unlock
timing remain unverified, as in predecessor handoffs. CSS-20 owns the UI;
post-restore reconciliation remains a downstream workflow. This CLI does not
resume queues.
Remaining work in this task: None. Next task ID and first action: CSS-20;
read the CSS-18 journal/hold contract and this CLI handoff before wiring
the settings UI to the same export/preview/activation services.

Direct dependent tasks: [CSS-20](20-backup-settings-ui.md), [CSS-65](65-optional-scheduled-backups.md), [CSS-67](67-packaging-and-user-docs.md).
