# Reverse proxy and production serving coding tasks

Parent: [Approved implementation plan](../reverse-proxy-production-plan.md).

Status: **RP-00 through RP-14 complete.**

This pack is written for a Terra-level coding subagent receiving one bounded task
without the conversation history. Read this file, the parent plan, and the assigned
task before changing code. The approved behavior below is authoritative; do not
infer approval for an unresolved architecture decision from the existence of a task.

## Assignment and execution

Give the subagent this prompt, replacing the task identifier and filename:

> Implement RP-XX from docs/reverse-proxy-production-tasks/FILE.md. Read that task,
> this pack's README, the parent plan, applicable AGENTS.md, and prerequisite handoffs.
> Implement only the assigned scope. Preserve unrelated changes. Complete the
> acceptance criteria and definition of done, record verification and exact remaining
> work in the task's handoff, and do not silently decide unresolved product or
> architecture questions. Do not start dependent tasks, deploy, restart a user's
> running instance, or wipe development data.

This is a task pack, not a request to launch agents or create sidebar tasks.
Dependencies represent completed, verified handoffs, not merely code in progress.
Each task owner updates its own file and checklist entry. Use NOT STARTED,
IN PROGRESS, BLOCKED, or DONE. Only check a task after its definition of done passes.

## Master checklist and dependencies

| Done | ID | Task | Prerequisites |
| --- | --- | --- | --- |
| [x] | RP-00 | [Close approval gates and record integration contracts](00-approval-contract.md) | None |
| [x] | RP-01 | [Persist and resolve deployment configuration](01-deployment-config.md) | RP-00 |
| [x] | RP-02 | [Resolve startup configuration and listening port](02-startup.md) | RP-01 |
| [x] | RP-03 | [Mount API, authentication, cookies, and redirects](03-backend-routing.md) | RP-02 |
| [x] | RP-04 | [Make frontend URLs runtime-configurable](04-frontend-base.md) | RP-00 |
| [x] | RP-05 | [Serve the production frontend](05-production-serving.md) | RP-03, RP-04 |
| [x] | RP-06 | [Expose protected deployment settings APIs](06-settings-api.md) | RP-03 |
| [x] | RP-07 | [Build deployment settings UI](07-settings-ui.md) | RP-04, RP-06 |
| [x] | RP-08 | [Implement actual service restart](08-restart-service.md) | RP-02, RP-03, RP-06 |
| [x] | RP-09 | [Add Restart Editarr button and reconnect flow](09-restart-ui.md) | RP-07, RP-08 |
| [x] | RP-10 | [Package frontend with the production service](10-release-build.md) | RP-05 |
| [x] | RP-11 | [Align development and API-only entry points](11-development.md) | RP-02, RP-04, RP-05 |
| [x] | RP-12 | [Add executable Nginx/Caddy proxy fixtures](12-proxy-fixtures.md) | RP-10 |
| [x] | RP-13 | [Verify production, settings changes, and restarts end to end](13-production-verification.md) | RP-09, RP-10, RP-11, RP-12 |
| [x] | RP-14 | [Finish operator docs and release acceptance](14-documentation-release.md) | RP-13 |

Default to sequential execution. If explicitly assigned concurrent agents, RP-04
can run alongside RP-01/02/03 once RP-00 freezes shared interfaces. Do not concurrently
edit scanner/api.py, scanner/cli.py, scanner/config.py, web/src/api.ts,
web/src/App.tsx, web/vite.config.ts, or pyproject.toml. Ownership is per assigned task;
coordinate shared-file handoffs rather than overwriting another agent's work.

## Approved product behavior

1. The production service serves the UI and API on one configurable listening port.
   Public TLS terminates at an external proxy. With no configured Base URL, the
   ordinary application runs permanently at http://localhost:<listening port>/,
   prefix /, using the existing loopback bind default. No URL, TLS setup, or manual
   cookie-security flag is required for local setup/login/normal use. This replaces
   RP-00's earlier proposal to reject startup when Base URL is absent.
2. General Settings edits **Base URL** and **Port**. Base path is derived, not edited
   independently. Examples: https://example.com and https://example.com/ both yield
   /; https://example.com/editarr/ yields /editarr; nested paths are supported.
3. Effective Base URL supplies both public origin (scheme/host/public port) and
   the prefix. With an explicit Base URL, upstream listening port is independent
   of public URL port. Without one, derive the local effective URL/origin from the
   explicit localhost default and selected port, never request headers. Do not save
   that implicit local URL as an override; it must follow future local port changes.
4. An explicitly saved Base URL wins over its environment seed, which only applies
   until the field has been explicitly saved. Saving a root URL must override an
   environment URL with a prefix. Unrelated saves must not create a Base URL override.
   Use EDITARR_BASE_URL as the optional seed. Remove EDITARR_AUTH_PUBLIC_ORIGIN
   without a compatibility period, alias, fallback, or validation path. This initial
   release has no backward-compatibility requirement for the old variable.
