# CSS-16 — Export full and settings-only backups with optional encryption

Status: **DONE** (September 27, 2026). Plain and opt-in age full/settings
exports round-trip with bounded validation. Native builds require and bundle a
pinned age v1.3.2 sidecar. A frozen macOS arm64 fake-model build passed; Windows,
Linux and Pi 3 hardware builds remain unrun. Restore activation belongs to CSS-17/18.

- Prerequisites: [CSS-03](03-safety-snapshots.md), [CSS-08](08-provenance-schema.md), [CSS-14](14-local-key-storage.md), [CSS-15](15-backup-format-evaluation.md)
- Decision gates: [G-BACKUP](decisions.md#g-backup)
- Parent sections: implementation §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

A complete backup spans SQLite plus deployment settings and secret files. Settings-only exports exclude scans and pending operations but include portable identity. Both kinds support unencrypted output by default and explicit encrypted output; either preserves the selected secrets and keys.

## Starting points

scanner/backup/; scanner/settings/; scanner/security/; backup manifest fixtures.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement explicit allowlisted content inventories for both modes and environment-supplied secret omissions.
2. Coordinate SQLite snapshot and external settings/key generation, package to a temporary private destination with explicit protection-mode metadata, verify and atomically publish. Encrypt only when requested and require a passphrase only then; never silently downgrade a failed encryption request.
3. Record versions/checksums/sizes/portability flags and safe backup status; cleanup temporary plaintext on all exits.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Both modes round-trip every selected secret and distinguish included versus externally omitted identity material.
- [x] Models/media/caches/logs/transcripts/sidecars are excluded as specified; no blanket environment export.
- [x] Concurrent settings change or rotation cannot produce a mixed-generation backup; disk-full leaves prior archives intact.
- [x] Full/settings × plain/encrypted all round-trip the same selected credentials/identity; plain mode works without passphrases or encryption-only tooling.
- [x] Encrypted mode authenticates manifest/contents; plain mode retains checksums and bounded validation without claiming those checksums prove authorship or resist deliberate rewriting.

## Verification

- Round-trip all four kind/protection combinations. Secret canaries are absent from logs/filenames and encrypted outer metadata; selected dummy secrets remain recoverable from plain archives as intended.
- Test WAL snapshot, interruption, concurrent generation changes and external credential inventory.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide archive writer/manifest APIs, size limits, inventory and failure behavior to CSS-17/19/20.

## Handoff summary for the next task

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

Status / date / implementer: DONE / September 27, 2026 / GitHub Copilot.

Implemented outcome and last completed substep: `scanner.backup.archive.export_backup`
publishes plain or optional age-encrypted full/settings archives from an SQLite online backup under
`DatabaseMaintenance.exclusive()`. Settings mode clears non-allowlisted tables
from a copy and VACUUMs it. It captures allowlisted private files and validated
community identity, checks external file generation before publication, then
checksums and validates the staged archive before atomic replacement. The
streaming plain reader rejects unexpected paths/types, checksum changes,
oversized members, truncated tar and trailing bytes. Native releases build a
pinned age v1.3.2 Go sidecar; source/wheel installs retain the fixed Expect
bridge on POSIX. Both receive the passphrase over stdin, never argv or
environment; ciphertext is decrypted and checked against the staged tar before
publication. The sidecar uses logN=18 for writes and caps reads at 18; the
writer also checks the selected scrypt header before publication.
Both output modes publish owner-only files. Missing encryption tools fail closed;
plain mode does not require either executable. Deployment, diagnostics,
community-identity and local CSRF key readers/writers now join the same
cross-process maintenance barrier, so normal external updates cannot interleave
with the database snapshot or external inventory capture. A private, serialized
output stage is cleaned on normal errors; after hard termination, the next
export removes only marked orphan stages before capturing new plaintext. Write
or rename failure preserves an existing archive; if directory sync fails after
replacement, `BackupPublicationUncertain` reports uncertain durability.

Files changed and relevant revision: `scanner/backup/archive.py`,
`scanner/backup/inventory.py`, `scanner/backup/age_transport.py`,
`scanner/backup/age_passphrase.exp`, `tests/test_portable_backup.py`,
`scanner/security/community_identity.py`, `scanner/security/auth_crypto.py`,
`scanner/settings/deployment_config.py`, `scanner/settings/diagnostics.py`,
`tests/test_auth_crypto.py`, `scripts/hatch_build.py`, `scripts/build_release.py`,
`tests/test_native_runtime_build.py`, `native_build/editarr.spec`,
`native_build/backup_age/{go.mod,go.sum,main.go}`, release documentation,
this brief and the task index. No migrations or public route were introduced;
current follow-up edits are uncommitted.

Contracts / APIs / schemas / migrations / fixtures introduced: Draft
`editarr-backup-1` tar/JSON manifest, `export_backup(database_path, data_dir,
destination, kind="full", passphrase=None)`, `validate_plain_archive(path,
protection="plain")` and `run_age(source, target, passphrase, decrypt=False)`.
Archive ceilings: 256 members, 1 MiB manifest, 256-byte names, 64 GiB database,
64 MiB other files, 80 GiB aggregate. These draft APIs are **not** yet a
supported restore format; CSS-17 must not assume completion until this task
finishes. Plain archives expose selected secrets; hashes detect corruption,
not malicious rewriting. For encrypted archives, the manifest is inside the
authenticated age ciphertext, with protection `age-scrypt`. The manifest records type-only included secrets,
external CSRF-key omission, installation key ID, portability caveats and file
digests/sizes.

Decisions used: G-BACKUP's selected shared tar/JSON base, optional age encryption,
default plain mode, logN=18 ceiling and approved size limits; no new format or
KDF policy chosen.

Verification: `.venv/bin/python -m pytest -q tests/test_portable_backup.py`
passed 22 before this follow-up, including four-mode round trips, wrong passphrase, tampering,
truncation, bounded header, secret channel, optional-tool absence, owner-only
outputs, WAL, external omission, manifest allowlist, four competing external
writers, simulated write/publish failures and prior-output preservation. The
combined diagnostics/auth/deployment/identity/maintenance/snapshot/backup suite
passed 112 after the two additional failure tests. Changed Python files
passed Ruff lint; format checks pass for new backup files, while existing
layout in deployment_config.py and auth_crypto.py is not Ruff-formatted.
The follow-up packaging/backup/snapshot/identity/maintenance suite passed 84,
including the post-rename sync failure test and wheel-inventory unit test.
The release wheel build from its sdist succeeded after explicitly including
`web/package.json` in the sdist. `build_release.py --wheel` now checks for the
age helper in the wheel; an unpacked-wheel age encryption/decryption round trip
with a dummy passphrase succeeded outside the checkout. Later native-helper
tests built the pinned Go sidecar and passed full and settings encrypted
round trips with age/Expect hidden from PATH, wrong-passphrase and truncation
rejection, and plaintext cleanup. A missing frozen sidecar fails closed instead
of borrowing host tools. Subprocess hard-exit tests cover both pre-publication
and post-rename crashes and next-export recovery of private plaintext staging.
The backup/native suite passed 50 tests; the affected backup/native/snapshot/
maintenance/deployment/auth/identity suite passed 137. Ruff and diff checks passed.
An actual disposable macOS arm64 PyInstaller fake-model build passed both
collected-sidecar encryption/decryption and frozen entrypoint smoke with a
restricted PATH; its inventory records the bundled executable at 3,841,088
bytes. `/usr/bin/time -l` on the collected sidecar encrypting a tiny dummy
payload at logN=18 measured 0.36 s and 275,038,208 bytes maximum RSS on
macOS arm64; this is not a Pi 3 or large-backup throughput measurement. This is not a
signed production release. A post-rename directory sync failure raises
`BackupPublicationUncertain` rather than suggesting the prior output was
preserved. Ruff and diff checks passed for the follow-up;
`build_release.py` and `test_native_runtime_build.py` retain pre-existing
formatting differences. The PyInstaller spec compiles, but ordinary
Ruff on the spec flags its existing injected `Analysis`/`PYZ`/`EXE`/`COLLECT`
globals. `git diff --check` passed.

Known limitations: Source/wheel encryption still requires age and Expect on
POSIX; the Windows wheel is developer-oriented and has no encrypted export
transport. Native Windows/Linux builds require their own Go 1.25+ toolchain;
their binary collection and encrypted-export execution were not run on this
macOS host. Pi 3 restore performance remains unmeasured (CSS-17). The wheel
build stages and removes production frontend assets automatically.
No real passphrase is routed via argv/env. Database and external-file generation is checked
under the maintenance lease; uncoordinated manual file edits are outside this
application-level barrier. A post-rename directory-sync failure reports
uncertain durability because it cannot guarantee preservation of the prior archive.
The plain parser is a draft shared validator, not a restore activation API.
Abruptly interrupted export staging stays owner-private until the next export;
without another export an operator must remove abandoned `.editarr-backup-*`
directories from the output directory manually. The lock file is persistent.

Remaining work: None within CSS-16. Before shipping a Windows/Linux installer,
build and run its target-native encrypted export smoke; measure Pi 3 decrypt
resource use in CSS-17. CSS-17 owns staged restore validation, not activation.
Next task ID and exact first action: CSS-17; implement staged age decrypt with
the same bounded work factor and tar/manifest validation before any active-data
mutation. Preserve output-stage recovery and uncertain-publication semantics.

```text
Status / date / implementer:
Implemented outcome and last completed substep:
Files changed and relevant revision:
Contracts / APIs / schemas / migrations / fixtures introduced:
Decisions used (gate IDs and recorded answers):
Verification commands and results:
Known failures, limitations, unavailable checks, and recovery notes:
Remaining work in this task:
Next task ID and exact first action:
```

Direct dependent tasks: [CSS-17](17-restore-validation.md), [CSS-19](19-backup-cli.md).
