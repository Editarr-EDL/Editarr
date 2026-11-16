# NATIVE-02 — Move durable state and logs to runtime paths

Status: **DONE**

Dependencies: NATIVE-01. Primary scope: database/config/secrets/deployment/replay/log
consumers, directory creation policy, path diagnostics, and regression tests.

## Background

NATIVE-01 supplies path resolution but existing consumers still use settings defaults
and CWD-derived locations. Durable credentials, SQLite state, deployment metadata,
authentication audit logs, and Qwen replay inputs must survive app upgrades and cache
deletion. This task migrates durable/state consumers without changing schema.

## Instructions

1. Inventory all reads/writes of `data_dir`, `database_url`, secrets, deployment JSON,
   logs, exports, and replay inputs with `rg`; record the inventory in the handoff.
2. Make the default SQLite URL derive safely from the resolved durable data root while
   preserving an explicit `EDITARR_DATABASE_URL` override.
3. Move deployment metadata, authentication secrets/audit logs, retained replay inputs,
   and other durable files to their contracted roots. Preserve atomic writes and
   user-only permissions.
4. Configure rotating application/controller/HTTP log files at the resolved log root;
   NATIVE-06 will connect child process output to this policy.
5. Add `editarr paths` (or the NATIVE-00-contracted equivalent) that prints resolved
   non-secret locations without importing/starting the API or exposing credentials.
6. Update tests to use injected temporary `RuntimePaths`, never the developer's native
   Application Support/AppData/XDG directories.
7. Update operator documentation. Do not automatically move or delete an existing
   checkout `data/` directory.

## Fixed decisions

- No database schema change, migration, or development-data wipe.
- Model weights and disposable frames are not durable and remain for NATIVE-03.
- An ordinary uninstall preserves durable state unless the user explicitly opts to
  remove it in a later platform installer task.

## Acceptance criteria

- [ ] No default durable write depends on CWD or the installation directory.
- [ ] Explicit database and data-root overrides retain documented precedence.
- [ ] Secrets and audit logs retain restrictive permissions and atomic behavior.
- [ ] Cache removal cannot remove database, credentials, deployment state, or replays.
- [ ] Path diagnostics are safe, offline, and covered by tests.

## Definition of done

Focused DB/auth/deployment/config/CLI tests and Ruff pass, documentation is current,
`git diff --check` passes, and consumer inventory shows no missed durable `./data` path.
Update the checklist and handoff.

## Handoff requirements

List every migrated consumer, final paths and precedence, old checkout-data behavior,
test evidence, and the exact remaining cache/model consumers assigned to NATIVE-03.

## Handoff record

