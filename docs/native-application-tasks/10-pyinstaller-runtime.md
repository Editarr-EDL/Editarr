# NATIVE-10 — Create the PyInstaller runtime build

Status: **DONE**

Dependencies: NATIVE-06 and NATIVE-09. Primary scope: locked freezer dependency,
checked-in spec/hooks, frontend staging reuse, artifact manifest, and macOS arm64
one-directory runtime proof. Platform installers belong to NATIVE-12–14.

## Background

The current release script validates and embeds the Vite output, then builds wheel/sdist
artifacts. Native releases should reuse the locked frontend and validation but freeze an
interpreter, Python modules, native libraries, and helpers. The 1.7-GiB development
environment contains large Torch, MLX, ONNX, OpenCV, PyAV, and FFmpeg payloads; implicit
import discovery is insufficient because model code uses dynamic imports.

## Instructions

1. Add and lock PyInstaller as a development/release build dependency. Do not make it a
   runtime/end-user dependency.
2. Add a checked-in `.spec` and focused hooks/collection helpers. Explicitly include the
   validated frontend, model-package metadata/resources required before weight download,
   native extensions/libraries, CA certificates, and the resolved FFmpeg/ffprobe helpers.
   Explicitly exclude tests, development corpora, local data, caches, downloaded weights,
   credentials, and the retired native prototype.
3. Produce a PyInstaller `onedir` runtime. Do not enable one-file extraction.
4. Define target-specific dependency inputs so macOS arm64 includes MLX stacks while
   Windows/Linux retain only intentional accelerator payloads. CTranslate2 CUDA runtime
   libraries are allowed for optional faster-whisper acceleration; unrelated MLX/generic
   GPU stacks and all GPU drivers remain excluded.
5. Extend/replace `scripts/build_release.py` with a reproducible native-runtime command
   that retains frontend validation and cleans staging on success/failure. Wheel/sdist
   may remain an internal developer command but are not the native release output.
6. Generate an artifact inventory with version, target, source revision, frontend hashes,
   included packages/helpers, exclusions, and total size. Fail if a managed data/model
   cache path was captured.
7. Build on macOS Apple Silicon in a clean environment and run import/startup smokes for
   FastAPI, Uvicorn, SQLite, cryptography, FFmpeg, ONNX, Torch, Transformers, MLX, and all
   dynamic model modules without downloading weights.
8. If PyInstaller cannot package a required dependency, make one bounded hook/config
   attempt and record the precise loader/import/signing failure. Only then may this task
   compare Nuitka against the same smoke; changing freezer requires user approval and an
   update to the parent plan.

## Fixed decisions

- PyInstaller `onedir`, not `onefile`, is the first candidate.
- Model weights are excluded and downloaded later to the native cache.
- Builds occur natively on each target OS; NATIVE-13/14 own Windows/Linux proof.
- No application rewrite or PyOxidizer fallback.

## Acceptance criteria

- [x] A clean, deterministic command produces the macOS arm64 one-directory runtime.
- [x] It starts with no external Python, Node, source checkout, or writable CWD.
- [x] All promised macOS runtime modules/helpers import or execute without weight download.
- [x] Frontend validation/hashes are preserved and no local data/weights enter the output.
- [x] Artifact inventory and size are recorded.

## Definition of done

Build-helper tests, release-asset tests, focused frozen smoke, Ruff, and
`git diff --check` pass. Generated output is not committed. The checked-in spec/hooks are
stable for platform packaging agents. A failed required import makes the task `BLOCKED`.

## Handoff requirements

Record exact build command/tool versions, spec/hook files, collected/excluded resources,
artifact path/hash/size (without committing it), import/startup smoke results, warnings,
and Windows/Linux target instructions for NATIVE-13/14.

## Handoff record

