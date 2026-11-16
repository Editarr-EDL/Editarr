# CSS-61 — Restore a hosted backup into an isolated environment

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

**Legacy D1 archive tooling after the October 5 Docker pivot.** Preserve this
completed format/evidence. Its output is not directly accepted by the
[Docker SQLite runtime](../community-service-docker.md); never blindly stamp or
install it into an active volume. CSS-67 owns any separately approved known-schema
conversion/cutover and CSS-68 independent recovery. This DONE status certifies
neither current Docker restore nor public activation.

Status: **DONE locally/mock, October 4, 2026**. Approved matched encrypted archive
restore into a new private, disabled local directory. Live hosted resources,
existing-database writes, callbacks, deployment and production cutover remain
separate authorization/acceptance gates and were not performed.

- Prerequisites: [CSS-51](51-import-receipts.md), [CSS-52](52-votes-service.md), [CSS-53](53-ranking-policy.md), [CSS-56](56-moderation-service.md), [CSS-60](60-hosted-backup.md)
- Decision gates: [G-SERVICE-BACKUP](decisions.md#g-service-backup)
- Parent sections: implementation §4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Storage contract: [D1-only scope and schema](d1-storage-scope.md).

CSS-56 inventory: restore all seven 0014 tables (`reports`, `moderation_actions`,
`vote_exclusions`, `moderation_versions`, `moderation_epoch`, `operator_request_counts`,
`report_usage_observations`) and original report/vote operations alongside source
votes, counted imports and historical keys. Recalculation must honor active
exclusions; suspension alone never removes votes. Preserve reasoned audit, stale-
write tokens and restrictions, and invalidate catalog cache identities on cutover.
Read the [CSS-56 recovery handoff](56-moderation-service.md) before restore work.

Restored application usage counters may lag actual daily usage. Restoration must not accidentally enable production callbacks, writes or client enrollment.

CSS-60 is DONE locally/mock. Read its [format, consistency and recovery handoff](60-hosted-backup.md)
before editing. Consume the matched age-encrypted `snapshot.tar.age` and
`secrets.tar.age` pair; verify job/config/input identities, exact SQL digest/size,
known 0014 schema and per-table typed inventories. Reuse
`community-service/scripts/backup/validate.mjs` / `restoreAndValidate` rather
than another signature/schema/storage verifier. Historical signature validation
is attribution only: preserve revoked/retired restrictions. SQL restore runs in
an isolated process without production bindings; provider credential is excluded.
Configuration is operator supplied, not provider attested, and explicitly records
unset values/bindings. Hosted deployment, configuration generation, billed usage
and off-device acceptance were not verified. Reputation rebuild, archive read
safety, decryption and hosted environment isolation belong to this task.

## Starting points

community-service restore/rebuild tooling; admin backup status; operator runbook.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Validate backup completeness and known schema revision, then restore D1 BLOBs,
   records and configuration into a separate disabled environment. Verify any
   recoverable pre-upgrade backup before applying numbered migrations; never reset
   or stamp an unknown schema.
2. Verify lengths/digests/signatures, foreign keys and completed operation/payload
   links. Rebuild/check storage counters from BLOB lengths and receipt/vote aggregates
   while preserving identity, keys, moderation and durable deduplication facts.
3. Label restored usage with original source/period and unknown provider freshness.
   Keep test restore disabled; production cutover is a separately reviewed action.
   Provider-verified reservation reconciliation remains deferred.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Test restore cannot touch production databases, callbacks or clients.
- [x] Exact signed bytes and catalog links verify; storage/reputation counters agree
  with durable records, and retired keys remain retired.
- [x] Missing/corrupt payloads, unsupported schemas and interrupted imports produce
  actionable failures without enabling the service or silently inventing bytes.
- [x] Restored usage observations are never presented as current provider totals;
  no activation happens as a side effect of restore.

## Verification

- Restore the CSS-60 fixture and run lookup/download/signature/reputation checks.
- Inject missing/truncated BLOBs, stale counters, retired keys, unknown schema and
  interrupted imports; prove isolation and safe resumption.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met locally/mock, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide restore drill report, downtime/cutover requirements, payload/counter validation and usage-provenance evidence to CSS-67/68/69.

## Handoff summary for the next task

DONE locally/mock, October 4, 2026, GitHub Copilot. Clean starting worktree; no
commit. G-SERVICE-BACKUP explicitly approved this local/mock restore drill.
No live credential, Cloudflare resource, existing database, migration, callback,
client enrollment, deployment, activation, server restart or paid service.

### Commands And Protected Output

`community-service/scripts/operator-cli.mjs` adds `restore`. Focused
`scripts/backup/{archive,restore,restore-worker,counters}.mjs` own bounded archive
reading, private job state, isolated import and derived reputation reconciliation.
Existing `validate.mjs` still owns schema/BLOB/signature/FK/storage verification;
`artifact-runtime.mjs` loads the actual shared import/vote reconciliation functions.
No new package, schema, migration, Worker route, dashboard job or signing format.

Node 26 and age are required. This command uses POSIX `/dev/fd/3` for the private
decryption-key pipe; Windows and other platforms were not certified. Supply an
owned 0600 age recipient identity file, not a key argument or chat message. An
interactive/passphrase-protected identity is not an unattended supported input;
decryption failures/timeouts never downgrade protection.

```sh
pnpm --dir community-service operator -- restore
pnpm --dir community-service operator -- restore --confirm \
  --source "$COMPLETED_PRIVATE_BACKUP_DIRECTORY" \
  --destination "$NEW_PRIVATE_RESTORE_DIRECTORY" \
  --identity-file "$PROTECTED_AGE_IDENTITY_FILE" \
  --maximum-archive-bytes "$ARCHIVE_BYTE_CEILING" \
  --maximum-sql-bytes "$SQL_BYTE_CEILING" \
  --artifact-bounds "$ARTIFACT_BOUNDS_JSON"
```

The first command is network-free dry-run. The source must include CSS-60's
complete private `state.json` and the matched `snapshot.tar.age`/`secrets.tar.age`.
Retain the checkpoint with both archives during off-device replication. Each
ciphertext's exact size/digest is checked against that checkpoint before decrypting.
The new destination's parent must exist; destination must be outside the checkout
and cannot equal or be inside the source. Existing directories are refused unless
resuming this exact restore job. No `--origin`, provider token, account/database
target, deployment credential or live import command is accepted.

Explicit SQL ceiling is 1..1,200,000,000 bytes; per-ciphertext ceiling is
100..1,300,000,000 bytes. These are safety bounds, not certified production limits.
The five positive artifact bounds match CSS-60's parser contract. Snapshot manifest
is limited to 262,144 bytes, secret manifest/input to 131,072 each; archive output
is bounded by member ceilings plus framing/metadata allowance. Decryption/import
subprocesses have 120-second bounds. Large SQL/archive buffers and SQLite restoration
can require substantial memory/disk; large-database capacity remains unverified.

Completed owned 0700 directory contains four 0600 outputs plus private `state.json`:

- `restore.sqlite`: known schema 0014, verified exact bytes and rebuilt statistics.
- `configuration.json`: inert disabled record; empty effective bindings,
  `APP_ENV=restore-disabled`, callbacks/enrollment/activation false. Original
  deployment, bindings and variables are retained only as nested source metadata,
  explicitly operator supplied and not provider attested. This is not Wrangler config.
- `secrets.json`: the three original service secrets, in plaintext inside this
  protected local directory only. No new key generation, rotation or installation.
- `restore-report.json`: source/job/bookmark/schema/inventories, counter checks,
  usage periods/freshness, local-only cost scope and unique isolated cache identity.
  No secret values, private report notes, provider credential or age key.

Keep the entire destination private; it contains Discord IDs, private report notes
and usable service secrets. The age identity is only piped to age and never saved
in restore state/output. Completion deletes owned SQL/validation/partial staging;
no secure-erasure claim is made for filesystem remnants. No retention or scheduling.

### Validation, Reconciliation And Recovery

Snapshot/secret manifests bind job ID, source account/database/bookmark, configuration
digest, input identity and exact SQL digest/length. Both secret inventories and
the original receipt signer/public-ring relationship must validate. New CSS-60
exports also include snapshot `inputDigest`; older version-1 exports bind it
through the required complete source checkpoint and secret manifest. No digest
is recomputed from a reserialized configuration and falsely called the original
raw-input hash. Encrypted files must still match the checkpoint's original hashes.

New backup tar output is explicit plain USTAR with macOS copyfile metadata disabled.
Restore never extracts tar paths. Only the exact expected regular members are
materialized. Traversal, unknown members, links, duplicate members, bad checksums,
truncated framing, missing termination, oversized payloads and unknown extensions
fail closed. Prior macOS archives' bounded AppleDouble and PAX timestamp/provenance
metadata are ignored, never extracted; path/size/link/sparse overrides are rejected.
Binary provenance values remain inert and are not interpreted as text or paths.

The child inherits only PATH, with no production binding or credential environment.
SQL cannot ATTACH/DETACH databases, load extensions, create virtual tables or use
unsafe PRAGMAs. An external sentinel database remained byte-identical under an
encrypted malicious-ATTACH drill. Only known current 0014 and exact migration
history are accepted; no upgrade, stamping, reset or existing-store write occurs.
Unordered inserts use the existing isolated-only FK-disabled import, followed by
mandatory integrity/FK checks and every referenced artifact/signature/publication
operation link. Source typed inventories match before any derived updates.

Storage bytes are checked against actual three-part BLOB lengths and retained
policy; stale storage counters fail rather than authorizing a payload repair.
Actual `reconcileImportCounts`/`reconcileVoteCounts` run in <=100-revision batches,
rebuilding counted imports and nonexcluded like/dislike totals. Neutral votes and
source rows remain intact; suspension alone never removes a vote. Missing statistics,
invalid counted-import facts and foreign vote-key ownership fail. A second inventory
check permits changes only to `contribution_statistics`; historical accounting/observations,
operations, historical keys, exclusions, reports, audit, versions and epoch remain
byte/logically exact. Retired/revoked artifact signatures prove attribution only,
never fresh membership, request authority or download eligibility. Shared ranking
continues to compute inline; no stored rank or author boost is introduced.

Report preserves original usage inventory hashes/counts and min/max periods where
present; original source/time/unknown fields remain in SQLite. Provider usage and
provider freshness are explicitly null/unknown, never relabeled as current totals.
No reservation/provider reconciliation or accounting reset is performed.

Checkpoint phases are `staging`, `importing`, `validated`, `complete`, with safe
`lastError` codes for failures. Add `--resume` with identical source generation,
archive hashes, limits and artifact bounds. Interrupted import starts a new owned
partial database from the same immutable SQL; it never appends into an uncertain
partial import or silently switches snapshots. Durable validated outputs resume
cleanup without another import. Completion rechecks every output size/digest.
Changed inputs require a reviewed new destination; repeated corrupt inputs remain
incomplete. After hard termination, verify no CLI process is active before removing
only its stale `lock`; never edit the saved source identity, hashes or completion
state. No rollback/deletion of an existing database is needed because none is opened.

### Verification And Drill Evidence

- `pnpm --dir community-service test:operator-cli`: **44 passed**, including real
  age/tar encryption/decryption, exact signature/schema/FK/inventory validation,
  shared lookup/payload reads/ranking, resumable import/cleanup, corrupted completion,
  changed scope, unsafe key permissions, mixed archives, damaged/truncated BLOBs,
  forged key, missing operations/migrations, stale storage, unknown schema and
  an untouched external database. Existing Access/action/backup regressions passed.
- After the manifest-overhead bound correction, final
  `node --test community-service/test/operator-cli/restore-job.test.mjs`:
  **9 passed**, including the added valid >131,072-byte snapshot manifest case.
- `pnpm --dir community-service exec vitest run test/operator-cli-service.test.ts
  test/moderation.test.ts test/import-receipts.test.ts test/votes.test.ts
  test/ranking.test.ts test/catalog-http.test.ts`: **53 passed**.
  Separate `test/download-http.test.ts test/download-receipts.test.ts`: **16 passed**.
- `pnpm --dir community-service exec tsc --noEmit`, CLI `restore` dry-run and
  `--help` passed. Scoped editor diagnostics and documentation/whitespace checks
  passed at completion. No unrelated frontend/browser/Python/native/build suite.
- Synthetic signed fixture: **59,992 SQL bytes**, **1 artifact / 2,750 payload
  bytes**, restored local SQLite **540,672 bytes**, about **486 ms** in the final
  focused drill. No application Worker/provider requests from restore; provider
  D1 reads/writes/billing and hosted database size remain **unknown**, not zero.
  Physical SQLite size is not D1 size and can differ after logical SQL restoration.
- Populated encrypted drill retained three import/vote facts, neutral vote, three
  durable vote results, report/private note, three applied audit records, epoch,
  excluded dislike, suspended like voter, revoked author key and original
  `2026-10-03` usage/unknown observations. Stale 99/99/99 statistics rebuilt to
  **3 imports / 1 like / 0 dislikes**, shared quality **0.207654955**. No fact,
  key state, moderation restriction, stale-write token or source timestamp changed.

Initial tests caught default macOS binary tar metadata, sealed-release fixture
ordering and `/var` versus `/private/var` path aliases. Corrected with explicit
USTAR/backward-safe metadata parsing, pre-seal fixture setup and canonical path
comparison; all recorded final checks passed without weakening application guards.

### Hosted Acceptance And Next Work

No remaining authorized local/mock CSS-61 work. A live separate D1 restore,
provider import limits/cost/lock behavior, deployment/config generation attestation,
off-device disaster recovery and non-macOS execution remain unverified. The
dashboard cannot attest to operator-local files and still reports backup unavailable.

CSS-67/68/69 receive this drill report, output inventory, byte/counter evidence and
usage provenance. Production recovery/cutover needs explicit separate authorization,
an independently verified recoverable backup of any existing target before its
migration/replacement, a distinct disabled D1 target and independently reviewed
bindings/origins/Access/Discord callbacks/secrets. Never reuse nested source bindings
as executable target config. Recheck current membership/key eligibility and stale
provider state before accepting traffic. Define downtime/write exclusion and
provider import/rollback steps from live evidence; this drill certifies no downtime
window. Cutover must introduce fresh catalog cache/cursor generation without erasing
audit/version/epoch history; the local report identity alone is not a deployed fence.
No automatic activation, rollback-over-production or provider reconciliation exists.

Next ready coding task: **CSS-62**. Read its brief, G-LICENSE/G-CONTENT and the
CSS-37/45/46 handoffs; start with the existing exact-preview/outbox approval boundary.
Confirm any unresolved automatic-donation policy before enabling it. Existing manual
sharing consent must never be treated as automatic opt-in.

Direct dependent tasks: [CSS-67](67-packaging-and-user-docs.md), [CSS-69](69-pilot-and-launch-scope.md).

