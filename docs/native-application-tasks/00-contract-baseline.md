# NATIVE-00 — Freeze contracts and baseline

Status: **DONE**

Dependencies: none. Deliverable: recorded contracts and baseline evidence; no product
implementation. Read the [task-pack README](README.md) and
[parent plan](../native-application-plan.md) first.

## Background

Native work crosses startup, settings import order, child processes, model downloads,
the browser UI, and three packaging systems. Later agents must not invent incompatible
role names, path fields, model states, or quit behavior. The current wheel release and
production browser verification are the behavioral baseline.

## Instructions

1. Inspect `git status --short`, applicable instructions, release/build scripts,
   `scanner/config.py`, `cli.py`, `api.py`, `restart_service.py`, `worker.py`, model
   modules, capability DTOs, and frontend scan forms. Record pre-existing changes.
2. Run and record a clean baseline without downloading models or touching real data:
   `uv run ruff check scanner tests scripts`, `uv run pytest -q`, and in `web/`,
   `pnpm test` and `pnpm build`. Use isolated data for any launch check.
3. Record the development host/toolchain, installed environment size, native dependency
   families, and current platform/model behavior. Do not claim other platforms.
4. Add a contract record to this file covering:
   - canonical `RuntimePaths` fields and override precedence;
   - public application modes and private frozen process roles;
   - singleton ownership and readiness transport;
   - HTTP restart versus full application Quit;
   - capability/download states shared by API and web;
   - version source and target identifiers.
5. Full Quit must stop the controller and all owned workers after a bounded graceful
   attempt. An interrupted active scan must remain recoverable/resumable using existing
   queue reconciliation; an HTTP-generation restart continues to preserve its worker.
6. Identify shared-file ownership and any baseline limitation that changes the order in
   `README.md`. Do not implement it here.

## Fixed decisions

- All decisions in the task-pack README are already approved.
- No database schema change or data wipe is expected or authorized.
- The frozen executable must retain source-checkout CLI behavior for developers.
- A second normal launch opens the existing UI and exits; it never starts a second server.
- Normal launch opens the browser after readiness; `--no-browser` remains available.

## Acceptance criteria

- [ ] Baseline commands, counts, warnings, host, and pre-existing changes are recorded.
- [ ] The contract names every shared interface needed by NATIVE-01, 04, 06, 07, and 10.
- [ ] HTTP restart and full Quit semantics are unambiguous.
- [ ] Deferred gaps `NATIVE-GAP-01` through `03` remain explicit.
- [ ] No application behavior, schema, real data, or live process changed.

## Definition of done

Meet the shared definition of done. Mark `DONE` only when the contract is sufficient for
dependent agents to implement without renaming or redesigning shared concepts. Update the
master checklist and handoff below.

## Handoff requirements

Include exact contract names/shapes, baseline commands/results, files inspected, known
platform unknowns, and `NATIVE-01` as the next eligible task. If a new material decision
is unavoidable, mark `BLOCKED` and state the one question requiring user approval.

## Contract record

### Baseline recorded 2026-09-05

The pre-task worktree was clean (`git status --short` produced no entries). No running
Editarr process, browser, user data directory, model download, schema operation, or
release artifact was used by this task. The required checks were run against the
checkout's test fixtures only:

| Command | Result |
| --- | --- |
| `uv run ruff check scanner tests scripts` | passed: `All checks passed!` |
| `uv run pytest -q` | passed: 441 tests in 21.51 s; 5 pre-existing deprecation warnings (Starlette `TestClient` and FastAPI `on_event`) |
| `cd web && pnpm test` | passed: 9 files, 121 tests |
| `cd web && pnpm build` | passed: TypeScript and Vite build; 36 modules transformed |
| `git diff --check` | passed after this documentation change |

