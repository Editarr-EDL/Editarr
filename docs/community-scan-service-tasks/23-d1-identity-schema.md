# CSS-23 — Add D1 identity and operation migrations

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE**, September 28, 2026. Local D1 implementation and evidence follow.

- Prerequisites: [CSS-22](22-worker-scaffold.md)
- Decision gates: [G-KEYS](decisions.md#g-keys)
- Parent sections: contracts §1–2 and §5; Discord §1.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Contributor identity is stable and privately linked to one Discord user ID. D21 confirms separate keys per logical installation, with shared contributor reputation and votes.

## Starting points

community-service/migrations/; src/identity/; src/operations/; tests/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Create numbered migrations for contributors, installations, external identity uniqueness, installation key history, bounded enrollment state, nonces and idempotent operations.
2. Implement atomic repository operations with explicit conditional-update checks and named uniqueness constraints.
3. Store public key metadata and approved private provider linkage only; preserve old key history and shared contributor identity.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Same Discord account cannot acquire duplicate contributor records under concurrent enrollment.
- [x] Retired/revoked keys remain distinguishable; schema supports the approved active-key policy without identity resets.
- [x] Fresh and upgrade paths match in the real D1 test runtime; no reliance on unsupported SQLite transaction behavior.

## Verification

- Run D1 migration and concurrent uniqueness/operation tests.
- Record query/index plans for frequent key/account lookups and row-write cost.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide D1 revision, key/account relationships and atomic mutation helpers to CSS-25/28/31.

## Handoff summary for the next task

Status / date / implementer: DONE, September 28, 2026, GitHub Copilot.

Implemented outcome and last completed substep: First numbered D1 migration,
focused identity and operation repositories, and workerd tests. No hosted
resources, public routes, enrollment broker or private signing keys were added.

Files changed and relevant revision: `community-service/migrations/0001_identity_operations.sql`,
`src/identity/repository.ts`, `src/operations/repository.ts`,
`test/identity-operations.test.ts`, `test/sql.d.ts`, service README and migration
README, this brief and the task index. No release/protocol version change.

Contracts / APIs / schemas / migrations / fixtures introduced: `contributors`
uniquely owns `discord_user_id`; `installations` refers to one contributor;
`installation_keys` has a partial unique active-key index per installation,
preserves retired/revoked history and never stores private keys. Expiring
`enrollment_states` requires provider-bound subject and single consumption;
`request_nonces` is unique on `(key_id, nonce)`; `operations` is unique on
`(contributor_id, kind, operation_id)` with immutable digest on retry.
`enroll` batches contributor/installation/key/state consumption so a constraint
or stale challenge aborts all writes. `replaceInstallationKey` atomically
retires one active key and activates its successor (caller must authorize the
action). `reserveOperation` reuses an equal digest and rejects conflicting
reuse; `completeOperation` conditionally advances pending to terminal.
`reserveNonce` enforces replay uniqueness, and
`pruneExpiredAuthenticationState` deletes at most the caller's bounded limit
per table. CSS-25 must reserve nonce and protected mutation in its own atomic
batch, not call these independent helpers consecutively for a signed mutation.

Decisions used (gate IDs and recorded answers): G-KEYS / D21, separate active
key per logical installation under one shared contributor, confirmed September
26. D18 keeps rotation manual; D24 reserves lost-key recovery for a later task.

Verification commands and results: `pnpm exec vitest run
test/identity-operations.test.ts` passed 7 tests in workerd D1: concurrent
Discord uniqueness, rollback on installation conflict, key history/stale
replacement, nonce replay, operation conflicts, and index plans. `pnpm run
test` passed 44 tests; `pnpm run typecheck` and `pnpm run build` (dry run)
passed; `CI=1 pnpm exec wrangler
d1 migrations apply CATALOG_DB --local --persist-to /tmp/samwise-css23-final`
applied 14 SQL commands successfully to a fresh D1, then reported no pending
migrations on a second run. `git diff --check` passed after documentation.
Query plans used SQLite autoindexes for Discord, nonce and operation uniqueness
and `uq_installation_keys_active` for active-key lookups. A new enrollment
changes one contributor (or reuses it), one installation, one key and one state
row, with unique/index maintenance; an operation reservation writes one row.

Known failures, limitations, unavailable checks, and recovery notes: No live
D1, production Discord verification, protected routes were tested.
Enrollment state must be created and bound to a verified Discord subject by
CSS-26/28; the repository does not validate the provider assertion or a
signed challenge itself. Key replacement needs CSS-31 authorization and an
operation ID before exposure. Staging data in local D1 is not deployment.
For hosted upgrades, take a recoverable D1 backup and apply numbered migrations
only after production prerequisites are approved.

Remaining work in this task: None.

Next task ID and exact first action: CSS-25; read this migration and the
current Docker contract, then audit current signed routes and atomic
nonce/operation admission without treating synthetic bounds as production policy.
CSS-24 is retired and is not a prerequisite.

Direct dependent tasks: [CSS-25](25-worker-request-auth.md), [CSS-26](26-discord-oauth.md), [CSS-28](28-enrollment-profile.md), [CSS-38](38-d1-catalog-schema.md).
