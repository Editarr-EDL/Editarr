# RP-00 — Close approval gates and record integration contracts

Dependencies: none. Deliverable: an approved, concrete contract and baseline, not
application code. Read [shared background](README.md) and the parent plan first.

## Background and goal

The user approved the deployment behavior but explicitly requires approval for major
decisions. Full Base URL replaced an earlier path-only proposal. The old public-origin
environment variable still exists, and the current CLI has no restart controller.
Do not let separate subagents choose conflicting fallback or process models.

## Scope and steps

1. Inspect git status and applicable AGENTS.md. Record existing user changes. Read
   scanner/config.py, cli.py, api.py, auth_api.py, auth_policy.py, auth_origin.py,
   worker.py, scripts/dev.sh, web/src/api.ts, web/index.html, and build configuration.
2. Incorporate the confirmed product/architecture decisions into the final contract:
   - remove EDITARR_AUTH_PUBLIC_ORIGIN without compatibility support; use the optional
     EDITARR_BASE_URL full-URL seed, saved values taking precedence;
   - implement the now-confirmed permanent localhost mode when no Base URL is
     configured: resolve http://localhost:<listening port>/ from the known local
     default and port, not request headers. Revise HTTP-cookie/source validation so
     normal local setup/login work without configuration. Specify loopback aliases
     and explicit non-loopback binds without silently broadening local HTTP policy;
   - standalone parent controller with one HTTP-service child, acknowledged restart,
     worker preservation, and last-working-listener recovery after bind failure;
   - persistent forms-session cutoff when a changed Base URL origin/prefix activates,
     old/root cookie cleanup, and session retention for listening-port-only changes.
     No database/session wipe is authorized or needed for this policy.
3. Do not ask again about these approvals or the permanent-localhost requirement.
   Finalize routine integration details within the approved contract. Ask only if
   further work introduces a new major product or architecture decision.
4. Record exact names/types and lifecycle rules in the Contract record below. Proposed
   route names are /api/settings/deployment and /api/system/restart; freeze methods,
   response/error shapes, auth classification, and duplicate-restart behavior.
5. Define the bootstrap transport early enough for the first script/CSS request to
   resolve under the prefix. A later React fetch alone cannot fix initial asset URLs.
6. Define the distinction between absent Base URL override and an explicit save,
   including what blank input means and how an unrelated save preserves fallback.
   Specify whether explicitly saving the currently displayed seed records an override.
7. Identify how the new origin source reaches all auth consumers without validating
   an obsolete lower-priority environment value as the active origin. Keep other
   authentication environment precedence unchanged.
8. Record startup modes, configuration filename/schema, active/next-start DTO,
   port override persistence across restart, minimum public bootstrap, and restart
   capability reporting for development/manual Uvicorn.
9. Run a baseline: targeted backend auth/CLI/queue tests, frontend tests, and a web
   build. Capture existing failures and commands; no performance/model inference run.

## Acceptance criteria

- [x] Each unresolved issue has an actual user-approved choice or the task is BLOCKED.
- [x] Contract covers first-run startup as well as already configured installations.
- [x] No-Base-URL startup and permanent local use require no URL/TLS/cookie flags;
      implicit local URLs follow port changes without becoming saved overrides.
- [x] Base URL, public origin, prefix, upstream port, and CLI override are unambiguous.
- [x] Both frontend and backend owners can implement bootstrap without inventing a
      second source of truth or waiting on unspecified endpoint shapes.
- [x] Restart design works for standalone launch and preserves dedicated workers.
- [x] Route protection, cookie transition, and reconnect behavior are explicitly recorded.
- [x] Baseline and unrelated worktree changes are captured without modifying real data.

## Verification

Use targeted existing suites: tests/test_auth_config.py, test_auth_policy.py,
test_auth_network.py, test_cli.py, test_startup_gate.py, test_queue_lifecycle.py,
and test_dedicated_worker.py. In web/, run pnpm test and pnpm build once for baseline.
Read the existing test harness before running it; keep configuration/data isolated.

## Definition of done

Shared DoD applies. The Contract record is filled with approved choices and exact
integration interfaces; baseline results are recorded. No implementation task may
substitute an unapproved recommendation for a completed contract.

## Contract record — to complete during this task

- User approval evidence/date: The authoritative record is the uncommitted 2026-09-03
  update to the parent plan, D2/D7/D8, and this task's Scope step 2. Its exact recorded
  wording is: “Absence of a saved Base URL and environment seed is valid”; “remove
  `EDITARR_AUTH_PUBLIC_ORIGIN` without a compatibility release, deprecated alias, or
  fallback”; “Confirmed restart architecture: standalone `editarr` runs a parent
  controller with one HTTP-service child at a time”; and “changing the configured Base
  URL's canonical origin or prefix requires forms reauthentication.” D1–D7 and D8 are
  therefore approved; no approval gate remains.
