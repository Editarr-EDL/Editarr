# CSS-53 — Implement the approved reputation and ranking formula

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally/mock**, October 4, 2026. Approved revision-only formula,
compatibility-first ordering, fresh-counter recalculation and strict client DTOs
are verified. Author boosts and author reputation tiers explicitly deferred;
no decay or schema changes. Production remains disabled.

- Prerequisites: [CSS-36](36-compatibility-policy.md), [CSS-41](41-catalog-lookup-api.md), [CSS-51](51-import-receipts.md), [CSS-52](52-votes-service.md)
- Decision gates: [G-REPUTATION](decisions.md#g-reputation)
- Parent sections: main §8; D12.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Compatibility must dominate quality rank. Downloads/uploads remain visible statistics and cannot directly buy trust or privileged access.

## Starting points

community-service/src/reputation/; catalog sorting; shared deterministic ranking fixtures.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement the user-approved prior/formula/caps/eligibility/decay with versioned policy and stable tie-breaks.
2. Rank quality only within match bands, with conservative handling of few votes.
  October 4 user decision: uploader boost cap is zero; author boosts and automatic
  author reputation tiers are explicitly deferred, not required for this task.
3. Expose understandable aggregate fields and support recalculation/moderation reversals without changing signed artifacts.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Wrong-cut popular scans never overtake stronger-compatible bands.
- [x] New/derived/revised scans do not inherit parent votes or manufacture independent agreement.
- [x] Duplicate uploads/downloads alone do not raise trust; policy changes invalidate affected ranking caches.

## Verification

- Approved adversarial ranking examples for few votes, prolific uploaders, abuse reversal and compatibility conflicts.
- Deterministic sort/pagination tests plus bounded query/cost measurement.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide formula/version, worked examples, rank API fields and recalculation path to CSS-54/57/61.

## Handoff summary for the next task

Status / date / implementer: DONE locally/mock / 2026-10-04 / GitHub Copilot.
No commit made; existing work preserved.

### Approved Formula

User approved G-REPUTATION: Wilson lower-bound revision quality with z=1.96,
one positive and negative prior vote, only after both video/audio compatibility
bands. Neutral does not count as rating evidence. No age decay; CSS-52 retains
votes during access loss. After explaining author boosts and reputation tiers, user
approved the revision-only approach: **zero uploader boost, unrestricted local usage**.
Both optional mechanisms are explicitly deferred. Future author evidence,
deduplication rules, boost caps, tier thresholds and ranking thresholds need approval.

For actual likes L and dislikes D, n=L+D+2, p=(L+1)/n, z=1.96:

$$
q = \frac{p + z^2/(2n) - z\sqrt{p(1-p)/n + z^2/(4n^2)}}{1+z^2/n}
$$

Clamp to [0,1], round half-up to nine decimals. Policy version:
`community-ranking-wilson-1`. `voteCount=L+D` excludes prior and neutral votes;
`qualityScore=q`, `uploaderBoost=0`, `score=qualityScore`. Negative, fractional
or unsafe counter totals fail closed. This is a conservative ordering statistic,
not a calibrated trust probability or proof of independent human voters.

| Actual likes/dislikes | Score | Outcome within equal bands |
| --- | --- | --- |
| 0/0 | 0.094528655 | New, revised and derived revisions use the same prior |
| 1/0 | 0.207654955 | Sparse praise stays below established praise |
| 10/0 | 0.646114078 | More corroborating votes increase quality |
| 100/0 | 0.946540780 | Cannot cross a stronger compatibility band |
| 0/10 | 0.014864709 | Negative evidence ranks below an unrated revision |
| 5/5 | 0.253778170 | Mixed evidence uses count as well as proportion |

### Implementation and Contracts

- [Hosted pure policy](../../community-service/src/reputation/ranking.ts) exports
  `rankRevision`, `Ranking`, version and score scale. No author, import, upload,
  parent or age inputs; no uploader fields, author promotions or tiers created.
- [Catalog search](../../community-service/src/catalog/search.ts) orders video
  band, audio band, descending score, ASCII revision ID. `compareCandidates`
  governs sorting and cursor positions. A million likes cannot overtake stronger
  video or audio compatibility. Classifications/strong eligibility are unchanged.
- Candidate `ranking:{policyVersion,qualityScore,voteCount,uploaderBoost,score}`
  is outside immutable artifact evidence. Actual counts remain in `statistics`.
  Authentication, membership, trusted ingress and passive observations and signed bytes unchanged.
  No public voter history or unauthenticated ranking route is introduced.
- Existing bounded `currentPublicState` rereads counters on each cache hit;
  scores are never cached or persisted separately. CSS-52 vote triggers and
  `reconcileVoteCounts(db,revisionIds<=100)` supply authoritative totals. Future
  authorized moderation reversals update source votes/reconcile before fresh
  lookup; no operator authority/route/audit is added here. Imports are preserved.
- Ranking policy is part of cache/cursor identity. `community-search-2` positions
  contain both bands, negative integer score units and ID, plus SHA-256 of bounded
  ranked revision IDs/vote counts. Changed votes/visibility return existing 400
  `invalid_cursor`: restart without a cursor. Old versions and changed policies
  fail closed; new inserts retain the original revision ceiling. Imports/display
  names alone do not invalidate rank order. No unbounded SQL or extra DB query.
- [Local verifier](../../scanner/modeling/community_ranking.py),
  [typed models](../../scanner/modeling/model_community_lookup.py) and
  [lookup adapter](../../scanner/integrations/community/lookup.py) independently
  verify non-null scores from counters; altered score/count/boost/policy/extra
  fields fail as `invalid_response`. Python half-up quantization matches JS.
  Pylance-specific tools unavailable; configured 3.11 venv runtime tests and
  editor diagnostics validate this slice.
- [Browser API DTO](../../web/src/api/api-community-lookup.ts) checks policy,
  score bounds/consistency, zero boost and safe counts. Python/browser accept
  legacy `ranking:null`. Older null-only clients reject ranked payloads: coordinate
  Worker/client release, no silent stripping or guessed scores. CSS-54 owns
  visible sorting/displays and local vote transport; no layout change here.
- [Precise shared vectors](../../tests/fixtures/community/ranking-v1.json) freeze
  version/prior/z/precision/zero boost/no decay and six cases. Worker/Python check
  exact scores; browser tests check DTO projection. No schema, secret, new
  configuration, persistent rank or artifact/signature format change.

### Verification

- `pnpm --dir community-service test`: **523 tests / 78 files passed**.
  Focused `exec vitest run test/ranking.test.ts test/catalog-http.test.ts`:
  **17 passed**, including examples/monotonicity/unsafe counters, wrong video/audio
  cuts, stable ties, parent/prolific-author neutrality, committed like-to-neutral
  reversal on warm cache, volume neutrality, stale vote/moderation cursors,
  original insert ceiling, old versions and changed policy refusal.
- Worker `typecheck` and dry-run `build` passed: **759.98 KiB / gzip 130.74 KiB**.
  Existing Wrangler Node-types advisory remains.
- `.venv/bin/python -m pytest -q tests/test_community_lookup.py
  tests/test_community_local_downloads.py tests/test_community_verified_import.py
  tests/test_community_import_receipts.py tests/test_community_api.py`:
  **122 passed**. Focused lookup: **34 passed**, including exact vectors, signed
  projection, legacy null and corrupt ranking denial. Four changed Python files
  pass Ruff check/format check after final newline/expression format corrections.
- Focused browser API: **13 passed**. Initial parallel frontend run: 241 passed,
  one saved-review retiming rendered-range expectation failed. It passed isolated;
  final `pnpm --dir web exec vitest run --maxWorkers=1`: **242 / 36 files passed**.
  No unrelated source/expectations changed. Web build passed, JS **530.58 kB**,
  with existing >500 kB advisory. Ten touched code-file diagnostics have no errors.
- Isolated `exec vitest run test/catalog-http.test.ts -t 'measures ranked
  cold/warm' --reporter=json`: two synthetic published revisions, cold/warm
  **14/13 queries, 66/43 rows read, 8/8 writes, 5919 response bytes**, local wall
  **7/4 ms**. Includes signed handler/passive observations, excludes native/bot/outer
  work and actual hosted billing/CPU. Metadata retained in test; not production
  sizing or hosted performance evidence.
- Node documentation validator passed **260 relative links**, balanced fences and
  completed CSS-53 checklist; `git diff --check` passed.

### Recovery and Next Work

No remaining CSS-53 work in approved scope. Author boosts/tiers explicitly
deferred. No existing DB access/reset/upgrade, live service/provider/account/media/
model, deployment, browser layout or native packaging exercised. No browser E2E:
visible interactions unchanged. Production disabled; hosted costs/platforms unverified.

No new rank backup state: preserve CSS-52 votes/imports, operations, statistics,
keys/schema together. After authorized restore/reversal, reconcile from source
votes if needed; ranking recalculates inline. No startup writer or automatic
permission change. Old cursors restart after coordinated Worker/client release.

Next: **CSS-54**. Read [54-feedback-ui.md](54-feedback-ui.md), CSS-50/51/52 and
this handoff. Start at `web/src/scans/community-result-state.ts` and candidate/
download rows; expose quality only within equal bands, actual counts and honest
legacy-null/low-vote states. Reuse protected admin/CSRF patterns for a persisted
exact-intent vote adapter. Own votes are private; saved outcomes are not current
vote state. No vote reason codes or fabricated author trust/tiers. Keep report
intent separate and ask for unapproved report fields; operator mutations remain
CSS-56. No automatic enrollment or review edits.

Direct dependent tasks: [CSS-54](54-feedback-ui.md), [CSS-57](57-operator-dashboard-read.md), [CSS-61](61-hosted-restore.md).

