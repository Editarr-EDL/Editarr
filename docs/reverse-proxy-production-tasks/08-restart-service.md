# RP-08 — Implement actual service restart

Dependencies: RP-02, RP-03, RP-06. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

Restart Editarr must restart the actual standalone service, re-read the deployment
file, and rebind a changed Port. The current CLI just calls uvicorn.run; returning
a fake success, reloading React, or exiting without a relaunch does not satisfy this.
The parent-controller restart mechanism is approved; use RP-00's finalized interfaces.

## Read and expected touch points

Read scanner/cli.py, api.py startup/shutdown/worker launch/recovery, worker.py,
repository.py worker reservation/PID-token handling, and tests/test_cli.py,
test_queue_lifecycle.py, test_dedicated_worker.py, test_startup_gate.py.
Use the RP-00 restart contract and RP-02 launch context.

## Implementation steps

1. Implement the approved standalone parent controller with one HTTP-service child
   at a time. Acknowledge before graceful child shutdown, then start a new generation.
   Keep its scope to HTTP service restart and preserve worker processes. Do not
   introduce an unapproved supervisor, worker model, or platform packaging mechanism.
2. Add the frozen protected restart endpoint. Validate policy/source/CSRF before
   scheduling; acknowledge only a restart the launcher supports. Deduplicate
   concurrent requests and return the agreed capability/error state otherwise.
3. Complete the acknowledgment before shutdown, then stop scheduling, drain/close
   server resources according to the approved mechanism, and start a new service
   generation with freshly loaded configuration.
4. Preserve explicit CLI args, especially --port override, data directory, environment,
   and chosen startup mode. Avoid caching saved values into relaunch args such that
   future UI saves are masked unintentionally.
5. Use the resolved next-start effective URL, including implicit localhost,
   for reconnect metadata. Unsaved UI
   text and client-provided redirect URLs must not control it.
6. Preserve scanner workers and PID/token reconciliation. No second scheduler or
   duplicate scan should appear while old/new HTTP lifetimes transition.
7. Report unsupported manual Uvicorn/development contexts honestly, and expose
   startup/bind failures through the agreed recovery/diagnostic path. Attempt the
   last working listener/snapshot if the new bind fails; retain saved changes as
   pending and distinguish recovery from successful application of new settings.
   Coordinate the persistent forms-session cutoff with actual URL activation so
   Save or an unapplied failed restart does not invalidate active sessions.
8. Verify restart as a subprocess integration test under temporary data and ports.
   Tests may use a lightweight controlled worker, but not mock the entire restart.

## Localhost requirement update

Test real restart without configured Base URL, including saved/CLI Port changes.
Resolve the new effective localhost URL without saving it as an override. An implicit
local origin-port update is not an explicit Base URL edit under port-only session rules.

## Acceptance criteria

- [x] A standalone launch acknowledges restart, runs a new service generation, and serves again.
- [x] New Base URL/Port are active after restart; explicit --port retains its documented effect.
- [x] Unauthorized/CSRF-invalid requests schedule nothing and duplicate requests restart once.
- [x] Acknowledgment reaches the caller before its connection is shut down.
- [x] Restart with no pending settings is still a genuine service restart.
- [x] Active dedicated workers survive and queued work is not duplicated.
- [x] Setup-required startup still prevents queue activation.
- [x] Unsupported launch modes and bind failures are visible, not reported as success.
- [x] Bind failure recovery preserves pending settings; failure of recovery is also
      reported. Recovery cannot be mistaken for successful application merely because
      the server generation advanced.
- [x] No real user instance, worker, database, or configuration is touched by tests.

## Verification

Unit-test controller states and endpoint policy; run a real isolated process through
at least same-port restart and saved-port change. Occupy the target port for the
failure case. Use process/server-generation evidence appropriate to the approved
mechanism (an exec replacement may retain its PID). Run queue/startup/CLI regressions.
Ensure test cleanup only stops processes it created, including surviving test workers.

## Definition of done

Shared DoD applies. Real standalone restart and worker continuity have evidence.
The handoff gives RP-09 exact responses/capabilities/timeouts/reconnect semantics and
RP-13 a reusable isolated process fixture. A mocked restart hook alone is insufficient.

## Handoff

