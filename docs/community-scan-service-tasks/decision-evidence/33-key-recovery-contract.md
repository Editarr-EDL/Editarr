# CSS-33 recovery contract and runbook

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Implemented local/mock scope, October 1, 2026. G-KEYS/D21 and G-RECOVERY/D24
remain the authority: independent installation keys, fresh Discord ownership,
explicit administrator confirmation, immediate affected-key retirement, and no
lost-Discord transfer bypass. This is not live deployment evidence.

The [shared fixture](../../../tests/fixtures/community/key-recovery-v1.json)
freezes routes and DTO fields. CSS-12 signatures and confirmation hash domain
remain unchanged; no new cryptographic primitive or signature profile is introduced.

## Hosted ownership and transaction

`GET /v1/discord/recovery-start` takes exactly `installationId`, `oldKeyId`,
`keyId`, canonical base64 `publicKey`, `confirmationHash`, and UUID `operationId`.
POST accepts the same strict JSON fields with same-origin protection. The backend
must persist the candidate and random confirmation secret before opening this URL.
Only its domain-separated hash is sent at start. The existing browser cookie,
explicit hosted consent, one-use OAuth state, fixed callback and fresh provider
identity/membership validation remain mandatory.

Start resolves an existing active contributor and owned installation/key, rejecting
a stale target when another key is active. The separate
`key_recovery_authorization` operation binds that contributor, installation,
old fingerprint, candidate fingerprint/public bytes, operation, confirmation hash
and ten-minute expiry. Callback refuses a different Discord subject or suspension
before issuing a grant. Public references, usernames and browser consent alone
never authorize replacement. Normal `/v1/discord/start` and enrollment completion
cannot create or consume this recovery authority.

After local administrator confirmation, the candidate signs
`POST /v1/keys/recover`, with `Idempotency-Key: operationId` and exact body:

```json
{
  "version": 1,
  "stateId": "<64 lowercase hexadecimal characters>",
  "operationId": "<canonical UUID>",
  "installationId": "<retained installation UUID>",
  "oldKeyId": "sha256:<affected fingerprint>",
  "confirmationSecret": "<backend-only 32-byte hexadecimal secret>"
}
```

No old private key/proof is required. Current contributor, fresh grant, stored
authorization, secret hash and candidate request signature must all agree.
Continuing bot membership still applies. One D1 batch claims the exact unconsumed
grant, enforces contributor/installation ownership and unchanged affected-key
scope, publishes `key_recovery`, retires an active affected key, activates the
candidate, inserts its nonce while preserving the existing contributor identity.
Already retired/revoked affected keys can be recovered only when there is no
different active key; revoked history stays revoked. Constraint/service failures
roll back the whole transition. Historical public bytes and all sibling keys,
contributor/name/moderation/profile stay unchanged.

Success/status returns `{version:1,state:"completed",result}`; result contains
`version`, `kind:"key_recovery"`, `operationId`, committed `stateId`,
`contributorId`, `installationId`, `oldKeyId`, `newKeyId`, `committedAt` in
milliseconds. No Discord ID, provider token, candidate seed or secret is returned.
Authoritative key checks and nonce guards deny later old-key reads and stale
preauthenticated mutations; there is no cache grace or retired-key exception.

## Uncertainty and fresh authorization

`POST /v1/keys/recovery-status` signs the exact saved body with the candidate.
It never mutates key state, and returns `not_committed`/null only for a valid,
unexpired grant and unchanged target; this is a read snapshot, not cancellation.
Committed retries/status verify exact digest and currently active candidate.
Identical retries reuse the result and do not repeat the mutation; explicit
status reads record passive request observations. Changed bytes conflict.

The completed authorization retains public candidate evidence after temporary
grant/nonce cleanup. Hourly bounded cleanup removes only expired unused
authorizations, not the one referenced by a committed recovery. Existing
operations/history remain durable; no schema migration was added or applied.
Hosted backups must preserve these existing operation and key tables.

A fresh grant can reuse the same prepared candidate/operation after expiration.
If an older grant already committed, active-candidate signed
`GET /v1/key-operations/key_recovery/{operationId}` retrieves its durable outcome
under existing contributor/membership/service checks. Unregistered/inactive
candidates gain no general read authority. Local validation requires the same
operation, contributor, installation, old/new fingerprints and a valid committed
state reference; a fresh reference cannot cause another replacement.

