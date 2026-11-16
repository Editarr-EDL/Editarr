# CSS-15 — Evaluate the backup archive and optional encryption

Status: **DONE** (September 27, 2026). The user approved the
shared archive/optional age recommendation after making encryption off by default,
and selected age's default logN=18 for writes and maximum restore factor. The
restore target is a Raspberry Pi 3 (1 GB RAM); 10-second passphrase unlock is a
design goal, not verified on Pi hardware. Local snapshot retention is
3 generations/3 days/5 GiB.
[The four-mode synthetic evaluation](decision-evidence/15-backup-container.md)
records both paths. G-BACKUP's initial archive ceilings are approved; production
export/restore integration and native/Pi checks belong to CSS-16/17 and release verification.

- Prerequisites: [CSS-03](03-safety-snapshots.md)
- Decision gates: [G-BACKUP](decisions.md#g-backup) for production format selection.
- Parent sections: implementation §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Full and settings-only portable backups support both unencrypted and encrypted output, with encryption off by default. A reviewed container and bounded passphrase KDF apply to the optional encrypted path; a custom encryption protocol is outside scope. Plain backups must not require encryption credentials or an encryption-only executable.

## Starting points

new scanner/backup/ prototype; backup fixtures; decision-evidence/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Evaluate the base archive/manifest for the default unencrypted path, and maintained optional encryption container/library choices, including dependency size and native packaging support.
2. Prove streaming read/write and bounded parsing/checksums for plain output. For encrypted output, also prove authenticated manifest/contents, minimal outer metadata, random salt and bounded restore KDF settings. Existing age measurements remain candidate evidence only.
3. Compare large-data memory use and platform support for both paths; submit remaining format/protection/retention questions under G-BACKUP. Optional encryption must not block ordinary backup use with an encryption-only dependency or passphrase.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Report includes threat limits, corruption behavior, password handling, cleanup and resource measurements.
- [x] The existing experiment adopts no invented encryption scheme, production format, saved export password or unattended backup policy.
- [x] Prototype fixtures use dummy secrets and cannot be mistaken for a supported release format.
- [x] Extend the report to cover unencrypted output and the four full/settings × plain/encrypted combinations; document plain-mode checksum limits and encryption-only dependencies.

## Verification

- Exercise wrong password, altered/truncated data, unreasonable KDF parameters and large synthetic input.
- Record reviewed format references and open questions for the user.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, including the new optional-encryption scope, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for this evaluation task; native/Pi integration remains unverified.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide chosen-format recommendation and prototype evidence for G-BACKUP; CSS-16/17 proceed after approval.

## Handoff summary for the next task

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

```text
Status / date / implementer: DONE / 2026-09-27 / Copilot
Implemented outcome and last completed substep: compared uncompressed tar/JSON, age v1, 7z and ZIP AES; shared synthetic writer/reader proved plain and encrypted full/settings modes; Mac scrypt cost curve and Pi 3 limitations in decision-evidence/15-backup-container.md.
Files changed and relevant revision: scripts/backup_age_probe.exp, scripts/backup_age_evaluation.py, scripts/backup_scrypt_benchmark.py, decision-evidence/15-backup-container.md, this brief, decisions.md, README.md and ../community-scan-service-implementation.md; uncommitted workspace with other agents' edits preserved.
Contracts / APIs / schemas / migrations / fixtures introduced: None; dummy experiment only, no application entry point or release dependency.
Decisions used (gate IDs and recorded answers): D06 defaults encryption off; G-BACKUP approves a shared bounded tar/JSON archive and optional age v1, logN=18 for writes and maximum restore factor, and initial restore ceilings of 256 files, 1 MiB manifest, 256-byte UTF-8 paths, 64 GiB SQLite member, 64 MiB per other member and 80 GiB total. Raspberry Pi 3 (1 GB RAM) is the restore target with an unverified 10-second unlock goal; pre-upgrade snapshots retain 3 generations/3 days/5 GiB subject to last verified recovery protection.
Verification commands and results: .venv/bin/python scripts/backup_age_evaluation.py with --plain/without and --kind full/settings passed four synthetic round trips, checksum/authentication and failure cases. PATH=/usr/bin:/bin /usr/bin/time -l .venv/bin/python scripts/backup_age_evaluation.py --plain --kind full passed without age; plain max RSS 37,683,200 bytes, encrypted full max RSS 347,652,096 bytes. /usr/bin/time -l .venv/bin/python scripts/backup_scrypt_benchmark.py <factor> for factors 14-18 measured the host-only scrypt cost curve in the evidence. .venv/bin/ruff check scripts/backup_age_evaluation.py scripts/backup_scrypt_benchmark.py, .venv/bin/ruff format --check scripts/backup_scrypt_benchmark.py and git diff --check passed (style commands also run separately; the approval-only edit validated with git diff --check).
Known failures, limitations, unavailable checks, and recovery notes: plain archive is readable and its checksums are not authentication. Probe temporarily writes plaintext tar for encrypted restore; no safe production parser, native Windows/Linux build, minimum-hardware KDF measurement or atomic restore. Both modes use temporary synthetic files; the dummy public passphrase is never for real data.
Remaining work in this task: None. Native Pi packaging, production parsing and a safe passphrase API must be verified in CSS-16/17 or release testing; no physical Pi is available for a real timing or memory guarantee at factor 18.
Next task ID and exact first action: CSS-16; implement the same selected full/settings identity inventory in plain default and optional authenticated age exports, enforcing the approved limits and atomic publication.
```

Direct dependent tasks: [CSS-16](16-encrypted-backup-export.md).
