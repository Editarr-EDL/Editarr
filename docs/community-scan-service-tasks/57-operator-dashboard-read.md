# CSS-57 — Build the operator overview and inspection pages

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** (October 4, 2026), local/mock scope only. Live Access,
provider measurements, hosted backup/recovery and production activation remain
unavailable/unperformed.

- Prerequisites: [CSS-41](41-catalog-lookup-api.md), [CSS-53](53-ranking-policy.md), [CSS-55](55-operator-access.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §4–5.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The requested dashboard is a small hosted application with bounded lists. It must show service state without revealing tokens, private scan payloads indiscriminately or individual library history.

## Starting points

web/src/settings/community-operator/; web/src/api/ operator helpers; shared ui/styles; admin read endpoints.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

CSS-56 now provides bounded protected resource inspection and the open report
queue behind `operatorService`; read its [DTO/limits handoff](56-moderation-service.md).
Reuse those reads and escaped private text. Mutation controls remain CSS-58;
unimplemented provider totals and backup status must remain explicitly unavailable.

## Work within this task

1. Build overview/usage, contributions/reports, contributors/keys, operation history and backup-status views.
2. Use paginated safe inspection and distinguish missing/stale/unavailable information; show passive activity and suspension state.
3. Reuse visual primitives but keep operator authorization/runtime independent from local Editarr.

Expose passive global/contributor payload-byte observations from `artifact_storage_usage`, separately from actual database/index/WAL sizes. Do not return admission caps or headroom.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Pages are Access-protected and display only authorized bounded data.
- [x] Untrusted text is escaped; no D1/deployment/provider secret reaches the browser.
- [x] Keyboard/mobile navigation and session-expiry recovery work without losing context.

## Verification

- Component/API fixtures for empty/large/stale data and expired/forbidden sessions.
- Browser axe/responsive checks and distinct hosted/local build output verification.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met locally, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for local/mock scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide dashboard routes/data hooks and status placeholders for CSS-58/60/61.

## Handoff summary for the next task

Status / date / implementer: DONE locally/mock, October 4, 2026, GitHub Copilot.
Clean starting worktree; no commit, deployment, existing-store access, migration,
secret, local Editarr API or backup-format change. Uses existing Access/dashboard,
D1-only storage, CSS-53 ranking and CSS-56 moderation policy. No new decision gate.

### Runtime and Views

The isolated `web/src/settings/community-operator/` dashboard uses hash routes
`/admin/#/overview`, `/contributions`, `/reports`, `/contributors`, `/operations`,
`/audit` and `/backup`. `dashboard.tsx` owns navigation, cancellation, explicit
refresh and selected-resource inspection; `overview.tsx` renders usage/backup
values. `operator-access.tsx` still gates mounting on verified Access identity.
`web/src/api/api-community-operator.ts` contains strict allowlisted DTO readers;
every read is same-origin, no-store, redirect-error and capped at 131,072 response
bytes. It never imports local bootstrap/auth, enrollment or contributor signing.

Lists and key history contain <=20 rows. Hash parameters `after`, `selected`,
`keysAfter` retain page/resource/key-page context through reload and Access
recovery; First/Next page and browser history support navigation. Operation
selection binds contributor/kind/operation, not the repeated operation ID alone.
Page/resource changes abort obsolete requests and clear old data. Failed refresh
retains a visibly stale snapshot; missing resources clear it. 401/403 clears
protected identity/data. Sign in reloads the current document, preserving its hash.
Browser tests found that assigning an already-current URL with a fragment did not
reload; this was corrected. No token is stored in browser state.

React escapes names, private report notes and audit reasons. Native links/buttons,
focusable labeled table scrollers and wrapping facts support keyboard/mobile use.
Styles and the real favicon reuse the operator surface. No mutation control,
local-app layout or auth change is added. `test-fixtures.ts` supplies synthetic
API/component/browser data; it is not imported into either application bundle.

### Protected Read Contracts

`community-service/src/admin/dashboard.ts` is dispatched only inside CSS-55
`operatorService`. New GET routes under `/admin/v1/dashboard/`:

- `overview`: `{version:1,observedAt,period,storage,databaseSize,providerUsage,backup,activity,observations}`.
- `contributions`, `contributors`, `operations`, `audit`: `{version:1,observedAt,source:"service_database",items,nextCursor}`.
- `contributors/{id}/keys`: the same paginated envelope, only key/installation IDs,
  lifecycle state and timestamps; no key bytes or Discord ID.
- `contributors/{id}/usage`: contributor/period/snapshot, payload bytes and passive
  request/write observations with outcome, occurrences, units and freshness.

List cursors are bounded opaque base64 JSON tuples, passed as encoded `after`.
SQL keyset order is revision ID, contributor ID, key ID, action ID, or the complete
contributor/kind/operation tuple. Extra/duplicate query parameters and malformed
cursors return 400; unknown resources 404; unsupported methods 405; query caps
413; missing configuration or verification failure 503.
No HTML fallback, CORS permission or contributor/CLI auth exception exists.
Rows never contain operation results/bodies/digests, artifact/summary BLOBs,
library inventories or secrets. Contributions use fresh CSS-53 revision quality
with zero author boost. Publication dates are normalized from stored seconds to
DTO milliseconds; other exposed timestamps already use milliseconds.

CSS-56 GET `/admin/v1/reports?after={reportId}` and
`/admin/v1/moderation/{revision|contributor|report}/{id}` are reused unchanged.
Inspection supplies state, optimistic `revision` and latest <=20 audit rows from
one D1 transaction. The report queue's browser receive time is labeled
`service_database_received`, not fabricated source freshness. Resolved reports
remain inspectable by ID; the queue lists open reports only.

`src/admin/admission.ts` shares CSS-56's explicit `MODERATION_LIMITS`, atomic
body/query bounds, query observer and passive daily observations with moderation.
No new defaults. High or unknown observations do not refuse reads; configuration
and authority checks still apply. Reads write observations, not moderation actions.

### Measurements and Recovery

Payload `artifact_storage_usage` supplies global and contributor byte observations,
explicitly not total database size or provider capacity. No limits/headroom are returned.
`databaseSize`, `providerUsage` and `backup` each return exactly
`{source:"unavailable",observedAt:null,value:null,providerVerified:false}`.
Daily activity reuses partial application observations with unknown restore
provenance. Separate download/import/vote/report observations preserve known
reads/writes, unknown counts, source and timestamp. They overlap with activity
accounting and are never summed into provider totals. Missing observations remain
null/Unknown, not invented zero. Retired object resources are absent.

No schema, persistent rank, provider credential, private state or backup inventory
is added. CSS-60/61 retain CSS-56's full inventory/restrictions and replace status
placeholders only after an approved source/backup workflow exists. Never reinterpret
payload counters as database-size evidence. Existing upgrades still need explicit
authorization and independently verified recoverable backups.

### Verification

- `pnpm --dir community-service exec vitest run test/operator-dashboard.test.ts
  test/operator-access.test.ts test/moderation-http.test.ts`: **50 passed**.
  Auth-before-read/privacy/JSON errors, hostile text, unknown/provider separation,
  contributor/key/composite-operation pagination, malformed cursors, missing config/
  resource, disabled policy, query caps, dates and no moderation writes covered.
- `pnpm --dir community-service test`: **589 tests / 83 files passed**.
  `typecheck` and dry-run `build` passed; Worker **843.99 KiB / gzip 149.14 KiB**.
  Existing Wrangler Node-types advisory remains; no deployed resource created.
- `pnpm --dir web exec vitest run src/settings/community-operator`: **17 passed**.
  Full serial `exec vitest run --maxWorkers=1`: **275 tests / 40 files passed**.
  Covers strict page/byte/extra-field bounds, unavailable totals, empty/missing/
  forbidden, stale refresh, late-response cancellation and scoped operation IDs.
- `pnpm --dir web build:operator`: passed, **213.64 kB JS / gzip 66.14 kB**,
  separate `web/dist-operator`. `pnpm --dir web build`: passed; local output stays
  **537.26 kB JS** with the existing >500 kB advisory.
- `pnpm --dir web exec playwright test --config playwright.operator.config.ts`:
  **14 Chromium journeys passed**, 320/1440px, keyboard navigation, pagination,
  stale/missing state, expired-session reload/recovery, escaped notes, no local API
  calls, strict CSP/logo, zero tested axe violations and no document overflow.
  Overview/contributor screenshots `web/test-results/css57-*` inspected; narrow
  tables scroll inside labeled keyboard-focusable regions.
- Focused overview probe, `exec vitest run test/operator-dashboard.test.ts
  -t 'measures bounded local overview' --reporter=json`: **14 queries, 26 rows
  read, 4 rows written, 1,663 response bytes** in an isolated minimal fixture.
  Excludes hosted billing/CPU/native/provider work; not production sizing.
- Scoped editor diagnostics and `git diff --check` passed. Existing frontend-only
  preview `http://127.0.0.1:5185/admin/` returned HTTP 200; without approved/mock
  Access it reports verification unavailable, not an authenticated hosted service.
- Inline Node documentation/build check: **5 documents / 131 relative links**,
  balanced fences, completed task/index checklists and distinct operator/local
  asset paths passed.

Limitations: synthetic identities/JWTs/provider answers only. No live Access/session
revocation, hosted D1/account measurements, backup/restore drill, deployment, native
build, Python test, media/model/user database or existing server restart performed.
CSS-25 broader gates remain incomplete. Provider/recovery placeholders are
intentionally unavailable, not missing CSS-57 implementation.

Remaining CSS-57 work: none in authorized local/mock scope.

Next task: **CSS-58**. Read its brief, CSS-56 mutation DTO/policy and this map;
start at `dashboard.tsx` resource inspection and strict operator API helpers.
Use fresh optimistic revisions, exact action identities/reasons and explicit impact/
confirmation. Preserve cancellation/session context; no contributor key rotation or
owner withdrawal. Distinct pause-enrollment/publishing services and per-feature
policy are not implemented/selected: resolve that dependency before adding controls,
never mutate protected runtime configuration through the dashboard. Keep local Report abuse disabled until its
separate local adapter/UI exists. CSS-60/61 replace backup/provider placeholders
only after their own approval and recovery evidence.

Direct dependent tasks: [CSS-58](58-operator-dashboard-actions.md).

