# RP-05 — Serve the production frontend

Dependencies: RP-03, RP-04. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

The backend currently serves no frontend. A plain static mount behind global auth
would prevent anonymous users from loading setup/login. Production serving must
expose the shell and packaged assets while keeping data and documentation protected.

## Read and expected touch points

Read scanner/api.py app assembly, scanner/auth_policy.py, RP-03's mount helpers,
RP-04's bootstrap/template contract, web/index.html, and existing auth/startup tests.
Add a focused static-serving module and tests if appropriate. Asset packaging is RP-10.

## Implementation steps

1. Resolve the approved packaged frontend directory independently of the working
   directory. Implement RP-00's explicit source-development/API-only modes.
2. Serve index HTML and the agreed runtime bootstrap with values from the active
   snapshot, never pending settings. Serialize safely; avoid executable string
   concatenation and do not expose secrets or an unrestricted settings object.
3. Make the initial HTML references load the built JS/CSS/icons under the prefix.
   Ensure relative assets resolve correctly by canonicalizing the mount entry URL.
4. Publicly serve only intended shell/asset/bootstrap routes using narrow classification.
   Missing assets must be real errors, and API/docs routes must never fall into an
   SPA HTML catch-all. Hash navigation needs no arbitrary server-side route fallback.
5. Restrict files to the packaged asset root. Handle percent-encoded traversal,
   separators, symlinks, directories, and unsupported methods without exposing files.
6. Use cache policies appropriate to content: hashed assets may be immutable;
   entry HTML/bootstrap must not keep old configuration after restart/upgrade.
   Authenticated responses retain their existing cache constraints.
7. Fail production startup clearly if required assets are missing/incompatible;
   API-only mode must still support backend development/tests explicitly.
8. Extend the route inventory to cover public static handlers intentionally while
   preserving fail-closed API and docs classification.

## Acceptance criteria

- [x] One listener serves setup/login UI anonymously and data only under existing auth.
- [x] Root and nested-prefix HTML load actual built scripts/styles/icons correctly.
- [x] /api errors, docs errors, and missing static files are never replaced with index HTML.
- [x] Out-of-prefix routes do not expose an extra application copy.
- [x] No configuration secret, database, source file, or out-of-root file is downloadable.
- [x] Bootstrap reflects active settings until restart, then the new settings.
- [x] Missing-assets error is actionable; explicit API-only launch works.
- [x] Correct MIME/cache behavior is tested and hash navigation survives page reload.

## Verification

Use temporary asset fixtures for security/error cases and RP-04's real build for a
smoke test. Exercise startup, anonymous/authenticated paths, cache headers, GET/HEAD,
missing files, traversal/symlinks, mount-entry redirect, and unknown APIs. Run relevant
test_auth_policy.py and test_startup_gate.py regressions.

## Definition of done

Shared DoD applies. Production app serves the actual build at root/prefix and static
access does not broaden data access. The handoff specifies asset location/layout,
bootstrap serving, and package expectations for RP-10.

## Handoff

Status: DONE
Audit September 4, 2026: Fixed HTML bootstrap insertion to use a callable regex
  replacement, preserving JSON backslash escapes literally rather than interpreting
  them as replacement syntax. Regression fixtures cover escaped Unicode and HTML
  delimiters as defense in depth even though current public path validation is ASCII.
  Final focused backend audit passed 83 tests; focused Ruff and the real frontend
  production build passed.
Last completed substep: Added a prefix-bound production frontend middleware that
  serves only the entry shell and explicit packaged assets before authentication,
  with the existing prefix middleware still outermost.
Files changed: scanner/production_frontend.py; scanner/api.py; scanner/cli.py;
  scanner/auth_policy.py; tests/test_production_frontend.py;
  tests/test_backend_routing.py; tests/test_cli.py;
  docs/reverse-proxy-production-tasks/05-production-serving.md;
  docs/reverse-proxy-production-tasks/README.md (RP-05 checklist only).
Contract/API changes: RP-10's asset location is frozen as the installed package
  directory `scanner/frontend`, resolved from `scanner/production_frontend.py`,
  never from cwd or `web/dist`. `FrontendAssets.from_directory()` validates that
  layout and reports actionable missing/incompatible-artifact errors; ordinary
  `editarr` requires it, while `editarr --api-only` sets no frontend assets. The
  public static surface is exactly canonical entry `/prefix/`, `assets/...`, and
  the enumerated built icon files; static routes are listed explicitly in
  `auth_policy.PUBLIC_STATIC_ROUTE_PATHS`. The entry replaces the RP-04 development
  fixture with a safely serialized, attribute-free embedded
  `#editarr-bootstrap[type="application/json"]` using the immutable active
  snapshot. It is not a `runtime-bootstrap.json` endpoint: the conflicting task
  wording was superseded by the frozen RP-00/RP-04 embedded-bootstrap contract.
  Hashed assets are immutable; entry HTML is `no-store` and other icons are
  `no-cache`.
Verification commands and results: `uv run ruff check
  scanner/production_frontend.py scanner/api.py scanner/cli.py scanner/auth_policy.py
  tests/test_production_frontend.py tests/test_backend_routing.py tests/test_cli.py`
  -> passed; `uv run pytest -q tests/test_production_frontend.py
  tests/test_backend_routing.py tests/test_auth_policy.py tests/test_startup_gate.py
  tests/test_cli.py tests/test_auth_api.py` -> 64 passed (5 existing FastAPI/
  Starlette deprecation warnings); `cd web && pnpm build` -> passed; the resulting
  `web/dist` was loaded by `FrontendAssets` and rendered with a `/editarr` active
  bootstrap -> passed; `git diff --check` -> passed.
Acceptance criteria still unmet: None. Full production artifact installation and
  real browser/TLS proxy verification remain RP-10/RP-13 scope.
Approvals received or outstanding: No new approval. The parent confirmed that the
  completed RP-00/RP-04 embedded bootstrap wins over the conflicting
  `runtime-bootstrap.json` wording. No migration, data wipe, deployment, or restart
  occurred.
Known failures/limitations: `scanner/frontend` is intentionally not populated or
  packaged in this task; RP-10 owns copying the generated Vite output there and
  including it in the release artifact. A normal source checkout therefore gives the
  documented missing-assets error unless launched with `--api-only`; RP-11 owns
  source/Vite development topology and its matching explicit development fixture.
Next exact action: RP-10 must build `web/` using the lockfile, copy the resulting
  `web/dist` contents to `scanner/frontend` in the release artifact, and verify the
  installed package from another cwd without Node/Vite. RP-11 must retain
  `--api-only` for backend/source work and align Vite's explicit development
  bootstrap/proxy topology without relying on this production asset lookup.
