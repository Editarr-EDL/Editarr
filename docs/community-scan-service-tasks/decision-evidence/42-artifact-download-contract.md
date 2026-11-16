# CSS-42 download and receipt contract

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Completed October 3, 2026 in local/isolated-test scope. The user explicitly
approved the proposed separate service Ed25519 receipt: contributor/revision/
digest-bound, stateless, no expiry, with trusted public-key history retained.
Downloads do not count imports or votes. Production remains disabled and no
existing database, real secret, Discord resource or personal media was touched.

## Modules and activation

Paths below are relative to `community-service/`:

- `src/contributions/storage.ts`: `readPublication` now optionally compares all
  three lengths/digests with immutable revision metadata as well as recomputing
  each original-byte SHA-256. Existing callers remain compatible.
- `src/contributions/download.ts`: explicit limits, immutable-byte cache,
  authoritative published revision/author-key reads and final active-requester/
  moderation fence.
- `src/contributions/receipts.ts`: separate service signer loading, receipt identity,
  issuance and verification against independently trusted service public keys.
- `src/http/download.ts`: signed bounded GET, trusted admission and passive usage, exact
  bundle transport, request/read/write observations and redacted failures.
- `src/budgets/download-usage.ts`: query-bounded D1 metadata instrumentation and
  per-contributor daily observations, including unknown-result counts.
- `src/index.ts` / `src/config.ts`: local-only dispatch and missing-config denial.
- `migrations/0011_download_usage.sql`: additive daily telemetry only.

Local activation needs known schema through 0011, D1, exact HTTPS `PUBLIC_ORIGIN`,
`POLICY_VERSION`, explicit `DOWNLOAD_LIMITS`, `DOWNLOAD_RECEIPT_PRIVATE_KEY`,
`DOWNLOAD_RECEIPT_KEYS`, trusted native admission, enabled protected runtime configuration and bot
membership configuration. No defaults or credentials were added to Wrangler.
All non-local requests still fail before route work.

Limits are positive safe integers: `requestBytes` (<=8192), `artifactBytes`,
`manifestBytes`, `payloadBytes` (<=1,900,000), `responseBytes` (<=3,000,000),
`maxQueries` (<=24 handler queries), `cacheEntries` (<=64), `cacheBytes`
(<=16,000,000), `cacheTtlSeconds`. Artifact + manifest caps + 256 signature bytes
must fit the combined payload cap. These are technical ceilings, not production
settings or hosted CPU/capacity approval. Native admission and membership work
outside the handler retain the remaining Free-plan query headroom.

## Signed download transport

`GET /v1/contributions/{revisionId}/artifact` requires CSS-12 exact-body signing
by a currently active key/contributor and current CSS-27 eligibility. The GET
body must be empty. Query strings, content encoding, ranges and conditional
requests are rejected; no 304 or public object URL bypass is supplied. Logical
read retries are observed but do not consume a mutation nonce or create operations.

Response JSON has these exact transport fields:

```text
version: 1
revisionId, artifactSchema, artifactFormat: "gzip-json"
artifactDigest, manifestDigest, envelopeDigest: original lowercase SHA-256
artifact, manifest, envelope: padded canonical base64 of original buffers
historicalKey: {contributorId, keyId, publicKey, status}
receipt: {receipt: {schemaVersion, service, serviceKeyId, contributorId,
                   revisionId, artifactDigest, receiptId, issuedAt}, signature}
```

This preserves all three original signed buffers without decompression,
reserialization or recompression. JSON/base64 is the outer transport; it is not
the signed artifact representation. `Content-Type` is `application/json`,
`Content-Length` is its actual byte length, and `Content-Digest` is the
structured-field SHA-256 of the complete personalized JSON response. That digest
is distinct from `artifactDigest`, which binds the original inner gzip bytes.
There is no HTTP `Content-Encoding`, even when the requester advertises gzip/br.
`Cache-Control: private, no-store, no-transform` forbids shared HTTP caching and
transformation. No artifact URL, storage key, private key or Discord ID is returned.

