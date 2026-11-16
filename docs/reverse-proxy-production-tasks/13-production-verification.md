# RP-13 — Verify production, settings changes, and restarts end to end

Dependencies: RP-09, RP-10, RP-11, RP-12. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

Current Playwright tests use Vite and intercepted APIs. They cannot prove bundled
frontend serving, real cookies/CSRF, prefix mounts, or process restart. Add a separate
production integration workflow using the installed artifact and a real TLS proxy.

## Read and expected touch points

Read web/playwright.config.ts, web/e2e/app.spec.ts, test fixtures from RP-08/12,
tests/test_editarr_integration.py, auth tests, and queue/worker tests. Add a dedicated
production Playwright config/command and fixture support; preserve the fast mocked suite.

## Implementation steps

1. Build/install the RP-10 artifact once and create isolated data/config/key directories,
   test ports, test TLS setup, and real service/proxy processes with bounded teardown.
   Refuse accidental use of a developer's running server or data path.
2. Run a real browser over HTTPS against root and /editarr using the same artifact.
   Include a nested-prefix smoke case and a second-proxy smoke using RP-12.
3. Exercise first-run setup, login, authenticated settings save, reload/hash deep links,
   logout, unauthenticated rejection, wrong Origin/missing CSRF, artwork, and preview
   requests. Mock external source services/heavy inference behind the service boundary;
   do not intercept Editarr's auth/settings/restart responses in the browser.
4. Assert no application request escapes the configured prefix, no mixed-content
   redirects appear, and actual cookie flags/path and static cache behavior are correct.
5. Change Base URL/Port through General Settings. Prove Save leaves the old listener/
   mount/origin active. Then click the actual top-right Restart Editarr button and
   observe the newly applied configuration, updating only the test proxy as required.
6. Cover same-origin prefix change, origin change requiring navigation/sign-in, and
   upstream Port change with unchanged public port. Confirm dirty form values are not
   used and an explicit CLI override remains visible/effective.
7. Cover restart without pending changes, repeated clicks, startup bind failure,
   unsupported mode, stale old-server readiness, timeout, and recovery link behavior.
   Backend integration can own expensive failure permutations; cross-reference evidence.
8. Start a lightweight real dedicated test worker with persisted PID/token and queued
   follow-on work. Restart the HTTP service and verify continuity/reconciliation without
   duplicate processing. Reuse genuine queue protocol; do not depend on ML downloads.
9. Record responsive/accessibility checks for General Settings and the restart button.
   Test build/static lookup from outside the checkout and absence of a Vite runtime.
10. Fix issues within approved scope or hand them to their task owners; rerun only the
    relevant checks until passing. Then run the existing backend/frontend regression
    suite once against the integrated result.

## Localhost requirement update

Run the installed service/browser over local HTTP with no proxy, saved Base URL,
URL seed, or cookie flag. Exercise setup/login, Settings writes, scan behavior,
logout/login, and restart across launches. Change only Port, restart/reconnect,
and verify no Base URL override is persisted. Retain the full TLS/proxy matrix.

## Acceptance criteria

- [x] Real HTTPS browser runs pass at root/prefix with the same installed artifact.
- [x] Auth, source checks, CSRF, cookies, assets, artwork, and preview requests are exercised.
- [x] Save-versus-Restart semantics and actual Port/Base URL changes are demonstrated.
- [x] Correct new-origin/prefix reconnect behavior occurs without leaking auth secrets.
- [x] Genuine restart is observed; an old-server response cannot falsely complete the flow.
- [x] Worker continuity and absence of duplicate scheduling are proven across restart.
- [x] Failure/unsupported modes are truthful and recoverable.
- [x] Obsolete EDITARR_AUTH_PUBLIC_ORIGIN has no effect on startup or origin resolution.
- [x] Active configured-origin/prefix changes require forms reauthentication via a
      persistent cutoff, without database/session-record wipes. Save, unapplied bind
      failures, and listening-port-only changes retain the prior session behavior.
- [x] Bind-failure recovery reports failed application and leaves saved settings
      pending; it cannot be mistaken for success by generation-only readiness checks.
- [x] No developer data/processes/system trust settings are changed; fixture cleanup succeeds.
- [x] Existing regression checks pass or a concrete unrelated failure is documented for review.

## Verification matrix and evidence

Record a row per scenario with proxy, Base URL, listener, setup state, expected result,
command/test identifier, and observed result. At minimum cover:
root TLS, /editarr TLS, nested prefix, saved root over environment prefix,
pending save, prefix restart, upstream-port restart, cross-origin reconnect,
explicit CLI override, duplicate restart, bind failure, active worker, and second proxy.

Use uv run ruff check scanner tests, uv run pytest -q, pnpm build, pnpm test, the
existing pnpm test:e2e, and the new production command as applicable. If test support
lives outside scanner/tests, lint/check those touched files too. Record current actual
counts, not counts copied from earlier tasks.

## Definition of done

Shared DoD applies. The matrix has passing evidence for every required behavior,
reproducible commands, and artifacts/logs without secrets. No skipped core production
scenario is described as supported/verified. RP-14 receives exact release evidence.

