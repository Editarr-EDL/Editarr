# CSS-60 — Export consistent D1 service backups

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

**Legacy D1 tooling after the October 5 Docker pivot.** Preserve this completed
format/evidence; do not use provider export as the current SQLite backup command.
The [Docker runbook](../community-service-docker.md) defines offline verified
snapshots; CSS-67/68 own current deployment/independent recovery, and CSS-67 owns
any separately approved D1/archive import. This DONE status certifies none of those
new deployment checks and does not authorize live provider work.

Status: **DONE locally/mock, October 4, 2026**. Approved full provider export with
temporary provider query lock, protected operator-local provider credential,
private independent local destination, age recipient encryption and separately
encrypted service secrets. Live provider authorization, export and hosted restore
remain separate acceptance gates. No application freeze, schedule, automatic
deletion, deployment or existing-database migration was performed.

- Prerequisites: [CSS-39](39-publication-service.md), [CSS-42](42-artifact-download-service.md), [CSS-59](59-operator-cli.md)
- Decision gates: [G-SERVICE-BACKUP](decisions.md#g-service-backup)
- Parent sections: implementation §4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Storage contract: [D1-only scope and schema](d1-storage-scope.md).

A consistent D1 backup includes artifact BLOBs and catalog/operation state. Include configuration/schema/deployment metadata and separately protected service secrets; Time Travel alone is not the independent backup.

## Starting points

community-service backup tooling; admin backup-status services; operator runbook.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

CSS-59's [commands and adapter handoff](59-operator-cli.md) provides approved human
Access transport, private checkpoints and `transferPage(cursor, read, commit)` for
both export/import directions. It preserves opaque BLOB bytes and advances only
after an idempotent destination commit. Existing inspection pages are not backups;
bind future transfers to one approved snapshot/job and known schema revision.
No backup format, destination, freeze or secret policy was selected by CSS-59.
The October 4 CSS-60 answers are recorded in G-SERVICE-BACKUP: use the provider
full SQL export, explicitly accepting its query lock, not a nonblocking snapshot.
Do not mistake the provider's temporary export storage for an application R2
bucket or an independent retained backup.

1. Implement the approved independent backup destination/protection and a consistent
   D1 export. If exporting in pages, use a write freeze or a proven snapshot protocol;
   unrelated live page reads do not form a consistent backup.
2. Include artifact BLOBs, revisions, operations, historical keys, historical accounting,
   storage policy/counters and migration revision. Verify exact byte lengths/digests,
   signatures, foreign keys and an isolated test restore before marking complete.
3. Save configuration/deployment versions; protect secrets separately. Provide
   resumable checkpoints, incomplete status and bounded D1 read/write accounting.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] An interrupted export cannot be advertised as recoverable; completion requires
  a verified isolated restore and every referenced artifact in the D1 snapshot.
- [x] Publication/withdrawal/maintenance races cannot mix snapshot generations or
  leave completed operation results pointing at absent payloads.
- [x] Restored storage counters match payloads and migration state is known.
- [x] Destination is independent; secrets and production authorization remain protected.
  There is no separate object-copy or bucket inventory phase.

## Verification

- Export a synthetic database during publication/maintenance, interrupt and resume,
  then restore it in isolation and verify all three signed BLOBs and catalog linkage.
- Corrupt/truncate a payload or omit operation/schema records and require validation
  to reject the backup. Record measured D1 cost and separately stored backup size.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met locally/mock, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide backup format, consistency protocol, destination setup, completed inventory and cost evidence to CSS-61/68.

## Handoff summary for the next task

DONE locally/mock, October 4, 2026, GitHub Copilot. Clean starting worktree; no
commit. No migration, new package dependency, real credential, existing database,
hosted operation, deployment, production activation or server restart.

### Commands And Protected Setup