Historical author keys come from the immutable registered revision-to-key-to-
contributor binding, not uploaded response claims. Retired author keys remain
valid for old signatures; revoked keys are denied. A retired requester cannot
download. Clients must trust the configured service origin and verify original
CSS-13 signatures/manifest/schema/limits locally, not treat public keys alone as
enrollment proof or use a service receipt as an artifact-author signature.

## Authorization and cache

Current requester signature and membership precede
revision and payload access. Only published revisions and trusted historical
author keys are eligible. Length limits are checked before reading payload BLOBs.
The payload reader compares immutable metadata and recomputes all three hashes.
Missing/corrupt/mismatched parts fail as `artifact_unavailable` without returning
a receipt or partial success.

The bounded isolate cache contains only immutable bytes/digests, never receipts,
public-key history, names or HTTP responses. It is keyed by revision,
artifact schema and all three immutable length/digest pairs and scoped to the
original D1 binding, not per-request instrumentation proxies. Every hit repeats
current admission, lifecycle and historical-key checks and issues the requesting
contributor's receipt. Reduced cache capacity/TTL also applies to existing entries.

After payload retrieval and receipt signing, a final authoritative query fences
requester retirement/suspension, author-key revocation/change and moderation.
Race tests withdraw/quarantine or revoke during the payload read and observe
denial. This query is the download authorization linearization point; a change
committed after it cannot retract bytes already authorized for an in-flight
response. No cached row grants subsequent requests permission.

Errors: malformed transport (400), inactive/invalid requester (401), nonmember
(403), absent revision (404), method (405), moderated/nonpublished revision or
untrusted historical author (410), configured/query/response bound (413), native
, provider/policy/signer/database/payload unavailable
(503). Responses are redacted. Telemetry failure adds request-local
`Community-Usage-Status: unavailable` without undoing authorized byte delivery;
the header is never cached.

## Receipt byte profile and idempotency

JSON has a strict receipt object plus a canonical padded-base64 64-byte Ed25519
signature. Unknown fields/versions, wrong binding, signature alterations and
untrusted/revoked service keys fail verification. JSON property order is not the
signature input: the fixed binary message below is authoritative.

`receiptId` is SHA-256 of ASCII `editarr-community-download-identity-v1` + NUL,
then four uint32-big-endian-length-prefixed UTF-8 strings, in order:
`service`, `contributorId`, `revisionId`, `artifactDigest` (lowercase hex text).
The service is the exact canonical HTTPS origin. This identity excludes time
and signing key, so same-contributor/revision/digest retries and key rotations
retain the identity. Different contributors get different identities.

The ordinary Ed25519 signing message is ASCII
`editarr-community-download-receipt-v1` + NUL, then seven uint32-big-endian-
length-prefixed UTF-8 strings in this order: `schemaVersion` (literal
`community-download-receipt-1`), `service`, `serviceKeyId`, `contributorId`,
`revisionId`, `artifactDigest` (hex text), `receiptId` (hex text), followed by
unsigned uint64-big-endian `issuedAt` (Unix seconds, within JavaScript's safe
integer range). `serviceKeyId` is `sha256:` plus SHA-256 of the raw 32-byte service
public key. No prehash or terminator follows. There is no expiry; issue time is
signed attribution, not permission to skip future current-authority checks.

Every download issues/signs the receipt for its requester after authorization;
same-second unchanged bindings naturally yield identical deterministic signatures.
No receipt row or popularity mutation is written. CSS-51 still owns signed
idempotent post-import acknowledgment and its G-REPUTATION policy. It must verify
current authority and receipt bindings, then atomically enforce durable unique
import facts. A receipt does not prove playback, accuracy or actual local commit.

The shared [receipt vector](../../../tests/fixtures/community/download-receipt-v1.json)
was independently generated using Node crypto and a disclosed synthetic test
seed; Workerd reproduces its signature and signing-message hash. Never provision
that seed or any other fixture as a real service key. Python consumer/verifier
implementation belongs to CSS-44/51, which must consume this same frozen vector.

## Secrets, history and backup inventory