Host evidence is limited to the development host: macOS 27.0 (build 26A5425a), Darwin
27.0.0, arm64; uv 0.12.5; Python 3.11.16; pnpm 11.22.0; checkout revision
`586f359`. The virtual environment is 1.7 GiB and `web/node_modules` is 133 MiB.
Installed native-dependency families include FastAPI/Uvicorn/SQLAlchemy, cryptography,
Torch 2.13.0, ONNX Runtime 1.28.0, NudeNet 3.4.2, Transformers 5.15.1,
faster-whisper 1.2.1, MLX-VLM 0.6.16, PyAV 18.1.0, OpenCV 5.0.0.93, and bundled
FFmpeg discovery through imageio-ffmpeg 0.6.0. This is dependency-inventory evidence,
not frozen-artifact or real-model-inference evidence.

The baseline currently has import-time `Settings()` defaults rooted at `./data`, global
model roots in the Qwen/Image Safety Gate/SigLIP2 modules, POSIX worker ownership
(`ps`, sessions, and process groups), and a source-worker command of
`sys.executable -m scanner.worker`. It exposes the legacy `ScanCapabilities` payload
(`audio_models`, `audio_filters`, `video_models`) but no enrichment/download state.
Qwen always follows flagged visual findings and rejects non-macOS; Windows/Linux
detector-only completion is therefore not yet implemented. The existing standalone
controller starts a spawned HTTP generation and intentionally leaves dedicated scan
workers alive across an HTTP-generation restart.

### Canonical runtime-path contract (NATIVE-01 onward)

`RuntimePaths` is an immutable, side-effect-free value resolved by the earliest entry
bootstrap before importing `scanner.config`, model modules, FastAPI, or database code.
Its canonical fields are:

```text
RuntimePaths(
  data_dir: Path,       # durable private state
  config_dir: Path,     # non-SQL configuration
  log_dir: Path,        # rotating logs and private diagnostic/control state
  cache_dir: Path,      # regenerable model and pipeline caches
  temp_dir: Path,       # per-run disposable frames/work files
  resources_dir: Path,  # immutable installed/source resources; never writable state
)
```

`data_dir` contains SQLite (`scanner.db` unless explicitly overridden), secrets,
deployment state, scan/queue state, durable replay inputs, and existing durable exports.
`config_dir` is reserved for non-SQL configuration. `log_dir` contains controller, HTTP,
worker, and crash logs plus user-private singleton/control metadata. `cache_dir` owns
downloaded weights, Hugging Face/library cache data, Core ML compilation cache, and
regenerable pipeline cache. `temp_dir` owns unique per-scan frame/work directories and
may be removed after a run. `resources_dir` identifies packaged frontend/static assets
and helper resources; it is distinct from every writable path.

Stable derived locations (not additional independently-overridable roots) are
`data_dir / "scanner.db"`, `data_dir / "secrets"`, `data_dir / "deployment.json"`,
`data_dir / "qwen-replays"`, `data_dir / "exports"`, `cache_dir / "models"`,
`cache_dir / "videos"`, and a unique child of `temp_dir` for each scan. EDL sidecars
remain next to user-selected media and are not redirected into application paths.

Native defaults are exactly the parent-plan table: data/config/cache use the documented
macOS Library, Windows LocalAppData, and Linux XDG roots; `log_dir` is the documented
Logs/diagnostic-state root; `temp_dir` uses usable `XDG_RUNTIME_DIR` on Linux and the OS
temporary directory otherwise. `resources_dir` comes only from the source/frozen
resource resolver. A source-checkout development launch context may select the
checkout-root `data` directory for predictable existing developer behavior; this must
be explicit in bootstrap state and must never be inferred from the process CWD.

Precedence is leaf-specific: an absolute public `--data-dir` wins for `data_dir`; then a
nonempty absolute `EDITARR_DATA_DIR`; then the selected development/native default.
Relative explicit roots fail with an actionable error. Existing explicit leaf settings,
especially `EDITARR_DATABASE_URL`, retain their documented precedence over their derived
location; the existing `EDITARR_FRAMES_DIR`, `EDITARR_EXPORTS_DIR`, and
`EDITARR_QWEN_REPLAYS_DIR` remain compatibility overrides until their consumer tasks
document their precise migration. A durable-data override does not move config, logs,
cache, temporary files, or resources. Importing/resolving this value creates no
directories; the relevant owner creates a directory with user-only permissions where
the OS/filesystem supports it.