```text
Status: DONE
Last completed substep: Added locked PyInstaller inputs; target-aware collection/spec policy;
frontend/helper staging; immutable resource, cache, and artifact-inventory checks; and a
no-weight frozen smoke. Built and ran the macOS arm64 onedir artifact.
Files changed: pyproject.toml; uv.lock; scanner/entry.py; scanner/runtime_smoke.py;
scripts/build_release.py; native_build/editarr.spec; native_build/pyinstaller_collect.py;
tests/test_native_runtime_build.py; docs/native-application-tasks/README.md;
docs/native-application-tasks/10-pyinstaller-runtime.md.
Verification run and result: `uv run ruff check scanner/entry.py scanner/runtime_smoke.py
native_build scripts/build_release.py tests/test_native_runtime_build.py` passed. `uv run
pytest -q tests/test_native_runtime_build.py tests/test_frozen_bootstrap.py
tests/test_runtime_paths.py` passed (31 tests). `git diff --check` passed. The reproducible
native command was `uv run python scripts/build_release.py --out-dir
/private/tmp/editarr-native10-review-final --work-dir /private/tmp --ffprobe-helper
/opt/homebrew/Cellar/ffmpeg/9.0.1/bin/ffprobe`; it completed with PyInstaller 6.22.2,
Python 3.11.16, pnpm 11.22.0, and no model download. It rebuilt/validated the frontend,
then ran the frozen import/helper smoke with only `/usr/bin:/bin` on PATH. A second run
from a mode-0555 temporary CWD whose name contains a space and Unicode also passed with
isolated runtime roots and no checkout/venv/Node PATH. The sandboxed variant cannot
initialize Metal, but the same host-device run passed.
Artifact/host evidence: `/private/tmp/editarr-native10-review-final/Editarr` is an unsigned
macOS 27.0 arm64 PyInstaller onedir runtime (generated, uncommitted), 2,079,038,091 bytes.
Its executable is Mach-O arm64, SHA-256
`3f0b66ba6edb96cd540405b41499082fd01f573e945c564ca0ff8567455b21d4`.
Its checked `editarr-runtime-inventory.json` records the final helpers, frontend hashes,
package versions, source revision, exclusions, and total. The inventory identifies
`_internal` as the resource root and verifies bundled FFmpeg and ffprobe hashes. It
includes the validated Vite assets, `scanner/model-manifest-v1.json`, certifi CA data,
required dynamic packages/native libraries, and target-native helpers. The checked
clean-runtime audit confirms no managed output root or declared development modules;
no downloaded weights, local credentials, development data, cache, corpora, retired
prototype, or source checkout entered the runtime.
Known failures or risks: PyInstaller analysis warns about absent optional `onnx`,
matplotlib, TensorBoard, and non-macOS CUDA/Windows/Linux ctypes candidates; the frozen
smoke imports FastAPI, Uvicorn, SQLite, cryptography, ONNX Runtime, Torch, Transformers,
all model pipeline modules, MLX/MLX-VLM/MLX-Whisper, and executes both helpers successfully
without weights. Both PyAV and OpenCV package FFmpeg libraries and macOS reports duplicate
Objective-C receiver-class warnings during analysis; no frozen smoke failure resulted.
NATIVE-15 owns real model inference/acquisition and release licensing; NATIVE-11 owns the
portable copied-installed-artifact suite.
Next exact action: NATIVE-11 should run its black-box verifier against a copied macOS
runtime, then NATIVE-12 may wrap the same shared runtime as Editarr.app. NATIVE-13/14 must
run this exact build command natively with their own regular target `ffprobe` helper; the
spec excludes MLX and unrelated GPU inputs while retaining the target-native CTranslate2
CUDA runtime for optional acceleration and guaranteed CPU fallback.
Next eligible task: NATIVE-11.
Decisions or deviations from the parent plan: PyInstaller onedir remains the selected
freezer; no Nuitka comparison was needed. The one collection correction mapped `cv2` to
the `opencv-python-headless` distribution when generating inventory metadata. PyInstaller
uses a disposable `PYINSTALLER_CONFIG_DIR` beneath the temporary build root rather than a
developer-home cache. macOS code signing may change helper bytes, so the inventory hashes
the collected `_internal/helpers` artifacts rather than their build-time sources.
User approval required: None.
```

### Post-handoff shared Windows layout correction

NATIVE-13 found that the otherwise stable native-build command did not account for the
Windows PyInstaller entrypoint filename. The shared layout contract now resolves exactly
one target-native entrypoint before the frozen smoke: `Editarr.exe` for `windows-x64` and
`Editarr` for macOS/Linux raw runtimes. It also stages and inventories Windows FFmpeg and
ffprobe helpers as `.exe`, matching the frozen runtime resolver. Missing, wrong-target,
symlinked/non-executable, or ambiguous `Editarr`/`Editarr.exe` candidates fail closed.
The build CLI and macOS evidence above are unchanged; no Windows artifact was built by
this correction. NATIVE-13 must run the same command natively on Windows with its
target-native `ffprobe` helper.