`community-service/scripts/operator-cli.mjs` adds `backup`; focused
`scripts/backup/{provider,files,job,validate,validation-worker,artifact-runtime}.mjs`
own provider transport, private files, journal, isolated validation and reuse of
the existing TypeScript artifact/receipt verifiers. Node 26, age and tar are
required. Current installed tools were used; none was installed in this task.

```sh
pnpm --dir community-service operator -- backup
pnpm --dir community-service operator -- backup --confirm \
  --destination "$NEW_PRIVATE_BACKUP_DIRECTORY" \
  --account "$ACCOUNT_ID" --database "$DATABASE_ID" \
  --token-file "$PROTECTED_PROVIDER_TOKEN_FILE" \
  --config-file "$PROTECTED_CONFIGURATION_FILE" \
  --secrets-file "$PROTECTED_SERVICE_SECRETS_FILE" \
  --recipient "$AGE_PUBLIC_RECIPIENT" \
  --maximum-provider-requests "$REQUEST_CEILING" \
  --maximum-sql-bytes "$SQL_BYTE_CEILING" \
  --artifact-bounds "$ARTIFACT_BOUNDS_JSON"
```

These variables are placeholders, not production defaults. The first command is
network-free dry-run. `--confirm` explicitly accepts the provider query lock and
creates a new private destination; use the same arguments plus `--resume` for an
existing checkpoint. Do not supply `--origin`: this approved provider credential
is separate from human Access and grants no Worker admin authority. Preserve
Access/CSRF unchanged for all existing admin commands. The destination's parent
must exist; the new directory must not be inside the checkout. A machine-local
copy is independent of Cloudflare, not verified off-device disaster protection.
Replicate both encrypted files independently and protect the age decryption key
separately. The tool does not delete old backups or schedule future ones.

Use operator-local, owned 0600 regular input files, never token arguments or chat.
The provider token file contains only the API token. Provision the minimum
permissions Cloudflare actually permits for export and confirm their scope before
live use; no claim of an export-only/read-only token is made. Neither the token nor
signed download URL is printed, sent to the Worker, persisted in the journal or
included in either archive. Recovery authorization remains operator-managed.

The configuration JSON has exactly `deployment`, `bindings`, and `vars`:

- `deployment`: nonempty `releaseVersion`, `revision`, `workerVersion` strings.
- `bindings`: `CATALOG_DB` contains the exact `accountId`/`databaseId` arguments;
  `OPERATOR_ASSETS` is `{ "sha256": "<asset-bundle-digest>" }` or explicitly null;
  Current runtime admission requires trusted ingress metadata, not native limiter bindings.
  The example limit is illustrative, not a new policy; preserve actual bindings.
- `vars`: every name in exported `configurationVariables` must be present, with
  its actual string value or explicit null if unset. This covers application,
  protocol/limit, receipt public-key history, Discord and Access variables. Unknown
  names, embedded secret fields and silent omissions fail closed.

Service-secret JSON has exactly `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN`, and
`DOWNLOAD_RECEIPT_PRIVATE_KEY`, each a nonempty string. The receipt private key is
canonical PKCS8 base64 and must match the single active entry in the full public
`DOWNLOAD_RECEIPT_KEYS` ring through the existing receipt signer validation.
These inputs are intentionally mandatory: an unconfigured/secret-incomplete
installation is refused rather than advertised as identity-complete. Do not
generate replacement keys to satisfy backup validation. Keep rotation metadata
and all retired/revoked public receipt keys in the configuration.

Configuration is explicitly **operator supplied, not provider attested**. Ensure
the supplied configuration/deployment/secret generation matches the exported
environment and do not deploy or rotate its secrets during this manual operation.
Input hashes bind resume to the same metadata and secrets; the API token may be
replaced independently. Live deployment/configuration capture remains unverified.