### Executable, process, singleton, and lifecycle contract

Public modes are `normal-launch`, public offline CLI (`admin`, `auth`, and later
`paths`), and explicit source-development launch. A normal launch accepts the ordinary
public server options plus `--no-browser`; it owns one controller. `--no-browser` and
foreground diagnostics never imply a second instance. Public help and configuration do
not expose private roles.

The frozen executable has exactly these private roles, dispatched before public argparse
or heavyweight imports:

```text
--internal-role http-child
--internal-role scan-worker --scan-id <scan UUID> --worker-token <opaque token>
```

`http-child` is controller-owned and receives only inherited/private controller IPC.
`scan-worker` has no browser or singleton responsibility. Source development keeps the
equivalent `python -m scanner.worker` path for tools/tests. Every executable entry calls
`multiprocessing.freeze_support()` before this dispatch. Private-role validation must
fail without echoing an opaque token.

The controller owns the per-user singleton, instance metadata, readiness lifecycle, and
HTTP generations; it owns scan-worker termination only for a full application Quit.
The control plane is user-private local IPC: Unix-domain socket in a private directory
on macOS/Linux and a same-user ACL named pipe on Windows, with an owner-only random
instance token. It is never an unauthenticated TCP API. Its versioned metadata records
only a process identity safe against PID reuse, control endpoint, active configured
public URL, and HTTP generation. Supported control requests are `open-ui`, `open-logs`,
`status`, and `quit`.

Readiness is a controller-observed event emitted only after the HTTP child has bound its
listener, activated the application, and published the immutable active deployment
snapshot. Its payload is conceptually `{status: "ready", generation, public_url}`;
`public_url` is the active configured Base URL/prefix, never a request Host value. First
normal launch waits a bounded time for this event/health result, then opens the browser.
A proven second launch sends `open-ui` to that controller and exits without spawning a
server. Browser close never stops Editarr.

An in-web **Restart Editarr** is an HTTP-generation restart only: the controller stops
and replaces its HTTP child while the independent active scan worker remains alive and
is adopted/reconciled by the next generation. A full application **Quit** is different:
the controller first stops admission/scheduling, requests a bounded graceful worker
stop, then forcibly terminates only processes whose ownership identity it proves. If a
scan is interrupted, its existing queue/checkpoint reconciliation marks it recoverable
and resumable; Quit must not silently discard queue history or corrupt durable state.

### Capability, download, target, and version contract

NATIVE-07 is the single backend source of truth. It will extend the legacy
`ScanCapabilities` response compatibly with stable structured records rather than make
the web UI infer platform support:

```text
ModelCapability {
  id, display_name,
  stage: "audio" | "detector" | "enrichment",
  availability: "available" | "unavailable",
  reason: null | "unsupported_platform" | "unsupported_architecture" |
          "missing_package" | "missing_provider" | "deferred_gap",
  device: "cpu" | "cuda" | "coreml" | "metal" | "mlx" | "none",
  weight_requirement: "none" | "on_demand",
  download: ModelDownloadState
}
ModelDownloadState {
  state: "not_required" | "not_downloaded" | "downloading" | "ready" |
         "failed" | "cancelled",
  bytes_downloaded, bytes_total, error_code, error_detail
}
```

`bytes_total`, `error_code`, `error_detail`, and progress values may be null when not
known; no token, local credential, or private filesystem path appears in the API. A
capability query is fast, offline, and has no download/model-load side effect. NATIVE-08
owns the download transitions and NATIVE-09 renders them. While retaining existing
`audio_models`, `audio_filters`, and `video_models` for compatibility, each selectable
stage must be linked to its detailed capability.

