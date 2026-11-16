# NATIVE-01 — Create early runtime-path resolution

Status: **DONE**

Dependencies: NATIVE-00. Primary scope: a path-resolution module, earliest entry-point
bootstrap, configuration declarations, dependency metadata, and focused path tests.

## Background

`scanner.config.settings` is constructed at import time with `./data` defaults. A native
app may start with `/`, a browser-selected directory, or a read-only bundle as its CWD.
Paths must be resolved before importing configuration or model modules. This task creates
the abstraction; NATIVE-02/03 migrate consumers.

## Instructions

1. Implement the `RuntimePaths` contract frozen by NATIVE-00 with pure, testable
   resolution functions for macOS, Windows, and Linux/XDG. A direct `platformdirs`
   dependency may be added and locked if it meets the contract.
2. Resolve platform defaults, `EDITARR_DATA_DIR`, and `--data-dir` without importing
   heavyweight application modules. Reject relative explicit overrides with an
   actionable error unless NATIVE-00 explicitly permits resolving them.
3. Establish the paths before `scanner.config.settings` is constructed. Keep a safe
   dependency-injection seam so tests can resolve all platforms on one host.
4. Separate durable data, config, state/log, cache/model, runtime/temp, and immutable
   bundle resources. Do not create every directory merely by importing the module.
5. Add focused tests for precedence, absent/empty XDG variables, Unicode/spaces, Windows
   path syntax, macOS paths, permissions intent, and arbitrary/read-only CWD.
6. Document the public override without changing unrelated configuration precedence.

## Fixed decisions

- Use the path table in the parent plan.
- Durable data override does not move the cache unless the NATIVE-00 contract says so.
- Environment and CLI overrides are operator features, not persisted settings.
- Bundle resources are read-only; state is never stored beside the executable.

## Acceptance criteria

- [ ] All target-platform defaults match the parent path table.
- [ ] Resolution occurs before global settings/model path construction.
- [ ] Importing the resolver has no filesystem side effect.
- [ ] Explicit override precedence and validation are tested.
- [ ] Existing development launches retain an explicit predictable data location.

## Definition of done

Focused path/config/CLI tests and Ruff pass, lock metadata is updated when needed, docs
match behavior, `git diff --check` passes, and no consumer migration is smuggled into
scope. Update the checklist and handoff.

## Handoff requirements

Record the exact `RuntimePaths` API, initialization call site, precedence, dependency
change, test commands, and the consumer inventory for NATIVE-02/03.

## Handoff record

```text
Status: DONE
Last completed substep: Added the side-effect-free RuntimePaths resolver, initialized it before source CLI/worker configuration imports, and changed only configuration declarations/defaults; no durable or cache consumer was migrated.
Files changed: scanner/runtime_paths.py; scanner/config.py; scanner/cli.py; scanner/worker.py; tests/test_runtime_paths.py; README.md; docs/native-application-tasks/01-runtime-paths.md; docs/native-application-tasks/README.md.
Verification run and result: `uv run ruff check scanner tests` passed. `uv run pytest -q tests/test_runtime_paths.py tests/test_cli.py tests/test_development.py tests/test_backend_routing.py tests/test_startup_gate.py` passed: 71 tests in 8.16s; 5 existing Starlette/FastAPI deprecation warnings. `git diff --check` passed.
Artifact/host evidence: Pure resolver tests exercised macOS, Windows (PureWindowsPath from the macOS host), and Linux/XDG contracts without creating paths. CLI bootstrap was verified in a subprocess from an arbitrary working directory with a spaced absolute --data-dir. No frozen artifact, other target host, browser, user data, live service, model, schema operation, or release artifact was used.
Known failures or risks: This is path resolution/configuration only. Direct library imports safely use native defaults, while source CLI and worker entry points explicitly select the checkout data directory; NATIVE-04 owns frozen resource discovery/roles. Actual Windows filesystem/long-path and installed-artifact checks remain for NATIVE-11/15. Existing consumer roots still require the migrations below.
Next exact action: NATIVE-02 must migrate durable consumers to RuntimePaths: scanner.db/database_url, auth crypto/audit/repository, deployment state, API source-artwork and exports/replays; it must preserve EDITARR_DATABASE_URL and the legacy leaf overrides. NATIVE-03 then migrates pipeline cache/frames plus Qwen, Image Safety Gate, SigLIP2, Hugging Face, and helper/model roots to cache/temp paths.
Next eligible task: NATIVE-02.
Decisions or deviations from the parent plan: RuntimePaths(data_dir, config_dir, log_dir, cache_dir, temp_dir, resources_dir) is scanner.runtime_paths.RuntimePaths. `resolve_runtime_paths()` is pure and injectable by platform/environment/home/temp/resources; `bootstrap_entrypoint_runtime_paths()` and `initialize_runtime_paths_from_argv()` initialize it before scanner.config.settings in scanner.cli and scanner.worker. `--data-dir` absolute > nonempty absolute EDITARR_DATA_DIR > explicit source-checkout data default or native platform default; the override changes data_dir only. USER_PRIVATE_DIRECTORY_MODE is 0700 for later directory-owning consumers. No dependency or lockfile change, schema change, data wipe, or deviation.
User approval required: None.
```
