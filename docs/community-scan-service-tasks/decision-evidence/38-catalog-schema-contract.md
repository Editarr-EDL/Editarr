# CSS-38 catalog schema and repository handoff

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


Completed October 3, 2026, local isolated-test scope. D1 revision
`0009_catalog.sql` is additive; no existing database was migrated, no R2 object
was written, and no route or deployment was enabled.

## Representation and ownership

- `works` and `work_identifiers` store immutable typed identity. The unique
  provider/entity-type/value tuple distinguishes movie, series and episode IDs.
  Explicit numbering can generate an opaque episode work under a typed series;
  the series is never substituted for the episode work.
- `releases`, `release_work_associations`, `release_episodes` and
  `release_fingerprints` retain bounded normalized matching evidence, ordered
  episode spans and independent file/audio/video fingerprints. Filename/scene
  names are searchable hints, never globally unique identities. Additional
  contributor-asserted IDs associate a release with works, not global aliases.
- `addVerifiedIdentifier` accepts explicit verified-source/operator provenance
  and an evidence reference. It is a trusted internal helper, not proof
  verification or an authorization endpoint. Existing identifiers cannot be
  rebound, including conflict-ignore attempts; unverified alias additions fail.
- `contributions` retains immutable family ownership.
  `contribution_revisions` retains immutable author/key/release, kind/schema,
  exact artifact/manifest/envelope SHA-256 digests and byte lengths, service-only
  object key, original summary JSON, indexed channel counts and lineage bounds.
  The key must belong to the author. Retiring a key preserves historical
  authorship; the repository does not grant current authorization to that key.
- Each revision has a contributor-owned `(operation_kind, operation_id)` foreign
  key to `operations`, unique per operation. `readReservation` provides an
  indexed contributor-scoped recovery lookup. Kind strings remain caller-chosen;
  test-only kinds are not protocol policy. Existing operation cleanup must
  preserve referenced rows. Missing/foreign/reused operations roll back insertion.
- `contribution_publications` and `contribution_statistics` separate mutable
  lifecycle, etag and counters from immutable claims. Omitted channel counts
  project to zero only in indexed columns; original v1/v2 summaries stay intact.
  Findings, manifest bodies, signatures, private keys and media bytes are not
  copied into catalog rows.

## Lineage and publication

`contribution_parents` requires a published parent, matching artifact digest,
older immutable insertion sequence, lower depth and available declared parent
capacity. Edges cannot be changed or deleted. This prevents self, forward and
cyclic references without recursive graph scans. Sealing requires exact maximum
parent depth plus one, or zero for a root. Supersedes must identify an older,
published revision in the same family. SQL rechecks parents during the atomic
batch, including a parent quarantined after planning.

Initial state is `reserved`. Allowed transitions are:

```text
reserved -> object_stored | failed | expired
object_stored -> published | failed | expired
published / withdrawn / quarantined -> a different state in that group
```

Failed/expired reservations cannot reopen. Object-stored/published/moderation
states require a retained etag; it cannot be replaced, identity cannot change,
and transition timestamps cannot decrease. These are persistence mechanisms,
not moderator/author permission rules. D1 cannot establish that R2 bytes exist:
CSS-39 must verify storage before publication. Catalog times use caller-supplied
Unix seconds, distinct from enrollment/profile millisecond timestamps.

## Repository APIs

Source paths below are relative to `community-service/`:

- `src/catalog/works.ts`: `workStatements`, `ensureWork`,
  `episodeWorkStatements`, `addVerifiedIdentifier`.
- `src/catalog/releases.ts`: `releaseStatements` validates matching evidence
  and returns work/release IDs with bounded prepared statements.
- `src/contributions/repository.ts`: `revisionStatements` accepts the genuinely
  verified artifact result, family/release IDs, owned operation, timestamp and
  explicit caps. Returns IDs, immutable object key, read-query count and prepared
  statements. `reserveRevision` is a convenience batch executor, not an
  authenticated or idempotent publication service. `readReservation` is internal
  recovery metadata, not a public catalog DTO. `publicationStatement` can join
  operation finalization in an atomic batch; `transitionPublication` requires
  exactly one changed row or reports a conflict.
- `src/catalog/lookup.ts`: `identifierQuery`, `fingerprintQuery`, `episodeQuery`
  and `lookupIdentifiers`, `lookupFingerprints`, `lookupEpisodes` return only
  published metadata with bounded keys, results and queries. They do not fetch
  R2, authorize requests or classify/promote compatibility.
- `src/catalog/records.ts`: validation, byte-bounded JSON and hashes; hosted
  SQL parameter limit is a technical constant, not an upload policy default.

V2 matching evidence uses the CSS-36 schema. V1 uses available typed identifiers
and preserves the verified original summary, without invented audio tracks,
fingerprints or timing. CSS-37 frozen signed v2 vectors and the original signed
v1 fixture exercise both paths; raw schema-hostility fixtures are synthetic,
not signed submissions.

## Bounded lookup and measured limits