Machine-readable target IDs are `macos-arm64`, `windows-x64`, and `linux-x86_64`.
`macos-arm64` promises the current audio/detector stacks and MLX Qwen enrichment subject
to packaged-runtime validation. `windows-x64` and `linux-x86_64` promise CPU fallback for
faster-whisper and CPU NudeNet/Image Safety Gate/SigLIP2 detectors. Faster-whisper may
report `device: "cuda"` only after the packaged CTranslate2 runtime proves a usable host
NVIDIA device/driver; bundled DLL presence is insufficient. Qwen enrichment is
unavailable with reason `deferred_gap`; flagged detector findings complete unchanged and
unsummarized. `NATIVE-GAP-01` is Windows/Linux Qwen, `NATIVE-GAP-02` is closed/revised by
this additive CUDA contract, and `NATIVE-GAP-03` is Intel macOS, Windows ARM64, and Linux
ARM64.

The one canonical product version source is `[project].version` in `pyproject.toml`.
Python metadata, executable metadata, the FastAPI API version, macOS bundle metadata,
Windows installer metadata, AppImage metadata, artifact inventory, and release notes
must derive from it. Baseline mismatch recorded: `pyproject.toml` and `web/package.json`
are 0.1.0 while `scanner.api` declares 0.2.0; NATIVE-10 must make generated/frozen
metadata consistent without inventing a second source.

### Shared ownership and ordering

| Area / files | Next owner | Boundary |
| --- | --- | --- |
| Runtime path resolver, earliest bootstrap, config declarations, path tests | NATIVE-01 | Establish `RuntimePaths`; do not migrate durable/cache consumers. |
| DB, auth crypto/audit, deployment, exports, artwork, replay consumers | NATIVE-02 | Move durable state only; preserve explicit database override. |
| Pipeline cache/frames and Qwen/Image Safety Gate/SigLIP2 model/cache roots | NATIVE-03 | Move regenerable models/caches/helpers after durable migration. |
| CLI/executable bootstrap, worker launch/dispatch, resource resolver | NATIVE-04 | Implement the private-role contract without lifecycle redesign. |
| API/restart service/worker/runner process ownership | NATIVE-05 | Implement cross-platform worker ownership and full-Quit behavior. |
| Launcher singleton, readiness, browser seam, rotating logs, lifecycle adapter | NATIVE-06 | Implement local control after NATIVE-05. |
| `scanner.models`, API capability endpoint, visual/runner validation and fallback | NATIVE-07 | Own detailed capability DTO and Qwen detector-only fallback; NATIVE-09 owns UI. |
| PyInstaller spec/hooks/build inventory and version propagation | NATIVE-10 | Use this contract after NATIVE-06 and NATIVE-09. |

The existing dependency order remains valid; no README ordering change is required.
NATIVE-07 may still run alongside NATIVE-04/05 only under the README's stated shared
file coordination. No schema change is expected or authorized.

## Handoff record

```text
Status: DONE
Last completed substep: Recorded the baseline and frozen shared contracts without product changes.
Files changed: docs/native-application-tasks/00-contract-baseline.md; docs/native-application-tasks/README.md.
Verification run and result: `uv run ruff check scanner tests scripts` passed; `uv run pytest -q` passed (441, 5 pre-existing deprecation warnings); `cd web && pnpm test` passed (9 files, 121 tests); `cd web && pnpm build` passed; `git diff --check` passed.
Artifact/host evidence: macOS 27.0 arm64 development host only; Python 3.11.16, uv 0.12.5, pnpm 11.22.0. No frozen artifact, other target host, browser, real model, user data, or live Editarr instance was used.
Known failures or risks: Current import-time `./data` settings/model paths, POSIX-only worker lifecycle, absent singleton/logging, and automatic Qwen-after-flagging remain baseline limitations for their assigned tasks. Version metadata currently disagrees (pyproject/web 0.1.0, API 0.2.0). Test suite emits 5 existing deprecation warnings.
Next exact action: NATIVE-01 implements the side-effect-free `RuntimePaths` resolver and earliest bootstrap using this contract.
Next eligible task: NATIVE-01.
Decisions or deviations from the parent plan: Selected `pyproject.toml [project].version` as the required single version source; it is a documentation contract only in this task. No deviation, schema change, data wipe, or behavior change.
User approval required: None.
```
