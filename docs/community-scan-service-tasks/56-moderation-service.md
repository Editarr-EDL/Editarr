# CSS-56 — Add reports and audited reversible moderation

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** (October 4, 2026), local/mock scope only. Production, live Access
configuration and existing-database upgrades remain disabled/unperformed.

- Prerequisites: [CSS-39](39-publication-service.md), [CSS-52](52-votes-service.md), [CSS-55](55-operator-access.md)
- Decision gates: [G-OPERATIONS](decisions.md#g-operations)
- Parent sections: main §8–9; contracts §7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Moderation requires reasons, stale-state conflict protection and auditability. It must not erase evidence already downloaded or silently rotate contributor keys.

## Starting points

community-service/src/admin/ moderation; report/audit migrations; contributor report endpoint.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

CSS-54 handoff: `web/src/scans/community-feedback.tsx` has a separate disabled
Report abuse command. No report fields, reasons, local outbox or hosted route
were approved/implemented by CSS-54. CSS-56 now provides the approved hosted API;
keep the command disabled until a separate local report adapter/UI is implemented.
CSS-58 must use a distinct report adapter, never reinterpret dislike/neutral or
extend the vote payload with reasons. Read [54-feedback-ui.md](54-feedback-ui.md).

## Work within this task

1. Implement bounded structured reports and operator inspect/quarantine/restore/suspend actions using shared services.
2. Add expected revision/conflict checks and audit actor/reason/resource/outcome for each mutation.
3. Support reversal/recalculation of abusive votes and cache invalidation; keep owner withdrawal deferred to CSS-63.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Contributor cannot moderate; stale double actions conflict rather than overwrite newer state.
- [x] Quarantine/suspension blocks cached reads and mutations; downloaded local evidence remains.
- [x] Reversal restores only eligible service state and leaves historical authorship intact.

## Verification

- Permission, CSRF, stale-write, audit and moderation/ranking reversal tests.
- Test hostile report text, repeated eligible reports and policy-approved reason codes.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met locally, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for local scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide admin mutation/report DTOs and audit/counter effects to CSS-58/59/61/63.

## Handoff summary for the next task

Status / date / implementer: DONE locally/mock, October 4, 2026, GitHub Copilot.
Clean starting worktree; no commit, deployment, existing database or secret change.

### Approved Policy

User selected G-OPERATIONS answers before implementation: acknowledged successful
import of the exact revision required to report; current Discord member/active key,
non-self author; reasons spam/inaccurate/privacy/rights/other, optional plain-text
note up to 1,000 characters. One open report per contributor/revision, private to
operators, exact retry identity, no report-count automation. Explicit configurable
limits have no production defaults. Report resolution only records handling.

Operators may quarantine/restore individual revisions and suspend/unsuspend
contributors. Suspension hides every authored revision and blocks the contributor's
community access. Unsuspension never unquarantines a revision or changes keys.
Abusive votes are explicitly excluded/restored per contributor/revision. Suspension
alone never changes totals. Keys, vote rows, import facts, signed bytes, historical
authorship and already downloaded local evidence are preserved. No owner withdrawal,
automatic enforcement, deletion/retention policy, CLI bypass or live setup is added.

### Shared Services and APIs

- `community-service/src/contributions/reports.ts` exports strict `reportInput`,
  `reportLimits`, `ReportResult` and `submitReport`. Signed
  `POST /v1/contributions/{revisionId}/reports` accepts `{version:1,reason,note?}`,
  with a fresh `Idempotency-Key` per intent. Response is
  `{version:1,state:"completed",result:{version:1,operationId,reportId,revisionId,state:"open"}}`.
  Retry identical bytes/operation with fresh signature/nonce under an active owned
  key. The saved result is historical, even after operator resolution. Changed
  revision/body conflicts; a separate intent while a report is open returns 409.
- `src/http/reports.ts` enforces current membership/key and trusted ingress
  through `src/index.ts`. Every admitted attempt records passive partial usage in
  `report_usage_observations`. Unknown metadata stays unknown. Responses are
  private/no-store, without individual voter history or echoed report notes.
- `src/admin/moderation.ts` exports strict `moderationInput`, `moderate` and
  `inspectModeration`. Inspection reads state/version/audit in one D1 transaction.
  `src/admin/moderation-http.ts` exposes `POST /admin/v1/moderation`,
  `GET /admin/v1/moderation/{kind}/{targetId}` and vote inspection with trailing
  `/{revisionId}`; kind is revision/contributor/report/vote. Open report queue is
  `GET /admin/v1/reports?after={reportId}` (20 rows, opaque next report-ID cursor).
  Per-resource inspection includes the latest 20 audit entries, never Discord IDs,
  signing/deployment secrets, artifact bytes or library inventories.
- Mutation body: `{version:1,actionId:<UUID>,command,targetId,revisionId?,expectedRevision,reason}`.
  Commands: quarantine/restore/suspend/unsuspend/exclude_vote/restore_vote/resolve_report.
  `revisionId` is required only for vote actions; nonempty reason <=1,000 characters.
  Result: `{version:1,actionId,resource,revision,outcome:"applied"}`. Resource names
  are `revision:{id}`, `contributor:{id}`, `report:{id}`, `vote:{contributor}:{revision}`.
  Retry is bound to exact parsed intent and verified operator subject; an old
  successful retry returns its original revision, not current state. Current state
  requires inspection. Concurrent stale actions return 409, invalid restoration 403,
  missing inspection resource 404, per-operation bounds 413 and unavailable service 503.
- All operator routes run inside CSS-55 `operatorService`, with verified Access
  subject/current allowlist, exact configured host, same-origin mutation/CSRF and
  private/no-store/CSP headers. Contributor signatures never grant operator access.
  Malformed or unauthenticated requests never create moderation actions. Valid
  applied/conflicting/ineligible attempts preserve actor/reason/resource/outcome;
  successful state effects and audit insertion are atomic. No CLI exception exists.

Configuration: `REPORT_LIMITS` is explicit JSON with positive `requestBytes<=8192`,
`maxQueries<=64`; daily and maximum-open allowances are retired.
`MODERATION_LIMITS` has only positive `requestBytes<=8192` and `maxQueries<=64`.
These are per-operation validation ceilings,
not selected production values. Both routes fail closed when configuration is absent.
Operator admission uses Access/CSRF, explicit configuration and bounded query work;
request/write observations remain partial application evidence, not provider billing.

### Persistence, Visibility and Recovery

Additive **0014_moderation.sql**, not applied to an existing database, introduces
`reports`, `moderation_actions`, `vote_exclusions`, `moderation_versions`,
`moderation_epoch`, `operator_request_counts` and `report_usage_observations`.
Reports retain original text/author/revision/key; only open-to-resolved changes.
Audit rows cannot be updated/deleted. Exclusions reference preserved vote/import
facts; vote updates cannot clear exclusion. Atomic exclusion/restoration rebuilds
that revision's totals and leaves import counts intact. `reconcileVoteCounts`
now excludes active exclusions. Restore checks counted non-self import, active
service contributor/author and published revision; it does not fabricate fresh
Discord evidence or reactivate a retired key. Ordinary access loss still preserves
stored votes, per CSS-52 policy. No score is persisted or inherited.

Publication/contributor/vote/report changes advance resource revisions, so stale
actions cannot overwrite newer state, including newer contributor vote updates.
Publication transitions use `RETURNING` rather than trigger-inclusive D1 change
counts. Quarantine/restore/suspension changes advance a persisted catalog epoch;
all isolates invalidate positive/negative cache keys and prior visibility cursors.
New publication preserves existing pagination ceilings. Fresh author-state checks
protect lookup plans and metadata/byte cache hits; the catalog response and download
receipt paths have final authority/visibility fences. Vote exclusions change fresh
ranking totals/snapshots, invalidating stale quality cursors without changing the
compatibility-first ordering. Local Editarr evidence is never touched.

CSS-60/61 full backup inventory must include all seven new tables, report operations,
source votes/imports/statistics, immutable BLOBs, authors and historical keys, schema
revision, epoch and optimistic-write tokens. Preserve explicit restored exclusions;
never recount all source votes as active. Retain audit and report evidence and original
operation identities pending CSS-64. Restore into a separately disabled environment;
reconcile counters from source facts plus exclusions without clearing restrictions.
Deployment Access configuration and receipt signer/history remain separately protected
inventory. Before any existing-store upgrade, obtain authorization and independently
verify a recoverable full backup/isolated restore; never reset or stamp unknown schema.

### Verification

- `pnpm --dir community-service test`: **577 tests / 82 files passed**.
  Covers report reasons/hostile text/eligibility/privacy, concurrent exact retries,
  body conflicts, daily/open/query/byte caps, native/bot/config/production refusal,
  atomic late quarantine, Access/CSRF/alternate-host denial, stale moderation,
  immutable audit, suspended author cache denial, exclusion/update/restore,
  warm-cache ranking/cursor reversal and raced final catalog visibility.
- `pnpm --dir community-service typecheck`: passed after explicitly typing
  heterogeneous D1 inspection rows. Scoped editor diagnostics reported no errors.
- `pnpm --dir community-service build`: dry-run passed, **834.63 KiB / 147.21 KiB gzip**;
  local D1, rate-limit and operator-assets bindings only. Operator build remains
  isolated and unchanged; no frontend/local Editarr API or Python change.
- Focused `exec vitest run test/reports.test.ts test/moderation.test.ts
  test/votes-upgrade-v12.test.ts test/catalog-upgrade-v8.test.ts`: **14 passed**.
  Existing fresh/populated supported upgrade helpers now advance through 0014,
  snapshotting every preexisting application row and checking foreign keys.
  Populated 0013 coverage preserves votes, import facts, operations/counters and
  exact signed artifact BLOBs. D1 rejects `PRAGMA integrity_check`; supported
  row-preservation/FK checks were run instead. No existing-store backup was tested.
- Focused `exec vitest run test/catalog-http.test.ts test/moderation-http.test.ts`:
  **18 passed** after final race/ranking changes. Local report handler measurement
  stays within the 32-unit reservation; native/bot/hosted billing/CPU are excluded.
  Full-suite costs are local regression evidence, not production sizing.
- Inline `node --input-type=module -e` probe using `node:sqlite` applied 0001-0014
  to a newly created disposable file-backed database, seeded synthetic suspended
  contributor/denied audit rows, checked integrity/FKs, used SQLite `backup`,
  reopened the backup read-only and compared all **47 tables**: passed. Initial
  fixture lacked 0008's required display_name_key; corrected without changing
  application code. Both temporary databases were removed. This is schema/backup
  compatibility evidence, not an existing-store or hosted restore drill.
- Inline Node documentation check: **8 documents / 207 local links**, balanced
  fences and completed task checklist passed; `git diff --check` passed.

Limitations: all identities/JWTs/provider responses are synthetic. Live Access,
membership outages, hosted D1 limits/CPU/account usage, deployment, hosted backup/
restore drills, native builds and browser journeys were not performed. Browser
code/layout is unchanged; Report abuse remains disabled pending its local adapter.
No existing database, secret, model, media file, server or local review was changed.
CSS-25 remain incomplete for their broader operational gates.

Remaining CSS-56 work: none in authorized local/mock scope. No production defaults
or deployment authorization follow from these tests.

Next task: **CSS-57**. Read its brief and CSS-41/53/55 handoffs plus this API map;
start at `web/src/settings/community-operator/` and bounded protected admin reads.
Build read-only overview/inspection with explicit unavailable provider/backup values,
not CSS-58 mutation controls. CSS-58 reuses the mutation DTO/expected revision and
escaped private report text; CSS-59 must settle CLI authentication, never bypass
CSRF; CSS-61 restores exclusions/audit/version/epoch intact; CSS-63 preserves
standing reuse and operator-only moderation, without author withdrawal.

Direct dependent tasks: [CSS-58](58-operator-dashboard-actions.md), [CSS-59](59-operator-cli.md), [CSS-61](61-hosted-restore.md), [CSS-63](63-sharing-policy-withdrawal.md), [CSS-64](64-retention-cleanup.md).

