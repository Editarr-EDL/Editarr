# CSS-58 — Wire reviewed moderation and operational controls

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** (October 4, 2026), local/mock scope only. Separate enrollment/
publishing pause controls were explicitly deferred by the user. Live Access,
production activation and CLI authentication remain separate operational gates.

- Prerequisites: [CSS-56](56-moderation-service.md), [CSS-57](57-operator-dashboard-read.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §4; main §9.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Operators need concrete impact previews and confirmations for destructive actions. UI and CLI must use the same audited service operations.

## Starting points

web/src/settings/community-operator/; operator API hooks and tests.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

CSS-57 handoff: `dashboard.tsx` provides read-only resource inspection, hash
page/selected/cursor context and expired-session recovery;
`web/src/api/api-community-operator.ts` supplies strict bounded readers. Reuse
CSS-56's fresh state/optimistic revision and audited mutation DTO, not list-row
state as a write token. Backup/provider measurements remain unavailable.
Distinct pause-enrollment/publishing services and per-feature policy are not
implemented/selected; the user deferred those controls on October 4. Never
change protected runtime configuration through this API or treat read-only disabled-service inspection as
authorization for a new mutation. Local reports need a separate adapter/UI;
do not extend the vote payload or silently enable its disabled command.

## Work within this task

1. Add quarantine/restore/suspend/reverse-vote controls with reasons and expected state versions. Separate enrollment/publishing pauses are explicitly deferred.
2. Display affected objects/credentials and irreversible limits before confirmation; keep contributor rotation absent.
3. Handle stale conflicts, partial failure and retry using the operation contract; connect report review flow.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Cancelled confirmations issue no mutation; repeated clicks cannot duplicate action.
- [x] Stale state forces refresh/review rather than silently applying against a changed record.
- [x] Every successful action has matching audit and updated authorization/cache behavior.

## Verification

- Browser journeys for moderation, conflict, expired session, cancel and reason validation.
- Verify accessibility of confirmations/errors and UI/API authorization separately.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met locally, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for local/mock scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide operator workflows and audit examples to CSS-59/67.

## Handoff summary for the next task

Status / date / implementer: DONE locally/mock, October 4, 2026, GitHub Copilot.
Clean starting worktree; no commit, schema/migration, secret, deployment, existing
database, local API, media/model or server restart. Uses CSS-55 Access, CSS-56
approved report/moderation policy and CSS-57 inspection. G-OPERATIONS records the
user's exact selection: **Defer separate pause controls**. No runtime configuration writes,
owner withdrawal, contributor rotation, key reactivation or automatic enforcement.

### Operator Workflows

`web/src/settings/community-operator/moderation-actions.tsx` owns confirmation,
single-flight submission, exact intent retry and individual vote inspection.
`dashboard.tsx` mounts it only for inspected revision/contributor/report resources
and disables new actions while snapshots are loading/stale. Contributions offer
quarantine for published and restore for quarantined revisions. Contributors offer
suspend/unsuspend; the fresh preview separately reads the first <=20 credential
rows and labels further keys as also affected. Suspension affects every installation
without changing key lifecycle, source votes or totals. Restoration never overrides
membership, author or independent revision restrictions.

Reports retain escaped private notes and link to the exact reported revision and
reporter. Inspect/quarantine content separately, then explicitly resolve the report
with another reasoned action. Resolution alone changes neither content nor access.
Contributors' Individual vote form accepts an explicit revision ID; protected exact
vote inspection returns `value` and numeric `excluded` (0/1), not a public voter
directory. Exclude/restore affects only that contributor/revision; vote audit is
shown after refreshed inspection. Local Editarr Report abuse remains disabled;
its transport/UI adapter is separate downstream work, not an extension of votes.

Before each confirmation, fetch a fresh transactional resource snapshot and compare
its optimistic revision with the inspected token. Changed state aborts confirmation
and requests refresh/review. The native modal previews the exact resource, state,
credential scope and limits (including inability to recall local downloads); it
requires a nonempty <=1,000-character reason. Its header and command footer stay
visible while details scroll. Escape/Cancel before submission sends no mutation;
focus returns to the opener. A synchronous guard prevents repeated-click writes.

`api-community-operator.ts` reuses the bounded same-origin/no-store/redirect-error
transport for `POST /admin/v1/moderation`, with JSON and `X-Community-Operator: 1`.
No local bootstrap/auth or contributor signing dependency. Result validation binds
version, exact action UUID/resource, expected revision + 1 and applied outcome;
extra fields and >131,072-byte responses fail closed. Reasons reject control bytes.
No new hosted API or persistence contract is introduced.

Each confirmed intent generates one UUID. Network errors, rate/availability failures
or invalid success responses retain the exact ID/body/reason/token for explicit
Retry saved action. Closing that uncertain dialog only collapses it; a new action
on that component remains blocked. Applied retries return the historical result,
then the UI freshly reloads current state/audit rather than projecting the old result.
Conflicts/invalid requests/missing targets/ineligible restoration end the attempt
and force refresh/review, never automatic resubmission against a newer token.
403 `moderation_ineligible` is a policy refusal; other 403 and 401 clear protected
Access state and confirmations while retaining the hash for sign-in recovery.

The retry intent is in-memory for the mounted resource, not browser persistent
storage. Navigation, document reload or Access loss may discard it; return to
fresh resource/audit inspection before deciding another action. Aborting browser
transport is not a rollback of an already submitted server operation. An explicit
close of an uncertain dialog is not reported as cancellation or success.

### Audit Examples And Shared Contract

For synthetic revision `revision-1` at revision 2, a confirmed quarantine posts
`{version:1,actionId:<UUID>,command:"quarantine",targetId:"revision-1",expectedRevision:2,reason:"Reviewed revision evidence"}`.
Success returns resource `revision:revision-1`, revision 3 and outcome `applied`.
Inspection/audit shows the verified actor, same ID/command/reason, expected revision
and applied outcome. A competing action with stale token 2 returns 409 and records
conflict; the UI requires fresh review. An exclusion additionally binds
`revisionId:"revision-1"` and resource `vote:contributor-1:revision-1`. Source vote
and import rows remain; only the exclusion and rebuilt totals change. These are
existing CSS-56 atomic server actions, not a second business-logic implementation.
CSS-59 must reuse this contract and settle CLI authentication before any exception.

### Verification

- `pnpm --dir web exec vitest run src/settings/community-operator`: **28 passed**.
  Cancellation, reason/control-byte validation, duplicate clicks, strict result/
  response bounds, changed fresh snapshots, conflict, policy refusal, Access denial,
  exact unknown retry and collapsed intent tested alongside CSS-55/57 regressions.
  jsdom requires a test-only `showModal` shim; real native focus uses browser tests.
- `pnpm --dir web build:operator`: passed, **223.97 kB JS / gzip 69.22 kB**,
  separate operator output. `pnpm --dir web build`: passed; local output remains
  **537.26 kB JS**, with the existing >500 kB advisory.
- `pnpm --dir web exec playwright test --config playwright.operator.config.ts`:
  **18 Chromium journeys passed**, including all seven commands, reason/cancel,
  report links/audit, individual votes, conflict, exact uncertain retry, expiry,
  responsive layout and isolation. 320/1440px; tested axe violations/overflow zero.
  After screenshot-driven dialog framing improvement, the focused
  `--grep 'reasoned moderation|conflicts require'` **4 journeys passed again**, with
  heading/command visibility and refreshed viewport screenshots
  `web/test-results/css58-confirm-{320,1440}.png` inspected.
- `pnpm --dir community-service exec vitest run test/moderation.test.ts
  test/moderation-http.test.ts test/operator-access.test.ts
  test/operator-dashboard.test.ts test/catalog-http.test.ts`: **70 passed**.
  Independent Access/CSRF authorization, exact idempotency, optimistic conflict,
  immutable atomic audit, eligibility, updated totals/visibility/cache covered.
- Scoped editor diagnostics passed. Documentation links/fences/checklists and
  `git diff --check` verified at completion. Existing frontend-only preview remains
  `http://127.0.0.1:5185/admin/`; without approved/mock Access it is unavailable,
  not an authenticated deployed service.

Limitations: synthetic identities/JWTs/provider answers only; live Access/revocation,
hosted D1/account/CPU, non-Chromium, backup/restore, packaging/native, full unrelated
application/Worker suites and Python tests not rerun. No existing DB or deployment
was changed. CSS-25 broader operational gates and CSS-60/61 placeholders remain.
Separate enrollment/publishing pauses are explicitly deferred, not secretly enabled
by the protected configuration and current authority checks.

Remaining CSS-58 work: none in authorized local/mock scope.

Next task: **CSS-59**. Read its brief plus CSS-55/56 and this shared action contract.
Start at `community-service/src/admin/auth/access.ts` and `admin/service.ts`; prepare
the necessary CLI authentication proposal and ask G-OPERATIONS before adding any
non-browser authorization path. Reuse exact UUID/reason/resource/revision and audited
results; do not bypass CSRF, implement deferred feature pauses or invent backup jobs.

Direct dependent tasks: [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md).

