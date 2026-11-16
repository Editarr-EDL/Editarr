# CSS-51 — Count unique successful imports with retry-safe acknowledgments

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally/mock**, October 4, 2026. Approved counting, signed
acknowledgments, post-commit retry/recovery and preserving upgrades are verified.
Production remains disabled; additive D1 0012 is unapplied to existing stores.

- Prerequisites: [CSS-25](25-worker-request-auth.md), [CSS-27](27-membership-eligibility.md), [CSS-38](38-d1-catalog-schema.md), [CSS-42](42-artifact-download-service.md), [CSS-44](44-verified-local-import.md)
- Decision gates: [G-REPUTATION](decisions.md#g-reputation)
- Parent sections: main §8; contracts §6.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Downloads mean acknowledged successful local imports per contributor/revision, not transfers or humans. Self-imports do not earn popularity.

## Starting points

community-service receipt/count repositories and routes; scanner/community/ receipt outbox.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Verify service receipts and atomically upsert the durable unique import record/counters.
2. Send acknowledgment only after local commit; retry independently using persisted receipt/operation identity.
3. Expose count semantics and support counter reconciliation from durable records.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Repeated downloads, restored queues and concurrent acknowledgments count once.
- [x] Wrong contributor/revision/digest/receipt or absent local successful import path cannot be accepted by the legitimate client.
- [x] Self-import does not increase displayed popularity; no last-seen write on every lookup.

## Verification

- Concurrency/forgery/retry tests in D1 plus local crash-after-import acknowledgment recovery.
- Record row-write amplification and retention dependency for self-hosted capacity planning.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide receipt endpoint/outbox and authoritative counter rules to CSS-52/53/54/61.

## Handoff summary for the next task

Status / date / implementer: DONE locally/mock / 2026-10-04 / GitHub Copilot.
Initially clean base `b93b12b4cced98d984a8ae52f06952a02d4f941a`; no commit made.

### Implementation and Contracts

- [Hosted import repository](../../community-service/src/contributions/imports.ts)
  verifies the existing service Ed25519 receipt against independently configured
  active/retired public history, contributor/service/revision/digest and stable
  receipt identity. Revoked/unknown receipt keys fail, including on retries.
  Current requester key/contributor/membership gates apply. No private signer
  is required; receipts prove authorized transfers, not actual playback or honest
  usage by modified clients.
- [Signed route](../../community-service/src/http/imports.ts):
  `POST /v1/import-receipts`, body `{version:1,receipt:<original envelope>}`,
  `Idempotency-Key: import-<receiptId>`. Response
  `{version:1,state:"completed",result:{version:1,operationId,revisionId,
  artifactDigest,receiptId,counted}}`. `counted` means eligible non-self import,
  not a new increment on this request. Exact HTTP bytes authenticate each request;
  after cryptographic verification, operation idempotency uses the stable receipt
  identity, excluding issue time/key/signature and transport digest. Independently
  issued valid receipts from sibling installations reconcile rather than conflict.
  Changed bindings or invalid signatures fail. Nonce, completed operation, unique
  import fact and counter commit atomically.
- [Additive D1 0012](../../community-service/migrations/0012_import_receipts.sql)
  adds immutable `import_receipts`, primary key `(contributor_id,revision_id)`,
  unique `receipt_id`, bound digest, immutable `counted` and original acknowledgment
  time. Only a new non-self fact increments `contribution_statistics.imports`;
  self facts persist with `counted=0`. No per-lookup last-seen, receipt refresh,
  vote/tier/trust write. Internal `reconcileImportCounts(db,revisionIds<=100)`
  rebuilds imports from facts without changing likes/dislikes. No public or
  contributor maintenance route is exposed; CSS-59/61 must supply operator authority.
- [Local outbox](../../scanner/integrations/community/receipts.py) requires a
  clean Session and committed import/scan/exact artifact digest, stages only the
  original saved receipt/operation ID, refreshes service public history, verifies
  the receipt and strictly checks the result against the import. Commit `submitted`
  before network mutation; uncertain/cancelled replies retain retry state. Mark
  `acknowledged` only after a matching result; acknowledged local intents stay terminal.
- `ReceiptOutbox.recover(limit<=100)` repairs missing intents after an import-commit
  crash using a bounded query, without sending or enrolling. Find Filters attempts
  acknowledgment after local commit; failures return durable receipt state and
  never undo scan/target approvals or change reviews/sidecars. Cancellation remains
  reconcilable through existing local download status.
- Protected local `POST /api/community/import-receipts/recover` returns `{importIds}`;
  `POST /api/community/import-receipts/{import_id}/retry` requires `{confirmed:true}`
  and returns `{importId,state:"acknowledged",result}`. Real administrator,
  same-origin/CSRF and no-store apply even with bypass modes. No browser-supplied
  receipt/key/service identity, startup scheduler or implicit enrollment.
- `IMPORT_LIMITS` explicitly supplies `{requestBytes,maxQueries}` (positive,
  at most 8192/64), with no default; `DOWNLOAD_RECEIPT_KEYS` retains trusted history.
  Worker dispatch stays local-only. Existing CSS-25/27 native admission,
  signatures, membership, trusted ingress and passive usage hooks are tested for this
  route. Every authenticated admitted attempt, including malformed/duplicate
  receipts, records passive request/write observations. Recorded activity never refuses acknowledgments.
- New `import_usage_observations` stores daily known row reads/writes, explicit
  unknown counts and freshness, not individual import histories. The
  [shared transfer observer](../../community-service/src/budgets/download-usage.ts)
  retains null metadata and response unavailable status. No publication charge
  helper or fixed write allowance remains in the current runtime.
- [Shared acknowledgment vector](../../tests/fixtures/community/import-acknowledgment-v1.json)
  freezes request/result using the independently signed CSS-42 receipt. Receipt/
  artifact signatures and local schema/secret inventory are unchanged. Other
  changed modules: Worker config/dispatch, upgrade helpers/download-usage test,
  local download orchestration/transport/API/auth inventory, local API tests and
  the focused Worker/Python receipt test files. Parent/API/task docs are aligned.

### Decisions and Verification

User approved G-REPUTATION counting October 4: one contributor/revision across
installations, channel selections, restores and retries; author adds zero, two
other contributors add two, and each new revision has its own count. Counts are
statistics, never trust points. Vote/ranking/tier decisions remain open.

- `pnpm --dir community-service test`: 500 tests / 75 files passed before the
  final shared-vector test. Final focused `exec vitest run test/import-receipts.test.ts`:
  10 passed including that vector. Covers concurrency, independent issuance/sibling
  keys, self exclusion, forgery/bindings/revoked keys, replay rollback/new revisions,
  malformed/service failure, reconciliation, native/membership Worker wiring,
  unknown telemetry and production/configuration refusal.
- Focused import/download-usage/catalog-upgrade/artifact-upgrade suite: 20 passed
  in 12 files. Fresh/populated supported 0001-0011 paths preserve every existing
  application table, including signed 0010 BLOBs and populated 0011 observations;
  foreign-key checks pass. Upgrade helper snapshots all application rows before
  0012. No existing database was reset or migrated.
- `pnpm --dir community-service typecheck` and dry-run `build` passed,
  748.81 KiB / gzip 129.13 KiB. Existing Wrangler Node-types advisory remains.
  Publication/HTTP regression after the charge adjustment: 19 passed.
- `.venv/bin/python -m pytest -q tests/test_community_import_receipts.py
  tests/test_community_api.py tests/test_community_local_downloads.py
  tests/test_community_downloads.py tests/test_community_verified_import.py
  tests/test_community_artifact_signatures.py tests/test_community_request_signatures.py
  tests/test_backup_restore.py tests/test_auth_policy.py`: 176 passed before the
  final shared vector. Final outbox/vector run: 11 passed. Covers lost response
  after remote commit, fresh nonces with unchanged body/operation, missing-intent
  recovery, restored pending queues, corrupt evidence/responses, clean-session
  refusal, independent import success, admin/CSRF and existing four-mode backups.
- Changed Python Ruff and six key-file editor diagnostics passed. Documentation
  links/fences/task-index status and whitespace validated at final handoff.
- Measured local D1 handler first/retry: 15/12 queries, 41/42 rows read, 27/9
  rows written, including handler accounting. Test metadata retains measurements
  from the historical handler; the former fixed write charge is removed. Trusted ingress,
  Discord work, outer observations, hosted CPU/index costs and account-wide
  billing are excluded; this is not production sizing.

### Recovery and Next Work

No remaining CSS-51 implementation in approved local/mock scope. Before applying
0012 to an existing D1 store, obtain authorization and verify a recoverable full
backup/restore plus trusted service key history/secrets. CSS-60/61 must include
both new tables, operations/statistics, exact BLOBs and service key history.
Retain durable import facts pending CSS-64; never delete deduplication facts while
trimming telemetry/retry records. Acknowledgment time is immutable. Existing full
local backups preserve imports and pending/submitted/acknowledged operations;
settings-only backups exclude them. No new local migration or secret.

No live service/provider/account/media/model, existing database, deployment,
frontend layout or native packaging exercised. No votes/reports/ranking, operator
route, automatic donation, author withdrawal or cleanup implemented. Hosted
performance and other platforms remain unverified.

Next task: **CSS-52**. Read [52-votes-service.md](52-votes-service.md), this
contract and G-REPUTATION. Start with `import_receipts` as durable contributor/
revision eligibility evidence; ask for vote eligibility and reason codes before
implementing unapproved policy. Keep one mutable vote per immutable revision,
self exclusion and independent aggregate reconciliation. Never inherit parent or
previous-revision votes or derive trust from these import counts.

Direct dependent tasks: [CSS-52](52-votes-service.md), [CSS-53](53-ranking-policy.md), [CSS-54](54-feedback-ui.md), [CSS-61](61-hosted-restore.md), [CSS-64](64-retention-cleanup.md).

