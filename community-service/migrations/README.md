# Community SQLite migrations

## Current Schema

The current Docker schema is **17**. Supported starting versions **1 through 16**
upgrade through the offline runtime runner with an independently verified,
recoverable pre-upgrade snapshot. Startup never upgrades or initializes a store.
The October 7 local-service decision retires all user usage admission. Historical
SQL below is retained unchanged for preserving upgrades and old archive recovery;
its former policies are not current application behavior.

`0015_local_usage.sql` removes accounting/name-change/cumulative-storage admission
triggers while preserving identity, keys, operations, all original artifact bytes,
legacy accounting rows and byte observations. Payload counters still commit
atomically but do not reject uploads. Per-operation artifact and schema bounds,
immutable bytes and current authority checks remain.

`0016_local_reports.sql` preserves recorded historical report allowance values in
`legacy_report_allowances`, removes those columns from active reports and retires
their admission trigger. Reports retain identity, reason, note, state and time;
only open-to-resolved updates are allowed. Eligibility, private immutable notes,
one-open-report per contributor/revision and moderation audit remain protected.
Report/operator configuration now contains only body/query bounds.

`0017_generic_provenance.sql` removes Radarr and Sonarr provenance from catalog
copies of match evidence. In `releases.evidence_json` and the `matching` object of
`contribution_revisions.summary_json`, the duration sources `radarr_catalog_minutes`
and `sonarr_catalog_minutes` become `jellyfin_ticks`, and identifier provenance in
the `radarr.` or `sonarr.` namespace becomes `source_metadata`. Seconds, precision,
row IDs, sequences and every other row stay unchanged. It drops and recreates
`guard_release_update` and `guard_revision_update` around the rewrite. Signed
artifact BLOBs are never rewritten; readers of those stored bytes apply the same
mapping. New requests that carry either Arr value fail ordinary validation.

