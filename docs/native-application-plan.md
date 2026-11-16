# Editarr native application distribution plan

## Status

Approved product direction. This plan records the decisions confirmed on
2026-09-05 and divides implementation into independently verifiable milestones.

Implementation is divided into checkpointable Terra-High assignments in the
[native application coding task pack](native-application-tasks/README.md).

## Objective

Distribute Editarr as one user-visible, self-contained application for each
supported operating system. An end user must not install Python, pip, Node.js,
pnpm, or Editarr dependencies. Launching the application starts Editarr's
background controller, HTTP server, and scan workers and opens the web UI in the
default browser after the server is ready.

"One application" means one download and one normal application entry point.
The installed application may contain private runtime libraries and helper
executables. It does not mean that every native library must be embedded in one
physical executable file.

The first release is per-user software, not a machine-wide daemon. It runs only
while that user is logged in. Installing or launching it must not require the
user to create a Python environment.

## Confirmed decisions

1. Keep the Python backend and React web UI. A language rewrite is not part of
   this project.
2. Freeze CPython and all runtime dependencies into platform-specific builds.
   Do not publish a wheel or source distribution as an end-user release.
3. Ship these initial targets:
   - macOS on Apple Silicon (`arm64`)
   - Windows on x86-64 (`x64`)
   - Linux on x86-64 (`x86_64`)
4. Run Editarr in the logged-in user's session. System services and unattended
   boot-time operation are deferred.
5. Download model weights on demand into a managed cache. Do not put model
   weights in the application or installer.
6. Windows and Linux guarantee CPU inference and may additionally use CUDA when a
   compatible NVIDIA GPU/driver and the packaged backend runtime are both verified.
   GPU drivers are never bundled.
7. Windows/Linux Qwen summarization remains the explicit deferred functionality gap
   `NATIVE-GAP-01`. `NATIVE-GAP-02` is revised and closed by the additive CUDA contract:
   capability reporting must distinguish verified CUDA use from the always-available CPU
   fallback, and may not infer CUDA from bundled library filenames.
8. macOS distribution is an unsigned bundled application: users perform the
   documented local ad-hoc signing step before first launch. Do not claim a
   Developer ID signature, notarization, stapling, or Gatekeeper acceptance.
   Windows installer upgrades remain signed when signing authority is available.
   An in-application updater is deferred.
9. The first Apple Silicon macOS native release supports macOS 26.2 and later.

## Inspiration and boundaries

Radarr and Sonarr publish self-contained .NET builds for each runtime identifier,
copy the web UI beside the server runtime, wrap macOS output in an application
bundle, and offer a Windows installer. Their application data is outside the
installation directory and can be overridden explicitly. Jellyfin similarly
separates its server, web client, platform packages, and Windows tray controller.

Editarr should copy the following patterns:

- one build per OS and CPU architecture;
- web assets embedded in the installed application;
- no language runtime installed by the user;
- writable application state outside immutable program files;
- a single-instance launcher that opens the existing web UI when invoked again;
- platform installers, documented macOS self-signing, and data-preserving upgrades;
- explicit service/launcher and updater process roles.

Editarr should not initially copy machine-service installation or in-app
self-update. Per-user execution avoids service-account access problems with GPU
drivers, network shares, removable storage, and the user's media library.

Primary references:

- <https://github.com/Radarr/Radarr/blob/develop/build.sh>
- <https://raw.githubusercontent.com/Radarr/Radarr/develop/distribution/osx/Radarr.app/Contents/Info.plist>
- <https://raw.githubusercontent.com/Radarr/Radarr/develop/distribution/windows/setup/radarr.iss>
- <https://github.com/jellyfin/jellyfin-packaging>
- <https://github.com/jellyfin/jellyfin-server-windows>

## Target architecture

```text
Editarr application entry point
|-- single-instance coordination and browser launch
|-- long-lived restart controller
|   `-- one active FastAPI/Uvicorn HTTP generation
`-- one independent scan worker at a time
    |-- FFmpeg helpers
    |-- audio transcription and profanity filters
    `-- visual detectors and optional Qwen enrichment
