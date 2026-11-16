# CSS-52 — Implement contributor votes and revision-safe aggregates

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally/mock**, October 4, 2026. Approved voting policy, signed
routes, atomic revision-safe state/totals and preserving upgrades are verified.
Production remains disabled; additive D1 0013 is unapplied to existing stores.

- Prerequisites: [CSS-25](25-worker-request-auth.md), [CSS-27](27-membership-eligibility.md), [CSS-51](51-import-receipts.md)
- Decision gates: [G-REPUTATION](decisions.md#g-reputation)
- Parent sections: main §8.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Like/dislike/neutral is one mutable state per contributor and immutable revision. Imported parent votes must not become support for derived contributions.

## Starting points

community-service reputation migrations/repositories/routes; shared vote DTOs.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add unique vote state with optional approved reason codes and required import eligibility.
2. Atomically handle create/change/neutral with aggregates; reject self-votes and unauthorized contributors.
3. Keep abuse reports separate; expose own vote privately and public aggregates without individual histories.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Concurrent changes preserve exact totals and one current state.
- [x] New revision/derivative starts without inherited votes; retries and credential rotation do not create another voter.
- [x] Restricted/suspended/nonmember contributors follow approved eligibility policy.

## Verification

- Exhaustive vote transition/concurrency/idempotency tests and private-cache isolation.
- Recompute aggregates from source records to detect drift.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide vote rules/DTOs and authoritative totals to CSS-53/54/56/61.

## Handoff summary for the next task

Status / date / implementer: DONE locally/mock / 2026-10-04 / GitHub Copilot.
Initially clean worktree; no commit made.

### Policy and Contracts

User approved G-REPUTATION voting October 4: only an acknowledged successful
import of the exact revision permits voting, with current Discord membership,
active key and no self-votes. Upload restrictions do not restrict voting.
Suspended contributors/nonmembers cannot read their private vote or change it;
stored votes/totals persist during access loss and verified return. No reason
codes for now. Reports, ranking/tier definitions and voting-specific production
tiers are not selected or implemented. Existing contributor storage has active/
suspended states, not upload tiers; no upload-tier check is added to voting.

- [Vote repository/DTOs](../../community-service/src/contributions/votes.ts)
  exports `VoteResult`, `OwnVote`, `VoteAggregates`, strict `voteInput`, `voteLimits`,
  `setVote`, `readVote` and internal `reconcileVoteCounts`. Votes are like/dislike/
  neutral, one mutable state per contributor/immutable revision, not installation.
- [Signed HTTP routes](../../community-service/src/http/votes.ts):
  `POST /v1/contributions/{revisionId}/vote`, JSON `{version:1,vote}`, a new
  `Idempotency-Key` for each intent. Response
  `{version:1,state:"completed",result:{version:1,operationId,revisionId,vote}}`.
  Retry original exact bytes/operation with a fresh signature/nonce under an
  active same-contributor key. Operation digest binds revision and HTTP content
  digest; changed body or revision conflicts. Old retries return their original
  outcome without reapplying it, even after a newer vote. Distinct concurrent
  intents serialize in D1; last committed vote is current, not client issue time.
- Signed `GET` on `/vote` returns `{version:1,revisionId,vote,eligible}` only for
  the caller; absent rows project neutral. Signed `GET` on `/votes` returns
  `{version:1,revisionId,likes,dislikes}`. No individual identity/history is public;
  even public aggregate reads require normal membership/authentication. All
  success/error responses are private/no-store and bypass any result cache.
  Missing/hidden revisions return 404; ineligible/self mutations return 403;
  replay/body conflicts return 409, storage/provider outage 503, unavailable service/
  membership 503. No browser/local vote adapter is added; CSS-54 owns that work.
- [Additive D1 0013](../../community-service/migrations/0013_votes.sql) creates
  `revision_votes` with composite primary/foreign key to `import_receipts`, an
  aggregate index, explicit neutral and last requester key/time. The key is a
  mutation-authority fence, never voter identity. Insert/update guards reject
  missing/noncounted/self imports, inactive/foreign keys, suspended contributors
  and unpublished revisions. Nonce, completed operation, vote and trigger-based
  aggregates commit atomically. No vote/counter inheritance through supersedes
  or parent lineage. No lookup last-seen/history write. Operator row deletion
  adjusts counters but no operator route/authority/audit is supplied here.
- `reconcileVoteCounts(db,revisionIds<=100)` independently rebuilds likes/dislikes
  from source votes using the revision/value index, preserving imports. No public
  or contributor maintenance route; CSS-56/59/61 must supply operator authority.
- `vote_usage_observations` holds UTC daily contributor/outcome known read/write
  sums, explicit unknown counts and freshness. Shared transfer observation helper
  gained `recordVoteUsage`. Worker config/dispatch adds explicit `VOTE_LIMITS`
  positive requestBytes<=8192 and maxQueries<=64, no defaults. Native admission,
  current membership/key and trusted ingress apply to
  reads and writes. Every admitted authenticated attempt, including malformed/
  duplicate votes, records passive request/write observations without admission charges.
- [Frozen shared DTO fixture](../../tests/fixtures/community/vote-v1.json) verifies
  mutation, completed result, own vote and aggregates under real signed HTTP.
  It freezes wire shapes for downstream Python/frontend consumers, not a new
  signing algorithm. No local schema, secret, artifact or signature format changes.

### Verification

- `pnpm --dir community-service test`: **516 tests / 77 files passed**.
  Includes exhaustive nine transitions/no-ops, concurrent same/distinct intents,
  old retry after new vote, altered content, cross-installation and retired-key
  recovery, author/nonimport denial, access loss/verified return, signed private
  isolation/public totals, malformed/reason-field/size/query refusal,
  replay rollback, key retirement and quarantine at the mutation boundary,
  new revision/parent independence, reconciliation and real Worker native/bot/
  configuration/production refusal. An initial nonexistent `mixed` fixture was
  corrected to existing `manual`/`zero` plus explicit supersedes/parent fixtures;
  reruns and full suite passed without changing application expectations.
- Focused `exec vitest run test/votes.test.ts test/votes-upgrade-v12.test.ts`:
  **15 passed**. Existing upgrade helper now carries fresh/populated 0001-0011
  fixtures through 0013, preserving every application row and foreign keys;
  new populated 0012 fixture preserves exact signed artifact BLOBs, import facts,
  operations/counters and observations. No existing database/reset exercised.
- `pnpm --dir community-service typecheck` and dry-run `build` passed:
  **758.33 KiB / gzip 130.07 KiB**. Existing Wrangler Node-types advisory remains.
  Eight touched TypeScript editor diagnostics checks reported no errors.
- Isolated focused measurement (`exec vitest run test/votes.test.ts -t
  'measures local mutation/retry' --reporter=json`): first/retry **15/14 queries,
  47/48 rows read, 26/9 rows written**, including handler accounting. Metadata is
  historical evidence, not a current write allowance. Trusted ingress,
  Discord work, outer observation, hosted CPU/index costs and account-wide billing
  are excluded. This is local regression evidence, not production sizing.
- Documentation validator passed **244 relative links**, balanced fences and
  the completed CSS-52 checklist; `git diff --check` passed.

### Recovery and Next Work

No remaining CSS-52 implementation in approved local/mock scope. Before applying
0013 to an existing D1 database, obtain authorization and verify a recoverable
full backup/isolated restore with integrity, foreign keys, inventory and exact
signed bytes. CSS-60/61 must include both new tables, import facts, vote/receipt/
publication operations, statistics and historical keys. Service secrets/history
remain a separate protected inventory. Do not restore votes by incrementing totals;
restore source rows/counters together and use authorized reconciliation if needed.
Retain current votes, explicit neutral, operation identities and referenced imports/
keys pending CSS-64; no automatic cleanup or vote removal is introduced.

No existing database migration, live service/provider/account/media/model,
deployment, frontend/native layout or packaging exercised. No Python/frontend
tests run because their code is unchanged. Reports, audited moderation, ranking,
tiers, UI/client and voting-specific production caps remain downstream. Broader
CSS-25 operator coverage remains incomplete. Production stays disabled.

Next task: **CSS-53**. Read [53-ranking-policy.md](53-ranking-policy.md), this
handoff and G-REPUTATION. Start with compatibility-first catalog ordering and
durable `revision_votes`/reconciled statistics. Prepare a concrete ranking table
for new scans, sparse/negative feedback and abuse cases; ask for formula/prior/
capped uploader boost, tier thresholds and decay approval before implementing
unapproved policy. Import/upload volume is never direct trust, and no parent or
previous-revision votes may be inherited.

Direct dependent tasks: [CSS-53](53-ranking-policy.md), [CSS-54](54-feedback-ui.md), [CSS-56](56-moderation-service.md), [CSS-61](61-hosted-restore.md), [CSS-64](64-retention-cleanup.md).

