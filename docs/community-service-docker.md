# Community service on a Mac mini

The community service runs in Docker with Node 24 LTS and SQLite. The service
tests use the same SQLite adapter as the Docker image.

This image contains the community API and its operator UI, not the Python scanner,
local Editarr UI, AI models, media files or personal configuration. All original
gzip artifacts, manifests and signatures remain BLOBs in the same database as
publication state. There is no object store or automatic data transfer.

## Build and start disabled

Docker Desktop or a compatible Docker engine and Compose are required. Run from
the repository root:

```sh
docker compose -f community-service/compose.yaml build
docker compose -f community-service/compose.yaml run --rm --no-deps maintenance init --confirm
docker compose -f community-service/compose.yaml up -d
curl --fail http://127.0.0.1:8787/health
```

Initialization only creates a new database. It refuses an existing path, even an
empty or unknown database. Do not rerun initialization to recover or upgrade.
Startup requires a known current schema and never initializes or migrates it.

The checked-in configuration is disabled: health returns 200, while readiness,
contributor routes and operator routes return 503. This is intentional, not a
publicly activated service. `COMMUNITY_PORT` selects a different loopback port.
`/health` checks process liveness; `/ready` requires validated, enabled startup,
an accessible current-schema database. Activation is controlled only by the
protected runtime configuration, not historical accounting policy rows.

The image uses a multiarchitecture official Debian/Node base. The ARM64 image
has been tested on Apple Silicon. Intel hosts require their own validation.
Node and pnpm are contained in the image.

## Configure before activation

Set `COMMUNITY_CONFIG_FILE` and `COMMUNITY_SECRETS_FILE` to absolute paths to
operator-owned JSON files outside the checkout. Compose mounts them read-only;
secrets are not environment values, image layers or command arguments. Keep the
host parent directory private and ensure Docker's UID 1000 can read these files;
ownership mapping varies between Docker engines. Check readability without
printing contents. Never commit actual configuration or credentials.

The configuration has exactly four fields:

```json
{
  "enabled": false,
  "trustedTunnel": false,
  "requestBytes": 8192,
  "variables": {}
}
```

All `variables` values are strings. Supply the existing service contract values:

- `PUBLIC_ORIGIN`, `POLICY_VERSION`, `DISCORD_CLIENT_ID`, `DISCORD_GUILD_ID`.
- `ACCESS_ISSUER`, `ACCESS_AUDIENCE`, `OPERATOR_ORIGIN`, `OPERATOR_SUBJECTS`.
- `SUBMISSION_LIMITS`, `VOTE_LIMITS`, `REPORT_LIMITS`, `MODERATION_LIMITS`,
  `DOWNLOAD_RECEIPT_KEYS`.
- `MASTER_POLICY`, with every key of contract §8.1. Startup refuses a missing
  or invalid value.

`VOTE_LIMITS` may add `votesPerHour` (default 120 votes per contributor per hour).
The revision-era `PUBLICATION_LIMITS`, `LOOKUP_LIMITS`, `DOWNLOAD_LIMITS` and
`IMPORT_LIMITS` variables were removed with their routes; configuration that still
names them is refused.

Structured variables are JSON serialized into strings, as in the existing binding
contract. Limits and the receipt key ring are validated with the shared service
validators; no test limits or fake credentials become production defaults.
The separate secrets JSON contains only string values for `DISCORD_CLIENT_SECRET`,
`DISCORD_BOT_TOKEN` and `DOWNLOAD_RECEIPT_PRIVATE_KEY`. The private receipt signer
must match its configured public key ring.

Use a stable HTTPS origin behind a protected Cloudflare Tunnel. Preserve the
original Host and configure Discord's callback as
`PUBLIC_ORIGIN/v1/discord/callback`. Protect `/admin` and `/admin/*` with Cloudflare
Access, separately from contributor authentication. The application still verifies
the Access JWT, exact subject allowlist, origin and mutation CSRF requirements.
Do not place a blanket Access login requirement on the signed contributor API.

`trustedTunnel: true` trusts `CF-Connecting-IP` only because the origin listener is
restricted to the tunnel/operator machine. It is not cryptographic proof of the
sender. Never expose this listener to a LAN or internet port, and never mount the
Docker socket. Using a different ingress requires a separately reviewed trust
adapter. Provisioning a tunnel, public hostname, Access policy or actual Discord
credentials is separate from installing the image.

