# CSS-26 — Implement the hosted Discord authorization broker

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE**, September 30, 2026. Configured local Worker broker,
trusted ingress and passive observation wiring and OAuth-to-signed-handoff security
are implemented and verified with mocked providers and Workerd bindings.
Downstream contributor enrollment and OPS-01/02 live setup are separate tasks;
no enrollment completion route or non-local service is enabled.

- Prerequisites: [CSS-22](22-worker-scaffold.md), [CSS-23](23-d1-identity-schema.md), [CSS-24A](24a-broker-admission.md) (user-approved completed trusted-ingress broker scope).
- Decision gates: None beyond the shared rules.
- Parent sections: Discord §3; contracts §1.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Discord authenticates the account; its client secret stays in the Worker. OAuth state and the resulting authorization must belong to the initiating installation.

## Starting points

community-service/src/auth/discord/; hosted enrollment page; bounded D1 state.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement configured hosted start/callback using authorization code, identify and guilds.members.read; server-side user/membership fetch.
2. Bind one-use state to browser session, enrollment public key, audience and expiry; show explicit installation connection confirmation.
3. Exchange authorization for a short-lived internal enrollment grant, never exposing provider tokens or accepting an arbitrary redirect destination.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Wrong/replayed state, substituted key, denied consent, nonmember and duplicate callback do not authorize enrollment.
- [x] Secrets/codes are redacted; a public enrollment ID alone cannot claim the grant.
- [x] Flow works with mocked provider responses and arbitrary self-hosted Editarr origins without wildcard OAuth callbacks.

## Verification

- Test login-CSRF, handoff phishing/substitution, callback replay and provider timeout/rate limit.
- Use no real user accounts/resources; live smoke awaits operator setup and scope authorization.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide enrollment grant/state schema, scope list, registered callback requirements and token cleanup rules to CSS-28/29.

## Handoff summary for the next task

Status / date / implementer: DONE, September 30, 2026, GitHub Copilot.

Current outcome, October 7: the Docker service requires trusted ingress and configured authority, records passive observations and has no user request buckets or usage-pressure pauses. Downstream client and live provider setup remain independently gated. See [CSS-24A](24a-broker-admission.md) for the current contract.

Final files: broker/page/admission/service/usage modules, Worker dispatch/config,
local Wrangler native bindings/hourly cron, additive migration 0006, admission,
usage, service/recovery and supported-upgrade tests, service/migration READMEs,
task handoffs and decisions. Existing provider/proof contract version is unchanged;
the additive response header `Community-Usage-Status: unavailable` signals
post-transition telemetry failure without hiding committed redirects or grants.
No commit, external setup, secret configuration or existing-store migration was
performed. Locally declared native namespace IDs are not hosted provisioning.

Final verification: `pnpm run test` passed **196 tests in 27 files**;
`pnpm run typecheck`, `pnpm run build` (Wrangler dry run) and `git diff --check`
passed. Native 60/300 binding tests and fresh/populated 0001-0005 upgrades to
0006 passed. Fault injection confirms confirmation/callback outcomes survive
accounting failures/missing metadata, overlapping timestamps do not cause false
outages, known writes include accounting overhead, and cleanup removes expired
state without touching live state or contributor history. A final independent
review found no remaining blockers in this scoped broker.

Known limitations: no real Discord account, registered live callback, deployed
Worker, hosted billing or production CSP journey was tested. Partial observations
are not Cloudflare billing; native limits are per location/eventually consistent.
Downstream profile/enrollment/retry routes and ongoing membership verification
remain in CSS-27/28/29/30, with OPS-01/02 owning live setup. Preserve the default-
disabled policy and non-local 503 guard until separate prerequisites are approved.
Applying 0006 to any existing store requires a verified recoverable backup and
explicit migration authorization.

The following notes retain earlier implementation/verification history. Current
status and configuration are governed by the final handoff above.