- Old origin variable and missing Base URL behavior: **Confirmed:**
  `EDITARR_BASE_URL` is the optional full-URL environment seed. Remove
  `EDITARR_AUTH_PUBLIC_ORIGIN` without alias, compatibility release, legacy seed,
  or deprecated validation path. Precedence is saved `base_url`, then
  `EDITARR_BASE_URL`, then the implicit local default. A malformed
  selected value is a startup error; malformed lower-priority values are ignored and
  must not make a valid selected value fail. **Confirmed correction:** with no saved
  Base URL or environment seed, start normally at
  `http://localhost:<effective listening port>/`, using prefix `/` and the existing
  default loopback bind. This is permanent normal operation, not temporary setup.
  Derive the local origin from that explicit default and resolved port, never request
  headers. Invalid explicit configuration remains an error, not an implicit fallback.
  Update application code, development scripts, tests, and documentation together.
  The obsolete environment setting must have no effect on resolution or startup.
- Base URL input/blank/canonicalization rules: A saved value is an absolute HTTP(S) URL
  whose path segments contain only ASCII letters, digits, `-`, `.`, `_`, and `~`.
  During the September 4, 2026 audit the user approved “ASCII segments are fine for
  now.” Reject percent escapes, reserved/non-ASCII path characters, and `.`/`..`
  segments; preserve root/nested paths and trailing-slash normalization. This closes
  accepted encoded-path cases that differed from ASGI's decoded routing paths.
  The URL is
  without userinfo, query, fragment, controls, backslashes, traversal, or encoded path
  separators. Normalize host/scheme/default port using the existing strict origin
  parser; normalize the prefix to `/` or a leading-slash path with no trailing slash;
  serialize the root URL without a trailing slash. Field absence means no change,
  `base_url: null` clears only the saved override and returns to seed mode, and an empty
  string is invalid. The UI may map a deliberate blank field submission to `null`, but
  a port-only/unrelated save must omit `base_url`. Submitting the displayed nonblank
  seed explicitly records it as an override, including an explicit root URL.
- Configuration path/schema and field-level saved-state representation:
  `${data_dir}/deployment.json`, private alongside existing service data and atomically
  replaced in that directory. Versioned JSON is `{ "version": 1, "base_url"?: string,
  "port"?: integer, "cookie_scope"?: { "not_before"?: RFC3339 timestamp,
  "legacy_paths": string[], "pending_previous_path"?: string } }`. Omitted
  `base_url`/`port` fields are unset overrides, so an explicit root is a present
  `base_url`, not a special prefix value. `cookie_scope` is internal, non-secret,
  non-user-editable metadata: on a pending scope change, retain the active prefix in
  `pending_previous_path`; only a successfully activated child advances the monotonic
  `not_before`, appends that path to de-duplicated `legacy_paths`, and clears pending
  state. The file contains no effective environment/local fallbacks, credentials,
  trusted-proxy settings, or separately editable origin/prefix.
- Resolution precedence and validation order: Resolve only the selected source in this
  order: saved Base URL → `EDITARR_BASE_URL` → implicit localhost URL; resolve
  listening port as explicit CLI `--port` → saved `port` → `8000`. Validate the chosen
  Base URL before deriving `public_origin` and `prefix`; validate the chosen port before
  bind. The built-in local default is available only at the normal `127.0.0.1` bind;
  its one browser origin is exactly `http://localhost:<port>`. It sets the *effective*
  session cookie Secure attribute to `false` automatically while retaining HttpOnly,
  SameSite, exact Origin/Referer checks, and CSRF. `127.0.0.1`, `[::1]`, `localhost.`
  and arbitrary loopback names are not equivalent browser-origin aliases: operators
  use the displayed `localhost` URL. A non-default/non-loopback bind with no explicit
  Base URL fails before bind rather than exposing an implicit insecure LAN service.
  Configured public HTTPS keeps Secure cookies. An explicitly configured HTTP Base URL
  still requires the existing explicit `EDITARR_AUTH_COOKIE_SECURE=false` escape hatch;
  a conflicting explicit `EDITARR_AUTH_COOKIE_SECURE=true` is an actionable local-mode
  startup error. Existing authentication environment precedence otherwise remains
  unchanged. Code must not instantiate or validate the obsolete origin environment
  setting after resolving Base URL.
