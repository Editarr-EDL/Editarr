# CSS-41 authenticated catalog lookup

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Completed October 3, 2026, in local/isolated-test scope. Production dispatch stays
disabled, `LOOKUP_LIMITS` has no Wrangler default, and no existing database,
personal media, Discord resource or deployment was touched. No migration or new
persistent state is introduced. This uses the current [D1-only scope](../d1-storage-scope.md),
CSS-25/27 authority checks, CSS-36 approved compatibility and CSS-38/39 records.

## Modules and activation

Paths are relative to `community-service/`:

- `src/catalog/search-input.ts`: strict request/configuration schemas and error codes.
- `src/catalog/search-repository.ts`: indexed multi-target query plans, bounded
  materialization, published-only rows and batched current public state.
- `src/catalog/search.ts`: DTO projection, compatibility sorting, cursors and bounded
  isolate-local metadata cache.
- `src/http/catalog.ts`: bounded exact-body authentication,
  passive observations, final active-authority check and fresh telemetry.
- `src/index.ts` / `src/config.ts`: local-only dispatch and explicit `LOOKUP_LIMITS`.
- `src/catalog/lookup.ts`: existing APIs retain compatibility; optional
  `beforeSequence` adds exclusive sequence pagination. Identifier/fingerprint
  builders now accept 32 keys, episode builders 19, reserving two SQL parameters.

Activation requires local environment, D1, exact HTTPS `PUBLIC_ORIGIN`,
`POLICY_VERSION`, explicit valid `LOOKUP_LIMITS`, trusted native admission,
enabled protected runtime configuration and configured bot membership. Production remains disabled
before route work. No downstream client, download, receipt, vote, moderation
authority, ranking formula or UI is implemented.

## HTTP and DTOs

Both routes are signed `POST` requests with `application/json`, exact original
body bytes and CSS-12 signatures. Queries, alternative authorities and content
encoding are rejected. They are logical reads: an operation ID in the signed
POST is not stored, and bounded read replay does not consume a mutation nonce.
Each authenticated attempt, including malformed JSON, records passive observations.
Every response has `Cache-Control: no-store`; there is no public artifact URL.

`POST /v1/availability/batch` request:

```json
{"version":1,"items":[{"id":"opaque-item-id","evidence":{"media":{"entity_type":"movie","identifiers":[{"provider":"tmdb","entity_type":"movie","value":"123","provenance":"user_asserted"}],"release":{}}}}],"refresh":false}
```

`evidence` is the full CSS-36 input and may include file and independent stream
fingerprints, release timing and selected/analyzed audio. IDs must be unique
opaque ASCII tokens, not paths. Output is `{version:1, policyVersion, items}`.
Each item has `id`, `status`, `candidateCount`, `truncated`, and `channels`.
For `status:"ok"`, channels has audio/video counts for each approved band:
`exact_file`, `strong_fingerprint`, `release_match`, `possible_cut`, `title_only`,
`incompatible`. Only candidates with findings in that channel contribute to its
counts. Counts are candidates, not events. `truncated:true` means a bounded lower
bound, not a complete catalog count. A target with no searchable keys has
`status:"missing_identity"`, null count/channels, and does not suppress valid
items. Database, service and membership failures are never successful empty items.

`POST /v1/matches` takes `{version:1, evidence, pageSize, cursor:null, refresh:false}`.
Output is `{version:1, policyVersion, candidates, nextCursor}`. Each candidate has:

- `revisionId`, `artifactDigest`, `artifactSchema`, `releaseId`, `catalogCreatedAt`
  (catalog insertion Unix seconds, not a fabricated original artifact timestamp).
- `attribution:{contributorId,displayName}` using the current public name.
- `summary`: allowlisted immutable original summary fields, including type/name,
  release/scanner versions, channel/source/taxonomy event counts, coverage, and
  available v2 audio/source state. Missing legacy metadata stays missing.
- `evidence`: immutable release evidence; `compatibility`: the complete CSS-36
  per-channel bands, reasons/conflicts/missing fields, eligibility, policy version,
  and `selected:false`.
- `statistics:{imports,likes,dislikes}`: current aggregate counts only.
- `ranking:null`: extension point for CSS-53, not an invented reputation score.

No findings, manifests, signatures, storage paths, Discord IDs, requester-specific
vote/import histories or receipts enter these DTOs. Current indexed
summaries do not contain all model/settings provenance or original creation time;
this route does not decompress BLOBs or invent those fields. Full immutable
evidence belongs to CSS-42/44 downloads.

## Bounds and pagination

All limits are caller configuration, not production defaults: `requestBytes`,
`responseBytes`, `maxItems`, `maxKeys`, `maxQueries`, `maxCandidates`, `maxRows`,
`maxPageSize`, `cacheEntries`, `cacheBytes`, `positiveTtlSeconds`,
`negativeTtlSeconds`. Positive safe integers are required; negative TTL is shorter
than positive TTL, page size is no larger than candidate capacity, and maxRows
must exceed candidate capacity. Technical ceilings are 50 items, 12 catalog
queries, 96 candidates/target, 500 materialized target/revision pairs and 128
cache entries. These are ceilings, not approved operational settings.