Status: DONE
Audit September 4, 2026: Reviewed the controller, protected endpoint, queue lifecycle,
  PID/token adoption, acknowledgment boundary, launch arguments, and D8 activation
  against RP-00 and the RP-02/03/06 handoffs. Fixed a failed-start edge: queue recovery
  now completes before the persistent URL/session cutoff is activated. Previously a
  queue recovery exception could leave an unapplied URL's cutoff durable while the
  controller recovered the prior listener. The existing startup exception handler
  still shuts down the scheduler if queue recovery or cutoff persistence fails;
  independent workers remain available for adoption by the recovery generation.
  Added a temporary-file regression proving failed queue recovery preserves the
  last active scope/cutoff, response-send ordering and cancellation tests, and a
  controller-unavailable assertion. No public contract or architecture changed.
  `uv run pytest -q tests/test_restart_service.py tests/test_queue_lifecycle.py
  tests/test_dedicated_worker.py tests/test_startup_gate.py tests/test_cli.py
  tests/test_deployment_config.py tests/test_deployment_settings_api.py` passed
  103 tests with five existing deprecation warnings. This includes actual isolated
  CLI same-port/saved-port/CLI-override restarts, occupied-listener recovery, prefix
  activation and forms reauthentication, and surviving-worker adoption. Focused
  Ruff and `git diff --check` passed. Tests used temporary data or in-memory
  databases, dynamic ports, and only test-owned processes; no live user instance,
  worker, data, or unrelated file was changed. No remaining RP-08 blocker found.
Last completed substep: Ran the focused restart, deployment, CLI, authentication-policy,
  startup-gate, queue-lifecycle, and dedicated-worker regressions after real process tests.
Files changed: scanner/restart_service.py; scanner/restart_api.py; scanner/cli.py;
  scanner/api.py; scanner/auth_policy.py; scanner/deployment_config.py;
  scanner/deployment_api.py; tests/test_cli.py; tests/test_restart_service.py;
  docs/api-contract.md; this task; task-pack README checklist.
Contract/API changes: Standalone `editarr` now owns one spawned HTTP child and private
  control pipe. `GET /api/system/restart` returns the frozen RestartCapability;
  `POST /api/system/restart` returns 202 with `status`, `target_generation`, and the
  resolved `reconnect_url`. Duplicate/unsupported/lost-controller responses are
  409 `restart_in_progress`, 409 `restart_unsupported`, and 503
  `restart_unavailable`. The response-end ASGI hook commits shutdown only after the
  complete 202 is sent. Startup is gated at 503 until bind, lifespan, and D8 activation
  finish. Internal `cookie_scope` gained optional `activated_base_url` and
  `activated_local_default` fields so cold/offline/environment transitions compare to
  the durable last working scope; initial activation establishes a baseline without a
  cutoff. Implicit-local port changes retain sessions. Failed pending activation leaves
  cutoff and last-active scope unchanged.
Verification commands and results: `uv run pytest -q tests/test_restart_service.py`
  passed 10 tests, including real isolated same-port, saved-port, CLI-mask, occupied-bind
  recovery, prefix/cutoff, and worker-adoption runs. `uv run ruff check` on all touched
  Python files passed. `uv run pytest -q tests/test_restart_service.py
  tests/test_deployment_config.py tests/test_deployment_settings_api.py tests/test_cli.py
  tests/test_auth_policy.py tests/test_startup_gate.py tests/test_queue_lifecycle.py
  tests/test_dedicated_worker.py` passed 123 tests with five existing deprecation
  warnings. `git diff --check` passed.
Acceptance criteria still unmet: None.
Approvals received or outstanding: Parent controller, recovery, permanent localhost,
  and D8 activation behavior were approved in RP-00. No approval remains.
Known failures/limitations: Manual Uvicorn reports restart unsupported. On an activated
  Base URL scope change, protected GET returns 401 to the old forms session; RP-09 must
  navigate to sign-in instead of treating 401 or an old-generation response as success.
  Recovery failure exits the controller with the actionable local diagnostic recorded
  in the contract. Corrupt deployment JSON still requires offline correction.
Next exact action: RP-09 should consume the documented 202/409/503 responses, poll only
  same-origin reconnects until `generation >= target_generation` with outcome `applied`,
  show `recovered` as failure with saved settings pending, and navigate/sign in on a
  cross-origin transition or 401. RP-13 can reuse `standalone_process` from
  tests/test_restart_service.py; it uses dynamic ports, absolute temp paths, actual CLI
  processes, and exact-PID cleanup.
