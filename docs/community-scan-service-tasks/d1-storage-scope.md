# D1-only community storage

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

**Historical October 3 decision; deployment superseded October 5 by
[Docker on a Mac mini with Node + SQLite](../community-service-docker.md).**
Retain the exact-BLOB/atomic-publication design, numbered schema, no-R2 scope and
per-operation validation bounds. Workers/D1 capacity and provider-export instructions below
describe the prior target, not current deployment work. CSS-66/67/68 own Node
parity, image/conditional legacy import and local capacity/independent recovery.
Current commands never adopt or stamp D1/CSS-60/61 output automatically.

Confirmed October 3, 2026: the user requested removing R2 and keeping all current
community-service persistence in D1, with only a note about potential future use.
The user also confirmed there is **no deployed R2 data** to carry over.
This supersedes the storage portions of D01, G-ENCODING, G-LIMITS and CSS-22/38/39,
retires CSS-40, and changes downstream work in CSS-42/45/57/60/61/64/68. It does not reopen
wire-format, signing, identity, license, membership or launch decisions.

## October 3 implementation and scope

Use one Worker and one D1 database per environment. Remove the `ARTIFACTS` binding
and all runtime bucket access. Catalog summaries remain indexed JSON text. Exact
signed gzip artifact bytes, original manifest bytes and detached signature bytes
are BLOB columns in `artifact_payloads`, linked to the existing revision storage
identifier. Clients keep the same upload, signature and operation-status contracts.

Publication validates first, then commits nonce, operation, catalog records,
payload bytes, byte counters, active-authority check, published state and saved
result in one D1 batch. Failure rolls back all publication records. Request-attempt
telemetry remains separate so rejected work still counts. A retry uses
the original operation ID and bytes; a completed retry returns the saved result.
The historical lifecycle labels `reserved` and `object_stored` are transitions
inside that batch. Existing pending records can complete through explicit retries;
no new schema migration, historical rewrite or recovery worker is needed.
Historical `object_key` and `object_etag` columns remain to preserve old schema
records; new etags use the artifact SHA-256 rather than a provider etag.
No new cross-store leases, orphan scans or object-copy protocol are needed.

Migration `0010_d1_artifacts.sql` is additive. It preserves all identity, operation,
catalog and accounting history. Retired resource rows remain available for audit;
current observations retain the historical request/write resource identifiers.
Passive usage reports ignore old R2 observations. No accounting ledger controls
current request admission.

The exact-BLOB design remains current, but provider capacity figures and aggregate payload admission are superseded. Migration 0015 removes capacity enforcement while retaining byte observations and legacy policy rows. Per-operation artifact/schema validation still applies. Monitor actual SQLite/WAL/index/backup growth separately; physical disk is not an application admission policy. Existing-store upgrades need explicit maintenance and a verified recoverable backup.

## Historical downstream handoff

- CSS-40 is retired. Atomic rollback and idempotency are covered by CSS-39; no
  standalone publication recovery or stale-reservation sweeper remains.
- CSS-42: authorize current membership/key/moderation state, read exact D1 BLOBs,
  verify digests and issue receipts. A cache cannot bypass those checks.
- CSS-45: retain the existing signed local outbox; test pre-send, D1 rollback,
  committed-response loss and local acknowledgment failures.
- CSS-57/68: report passive payload and request/write observations, measured SQLite
  size and hardware headroom; test physical storage failures and recovery, not user caps.
- CSS-60/61: independently export and restore one consistent D1 snapshot including
  BLOBs, catalog, operations, counters and schema revision. Verify byte digests,
  signatures and foreign keys. Protect service secrets separately.
- CSS-64: reuse bounded transient cleanup and define approved retention inside
  D1. Deletion is intentionally prohibited by current immutable-table triggers; any future reclamation needs an explicit
  data-preserving migration and safe reference/backup rules.

## Existing database upgrade procedure

Before applying numbered migrations to any existing store, identify its known
migration revision and pause writes. Export a recoverable backup; restore it into
an isolated disabled environment and verify integrity, foreign keys and existing
records before proceeding. Keep deployment/configuration metadata and separately
protected secrets. Never reset a database or stamp an unknown schema.

Although no deployed artifacts exist for this project, a private development copy
might contain old R2-backed revisions. SQL cannot reconstruct external bytes.
Inventory every revision and its exact three signed parts, independently back up
and verify those bytes, and arrange a reviewed import into D1 before cutover.
Migration 0010 preserves those references and never claims they were imported.
Do not enable such a store until every referenced payload and digest verifies.
No live R2 import tool is required by the confirmed current scope.

## Verification

After combining publication into one transaction, verification passed:

- `pnpm --dir community-service test`: 462 tests across 69 files.
- `pnpm --dir community-service exec tsc --noEmit`: passed.
- `pnpm --dir community-service run build`: Wrangler dry-run passed.
- Markdown local-link, heading, fence and task-dependency checks; `git diff --check`.

Coverage includes rollback at payload, lifecycle and result writes; capacity and
key-authority refusal; concurrent identical/conflicting requests; lost responses;
and explicit retries of older pending records. Populated 0009 and fresh/0001–0008
upgrade suites exercise 0010 with table snapshots and foreign-key checks.
The historical dry-run inventory contains former provider bindings, not current Docker admission requirements.
All database tests used isolated fixtures; no existing store or hosted resource was
changed. Backup/restore tooling and production CPU/capacity checks remain assigned
to their existing downstream tasks.

## Future R2 note

Reconsider R2 only if measured artifact growth, required individual artifact size,
or sustained download traffic makes the current single-SQLite design impractical after reasonable
bounds and retention. It is not a planned dependency. A future decision must show
why local SQLite is insufficient, compare costs and operational complexity, and specify a
versioned migration, verified backups, private access, byte-preserving transfer,
and rollback. Do not activate a bucket or paid storage automatically.