After reviewing launch configuration and validation bounds, set both `enabled`
and `trustedTunnel` to true in the protected configuration
and recreate the container with `docker compose -f community-service/compose.yaml
up -d --force-recreate`. Set `enabled` to false and recreate to disable service
without erasing data. There is no maintenance `policy` command.

The service does not enforce daily
request/write ceilings, cumulative global/contributor payload caps, usage-pressure
pauses, IP/shared request buckets, hourly name-change caps, daily report counts or
maximum-open-report allowances. Usage and payload-byte observations are passive,
never admission controls. Historical `worker_requests` / `d1_writes` labels denote
observations, not Cloudflare charges. SQLite reports logical changes including
triggers; scanned-row counts and restored provenance remain unknown.

Signature, replay, membership, Access/CSRF, immutable evidence, one-open-report per
contributor and target, and provider cooldown protections remain. Configured body,
artifact expansion, schema, query and page bounds protect individual operations.
The `MASTER_POLICY` per-contributor submission, master-check and per-installation
sync limits and the stored-submission byte caps in `artifact_storage_policy` (350 MB
global, 7 MB per contributor) protect the shared store. Docker CPU/memory and physical disk
capacity remain operational constraints. Capacity tests must use isolated storage.

## Persistence and recovery

Compose uses separate named volumes for `/data`, `/backups` and `/online-backups`.
The running API mounts data and the online-backup volume only; the offline
maintenance service mounts data and `/backups` without service secrets or a
listener, so the API can never read or prune offline snapshots. Rebuilding or
replacing the image does not delete any of them.
Never use `docker compose down -v` as
an upgrade or recovery operation. Do not move a live SQLite database to SMB/NFS
or a cloud-synchronized directory.

The runtime uses WAL, FULL synchronous durability and SQLite's exclusive OS lock.
Only one process serves the database; a second writer is refused. The lock is
released after a crash without stale lock-file removal. Restart is automatic,
but this is not a multi-host failover or high-availability deployment.