Limits must be explicitly supplied: total export/poll/download attempt ceiling
2..1000, SQL download ceiling 1..1,200,000,000 bytes, and exactly five positive
artifact bounds (`compressedBytes`, `expandedBytes`, `events`, `text`, `depth`).
These are parser safety ceilings, not certified capacity or production defaults.
Attempt counters are saved before requests and survive restarts. Polling uses
one-second spacing; transport responses are bounded to 131,072 bytes/15 seconds.
Downloads, isolated validation and archive subprocesses have 120-second bounds.
age recipient/tool availability is checked before starting an export.

### Format, Consistency And Completion

Cloudflare's full export POST requests `output_format: polling`, with no table,
schema or data filter. Its fixed `at_bookmark` is the snapshot identity; all polls
send the saved `current_bookmark`. A changed bookmark or failed/cancelled export
is terminal. Resumption never silently creates another snapshot. Official sources:
[D1 import/export](https://developers.cloudflare.com/d1/best-practices/import-export-data/)
and [export API](https://developers.cloudflare.com/api/resources/d1/subresources/database/methods/export/).
Both state that export blocks database queries; interrupted polling may cancel
the provider job, and the signed download URL lasts one hour. No application
freeze, watermark protocol, object-transfer command or bucket binding was added.

The SQL download commits through CSS-59's `transferPage` adapter, bound to the
bookmark. Download redirects, foreign download hosts, transparent content
encoding, oversized/empty bodies and incomplete streams fail closed. Download
URLs are limited to HTTPS provider R2 export hosts; this is provider-managed
temporary storage, not retained application storage or new resource setup.

Completed destination contains only private `state.json`, `snapshot.tar.age`,
and `secrets.tar.age`. Standard tar members are:

- Snapshot: `manifest.json` (`community-d1-backup-1`) and exact `database.sql`.
- Secrets: `secret-manifest.json` (`community-d1-secrets-1`) and `secrets.json`.

CSS-61 compatibility update: new archives use explicit USTAR with macOS copyfile
metadata disabled; new snapshot manifests also carry `inputDigest`. The restore
reader safely ignores narrowly bounded inert metadata in prior macOS tar output.
Keep the original complete private `state.json` with both encrypted files: CSS-61
requires its ciphertext digests and raw input identity when reading either version-1
form. See the [completed local restore runbook](61-hosted-restore.md); this does not
grant live provider import, deployment or production cutover authority.

Manifests bind job ID, metadata/input digest, configuration digest, schema,
bookmark, exact SQL digest/length and explicit secret inventory. The snapshot
manifest includes per-table row counts and deterministic typed/framed SHA-256
inventories, including exact BLOBs and integer values, plus payload/isolated-size
measurements. The secret archive and snapshot must be kept as a matched pair.
Ciphertext lengths/digests are durably recorded before cleanup. age encryption
must succeed for both archives; there is no plaintext downgrade.

Only current known schema **0014** with every migration 0001..0014 is accepted.
Isolated restoration requires the exact application schema, integrity/FK checks,
every referenced payload's three byte lengths/digests, existing artifact parser /
Ed25519 signature verification, exact revision/summary/author binding, completed
publication-result linkage, and payload policy/counter agreement. All **47
application tables**, D1 migration history and SQLite sequence state are retained.
This includes historical accounting/operation/nonces, historical keys, membership/enrollment,
imports, source votes/statistics/exclusions, private reports, moderation audit /
versions/epoch and all partial usage observations. Provider internals are excluded.
Historical signature checks prove cryptographic attribution only, including
revoked keys; they never change key state or claim current download eligibility.
Reputation rebuild and hosted activation remain CSS-61, not part of this export.

The child restore has no production bindings/network calls and denies ATTACH,
DETACH, virtual-table creation, extension loading and unsafe PRAGMAs. Unordered
SQL inserts load with FK enforcement disabled in the isolated copy only; a clean
post-import FK check is mandatory. No unknown schema is stamped or migrated.

Journal phases are `starting`, `exporting`, `exported`, `sealed`, `complete`, with
terminal `cancelled`/`validation_failed`. An uncertain initial POST has no safe
bookmark: `export_start_outcome_unknown` requires operator review, not replay.
Lost polling/download resumes the same bookmark if the provider still retains it;
otherwise require a reviewed new job/directory. Saved downloads are digest checked
before local retry, so encryption retry does not re-export. A sealed checkpoint
finishes cleanup without SQL or another export. Completion rechecks both files;
missing/corrupt ciphertext or an empty inventory cannot claim recoverability.

Incomplete checkpoints may contain private plaintext SQL, restored SQLite and
secret staging, including Discord IDs/report notes. Protect the whole directory
until resolved; completion removes owned partial files and fixed staging members.
No secure-erasure claim is made for filesystem remnants. Exclusive locking and
atomic/fsynced replacements reuse CSS-59. After hard termination, verify no CLI
process is active before removing only its stale `lock`; never rewrite bookmark,
intent or completion state manually. Unknown/cancelled jobs need fresh review.

### Verification And Cost Evidence

- `pnpm --dir community-service test:operator-cli`: **33 passed**, including real
  age/tar round trips of both archives, exact signed BLOB/schema inventory, retired
  keys, corruption/truncation/missing records, scope/auth failures, interrupted
  polling/download/encryption/sealed cleanup, changed source moderation/cleanup,
  fixed generation, explicit durable attempt caps and existing CLI regressions.
- `pnpm --dir community-service exec vitest run test/operator-cli-service.test.ts
  test/publication.test.ts test/publication-storage.test.ts test/moderation.test.ts`:
  **17 passed** against actual shared Worker services.
- `pnpm --dir community-service exec tsc --noEmit`: passed. Package `operator --
  --help` and `operator -- backup` dry-run passed; scoped editor diagnostics and
  documentation/whitespace checks verified at completion.
- Disposable real Wrangler 4.141.0 local migrations/export, with a synthetic
  signed publication seeded only into its verified temporary database: schema
  **0014**, **49 tables**, **1 artifact**, exact **2,750 payload bytes**, **61,614
  SQL bytes**, isolated restored SQLite **544,768 bytes**. Empty export also passed.
  Initial `--persist-to` export invocation was rejected by Wrangler; corrected
  using a temporary configuration/default local state. No workspace DB was used.
- That actual local SQL through fake provider transport and real age/tar produced
  **82,136 snapshot bytes + 10,440 secret bytes**, about **465 ms local wall time**.
  **1 provider API attempt + 1 download; 0 application Worker requests**. D1
  provider rows read/written, total hosted D1 size and provider billing are
  explicitly **unknown**, not zero. Local SQLite layout/size can change on SQL
  restoration and must not be reported as hosted D1 size. Backup bytes are separate
  from the retained payload counter. These tiny samples do not certify capacity.

Initial fixture failures exposed a loader typo, unordered FK insertion and
duplicated SQLite sequence rows; fixtures/import handling were corrected without
weakening mandatory checks. Logical inventory comparison excludes physical page
layout. No production Worker/frontend/Python code changed; full unrelated,
browser, native/build and other-platform suites were not rerun.

Live provider permission scope, export query-lock duration/cancellation/download
host compatibility, D1 billed cost, deployment/config generation attestation,
off-device replication, large-database resource limits and hosted restore were
not exercised. No secret or deployment authority is installed on the Worker.
Production remains disabled and its dashboard backup status stays unavailable;
it cannot inspect an operator machine's files. Local verified completion is not
a claim of a hosted drill or current provider account usage.

Remaining authorized local/mock CSS-60 work: none. Operational acceptance is
separate. Next task: **CSS-61**. Read this format/protocol and CSS-51/52/53/56
inventories; implement matched archive decryption and validation into a separate
disabled environment, reusing `restoreAndValidate` and table inventories. Preserve
all source facts/key restrictions and honor vote exclusions when checking/rebuilding
aggregates. Do not enable production, overwrite an existing DB or perform cutover.

Direct dependent tasks: [CSS-61](61-hosted-restore.md), [CSS-64](64-retention-cleanup.md).

