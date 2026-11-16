# NATIVE-13 — Package and sign Windows

Status: **BLOCKED — NEEDS REVISIT (native Windows confirmation pending)**

Dependencies: NATIVE-11. Required host: Windows x64. Primary scope: native frozen build,
per-user Inno Setup installer, tray/lifecycle integration, Authenticode, and Windows tests.

## Background

Windows must not inherit the POSIX assumptions hidden by development on macOS. The normal
app has no console, installs without administrator rights, stores data separately, and
runs in the logged-in user's security context so local/network media access matches that
user. A machine service and firewall-wide listener are not first-release features.

## Instructions

1. Recreate the locked environment and run the shared PyInstaller build natively on
   Windows x64. Add only platform-specific collector/config files; return shared spec
   defects to NATIVE-10 rather than forking behavior silently.
2. Prove native imports/helpers and run NATIVE-11 before installer work. The output must
   include no MLX, model weights, development data, external Python, GPU drivers, or
   unrelated GPU stacks. The target-native CTranslate2 CUDA runtime is intentional.
3. Create a per-user Inno Setup installer under `%LOCALAPPDATA%\Programs\Editarr` with
   Start-menu/uninstall entries, optional launch-at-login, upgrade handling, and the
   approved Editarr icon/version/publisher identity. Do not request elevation normally.
4. Provide a tray/lifecycle surface using NATIVE-06 for Open Editarr, Open Logs,
   status/start/stop, launch-at-login, and Quit. Normal launch has no console; include an
   explicit diagnostic/foreground command or shortcut.
5. Do not add a Windows service, broad firewall exception, ProgramData state, or service
   account. Keep default binding loopback and preserve explicit advanced bind behavior.
6. Sign the app and installer with the authorized Authenticode certificate/timestamp
   service and verify signatures. If unavailable, finish unsigned development tests and
   mark signing `BLOCKED`.
7. On a clean Windows user, test install, launch/relaunch, path Unicode/spaces/length,
   CPU models, download interruption/offline reuse, restart, Quit during scan, login
   startup, upgrade, uninstall, and durable-data preservation.

## Fixed decisions

- Windows x64, per-user, with guaranteed CPU support and optional CTranslate2 CUDA when a
  compatible host NVIDIA GPU/driver passes runtime probing. Qwen remains unavailable;
  GPU drivers are not bundled.
- Installer is Inno Setup unless a blocker requiring a parent-plan change is approved.
- Ordinary uninstall preserves durable data; model-cache removal requires an explicit
  user choice and must never imply durable-data removal.

## Acceptance criteria

- [ ] Frozen runtime and installer work on Windows with no Python/Node installed.
- [ ] Normal install/launch needs no elevation and shows no console.
- [ ] Tray, singleton, readiness, browser, restart, and Quit behavior pass.
- [ ] All advertised CPU scans succeed; CUDA is advertised/used only after a successful
      runtime/driver probe and CPU fallback works without NVIDIA; Qwen remains visible.
- [ ] Authenticode verification passes for executable and installer.
- [ ] Upgrade/uninstall preserve durable data by default.

## Definition of done

Windows lint/tests, NATIVE-11 verifier, real CPU model smoke, installer lifecycle tests,
signature verification, and `git diff --check` pass on Windows x64. Missing Windows host
or signing authority makes the relevant completion `BLOCKED`, never inferred from Wine.

## Handoff requirements

Record Windows/build versions, PyInstaller command, installer options and product IDs,
install/data/cache paths, tray technology, signatures/timestamp result, artifact hash/
size, verifier/model/lifecycle evidence, and exact unresolved blocker.

## Handoff record

