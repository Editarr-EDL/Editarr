# Linux AppImage package

Build the raw runtime on native Linux x86_64 against glibc 2.28, then package it with
the pinned AppImage tooling listed below. The baseline build runs in
Rocky Linux 8.10 image
`rockylinux/rockylinux@sha256:e8a49c5403b687db05d4d67333fa45808fbe74f36e683cec7abb1f7d0f2338c6`:

```console
uv run python scripts/build_release.py --target linux-x86_64 \
  --out-dir "$RUNTIME_OUTPUT" --work-dir "$BUILD_TEMP" \
  --ffprobe-helper /usr/bin/ffprobe
uv run python scripts/package_linux.py --runtime "$RUNTIME_OUTPUT/Editarr" \
  --out-dir "$PACKAGE_OUTPUT" --work-dir "$BUILD_TEMP" \
  --appimagetool "$APPIMAGETOOL" --appimage-runtime "$APPIMAGE_RUNTIME"
```

The supported baseline is glibc 2.28 on x86_64 (represented by maintained Rocky
Linux 8). Current locked Python 3.11 Linux wheels for CTranslate2, ONNX Runtime, PyAV,
PyTorch CPU, torchvision CPU, SciPy, scikit-learn, and tiktoken are tagged for glibc 2.28;
the package script additionally rejects any final ELF payload requiring a newer GLIBC
symbol. PyTorch/torchvision come from the explicit CPU wheel index so an unrelated Torch
CUDA stack is not bundled. CTranslate2 remains the only optional CUDA backend: runtime
probing must prove a compatible NVIDIA device/driver before it is advertised, and CPU is
the supported fallback.

The universal lock requires Linux x86_64 wheels explicitly, including when regenerated
on macOS. The paired Linux packages are `torch==2.9.1+cpu` and
`torchvision==0.24.1+cpu`; upstream's unsuffixed torchvision 0.24.1 Linux wheels are
ARM64-only. The baseline build also installs `procps-ng` for verifier process inspection
and `file` for appimagetool's architecture inspection.

The pinned upstream inputs are:

- `appimagetool` commit `8c8c91f762b412a19f4e8d2c4b35afb98f2d7c81`, SHA-256
  `a6d71e2b6cd66f8e8d16c37ad164658985e0cf5fcaa950c90a482890cb9d13e0`;
- AppImage type-2 runtime commit `75849dc`, SHA-256
  `1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf`.

The package gate allows only CTranslate2-owned CUDA runtime libraries. It rejects MLX,
NVIDIA Python/Torch stacks, and GPU drivers such as `libcuda.so.1`; NVIDIA drivers always
remain host-provided.

It creates `Editarr-<version>-x86_64.AppImage`, a matching SHA-256 file, a package
inventory, and a disposable review AppDir. The AppImage installs no systemd unit and
never writes `/etc` or `/var`; durable and cache data stay in the documented XDG roots.

The Linux package workflow builds and runs the raw runtime and AppDir on
the baseline, then checks the same package payload on Ubuntu 24.04. It separately covers
normal FUSE execution, `--appimage-extract-and-run`, explicit extraction plus `AppRun`,
headless URL logging, graphical browser launch under Xvfb, second launch, Quit, arbitrary
CWD, and isolated XDG overrides. The fake-model marker in that workflow is lifecycle-only;
it cannot satisfy the required real CPU/CUDA model evidence. The workflow separately
runs real scans with that adapter disabled through the AppImage as an ordinary user:

```console
python -m scripts.verify_linux_models \
  --runtime "$PACKAGE_OUTPUT/Editarr.AppDir/usr/lib/editarr" \
  --appimage "$PACKAGE_OUTPUT/Editarr-0.1.0-x86_64.AppImage" \
  --mode extract --json-out "$EVIDENCE_OUTPUT/linux-real-models.json"
```

Use `--mode fuse` on a host with FUSE. The harness uses isolated state and generated
colour/tone media to exercise Whisper, all three audio filter loaders, and each advertised
detector with its default configuration. It restarts with the managed downloader's HTTP
proxies blocked and HF offline mode enabled, then uses different media content to force
fresh inference with the cached weights. These checks cover execution and offline reuse with default settings on CPU. The workflow retains a successful development
AppImage privately in CI for seven days; it is a marked test build, not a release.
