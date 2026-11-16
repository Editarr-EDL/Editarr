# CSS-31 — Rotate and revoke keys atomically on the service

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** (October 1, 2026), local/mock hosted scope. Live deployment and
local UI triggers remain separate tasks.

- Prerequisites: [CSS-25 shared auth/transaction hooks](25-worker-request-auth.md)
  (implemented; lifecycle route integration verified here; CSS-25 remains partial
  for downstream routes), [CSS-28](28-enrollment-profile.md) (DONE).
- Decision gates: [G-KEYS](decisions.md#g-keys)
- Parent sections: contracts §1–2; main D18.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Rotation is initiated only by the user's UI. Activating the replacement immediately retires that installation's prior key, while preserving installation/contributor IDs and historical signatures. Other installations' keys remain active.

## Starting points

community-service/src/identity/ key lifecycle; D1 repositories; operation status route.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Verify old/new proof and a durable operation ID; atomically replace the target installation's active key and mark its old key retired without changing other installations.
2. Implement revocation and status/reconciliation permitting an activated new key to recover a lost response.
3. Retain public-key history; enforce authoritative status for every later request with no grace cache.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] After commit the old key fails reads/mutations on all isolates; earlier artifact signatures remain verifiable.
- [x] Duplicate/concurrent replacement requests do not activate multiple unintended keys or reset reputation.
- [x] Lost-response reconciliation distinguishes committed/not-committed without requiring a retired key.

## Verification

- Concurrent rotation/use tests and failures before/after commit; historical signature check.
- Test cross-contributor replacement and unauthorized operation lookup rejection.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide rotation transaction, operation reconciliation and revocation semantics to CSS-32/33/66.

## Handoff summary for the next task

DONE / October 1, 2026 / GitHub Copilot / uncommitted working-tree changes.
Confirmed G-KEYS/D18/D21: manual UI-originated intent, independent installation
keys, immediate retirement, preserved public history and shared account identity.
No new product/recovery policy, migration, local key generation or automatic trigger.

### Implementation and contract

- [Atomic lifecycle transaction](../../community-service/src/identity/key-lifecycle.ts)
  binds active signer/contributor, owned target, nonce, operation, replacement and
  replay state in one SQLite batch. Unique constraints and zero-change rollback
  preserve one active key. Contributor/profile/installation rows stay unchanged.
- [Dual proof verifier](../../community-service/src/identity/rotation-proof.ts)
  checks canonical public-key/signature bytes and a domain-separated versioned
  old/new proof pair bound to service/operation/installation/both keys.
- [HTTP handler](../../community-service/src/http/key-lifecycle.ts) exposes rotation,
  revocation, replacement-signed status and contributor-scoped operation reads.
  [Worker dispatch](../../community-service/src/index.ts) wires existing mandatory
  trusted ingress and explicit runtime configuration and CSS-27 membership. Nonlocal
  requests remain disabled. No operator or local CLI rotation endpoint was added.
- [Exact route/proof/recovery contract](decision-evidence/31-key-lifecycle-contract.md)
  and [shared independent vector](../../tests/fixtures/community/key-rotation-v1.json)
  are authoritative for CSS-32. Public DTOs contain no private/provider linkage.
  The existing profile v1 fields and enrollment behavior remain compatible.

`POST /v1/keys/rotate` requires an active old-key CSS-12 signature, immutable
operation/body, and both detached proofs. The transaction immediately retires
only that old key and activates the new fingerprint under the same installation/
contributor. Earlier public bytes remain verifiable. Current D1 status, never a
cached verification result, gates later reads/mutations; stale authenticated
principals are denied by the nonce trigger inside subsequent mutation batches.

`POST /v1/keys/rotation-status` signs the exact original dual-proof body with the
prepared replacement and same operation ID. The retained old proof is verified
with historical public bytes, not an old private key or general retired-key
authorization. With current contributor/membership and relevant active key, it
returns `completed` plus stored result or `not_committed`/null. It never changes
keys or grants an unregistered candidate general authority. Not committed is a
read snapshot, not cancellation of an in-flight POST. Serialize recovery probes
with sends; keep a pending candidate on outage/conflict/inactive responses.

`POST /v1/keys/revoke` permits an active owned signer to revoke a named active or
retired key under the same contributor. Other installations stay active. Signed
`GET /v1/key-operations/{key_rotation|key_revocation}/{operationId}` returns only
that contributor's operation; foreign/absent IDs are indistinguishable 404s.
Self-revocation loses read authority immediately; use an active owned sibling
to reconcile, or await CSS-33 fresh Discord recovery if none survives. Retired/
revoked replacements cannot be silently reactivated through status. Revoking
historical old bytes does not revoke the still-active replacement.

No schema changes: numbered migrations 0001-0008 already provide keys/history,
one-active-key uniqueness, durable operations, active-nonce triggers.
Operations survive nonce cleanup. No database backup/migration/reset was applied;
future D1 backups must continue retaining the existing key/operation tables.

### Evidence and limitations

- `pnpm --dir community-service exec vitest run test/key-lifecycle-http.test.ts
  test/key-lifecycle.test.ts test/rotation-proof.test.ts`: final focused run 24 passed.
  Covers real Workerd D1 concurrent identical/different rotations, signed HTTP
  races, activation rollback, stale principals, cross-contributor targets,
  valid dual-proof reuse of another account's key, unauthorized operation lookup,
  later retirement/revocation, old-key read/mutation denial, sibling independence,
  historical Ed25519 verification, prepared/committed status, expired/malformed/
  oversized proofs, wrong service, unavailable membership and post-commit telemetry.
- `pnpm --dir community-service test`: final run 297 tests/41 files passed,
  including both last HTTP adversarial tests.
- `pnpm --dir community-service run typecheck` and `run build` passed after the
  vector/input changes. Build is Wrangler `--dry-run`, not deployment.
- `.venv/bin/python -m pytest -q tests/test_community_rotation_contract.py`:
  2 passed, independently verifying fixture proof bytes/signatures with Python
  cryptography. Changed-test Ruff passed. Node crypto generated the public RFC
  8032 vector independently of the Worker implementation.
- Editor diagnostics, 154 edited-document local links/fences and
  `git diff --check` passed.
- Initial synthetic tests needed explicit fixture configuration, BLOB conversion,
  legacy-counter compatibility, BufferSource typing and trusted ingress
  bindings; repaired slices passed reruns. No known implementation failure remains.

All tests use isolated synthetic stores/keys/provider responses. No live Discord,
Cloudflare resource, existing database, model, user identity or deployment was
touched. Separate live isolates/production replication/latency are not measured;
authoritative uncached D1 checks and transactional stale-principal denial are
verified locally. Local administrator/CSRF confirmation, pending private proof
journal, backup capture, actual user trigger and UI remain CSS-32. Fresh Discord
lost-key recovery remains CSS-33. CSS-25 are not declared globally complete.
No remaining CSS-31 scoped work.

Next exact action: CSS-32. Read this contract and CSS-14/29/30 handoffs, then extend
the existing private pending-key generation/journal in `scanner/security` and
`scanner/integrations/community` to persist replacement key, exact dual-proof body
and operation before a user-confirmed send. Never generate another candidate on
reload/status/restore; activate locally only after verified committed outcome and
prompt for a fresh optional-encryption backup. Keep both proofs private to the
local backend/backup even though they contain no private-key bytes.

Direct dependent tasks: [CSS-32](32-manual-key-rotation-ui.md), [CSS-33](33-identity-recovery.md).
