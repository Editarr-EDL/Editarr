# CSS-39 publication and recovery contract

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Storage scope superseded October 3, 2026: [D1-only storage](../d1-storage-scope.md).
> R2 references below record historical implementation/evidence, not current requirements.


Completed October 3, 2026, in the user-approved scope: "Implement local/mock
publication with explicit limits; keep production disabled." No production
payload/CPU bound, license, deployment, live resource, personal content publication
or existing-database migration was authorized or performed.

## Modules and activation

Paths below are relative to `community-service/`:

- `src/contributions/publication-input.ts`: `PublicationLimits`,
  `publicationLimits`, `PublicationInput`, `publicationInput`.
- `src/contributions/publication.ts`: `publishArtifact`, `publicationOperation`,
   `activePublicationKey`.
- `src/contributions/storage.ts`: `publicationStorageStatement`, immutable SQLite BLOBs.
- `src/http/publication.ts`: `contributorPublication`, signed HTTP handling.
- `src/index.ts`: local dispatch behind trusted ingress and current authority.
- CSS-38 `revisionStatements`, `readReservation`, `publicationStatement` and
  D1 0009 are reused. No new migration was needed.

Non-local requests still return redacted 503 before route work. Local activation
requires D1/R2, exact HTTPS `PUBLIC_ORIGIN`, `POLICY_VERSION`, native IP/shared
trusted ingress, bot membership, enabled runtime configuration and valid `PUBLICATION_LIMITS` JSON.
No publication limits are supplied in Wrangler configuration or capabilities.
All bounds are explicit positive safe integers: `requestBytes`, `manifestBytes`,
`validationWallMs`, artifact `compressedBytes/expandedBytes/events/text/depth`,
catalog `maxParents/maxLineageDepth/maxJsonBytes/maxReadQueries/maxStatements`.
The shared auth helper retains its ordinary 8 KiB default; upload supplies its
bounded original body and explicit override. Signature envelopes remain <=256 bytes.

## Signed HTTP

`POST /v1/contributions` uses unchanged CSS-12 exact-body signatures, stable
`Idempotency-Key`, fresh nonce and a currently active key/member. No query,
content encoding, alternate authority or client storage path is accepted.
JSON has exactly `version: 1`, `contributionId`, `submissionId` and canonical
base64 `artifact`, `manifest`, `envelope` containing the original three buffers.
Transport v1 accepts strict artifact v1 or approved v2; signature v1 is unchanged.

The server recomputes the entire summary, validates taxonomy/ranges/coverage/v2
source and audio semantics, compares the manifest and verifies its detached
signature using registered D1 identity. Uploaded public keys/counts are not
authorities. Partial/unknown-provenance policy is unchanged.

Success is 200 with `Cache-Control: no-store`:

```json
{
  "version": 1,
  "state": "completed",
  "publicationState": "published",
  "result": {
    "version": 1,
    "operationId": "stable-operation-id",
    "contributionId": "family-id",
    "revisionId": "signed-submission-id",
    "artifactDigest": "sha256-of-original-gzip-bytes",
    "committedAt": 1700000030
  }
}
```

Signed `GET /v1/publication-operations/{id}` is current-key/member authorized and
contributor-scoped. It returns operation `state`, separate `publicationState`, and
the original result only while published; otherwise `result` is null. Foreign or
absent operations return 404. No Discord identity, private key, object path,
manifest body or finding array is exposed. A withdrawn/quarantined revision cannot
be republished by retry.

Redacted errors: operation/object/revision conflict, publication unavailable for
retry, first-attempt race requiring retry, nonce conflict (409); invalid publication,
artifact, detached signature or lineage (400); body/publication limit (413);
invalid/inactive request authority (401), ineligible member (403), per-operation bounds (413) and unavailable service (503).
Concrete codes are in `src/http/publication.ts`; no raw SQL/content escapes.

## State machine and retries

1. Authenticate and validate the bounded exact envelope before publication.
   Malformed signed requests are observed without mutating publication state.
2. Resolve the owned `publication` operation. Changed exact HTTP content digest
   conflicts. Every retry revalidates the original bytes, author/submission/family
   and retained digests. Signing identity is never supplied by the client.
3. Atomically consume nonce and reserve pending operation, immutable catalog
   metadata, `reserved` state, statistics and lineage.
   Failure rolls back reservation/nonce/storage admission before R2; request
   passive observations may remain partial. Concurrent first attempts reserve
   once; the loser may receive `publication_retry_required` and sign a fresh retry.
4. Conditionally create the three immutable R2 parts at the retained key. Partial
   groups stay `reserved`, invisible to published-only lookup.
