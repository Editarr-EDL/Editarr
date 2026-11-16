# CSS-31 hosted key lifecycle contract v1

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

October 1, 2026. Local/mock implementation under confirmed G-KEYS/D18/D21.
No production route, new migration, local rotation trigger or lost-key recovery.

## Routes and authorization

All paths use the configured canonical HTTPS service authority. Nonlocal Worker
requests still return 503. Trusted ingress and current membership remain required.
Discord ID, private key, provider credential or local administrator credential
appears in request/response bodies. Responses are `Cache-Control: no-store`.

| Route | Authorization and result |
| --- | --- |
| `POST /v1/keys/rotate` | CSS-12 signature from the target's currently active old key; versioned dual-proof body below; durable `Idempotency-Key`. Atomically retires the old key and activates a previously unused replacement. |
| `POST /v1/keys/rotation-status` | CSS-12 signature from the prepared replacement key, exact original body and operation ID. Stored old/new proofs establish authority for this operation only. Returns `completed` plus immutable result or `not_committed`/null while the original key is still active. |
| `POST /v1/keys/revoke` | CSS-12 signature from any currently active installation under the same contributor; `{version:1,installationId,keyId}` and a durable operation ID. Marks that contributor's named active or retired key revoked, preserving public bytes. |
| `GET /v1/key-operations/{key_rotation\|key_revocation}/{operationId}` | CSS-12 signed read by an active key under the owning contributor. Returns stored `state`/`result`; foreign or absent operations return the same 404. Never authorizes an inactive key. |

IDs and proofs do not establish ordinary request authority. Operation reads and
replacement-signed status must pass current membership and contributor checks.
An inactive prepared replacement cannot recover a committed operation after it
has itself been retired/revoked. A current owned sibling can inspect historical
results using the signed GET, but must not reactivate historical credentials.

## Dual proof bytes

The strict JSON object has exactly six fields:

```json
{"version":1,"installationId":"<lowercase canonical UUID>","oldKeyId":"sha256:<64 lowercase hex>","newPublicKey":"<canonical padded standard base64 of 32 raw Ed25519 bytes>","oldProof":"<base64 of 64 signature bytes>","newProof":"<base64 of 64 signature bytes>"}
```

Maximum body is 2048 bytes. Content type is exactly `application/json`, with no
content encoding or query. Operation IDs use the CSS-12 1-128 ASCII token grammar.
The new key ID is `sha256:` plus SHA-256 hex of its raw public bytes; it cannot
equal the old key ID. Old public bytes must also match their SHA-256 ID.

For each role `old` and `new`, sign the UTF-8 bytes of this JSON array using the
corresponding Ed25519 private key:

```text
["editarr-key-rotation-v1",role,authority,operationId,installationId,oldKeyId,newKeyId,newPublicKey]
```

Serialize compactly with commas/colons and no spaces/newline. Every element is
ASCII and all strings use ordinary JSON quoting. Authority is the exact service
host (including port when present), not the origin with scheme. Role separation
prevents reflecting one proof into the other. Service, installation, both keys
and operation are all bound. The request itself separately signs exact HTTP body
bytes under the unchanged CSS-12 profile, including its digest/idempotency key.

Freeze the body bytes, both detached proofs and operation ID before submission.
Retries/status reuse those exact bytes; only the outer HTTP signature timestamps
and nonce change. Reordering or otherwise changing JSON under the same operation
conflicts after commit, even when the decoded intent is equal. The shared
[RFC 8032 public-key vector](../../../tests/fixtures/community/key-rotation-v1.json)
was generated independently with Node crypto and verified by Worker Web Crypto
and Python cryptography. No real private keys are in the fixture.

## Atomic state and recovery

`changeInstallationKey` executes one D1 batch:

1. Insert the signed request's nonce with contributor binding. Existing schema
   0004 rechecks active signer/contributor inside the transaction.
2. Compare-and-change the exact owned target key. Rotation requires signer = old
   active target; revocation permits an active owned sibling and active/retired target.
3. Insert a completed operation only when the target changed. A zero-row change
   forces a NOT NULL failure and rolls back the entire batch.
4. For rotation, insert the replacement key as active. Global fingerprint uniqueness
   and the existing one-active-key index reject collisions/concurrent replacements.
5. Commit nonce, operation and authority changes atomically; any activation/constraint failure rolls back the batch.

No contributor/profile/reputation/installation row is replaced. Other installations
keep their active keys. Old public bytes remain available to verify old artifact
signatures. Revoking retired history retains the original retirement timestamp;
the revocation operation records its own commit time.

The immutable operation result is:

```json
{"version":1,"kind":"key_rotation","operationId":"...","contributorId":"...","installationId":"...","oldKeyId":"...","newKeyId":"...","committedAt":1700000030000}
```

Revocation uses kind `key_revocation` and `newKeyId:null`. Commit timestamps are
Unix milliseconds; outer HTTP signatures/nonce expiry remain Unix seconds. D1
operation history is durable, not removed by nonce/grant cleanup. No new table
or backup inventory is needed: the existing keys/operations tables hold it.

After an uncertain send, use the replacement private key and retained dual proofs
for status; no retired old private key is needed. `completed` confirms the stored
operation and requires the replacement to still be active. `not_committed` means
there is no operation at that read and the original key is still active. It is
not cancellation of an in-flight POST: serialize local sends/status and never
discard a candidate solely because a concurrent probe saw no commit. A stale/
revoked target returns `key_inactive`, not a misleading `not_committed`.

Status is a read-only POST so an unregistered candidate can sign with its raw
public key; it does not insert a nonce with an unregistered foreign key or perform
a mutation. Bounded signed-read replay applies: every probe/operation read consumes
a fresh passive request observation. Dual proofs restrict the candidate to
one owned prepared rotation and confer no catalog/mutation authority. Mutations
consume nonce atomically; same-digest completed operations return their result
without another mutation. The retired old key still cannot retry a
rotation POST; it must use replacement-signed reconciliation.

Self-revocation intentionally removes the caller's ordinary authority. A current
owned sibling can reconcile/retry that operation. If none survives, do not grant
revoked-key lookup access: CSS-33's fresh Discord recovery remains necessary.
No automatic replacement, operator shortcut or recovery protocol is added here.

Usage observation failure after commit only adds `Community-Usage-Status:
unavailable`; it cannot undo the operation or turn a known success into failure.
Underlying storage failure can still yield an uncertain outcome: clients must
retain their journal and probe, never generate a second candidate implicitly.

Safe errors include `invalid_key_operation` (400), `body_too_large` (413),
`invalid_rotation_proof`/`invalid_signature`/`key_inactive` (401), membership
ineligible (403), operation not found (404), operation/key/nonce conflicts (409),
unavailable membership/service (503). Existing
enrollment/profile contracts and the nonlocal service guard are unchanged.