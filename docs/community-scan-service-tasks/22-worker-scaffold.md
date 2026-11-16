# CSS-22 — Scaffold the separately deployed TypeScript Worker

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Current storage: [D1-only storage](d1-storage-scope.md), updated October 3, 2026.
> Dated completion evidence below may describe the earlier storage implementation.

Status: **DONE** (September 26, 2026). Local-only Worker scaffold and test harness
are operational; production deployment remains disabled by design.

- Prerequisites: [CSS-00](00-repository-preflight.md)
- Decision gates: None beyond the shared rules.
- Parent sections: main §4; implementation §5.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The confirmed hosted footprint is one Worker, one D1 database, with no paid additions or separate always-on server.

## Starting points

new community-service/ package, src/, test/, migrations/, deployment config and lockfile.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Create focused modules, type checking, Worker-runtime tests and a local D1 binding; pin a tested runtime compatibility date.
2. Add health/capability/error envelopes and environment validation with redacted logging.
3. Document local commands, disabled-by-default deployment configuration and production/test separation; introduce no real resources.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Clean checkout can run typecheck/unit/runtime smoke with mocks or local bindings.
- [x] Only D1 is bound for persistence; unprotected alternate admin routes are not configured.
- [x] Missing production policy/credentials causes clear startup/deployment validation, not permissive defaults.

## Verification

- Establish and record actual package test/typecheck/build scripts; test missing-config failures.
- Verify no deployment command, real token, provider ID or paid plan is committed.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide Worker commands, module boundaries, binding interfaces and version pins for all service tasks.

## Handoff summary for the next task

Wrangler now binds only `CATALOG_DB` for storage; `Env` and route configuration
require no bucket. The historical dry-run inventory confirms the former D1 provider bindings; it does not define current Docker admission.
Continue using the existing local-only runtime and production gates.
See [current scope, schema and verification](d1-storage-scope.md).

## Historical completion evidence

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Added isolated pnpm TypeScript
Worker package with strict type generation/checking, workerd Vitest integration,
local D1/R2 binding smoke, redacted HTTP errors, local-only health, and
fail-closed non-local admission. Dry-run bundle works. No real Cloudflare
resources or credentials were used.

Files changed and relevant revision: New `community-service/` package
(`package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `tsconfig.json`,
`vitest.config.ts`, `wrangler.jsonc`, `src/`, `test/`, `migrations/README.md`,
`scripts/check-deploy.mjs`, `.gitignore`, `README.md`), this brief and task
index. No version bump.

Contracts / APIs / schemas / migrations / fixtures introduced: Only local
`GET /health` and fail-closed placeholder `GET /v1/capabilities`; error envelope
`{"error":{"code":"service_unavailable"}}`. Bindings `CATALOG_DB: D1Database`,
`ARTIFACTS: R2Bucket`, `APP_ENV: local`; optional future policy, Discord and
Access fields have no committed values. No D1 schema or production artifact
contract. CSS-23 owns migrations; CSS-10 may now add cross-language benchmark
tests in this Worker harness.

Decisions used (gate IDs and recorded answers): Confirmed D11 Cloudflare
Worker/D1/private R2 and D17 Access-protected operator surface; no outstanding
gate answered by this scaffold. No admin route, deployment script, public R2
access, account ID, or production policy is present.

Verification commands and results: From `community-service/`,
`pnpm install --frozen-lockfile`, `pnpm run typecheck`, `pnpm run test`,
`pnpm run build`, `pnpm peers check` all passed; `pnpm run preflight:deploy`
exited 1 as intended, listing missing variable names only. `git diff --check`
passed. Runtime tests passed 3/3, covering a local D1 query, R2 put/get,
HTTP and missing production policy. `pnpm run dev` started on 127.0.0.1:8787;
`curl -i` confirmed `/health` returns 200 and `/v1/capabilities` returns
redacted 503. See package README for command semantics.

Known failures, limitations, unavailable checks, and recovery notes: Current
Worker is a disabled scaffold, not a production API. `workers_dev` and preview
URLs are disabled; local placeholder D1 ID cannot be a hosted binding. No real
deployment, hosted CPU, provider account configuration, Discord login, or migration tested.
Do not promote `APP_ENV=local` as a production policy or activate service
routes until the dependent tasks implement and approve them.

Remaining work in this task: None. Production activation belongs to later
scoped tasks with explicit policy and credential validation.

Next task ID and exact first action: CSS-10; add a TypeScript/Worker encoding
benchmark to the local workerd harness, compare its bytes and bounded CPU to
the existing Python experiment, then present G-ENCODING for user selection.

Direct dependent tasks: [CSS-10](10-encoding-benchmark.md), [CSS-11](11-artifact-contract.md), [CSS-12](12-request-signing-profile.md), [CSS-23](23-d1-identity-schema.md), [CSS-26](26-discord-oauth.md), [CSS-55](55-operator-access.md).