5. Store deployment overrides in a separate file in the data directory. No database
   schema changes are expected. Write atomically and distinguish absent from explicit.
6. Saved port wins during normal startup; default is 8000. Explicit --port overrides
   it temporarily and must be disclosed in the UI, including after UI restart.
7. Save persists pending settings; it does not change active routes, cookies, origin
   checks, or listener. Restart applies one consistent new configuration.
8. **Restart Editarr** is at the top right of General Settings, works even without
   pending changes, does not implicitly save dirty forms, and restarts the actual
   service. It acknowledges before shutdown and provides reconnect/failure feedback.
   The approved standalone architecture is a parent controller with one HTTP child
   at a time. On new-listener bind failure, attempt the last working listener/snapshot,
   leave saved changes pending, and report the failure. Manual Uvicorn/Vite/reload
   modes without that controller report in-app restart as unsupported.
9. Active dedicated scan workers survive API restarts. Scheduling stops/reconciles
   without duplicate scans. Preserve the current setup gate on queue activation.
10. The proxy preserves the prefix: public /editarr/api/scans reaches Editarr at
    /editarr/api/scans. Do not add prefix-stripping mode or header-inferred mounts.
11. Keep Uvicorn proxy rewriting disabled and retain Editarr's trusted-address
    resolver. Use prefix-aware origin-relative redirects and the configured public
    origin for necessary absolute URLs. Do not trust Host as the public origin.
12. Only intended frontend shell/assets/bootstrap are public. APIs/docs/artwork/media
    remain under existing auth rules. Preserve forms, external, and local bypass
    semantics, source checks, CSRF, session handling, and address throttling.
13. Package one frontend artifact usable at any configured prefix; no rebuild per URL.
    Include native/package install instructions plus verified Nginx/Caddy examples.
14. Docker/Compose deployment and native Windows/macOS/Linux executable builds are
    deferred. Record the executable goal without choosing a bundler or designing it.
15. A configured Base URL origin/prefix change requires forms reauthentication using
    a persistent monotonic cutoff activated with the change, never just on Save.
    Clear obsolete/root cookie paths as applicable; do not wipe data or delete session
    records. Listening-port-only changes retain sessions, including implicit localhost
    port changes. An explicit Base URL public-port change is an origin change.

RP-00's original four approval gates are resolved: remove the old environment setting,
support permanent localhost without Base URL, use the parent controller, and enforce
the approved forms-session transition. Finalize remaining integration details; do not
ask the user to approve those decisions again.

## Current code map and pitfalls

- scanner/config.py constructs environment settings; scanner/auth_origin.py parses
  origin-only strings. Do not relax Origin-header grammar to accept Base URL paths.
- scanner/cli.py calls uvicorn.run and has an argparse default port. Saved configuration
  must be loaded before binding and before caching the active HTTP configuration.
- scanner/api.py owns app assembly, startup/shutdown, generated artwork URLs, and
  scheduler state. Several modules import the global settings object: inspect all
  consumers rather than updating only one startup call.
- scanner/auth_policy.py compares request paths literally and has an explicit route
  inventory. Mounted paths and public assets require intentional classification.
- scanner/auth_api.py sets/deletes cookies at /. Cookie migration needs a documented
  strategy, particularly duplicate same-name cookies at / and the new prefix.
- scanner/api.py launches scanner.worker in a dedicated process/session, and startup
  matches PID plus token to existing workers. tests/test_queue_lifecycle.py and
  tests/test_dedicated_worker.py encode this behavior.
- web/src/api.ts hardcodes /api, including bootstrap exemptions and CSRF classification.
  Asset URLs also occur in web/index.html, App.tsx, auth-login.tsx, and auth-setup.tsx.
  React navigation uses a URL hash and should remain hash-based.
- web/vite.config.ts has a development /api proxy; web/playwright.config.ts starts
  Vite and web/e2e/app.spec.ts mocks APIs. These are not production integration tests.
- pyproject.toml packages scanner only. web/dist is ignored build output.
- scripts/dev.sh uses Uvicorn reload and currently differs from CLI proxy policy.
- Re-read applicable source when starting: other tasks may have changed these details.

## Integration contract discipline

RP-00 records exact interfaces in its Contract record before dependent work starts:
configuration filename/schema, unset/save semantics, bootstrap transport and timing,
runtime snapshot access, planned settings/restart endpoints and payloads, lifecycle
capabilities, status/error codes, cookie transition behavior, and API-only selection.
Later tasks implement that contract; names in task examples are candidates, not
permission to diverge. Endpoint names and internal helpers are routine implementation
details; public behavior, persistence precedence, restart architecture, and startup
fallback behavior need the approval handling described in RP-00.

Distinguish at least these concepts, regardless of final field names:

- persisted overrides: only explicit saved fields;
- active configuration: immutable for this HTTP service lifetime;
- next-start configuration: resolved saved values plus environment and CLI policy;
- browser bootstrap: minimum public URL/path information, never deployment secrets;
- protected settings response: current and next values, source/override metadata,
  restart requirement, and derived display-only values;