## Handoff

Status: DONE

Last completed substep: Built the fresh current-worktree RP-13 wheel offline, installed
it without dependencies in a new temporary environment, and passed the real Nginx
production Chromium run. The final run exercised real artwork and preview requests,
same-origin and second-origin restart/reconnect, and sign-in on the new origin.

Files changed: `scripts/proxy_fixture.py`; `scripts/production_browser_fixture.py`;
`web/e2e/run-production-e2e.mjs`; `web/playwright.production.config.ts`;
`web/e2e/production.spec.ts`; `web/playwright.config.ts`; `web/package.json`;
`web/src/restart-editarr.tsx`; `web/src/restart-editarr.test.tsx`; this task; task-pack
README. Existing unrelated changes were preserved.

Contract/API changes: `pnpm test:e2e:production` is an opt-in production browser
command. It refuses to run without an installed artifact and dependency site-packages,
launches only owned Nginx/Caddy TLS proxy/controller process groups, and supplies
Chromium one comma-separated leaf-SPKI exception for each generated fixture certificate
(not `ignoreHTTPSErrors` and not system trust). It seeds a disposable service-boundary
Jellyfin artwork source and completed audio finding; the browser still calls real
Editarr endpoints, while the external source and renderer boundary are fake. A second
owned TLS proxy is reconfigured only after the actual restart acknowledgement and waits
for proxied readiness before the browser follows the server-provided cross-origin link.
The normal mocked configuration explicitly ignores the production spec. A production
failure found that an observed same-origin restart never released the UI in-flight guard
when hash navigation did not remount; RP-09 now releases it only after `applied` target
generation confirmation, retaining suppression during active/uncertain restart states.

Verification matrix and evidence:

| Scenario | Proxy / Base URL / listener | Setup state | Command or test | Observed result |
| --- | --- | --- | --- | --- |
| Root TLS | Nginx / generated `https://localhost:<dynamic>` / saved dynamic upstream | first run | `EDITARR_PRODUCTION_PROXY=nginx pnpm test:e2e:production` | PASS: installed wheel, real TLS, setup/login/settings/logout; Secure HttpOnly SameSite=Lax `/` cookie; static shell/assets and source/CSRF rejection. |
| `/editarr` TLS and pending save | Nginx / root then saved `<origin>/editarr` / dynamic then `upstream + 1` | authenticated forms | same production spec | PASS: Save retained old root listener/mount; UI Restart reconfigured only test proxy, applied prefix and port, rejected old forms session, and required sign-in at `/editarr`. |
| Nested prefix | Nginx / `<origin>/media/editarr` then `/media/editarr/nested` / dynamic then `upstream + 1` | first run then authenticated | `EDITARR_PRODUCTION_PROXY=nginx EDITARR_PRODUCTION_PREFIX=/media/editarr pnpm test:e2e:production` | PASS: real HTTPS setup, prefix-preserving restart, scoped cookie/request paths, reauthentication, and logout. |
| Second proxy | Caddy / generated root origin / saved dynamic upstream | first run | `EDITARR_PRODUCTION_PROXY=caddy pnpm test:e2e:production` | PASS: same real browser workflow using the same RP-12 installed artifact. |
| Saved root over environment prefix | direct isolated process | setup independent | `tests/test_deployment_config.py::test_saved_base_url_overrides_changed_environment_and_public_port_is_not_listener` | PASS. |
| Same-port/repeated restart and stale generation | standalone controller | authenticated forms | `tests/test_restart_service.py::test_actual_cli_acknowledges_before_same_port_restart`, `test_supported_api_deduplicates_before_first_response_commit` | PASS: 202 follows response delivery; duplicate is rejected; readiness requires target generation/outcome. |
| Upstream-port restart | standalone controller | authenticated forms | `tests/test_restart_service.py::test_actual_cli_rebinds_saved_port_and_preserves_worker` plus browser spec | PASS: saved port rebinding retained public proxy port and browser reconnected after proxy-only update. |
| Explicit CLI port override | standalone controller | authenticated forms | `tests/test_restart_service.py::test_cli_port_override_masks_saved_port_across_restart` | PASS: saved port remains pending while explicit CLI port stays effective/visible. |
| Bind failure/recovery | standalone controller with occupied test port | authenticated forms | `tests/test_restart_service.py::test_occupied_target_recovers_old_listener_without_activating_pending`, `test_recovery_failure_has_actionable_manual_diagnostic` | PASS: prior listener recovers, settings remain pending, cutoff is not activated, and failure is explicit. |
| Active dedicated worker | standalone controller plus persisted PID/token test worker | authenticated forms | `tests/test_restart_service.py::test_actual_cli_rebinds_saved_port_and_preserves_worker` | PASS: worker survives/reconciles with no duplicate processing. |
| Unsupported/reload modes | direct app and development reload | n/a | `tests/test_restart_service.py::test_direct_app_reports_restart_unsupported`, `test_development_reload_reports_accurate_restart_limit` | PASS: protected 409s truthfully identify unsupported mode. |
| Forms cutoff / port-only retention | standalone controller | authenticated forms | `tests/test_restart_service.py::test_successful_prefix_activation_invalidates_old_forms_session`; `tests/test_restart_service.py::test_activation_records_baseline_then_advances_cutoff_only_for_explicit_scope`; `tests/test_deployment_config.py::test_local_default_is_unsaved_and_follows_saved_port` | PASS: cutoff is persistent/monotonic only after applied scope transition; port-only local change retains session behavior. |
| Obsolete environment variable | isolated configuration | n/a | `tests/test_deployment_config.py::test_invalid_lower_priority_environment_and_obsolete_origin_do_not_override_saved_url` | PASS: `EDITARR_AUTH_PUBLIC_ORIGIN` does not select origin/startup behavior. |
| Artwork and preview over real TLS browser | Nginx / generated root URL / fresh RP-13 wheel dynamic listener | authenticated forms | final fresh-artifact production command below | PASS: browser fetched the real protected Editarr artwork route (200 `image/png`) and mute-preview route (200 `audio/mpeg`); only its external source/renderer boundaries were disposable fakes. |
| Cross-origin reconnect in real browser | two owned Nginx TLS origins / saved second `<origin>/cross` / same dynamic listener | authenticated forms, then new-origin sign-in | final fresh-artifact production command below | PASS: UI supplied the exact server target without credentialed cross-origin polling; after acknowledged restart and proxy readiness, browser followed it, received the sign-in gate, and completed forms sign-in. |