No existing database was changed. To adopt schema 17, explicitly stop the service
and use the [verified-backup-first upgrade procedure](../../docs/community-service-docker.md#persistence-and-recovery).
Never reset a store, overwrite a backup or stamp an unknown schema. Current
snapshots contain all tables, including historical data; legacy encrypted archive
validation remains pinned to its original schema and is not automatic adoption.

## Historical Migrations

`0001_identity_operations.sql` creates the private Discord-to-contributor link,
installations, historical public signing keys, enrollment challenges, nonce
reservations and durable operation IDs. It does not contain private signing keys
or provider tokens. Use the Docker guide's explicit offline maintenance commands
for initialization and verified-backup-first upgrades. Startup never migrates or
initializes a database. Node tests apply the same SQL to disposable file-backed SQLite.
See the [runtime migration and recovery procedure](../../docs/community-service-docker.md#persistence-and-recovery).
Historical SQL filenames and stored accounting labels remain unchanged; they are
not provider runtime dependencies. Existing legacy stores require separately reviewed
conversion, not automatic adoption or schema stamping.

`0002_budget_ledger.sql` adds policy/caps, global and contributor counters,
reservations and permanent settled-ID tombstones. It seeds no policy or limits
and is not wired into routes. Its provider-verified reservation prototype is
retained for schema compatibility.
Do not treat these unused tables as current admission or provider-usage totals.

`0003_quotas.sql` records the former accounting schema. Its tables/rows remain
historical compatibility data, but 0015 removes enforcement and current routes
never charge them. Restored counters have unknown provenance and are not provider
billing readings. No public route is enabled by applying a migration.

`0004_request_admission.sql` binds new nonce records to the contributor who
owns the active signing key, and rejects conflicting operation digests before
insertion. Apply only as a versioned upgrade after a recoverable backup of an
existing database; the Node upgrade tests preserve earlier data. It does
not enroll users or authorize requests without the service signature checks.

`0005_discord_oauth.sql` adds expiring, browser-bound, single-use Discord OAuth
state. The callback creates a separate short-lived entry in `enrollment_states`
only after checking the authenticated Discord user and guild membership. Bounded
cleanup is available through `pruneDiscordOAuth`; no Discord token is stored.
Fresh and populated starting versions 0001 through 0004 are tested in separate
Node databases without resets or migration rewrites. Browser/grant timestamps
are milliseconds; request nonce expiry is Unix seconds. Authentication cleanup
converts units explicitly. Before upgrading any existing store, verify a
recoverable backup; synthetic upgrade fixtures do not fulfill that requirement.

Subsequent schema changes require a new numbered migration. Do not edit an
already-applied revision or reset a persistent database to upgrade it.

`0006_oauth_usage.sql` adds bounded anonymous daily broker request/write counters
and an aggregate-read index on contributor observations. The WITHOUT ROWID table
has one data row per resource/day; accounting includes its own single-row write.
It records no IP, OAuth/account identity, tokens or secrets. Include it and its
schema revision in full SQLite backups. Restored observations remain partial with
unknown provenance, not provider billing totals. Fresh plus populated 0001-0005
upgrades preserve all prior identity/moderation/key/grant/nonce/operation data,
historical accounting and confirmed pending browser state. Hourly cleanup
prunes bounded expired state and old anonymous detail, never durable operations.
No existing store was upgraded: applying 0006 still requires a verified
recoverable backup and explicit authorization for that migration run.

`0007_membership.sql` adds continuing Discord evidence and shared refresh
leases/cooldowns. All timestamps are Unix seconds; eligible/ineligible evidence
is valid for less than 900 seconds. The authority digest changes with guild or
bot credentials; tokens are never stored in these tables. Full SQLite backups must
include both tables and migration revision; secrets are managed separately.
Restoring evidence does not constitute fresh verification and may preserve only
its original remaining freshness. Isolated restores must not contact production
Discord; changing credentials invalidates old authority. Hourly bounded cleanup
retains fresh evidence, active leases and cooldowns. Fresh and populated versions
0001-0006 upgrade through 0007 without losing prior data. No existing store was
upgraded; verify a recoverable backup and explicitly authorize the migration
before applying it. Never reset or silently stamp a database to adopt this schema.

`0008_enrollment_profiles.sql` adds backend confirmation hashes to OAuth/grants,
current normalized unique-name keys, monotonic profile revision and rolling
name-change events. Secret values are not stored in SQLite. Registration/key/nonce
and rename/nonce/operation/event/name are atomic in application batches.
Hash/grant/profile/name-event times are milliseconds (membership/nonces remain
seconds). Include new columns/events and schema revision in SQLite backups; local
pending secrets are local private identity state, not a hosted provider credential.
Hourly cleanup deletes at most 1000 expired name-change events; restores preserve
historical events and never verify membership by themselves. Hourly admission is retired.

Fresh and populated versions 0001-0007 are tested through 0008, including provider
lease/cooldown preservation. Legacy ASCII names backfill trimmed lowercase keys.
Existing normalized collisions or non-ASCII names requiring application-side
Unicode normalization stop the upgrade for operator review; no name, identity,
moderation or key is silently rewritten/deleted. Use the reviewed transactional
migration runner and a verified recoverable backup; a failed raw SQL probe may
have added columns before its guard fails and is not authorization to stamp or
rerun an unknown partial schema. Restore/recover through the migration/backup
workflow, never a reset.

`0009_catalog.sql` adds `works`, `work_identifiers`, `releases`,
`release_work_associations`, `release_episodes`, `release_fingerprints`,
`contributions`, `contribution_revisions`, `contribution_publications`,
`contribution_statistics` and `contribution_parents`. Typed identifiers and
verified aliases are protected; filenames remain non-unique hints. Immutable
authorship, historical key ownership, exact digests/byte lengths/object keys and
bounded older-parent lineage are separate from mutable lifecycle/counts. Each
revision references one contributor-owned durable operation; referenced keys
and operations must not be pruned. Catalog timestamps are Unix seconds.

Fresh and populated starting versions 0001-0008 preserve every application row
through 0009 and 0010 in isolated Node upgrade tests. Referenced operations and
historical keys are durable data and must be included in backups.

`0010_d1_artifacts.sql` adds `artifact_payloads`, `artifact_storage_policy` and
`artifact_storage_usage`. Payloads contain the original gzip, manifest and signature
BLOBs; metadata constraints and immutable triggers prevent replacement. Storage
counters and final publication state commit in the same transaction as the BLOBs.
The inherited combined-payload bound remains per-operation validation. Aggregate
payload admission is retired by 0015; prior policy values remain historical data.
Retired resource counters/caps remain preserved for audit, but new R2 charges and
observations are rejected and omitted from active reporting. Original migrations
are unchanged; never rewrite migration history to remove those legacy columns.

No deployed R2 data exists for the current project. A development copy containing
old object references requires independently backed-up, verified exact bytes and
an explicit backfill before cutover; SQL alone does not copy external content.
Do not serve that database until all referenced payloads have been verified.
New publication transitions fail if their SQLite payload is missing.

Before upgrading any existing database, identify the supported migration revision,
pause writes, export a backup and verify an isolated restore (integrity, foreign
keys, row inventory and artifact digests/signatures). Include all BLOBs, operations,
historical keys, accounting and migration state; protect service secrets separately.
Only then apply numbered migrations through the reviewed migration runner. Never
wipe data, silently stamp an unknown schema, or use reset as a migration strategy.
No existing database was modified during this storage change.

Publication now creates its nonce, operation and catalog records in the same SQLite
batch as payloads, counters and the published result. The existing lifecycle states
are transitions within this transaction. This runtime simplification requires no
new schema migration and does not rewrite historical pending records. Such records
remain compatible with explicit retries; new failures leave no publication to sweep.

`0011_download_usage.sql` adds daily `download_usage_observations` keyed by
contributor, UTC day and allowed/denied outcome. It preserves all prior tables and
records known handler read/write sums, explicit unknown-observation counts and
freshness, never receipt contents, revision/target history, Discord IDs or keys.
These are partial application observations, not provider billing or restored
baseline proof. No retention/pruning is introduced. Include the new table and
migration revision in full SQLite backups. Fresh/populated 0001-0009 upgrades and a
populated signed-BLOB 0010 fixture preserve every application row through 0011.
Only isolated fixtures were upgraded. Applying it to any existing store still
requires explicit authorization and a verified recoverable backup/isolated restore.
Service receipt keys are managed/backed up separately, never stored in this table.

`0012_import_receipts.sql` adds immutable contributor/revision import facts and
daily unknown-aware import observations. Only non-self facts increment imports;
retention must preserve durable deduplication facts. Full backups include both
tables and associated operations/statistics.

`0013_votes.sql` adds one mutable `revision_votes` row per contributor/immutable
revision, referencing its durable import fact. Neutral is explicit; insert/update
eligibility guards fence active requester ownership, counted non-self imports and
published revision state. Triggers atomically adjust likes/dislikes on insert,
change and operator deletion. Access loss does not delete votes. Indexed bounded
reconciliation can rebuild totals without altering imports. Separate daily
`vote_usage_observations` retains known/null handler costs, not voting histories.
Full SQLite backups must include both tables, vote operations, statistics, referenced
import facts and historical keys. Retain vote and retry identity.

Isolated fresh/populated supported 0001-0012 upgrade checks preserve every prior
application row, including signed artifact BLOBs and import facts/observations.
No existing store was migrated. Before applying either migration to an existing
store, authorize the upgrade and verify a recoverable full backup/isolated restore
with integrity, foreign keys, row inventory and exact signed bytes. No reset or
unknown-schema stamping is allowed. Production remains disabled.
