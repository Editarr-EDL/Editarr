# CSS-27 — Enforce continuing Discord membership

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE**, October 1, 2026. User selected single-member bot REST.
Local/mock membership enforcement is implemented; live setup remains OPS-01/02.

- Prerequisites: [CSS-25](25-worker-request-auth.md), [CSS-26](26-discord-oauth.md), [CSS-70](70-membership-method-comparison.md)
- Decision gates: [G-MEMBERSHIP](decisions.md#g-membership), [G-ADMISSION](decisions.md#g-admission)
- Parent sections: Discord §4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Initial membership is insufficient. D22 requires rechecking on use after 15 minutes and pausing community reads/writes if an expired check cannot refresh. Membership alone suffices; outages remain distinct from verified nonmembership.

## Starting points

community-service/src/integrations/discord/membership.ts; member-provider.ts;
config/migrations; test clock/provider fixtures.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Read CSS-70's technical comparison and obtain the user's method selection; implement that strategy with the confirmed 15-minute/on-use/fail-closed policy. A returning member regains access after successful verification unless separately suspended by Editarr operators.
2. Coalesce bounded checks, honor provider rate limits, and integrate eligibility into every protected request path and caches.
3. Keep known service suspensions authoritative across leave/rejoin; do not require rules-screening completion or an approval role for service access.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Positive/negative/stale/unknown membership and provider-failure cases follow the recorded policy.
- [x] Known ineligibility blocks reads/writes despite valid signatures; stale caches cannot extend the approved window.
- [x] Chosen secret retention matches the decision; no always-on Gateway, broad bot privileges or second strategy is added silently.

## Verification

- Fake-clock tests for expiry, removal, rejoin, provider rate limits/outages and concurrent refresh.
- Route-matrix tests prove cached artifacts, metadata and mutations cannot bypass eligibility.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for local coding scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide eligibility state machine, staleness bound, Discord permissions/secrets and operating tests to CSS-28/42/55/68.

## Handoff summary for the next task

October 1, GitHub Copilot: user explicitly answered "Okay, then let's go with
the bot plan." `createBotMembership` now supplies the existing signed-auth hook
to Worker identity. State machine: fresh eligible/ineligible evidence lasts
strictly less than 900 seconds; expired/unknown evidence refreshes only on use.
Only Discord Unknown Member (404/code 10007) proves absence; outages, missing
access/guild, malformed bodies, missing secret and database errors are unavailable.
Confirmed rejoin restores access without clearing operator suspension. No roles
or rules-screening gate is added. Slow cache reads cannot extend freshness.

`0007_membership.sql` adds evidence and shared refresh coordination, in Unix
seconds. A credential/guild authority hash invalidates evidence on rotation;
no bot token or new Discord linkage is stored in D1. A shared 10-second lease
coalesces concurrent stale checks across isolates; contenders fail closed rather
than waiting. Publication is fenced by lease ownership and completion time.
Provider calls are fixed-endpoint, redirect-denied, 5-second/16-KiB bounded;
decimal retry/reset values and shared cooldowns are honored without retry loops.
Hourly local cleanup bounds each table's deletion to 1000 rows and preserves
active leases/cooldowns. Mutation telemetry measures cleanup, not all membership
request writes; application observations remain partial, never billing totals.

Tests cover expiry/removal/rejoin, suspension during refresh, delayed cache and
provider reads, lease expiry/takeover, cooldown anchoring, malformed response
headers/bodies, and missing bindings. Actual Worker identity and signed read/
mutation authentication tests reject ineligible/unavailable users before application
or protected work. Catalog/artifact/mutation matrix tests exercise the shared
auth boundary, not unimplemented routes. Those future handlers must invoke it
before serving metadata/cached bytes or side effects; CSS-25 remains partial.
Fresh and populated schema versions 0001-0006 upgrade through 0007 preserving
identity, moderation, keys, grants, nonces, operations, counters and browser state.
Focused final provider/membership checks: 41 passed; TypeScript passed.
Final verification: `pnpm run test` passed 240 tests across 30 files;
`pnpm run typecheck`, `pnpm run build` (dry-run only) and `git diff --check`
passed. Deployment preflight remains intentionally blocked, not live acceptance.

Required setup for CSS-28/42/55/68: one Worker secret `DISCORD_BOT_TOKEN`, existing
`DISCORD_GUILD_ID`, and minimal guild installation (`bot`, permissions=0).
Initial OAuth still discards user tokens. No Gateway, bulk-list privileged
intent, retained OAuth strategy or administrator permission is implemented.
See [operator setup](operator-setup.md) for live member/nonmember/inaccessible
guild checks. No bot installation, live requests, deployment, secret provisioning
or existing-store migration occurred. Existing stores require a verified
recoverable backup and authorized migration run; restored evidence is not a
new provider check. Next exact coding action: CSS-28's administrator-confirmed
enrollment handoff with CSS-29/30. OPS-01 separately verifies minimal permissions.

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

```text
Status / date / implementer:
Implemented outcome and last completed substep:
Files changed and relevant revision:
Contracts / APIs / schemas / migrations / fixtures introduced:
Decisions used (gate IDs and recorded answers):
Verification commands and results:
Known failures, limitations, unavailable checks, and recovery notes:
Remaining work in this task:
Next task ID and exact first action:
```

Direct dependent tasks: [CSS-28](28-enrollment-profile.md), [CSS-39](39-publication-service.md), [CSS-41](41-catalog-lookup-api.md), [CSS-42](42-artifact-download-service.md), [CSS-51](51-import-receipts.md), [CSS-52](52-votes-service.md).
