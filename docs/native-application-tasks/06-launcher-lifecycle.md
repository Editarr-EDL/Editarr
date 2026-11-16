# NATIVE-06 — Implement single-instance launch, readiness, browser, and logging

Status: **DONE**

Dependencies: NATIVE-05. Primary scope: per-user singleton/control state, readiness,
browser launch, child log plumbing, and platform-lifecycle adapter used by packages.

## Background

Normal users launch an application icon, not a terminal command. First launch must start
the server and open its configured UI only after readiness. Reopening the app must find
the same user's running instance and open that UI. Controller/worker failures currently
may disappear into `DEVNULL`; native packages need durable diagnostics.

## Instructions

1. Implement a per-user singleton using user-private runtime state and authenticated or
   OS-protected local control. Do not expose an unauthenticated TCP management endpoint.
2. Publish only the minimum instance metadata needed to prove ownership and request
   `open-ui`, `open-logs`, status, and full Quit. Treat stale state and PID reuse safely.
3. On first normal launch, start the controller, wait on a real readiness signal/health
   result with a bounded timeout, then open the exact active public URL in the default
   browser. Never derive it from request `Host` headers.
4. On a second launch, request `open-ui` from the proven existing instance and exit.
5. Preserve `--no-browser`, foreground diagnostics, offline admin/auth/path commands,
   configured Base URL prefixes, and saved/CLI port precedence.
6. Route controller, HTTP, scan worker, and uncaught startup diagnostics to rotating
   native log files. No credentials, session values, CSRF keys, or worker secrets may be
   logged. Normal Windows launch must tolerate `stdout`/`stderr` being absent.
7. Expose a small lifecycle adapter for NATIVE-12/13 menu/tray actions without choosing
   a machine service. Launch-at-login installation remains platform packaging work.
8. Add tests for first/second launch, stale singleton, readiness timeout, bind failure,
   prefix URL, browser failure, log rotation/redaction, and Quit during active work.

## Fixed decisions

- Browser close does not stop Editarr. Explicit application Quit does.
- No browser is opened by tests or private child roles.
- The existing in-web Restart action remains an HTTP-generation restart, not full Quit.

## Acceptance criteria

- [x] Concurrent normal launches produce exactly one controller and one browser action.
- [x] Browser launch occurs only after readiness and uses the active configured URL.
- [x] Stale/corrupt instance state recovers without killing an unrelated process.
- [x] All process diagnostics reach rotating native logs with secret redaction.
- [x] Lifecycle actions are usable by macOS/Windows package shells without API weakening.

## Definition of done

Focused launcher/CLI/restart/auth/log tests and Ruff pass, real macOS and Windows launch
checks pass with an injected/no-op browser, `git diff --check` passes, and platform-shell
integration points are recorded. Missing required Windows evidence means `BLOCKED`.

## Handoff requirements

Record singleton/control format and security, readiness protocol, browser command seam,
log files/rotation, lifecycle adapter, native-host evidence, and packaging instructions.

## Handoff record

```text
Status: DONE
Last completed substep: Recorded the successful required Windows x64 source-launcher evidence for commit `98ab1fc`, completing the macOS-and-Windows host requirement.
Files changed: scanner/native_lifecycle.py; scanner/runtime_logging.py; scanner/cli.py; scanner/entry.py; scanner/restart_service.py; scanner/worker.py; tests/test_native_lifecycle.py; tests/test_cli.py; tests/test_restart_service.py; tests/test_durable_runtime_paths.py; .github/workflows/native06-windows-launcher.yml; README.md; docs/native-application-tasks/02-durable-path-consumers.md; docs/native-application-tasks/06-launcher-lifecycle.md; docs/native-application-tasks/README.md.
Verification run and result: On the reviewed macOS development host, focused Ruff passed; `uv run pytest -q tests/test_native_lifecycle.py tests/test_cli.py tests/test_durable_runtime_paths.py tests/test_restart_service.py tests/test_frozen_bootstrap.py tests/test_process_control.py tests/test_queue_lifecycle.py` passed 98 tests in 10.65s with 5 known FastAPI/Starlette deprecation warnings. Ruby YAML parsing for `.github/workflows/native06-windows-launcher.yml` and `git diff --check` passed. The corrected isolated run neither modified the previously leaked user log directory nor left sockets. On GitHub Actions, Ruff and `git diff --check` passed; the host native Editarr-root absence guard passed; and pytest passed 93 tests, skipped 1 POSIX-only session-isolation test, with 5 known warnings in 39.91s.
Artifact/host evidence: Successful required Windows evidence is GitHub Actions run `34070595780` at commit `98ab1fc`: job `Windows x64 source launcher tests` on `windows-2025-vs2026`, image `20260824.214.3`, CPython 3.11.9, and uv 0.12.5 (`x86_64-pc-windows-msvc`). The retained JUnit artifact `native06-windows-x64-junit-34070595780/native06-windows-junit.xml` is 13,397 bytes, SHA-256 `6cabcdd5c7c9773e89196cfe8a00127308d2a843462df726169533bf7cb32bfb`, with tests=94, errors=0, failures=0, skipped=1, time=39.897. It covers Windows process-held singleton locking, authenticated named-pipe lifecycle, first/second launch, active URL, readiness timeout/cleanup, redacted rotating controller/http/worker logging, actual controller/HTTP restart, worker preservation, and full Quit. macOS evidence is source-host/injected-browser only; no frozen application, real browser, user data, or live Editarr instance was used.
Known failures or risks: The normal native path is frozen execution (or the explicit `EDITARR_NATIVE_LAUNCHER=1` package/test seam); `--foreground` propagates an explicit private-child marker so frozen diagnostic launches retain their console. Native log redaction removes credential-shaped key/value and header values but remains defense in depth; callers must not log bare credentials or opaque capabilities. An earlier flawed test created `~/Library/Logs/Editarr` with four task-looking files because its child inherited the developer HOME. Cleanup remains awaiting the user's explicit authorization; it is disclosed post-test housekeeping, not a code or required-host-evidence blocker. The later corrected isolated verification did not modify that directory or leave sockets.
Next exact action: Begin NATIVE-07 capability/fallback work; retain the disclosed log-directory cleanup decision until the user explicitly authorizes it.
Next eligible task: NATIVE-07 — Make model capabilities truthful and Qwen optional.
Decisions or deviations from the parent plan: `${data_dir}/native-instance/instance.lock` is a persistent owner-private file held for the controller lifetime with POSIX `flock` or Windows `msvcrt.locking`; crash recovery comes from automatic kernel-lock release, and a held but unresponsive owner is never unlinked or replaced. Atomic `instance.json` contains only version, controller PID, random local endpoint, opaque capability, exact active public URL, and readiness. POSIX uses an owner-private AF_UNIX endpoint; Windows uses a logon-SID ACL named pipe plus capability authentication. Every action requires authenticated endpoint response and exact metadata PID; stale/corrupt state is ignored without signaling a PID. The endpoint must exactly match the token-derived endpoint before a client connects. A second launch first requests the owner's active URL before resolving pending server configuration. The controller publishes every activated URL but opens the browser only on first readiness; later explicit `open-ui` remains available through public `NativeInstance` methods. `--no-browser` suppresses opening and `--foreground` preserves direct diagnostics in parent and private children. Native stdout/stderr emit through the same owner-private rotating/redacting handler used by each process role: `controller.log` covers controller/startup failures, `http.log` covers HTTP generations, and `worker.log` covers scan workers, including with absent Windows standard handles.
User approval required: Explicit approval is required only to delete the disclosed task-created `~/Library/Logs/Editarr` directory; no implementation decision is pending.
```