VALUES keys drive CROSS JOIN index searches: typed identifier uniqueness,
work-to-release association, `(channel, algorithm, digest, release_id)` fingerprint,
`(series_work_id, scheme, season, episode_number, release_id)` episode and
release-to-revision sequence indexes. Publication state excludes reserved,
failed, expired and moderated rows. Stable revision-sequence ordering and
deduplication retain only the caller's result cap. Workerd EXPLAIN tests inspect
the actual index plans, including contributor/operation recovery uniqueness.

Identifier/fingerprint batches use at most 33 three-parameter keys plus one
limit (100 binds); episode batches use 19 five-parameter keys plus one limit
(96 binds). Direct builders also reject over-limit batches. All key/query/result,
JSON/read/write/parent/depth caps are explicit caller inputs, not approved
production settings. CSS-39/41 must budget their authentication, membership,
nonce, operation and telemetry queries as well, not just catalog queries.

The [official D1 limits](https://developers.cloudflare.com/d1/platform/limits/)
reviewed for this task specify 100 parameters per statement, 50 queries per
Free-plan Worker invocation, 100 KB SQL statements and 2,000,000-byte rows/values.
The parameter cap applies per statement in a batch. Local Node SQLite accepts
101 parameters; this is not permission to exceed the hosted limit. No hosted
CPU, billing or capacity claim follows from local tests.

`node community-service/scripts/measure-catalog.mjs <count>` requires an explicit
synthetic count from 1 to 10000. It uses a new temporary SQLite database, applies
0001-0008, creates and verifies a recoverable backup, then applies 0009. It
cycles eight frozen summary/evidence shapes, ten releases per work, non-unique
scene names, one synthetic published root and imported parent links. Operations
and all catalog table/unique/secondary index pages are included via `dbstat`;
event arrays are excluded. Only its own temporary directory is removed.

| Measurement | 100 revisions + root | 1000 revisions + root |
| --- | ---: | ---: |
| Parent edges | 13 | 125 |
| Full database bytes | 720896 | 4157440 |
| Growth after 0008 backup | 323584 | 3760128 |
| Catalog/operation table bytes | 335872 | 2969600 |
| Catalog/operation index bytes | 151552 | 954368 |
| Index / table amplification | 0.4512 | 0.3214 |
| Public JSON bytes | 167213 | 1659045 |
| Table + index bytes per requested revision | 4874.24 | 3923.968 |

Page allocation, empty-table baseline and the extra root affect small-count
results. These are synthetic local Node SQLite estimates, not real-user catalog
distributions, production caps, cold Worker CPU or hosted D1 billing.

## Verification and recovery

Commands run successfully after the final schema/API change:

```sh
pnpm --dir community-service exec vitest run
pnpm --dir community-service exec tsc --noEmit
WRANGLER_SEND_METRICS=false pnpm --dir community-service run build
node community-service/scripts/measure-catalog.mjs 100
node community-service/scripts/measure-catalog.mjs 1000
```

432 tests in 61 files passed, including 27 new catalog/upgrade tests. Workerd
coverage includes foreign keys, concurrent revision/parent insertion, atomic
rollback, key/family ownership, immutable claims, hostile lineage, state/etag
guards, sealed release metadata, v1/v2 projection, positive/chunked indexed
lookup, specials and distinct numbering schemes. Fresh plus populated starting
versions 0001-0008 preserve every application row through 0009 and pass foreign-key
checks; `_cf_*` D1 implementation tables are excluded from application snapshots.
Typecheck, touched editor diagnostics and Wrangler dry-run passed. The bundle is
593.38 KiB / gzip 96.59 KiB; no deployment occurred.

Backups must include the full migration revision, old application tables,
operations, all eleven new catalog tables, immutable digest/length/object-key
records and mutable publication/statistics state. Referenced historical keys
and operations cannot be pruned as transient auth state. R2 requires a separate
recoverable inventory/object backup; restoring D1 alone is not proof of object
presence. CSS-40/60 own D1/R2 reconciliation and operator restore procedures.
No existing local/live database was touched. Apply 0009 to an existing database
only after explicit authorization and a verified recoverable backup using the
reviewed migration workflow; never reset or stamp unknown/partial schemas.

## Next exact action

CSS-39: read CSS-13/24/25/27/38 handoffs and the publication brief, inspect
`revisionStatements`, `readReservation`, shared mutation admission and passive usage APIs,
then resolve remaining G-CONTENT/G-LIMITS prerequisites before enabling upload.
CSS-25 remain partial for downstream routes. Authenticate the current active
key/contributor and membership, bind the verified artifact author, reserve
nonce/operation/catalog atomically, and use owned recovery lookup before
any retry. Preserve original exact bytes, digests and service key across R2/D1
failures; verify R2 etag before atomically publishing/completing the operation.
No upload route, retry protocol, receipt/vote counting, moderation authority,
automatic alias merge, orphan cleanup, license/launch decision or production
capacity approval is included here. CSS-41/51/56 reuse the indexes, immutable
revision identity and separate counts rather than modifying signed claims.