5. Recheck active requester, retain artifact etag and advance to `object_stored`.
6. Atomically recheck active key/contributor, mark `published` and complete the
   operation/result. Failure in the final operation UPDATE rolls the publication
   flip back to `object_stored`. Concurrent pending retries converge to the
   original committed result without another revision/publication.

R2 success followed by D1 seal/finalization failure is recoverable at the same key.
Failed/expired states cannot reopen. Completed unchanged retries do not call R2;
they record passive request/write observations. Pending retries require fresh signature/
nonce over unchanged body. Completed retries within the signature window are
bounded result reads, never authority to repeat side effects.

Fresh reservations require the authenticated key's artifact signature. Existing
reservations may retain a retired historical signature when a currently active
key under the same contributor authorizes recovery. Revoked/untrusted history
fails. Retirement during R2 leaves recoverable invisible state, not old-key authority.

## R2 Inventory

| Physical key | Exact bytes | Content type |
| --- | --- | --- |
| `artifacts/<service-UUID>` retained base | original gzip artifact | `application/gzip` |
| base + `.manifest` | original manifest JSON | `application/json` |
| base + `.signature` | original detached envelope JSON | `application/json` |

No recompression/reserialization, public path, transport content-encoding or
client-selected key is introduced. Separate objects avoid R2 custom-metadata
limits without a new signed bundle format. `onlyIf.etagDoesNotMatch = '*'` makes
writes create-only. Existing parts must match bounded length/SHA-256; conflicts
are never overwritten. The etag is retained only after all three parts succeed.

CSS-40/60 must reconcile/back up all three parts as a group with full D1 0009,
operation/result/state and historical keys. Absence of D1 etag does not make a
partial group disposable. Never prune referenced operations/keys or infer R2
presence from restored D1 alone. Cleanup/deletion/backup leases are not implemented
here. Existing-store migrations need authorization and verified recoverable backup.

## Passive Usage

The former cloud-admission policy is retired. Valid publication and owned reads are not gated on user counters or recorded activity. Preserve passive unknown-aware observations, bounded validation and atomic authority/replay/payload/result commits.

## Verification

Final full Worker suite: 459 tests in 67 files, including 27 new tests in six
publication files. `pnpm --dir community-service exec vitest run`,
`pnpm --dir community-service exec tsc --noEmit`, dry-run `run build` with
`WRANGLER_SEND_METRICS=false`, editor diagnostics and `git diff --check` passed.
Dry-run bundle: 688.16 KiB / gzip 117.07 KiB. Existing preserving upgrade tests
remain passing. No live Discord, hosted R2/D1, deployment or personal data access.

Real isolated D1/R2 tests cover each part failure, R2 success then D1 seal/finalize
failure, an actual SQL abort after publication UPDATE within the final batch,
concurrent first/pending retries, object conflicts, false genuinely signed counts,
exact v1/v2 bytes, foreign-owner HTTP/status access, limits/membership/retirement,
storage-failure rollback, malformed authenticated envelopes, high recorded activity and telemetry faults.

Full signed v2 validation uses generated public notes, unique event IDs, actual
gzip/manifest/signature, complete schema/summary checks, SHA-256 and Ed25519.
Five local samples are recorded in test metadata (`--reporter=json`):

| Events | Compressed | Expanded | Manifest | Envelope | Median / sample p95 wall ms |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 1952 | 35704 | 1716 | 154 | 2 / 4 |
| 2500 | 16323 | 833104 | 1720 | 154 | 27 / 29 |

UUID/compression sizes/coarse timing vary slightly. This is local Workerd wall
time, not hosted CPU, full HTTP/R2 latency, memory or representative user notes.
The larger case uses a synthetic 4 MiB expansion cap, exceeds the unapproved
512 KiB example and does not establish the 10 ms free-plan CPU budget.
`validationWallMs` is a cooperative post-validation check before reservation/R2,
not preemption. Byte/event/text/depth caps bound work without skipping validation.
Hosted CPU and whole-route D1 query feasibility remain production approval gates.

## Next Action

CSS-40: read this contract/reconciliation brief and inspect reservation/finalization,
all physical keys and backup/operation references. Propose explicit stale-time,
lease/cursor/batch/query/R2 bounds and safe deletion predicates before maintenance.
Add versioned preserving schema changes if needed; authorize and verify backup
before any existing-store application. Do not delete by age/missing etag alone.
CSS-45 must journal exact body/buffers/stable ID, sign pending retries freshly and
use owned status. CSS-60 backs up all three parts and historical keys. CSS-25
remain partial for other downstream routes; production limits/CPU, license,
live setup and deployment remain open.