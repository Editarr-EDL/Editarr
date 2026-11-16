# RP-11 — Align development and API-only entry points

Dependencies: RP-02, RP-04, RP-05. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

scripts/dev.sh currently hardcodes origin/HTTP-cookie settings and launches Uvicorn
reload with proxy rewriting enabled by default. The production CLI disables it.
Development must use the same deployment resolution and URL rules without requiring
a production asset bundle for API-only work.

## Read and expected touch points

Read scripts/dev.sh, scanner/cli.py, config.py, web/vite.config.ts,
web/playwright.config.ts, and RP-00/02/04/05 launch/bootstrap contracts.

## Implementation steps

1. Align all documented Uvicorn entry points with proxy_headers=False or the
   equivalent CLI flag. Preserve reload behavior where the approved mode supports it.
2. Use the new Base URL seed/bootstrap rather than a second hardcoded public-origin
   system. Do not let the dev script silently override an explicit saved Base URL.
3. Make Vite's browser-facing prefix and API proxy preserve the same path as the
   backend. Handle root and a configured prefix with the runtime contract.
4. Make API-only operation work without web assets. Built-in loopback HTTP cookies
   need no manual flag; retain secure cookies for configured public HTTPS.
5. Show accurate URLs/ports in startup output. If saved settings conflict with a
   requested dev topology, explain the actual configuration/remedy instead of lying.
6. Report restart capability accurately for reload/manual Uvicorn launches. Do not
   claim UI restart support where there is no approved relaunch mechanism.
7. Maintain existing frontend component/E2E workflows; production tests use their
   own configuration in RP-13. Avoid broad CORS as a substitute for same-origin proxying.
8. Ensure scripts/testing defaults do not edit the user's deployment file as a side effect.

## Acceptance criteria

- [x] CLI, API-only, and dev entry points preserve original peer information.
- [x] Root and prefixed dev UI/API requests use matching prefixes and valid source checks.
- [x] API-only startup does not require production static assets.
- [x] Saved Base URL precedence is respected and startup output reflects effective settings.
- [x] Localhost HTTP works without manual cookie flags; configured public HTTPS
      retains secure cookies. Vite's separate browser origin is handled explicitly.
- [x] Unsupported restart contexts are visible and never return fake restart success.
- [x] Existing test/dev commands continue to run with isolated fixtures.

## Verification

Add focused launch/script checks as appropriate and smoke-test dev/API-only with
temporary data and ports at root/prefix. Test backend peer resolution through the dev
proxy. Run relevant CLI tests and existing browser smoke tests; do not need a full
production TLS matrix in this task.

## Definition of done

Shared DoD applies. Documented entry points agree on configuration/trust behavior,
and RP-14 receives exact current dev/API-only commands and restart limitations.

## Handoff

Status: DONE

Audit September 4, 2026: Corrected reload-worker propagation of `--web-host`, so
`127.0.0.1` and `localhost` retain their exact browser origins for source checks and
matching saved prefixes. Preserved the launcher's default/saved/explicit CLI port
provenance separately from Uvicorn's fixed listener port. The user approved using
the Vite address for development next-start comparisons; the protected settings API
now resolves pending values through the same development topology. No-op settings
correctly report no restart needed, sparse port saves do not persist an implicit
Base URL, and incompatible saved targets remain actionable errors. Active snapshots
and routes remain fixed until a new app starts.

Audit files changed: `scanner/development.py`; `scanner/deployment_api.py`;
`tests/test_development.py`; this handoff; the parent plan and RP-00 decision record.

Audit verification: `uv run pytest -q tests/test_development.py
tests/test_deployment_settings_api.py tests/test_cli.py tests/test_backend_routing.py
tests/test_auth_network.py` passed 113 tests, and the final added test
`tests/test_development.py::test_reload_factory_rejects_a_newly_incompatible_saved_url_without_writing`
passed separately (1 passed). Twelve isolated
subprocess cases exercise the actual app factory and protected deployment endpoints
across both browser hosts, root/nested prefix, and default/saved/CLI API ports. They
verify exact Origin rejection/acceptance, real forms session/CSRF authorization,
saved-over-environment precedence, no-op restart flags, sparse port saves, immutable
active settings/routes, and rejection of incompatible pending URLs. Focused Ruff and
`git diff --check` passed. No live service, user database, or deployment file was changed.

