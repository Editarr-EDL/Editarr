# CSS-55 — Protect the operator UI and API with Cloudflare Access

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** (October 4, 2026), local/mock implementation only. Live Access
configuration and deployment verification remain OPS-02; production is disabled.

- Prerequisites: [CSS-22](22-worker-scaffold.md), [CSS-25](25-worker-request-auth.md)
- Decision gates: [G-OPERATIONS](decisions.md#g-operations)
- Parent sections: contracts §7; D15/D17.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Operator authorization is separate from contributor keys and Discord membership. Both static dashboard assets and administrative APIs must be protected.

## Starting points

community-service/src/admin/auth/; asset routing/deployment config; isolated operator frontend entry.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Validate Access JWT signature/issuer/audience/expiry and the configured operator allowlist.
2. Protect dashboard/API routing including workers.dev/preview/SPA fallbacks; add origin/CSRF controls for operator mutations.
3. Set up a separate frontend runtime/build entry that never uses local Editarr session URLs or sends deployment secrets to the browser.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Forged headers, wrong audience, expired token and contributor signatures cannot grant operator access.
- [x] Alternate hosts and asset fallbacks cannot bypass protection or mask API denial.
- [x] Local Editarr build/session behavior remains unchanged.

## Verification

- Route-matrix and JWT fixture tests plus isolated frontend build smoke.
- Live Access verification is explicitly pending until authorized operator setup; do not call mocks production evidence.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met locally, and relevant verification passed.
- [x] Confirmed Access/dashboard choice implemented; G-OPERATIONS live values remain explicitly pending, with no production defaults.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for the local/mock scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide operator auth middleware, build outputs, allowed hosts and configuration checklist to CSS-56/57/59.

## Handoff summary for the next task

Status / date / implementer: DONE locally, October 4, 2026, GitHub Copilot.
Clean starting worktree; no commit, deployment or existing-database change.

Implemented outcome: `community-service/src/admin/auth/access.ts` verifies the
Access assertion with pinned JOSE 6.2.3, RS256 only, configured issuer/audience,
required expiry/issued-at/subject, application-token type and exact subject
allowlist. Forged email headers, bearer/contributor credentials, duplicate or
malformed assertions, future issuance, expired tokens and wrong algorithms fail.
Keys come only from the configured team's `/cdn-cgi/access/certs`; the bounded
JOSE remote key cache supports rotation without caching authorization results.
Every request rechecks the current allowlist and exact configured HTTPS origin.
There is no database, Discord or local administrator dependency.

Middleware / routes: `src/admin/service.ts` is the authenticated dispatcher used
by `src/index.ts` for `/admin` and its entire subtree, behind the existing
local-only guard. `GET /admin/v1/session` returns exactly
`{"version":1,"subject":"<verified Access sub>"}`. Unknown admin API paths
return JSON 404, never HTML. Future handlers must stay behind this boundary and
use the verified principal for their own permission/stale-write/audit checks.
All non-GET/HEAD admin requests require exact `Origin`,
`X-Community-Operator: 1`, and same-origin `Sec-Fetch-Site` when present.
No cross-origin preflight is granted. No moderation action or CLI exception exists.

Assets / frontend: `/admin`, `/admin/`, `/admin/index.html` and bounded flat
`/admin/assets/<file>` paths serve only after JWT verification. The binding
receives neither assertions nor cookies; protected responses are private/no-store,
nosniff, no-referrer and frame-denied, with a strict self-only resource CSP.
Wrangler uses `OPERATOR_ASSETS`, `run_worker_first: true`, no HTML rewriting or
SPA fallback, and disabled workers.dev/preview URLs. Direct `/index.html`,
`/assets/*`, unknown paths and alternate origins cannot reach protected assets.
The isolated `web/operator/index.html`, `vite.operator.config.ts` and
`src/settings/community-operator/` produce `web/dist-operator/index.html`
and hashed JS/CSS/favicon assets. Local `web/src/main.tsx`, bootstrap and
administrator auth remain unchanged. Operator runtime calls only same-origin
`/admin/v1/session`, rejects redirects/login HTML/unexpected DTO fields, clears
identity while checking and ignores cancelled responses. No deployment secrets,
contributor key, local session URL or local login component enters its bundle.

Configuration / allowed hosts: There are no committed operator values. Set all
of `OPERATOR_ORIGIN` (canonical HTTPS origin, no trailing slash), `ACCESS_ISSUER`
(canonical HTTPS `<team>.cloudflareaccess.com` origin), `ACCESS_AUDIENCE`
(operator application's AUD), and `OPERATOR_SUBJECTS` (JSON array of exact
verified Access `sub` strings; 1-100 entries). Only `OPERATOR_ORIGIN` is accepted;
workers.dev cannot be configured as that origin. Do not substitute an email
header or a contributor ID. The Access policy and Worker allowlist must agree.
Account/domain/identity selection, real values and CLI authentication remain
G-OPERATIONS/OPS-02 decisions, not synthetic defaults. Follow the
[live setup checklist](operator-setup.md#ops-02--configure-cloudflare-environments-and-operator-access).

Build / commands: Install both web and community-service dependencies with their
existing frozen lockfiles. `pnpm --dir web build:operator` typechecks only the
operator feature and emits its isolated build. Worker test/typecheck/build/dev
scripts build those assets automatically; `pnpm --dir community-service build`
remains dry-run only. `web/playwright.operator.config.ts` runs a separate
frontend-only preview; normal local-app browser configuration excludes its spec.

Verification commands and results:
- `pnpm --dir community-service test`: 557 tests / 79 files passed, including
  RSA-signed hostile JWTs, deployment config checks, real Worker dispatch with a
  mocked Access certificate endpoint and real built static asset binding.
- `pnpm --dir community-service typecheck`: passed after adding the raw JSONC
  fixture declaration beside the existing raw SQL declaration.
- `pnpm --dir community-service build`: dry-run passed, 813.64 KiB Worker /
  142.77 KiB gzip; only local D1/rate-limit/asset bindings.
- `pnpm --dir web exec vitest run --maxWorkers=1`: 267 tests / 39 files passed.
- `pnpm --dir web build`: passed; unchanged existing 537.26 kB chunk warning.
- `pnpm --dir web build:operator`: passed, 196.69 kB JS / 61.64 kB gzip,
  1.72 kB CSS and 3.00 kB favicon, independent of the local application bundle.
- `pnpm --dir web exec playwright test --config playwright.operator.config.ts`:
  10 Chromium journeys passed at 320/1440px, including strict CSP image rendering,
  keyboard refresh, long-subject wrapping, 401/403/503, offline/login HTML,
  no local API requests, zero axe violations and no document overflow. Screenshots
  `web/test-results/css55-verified-{320,1440}.png` were inspected.
- `pnpm --dir community-service preflight:deploy`: expected exit 1, names-only
  missing configuration; no deployment authorization. Scoped editor diagnostics
  reported no errors. Node documentation check passed for 8 changed documents,
  198 local links, balanced fences and the CSS-55 checklist; `git diff --check`
  passed.

Limitations / recovery: JWTs, identities and network answers in tests are synthetic.
No real Access application, operator allowlist, DNS, hosted key rotation/revocation,
provider account configuration or CLI authentication was exercised. JWKS verification is not an
extra online session-revocation check; live session lifecycle remains OPS-02
verification. Browser tests exercise the isolated frontend, not production Access.
No persistent data/schema/migration, secret or backup format changed. Preserve
deployment auth configuration independently with the future hosted backup inventory;
there is no new database or private key to export. No production APP_ENV override,
local database initialization, media/model read or existing server restart occurred.
Missing config/keys/assets fails closed; restore approved config/build artifacts,
never add a contributor or development-auth bypass.

Remaining work: None in authorized local CSS-55 scope. OPS-02 must obtain approved
Access/domain/allowlist configuration and verify member/denied/expired sessions,
all asset paths, alternate hosts and mutation origins live before activation.
CSS-25 remains IN PROGRESS for downstream operational/mutation integration.

Next task: CSS-56. Read its brief, CSS-39/52/54 handoffs and G-OPERATIONS;
ask for report fields/reasons and moderation/reversal policy before encoding them.
Then add shared audited services behind `operatorService`, carrying the verified
subject into stale-state and audit checks. Keep Report abuse disabled until its
separate approved report API exists. CSS-57 reuses this isolated frontend/runtime;
CSS-59 must settle its own administrative CLI auth rather than waive CSRF.

Direct dependent tasks: [CSS-56](56-moderation-service.md), [CSS-57](57-operator-dashboard-read.md), [CSS-59](59-operator-cli.md).

