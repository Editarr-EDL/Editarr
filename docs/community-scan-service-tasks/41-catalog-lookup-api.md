# CSS-41 — Serve bounded authenticated availability and match queries

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** October 3, 2026, in local/isolated-test scope. Authenticated
batch availability, compatibility-first paginated matches, bounded metadata cache
and current authority/moderation rechecks are implemented. Production remains
disabled; explicit operational limits and hosted CPU/cost verification remain gated.

- Prerequisites: [CSS-25](25-worker-request-auth.md), [CSS-27](27-membership-eligibility.md), [CSS-36](36-compatibility-policy.md), [CSS-38](38-d1-catalog-schema.md), [CSS-39](39-publication-service.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §7; main §7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Availability counts differ from full match results and artifact downloads. Query failures must never look like zero matches.

## Starting points

community-service/src/catalog/ routes; pagination/cache helpers.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement bounded batch availability and paginated candidate summaries using indexed typed identities.
2. Return match reasons, provenance/count summaries and safe public attribution; reserve ranking extension fields for CSS-53.
3. Cache by work/release/fingerprint/schema/policy while checking current authorization/moderation before serving.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] No N+1 per-item query pattern; SQL parameters/query counts stay bounded.
- [x] Uncertain candidates are a distinct band; no artifact bodies or personal vote/download histories leak.
- [x] Private identity-specific fields and private response headers never contaminate shared cache entries.

## Verification

- Batch boundary/query-plan tests, stable pagination, negative-cache refresh and nonmember/cached authorization cases.
- Contract fixtures verify lookup-offline/service/error distinctions.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide availability/match DTOs, cursors/cache keys and latency/cost baseline to CSS-48/49/53.

## Handoff summary for the next task

October 4 CSS-53 follow-up: ranked candidate DTOs now carry
`community-ranking-wilson-1` quality after both compatibility bands. The original
null-ranking/search-1 contract below is historical. `community-search-2` cursor
and cache keys include ranking policy; changed votes or moderation invalidate
pagination rather than silently skipping/duplicating results. New inserts retain
the revision ceiling. Current counters are still reread on cache hits, with no
additional ranking query. Read the [CSS-53 handoff](53-ranking-policy.md) before
client/feedback/restore integration. Production remains disabled.

Completed October 3, 2026 by GitHub Copilot; uncommitted workspace changes.
The [lookup contract and evidence](decision-evidence/41-catalog-lookup-contract.md)
records exact modules, DTOs, cursor/cache keys, bounds, privacy and failure behavior,
commands and the local latency/cost baseline for CSS-48/49/53.

Implemented `POST /v1/availability/batch` and `POST /v1/matches` using indexed
multi-target queries, current key/member/moderation checks, honest truncated/null
availability outcomes, deterministic compatibility keyset pagination, current
public names/aggregate statistics and identity-free metadata caching. No schema
change, artifact-body read, download receipt, vote history, ranking policy or UI.
The portable fixture is `tests/fixtures/community/catalog-lookup-v1.json`.

Used approved G-MATCHING/CSS-36 and G-MEMBERSHIP/G-ADMISSION rules and D1-only
storage. Bounds/TTLs are explicit local configuration, not invented production
defaults. CSS-25 downstream integrations remain partial.
Final verification: 475 Worker tests/71 files, generated types/typecheck and
Wrangler dry-run passed; 13 new lookup tests include real mocked Worker/native/bot
dispatch. Local 50-item medians were 9 ms cold / 5 ms warm, with 16/13 statements
and 1351/45 locally reported rows read. No hosted CPU/billing claim follows.

Known limitations: bounded candidate universe refuses oversized match searches;
availability truncation is a lower bound. Indexed summaries do not expose every
model/setting or original artifact timestamp. Live bot/production limits/capacity/
deployment remain unverified. Existing databases were not opened or migrated;
there is no new backup inventory or persistent lookup history.

Remaining task work: none in the approved local mechanism scope.
Next exact action: **CSS-42**, read its brief, D1-only scope and CSS-13/39 handoffs,
then implement current-authority/moderation-gated exact BLOB reads, digest checks
and contributor-bound receipts. CSS-48/49/53 first consume this handoff and fixture.

Direct dependent tasks: [CSS-48](48-local-lookup-adapter.md), [CSS-53](53-ranking-policy.md), [CSS-57](57-operator-dashboard-read.md).

