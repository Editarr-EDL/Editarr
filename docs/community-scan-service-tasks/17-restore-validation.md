# CSS-17 — Preview and stage backups without changing active data

Status: **DONE** (September 27, 2026). Four-mode restore preflight stages and
validates backups without activating them. Pi 3 unlock timing remains unmeasured.

- Prerequisites: [CSS-16](16-encrypted-backup-export.md)
- Decision gates: [G-BACKUP](decisions.md#g-backup)
- Parent sections: implementation §3 restore steps 1–3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Restore must validate a hostile archive and show replacement effects before touching live data. Migration of restored state runs in an isolated generation.

## Starting points

scanner/backup/ readers/preflight; migration service; test archives.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Detect and validate plain versus encrypted format. Plain input needs no passphrase; encrypted input requires successful authentication/decryption with bounded KDF/resource usage. Both paths validate format, allowlisted paths, checksums, symlinks and future versions; encryption failure cannot fall back to a plain parser.
2. Stage supported database/settings/key contents and run integrity plus supported migrations in isolation.
3. Build a redacted restore plan showing identity/auth/settings/path conflicts and explicit choices; no activation in this task.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Wrong password or failed ciphertext authentication, detectable corruption, traversal, oversized members and future format leave active data unchanged. Plain checksums are not claimed to detect deliberate content-and-checksum rewriting.
- [x] Preview never returns raw secrets; source/target identities and omitted external credentials are clear.
- [x] Settings-only restore plans preserve target authentication by default; replacement choices are explicit data in the plan. No activation is implemented here.
- [x] Restore preflight supports both backup kinds in both protection modes and displays the detected mode without prompting for a password on plain input.

## Verification

- Run malicious archive fixtures and migration-on-staging tests across plain/encrypted full/settings inputs, including truncated encrypted input and no password prompt for plain input.
- Compare active file hashes before/after every failed preflight.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide validated restore-plan schema, staging lifetime/cleanup and consent fields to CSS-18/20.

## Handoff summary for the next task

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

Status / date / implementer: DONE / September 27, 2026 / GitHub Copilot.

Implemented outcome and last completed substep: `preview_restore` detects plain tar
versus age-scrypt from bytes, bounds age's work factor before decryption, checks
available staging space, validates the allowlisted archive while streaming each
member into an isolated private directory, checks SQLite integrity, foreign keys,
exact supported schema and staged migrations, validates community keys and the
manifest identity, then yields a redacted comparison plan. No target writes or
activation occur. Strict manifest metadata and duplicate field checks reject
untrusted preview text. A successful exit or handled failure removes staging;
after a hard exit, the next preflight removes marked private orphan stages.

Files changed and relevant revision: `scanner/backup/archive.py`,
`scanner/backup/restore.py`, `tests/test_portable_backup.py`,
`tests/test_backup_restore.py`, this brief and `README.md` in this task pack.
No schema or archive format revision was added; edits are uncommitted.

Contracts / APIs / schemas / migrations / fixtures introduced:
`scanner.backup.restore.preview_restore(archive_path, target_data_dir, *,
target_database=None, passphrase=None, staging_parent=None)` is a context manager
yielding `RestorePreview(stage, plan)`. `stage` holds checked files only within
the context. `plan` contains `format`, `kind`, `protection`, `source_schema`,
`staged_schema`, source/target identities (installation/key IDs only),
`omitted_environment`, `included_secret_types`, boolean source/target DB
inventories, `conflicts` (local_work, identity, local_auth, connections, paths,
external_files), and `choices` (replace_local_work, move_community_identity,
replace_local_auth, replace_connections, replace_paths default false;
create_target_safety_backup default true). Choices are *proposed consent fields*;
this task does not accept or enforce a confirmation. CSS-18 must revalidate the
stage/target generation under maintenance and require affirmative confirmation
before mutation. Settings-only replacement must retain target local auth unless
explicitly selected; its staged SQLite copy is not itself a ready-to-swap DB.
No persistent schema change; a disposable unversioned-v1 fixture proves staged
upgrade to head without touching either source or target.

Decisions used: G-BACKUP September 27 answers: shared tar/JSON, optional age
logN=18 (reject above 18), plain default, agreed member/size ceilings and
identity portability. No new policy was selected.

Verification commands and results: `.venv/bin/python -m pytest -q
tests/test_backup_restore.py tests/test_portable_backup.py
tests/test_existing_migrations.py tests/test_fresh_migrations.py` passed 69.
`.venv/bin/ruff check scanner/backup/archive.py scanner/backup/restore.py
tests/test_backup_restore.py tests/test_portable_backup.py` passed;
`ruff format --check` for the same four files passed; `git diff --check`
passed. Tests include four backup modes, hostile tar/ciphertext, high KDF,
redaction, isolated legacy migration, and abrupt-exit cleanup.

Known limitations and recovery: A checksum-correct deliberately rewritten
plain archive is not authenticated. The API is a preflight context manager,
not an activation or CLI/UI endpoint; confirmation, target safety backup,
session expiry and generation swap belong to CSS-18/19/20. On a hard exit with
no later preview, an operator may remove only marked `.editarr-restore-*`
directories under the private staging parent after ensuring no preview runs.
Pi 3 scrypt unlock time and non-macOS native release behavior remain unverified.
Disk-space checks are conservative preflight estimates, not a reservation;
disk exhaustion still aborts and removes temporary files on handled failure.

Remaining work in this task: None.
Next task ID and exact first action: CSS-18; read this plan schema and CSS-02/05
maintenance/migration handoffs, then implement an explicit confirmed activation
transaction with target safety snapshot and recoverable generation switch.

Direct dependent tasks: [CSS-18](18-restore-activation.md), [CSS-19](19-backup-cli.md).