VALUES batches carry target IDs and drive indexed typed identifier, numbered
episode and file/audio/video fingerprint searches. Duplicate keys and revisions
are deduplicated. Movie/series/episode namespaces and episode numbering schemes
remain distinct. Four reserved parameters leave 24 four-value keys or 16 six-value
episode keys per statement (at most 100 binds). A pre-window candidate sentinel
caps SQL materialization; a batch above maxRows fails as a whole rather than
silently starving later targets. Current state uses at most five 100-ID queries,
not per-item name/statistics/moderation reads. The 50-item one-ID test uses three
search statements. Auxiliary authorization, membership and passive observations
work is separate from `maxQueries` and retained below the Free 50-query ceiling.
Indexed rows examined are not a hard provider read/CPU budget; representative
large-catalog hosted costs remain CSS-68 work.

For matches, the complete bounded candidate set is sorted by approved video band,
audio band and ASCII revision ID before paging. A set exceeding maxCandidates
returns 413 `lookup_limit`, not a misleading partial globally ranked page.
Availability can instead expose its explicit truncated lower bound.

The base64 JSON cursor has exactly `version:"community-search-1"`, `key`,
`ceiling` and `after:[videoBandIndex,audioBandIndex,revisionId]`. The key binds
canonical target evidence, schema list, compatibility/service policy and candidate
bounds. Ceiling is the initial maximum insertion sequence. Newer insertions stay
outside that traversal; keyset ordering avoids offset skips when prior results
are withdrawn. Current moderation always overrides a cursor. Changed target or
policy, malformed positions and impossible ceilings fail as `invalid_cursor`.
Cursors are validated positions, not credentials or authenticated snapshots;
membership/key checks still run. Reopening an older publication may change the
currently visible set, and refresh is available; there is no historical visibility
guarantee across moderation changes.

## Cache and failure behavior

Only immutable public catalog rows are cached, never HTTP responses. Keys include
canonical work/release/file/stream evidence, schemas, matching/service policy,
candidate bounds and insertion ceiling. New revisions change the ceiling. Cache
entries/total serialized bytes/TTLs are explicitly bounded; negatives expire
sooner, and `refresh:true` bypasses them. There is no durable library inventory,
target logging or background library crawl.

The cache is scoped to the original D1 binding, not the fresh accounting proxy
created on each Worker request. On every hit the HTTP handler authenticates the
currently active requester and membership, records passive observations, re-reads
current publication states, public names and aggregate statistics, and checks
requester activity again before returning. Withdrawal/quarantine cannot be served
from old cached rows. Telemetry failure adds a fresh request-local
`Community-Usage-Status: unavailable`; that header never enters cache entries.

Errors remain redacted: invalid request/cursor or missing match identity (400),
inactive/invalid key/signature (401), nonmember (403), method (405), request/result/
query/candidate bound (413), native , unavailable policy,
provider/storage/corrupt server metadata (503). The cross-language-ready
[contract fixture](../../../tests/fixtures/community/catalog-lookup-v1.json)
freezes empty, unavailable, membership and offline distinctions. An offline
transport has no HTTP response and must not become zero matches in CSS-48.

## Verification and local baseline

Final successful commands:

```sh
pnpm --dir community-service exec vitest run test/catalog-search.test.ts test/catalog-http.test.ts
pnpm --dir community-service exec vitest run
pnpm --dir community-service typecheck
WRANGLER_SEND_METRICS=false pnpm --dir community-service run build
pnpm --dir community-service exec vitest run test/catalog-http.test.ts --reporter=json
```

475 tests in 71 files passed; 13 new tests cover SQL plans/bounds, per-item
identity outcomes, published-only state, stable pagination, malformed/changed
cursors, uncertain channel separation, privacy, cross-contributor private-header
isolation, negative expiry/refresh, revoked keys, nonmembers, provider/database
outages, native admission, real bot/Worker cache dispatch and activation gates.
Generated types/typecheck and dry-run build passed (717.80 KiB / gzip 123.77 KiB).
Wrangler prints its existing `@types/node` advisory; no dependency was added.

Five cold and five warm signed 50-item requests over one currently published
synthetic revision were measured in isolated local Workerd. Other withdrawn/
quarantined revisions and two contributors remain in the test database.
The measured handler uses a fake eligible membership hook; native/bot admission
is tested separately, not included in these figures. Test metadata retains the
individual measurements in Vitest's JSON reporter.

| Mode | Median wall ms | Maximum of five ms | D1 statements | Local rows read | Local rows written | Response bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cold | 9 | 10 | 16 | 1351 | 8 | 15614 |
| Warm | 5 | 11 | 13 | 45 | 8 | 15614 |

Passive observations measure known logical writes per read;
available actual mutation metadata is separately observed. Native and attributable
usage can overlap, as in the existing partial accounting. These are not hosted
CPU, cold-start, billing, representative accuracy or free-tier feasibility claims.
Production limits/CPU, live bot setup and deployment remain gated. No migration
or backup inventory change is needed for transient cache state.

## Next exact actions

CSS-42: read its brief, the D1-only scope and CSS-13/39 handoffs; implement current
key/member/moderation-authorized exact D1 BLOB reads, digest verification and
contributor-bound receipts. A catalog cursor or cache hit never grants download
permission. Do not enable production or assume legacy payload references exist.

CSS-48/49: consume these DTOs and the fixture, preserve local discovery and prior
results across offline/service/errors, display null identity outcomes and truncated
counts honestly, and keep every candidate unselected. CSS-53 adds approved quality
ordering only within compatibility bands; it must version any changed cursor order.