Implemented locally: `src/integrations/discord/oauth.ts` accepts a browser-openable
start with a canonical installation UUID, SHA-256 key ID and raw Ed25519 public
key. It issues an HttpOnly Secure SameSite=Lax cookie and shows the complete
installation ID and signing-key fingerprint before explicit confirmation.
The authorization request asks only for `identify guilds.members.read`, with
the fixed callback `https://community.editarr.org/v1/discord/callback` and
application ID `1554924079486607490`. Arbitrary self-hosted installations open
this hosted flow; no client redirect URL or wildcard OAuth callback is accepted.

Both successful and denied callbacks validate and atomically consume the
same confirmed, browser/origin-bound state. Mixed code/error, duplicate state
or cookies, changed key/installation bindings and replay are rejected before
provider calls. The server checks `/users/@me` and current-user membership in
guild `1554889314549243986`; only matching string snowflakes authorize a grant.
Provider timeouts, 429s, malformed JSON/types and oversized replies fail closed
with redacted errors. Start JSON is strict and streamed to a 2,048-byte bound;
provider JSON is streamed to a 16,384-byte bound. These defensive parsing bounds
are not approved production request-rate or service-budget limits.

Admission contract: `discordOAuth(request, env, nowMs, fetchProvider, admit)`
requires a trusted `OAuthAdmission` hook before broker D1/provider work.
`allowed` proceeds; `limited` returns redacted 429; absent, unavailable,
invalid or throwing admission returns redacted 503. The hook must supply bounded
pre-D1 abuse protection and the service's admission/pause policy without relying
on attacker-selected identity. `src/integrations/discord/admission.ts` now
implements a native Cloudflare `RateLimit.limit({key})` adapter. Its dependencies
are trusted Tunnel metadata and available handler configuration. There are no request buckets or usage-pressure reads. Missing or invalid authority fails closed.

Schema / lifetime handoff: additive `0005_discord_oauth.sql` creates
`discord_oauth_states(state_id, browser_hash, installation_id, key_id, public_key,
confirmed, expires_at, consumed_at)`. State IDs and cookie tokens are independent
256-bit random values; only an origin-bound SHA-256 cookie hash is stored.
State lifetime is ten minutes from start. A verified callback creates a grant
in existing `enrollment_states`, with the same state/installation/key and private
provider subject, expiring ten minutes after callback. State/grant timestamps
use milliseconds. No migration was changed or applied to an existing store.
Fresh and populated schema versions 0001 through 0004 upgrade in separate
Workerd fixtures, preserving suspension, key history, grants, nonces and operations.
Applying to a real store still requires a verified recoverable backup and an
approved numbered migration run; tests are not a backup or deployment approval.

Token / code cleanup rules: codes are sent only to Discord's fixed token endpoint;
all token/user/member requests use `redirect: 'error'`, so provider redirects
cannot forward authorization codes, client secrets or bearer credentials.
client secrets stay in the Worker binding. Access tokens are used only for the
two bounded provider requests. No access/refresh token, code or client secret is
stored in D1, placed in response HTML, redirected to a client, or logged by this
broker. Provider error details are never reflected. Token material is discarded
with callback execution; remote token revocation is not implemented or claimed.
Any retained-token recheck strategy requires the user's CSS-70 choice and a
separate secret-storage design. Infrastructure/proxy logs must also redact
callback query strings before production. Success clears the browser cookie;
claimed failures require a new start, not replaying the old authorization.
`pruneDiscordOAuth(db, nowMs, limit)` removes bounded expired browser states;
`pruneExpiredAuthenticationState(db, nowSeconds, limit)` removes Unix-second
nonces and converts explicitly for millisecond grant expiry. Local-only hourly
cleanup now invokes these bounded helpers and prunes anonymous usage detail;
its mutations are recorded in partial application observations.

CSS-28/29 handoff: `src/identity/enrollment-proof.ts` verifies the exact CSS-12
signed handoff against the grant's stored key, then atomically registers the
installation, consumes the grant and inserts a contributor-bound nonce. Nonce
expiry uses Unix seconds, matching ordinary signed requests. End-to-end tests
exercise a real generated key through mocked OAuth, reject a stolen public
state ID and permit only one signed consumption. Full-schema integration found
and corrected a missing nonce contributor ID and mismatched cleanup units.
Suspended contributors remain suspended and cannot complete registration;
retired/revoked keys cannot re-register. The proof helper is not a Worker route
or local client; lower-level persistence still trusts its internal caller.

