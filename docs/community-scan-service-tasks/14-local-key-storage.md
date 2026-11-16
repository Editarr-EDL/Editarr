# CSS-14 — Persist portable installation signing keys safely

Status: **DONE**, September 27, 2026. Evidence and handoff follow.

- Prerequisites: [CSS-06](06-migration-startup-cli.md), [CSS-12](12-request-signing-profile.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §1; implementation §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Keys must survive restart/container recreation and be portable through backups. D06 now makes encryption optional and off by default; both output modes include the selected identity files. Hardware identifiers and image-embedded shared keys are prohibited.

## Starting points

scanner/security/; scanner/runtime/runtime_paths.py; new scanner/community/identity modules.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Load/create Ed25519 key under the existing lock with atomic private-file publication and canonical public-key ID.
2. Maintain a versioned local identity manifest for service/contributor/key state without exposing private material through ordinary settings APIs.
3. Define export inventory and pending replacement slots; normal startup/upgrade/restore never rotates keys.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Concurrent initialization produces one retained key; restarts and durable-volume moves preserve it.
- [x] File permissions follow existing Unix/Windows secure-file practices; logs/frontend never receive the private key.
- [x] Corruption/missing external key produces an explicit recoverable status rather than silent new enrollment.

## Verification

- Test concurrent creation, interruption, permission failure, invalid key and stable fingerprint.
- Run runtime-path/auth-crypto regressions with temporary data directories.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide private-file inventory, key manifest format and load/sign interfaces to CSS-16/21/29/32.

## Handoff summary for the next task

September 27 policy update: the historical handoff below refers to encrypted backups.
D06 now makes encryption optional and off by default; the same validated identity
allowlist is included in either mode. CSS-16 controls archive protection, while this
completed task's private-file storage and snapshot behavior remain unchanged.

Status / date / implementer: DONE, September 27, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Locked, atomic, owner-private
Ed25519 installation identity storage with canonical SHA-256 public-key ID.
Threads and separate processes converge on one key; restart and volume copy
preserve installation and signing identity. Strict versioned manifest detects
missing/corrupt keys, symlinks, broad POSIX permissions, incomplete writes and
invalid public/private pairs without generating a replacement. Explicit
pending slot retains an operation ID and second key without rotating at
startup. Local enrollment-state updates cannot change contributor identity
silently. See [private-file inventory and state contract](decision-evidence/14-local-key-storage.md).

Files changed and relevant revision: `scanner/security/community_identity.py`,
`scanner/security/private_identity_files.py`, `tests/test_community_identity.py`,
the contract/implementation evidence docs, this brief and task index. No
commit, migration, service route, private-key export endpoint or release bump.

Contracts / APIs / schemas / migrations / fixtures introduced: `community-identity-1`
under `${data_dir}/secrets/community/`; `identity.json`, `current.key`, optional
`pending.key`, and excluded `identity.lock`. `load_or_create_installation_identity`
returns a private-key-bearing Python object, `save_identity_registration` stores
trusted local service/contributor/last-known status without replacing the key,
`stage_identity_replacement(operation_id=...)` holds one future user-initiated
rotation slot without activation, and `read_identity_backup_snapshot` copies
the exact validated allowlist under the lock for **encrypted** backups.
`IdentityUnavailable.status` reports `incomplete`, `corrupt`, `unavailable` or
`pending`. Raw 32-byte Ed25519 public key SHA-256 is encoded as `sha256:` plus
lowercase hex. No schema migration or persistent database change.

Decisions used (gate IDs and recorded answers): D18 manual UI-only rotation
and immediate retirement; D20 Ed25519; G-KEYS separate portable keys per
installation (confirmed September 26). No new policy default or key recovery
bypass was selected.

Verification commands and results: `.venv/bin/ruff format --check
scanner/security/community_identity.py scanner/security/private_identity_files.py
tests/test_community_identity.py` and `.venv/bin/ruff check` on those paths
passed; `.venv/bin/python -m pytest -q tests/test_community_identity.py
tests/test_auth_crypto.py tests/test_runtime_paths.py
tests/test_durable_runtime_paths.py tests/test_community_request_signatures.py
tests/test_community_artifact_signatures.py` (65 passed). Thread and separate
process contention, crash interruption, permission failure, corruption,
missing external key, pending-slot restart/move, symlink refusal and backup
inventory are covered with temporary directories. `git diff --check` passed.

Known failures, limitations, unavailable checks, and recovery notes: Native
Windows ACL enforcement could not be exercised on macOS; the code follows
the existing per-user AppData ACL convention and cross-platform kernel lock
primitive, with native verification left to platform testing. No user data,
live enrollment, restore activation, service key-state check, production
backup archive or protected API was touched. A complete loss of **all**
external identity files looks like a fresh installation to this file-only
loader: CSS-21 must check its persisted contributor references before calling
it, and CSS-17/18 must restore the matched key and manifest generation.
Local last-known key state cannot authorize requests. An interrupted write
is intentionally `incomplete`; use a verified encrypted backup or future
explicit Discord-assisted recovery, never delete the files to force a new key.

Remaining work in this task: None. Native Windows ACL/runtime checks remain
platform verification rather than proof claimed from macOS tests.

Next task ID and exact first action: CSS-15; read its encrypted-backup
container evaluation brief and CSS-03 snapshot handoff before comparing
formats. CSS-16 must consume `read_identity_backup_snapshot` for both backup
kinds; CSS-21 must reconcile file presence with persisted contributor state;
CSS-29 must use the in-memory private key for CSS-12/13 signing without exposing
it to HTTP; CSS-32 must drive staging/activation only from the explicit UI
flow, retaining the operation ID and both key files until authoritative
reconciliation.

Direct dependent tasks: [CSS-16](16-encrypted-backup-export.md), [CSS-21](21-local-community-state.md), [CSS-29](29-local-service-client.md), [CSS-32](32-manual-key-rotation-ui.md).
