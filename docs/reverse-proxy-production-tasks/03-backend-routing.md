# RP-03 — Mount API, authentication, cookies, and redirects

Dependencies: RP-02. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

Every application-owned URL must respect the Base URL prefix. The current global
auth middleware compares literal /api/auth paths; blindly mounting FastAPI or adding
root_path can break bootstrap authentication or expose incorrect routes. Uvicorn
scheme rewriting is intentionally disabled, so framework slash redirects also need care.

## Read and expected touch points

Read scanner/api.py, auth_policy.py, auth_api.py, auth_network.py, auth_origin.py,
models.py, arr.py, and jellyfin.py. Inspect all generated image/link URLs, not only
the known /api/library-items artwork field. Review test_auth_api.py,
test_auth_policy.py, test_auth_network.py, test_editarr_integration.py.

## Implementation steps

1. Mount the API at the resolved active prefix using RP-00's assembly contract.
   Establish a canonical app-relative path for auth classification and use supported
   ASGI/framework routing semantics. Do not double-prepend or double-strip paths.
2. Preserve bootstrap status/setup/login rules, protected APIs/docs, existing source
   and CSRF checks, and external/local bypass semantics. Update the route inventory
   to inspect mounted child routes; do not simply exempt the entire mount.
3. Reject or leave inaccessible out-of-prefix API aliases when a non-root prefix is
   active. A public static router added later must not turn them into HTML responses.
4. Scope cookie set/delete consistently to the approved prefix, including RP-00's
   cookie transition. Apply the approved local-versus-HTTPS Secure policy and preserve
   HttpOnly/SameSite, source checks, and CSRF protection.
   Test duplicate root/prefix cookie behavior and that logout removes the active cookie.
5. Replace application-generated URLs with a shared prefix-aware helper. Do not
   rewrite external Jellyfin/Radarr/Sonarr addresses or filesystem media paths.
6. Make docs, schema URLs, and OpenAPI server metadata use the prefix while keeping
   their existing authentication requirement.
7. Implement origin-relative canonical redirects for the entry point and API slash
   behavior. Preserve query strings/methods and prevent //host, internal HTTP URLs,
   wrong prefixes, or trusted-client policy changes.
8. Do not add public asset exemptions yet beyond the RP-00 contract; RP-05 owns serving.

## Localhost requirement update

Test permanent loopback HTTP with no Base URL: setup, login, CSRF-protected writes,
and logout need no manual cookie flag. Derive origin from localhost and selected
port, not arbitrary Host headers. Retain wrong-origin rejection and HTTPS security.

## Acceptance criteria

- [x] Root, /editarr, and /media/editarr work through prefix-preserving requests.
- [x] Setup/login/status still work at their mounted URLs before setup or login.
- [x] Protected APIs, artwork, previews, and docs retain their auth/error behavior.
- [x] Route inventory covers mounted endpoints; unknown/out-of-prefix routes do not bypass policy.
- [x] Set/delete cookies use matching paths and approved legacy transitions work.
- [x] Only an activated configured-origin/prefix transition rejects older forms
      sessions; Save and unapplied failed changes do not. Cutoff persists across
      restart/reversion, without deleting session records or other data.
- [x] Listening-port-only changes retain sessions, including implicit localhost;
      an explicit Base URL public-port change requires reauthentication.
- [x] Every generated application link contains exactly one active prefix.
- [x] Redirect Location is origin-relative and query/method-preserving behind HTTPS-over-HTTP.
- [x] Spoofed Host/proto/prefix/client headers do not change public origin or trust rules.

## Verification

Parameterize actual ASGI requests at root and prefixes with an HTTP upstream scope
and HTTPS public Base URL. Exercise cookie-authenticated GET/mutations, bootstrap,
wrong Origin, missing CSRF, docs, slash redirects, encoded path edge cases, and
trusted/untrusted forwarding chains. Run existing auth/network/integration regressions
affected by route assembly. Use isolated databases, not the development service.

## Definition of done

Shared DoD applies. Routing, URL construction, authentication, and cookies agree on
the active mount and are covered by meaningful tests. RP-05 can add static serving
without inventing path interpretation; RP-06/RP-08 know how to register protected routes.

## Handoff

Status: DONE
Audit September 4, 2026: Fixed direct `scanner.api:app` imports to set FastAPI
  `root_path` from their initial snapshot, as standalone configuration already did.
  A fresh subprocess regression uses isolated data and verifies prefixed status,
  protected docs, and rejected root aliases without invoking CLI configuration.
  Final focused backend audit passed 83 tests; focused Ruff passed. CLI/controller
  runtime claims remain with RP-08 and RP-13.
Last completed substep: Configured FastAPI's immutable service-generation root path,
  enforced the preserved-prefix boundary, and aligned policy classification, cookies,
  generated links, OpenAPI, and redirects with that active snapshot.
Files changed: scanner/application_paths.py; scanner/api.py; scanner/auth_api.py;
  scanner/auth_policy.py; tests/test_backend_routing.py;
  docs/reverse-proxy-production-tasks/03-backend-routing.md;
  docs/reverse-proxy-production-tasks/README.md (RP-03 checklist only).
Contract/API changes: `application_url(snapshot, path)` creates one prefix-aware,
  origin-relative application link and `application_relative_path(request, snapshot)`
  classifies routes beneath the active mount. `PrefixRoutingMiddleware` rejects root
  aliases for non-root deployments, canonicalizes the prefix entry path with a 307
  origin-relative redirect, and rewrites framework slash redirects to relative active
  paths. `configure_http_service()` sets FastAPI's root_path from the frozen
  `DeploymentSnapshot`, retaining root-path-aware route matching and OpenAPI server
  metadata while never accepting prefix-stripping requests. Authentication policy and
  auth status use the same application-relative path; older forms sessions are rejected
  only when the supplied active snapshot has an activated `session_not_before` cutoff.
  Login/logout expire `/`, every active `legacy_cookie_paths` entry, and the active
  cookie path; login then writes the active-path cookie. Duplicate same-name request
  cookies fail closed. No code activates or persists a cutoff: RP-06 only records a
  pending previous path and RP-08 activates transitions after a successful child start.
Verification commands and results: `uv run ruff check scanner/application_paths.py
  scanner/api.py scanner/auth_api.py scanner/auth_policy.py tests/test_backend_routing.py`
  -> passed; `uv run pytest -q tests/test_backend_routing.py tests/test_auth_api.py
  tests/test_auth_policy.py tests/test_cli.py tests/test_startup_gate.py
  tests/test_editarr_integration.py` -> 110 passed (5 existing FastAPI/Starlette
  deprecation warnings); `uv run pytest -q` -> 371 passed (the same 5 warnings);
  `git diff --check` -> passed.
Acceptance criteria still unmet: None.
Approvals received or outstanding: No new approval was needed. RP-00's local-default,
  prefix-preserving proxy, cookie-transition, and parent-controller decisions were used
  as recorded. No migration, database wipe, deployment, or restart occurred.
Known failures/limitations: Production frontend/static serving remains RP-05; the
  deployment-settings and restart endpoints remain RP-06/RP-08. FastAPI's supported
  `root_path` route assembly is protected by a strict outer boundary rather than adding
  a second public root mount. Unrelated worktree changes were not touched.
Next exact action: RP-05 can add only the approved public frontend shell/static routes
  beneath the already-enforced active prefix. RP-06 must register its deployment routes
  as protected app-relative API routes and save, but not activate,
  `pending_previous_path`. RP-08 must activate the persisted cutoff/legacy paths only
  after a new child is serving, then preserve this middleware's active snapshot on any
  recovery generation.