- Active/next-start snapshot interface: Exact immutable internal DTO:
  `DeploymentSnapshot(base_url: str, public_origin: PublicOrigin, prefix: str,
  listening_port: int, base_url_source: Literal["saved", "environment",
  "local_default"], port_source: Literal["cli", "saved", "default"],
  cli_port_override: int | None, cookie_secure: bool, session_not_before:
  datetime | None, legacy_cookie_paths: tuple[str, ...])`. One active snapshot is created before HTTP app
  assembly and remains unchanged for that service generation. Independently resolve a
  next-start snapshot after every deployment-file read; a saved update affects only
  that next snapshot. A CLI port override remains in the controller launch context and
  masks a saved next-start port without changing the saved file. In local-default
  mode, base_url is a derived effective URL, not a configured/saved requirement;
  recompute it from the selected port on each launch. Settings must keep the optional
  Base URL input distinct from this read-only effective local address.
- Bootstrap transport/schema and initial asset loading: The exact public bootstrap is
  the production HTML response, after canonicalizing `/prefix` to
  `/prefix/`, contains an inert, safely JSON-serialized
  `<script id="editarr-bootstrap" type="application/json">` before the module script:
  `{ "version": 1, "basePath": "/editarr", "apiBasePath": "/editarr/api" }`.
  The built entry HTML uses relative (`./assets/...`) script, stylesheet, and icon URLs,
  so the initial requests resolve beneath the active prefix before React runs. It
  contains no secrets, origin header policy, stored overrides, or upstream port. RP-04
  consumes this before its first authentication request; production must fail clearly
  rather than silently assume `/` if it is missing/malformed. Development/test uses an
  explicit matching fixture.
- Settings methods, DTOs, errors, and policy classification: Exact API:
  `GET /api/settings/deployment` and `PUT /api/settings/deployment`. The PUT body is
  `DeploymentUpdate { base_url?: string | null, port?: integer | null }`; omission
  leaves a field unchanged and `null` clears that field's saved override. The response
  is `DeploymentConfiguration { active: DeploymentSnapshotDto, next_start:
  DeploymentSnapshotDto, saved: { base_url: string | null, port: integer | null },
  restart_required: boolean, restart: RestartCapability }`, where each snapshot DTO is
  `{ base_url: string, public_origin: string, prefix: string, listening_port: integer,
  base_url_source: "saved" | "environment" | "local_default", port_source:
  "cli" | "saved" | "default", cli_port_override: integer | null }`. Validation uses
  `422` with a safe field error; unreadable,
  corrupt, or atomic-write failures use `503` and
  `{ "code": "deployment_configuration_unavailable", "detail": "…" }` without a
  filesystem path or file content. Both routes are explicitly protected in the policy
  inventory after setup and use the existing forms/external/verified-local rules;
  cookie-authenticated PUT retains existing same-origin and CSRF checks.
- Restart mechanism, methods, DTOs, capabilities, duplicate handling: Standalone
  `editarr` is a small parent controller
  that owns one child HTTP-service generation and preserves original argv/environment.
  The child acknowledges the request first, then asks the controller over private IPC
  to perform graceful shutdown; the controller waits for shutdown and starts a child
  with a freshly resolved snapshot. If that child cannot bind, it relaunches the prior
  snapshot on its old listener, keeps saved changes pending, and exposes the failed
  attempt through restart status plus recovery instructions. Recovery is not success
  applying the requested settings; if the old listener is also unavailable, report
  that failure explicitly rather than promise an impossible rollback.
  The controller must
  not own scan workers: API shutdown stops only scheduling and the child startup keeps
  the existing PID/token adoption behavior. The protected routes are
  `GET /api/system/restart` → `RestartCapability { supported: boolean, mode:
  "standalone_controller" | "unsupported", generation: integer, in_progress: boolean,
  outcome: "idle" | "restarting" | "applied" | "recovered" | "failed",
  target_generation?: integer, reason?: "manual_uvicorn" | "development_reload" |
  "controller_unavailable", detail?: string }` and
  `POST /api/system/restart` (empty body) → `202 { "status": "accepted",
  "target_generation": integer, "reconnect_url": string }`. A concurrent POST returns
  `409 { "code": "restart_in_progress", "detail": "…" }`; unsupported launch modes
  return `409 { "code": "restart_unsupported", "detail": "…" }`; an unavailable
  controller returns `503 { "code": "restart_unavailable", "detail": "…" }`. The
  endpoint must schedule no restart until its `202` response has completed. A
  same-origin UI may poll the protected GET and accept readiness only
  once `generation >= target_generation`; cross-origin changes navigate to the supplied
  URL instead of credentialed polling. Distinguish a successfully applied restart
  from recovery on the old snapshot; generation alone must not hide a failed change.