## Local Runbook

1. Try an active matching backup first. Restoring an old retired/revoked key never
   reactivates it. Local scans/reviews remain independent of community eligibility.
2. For lost private bytes, retain the installation/contributor metadata or restore
   it from a matching backup. Settings > Connections > Lost-key recovery shows
   the target installation and affected fingerprint. A newer affected fingerprint
   may be entered explicitly; nothing is inferred from names or machine identity.
   If all metadata is lost, restore a matching backup to identify the old target,
   or register a separate installation with normal Discord connection. A new
   installation still resolves to the existing contributor, not fresh reputation.
3. Confirm **Recover with Discord / Prepare recovery**, authorize the original
   account on the separate hosted recovery page, and paste the public reference.
   Review the target and immediate old-copy/old-backup access loss, then explicitly
   **Confirm recovery**. Cancelling either dialog sends no corresponding action.
4. Reload is passive. **Check recovery** probes only the saved candidate/body.
   **Retry saved recovery** requires confirmation, probes first, then resends only
   a verified uncommitted attempt. Outage/conflict/inactive results retain the
   journal. Never delete it or generate another candidate to guess an outcome.
5. For expired authorization, confirm **Fresh Discord recovery authorization**,
   paste its new reference and approve again. Candidate, operation and target remain
   unchanged. Existing committed-operation lookup resolves an older lost response.
6. After verified completion, create a fresh full or settings identity backup.
   Encryption remains optional/off by default. The reminder honestly leaves
   freshness unverified. Without the original Discord account and an active
   matching backup there is no transfer/operator bypass; continuing membership
   and suspension checks also apply to restored active credentials.

The optional `recovery` field in the existing owner-private `community-identity-1`
manifest stores candidate seed, operation, target, secret and exact body atomically.
Recovery-only reads tolerate a missing old private file while validating retained
public metadata and SQLite identity ownership; normal signing stays strict.
`rotation.lock` and the maintenance worker lease serialize probes/sends/publication
against rotation and backup capture. No automatic startup/restore/CLI trigger exists.
Verified activation writes the inactive existing slot and atomically switches the
manifest pointer, removes the candidate seed from the journal and preserves sharing
consent when repairing the matching SQLite projection. Pending rotation evidence
is retained until recovery actually commits, then superseded without resending it.

All four backup modes retain a coherent pending/completed generation. A pending
lost-old-key backup can contain only the manifest's validated candidate seed;
restore permits that explicit journal but never ordinary signing with absent keys.
Secret inventory now includes `community-recovery-proof`. Older application builds
without this manifest/secret type reject these new archives rather than silently
discarding recovery authority; earlier archives remain readable by this build.

## Evidence and Limitations

- Worker full suite: 318 tests/43 files; focused recovery transaction/HTTP: 20 tests.
  Covers fresh/wrong Discord, separate installations, sibling independence,
  duplicate/concurrent recovery, historical signatures, stale mutations, revocation,
  activation rollback, expiration/cleanup, suspension/outage, native
  admission and the nonlocal guard. Worker typecheck/dry-run build pass.
- Final scoped community/auth/backup backend suite: 224 tests passed.
   Local missing-key/retry/result/persistence/consistency/contract suite: 14 tests.
  Four-mode pending/completed recovery backup checks are included in the 21-test
  rotation/backup suite. Administrator/CSRF tests cover every new route under both
  local-bypass and external-auth deployment modes. The auth inventory omission
  was repaired and its 45-test auth/API rerun passed.
- Frontend full unit suite: 171 tests/23 files; eight focused Chromium connection,
  rotation and recovery journeys pass at 320px/1440px, including axe, overflow,
  focus/Escape, passive reload, no secret exposure and unchecked backup encryption.
  Frontend TypeScript/production build and changed-file Ruff pass.
- All provider replies, keys, databases and browser traffic are isolated/mock.
  No live Discord, Cloudflare resource, existing database, deployment, model or
  user identity was touched. Separate production isolates/replication, native
  Windows ACL/locking, non-Chromium and packaged-runtime UI remain unverified.