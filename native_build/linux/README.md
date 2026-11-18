# Linux AppImage package

The workflow builds the raw runtime on Linux x86_64 against glibc 2.28, then packages
it with the pinned AppImage tooling listed below. The baseline build runs in
Rocky Linux 8.10 image
`rockylinux/rockylinux@sha256:e8a49c5403b687db05d4d67333fa45808fbe74f36e683cec7abb1f7d0f2338c6`:

See `build_package_ci.sh` for the baseline recipe, including toolchain setup,
source-built media, runtime verification, and AppImage assembly.

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

- `appimagetool` 1.9.1, commit `8c8c91f762b412a19f4e8d2c4b35afb98f2d7c81`, SHA-256
  `ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0`
- AppImage type-2 runtime 20251108, commit `dd6cebedcbddde9c82f89b011e8e1d40b6e43868`, SHA-256
  `2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d`

The public release job builds FFmpeg, ffprobe, PyAV, and OpenCV from pinned
source inputs. It freezes the requested release revision, checks for the correct
version and source commit, verifies the runtime and AppDir, and exercises the
AppImage on the Rocky baseline and the Ubuntu runner. It bundles the license
notices and retains the versioned AppImage, notices, and source archive for
publication by the release workflow.

The package gate allows only CTranslate2-owned CUDA runtime libraries. It rejects MLX,
NVIDIA Python/Torch stacks, and GPU drivers such as `libcuda.so.1`; NVIDIA drivers always
remain host-provided.

It creates `Editarr-<version>-x86_64.AppImage`, a matching SHA-256 file, a package
inventory, and a disposable review AppDir. The AppImage installs no systemd unit and
never writes `/etc` or `/var`; durable and cache data stay in the documented XDG roots.

The workflow checks FUSE where the runner provides it, `--appimage-extract-and-run`,
explicit extraction plus `AppRun`, headless URL logging, graphical browser launch
under Xvfb, second launch, Quit, arbitrary CWD, and isolated XDG overrides. The
manual evidence workflow includes a test-only marker for the deterministic
lifecycle fixtures, then runs real scans with that adapter disabled:

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
fresh inference with the cached weights. These checks cover execution and offline
reuse with default settings on CPU. GitHub Actions retains successful development
AppImages for seven days as marked test builds. Release AppImages omit the marker.