Offline backups and upgrades require a short maintenance stop (scheduled online
backups do not; see [Online backups](#online-backups)). Back up to a new
destination; existing files are never overwritten:

```sh
docker compose -f community-service/compose.yaml stop community
docker compose -f community-service/compose.yaml run --rm --no-deps maintenance backup --confirm --destination /backups/snapshot-2026-10-05.sqlite
docker compose -f community-service/compose.yaml up -d
```

The snapshot is checked against the known schema, integrity, foreign keys and
streamed per-table row/BLOB fingerprints. The command does not mark an unverified
copy complete. Snapshots are private but plaintext: encrypt them before off-device
transfer, protect configuration and signing secrets separately, and keep the
decryption key independent. The backup volume on the same Mac is not disaster
recovery. Keep independent encrypted copies according to the deployment’s
recovery requirements. Manual snapshots have no automatic pruning or schedule.

### Online backups

The operator console's **Backups** page configures scheduled backups of the running
service. They are confined to `COMMUNITY_BACKUP_ROOT`, which Compose sets to
`/online-backups` (the `community-online-backups` volume). Without that variable,
backups are disabled and the API reports `root: null`. The root must be an absolute,
existing directory; it is resolved with `realpath` once, and every destination,
backup file and staged copy must resolve inside it. A destination is a path relative
to the root (absolute paths inside the root are accepted and stored relative); `..`,
hidden names, any symlinked component and the reserved `staged` directory are
refused. Directories are created `0700` and files `0600`.

- **Schedule.** `hourly` runs at the minute of the configured UTC time, `every_6h`
  at that time plus multiples of six hours, `daily` at that time, and `weekly` on
  Sundays (UTC). The maintenance loop checks every minute. A slot that passed before
  the configuration was saved never fires; a slot missed while the service was
  stopped runs once after restart. Only one backup runs at a time; a manual run
  during another answers `409 backup_running`. Runs left `running` by a stopped
  process are recorded as failed with `interrupted`.
- **Snapshot.** The SQLite online backup API copies the database through the
  serving connection in small steps, so the event loop keeps serving requests and
  writes made during the copy are included in one transaction-consistent snapshot.
  The copy is switched to rollback journaling, written as a hidden partial file and
  only renamed and fsynced after verification.
- **Verification.** A worker thread runs the offline store checks on the copy:
  `community-master-1` lineage, exact known schema version, and (unless disabled in
  the UI) `PRAGMA integrity_check` and `foreign_key_check`. The SHA-256 is recorded.
  Failures keep no file and record an error code (`snapshot_failed`,
  `verification_failed`, `integrity_failed`, `destination_unwritable`,
  `destination_full`, `destination_invalid`).
- **Retention.** After each verified backup, the newest `keep_last` verified backups
  and the newest backup of each of the newest `keep_weekly` UTC weeks (Monday start)
  are kept; the newest verified backup is never pruned. Only files recorded for
  online runs inside the root are deleted.
- **Cleanup.** While no run is active, the minute check marks runs left `running` by
  a stopped process as `interrupted` and removes their hidden
  `.community-…sqlite.partial` files from the root, the configured destination and
  (when no stage is in progress) `staged/`. Only that exact naming is removed, never
  through symlinks. Failed and pruned run history is kept for 90 days and at most
  200 rows; verified runs are never removed from history.
- **Audit.** Saving configuration, starting a manual run and staging write
  `moderation_actions` rows with resource `backup`. Every mutation needs the operator
  Access session and the CSRF headers used by other operator actions.

Download a verified backup from the console for off-device encryption and storage;
it is streamed with `Cache-Control: no-store` and an `X-Backup-Sha256` header.
Online backups on the same Mac are not disaster recovery.

#### Restore from an online backup

Restore stays offline. **Stage for restore** copies the backup to
`/online-backups/staged/`, re-checks its SHA-256, schema and integrity, and returns
the exact commands. Only the newest staged copy is kept: staging another backup
removes older staged copies, and one stage runs at a time. For example:

```sh
docker compose -f community-service/compose.yaml stop community
COMMUNITY_RESTORE_VOLUME=editarr-community-restore-1a2b3c4d docker compose -f community-service/compose.yaml run --rm --no-deps restore --confirm --source /online-backups/staged/community-20261125T033000Z-1a2b3c4d.sqlite --sha256 HEX
```

The `restore` maintenance service has no network, secrets or listener and mounts the
online-backup volume read-only. `manage.mjs restore` refuses an existing database,
verifies the source's SHA-256 and schema, copies it to `/data/community.sqlite` in
the named new volume (`0600`), re-hashes the copy and opens it with the startup
checks; a failed copy is removed. It prints `community_restore_complete
schemaVersion=N`, plus `upgrade_required` for an older `community-master-1` version
(then run the backup-first `upgrade` in that volume). Then follow
[Isolated snapshot restore](#isolated-snapshot-restore) from the Compose override
step, using the printed volume name, to start a disabled deployment and verify it
before cutover.

### Retention And Offline Preview

Policy community-retain-first-1 retains exact signed submission BLOBs, master
revisions, identity/public-key history, durable operations, download receipts, votes,
reports and moderation audit indefinitely. Only existing transient expiries and
Docker log rotation apply; moderation/withdrawal does not delete evidence. No schema
guard is weakened, and no VACUUM/reclaimed-capacity or paid-storage policy is added.

After authorization, stop the API and inspect without service secrets or activation:

```sh
docker compose -f community-service/compose.yaml stop community
docker compose -f community-service/compose.yaml run --rm --no-deps maintenance retention --dry-run
docker compose -f community-service/compose.yaml up -d
```

This verifies current schema/integrity/FKs and reads one SQLite snapshot without
mutations. It returns counts for at most 1000 eligible rows per transient category
plus an overflow flag, not identifiers or secret values. Counts are currently
eligible observations, not reservations or known scanned-row totals; membership
coordination may become eligible only after stale evidence is removed. Payload
bytes reclaimed are zero. It refuses running/unknown/future/outdated stores and
has no `--confirm` or delete form.

To restore, stop the service and stage the verified snapshot in a new data volume
as `community.sqlite`, with UID 1000 access and its original `user_version`.
Point a disabled deployment at that volume first and verify recovery before
cutover. Preserve the original volume, configuration and receipt key history.
Restoring stale snapshots can roll back revocations, moderation, replay and
operation state: recovery must account for that before access is enabled.

Current schema is **`community-master-1` version 5** (`0001_master_baseline.sql`
through `0005_operator_insights.sql`; see the
[migrations README](../community-service/migrations/README.md)). The master-filter-set refactor rebased the service
migrations ([contract §8](community-scan-service-contracts.md#8-storage-schema-service),
[ADR 0002](adr/0002-community-master-filter-sets.md) D1). The owner explicitly
authorized discarding the old community service store because it has no users;
the old revision-era lineage (schemas 1–18) cannot be upgraded. Startup, `backup`,
`upgrade` and `retention` refuse an old-lineage store with
`reason=unsupported_database_lineage` and never stamp, upgrade or back it up.

To move from the old lineage, an operator resets the store explicitly:

1. Stop the service: `docker compose -f community-service/compose.yaml stop community`.
2. Archive the old data volume, for example by renaming or copying it to a
   private location. Do not delete it.
3. Create a new, empty data volume for `community-data`, then run
   `docker compose -f community-service/compose.yaml run --rm --no-deps maintenance init --confirm`.
4. Update configuration to the master-set variables above and start the service.

Contributor identities are not carried over; contributors enroll again. This reset
applies only to the community service. It is never automatic, and later schema
changes return to versioned, verified-backup-first upgrades.

To upgrade to the current schema, build the reviewed image, stop the
service, and run `docker compose -f community-service/compose.yaml run --rm --no-deps
maintenance upgrade --confirm --destination /backups/NEW_FILE`.
It first writes the pre-upgrade backup to `--destination` (a new filename) and
verifies it, then runs an atomic migration. Unknown
schemas are refused, and failed migrations roll back. Image rollback is safe only
with a schema-compatible image; it is not a database downgrade.

Legacy D1 stores and encrypted legacy archives belong to the retired lineage and
cannot be restored into the current service. Keep them archived if they must be retained.

### Image replacement and rollback

Record the running image ID/tag and volume names before maintenance. Keep the prior
image locally; a mutable tag alone is not a rollback record. Build the reviewed tag,
stop the API, take a verified snapshot with a new filename, then replace the container
without removing volumes. All examples require the same Compose project and protected
configuration/secret paths as the original installation:

```sh
COMMUNITY_IMAGE_TAG=REVIEWED_TAG docker compose -f community-service/compose.yaml build
docker compose -f community-service/compose.yaml stop community
docker compose -f community-service/compose.yaml run --rm --no-deps maintenance backup --confirm --destination /backups/NEW_PRECHANGE.sqlite
COMMUNITY_IMAGE_TAG=REVIEWED_TAG docker compose -f community-service/compose.yaml up -d --no-build --force-recreate community
curl --fail http://127.0.0.1:8787/health
```

Check readiness and reviewed synthetic workflows before reopening ingress. A schema
upgrade is a separate offline `maintenance upgrade` command using a new snapshot
destination and the new image. Old/outdated/unknown schemas are refused by startup.
On a failed upgrade, preserve its snapshot and database; the migration transaction
rolls back. Do not initialize, stamp, delete WAL files or retry an occupied destination.

For a schema-compatible rollback, stop the API and recreate it with the recorded prior
tag using `COMMUNITY_IMAGE_TAG=PRIOR_TAG docker compose -f community-service/compose.yaml
up -d --no-build --force-recreate community`. If the prior image cannot open the current
schema, do not downgrade the database in place. Use its matching verified snapshot
in a new volume, with ingress disabled and source volumes retained.

### Isolated snapshot restore

Use an operator-reviewed current-schema snapshot and reviewed image. Stop the service,
preserve the original data volume and set the protected configuration's `enabled` and
`trustedTunnel` fields to false before staging recovery. Copy into a **new** named volume
without overwriting any existing file. For an online backup, use the staged
`restore` command above instead of this manual copy. Replace uppercase placeholders
deliberately:

```sh
docker volume create NEW_RECOVERY_VOLUME
docker run --rm --network none --read-only --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:size=32m,noexec,nosuid,nodev --volume ORIGINAL_BACKUP_VOLUME:/backups:ro --volume NEW_RECOVERY_VOLUME:/data editarr-community:REVIEWED_TAG node --input-type=module -e 'import {copyFileSync,chmodSync,constants} from "node:fs"; import {openDatabase} from "./dist/runtime/schema.mjs"; copyFileSync("/backups/VERIFIED_SNAPSHOT.sqlite","/data/community.sqlite",constants.COPYFILE_EXCL); chmodSync("/data/community.sqlite",0o600); openDatabase("/data/community.sqlite").database.close();'
```

This has no listener, service-secret mount or network. For a snapshot of an older
`community-master-1` version, run the explicit backup-first upgrade in the new volume
before validation; it cannot be opened by the current server until upgraded. An
old-lineage snapshot is refused. Keep original `user_version` and bytes.
Create a protected Compose override outside the checkout:

```yaml
volumes:
  community-data:
    external: true
    name: NEW_RECOVERY_VOLUME
```

Start the disabled recovery deployment using both Compose files:

```sh
docker compose -f community-service/compose.yaml -f /private/recovery/restore-volume.yaml up -d --no-build community
curl --fail http://127.0.0.1:8787/health
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8787/ready
```

Readiness must be 503 while protected runtime configuration is disabled.
Do not run `policy --enabled true` as a restore step. Recover matched configuration,
private receipt signer and public key/revocation history separately. Review stale
revocations, membership evidence, moderation, replay/nonces, operations and local usage
before separately approved activation. Preserve both generations and restore the
original volume mapping if staging fails. A same-Mac snapshot is not an off-device
recovery drill. An off-device drill requires access to the independent snapshot
and matching secrets on another host.

## Security and operations

The runtime image contains Node, the bundled service, operator assets and numbered
migrations; no package manager dependencies or build tools are needed at runtime.
Compose uses non-root UID 1000, a read-only root filesystem, dropped capabilities,
no-new-privileges, bounded CPU/memory/PIDs/logs and loopback-only published access.
The Dockerfile-specific allowlist excludes configuration, credentials, databases,
media and unrelated application directories from the build context.

Use a dedicated Mac, wired Ethernet, a UPS and an independent availability monitor.
Maintain macOS, the Docker engine and Node base-image security updates. Test cold
boot behavior: FileVault and Docker Desktop login/startup can require intervention.
For reviewed releases, set `COMMUNITY_IMAGE_TAG` to a release identifier; the base
can be pinned with the `NODE_IMAGE` build argument. Rebuild and test updates instead
of blindly replacing running images with `latest` or an automatic updater.

### Host restart checklist

Rehearse host restart with an operator present: graceful stop,
hard restart, power restoration/UPS behavior, FileVault unlock, macOS login, Docker
engine start, container health, tunnel reconnection and independent HTTPS monitoring.
Record timestamps, which steps required a human, and whether the host stayed locked.
Do not disable FileVault, enable automatic login or change tunnel startup policy to
make a test pass. An encrypted boot volume may require physical unlock; Docker Desktop
may require a user session. Arrange operator access and UPS-assisted orderly shutdown
accordingly. Verify UID 1000 readability after host/engine updates without printing
configuration or secret contents. Check local restart and live provider connectivity independently.

### Operator UI known limitations

- The item runtime timeline has no vote-heat row, and moderation queue cards have no
  closest-version hint; the operator API does not provide either yet.
- A revision's change details (added, removed, retimed, recategorised) are blank when
  retention has already pruned the stored snapshots for that revision and the one before it.
- The Items list and the contributor leaderboard aggregate across the whole catalog on
  each request, so they slow down as the catalog grows. Precomputed rollups would be needed
  for large catalogs.
- Any allowlisted operator can download a full online backup of the database.

## Verification

```sh
pnpm --dir community-service test
docker compose -f community-service/compose.yaml build
pnpm --dir community-service test:container
COMMUNITY_RUNTIME_IMAGE=editarr-community:local node --test community-service/test/runtime/journeys.test.mjs community-service/test/runtime/service.test.mjs
```

The default test command includes unit, operator CLI and source-runtime checks.
Verification covers the shared Node regressions, every supported SQLite schema
upgrade, failed-upgrade rollback, signed publication over Node HTTP, exact BLOBs,
idempotence, permission rejection, operator JWT/CSRF and container crash recovery.
Container tests create and remove only uniquely named disposable test volumes.
Real Discord/Access/tunnel setup, public activation, off-device recovery, large
catalog performance and Mac cold-boot behavior remain deployment checks.

Source journeys
also require the repository Python `.venv` for actual downloaded-artifact import
verification; build operator assets first with `pnpm --dir community-service assets`.
Both build and test tooling use Node, locally owned runtime types, SQLite and esbuild.
No provider deployment tool, generated Worker types or provider test pool is installed
in either build or runtime stages. `test` runs unit, CLI and source-runtime checks;
`test:unit` is the unit-only command. Tunnel/Access uses the separate `cloudflared`
tool and remains the selected ingress/operator authentication integration.