- Cookie transition and reconnect rules: **Confirmed:** changing the configured
  Base URL's canonical origin or prefix requires forms users to sign in again;
  a listening-port-only change does not. Do not wipe the database or delete session
  records. Compare canonical *effective* origin/prefix values: an explicit URL that
  equals the active local effective scope needs no transition, while an explicit public
  port change does. A pending save records cleanup information but cannot invalidate a
  session. On successful activation only, persist the non-secret cutoff and reject
  sessions issued before it; login/logout clear `/`, every recorded legacy path, and
  the active path before the new login sets the active-prefix cookie. This prevents
  ambiguous same-name `/` and `/prefix` cookies.
  Keep the cutoff persistent and monotonic across restart or later configuration
  reversion so previously invalidated sessions cannot become valid again. Cookies
  on a different host cannot be cleared by the new host and expire normally. The
  reconnect target is the resolved next-start effective URL plus
  `#/settings/general`, never a client value, unsaved form value, or upstream port.
  Changing the proxy route before following that target remains an operator action.
  That URL can be an unsaved localhost default. A local port-only change
  changes that derived origin but must not be mistaken for an explicit Base URL
  scope change under the approved port-only session-retention rule. Changing a public
  port in an explicit Base URL does change its origin and requires reauthentication.
  Coordinate cutoff activation with the controller so failed, unapplied URL changes
  do not invalidate current sessions. Existing auth bypass policies remain unchanged.
- Production/API-only/development launch contract: Production standalone launch is
  `editarr` under the approved parent controller and requires packaged assets,
  but does not require a configured Base URL. Absent configuration selects the
  permanent localhost mode described above. `editarr --api-only` uses the same resolver/listener but does
  not require packaged assets; offline `editarr admin`/`auth` commands do not require a
  public URL. Direct `uvicorn scanner.api:app`, Vite development, and reload mode are
  explicitly unsupported for in-app restart and must report that capability rather
  than fake success. Every server launch keeps `proxy_headers=False`. Local HTTP
  browser authentication must work with no manual cookie environment setting; the
  earlier rule requiring that flag for all HTTP launches is superseded for the
  built-in loopback mode. Vite's separate browser origin still needs an explicit
  development resolver/bootstrap contract and must not override saved settings. A Vite
  launcher either supplies a matching explicit development bootstrap/origin context
  without writing deployment state, or refuses a conflicting saved public
  configuration with an actionable API-only remedy.
  September 4, 2026 clarification approved by the user: “Use the Vite address for
  next-start comparisons while in development mode.” General Settings therefore uses
  the same development resolver for pending values, preserving saved/seed precedence
  and the original explicit CLI port policy. An unchanged Vite topology has
  `restart_required: false`; its implicit browser URL is never persisted. Incompatible
  configured targets retain the actionable API-only/isolated-data error.
- Baseline results and existing changes: At task start `git status --short` contained
  only one unrelated untracked scanner file, which was not modified.
  `uv run pytest -q tests/test_auth_config.py tests/test_auth_policy.py
  tests/test_auth_network.py tests/test_cli.py tests/test_startup_gate.py
  tests/test_queue_lifecycle.py tests/test_dedicated_worker.py` passed: 138 tests,
  5 existing FastAPI/Starlette deprecation warnings. In `web/`, `pnpm test` passed:
  6 files/82 tests; `pnpm build` passed (TypeScript and Vite v6.4.3).

## Handoff

Status: DONE
Last completed substep: Located the recorded approvals, reconciled their permanent
  localhost correction and D8 transition policy, and froze the configuration, bootstrap,
  settings, restart, cookie, and launch interfaces.
Files changed: docs/reverse-proxy-production-tasks/00-approval-contract.md;
  docs/reverse-proxy-production-tasks/README.md (RP-00 checklist only).
Contract/API changes: Documentation-only frozen interfaces above; no application code
  is implemented. The legacy-origin removal, permanent local default, parent controller,
  and forms-session transition are all approved and have no remaining approval gate.
Verification commands and results: `uv run pytest -q tests/test_auth_config.py
  tests/test_auth_policy.py tests/test_auth_network.py tests/test_cli.py
  tests/test_startup_gate.py tests/test_queue_lifecycle.py
  tests/test_dedicated_worker.py` → 138 passed (5 deprecation warnings); `cd web &&
  pnpm test` → 82 passed; `cd web && pnpm build` → passed; `git diff --check` → passed.
Acceptance criteria still unmet: None. All eight criteria are checked below and the
  Contract record contains the required approved behavior and integration interfaces.
Approvals received or outstanding: D1–D8 are recorded in the parent plan; the four
  former RP-00 gates are explicitly recorded above. No approval is outstanding.
Known failures/limitations: No application code, runtime configuration, service,
  database, or scan worker was changed. The baseline generated only ignored web build
  output; unrelated worktree changes remain untouched. The
  recorded approval session's plan/dependent-task documentation edits are preserved.
Next exact action: Begin RP-01; it owns the approved deployment resolver/store and
  focused persistence tests. RP-04 may follow its frozen bootstrap interface.
