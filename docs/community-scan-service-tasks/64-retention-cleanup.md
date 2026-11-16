# CSS-64 — Implement approved retention without breaking deduplication

Status: **DONE locally/disposable**, October 8, 2026, GitHub Copilot.
The user explicitly reopened CSS-64 and approved retain-first under G-RETENTION.
Durable evidence, existing transient expiry, shared bounded selectors and read-only
offline preview are implemented and verified in source and the Node 24 ARM64 image.
No durable-data deletion, backup pruning, existing-database changes or live
activation is authorized. CSS-68 remains canceled; CSS-69's pilot scope is unchanged.

- Prerequisites: [CSS-39](39-publication-service.md), [CSS-51](51-import-receipts.md), [CSS-52](52-votes-service.md), [CSS-56](56-moderation-service.md), [CSS-60](60-hosted-backup.md), [CSS-63](63-sharing-policy-withdrawal.md)
- Decision gates: [G-RETENTION](decisions.md#g-retention)
- Parent sections: contracts §7; implementation §4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Storage contract: [self-hosted SQLite runtime](../community-service-docker.md),
superseding the historical [D1-only deployment](d1-storage-scope.md).

Retaining every receipt costs database space, but deleting receipts can allow double counting. Historical public keys and attribution serve long-lived verification.

CSS-63's [completed permission/moderation state machine](63-sharing-policy-withdrawal.md)
requires exact compatible parent BLOB evidence for new/pending derivatives. Moderation
does not revoke standing reuse permission or delete evidence. Any proposed parent-byte
retention must explicitly resolve that fail-closed check; metadata cannot silently
replace signed permission. Author withdrawal remains absent, not a cleanup trigger.

## Approved Retention Matrix

October 8 user answer: **Approve retain-first**, superseding the October 6 deferral.
Policy version: community-retain-first-1. No schema migration is required because
the approved policy keeps all existing durable-data deletion guards.

| Records | Approved rule |
| --- | --- |
| Exact artifact/manifest/envelope BLOBs, immutable revisions/parents and attribution | Indefinite; no TTL, withdrawal deletion, reclaimed-byte release or tombstone substitution |
| Contributor/installation identity and historical public keys | Indefinite; cleanup never changes revocation/retirement or grants authority |
| Durable publication/import/vote/rename/key/moderation operations, pending intents | Indefinite; preserve exact replay/reconciliation facts |
| Import receipts/deduplication facts, votes including neutral and exclusions, statistics | Indefinite; no double-counting after cleanup or inherited derivative votes |
| Reports including resolved notes, moderation audit/version/epoch | Indefinite; no automatic moderation or privacy deletion; bounded inspection pages remain |
| Browser OAuth/enrollment state and request nonces | Existing strict expires_at < current time; original millisecond/second boundaries and replay windows; at most 1000 per category per pass |
| Unused completed browser recovery authorizations | Existing expiry plus older-than-600000-ms cutoff; authority referenced by committed recovery and pending operations remain protected |
| Membership evidence/coordination | Existing 900-second evidence cutoff; remove idle coordination only after lease/cooldown expiry with no remaining evidence |
| Temporary profile-name events | Existing changed_at <= now - 3600000-ms rule; durable rename results/current identity retained |
| Passive observations and runtime logs | Existing broker-observation cleanup before the current UTC month and Docker log rotation only; other observations unchanged, never usage admission or provider billing |
| Manual community-service snapshots/configuration/signing history | No automatic pruning or schedule; protect existing inventory and backups; independent destination/custody/recovery choices still separate |
| Local-app scheduled backups | CSS-65's previously approved success-only count retention remains unchanged; no manual-backup pruning |

The offline preview uses the same selectors as cleanup, returns no identifiers or
secret values, selects at most 1001 eligible rows per category to detect overflow,
and reports at most 1000 candidates. Scanned-row counts remain unknown. It is a
read-only observation, not a reservation: deleting stale membership evidence may
make coordination eligible later in the same pass. Payload bytes reclaimed are
always zero. No automatic VACUUM/checkpoint or physical-space-reclamation claim.

## Starting points

community-service retention jobs/tombstones; local transient cleanup; admin status.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement the approved retention matrix for SQLite payloads, receipts/dedup markers,
   votes, OAuth state, reports/audits, signatures and independent backups.
2. Keep uniqueness facts and required keys/lineage. Retain-first deletes no payload
  and releases no capacity. Any future approved payload deletion must preserve
  operation/moderation/backup references and decrement global/contributor byte
  counters in the same SQLite transaction.
3. Current schema forbids payload/revision deletion. Retain-first keeps these guards;
  no migration or tombstone layer is needed. Any future guard changes require a new
  explicit policy decision, versioned migration, populated upgrade tests and verified
  recoverable backup. Use bounded transient cleanup and read-only dry-run accounting.
4. Reuse existing scheduled OAuth/nonce/profile cleanup in `community-service/src/index.ts`
  and its feature services, invoked hourly by `src/runtime/server.mjs`. Preserve
  replay windows and durable deduplication facts. Verify invocation after restart,
  bounded work and failure accounting with CSS-66; a direct scheduled-handler unit
  test does not establish Node scheduler wiring.
   New uploads are atomic; do not add a publication reservation-expiry or recovery job.
5. Treat CSS-60's D1 backup handoff as historical inventory, not a current backup
  command. Use the Docker offline verified-snapshot contract for any approved schema
  upgrade. Backup destination/protection and off-device evidence remain G-SERVICE-BACKUP
  and CSS-69's launch disposition; CSS-68 is canceled. No schedule, deletion age
  or backup pruning is chosen by this task brief.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Cleanup cannot re-enable duplicate imports/votes or erase required key/lineage
  bindings. No payload is deleted solely because its contribution is withdrawn.
- [x] Pending operations, moderation and backup references remain protected.
- [x] Interrupted/concurrent reclamation cannot double-release capacity or leave
  dangling references; reported payload bytes match retained BLOBs.
  Retain-first performs no reclamation; deletion guards and exact counters survive.
- [x] Expired transient secrets are removed without expanding personal-data collection.

## Verification

- Use time-boundary, retry-after-pruning and existing backup/publication fixtures.
- Verify supported upgrades/recovery, bounded transient batches, Node timer/restart
  and kill-switch behavior. Durable deletion/counter rollback is not applicable to
  retain-first; any future deletion policy requires its own migration/race tests.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide retention matrix, cleanup cursors, dedup guarantees and measured storage trend to CSS-69 if this deferred task is resumed.

## Handoff summary for the next task

### October 8 Completion

Status / date / implementer: DONE locally/disposable, October 8, 2026, GitHub Copilot.

User selected "Reopen CSS-64 retention" after the eligible CSS coding backlog was
complete. Existing payload/revision/import-receipt/report/audit deletion guards
remain intact. Node invokes bounded transient cleanup hourly through the shared
scheduled service; durable operation/key/lineage facts are not a deletion target.

Subsequent user answer: **Approve retain-first**. The matrix above is now approved.
Implemented selectors/policy under community-service/src/retention; original
feature pruning APIs and hourly scheduler remain in use. Offline maintenance adds
retention --dry-run using a verified read-only current-schema SQLite transaction.
No mutation, confirmation or activation form is supported for that command.

Verification passed: 570 shared tests in 83 files, TypeScript, 5 focused retention
tests, 24 original cleanup/wiring tests, 19 schema/read-only/upgrade/recovery tests,
2 source HTTP/SQLite sharing/cleanup journeys, a final strengthened read-only test,
1 native-image lifecycle/dry-run test and 3 image sharing/cleanup/restart journeys.
These suites overlap; counts are not additive. Completed recovery authority, pending operations,
current/future nonces, active membership leases/cooldowns and durable signed
inventories remain protected. A pending-operation fixture initially violated the
existing NULL-result constraint; corrected without changing the schema.

Latest synthetic source workflow observed 2 retained artifacts / 5766 exact three-part
payload bytes before/after cleanup. SQLite main 540672 bytes before/after; WAL
3184792 -> 3258952 bytes; SHM absent (0 bytes). This interval includes test writes,
cleanup and replay checks, not a launch growth forecast or capacity/billing claim.
Indexes share the main file; separate index sizes were not measured. No bytes
released from passive payload observations and no provider traffic performed.

Final image: editarr-community:css64-retain-first-4dabea03-3118-4f93-8d69-ac848025f42d,
ID sha256:8b98890c2a1fe85f60515ad457c5aca30c400e51f8e48cc153ff6c2765e0c1b2,
linux/arm64, Node v24.21.0, schema 16, 80951587 image bytes. Packaged preview
succeeded on a read-only data mount with no network, secrets or backup mounts;
retention --confirm and inspection while the exclusive writer ran were refused.
The expired preview nonce survived dry-run and appeared in the verified snapshot.
Existing hard-kill/restart, second-writer refusal, hourly failure/retry wiring and
durable replay passed. Fixture containers/volumes were removed; tagged image retained.

Commands executed (all database work uses disposable fixtures):

```sh
pnpm --dir community-service exec vitest run test/retention.test.ts
pnpm --dir community-service test:unit
pnpm --dir community-service typecheck
node --test community-service/test/runtime/schema.test.mjs
node --test --test-name-pattern='sharing|cleanup' community-service/test/runtime/journeys.test.mjs
node --test --test-name-pattern='read-only' community-service/test/runtime/schema.test.mjs
COMMUNITY_IMAGE_TAG="$retention_tag" docker compose -f community-service/compose.yaml build community
COMMUNITY_TEST_IMAGE="editarr-community:$retention_tag" node --test --test-name-pattern='native image' community-service/test/container/runtime.test.mjs
COMMUNITY_RUNTIME_IMAGE="editarr-community:$retention_tag" node --test --test-name-pattern='sharing|cleanup|restart' community-service/test/runtime/journeys.test.mjs
```

The recorded retention_tag is css64-retain-first-4dabea03-3118-4f93-8d69-ac848025f42d.
No durable cleanup cursor exists because durable records are not pruned. Transient
selectors independently limit each category to 1000 rows per pass; each next pass
reselects eligible rows, preserving original boundary/reference/lease conditions.
Preview's 1001st-row sentinel only indicates additional currently eligible rows.
Exact import and original like-operation replay after two cleanup passes preserved
one import and the newer neutral vote; no count reinflation or capacity release.

Files changed: Task/gate documents, shared retention selectors/policy and original
pruners, read-only runtime schema/maintenance support and focused tests. No schema,
backup format, existing database or deployment configuration changes.
API changes: no HTTP/client DTO changes; offline retention --dry-run only.
Migration/backup: no migration or backup format change; supported schema 1-15
backup-first upgrades to 16 and failed-upgrade recovery remain verified. No existing
database was inspected or upgraded. Manual service backups are not pruned; CSS-65
local-app count retention remains unchanged.
Limitations: no target-Mac headroom, index-level size, off-device recovery, paid
storage, native/AMD64, live Discord/Access/Tunnel or public-release certification.
Next exact action: no eligible CSS coding task remains. Await a concrete new scope;
future public readiness must consume this matrix/evidence and separately resolve
open launch/operator gates. Do not resume CSS-68 or activate the service by inference.
Historical deferral handoff below is retained as prior evidence, not current status.

### October 6 Deferral Record

Status / date / implementer: BLOCKED, October 6, 2026, GitHub Copilot.

Outcome: User selected "Defer CSS-64" rather than approving retain-first or
age-based deletion. No runtime, schema, existing database or backup changed.

Files changed: This brief, the task index and G-RETENTION in the decision register.
No contracts, APIs, migrations or fixtures introduced.

Decision: Explicit deferral permits moving to the next eligible task, not choosing
a retention policy or declaring the storage/privacy release gate complete.

Verification: Documentation-only change; task status, relative links and whitespace
checked. Runtime tests are not applicable. Existing cleanup predicates were read,
not changed or executed against an existing database.

Remaining work: All acceptance criteria and retention/storage evidence remain open.
No retain-first proposal, destructive cleanup, backup pruning or payload-guard
migration is authorized. Existing transient cleanup and immutable guards remain.

Next task: CSS-65; read its brief and G-SCHEDULING, then ask whether unattended
local-app backups are selected before implementing scheduling. CSS-64 resumption
starts with an explicit retention matrix and disposable storage measurements.

Direct dependent tasks: None; CSS-68 canceled. CSS-69's local-only pilot remains
complete; a future public readiness review must use the final CSS-64 policy/evidence.