```text
Status: BLOCKED — NEEDS REVISIT
Last completed substep: Applied the user's 2026-09-09 product decision that Windows and
Linux guarantee CPU and may additionally use CUDA, closed/revised `NATIVE-GAP-02`, and
completed the authorized bounded Windows repair cycle. Final native run `34379530289`
(job `102560651285`, commit `621beb6`) passed Ruff, all 106 focused tests, the refined
native-binary payload audit, the native x64 PyInstaller build, and frozen startup smoke.
Its raw NATIVE-11 verifier then exercised first-run readiness, bundled UI, setup/auth/CSRF,
Base URL and saved-port restarts, failed-bind recovery, singleton behavior, packaged
process roles, and runtime immutability before failing during final teardown validation:
`scripts/verify_installed_release.py` evaluated `signal.SIGKILL` on Windows, where that
attribute does not exist. Commit `535e4b8` subsequently made accepted shutdown return
codes platform-specific and added a regression test that replaces `signal` with an object
having no POSIX constants; the 2026-09-12 audit passed that focused portable test. This has
not been rerun on a Windows host. Per the bounded Windows-cycle instruction, no fourth
repair run was started. Runs `34377972322` and `34378301789` respectively exposed and
received the path-neutral assertion correction and the native-binary-only payload-audit
correction.
Earlier run `34306250232` remains valid through its x64 PyInstaller build and frozen
startup smoke; its stop on `_internal\\ctranslate2\\cudnn64_9.dll` is superseded.
Files changed: `.github/workflows/native13-windows-package.yml`;
`native_build/windows/EditarrLauncher.cpp`; `native_build/windows/Editarr.iss`;
`native_build/windows/README.md`; `scripts/package_windows.py`;
`scripts/verify_installed_release.py`; shared Windows corrections in
`scripts/build_release.py`; `native_build/pyinstaller_collect.py`;
`scanner/capabilities.py`; `scanner/models.py`; `scanner/pipeline/audio.py`;
`web/src/api.ts`; `web/src/App.tsx`; `tests/test_model_capabilities.py`;
`tests/test_audio_progress.py`; `tests/test_windows_package.py`;
`tests/test_installed_artifact_verifier.py`; `tests/test_native_runtime_build.py`; this
handoff, parent plan, task-pack README, prerequisite/downstream task contracts, and feature
design specification.
Verification run and result: On macOS, Ruff passed all changed Python files and the exact
expanded Windows workflow subset passed 106 tests. Web unit tests passed 124 tests and the
production frontend build passed (36 modules). Ruby parsed the workflow YAML and
`git diff --check` passed. Native run `34379530289` used Python 3.11.9, uv 0.12.5, Node
24, and locked pnpm 11.22.0; Ruff passed and its JUnit evidence records 106 passed, zero
failed/errors/skipped in 11.344 seconds. The native PyInstaller build and frozen startup
smoke passed. The raw verifier ran 89.625 seconds and emitted only the Windows-unsupported
`signal.SIGKILL` failure. Earlier run `34378301789` passed the same 106 Windows-host tests
and native build/smoke before the now-corrected overbroad payload audit; run `34306250232`
passed its then-current 54 tests and the same native build/smoke path.
Artifact/host evidence: GitHub `windows-2025` x64 runner, run `34379530289` from
2026-09-09 16:53:21Z through 17:04:52Z; FFprobe
`9.0.1-essentials_build-www.gyan.dev`, built with GCC 16.1.0, installed from the pinned
Chocolatey `ffmpeg` 9.0.1 package and selected from the real package tree rather than its
shim. A native Windows onedir runtime was produced, passed the narrowed payload gate, and
started without external Python or Node. The verifier reported artifact SHA-256
`a6a2501ff0e3a26c783f60bc8b759d036b91cdb2ff999bf4b9436b0932fec228`, inventory SHA-256
`0dcb7284f1eca30441259f32dbb46a079cfff1016fc36800423c76b8c53152ff`, target
`windows-x64`, version `0.1.0`, and source revision
`621beb655639f8e1b8731dc6178ecd58816d910b`. Bounded artifact
`native13-windows-x64-evidence-34379530289` (2,871 compressed bytes; retained through
2026-09-23) contains the 15,006-byte passing JUnit XML; the verifier did not write its JSON
after failure. No installer was produced. No Wine or macOS inference is used as Windows
evidence.
Known failures or risks: The original immediate verifier blocker at
`{-signal.SIGTERM, -signal.SIGKILL}` is corrected in commit `535e4b8`, but it has only
portable regression coverage: `UV_CACHE_DIR=/private/tmp/samwise-native13-audit-uv-cache
uv run pytest -q tests/test_installed_artifact_verifier.py tests/test_native_runtime_build.py
tests/test_windows_package.py` passed 60 tests on 2026-09-12. A native Windows rerun is
still required before any raw/verifier or installer claim can advance. The revised payload
policy allows only CUDA runtime DLLs under
`_internal\\ctranslate2` (`cudnn`, `cublas`, `cudart`, `nvrtc`, or `nvjitlink` families),
rejects MLX/unrelated GPU stacks, and rejects `nvcuda.dll` so the GPU driver stays
host-provided. Runtime CUDA reporting requires CTranslate2 to prove both a visible device
and supported CUDA compute types; every error/absence falls back to CPU. The current ONNX
detectors remain CPU. The C++ tray launcher and Inno definition remain uncompiled and
unexecuted because raw NATIVE-11 did not return success. Installed NATIVE-11, unsigned
installer install/launch/relaunch/upgrade/uninstall/data preservation, clean-user path
coverage, a real CPU scan, real CUDA-host inference/fallback evidence, download
interruption/offline reuse, Quit during scan, and production Authenticode are unproved.
There is no production Publisher, certificate, or timestamp authority.
Next exact action: In one new native Windows revisit, rerun
`.github/workflows/native13-windows-package.yml` at or after `535e4b8`, beginning with raw
NATIVE-11 and continuing through the explicitly unsigned installer lifecycle only if the
raw verifier succeeds. Confirm the platform-specific teardown regression in the retained
raw evidence. Do not require or bundle an NVIDIA driver; a CPU-only runner must remain a
successful baseline.
Next eligible task: NATIVE-13 is set aside under the bounded Windows-cycle rule. The parent
may continue independent NATIVE-14 Linux work, but NATIVE-15 remains blocked by
NATIVE-12 through NATIVE-14.
Decisions or deviations from the parent plan: Stable Inno AppId is
`{B1956A76-50E9-49E4-9A12-03683D04AB64}`. The user approved an explicitly unsigned
development mode with neutral metadata (`Unsigned development build - no publisher
identity`) and visibly suffixed artifact name; production mode fails closed without a
publisher, certificate, and HTTPS timestamp URL. The launcher is a statically linked Win32
`Shell_NotifyIconW` shell around the console runtime; normal launches use
`CREATE_NO_WINDOW`, diagnostics use `Runtime\\Editarr.exe --foreground`, Launch at Login is
HKCU Run, and ordinary uninstall preserves `%LOCALAPPDATA%\\Editarr`. No schema, migration,
data wipe, service, firewall exception, ProgramData state, cross-build, or Wine evidence
was introduced. `NATIVE-GAP-02` is closed/revised: CPU is guaranteed and CTranslate2 CUDA
is optional when the packaged runtime and compatible host NVIDIA driver pass detection.
User approval required: None for the already approved unsigned development path. The user
confirmed there is no production Windows Publisher display name or Authenticode
certificate, so production signing remains intentionally BLOCKED; a future production
release would additionally require an RFC 3161 HTTPS timestamp authority.
```