Verification commands and results:

- `UV_CACHE_DIR=/path/to/Editarr/.uv-cache uv run ruff check scanner tests scripts` — passed.
- `UV_CACHE_DIR=/path/to/Editarr/.uv-cache uv run pytest -q` — **441 passed**, 5 existing FastAPI/Starlette deprecation warnings.
- `pnpm test` — **9 files, 121 tests passed** in the final parent review after the
  RP-09 lifecycle regression was added.
- `pnpm build` — passed (`tsc` plus Vite production build).
- `pnpm test:e2e` — **30 passed**. One initial full-suite timeout in existing
  `opens the first library from the root route when configured` passed immediately on its
  focused rerun and the subsequent full 30-test run passed.
- `EDITARR_PRODUCTION_EDITARR='/tmp/Editarr RP12 installed env/bin/editarr'
  EDITARR_PRODUCTION_DEPENDENCY_SITE_PACKAGES='/path/to/Editarr/.venv/lib/python3.11/site-packages'
  EDITARR_PRODUCTION_PYTHON='/path/to/Editarr/.venv/bin/python'
  EDITARR_PRODUCTION_PROXY=nginx pnpm test:e2e:production` — **1 passed**.
- The same command with `EDITARR_PRODUCTION_PREFIX=/media/editarr` — **1 passed**;
  with `EDITARR_PRODUCTION_PROXY=caddy` — **1 passed**.
- `pnpm exec vitest run src/restart-editarr.test.tsx` — **16 passed** after the RP-09
  lifecycle fix; `pnpm build` passed.
- Fresh artifact: `UV_OFFLINE=1 UV_CACHE_DIR=/path/to/Editarr/.uv-cache
  uv run python scripts/build_release.py --out-dir '/tmp/Editarr RP13 build'` passed.
  Wheel SHA-256: `4ae7a2237d245303a2a332dedab156839552d3973a982b00388242c2cfff0c05`.
  Sdist SHA-256: `3b2b53fda9bc2aaeedfa76a3253230f07373e405a522846a5e69d86790b43ee8`.
  `uv venv '/tmp/Editarr RP13 installed env' --python .venv/bin/python` and
  `uv pip install --no-deps --python '/tmp/Editarr RP13 installed env/bin/python'
  '/tmp/Editarr RP13 build/editarr-0.1.0-py3-none-any.whl'` passed.
- `EDITARR_PRODUCTION_EDITARR='/tmp/Editarr RP13 installed env/bin/editarr'
  EDITARR_PRODUCTION_DEPENDENCY_SITE_PACKAGES='/path/to/Editarr/.venv/lib/python3.11/site-packages'
  EDITARR_PRODUCTION_PYTHON='/path/to/Editarr/.venv/bin/python'
  EDITARR_PRODUCTION_PROXY=nginx pnpm test:e2e:production` — **1 passed in 5.5s**
  (test 5.0s), exit 0, against the fresh artifact.

Acceptance criteria still unmet: None.

Approvals received or outstanding: None. No product or architecture decision, migration,
database wipe, deployment, global trust change, or real-service restart was needed.

Known failures/limitations: Fixture sources, renderer output, TLS CAs, ports, data, and
profiles are disposable and test-owned. External media/inference and a clean-network
dependency installation remain outside this verification claim. The RP-13 installed
environment reuses locked project site-packages by design. No developer data, user
process, system trust setting, service, or database was changed.

Next exact action: RP-14 may use this matrix, fresh artifact identities, and reproducible
commands for operator documentation and release acceptance.
