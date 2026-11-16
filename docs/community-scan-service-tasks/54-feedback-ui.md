# CSS-54 — Add voting, report controls and reputation displays

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally/mock**, October 4, 2026. Protected persisted vote transport,
private fresh feedback, compatibility-first quality sorting and responsive controls
are verified. Report submission remains explicitly deferred to CSS-56; no report
fields or moderation authority invented. Production remains disabled.

- Prerequisites: [CSS-50](50-find-filters-downloads.md), [CSS-51](51-import-receipts.md), [CSS-52](52-votes-service.md), [CSS-53](53-ranking-policy.md)
- Decision gates: [G-REPUTATION](decisions.md#g-reputation)
- Parent sections: main §7–8.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The UI should explain displayed counts and let eligible users change votes without presenting a dislike as an abuse report.

## Starting points

web/src/scans/ contribution details; scanner/community/ vote transport/outbox; web/src/api/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Show unique import counts, likes/dislikes, revision-only quality and supported
  sorting. CSS-53's approved policy defers author reputation boosts and reputation tiers;
  keep author reputation unavailable, not fabricated from volume.
2. Implement like/dislike/neutral with retry-safe local transport. CSS-52 approved
  no vote reason codes for now; do not add a reason selector. Keep abuse report
  intent separate pending the moderation API and approved report fields.
3. Explain ineligibility/self-vote restrictions, stale totals and pending changes.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Optimistic changes roll back or remain explicitly pending on failure; duplicate retries never inflate totals.
- [x] Unknown legacy stats and low vote counts are not represented as established trust.
- [x] Accessible controls work across local reused and newly downloaded contributions.

## Verification

- Component/API/browser tests for vote transitions, self/ineligible vote, stale response and offline retry.
- Verify private own-vote state does not leak across users or cached result entries.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide feedback UI/transport and report intent seam to CSS-56/58/66.

## Handoff summary for the next task

Status / date / implementer: DONE locally/mock / 2026-10-04 / GitHub Copilot.
Worktree was clean at start; no commit made. Existing databases were not opened
or migrated, and production remains disabled.

### Feedback and Display

- [Result state](../../web/src/scans/community-result-state.ts) keeps both video
  and audio compatibility bands ahead of quality, imports, newest and uploader
  sorts. Default/catalog and revision-quality sorts use CSS-53 scores, descending,
  then ASCII revision IDs. Legacy null quality sorts last within equal bands,
  remains unavailable, and never becomes an invented score. Other sorts use the
  same deterministic ID tie-break. No client reclassification or channel changes.
- [Result rows](../../web/src/scans/community-filter-row.tsx) label unique imports,
  actual likes/dislikes, catalog snapshots, nine-decimal revision quality and actual
  rating-vote counts. Zero votes are explicit; one vote is limited evidence. Quality
  is not a trust probability; volume is not trust. Author reputation remains
  unavailable, with zero author boost and unrestricted local usage. No fabricated legacy
  rating, model/settings or author-trust data.
- [Feedback controls](../../web/src/scans/community-feedback.tsx) use native
  labeled like/dislike/neutral radios and explicit Check feedback / Retry saved
  vote commands. No feedback request on mount, polling, enrollment or receipt
  acknowledgment. Current feedback is a separate private read, never candidate
  cache data or a historical operation result. Pending selection is visibly
  selected but not claimed as confirmed; failures hide stale own state/totals
  and retain an explicitly pending change. Success always triggers fresh reads.
- Controls reset/abort on revision, target snapshot or stale/disabled changes;
  generation guards discard late replies. Reopening starts with no cached own
  vote and can recover a saved pending operation through Check feedback. Fresh
  reads replace changed contributor state. Authors and unacknowledged imports
  are explained; membership, suspension and unavailable-revision errors
  disable new changes rather than fabricating eligibility. A fresh download and
  existing local reuse share the same revision feedback controls.

### Local Transport and API

- [VoteOutbox](../../scanner/integrations/community/votes.py) uses existing
  `community_operations`, with approved `like`/`dislike`/`neutral` in `vote` and
  exact `{version:1,vote}` JSON in the existing bounded payload field `receipt`.
  No schema, migration, new secret or signed artifact change. The repository
  still accepts legacy `up`/`down` records, but this adapter refuses to translate
  or send those payloadless intents (`legacy_vote_intent`). They are preserved.
- Prepare requires committed exact-revision local evidence, non-self author and
  an acknowledged original receipt operation matching service/contributor,
  revision/digest/scan. `BEGIN IMMEDIATE` serializes the pending-intent check and
  insert; identity projection repair happens before that transaction. Only one
  unresolved local intent per contributor/installation/revision can be prepared.
  Persist before network; mark submitted before mutation. Hosted current
  membership/eligibility checks remain authoritative.
- Retry sends the same saved body/revision/operation with fresh signatures/nonces
  through the existing bounded transport. Identity/service/installation checks
  fence pending reads and retries. Completed responses must exactly match the
  saved intent; cancellation/lost/malformed replies retain submitted state. An
  acknowledged local retry never resends or purports to describe current state.
- [Protected API](../../scanner/http/community_votes_api.py):
  `GET /api/community/votes/{revision_id}` returns
  `{version:1,revisionId,scope:{serviceUrl,contributorId},current,totals,pending,error}`.
  `current` is `{vote,eligible,reason}`; totals `{likes,dislikes}`; pending
  `{operationId,vote,state}`. Remote failure returns null current/totals plus safe
  error and any local pending intent. Partial own/aggregate reads are not exposed.
  Scope contains only public contributor/service IDs, never voter histories.
- `POST` on the same route accepts strict
  `{vote,operation_id:"vote-...",confirmed:true}` and prepares/sends one intent.
  `POST /{revision_id}/retry` accepts `{operation_id,confirmed:true}` and sends only
  the saved intent. Both return the CSS-52 completed-result envelope. Extra/reason
  fields are refused. Real administrator, same-origin/CSRF and no-store apply
  even with local/external bypass; routes are in the protected inventory.
- [Browser API](../../web/src/api/api-community-votes.ts) validates exact private
  DTO shapes, safe counters, revision/operation identity, states and own/error
  consistency. Uses mounted same-origin CSRF transport, AbortSignal and no-store.
  The existing [shared vote fixture](../../tests/fixtures/community/vote-v1.json)
  verifies completed-wire compatibility, not current own-vote state.

### Reports and Decisions

G-REPUTATION October 4 CSS-51/52/53 answers are reused without changes: counted
exact-revision import, current membership, no self votes/reasons, retained votes
during access loss, revision-only Wilson score, no decay/author boost/reputation tiers.
Reports are separate from dislikes. The Report abuse command is deliberately
disabled with an unavailable-service title. No report modal, fields, persistence,
sender, usage policy, reason policy or operator authority is introduced. CSS-56 must
obtain approval for report fields/policy and provide an authenticated report API;
CSS-58 then wires that command through a separate report adapter, never the vote
outbox. This is the task brief's explicit deferral, not a missing implemented form.

### Verification

```sh
.venv/bin/python -m pytest -q tests/test_community_votes.py tests/test_community_api.py tests/test_community_repository.py tests/test_community_import_receipts.py tests/test_community_local_downloads.py tests/test_community_lookup.py tests/test_backup_restore.py tests/test_auth_policy.py
pnpm --dir web exec vitest run --maxWorkers=1
pnpm --dir web build
pnpm --dir web exec playwright test e2e/find-filters.spec.ts e2e/community-results.spec.ts e2e/community-downloads.spec.ts
```

- **167 backend tests passed**, including all three votes, lost responses/exact
  fresh-signature retries, blocked second intent, historical-result/current-vote
  distinction, explicit cancellation, restored exact bytes, self/unacknowledged
  and foreign-identity denial, malformed/extra/unsafe values, admin/CSRF/bypass
  protection and shared import/lookup regressions. Focused vote file: **15 passed**.
- Four-mode backup/preflight tests retain neutral submitted intent, exact body,
  state, revision and contributor in full plain/encrypted backups, and exclude
  votes from settings-only archives. Separate SQLite restore sends the exact
  original operation/body rather than manufacturing a new vote. No existing
  database was used as a fixture or wiped.
- **257 frontend tests / 38 files passed**, including strict API/shared-vector,
  compatibility-first ties, offline retry, stale reply/candidate isolation, changed
  contributor own-state replacement, self/ineligible and report separation.
  Build/typecheck passed: **537.26 kB JS**, existing >500 kB advisory remains.
- **14 Chromium journeys passed** at 320x900 and 1440x900, including download
  eligibility, native keyboard vote selection, lost reply/reopen/retry, a newer
  sibling vote surviving historical retry, neutral change, local reuse without
  another download, stale lookup, focus, axe and page overflow checks. No implicit
  review/export writes. `web/test-results/css54-feedback-{width}.png` screenshots
  were visually inspected; labels/controls fit both viewports.
- Nine changed Python files pass Ruff check and format check. Twenty touched
  code-file editor diagnostics are clear; final documentation checks passed
  212 relative links, balanced fences and task/index status. `git diff --check` passed.
  Pylance-specific tools are unavailable; existing venv runtime tests and editor
  diagnostics verify Python behavior. Worker implementation is unchanged; no
  redundant full hosted suite, actual hosted performance or billing measurement.
- Resolved test/development findings: formatting/newlines, unsupported Testing
  Library role option, reused consumed mock Response, wrong shared fixture key,
  transient radio deselection, duplicate feedback landmarks, and old browser
  reputation-label expectation. Relevant checks were rerun successfully.

### Recovery and Next Work

No remaining CSS-54 work in approved local/mock scope. Full backups retain pending
vote intents with existing imports/identity/operations; settings-only backups do
not include local vote work. Restore requires the same bound identity/service and
an active key. No background retry, cleanup, automatic enrollment/receipt sending,
review edits or counter increments on the client. Unknown/malformed outcomes stay
pending and retry exact saved intent; only fresh signed reads establish current
state. Do not translate legacy payloadless vote rows or silently purge them.

No live service/Discord/provider/account/media/model, deployment, existing migration
or native packaging exercised. Non-Chromium/native platforms and real hosted costs
remain unverified. Hosted D1 0012/0013 remain unapplied to existing stores; production
activation still requires separately approved configuration/resources.
Frontend-only preview: `http://127.0.0.1:5184/`; no backend was started. Community
actions require separately configured/enrolled backend and hosted service access.

Next: **CSS-55**. Read [55-operator-access.md](55-operator-access.md), CSS-22/25
handoffs and G-OPERATIONS before editing. Start at Worker routing/shared auth and
keep operator JWT/allowlist authority distinct from contributor signatures. CSS-25
still has downstream operator coverage pending; do not treat that as complete or
invent production Access audience/issuer/hosts/allowlist. Prepare local mock scope
and ask about any unresolved major policy/configuration.

CSS-56/58/66: preserve the private feedback DTO/outbox and disabled report command
described above. Approve report fields before enabling it; do not reinterpret
dislike/neutral as moderation, inherit votes, or mutate immutable local evidence.

Direct dependent tasks: [CSS-66](66-integration-and-abuse.md).

