# NATIVE-04 — Add frozen executable bootstrap and internal roles

Status: **DONE**

Dependencies: NATIVE-03. Primary scope: executable entry point, frozen/source dispatch,
multiprocessing bootstrap, private roles, and role-dispatch tests.

## Background

In a frozen application `sys.executable` is Editarr, not Python. Existing worker launch
uses `sys.executable -m scanner.worker`, and spawned multiprocessing children relaunch
the frozen program with special arguments. Without early dispatch, argparse errors or a
recursive spawn loop result. The bootstrap must run before settings and ML imports.

## Instructions

1. Implement the NATIVE-00 executable-mode and internal-role contract in a minimal entry
   module that imports no FastAPI, SQLAlchemy, Torch, ONNX, MLX, or application settings
   before child diversion and path bootstrap.
2. Call `multiprocessing.freeze_support()` at the earliest safe point on every platform.
3. Dispatch normal controller launch, offline public CLI commands, and the private scan
   worker role. Preserve `python -m scanner.worker` or an equivalent source-development
   path where tests/tools require it.
4. Change worker command construction to relaunch the frozen executable with its private
   role when frozen and use the source interpreter safely when unfrozen.
5. Keep private roles out of normal help and reject direct malformed invocation without
   leaking worker tokens or secrets.
6. Make bundled-resource discovery explicit and independent from writable runtime paths.
7. Add tests that simulate frozen/unfrozen values, multiprocessing child arguments,
   public CLI commands, malformed internal roles, and import-order constraints.

## Fixed decisions

- One frozen program may execute multiple private roles.
- PyInstaller-specific environment workarounds belong behind a small freezer adapter,
  not scattered throughout application code.
- An HTTP restart remains controller-owned; a private child never launches a browser.

## Acceptance criteria

- [x] Frozen multiprocessing arguments divert before the public parser/heavy imports.
- [x] Frozen scan workers execute the intended worker function exactly once.
- [x] Source CLI, admin/auth commands, and development entry points still work.
- [x] Private malformed arguments fail safely and are absent from public help.
- [x] Resource lookup never treats runtime data/cache as bundled resources.

## Definition of done

Focused CLI/restart/worker/bootstrap tests and Ruff pass, an import-order assertion is
present, `git diff --check` passes, and interfaces needed by NATIVE-05/10 are documented.

## Handoff requirements

Record the actual entry module, role names, frozen detection/resource API, source/frozen
worker commands, multiprocessing behavior, tests, and any freezer-specific assumption.

## Handoff record

```text
Status: DONE
Last completed substep: Added the standard-library-only executable bootstrap, private-role validation/dispatch, frozen/source scan-worker command selection, and explicit frozen resource discovery without changing controller ownership.
Files changed: scanner/entry.py; scanner/runtime_paths.py; scanner/api.py; tests/test_frozen_bootstrap.py; docs/native-application-tasks/04-frozen-bootstrap.md; docs/native-application-tasks/README.md.
Verification run and result: With a new isolated `/tmp/editarr-native04.*` HOME, TMPDIR, data, XDG cache/state, and uv cache, `uv run ruff check scanner tests` passed. `uv run pytest -q tests/test_frozen_bootstrap.py tests/test_runtime_paths.py tests/test_cli.py tests/test_queue_lifecycle.py tests/test_restart_service.py` passed: 68 tests in 9.87s with 5 existing FastAPI/Starlette deprecation warnings. The first sandboxed run passed 62 tests but five actual restart-listener cases could not bind localhost (`PermissionError`); the same isolated command was rerun with loopback permission and passed all 68. `git diff --check` passed. Metadata-only checks before and after each run reported `ABSENT:~/Library/Caches/Editarr` and `ABSENT:~/Library/Logs/Editarr`.
Artifact/host evidence: macOS development-host unit/integration coverage only. No frozen artifact, browser, real model, user data, live Editarr service, schema operation, migration, or release artifact was used. The subprocess import-order test runs from an arbitrary temporary CWD and asserts `scanner.config` remains absent before and after a malformed private invocation.
Known failures or risks: `http-child` is intentionally recognized as the second private role but has no command-line payload and rejects direct invocation until NATIVE-05 moves the current inherited multiprocessing controller IPC to that role. Current `scanner.api` worker ownership remains POSIX-specific (`ps`, sessions, process groups, and `Path.cwd()`); NATIVE-05 owns that redesign. This task provides no PyInstaller collection proof; NATIVE-10 must collect resources at the resource root selected through `sys._MEIPASS` (with executable-parent fallback) and validate a real frozen artifact.
Next exact action: NATIVE-05 implements cross-platform ownership and full-Quit behavior, then wires the inherited HTTP-child context to the already reserved private role.
Next eligible task: NATIVE-05.
Decisions or deviations from the parent plan: `scanner.entry:main` is the future freezer entry module. It calls `multiprocessing.freeze_support()` before runtime-path bootstrap, role dispatch, public argparse, or application imports. Its only roles are `http-child` and `scan-worker`; the latter requires exactly `--scan-id <UUID>` and nonempty `--worker-token <opaque token>`, calls `scanner.worker.run_scan_worker()` once after validation, and all malformed private input prints only `error: invalid internal Editarr invocation` without echoing argv. Public parser help continues to have no private role. `scanner.runtime_paths.is_frozen_application()` checks `sys.frozen`; `resolve_resource_directory()` selects PyInstaller `sys._MEIPASS`, otherwise the frozen executable parent, and source checkout/package roots only in source mode. Resources never derive from data/cache/log/temp roots. Exact source worker command is `sys.executable -m scanner.worker --scan-id <scan UUID> --worker-token <opaque token>`; exact frozen command is `sys.executable --internal-role scan-worker --scan-id <scan UUID> --worker-token <opaque token>`. No lifecycle, singleton/browser/log wiring, capability/model, PyInstaller collection, schema, migration, or data-wipe behavior changed.
User approval required: None.
```