```text
Status: DONE
Last completed substep: Repaired every traced API/CLI/controller/subprocess test isolation gap after moving durable logs, then passed focused and full verification without recreating native runtime roots.
Files changed: README.md; scanner/runtime_paths.py; scanner/config.py; scanner/db.py; scanner/deployment_config.py; scanner/auth_audit.py; scanner/api.py; scanner/cli.py; scanner/runtime_logging.py; tests/test_durable_runtime_paths.py; tests/test_auth_services.py; tests/test_cli.py; tests/test_startup_gate.py; tests/test_editarr_integration.py; tests/test_restart_service.py; tests/test_backend_routing.py; tests/test_development.py; docs/native-application-tasks/02-durable-path-consumers.md; docs/native-application-tasks/README.md.
Verification run and result: `uv run ruff check scanner tests` passed. Focused isolation verification, `uv run pytest -q tests/test_editarr_integration.py::test_settings_api_tests_before_saving_and_never_returns_key tests/test_editarr_integration.py::test_default_filter_settings_api_updates_and_reads tests/test_cli.py tests/test_startup_gate.py tests/test_restart_service.py tests/test_backend_routing.py tests/test_development.py`, passed: 76 tests in 16.20s with 5 existing FastAPI/Starlette deprecation warnings. Full verification, `uv run pytest -q`, passed: 463 tests in 20.07s with the same 5 warnings. `git diff --check` passed. Metadata-only post-run checks returned `ABSENT:~/Library/Logs/Editarr` and `ABSENT:~/Library/Caches/Editarr` after both focused and full runs.
Artifact/host evidence: macOS development-host unit coverage only. Tests inject temporary RuntimePaths/settings or temporary HOME/TMPDIR for every writable root; no frozen artifact, browser, real model, schema operation, data migration, or live Editarr instance was used.
Known failures or risks: NATIVE-06 must connect spawned HTTP/worker child stdout/stderr to this already-defined log policy; NATIVE-03 owns source-artwork, video/model caches, temporary frames, helper discovery, and all model roots. The current test suite still has the recorded five FastAPI/Starlette deprecation warnings. No existing checkout data is migrated automatically. The initial lapse created four logs under `~/Library/Logs/Editarr`; cleanup was user-authorized and completed. A second shared-application test isolation gap and a third controller-subprocess HOME/TMPDIR gap each recreated that exact root; each cleanup was explicitly user-authorized and completed by the root agent. The final repair patches the shared Settings object retained by API middleware, gives controller subprocesses temporary HOME/TMPDIR, and applies the same isolation to API/development subprocess tests; focused and full post-repair metadata checks confirm neither native log nor cache root is recreated.
Next exact action: NATIVE-03 migrates the remaining cache/model/frame/helper consumers while retaining `qwen_replays_dir` as durable data.
Next eligible task: NATIVE-03.
Decisions or deviations from the parent plan: None. The default database URL is `${data_dir}/scanner.db`; absolute `--data-dir` then absolute nonempty `EDITARR_DATA_DIR` chooses data_dir, while explicit `EDITARR_DATABASE_URL` remains the leaf override. Data-root overrides do not move config/log/cache/temp/resources. Deployment JSON/lock remain `${data_dir}/deployment.json` and `.lock` with atomic replacement and restrictive modes; CSRF key remains `${data_dir}/secrets/auth-csrf.key`; durable exports/replays remain `${data_dir}/exports` and `${data_dir}/qwen-replays`; auth audit is now `${log_dir}/auth-audit.jsonl`; rotating application/controller/HTTP logs are `${log_dir}/application.log`, `controller.log`, and `http.log`. `editarr paths` prints data/config/log/cache/temp/resources without creating them, starting the API, or printing secrets. A source-checkout launch still explicitly uses checkout `data/`; a native launch uses the per-user data root. Neither copies, deletes, nor auto-migrates an existing checkout `data/` directory.
User approval required: None.
```

## Durable consumer inventory (completed)

| Consumer | Final root/path | Notes / precedence |
| --- | --- | --- |
| SQLAlchemy SQLite default | `${data_dir}/scanner.db` | `EDITARR_DATABASE_URL` remains explicit and wins over the derived default. |
| Database directory creation | `${data_dir}` | Created owner-private where POSIX permissions are available. |
| CSRF recovery key | `${data_dir}/secrets/auth-csrf.key` | Existing environment key remains higher precedence; atomic/concurrent-safe key creation is unchanged. |
| Deployment snapshot/lock | `${data_dir}/deployment.json`, `.lock` | Data root is owner-private; lock/final file are restricted and JSON replacement remains atomic. |
| Durable exports | `${data_dir}/exports` | Existing `EDITARR_EXPORTS_DIR` leaf override remains supported. |
| Retained Qwen replay inputs | `${data_dir}/qwen-replays` | Existing `EDITARR_QWEN_REPLAYS_DIR` leaf override remains supported; NATIVE-03 must retain this durable root. |
| Authentication audit | `${log_dir}/auth-audit.jsonl` | Daily rotation/retention and restrictive file/directory modes are preserved. |
| Application diagnostics | `${log_dir}/application.log`, `controller.log`, `http.log`, `worker.log` | Owner-private rotating-file policy; NATIVE-06 owns child-process output wiring. |
| Offline diagnostics | `editarr paths` | Prints only the six resolved roots and has no API/database/directory side effect. |

The checkout `data/` directory is retained exactly as-is: source-development bootstrap
continues to select it explicitly, while native defaults select their platform data
root. There is no automatic copy, deletion, or migration.

## Remaining NATIVE-03 inventory

- `scanner.pipeline.cache`: `${data_dir}/cache/videos` legacy video cache.
- `scanner.api`: `${data_dir}/source-artwork` regenerable source-artwork cache.
- `settings.frames_dir` and `scanner.pipeline.runner`: disposable frame/work directories.
- `scanner.pipeline.qwen_summary`, `image_safety_gate`, and `siglip2_moderation`:
  checkout-relative model roots; plus Hugging Face/Transformers/library download cache
  environment and model artifact placement.
- FFmpeg/ffprobe and imageio helper resolution, scene/preview cache behavior, and any
  remaining CWD/support-file assumptions. `scanner.api`'s child-process `Path.cwd()`
  lifecycle use must be evaluated by its owning lifecycle task if it is not a support
  path migration.