Files / fixtures: broker, shared `src/http/bounded-body.ts`, proof/identity and
authentication-cleanup helpers; `test/discord-oauth.test.ts`,
`test/discord-oauth-security.test.ts`, file-isolated `test/discord-oauth-upgrade*.test.ts`,
shared test helpers and full-schema proof tests. No production configuration,
secrets, applied migrations, external resources or deployment were changed.

Local acceptance evidence: wrong/replayed/unconfirmed state, substituted key,
denied consent, nonmembership and duplicate callbacks cannot issue a grant;
secrets/codes are absent from responses and a public ID cannot pass key proof;
an arbitrary self-hosted browser origin uses the fixed hosted callback. All
three acceptance bullets have mocked evidence, not production/live sign-off.
Task and master completion follow the scoped mocked criteria and approved
CSS-24A split; operational and downstream tasks remain unchecked.

Verification: final `pnpm run test` passed 136 tests across 20 files;
`pnpm run typecheck` and `pnpm run build` (dry run) passed. Focused broker/proof/
identity cleanup run passed 70 tests; fresh plus four supported upgrade paths
passed in independent Workerd databases. Generated confirmation HTML was checked
with Playwright at 1440x900 and 320x700: document width equaled viewport width,
fingerprints stayed in bounds and the Continue button accepted keyboard focus.
The temporary markup probe was removed. This was synthetic HTML inspection,
not a live Discord browser journey, production CSP test or live permission check.

Styling follow-up: `src/integrations/discord/pages.ts` owns confirmation and
verified HTML, matching the main Editarr authentication theme (charcoal surface,
blue primary action, existing embedded Editarr favicon and system font fallback).
Static CSS is authorized by its exact SHA-256 CSP hash; scripts remain denied,
forms remain same-origin, and no external image/font/style requests are allowed.
Displayed identities/form values remain escaped. Browser checks against the actual
renderer with response CSP at 1440x900 and 320x700 verified the icon loads,
styles apply, full identity details wrap without overflow and the confirmation
button accepts keyboard focus. Verified handoff uses the same layout without
claiming enrollment is finished. Page/CSP/escaping tests cover this renderer.
Final styling verification: `pnpm run test` passed 139 tests across 21 files;
`pnpm run typecheck`, `pnpm run build` (dry run) and `git diff --check` passed.

Current admission follow-up: `createOAuthAdmission` validates the protected
Tunnel actor identifier. Payload keys, OAuth state, cookies and forwarding
aliases cannot establish ingress trust. There are no request buckets.
Trusted actor validation runs before handler/database/provider work. Missing dependencies, invalid actors and thrown errors return unavailable without exposing sensitive details. Repeated valid requests remain allowed.

The earlier provider-native admission implementation is superseded by the
October 7 local-service decision. No request buckets or recorded-usage pauses remain.

Follow-up verification: focused adapter/broker security run passed 41 tests;
final `pnpm run test` passed 163 tests across 22 files, `pnpm run typecheck`,
`pnpm run build` (dry run) and `git diff --check` passed. Provider redirect fixtures
reject redirects at token, identity and member endpoints without issuing grants,
and cannot replay claimed callbacks. All provider/limiter work is synthetic;
no real provider credentials or resources were used.

Remaining work in CSS-26: **none** within the approved mocked broker scope.

Next task and exact first action: CSS-70, perform the official-source bot versus
retained-OAuth comparison and ask the user which continuing-membership method
CSS-27 should implement. CSS-28/29 consume the private grant and signed proof
boundary, and own local administrator confirmation, profile/enrollment routes,
retry contracts and client fixtures. OPS-01/02 separately verify redirect
registration, approved environment/secret management and dedicated live member/
nonmember checks before any routing change. Those operational actions are not
authorized by marking this bounded implementation task DONE.

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

Direct dependent tasks: [CSS-27](27-membership-eligibility.md), [CSS-28](28-enrollment-profile.md).