Last completed substep: Ran the existing component, production-build, and Playwright
workflows after real root and nested-prefix Vite-to-API smoke tests, then verified the
focused backend launch, routing, trust, and restart suites.

Files changed: `scanner/development.py`; `scanner/restart_api.py`; `scripts/dev.sh`;
`scripts/run_development.py`; `web/vite.config.ts`; `web/e2e/app.spec.ts`;
`tests/test_development.py`; `tests/test_restart_service.py`; this task; task-pack
README checklist. No deployment file, database, production assets, or user process was
changed.

Contract/API changes: `./scripts/dev.sh api` now executes the ordinary
parent-controlled `editarr --api-only` path, resolves saved/environment/default
deployment state, requires no frontend bundle, and accepts `--api-host`/`--api-port`
temporary overrides. `./scripts/dev.sh dev` resolves the same saved API port and Base
URL precedence, runs API-only Uvicorn reload with `--no-proxy-headers`, and runs Vite
at the exact printed browser URL. With no configured Base URL, its browser URL exists
only in the immutable development snapshot; it is neither written nor exported as
`EDITARR_BASE_URL`. A saved/environment Base URL must exactly match Vite's loopback
origin and inferred prefix or launch fails with an actionable API-only/isolated-data
remedy. `--check` validates and prints the topology without launching.

Vite serve mode validates `EDITARR_VITE_BASE_PATH`, injects the RP-04 bootstrap, and
proxies exactly `/api` or `<prefix>/api` without rewriting. `changeOrigin: false`
preserves Host, while `xfwd: false` prevents development-only forwarding headers from
replacing the direct API peer; Uvicorn also disables its proxy middleware. This lets
Editarr's resolver treat Vite's direct loopback connection according to the normal
trusted-peer policy. Vite production builds retain `base: "./"` and do not receive the
development bootstrap transformation. Reload mode reports unsupported restart reason
`development_reload`; raw manual Uvicorn retains `manual_uvicorn`. POST in either
unsupported context returns 409 and never schedules a restart.

Verification commands and results:

- `uv run ruff check scanner/development.py scanner/restart_api.py
  scripts/run_development.py tests/test_development.py tests/test_restart_service.py`
  passed.
- Escalated loopback run of `uv run pytest -q tests/test_development.py
  tests/test_cli.py tests/test_restart_service.py tests/test_backend_routing.py
  tests/test_auth_network.py` passed 104 tests with five existing FastAPI/Starlette
  deprecation warnings. Before loopback permission was granted, the same suite passed
  99 tests and its five real-process restart cases failed only at sandbox `socket.bind`.
- In `web/`, `pnpm test` passed 114 tests and `pnpm build` passed with Vite 6.4.3.
  The built entry retained relative hashed CSS/JS asset references. `pnpm test:e2e`
  passed all 30 browser tests. The stale path-mapping selector was made exact because
  the completed RP-09 `Restart Editarr` button also contains the word “Edit.”
- Disposable loopback smokes ran `scripts/run_development.py dev` with dynamic API and
  Vite ports, isolated data/database paths, and no cookie-security flag. Both `/` and
  `/media/editarr` returned an index with the matching bootstrap, proxied the matching
  API path unchanged, allowed a protected request under verified local-only auth, and
  returned restart reason `development_reload`. Prefix ports were 58709/58710 and root
  ports were 58745/58746; each owned process group was terminated and awaited.
- `bash -n scripts/dev.sh` passed, obsolete `EDITARR_AUTH_PUBLIC_ORIGIN` is absent from
  the launch files, focused TypeScript checking passed, and `git diff --check` passed.

Acceptance criteria still unmet: None.

Approvals received or outstanding: RP-00 already approved the API-only, Vite/reload,
peer-preservation, permanent localhost, and restart-capability contract. No migration,
database wipe, new product decision, or approval was needed.

Known failures/limitations: `web` alone remains for mocked frontend work and prints
that `dev` is required for a live authenticated API. A saved public HTTPS Base URL
cannot be represented by local HTTP Vite, so `dev` refuses it and points to API-only
or an isolated development data directory. In-app restart is intentionally unavailable
under reload/manual Uvicorn; only standalone `api` has the real parent controller.

Next exact action: RP-13 can use `./scripts/dev.sh dev --check`, the recorded root and
prefix smoke topology, and the production proxy fixtures after RP-12. RP-14 should
document `api`, `web`, and `dev` commands plus the saved-URL conflict and restart limits.
