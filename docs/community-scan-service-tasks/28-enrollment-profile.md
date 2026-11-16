# CSS-28 — Register Discord-bound keys and contributor profiles

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE**, October 1, 2026, hosted local/mock scope. Confirmed enrollment,
same-key reconnect/retry, unique three-word names and signed profile updates are
implemented. Local administrator/CSRF confirmation UI and client persistence are
CSS-29/30; live deployment/setup remain OPS-01/02.

- Prerequisites: [CSS-23](23-d1-identity-schema.md), [CSS-25](25-worker-request-auth.md), [CSS-26](26-discord-oauth.md), [CSS-27](27-membership-eligibility.md)
- Decision gates: [G-KEYS](decisions.md#g-keys)
- Parent sections: contracts §1; Discord §1–3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Discord account linkage, key possession, names and signing authority are separate. Reinstall/retry must preserve contributor reputation and cannot silently replace a key.

## Starting points

community-service/src/identity/; enrollment routes; profile routes.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Consume Discord grant plus key challenge atomically; enroll a new logical installation with its own key under the existing contributor when the Discord account already exists.
2. Return stable contributor/key status, generated word name, limits and policy versions; implement bounded custom display-name updates.
3. Preserve moderation and reputation on reconnect; distinguish a new installation from explicit lost-key recovery of an existing one. Normal enrollment never retires another installation's key.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Concurrent/repeated same-account enrollment creates one contributor and no duplicate contributors or votes.
- [x] Grant/public-key mismatch fails; retired credentials cannot re-register themselves.
- [x] Names are untrusted display labels, not credentials; JSON profiles expose no private Discord IDs/tokens. Consumers must escape text when rendering.

## Verification

- D1 tests for duplicate enrollment, stolen grant, same key/different identity conflict, rename validation and suspended rejoin.
- Contract-test identity response with the local client fixtures.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for hosted local/mock scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide enrollment/profile routes, reconnect outcomes, name rules and recovery-required errors to CSS-29/30/33.

## Handoff summary for the next task

October 1 completion, GitHub Copilot (uncommitted workspace changes):
`src/http/enrollment.ts` exposes local `POST /v1/enrollments/complete` with
trusted ingress and passive measured-write observations, signature,
backend-secret hash and CSS-27 membership enforcement. `enrollment-proof.ts` and
`repository.ts` atomically claim grant, resolve the existing Discord contributor,
register installation/key and record nonce; failure rolls back.
Repeated same-grant/active-key completion reconciles within grant expiry, without
another grant claim. Fresh same-key/installation authorization reconnects, atomically
reserving a fresh proof nonce with its fresh grant claim. A different
key for that installation is `recovery_required`, never replacement; inactive
credentials, wrong keys/subjects and suspended contributors fail. Existing
contributor names, moderation, reputation and contributor identity remain.

`src/http/identity.ts`, `identity/profile.ts`, `identity/rename.ts` implement signed
GET/PATCH own profile, current label/revision and unique normalized names with
unrestricted valid name changes. Nonce/operation/name-event/name update
are atomic. Same-operation changed bodies conflict; idempotent/no-op updates do
not spend a name change. Generated names use three curated words, bounded collision
retries and a unique name-key index. No HTML renderer is introduced; labels remain
untrusted JSON text and consumers must escape them. Responses contain policy,
protocol, active key, installation/contributor IDs, implemented allowed operations
and allowed operations, never Discord linkage or secrets. Every success
reads current labels so retries cannot restore a stale client name.

`0008_enrollment_profiles.sql` adds confirmation hashes, normalized current-name
keys, revision and bounded rolling name-event cleanup. Fresh and populated versions
0001-0007 upgrade with identity/moderation/key/grant/nonce/operation/counter/browser/
provider-coordination preservation. Legacy ASCII names backfill lowercase trimmed
keys; Unicode requiring application normalization or collisions stop safely for
review instead of silently renaming. Verify a recoverable backup and authorize
each existing-store upgrade; no real store was accessed/applied/reset here.

Approved gates: G-KEYS/D21 plus October 1 explicit backend-held confirmation,
active-key retry and three-word/current-unique/unrestricted-name-change/client-refresh answers;
case-insensitive normalized uniqueness explicitly selected. Local admin confirmation
and storage are a client responsibility in CSS-29/30 as approved, not implemented
by a hosted endpoint. The fixture
[enrollment-profile-v1](../../tests/fixtures/community/enrollment-profile-v1.json)
freezes fields/hash domain/routes/window/name bounds for independent client tests.
Full synthetic browser OAuth->grant->actual Worker completion passes. Existing
proof tests retain attack/rollback/suspension/retired-key coverage; new tests cover
same-key concurrent reconciliation, private data exclusion, storage-failure rollback,
rolling rename boundaries/uniqueness and latest names on retries.

Verification: `pnpm run test` 268 tests/36 files passed, `pnpm run typecheck`,
`pnpm run build` (dry run) and `git diff --check` passed. All isolated/mock only;
no live Discord, Python client transport, admin UI, production deployment or existing
database migration was performed. Nonlocal requests/scheduled work remain disabled;
protected runtime configuration remains disabled. CSS-25 remain partial downstream workflows.

Next exact action: CSS-29 implements private durable confirmation secret/pending
transaction storage before OAuth; authenticate local administrator + CSRF for start
and explicit completion; sign exact fixture bytes using the existing portable key;
reconcile lost responses with the same state/operation and refresh nonces/signatures.
Persist identity/profile updates atomically, clear pending secret after success/expiry,
replace cached names from every response and refresh signed identity on connection/
profile views. CSS-30 renders the explicit confirmation and escaped updated labels.
CSS-33 owns actual lost-key replacement; present `recovery_required` without doing it.

Historical partial evidence below is retained for context; the October 1 handoff
supersedes its pending method/route items.

September 30 partial handoff: the smallest independent step was hardening the
existing `community-service/src/identity/repository.ts` enrollment transaction.
The initial read remains an early invalid-state check, but the batch now first
claims an unconsumed, unexpired grant matching the provider subject, installation,
key ID and exact public-key bytes. The following contributor insert requires
`changes() = 1`; a failed claim aborts the batch through the existing NOT NULL
constraint, including when the Discord account already exists. Installation/key
constraint failures roll back the grant claim and contributor writes together.
Normal enrollment still cannot replace or retire an existing installation's key.

`test/identity-operations.test.ts` adds simultaneous same-grant consumption and
deterministic changes between the initial read and batch for installation ID,
key ID, public key, provider subject, expiry and consumption. It also verifies
that an installation conflict leaves the grant unconsumed. Existing migration
0001 constraints suffice; no migration was added, rewritten or applied, and no
existing database was accessed. Future schema upgrades still require an additive
numbered migration, upgrade tests and a verified recoverable backup.

Decisions used: G-KEYS/D21 preserves separate installation keys under the same
contributor. No membership recheck method or production limit was selected.
The Worker retains its non-local 503 guard; routing and secrets are unchanged.

Verification from `community-service`: `pnpm exec vitest run
test/identity-operations.test.ts` passed 14 tests; `pnpm run test` passed 85 tests
across 13 files; `pnpm run typecheck` and `pnpm run build` (dry run) passed.
All checks used synthetic Workerd fixtures, not live Discord or hosted resources.

Follow-up on September 30: `src/identity/enrollment-proof.ts` adds
`completeEnrollmentProof`, an internal helper with no Worker route. It reads
at most 1,024 body bytes, uses a strict versioned payload and verifies CSS-12
signatures against the OAuth grant's stored public key and SHA-256 key ID.
The provider subject comes only from the grant, never from the caller's body.
The verified nonce is inserted into existing `request_nonces` in the same batch
as grant consumption, contributor resolution and installation/key registration.
A nonce insert failure rolls back all registration writes and consumption.
`enroll()` remains a trusted internal persistence helper; future handlers must
use the proof helper rather than accepting caller-supplied enrollment objects.
No new persistent table, secret or schema migration is required for this slice.

Draft internal proof contract for CSS-29, not an enabled or frozen public API:

```json
{"version":1,"stateId":"<64 lowercase hex characters>","installationId":"<canonical installation UUID>"}
```

Sign the exact UTF-8 JSON bytes as a CSS-12 JSON POST to the configured trusted
HTTPS authority at `/v1/enrollments/complete`, without a query or content encoding.
Set `Idempotency-Key` to `stateId`; all other headers and covered components are
the existing `editarr-community-request-v1` profile. No public key or Discord ID
is accepted in the body. The policy supplies authority, signature lifetime/skew
and a server-selected display name. Signature and persisted nonce expiry times
are Unix seconds; grant and registration timestamps are milliseconds. The helper
returns only contributor, installation and key IDs, not the private Discord ID.
Consumed-grant retries are rejected; a recoverable retry/status response contract
is still needed before public integration. A response is not membership authority.

Verification for this follow-up from `community-service`: `pnpm exec vitest run
test/enrollment-proof.test.ts test/identity-operations.test.ts` passed 37 tests;
`pnpm run test` passed 108 tests across 14 files; `pnpm run typecheck` and
`pnpm run build` (dry run) passed. The proof suite has 23 tests covering stolen
grant IDs, unsigned/tampered/stale proofs, payload/target mismatches, concurrent
completion, rollback after nonce-insert failure and installation conflict,
preserved contributor names/moderation and rejected retired
or revoked key reuse. A multiline D1 `exec()` trigger fixture initially failed
to parse; the one-line fixture passes. No live provider, client transport or
cross-language enrollment payload round trip was run; CSS-12 byte-profile vectors
remain unchanged and are covered by the full Worker suite.

CSS-26 integration follow-up: the proof suite now runs through schema 0004
and records `request_nonces.contributor_id` resolved inside the registration
batch, satisfying active-key/contributor admission. Suspended registration rolls
back rather than issuing a new credential. Nonce expiry and cleanup now use
Unix seconds consistently; grant cleanup converts to milliseconds. A generated
key passes the complete mocked browser start/confirmation/callback and one-use
proof consumption, while another key cannot steal its public state ID. Final
full Worker suite: 136 tests across 20 files; typecheck and dry-run build pass.

Historical remaining work before October 1: local administrator confirmation binding, public enrollment/retry
response contract, generated names, rename API and completed profile routes.
The internal proof helper is not wired into the Worker or local client.
CSS-25 remain IN PROGRESS. CSS-26 and approved CSS-24A broker admission are
DONE locally; CSS-27 is DONE with user-selected bot REST. Production/live
verification remains outstanding. Do not mark CSS-28 DONE.

Historical next action: define the local administrator-confirmed handoff and retry
contract with CSS-29/30, add independent client signing fixtures, and integrate
the proof helper only behind the missing eligibility and admission protections.
Use CSS-27's bot membership hook; no retained OAuth strategy is authorized.

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

Direct dependent tasks: [CSS-29](29-local-service-client.md), [CSS-30](30-connection-settings.md), [CSS-31](31-key-rotation-service.md), [CSS-33](33-identity-recovery.md).