```

The existing parent-controller/HTTP-child architecture remains authoritative.
The frozen program exposes private internal process roles rather than attempting
to invoke a separately installed Python interpreter. Example roles are:

```text
Editarr --internal-role http-child ...
Editarr --internal-role scan-worker ...
```

These arguments are implementation details and must not appear as normal UI or
operator configuration.

## Native release artifacts

| Platform | Primary artifact | Installed form | First-release behavior |
| --- | --- | --- | --- |
| macOS arm64 | Unsigned DMG | Unsigned `Editarr.app`; end user ad-hoc-signs it locally | User application/agent; opens browser and remains available while running |
| Windows x64 | Authenticode-signed setup EXE | Per-user application under Local AppData Programs | Start-menu entry; optional launch at login; no console window |
| Linux x86_64 | AppImage | One executable AppImage download | Runs in the user session and opens a browser when a desktop session is available |

A portable Linux archive may be emitted for diagnostics after the AppImage is
working, but it is not a substitute for the primary artifact. Debian/RPM
packages and systemd integration are future service-distribution work.

## Native application paths

Resolve paths before importing `scanner.config` or any module that constructs a
model path. Use platform APIs through a small Editarr-owned path resolver (a
direct dependency on `platformdirs` is acceptable) and never derive application
state from the process working directory.

| Purpose | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Durable data: SQLite, deployment state, secrets, replay inputs | `~/Library/Application Support/Editarr` | `%LOCALAPPDATA%\Editarr\Data` | `${XDG_DATA_HOME:-~/.local/share}/editarr` |
| Configuration not stored in SQLite | same Application Support root | `%LOCALAPPDATA%\Editarr\Config` | `${XDG_CONFIG_HOME:-~/.config}/editarr` |
| Logs and diagnostic state | `~/Library/Logs/Editarr` | `%LOCALAPPDATA%\Editarr\Logs` | `${XDG_STATE_HOME:-~/.local/state}/editarr` |
| Downloaded models and regenerable caches | `~/Library/Caches/Editarr` | `%LOCALAPPDATA%\Editarr\Cache` | `${XDG_CACHE_HOME:-~/.cache}/editarr` |
| Per-run temporary frames | OS temporary directory | OS temporary directory | `XDG_RUNTIME_DIR` when usable, otherwise OS temporary directory |

`EDITARR_DATA_DIR` and a `--data-dir` CLI option override the durable data root
for diagnostics and advanced portable use. Existing specific environment
overrides retain their documented precedence. The application must create
directories with user-only permissions where the platform supports them.

Downloaded model weights are cache data: clearing them must never remove scan
history, credentials, settings, replay inputs, or EDL state. The next scan may
download them again.

## Initial model support contract

### macOS Apple Silicon

- faster-whisper and the existing profanity filters;
- mlx-whisper;
- NudeNet;
- Image Safety Gate;
- SigLIP2 moderation heads;
- Qwen3-VL enrichment through MLX;
- CPU, ONNX Core ML, Metal/MPS, and MLX behavior already selected by the model
  implementations, subject to packaged-runtime verification.

### Windows x64 and Linux x86_64

- faster-whisper on CPU, or CUDA when its packaged CTranslate2 runtime successfully
  probes a compatible host NVIDIA device/driver;
- all existing profanity filters;
- NudeNet on CPU;
- Image Safety Gate on CPU;
- SigLIP2 moderation heads on CPU;
- CPU remains supported on machines without NVIDIA hardware or a compatible driver;
- CUDA support is backend-specific; the initial packaged CUDA path is CTranslate2 /
  faster-whisper, while the current ONNX detector stack remains on CPU;
- CUDA runtime/provider libraries may be bundled only when required by an advertised
  backend. NVIDIA GPU drivers remain host-provided;
- no Qwen enrichment in the first release.

The Qwen gap must not make a Windows or Linux visual scan fail. When a detector
flags frames on those platforms, Editarr must retain and return the detector's
findings without Qwen descriptions. The capabilities response and scan UI must
state that Qwen enrichment is unavailable rather than advertising it and
failing at runtime.

Track the following future requirements until complete:

- `NATIVE-GAP-01`: provide a validated local Qwen3-VL backend for Windows and
  Linux, with output checked against the existing labeled/replay corpus;
- `NATIVE-GAP-02` (CLOSED — revised 2026-09-09): Windows/Linux support additive CUDA
  acceleration for a backend only after a runtime probe verifies the packaged provider
  and compatible host NVIDIA device/driver. CPU remains the guaranteed fallback and GPU
  drivers are not shipped;
- `NATIVE-GAP-03`: evaluate Intel macOS, Windows ARM64, and Linux ARM64 artifacts.
- `NATIVE-GAP-04`: evaluate support for an older macOS floor. This must rebuild or
  replace `_internal/mlx/core.cpython-311-darwin.so`, `_internal/mlx/lib/libjaccl.dylib`,
  and `_internal/mlx/lib/libmlx.dylib` rather than merely changing `Info.plist`.
  Before selecting a lower floor, prove every bundled Mach-O deployment load command is
  compatible, then repeat the installed-package self-sign and complete real MLX/Qwen
  model smoke on that lower macOS version.

## Implementation milestones

### Milestone 0: record contracts and acceptance fixtures

1. Accept this plan as the native-distribution contract.
2. Add a machine-readable target/model matrix used by the capability API,
   packaging, tests, and release notes.
3. Select minimum supported OS versions and the oldest supported Linux glibc
   baseline during the packaging spike.
4. Select a small, redistributable media fixture that exercises audio, visual
   detection, Qwen on macOS, cancellation, and EDL export.
5. Define one version source for Python metadata, executable metadata, the
   macOS bundle, Windows installer, AppImage, and API version.

Acceptance: a reviewer can identify every promised artifact and model feature,
including each deliberate gap, from committed files without reading CI logic.

### Milestone 1: platform-independent runtime paths

1. Introduce `RuntimePaths` and resolve it at the earliest process entry point.
2. Make database, secret, deployment, log, replay, frame, export, Hugging Face,
   and model locations derive from it.
3. Remove all production uses of `Path("./data...")` and `Path.cwd()` for
   support files.
4. Keep development defaults available only through an explicit development
   launch context.
5. Add `--data-dir`, a command that prints resolved paths, and UI access to the
   data/log folders where practical.
6. Test read-only and arbitrary working directories, spaces, Unicode, and long
   Windows paths.

Acceptance: the installed application can launch from any working directory and
writes only to documented per-user paths and selected media-sidecar locations.

### Milestone 2: frozen and cross-platform process lifecycle

1. Add an early executable bootstrap and call
   `multiprocessing.freeze_support()` before heavyweight imports.
2. Route private child-process arguments before the public CLI parser.
3. Replace `sys.executable -m scanner.worker` with the frozen internal worker
   role while preserving source-checkout development.
4. Replace `ps`, POSIX-only process groups, and `os.killpg` with a
   cross-platform process abstraction. Store enough process identity to avoid
   signaling a reused PID.
5. Decide and test Windows Job Object ownership so an HTTP restart does not
   kill the active independent scan worker, while an explicit full application
   quit has defined behavior.
6. Send controller, HTTP, worker, and crash diagnostics to rotating files in
   the log directory.
7. Add single-instance coordination. A second normal launch opens the active
   URL and exits without starting another server.
8. Open the browser only after a readiness probe succeeds. Retain
   `--no-browser` and foreground diagnostic modes.

Acceptance: source, frozen macOS, and frozen Windows builds pass the same start,
restart, scan-worker, cancellation, crash-recovery, and second-launch tests.

### Milestone 3: model acquisition and capability correctness

1. Place every model behind an explicit backend/capability interface.
2. Change Qwen enrichment from an assumed visual-scan stage to a declared
   backend capability. Preserve detector findings when it is unavailable.
3. Pin the repository revision and expected files for every downloaded model.
4. Store a model manifest containing backend version, model revision, license
   metadata, and checksums.
5. Provide download progress, cancellation, disk-space checks, actionable
   errors, and an option to remove downloaded model data.
6. Configure Hugging Face and library-specific caches beneath the resolved
   Editarr cache before importing those libraries.
7. Verify that a completed model download permits subsequent offline scans.
8. Audit licenses for bundled FFmpeg/native libraries and downloaded weights.

Acceptance: each platform advertises only working choices; every advertised
model completes a scan; Windows/Linux flagged visual scans succeed without
Qwen; and deleting cache data does not damage durable application state.

### Milestone 4: frozen-runtime viability gate

Use PyInstaller `onedir` as the first candidate. It keeps native libraries on
disk, supports macOS application bundles and code signing, and documents the
required multiprocessing behavior. Add a checked-in spec file and explicit
hooks for dynamic imports, package data, native libraries, frontend assets, and
FFmpeg helpers.

Build macOS arm64, Windows x64, and Linux x86_64 on their target operating
systems. Do not cross-build Python native extensions.

The viability gate must exercise:

- a machine without Python or Node installed;
- startup outside the source checkout;
- setup, login, logout, CSRF, and embedded static assets;
- saved port/base-path restart and failed-listener recovery;
- the full initial model matrix using real local inference;
- cancellation and controller restart during a scan;
- on-demand downloads and offline reuse;
- packaged FFmpeg discovery;
- media and data paths containing spaces and Unicode;
- no writes inside the application bundle/install directory.

If PyInstaller fails a native dependency after a bounded hook/configuration
attempt, compare Nuitka `standalone` against the same acceptance suite. Do not
rewrite the application or select PyOxidizer merely to rescue the spike.

Acceptance: all three unsigned/unnotarized development artifacts pass the
installed-release suite before installer work begins.

### Milestone 5: platform launcher experience and packaging

#### macOS

1. Produce `Editarr.app` with the existing Editarr icon and an Editarr-owned
   reverse-DNS bundle identifier.
2. Run as a user application/agent with a small menu-bar or lifecycle surface
   for Open Editarr, Open Logs, Start/Stop, Launch at Login, and Quit.
3. Do not use App Sandbox; Editarr needs user-authorized access to arbitrary
   local and mounted media paths.
4. Distribute an unsigned DMG containing the bundled app. Document and test the
   end-user local ad-hoc-sign procedure (clear the downloaded quarantine attribute,
   `codesign --force --deep --sign -`, then strict verification). This release does
   not claim Developer ID signing, hardened-runtime coverage, notarization, stapling,
   or Gatekeeper acceptance.

#### Windows

1. Create a per-user Inno Setup installer with no administrator requirement in
   the normal path.
2. Install beneath the user's Local AppData Programs directory and preserve the
   separate data directory on upgrade/uninstall unless the user explicitly
   requests removal.
3. Provide Start-menu shortcuts, optional launch at login, and a tray/lifecycle
   surface matching macOS where feasible.
4. Hide the console for normal launch; retain an explicit diagnostic console
   mode.
5. Authenticode-sign the executable and installer.

#### Linux

1. Wrap the verified one-directory runtime as an AppImage with icon and desktop
   metadata.
2. Open the browser only when a graphical session is available; otherwise log
   and print the URL in foreground mode.
3. Build against the selected oldest supported glibc baseline and test on each
   supported distribution generation.
4. Do not install a systemd unit in the first release.

Acceptance: install/open, second launch, restart, launch-at-login where offered,
quit, upgrade, downgrade rejection, and uninstall all have automated or recorded
platform tests. Application data survives ordinary upgrades and uninstall.

### Milestone 6: release CI and supply-chain controls

1. Build each artifact on a pinned native runner/toolchain from `uv.lock` and
   the frontend lockfile.
2. Build the frontend once from locked inputs and verify its manifest before
   including it in each native artifact.
3. Run unit, integration, browser, installed-release, process-lifecycle, and
   real-model smoke tests on each target artifact.
4. Verify that the macOS package is intentionally unsigned and that the documented
   end-user self-sign procedure passes strict verification; verify Windows signatures
   and AppImage execution after download.
5. Produce SHA-256 checksums, an SBOM, license notices, build metadata, and
   provenance for every release.
6. Publish only the native application artifacts and supporting checksum/
   metadata files. Wheels and source distributions are not release downloads.
7. Keep Windows signing credentials in protected CI environments. Make macOS
   release packages visibly unsigned and include the end-user self-sign instructions;
   distinguish them from any non-distributable signed test builds.

Acceptance: a tagged release produces three installable artifacts whose hashes,
signatures, dependency inventory, target/model capabilities, and test evidence
can be independently inspected.

### Milestone 7: beta and release hardening

1. Test clean install and upgrade on physical machines, not only CI images.
2. Run long scans, sleep/wake, removable-media removal, network-share loss,
   low-disk, model-download interruption, and forced process termination cases.
3. Measure application download size, installed size, first-launch latency,
   steady memory, model-cache size, and CPU scan performance.
4. Verify accessibility of the setup and lifecycle surfaces.
5. Publish troubleshooting instructions for data paths, logs, ports, model
   cache, browser launch, and clean uninstall.

Acceptance: the release candidate completes the supported model matrix and a
24-hour lifecycle soak on each platform with no orphaned server generation,
unrecoverable queue state, or writes to the installation directory.

## Deferred work that must remain tracked

- Windows/Linux Qwen3-VL local inference (`NATIVE-GAP-01`). A future spike
  should first evaluate a PyTorch/Transformers implementation using the existing
  dependency stack, then a bundled llama.cpp-compatible backend if it offers
  materially better CPU/quantized operation. Output quality must be compared
  against the saved Qwen replay corpus before changing backends.
- `NATIVE-GAP-02` is closed under the additive CUDA contract recorded above. Extending
  CUDA to additional backends is ordinary capability work, not permission to advertise
  an unverified provider or to ship NVIDIA drivers.
- Additional CPU architectures (`NATIVE-GAP-03`).
- Older macOS deployment support (`NATIVE-GAP-04`). The first Apple Silicon release
  supports macOS 26.2 and later. Lowering that floor requires rebuilding or replacing the
  MLX objects identified above and rerunning the documented Mach-O, installed-package
  self-sign, and real MLX/Qwen verification; changing the bundle declaration alone is
  not evidence of support.
- Machine-wide Windows service, launchd daemon, and systemd service packages.
- Signed in-app updates with signature verification, external updater process,
  rollback, and channel selection.
- Optional offline model packs generated from the same pinned model manifest.

Open items must appear in the project issue tracker or implementation task index before
the first native release is declared complete. Release notes must repeat
`NATIVE-GAP-01` and `NATIVE-GAP-04` while they remain open, and record
`NATIVE-GAP-02` as closed/revised so CUDA is not mistaken for a universal guarantee.

## Non-goals

- Replacing the browser UI with three new native user interfaces.
- Rewriting the FastAPI, SQLAlchemy, scanning, or model pipeline in Rust, Go,
  Swift, C#, or another language.
- Shipping Python or model packages through pip to end users.
- Bundling downloaded model weights in the initial installers.
- Supporting containers, system services, or unattended multi-user operation as
  part of the first native release.
- Adding a database schema migration solely for packaging. This plan changes
  path and process bootstrap behavior, not the database schema.

## Completion definition

The project is complete when a user can download the one artifact for a
supported platform, complete the documented macOS local self-sign step where
applicable, install or open it without a language runtime, complete first-run
setup in the automatically opened browser, download an advertised model on
demand, complete a local scan, restart Editarr, and retain all durable state in
native per-user locations. Windows and Linux must complete CPU scans
without Qwen enrichment and must describe that limitation before a scan begins. A
compatible CUDA host may accelerate advertised backends, while a host without an NVIDIA
driver must retain the same supported CPU behavior.