`DOWNLOAD_RECEIPT_PRIVATE_KEY` is a separate service-only secret containing
canonical padded-base64 DER PKCS8 Ed25519 key bytes. It is unrelated to contributor
installation keys. Provision through the approved secret manager/Worker secret
binding, never chat, task records, tracked config or D1. The loader imports it
non-extractably and verifies possession against the configured active public key;
missing/mismatched configuration fails closed without logging values.

`DOWNLOAD_RECEIPT_KEYS` is public configuration: JSON array of
`{keyId,publicKey,status}`, with canonical base64 raw 32-byte public keys and
`active`, `retired` or `revoked` status. Exactly one key is active for issuance;
verification permits active/retired, never revoked. Key IDs must match public-key
hashes. Runtime sets are bounded to 16 keys; this is not permission to prune
historical verification evidence. Preserve full history in protected inventory
and supply the relevant trusted set to future acknowledgment verification.
Public keys in a downloaded response or arbitrary caller input are not a trust
root. CSS-44/51 must obtain service trust through configured origin/key history.

CSS-60/61 backups must independently protect the active service secret and retain
public verification/revocation history and configuration alongside the full D1
schema, exact BLOBs, catalog, operations, author-key history, counters and the new
`download_usage_observations`. Verification of existing no-expiry receipts needs
historical public keys, not retired private keys. Restore must not silently
reactivate a retired/revoked signing key or treat restored membership/usage as
fresh provider evidence. Destination/encryption/rotation/restore operations stay
under their existing user gates; none were performed here.

## Migration, usage and verification

0011 only adds `download_usage_observations`, a WITHOUT ROWID aggregate keyed by
contributor/UTC day/allowed-or-denied outcome. It stores occurrences, known read/
write sums, explicit unknown-observation counts and millisecond freshness. It
stores no revision, target, receipt, Discord ID, private key or library inventory.
No retention/pruning policy was invented. Before applying it to an existing
database, verify known revision, pause writes, export a recoverable backup and
validate an isolated restore before the explicitly authorized numbered upgrade.
Never wipe/stamp unknown schema. Only isolated fixtures were migrated here.

Daily request/write charging is removed for the local Docker service.
Passive request/write observations remain. Separate download observations
record available D1 metadata; missing metadata or an execution fault marks usage
unknown, not verified zero. These observations cover the handler span, excluding
native/bot work outside its wrapper and the final observation row's own mutation.
They are partial application readings, not provider billing, account-wide usage
or total database size. Telemetry/extra key-history rows still need capacity and
retention evaluation before launch.

Successful commands:

```sh
pnpm --dir community-service exec vitest run test/download-http.test.ts test/download-receipts.test.ts test/download-usage.test.ts
pnpm --dir community-service exec vitest run test/publication-storage.test.ts
pnpm --dir community-service exec vitest run
pnpm --dir community-service typecheck
WRANGLER_SEND_METRICS=false pnpm --dir community-service run build
```

Final full suite: 490 tests/74 files; 15 new tests in three files, plus extended
storage/upgrade checks. Fresh and populated starting versions 0001-0009 retain
all prior application rows through 0011; a populated 0010 signed-BLOB test also
snapshots/preserves every table. Foreign-key checks pass. Exact-byte, missing/
corrupt/length/digest, cold/warm moderation, requester/author retirement/revocation,
concurrency, cross-contributor receipts, truthful unknown usage, TTL/capacity
reductions, configuration/ingress/bot denial and transport digest/encoding pass.
Typecheck/generated types, editor diagnostics and dry-run build passed:
738.08 KiB / gzip 127.69 KiB. Wrangler's existing `@types/node` advisory remains;
no dependency was added. No hosted CPU/latency/capacity or real reverse-proxy
compression test was performed. Production payload limits, live setup, schema
upgrade and deployment remain unverified operational gates.

## Next exact actions

CSS-43: read its brief and current local import transaction tests; extract reusable
import transactions without changing local EDL/MCF behavior. CSS-44 then consumes
this bundle/vector, verifies service trust, original author signatures, bounds and
matching, and atomically saves original evidence plus receipt after local commit.
CSS-51 must separately verify/acknowledge saved receipts under approved counting
policy. CSS-60 first inventories 0011, exact BLOBs and the separate service secret/
public-key history before proposing backup/restore operations.