- restart result: accepted/unsupported/error and a public reconnect target based on
  saved configuration, never arbitrary client input or unsaved UI text.

## Safety, scope, and verification rules

### Resumed completed-task audit — September 4, 2026

Re-read the approved contract and completed handoffs against the current worktree.
The prior RP-00–RP-07 fixes remain present. Fresh targeted configuration, auth,
routing, serving, deployment API, and CLI checks passed 130 backend tests; affected
deployment/runtime/API/auth/App frontend suites passed 92 tests. RP-09's existing
nine component tests, RP-11's 14 development tests, and the frontend production build
also passed. The recorded RP-10 wheels still match their published SHA-256 and
contain the nine required frontend files without runtime data. Rebuild the artifact
after audit fixes before proxy tests.

The deeper audit found gaps in failed-start cutoff activation, stalled restart UI
requests, and propagation of the selected development browser host. Sequential
task-specific subagents corrected those under RP-08, RP-09, and RP-11; their
handoffs record the final verification. RP-11 also corrected port-source metadata
and, with the user's explicit approval, compares next-start settings using the Vite
address in development mode without persisting that derived address. No remaining
completed-task audit blocker was found. Do not treat the original passing tests as
evidence that those gaps were covered. No developer database wipe or live service
restart is authorized or required.

### Completed-task audit — September 4, 2026

RP-00 through RP-07 were reviewed before proceeding to RP-08. Fixed accepted path
syntax that failed ASGI routing (with the user's ASCII-segment approval recorded in
RP-00), escaped bootstrap insertion, empty injected environment precedence, initial
direct-import prefix mounting, and the implicit-local Base URL draft. Affected
handoffs record the specific corrections. No remaining audit blocker was identified;
restart activation, release packaging, development topology, proxy fixtures, and
production browser verification remain assigned to RP-08 through RP-14.

Verification: initial targeted backend regression suites (deployment configuration,
authentication configuration/policy, routing, frontend serving, deployment API, CLI,
startup gate, queue lifecycle, and dedicated workers) passed 135 tests. After fixes,
`uv run pytest -q tests/test_deployment_config.py tests/test_production_frontend.py
tests/test_backend_routing.py tests/test_cli.py tests/test_deployment_settings_api.py`
passed 83 tests. Focused Ruff and `git diff --check` passed. In `web/`,
`pnpm exec vitest run src/deployment-settings.test.tsx src/runtime-urls.test.ts
src/api.test.ts src/auth-settings.test.tsx src/App.test.tsx src/auth-gate.test.tsx`
passed 90 tests, and `pnpm build` passed. Backend tests retain the five existing
FastAPI/Starlette deprecation warnings. No live service was restarted or database
wiped; new routing tests use an isolated subprocess/data directory.

Read repository AGENTS.md. Do not add migrations merely to preserve v1 development
data. If a schema change becomes necessary, explain it and obtain explicit approval
to skip migration and wipe data before any wipe. A prior authentication-task database
reset is not authorization for this work. All tests use isolated temporary data,
configuration, keys, model caches where needed, and ports. Never restart the user's
actual Editarr process during verification.

Preserve unrelated changes.
Do not commit secrets, generated private keys, sample passwords from real users,
database files, node_modules, or build output. Do not change existing auth precedence
globally to implement the distinct Base URL precedence.

Each coding task runs focused tests for its concrete risk and relevant existing
regressions. Do not run the full suite after every task or lower coverage thresholds.
RP-13/RP-14 own broad verification. Use actual CLI launch for process claims, actual
HTTP/TLS for proxy claims, and a production build for production-browser claims.
Mocks are appropriate for external media services/heavy inference, not for the
routing/auth/settings/restart behavior claimed as integrated.

For shell commands below, backend commands run at the repository root; pnpm commands
run in web/. Use uv run pytest/ruff and the established pnpm scripts. If dependencies,
browsers, TLS tools, or ports are unavailable, record the exact blocker; do not claim
a skipped check passed. Request sandbox escalation through the normal tool when needed.

## Shared definition of done

Every task additionally requires:

- All its acceptance criteria checked, with actual evidence and no unapproved
  deviations. Prerequisite contracts and handoffs remain consistent.
- Focused checks pass; unrelated failures are named, not hidden or silently changed.
- git diff --check is clean, including newly created files when needed.
- Relevant tests/docs and the task handoff are updated; no real user data changed.
- The next task receives exact APIs/files/commands and any remaining limitations.
- Only then mark DONE in the task and check its row here. A blocked task stays unchecked.

## Handoff format

Each task contains a handoff section. Replace the initial values on completion or
interruption:

    Status: NOT STARTED | IN PROGRESS | BLOCKED | DONE
    Last completed substep:
    Files changed:
    Contract/API changes:
    Verification commands and results:
    Acceptance criteria still unmet:
    Approvals received or outstanding:
    Known failures/limitations:
    Next exact action:
