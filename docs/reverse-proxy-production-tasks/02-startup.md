# RP-02 — Resolve startup configuration and listening port

Dependencies: RP-01. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

scanner/cli.py currently passes an argparse default directly to Uvicorn. Configuration
must now be resolved before socket binding. Saving deployment settings must not change
the running origin/path/cookie policy until restart. Multiple auth modules currently
import the global settings object; replacing one variable is not sufficient.

## Read and expected touch points

Read scanner/cli.py, scanner/config.py, scanner/api.py startup/shutdown, auth_api.py,
auth_policy.py, auth_origin.py, db.py, and all imports of settings. Tests include
test_cli.py, test_startup_gate.py, test_auth_config.py. Use RP-01's resolver.

## Implementation steps

1. Distinguish absent --port from an explicitly supplied value. Resolve explicit CLI
   port, saved port, and default 8000 before starting Uvicorn; retain --host behavior.
2. Construct one immutable active deployment snapshot per HTTP service lifetime,
   including Base URL, origin, prefix, listening port, and CLI override metadata.
   Keep offline administrator CLI operations usable without HTTP-only configuration.
3. Wire active public origin into source checks, cookie-security validation, and
   HTTP startup consistently. Remove the old-origin environment input without fallback.
   Never use request Host/Forwarded to invent a public origin.
4. Ensure API app construction/startup receives the snapshot before route assembly
   and auth behavior use it. Preserve original-peer information and proxy_headers=False.
5. Compute next-start values independently for later settings responses. Persisted
   updates must not mutate active origin/path during the current process lifetime.
6. Define the approved production versus API-only launch selection. Retain an explicit
   development/testing path when production assets are not installed.
7. Preserve data/key directories and first-run administrator gating. Queue recovery
   still starts only when setup is complete; no extra scheduler may be initialized.
8. Leave actual restart scheduling to RP-08, but expose the agreed launch context,
   including explicit CLI args needed across a restart.
9. Follow the approved parent-controller/one-HTTP-child architecture. Remove obsolete
   origin validation early enough that an old environment value cannot block startup.

## Acceptance criteria

- [x] No --port uses saved port, falling back to 8000; explicit --port wins and is visible.
- [x] Saving new settings leaves the active snapshot unchanged until a new launch.
- [x] Next launch uses new saved values and preserves an explicit CLI recovery override.
- [x] Absent Base URL starts permanent localhost use; setup/login need no URL/TLS/cookie flags.
- [x] Source checks use configured or deterministic local origin; local HTTP works
      while public HTTPS remains secure. Local URL follows Port without being saved.
- [x] Offline admin commands do not suddenly require a public URL or frontend assets.
- [x] Uvicorn proxy rewriting stays disabled and original peer data remains available.
- [x] First-run setup gate and worker reconciliation remain intact.
- [x] Startup/bind configuration errors identify the remedy without overwriting settings.

## Verification

Extend test_cli.py for omitted/explicit port and launch context; test active-versus-next
snapshots and selected-origin validation. Run test_startup_gate.py and relevant
auth configuration tests. Do not require the old exact uvicorn.run call shape if
the approved implementation changes it; assert the actual policy/port behavior.

## Definition of done

Shared DoD applies. Startup has a single resolved active snapshot and tested port
selection. RP-03 can mount from it and RP-08 can retain its launch context. CLI
recovery and offline administrator operations continue to work.

## Handoff

Status: DONE
Last completed substep: Resolved the immutable deployment snapshot before HTTP bind,
  installed it on the app generation, and replaced the removed legacy-origin consumers
  in CORS, source checks, and session-cookie policy.
Files changed: scanner/cli.py; scanner/api.py; scanner/auth_policy.py;
  scanner/auth_api.py; tests/test_cli.py; tests/test_startup_gate.py;
  tests/test_auth_policy.py; tests/test_editarr_integration.py;
  docs/reverse-proxy-production-tasks/02-startup.md;
  docs/reverse-proxy-production-tasks/README.md (RP-02 checklist only).
Contract/API changes: `cli.resolve_server_launch_context()` returns immutable
  `ServerLaunchContext(host, cli_port_override, api_only, snapshot, original_argv)`.
  It preserves absent `--port` as `None`, resolves through `DeploymentStore` before
  importing HTTP code, and does not run for offline `admin`/`auth` commands.
  `api.configure_http_service(app, context)` freezes the supplied snapshot before
  middleware construction; `app.state.deployment_snapshot` is the sole active
  public-origin/prefix/cookie-security source for this generation. Saved-file changes
  do not mutate it. Direct ASGI import has a separately resolved API-only/manual
  snapshot and must be reported by RP-08 as restart-unsupported. Production assets
  remain RP-05's responsibility. Session cookies now use the active snapshot's secure
  flag and prefix; transition cutoff/legacy-path deletion remains unactivated.
Verification commands and results: `uv run ruff check scanner/cli.py scanner/api.py
  scanner/auth_api.py scanner/auth_policy.py tests/test_cli.py tests/test_startup_gate.py
  tests/test_auth_policy.py` -> passed; `uv run pytest -q tests/test_cli.py
  tests/test_startup_gate.py tests/test_auth_policy.py tests/test_auth_api.py` ->
  53 passed (5 existing FastAPI/Starlette deprecation warnings); `uv run pytest -q
  tests/test_deployment_config.py tests/test_auth_config.py tests/test_editarr_integration.py
  tests/test_queue_lifecycle.py tests/test_dedicated_worker.py` -> 118 passed
  (5 existing warnings); `git diff --check` -> passed.
Acceptance criteria still unmet: None.
Approvals received or outstanding: All RP-00 startup, localhost, controller, and
  transition decisions were already approved; no new approval is needed.
Known failures/limitations: RP-02 supplies launch context only; RP-08 must replace
  the direct Uvicorn service path with the approved one-child parent controller,
  retain `original_argv`/`cli_port_override`, and expose unsupported direct-launch
  capability. RP-03 owns prefix route mounting, redirects, and full cookie migration;
  it must retain the active snapshot rather than re-resolve deployment state per
  request. RP-06 saves pending scope metadata and RP-08 alone activates the cutoff.
Next exact action: RP-03 should mount API/auth routes from
  `app.state.deployment_snapshot.prefix`, preserve `public_origin` source checks,
  and extend cookie clear behavior using `legacy_cookie_paths`; RP-08 should consume
  `ServerLaunchContext` for parent-controlled child replacement and bind recovery.
