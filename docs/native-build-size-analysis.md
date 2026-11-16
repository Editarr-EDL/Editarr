# Native artifact size investigation

Investigation date: October 24, 2026. The initial investigation below analyzes the
packaging code, recorded release evidence, and installed macOS dependency files.
The implementation section records subsequent work on
`codex/reduce-native-artifact-size`. No models were downloaded.

## What the 2 GB figure means

The [NATIVE-10 handoff](native-application-tasks/10-pyinstaller-runtime.md) records
2,079,038,091 bytes for the macOS frozen runtime. The
[NATIVE-12 handoff](native-application-tasks/12-macos-package.md) records
2,081,611,995 bytes for the app and **747,395,006 bytes for its DMG**: about 2.08 GB
installed versus 747 MB downloaded, using decimal units. The native launcher and
bundle wrapping account for only about 2.6 MB in those recorded figures.

Those historical artifacts are no longer present at their recorded `/private/tmp`
paths. Consequently, the measurements below describe current dependency/build
inputs, not a recovered file-by-file breakdown of that historical artifact. No
new native build or optimized artifact has been certified by this investigation.
Windows and Linux sizes must be measured from their own target-native builds.

There is also an accounting defect: `_tree_size()` in `scripts/build_release.py`
and all three platform packagers sums `path.stat().st_size` for every
`path.is_file()`. Both operations follow file symlinks. A 10-byte file with one
symlink to it is reported as 20 bytes; this was reproduced using the actual build
helper in a temporary directory. macOS/Linux PyInstaller outputs use symlinks,
and their packagers preserve them. The recorded tree totals can therefore count
the same library repeatedly. The amount of historical overstatement is unknown;
the DMG's actual file length does not have this defect.

Report separate metrics for regular-file payload bytes, symlink metadata,
allocated disk space, and compressed download size. Deduplicate hard-linked
regular files by `(st_dev, st_ino)` when reporting unique on-disk payload. Changing
the existing metric needs an explicit definition and matching inventory tests.

## Where the bytes come from

The native app embeds Python, its web/API application, and every supported ML
backend. It is not just a native UI executable. `native_build/pyinstaller_collect.py`
calls `collect_all()` for 25 package families plus three MLX families on macOS.
This requests all submodules, package resources, metadata, and raw Python source.
Transitive imports then add further libraries. A second explicit
`collect_dynamic_libs()` call repeats work already done by `collect_all()`;
PyInstaller normalizes collection entries, so this alone does not prove duplicate
bytes in the output.

Current macOS inputs, measured as regular-file lengths excluding symlinks:

| Dependency | MiB | Reason it is present |
| --- | ---: | --- |
| Torch | 483.6 | Image safety/NSFL, CLIP and SigLIP inference |
| MLX | 194.6 | Qwen and MLX Whisper acceleration |
| llvmlite | 124.5 | Numba used by MLX Whisper word alignment |
| OpenCV | 119.1 | Frame/credits processing and NudeNet |
| SciPy | 78.4 | Scientific/model dependencies, including alignment |
| ONNX Runtime | 69.6 | NudeNet and ONNX image safety inference |
| Transformers | 54.6 | CLIP, SigLIP and MLX model integration |
| imageio-ffmpeg | 47.2 | Bundled FFmpeg executable |
| pandas | 45.1 | Image safety tag CSV loading |
| PyAV | 43.2 | Video/frame/audio decoding |
| scikit-learn | 32.5 | Alternative profanity classifier |
| Numba | 17.9 | MLX Whisper word alignment |

These are overlapping functional families but distinct package directories. They
include source, bytecode caches and tests present in the environment; the release
already prunes tests and development output. Do not sum this table and treat it
as an exact release manifest. Conversely, it omits the interpreter, many smaller
dependencies, the separately staged helpers and libraries resolved outside the
Python environment.

Particularly large individual files are Torch's `libtorch_cpu.dylib` (322.3 MiB),
MLX's `mlx.metallib` (166.7 MiB), and llvmlite's native library (123.5 MiB). These
are runtime engines, rather than downloaded model weights or frontend assets.
`scanner/pipeline/audio_transcription.py` requests word timestamps, and
`mlx_whisper/timing.py` actually uses Numba and SciPy for alignment; llvmlite cannot
simply be excluded while preserving that backend's current behavior.

Managed Hugging Face/Qwen weights remain external downloads. There are small,
intentional package-owned models: NudeNet's `320n.onnx` is 11.6 MiB and the
alternative profanity package includes joblib data. The model manifest explicitly
identifies these as bundled package data. Removing them would break those models
and would not explain the gigabyte-scale total.

Measured environment: Python 3.11, PyInstaller 6.22.2, hooks-contrib 2026.7,
Torch 2.13.0, MLX 0.32.1, mlx-vlm 0.6.16, mlx-whisper 0.4.3,
imageio-ffmpeg 0.6.0, PyAV 18.1.0 and opencv-python-headless 5.0.0.93.

## Reductions, in recommended order

1. **Correct and expand size reporting before setting a target.** Add a directory
   breakdown, largest files, and duplicate-content report to release evidence.
   Keep link-aware installed size separate from compressed installer size. This
   corrects reporting; it does not itself shrink the download.

2. **Keep one FFmpeg executable.** `scripts/build_release.py` copies the executable
   from imageio-ffmpeg into a staged `helpers/ffmpeg`. The spec also broadly collects
   imageio-ffmpeg, whose upstream hook collects its `binaries` directory. The build
   inputs therefore request the same executable at two destinations, from different
   source paths. The current binary is 49,368,728 bytes (47.1 MiB). Verify the final
   output's copies and hashes, then suppress the unused package copy, or use a
   relative alias on platforms that support it. Audit both the explicit collector
   and the upstream hook; changing only one leaves the other collection path.
   The frozen runtime already prefers `helpers/ffmpeg`, but audit dependency calls
   to imageio-ffmpeg and update any manifest/package-data checks before removal.

3. **Remove build-only resources from broad data collection.** The explicit
   collector currently selects 40.2 MiB under `include` trees, mostly Torch headers
   (36.7 MiB) and MLX headers (3.2 MiB). Torch's `bin` directory adds about 7.7 MiB,
   including two 3.8-MiB protobuf compiler executables. Check the selected models
   for extension compilation needs, then exclude unused headers, build metadata
   and compiler tools while retaining notices and licenses. The upstream Torch
   hook already excludes several header/build suffixes; the project's broad
   collector reintroduces them.

4. **Replace blanket collection with package-specific policies.** The explicit
   collector selects 135.8 MiB of Python source as data, after its existing
   test/corpora filter. Some source is necessary: the installed Torch and
   Transformers hooks deliberately use `module_collection_mode = 'pyz+py'` for
   JIT/TorchScript support. Setting `include_py_files=False` globally will not
   remove hook-required source and can break other introspection. Keep source
   where required; let ordinary imports and established hooks handle simpler
   packages. Filter test/development *hidden imports before analysis*, as the
   current data-file filtering/post-build pruning cannot prevent those imports
   from introducing transitive dependencies. Limit dynamic model families only
   after enumerating all selected architectures and lazy processor imports.
   The 135.8 MiB is an audit surface, not a promised saving.

5. **Consider separate backend distributions for a substantial reduction.** A
   core download with ONNX/NudeNet and faster-whisper could omit Torch/timm,
   Transformers families that become unused, and MLX/Numba/llvmlite where their
   backends are not selected. The current macOS Torch + MLX + llvmlite package
   inputs alone total about 803 MiB before dependency sharing and release pruning.
   This is a product/architecture change: ship optional backend packages or
   separately verified workers, preserve the settings/capability experience,
   and define installation, updates, compatibility and integrity verification.
   Existing model-weight downloads do not install Python execution engines.
   A lighter distribution must clearly represent which models it supports.

6. **Treat media-stack consolidation as a later, higher-risk project.** PyAV and
   OpenCV carry different FFmpeg ABIs (`libavcodec` majors 62 and 61 locally),
   while the current Homebrew ffprobe uses major 63 and the standalone FFmpeg is
   another build. Their libraries cannot safely be replaced with arbitrary
   symlinks. A controlled OpenCV/PyAV build sharing compatible libraries, or a
   smaller custom ffprobe build, needs codec/seek/timestamp/credits regression
   coverage. The local ffprobe's complete non-system dependency closure is only
   37.1 MiB across 20 unique resolved files, so it is not the primary cause.
   Replacing pandas CSV loading is another smaller opportunity if pandas becomes
   unreachable from every other retained backend.

The first packaging cleanups offer tens to low hundreds of MiB of potential
installed-size savings, subject to final artifact measurement and inference
verification. They do not establish a sub-1-GB full-featured application target.
Separating heavy engines is the stronger route to a much smaller initial download.

## Changes unlikely to solve the main problem

- Changing the native launcher or trimming web assets cannot remove the ML engines.
- Linux already selects CPU Torch/torchvision wheels, and non-macOS collection
  excludes unrelated NVIDIA/MLX stacks. Do not assume accidental CUDA is the cause.
  CTranslate2 CUDA payloads are an intentional optional feature in that policy.
- Windows already uses solid `lzma2/ultra64`; macOS uses a compressed UDZO DMG;
  Linux uses zstd AppImage compression. Compression experiments affect download
  size, rather than the underlying installed payload or backend requirements.
- `onefile` packaging would still contain the dependencies and need extraction.
  Changing freezer does not remove required native libraries.
- Global stripping/UPX needs per-target loader/signing/inference verification;
  the spec currently disables both. It is not a substitute for fixing collection.

## Verification for an optimization change

Build a same-source baseline and candidate on each target using identical locked
dependencies and helper inputs. Retain link-aware file inventories, duplicate
hashes, archive size and package hashes. Exercise installed startup plus real
inference for every promised backend, including MLX Whisper word timestamps,
Qwen, Torch image models, ONNX models, profanity classifiers, and media operations.
The existing no-weight import smoke alone does not exercise lazy model loading,
runtime source inspection or alignment compilation.

The installed-artifact verifier should also run from copied space/Unicode paths
with restricted PATH, isolated data/cache roots and immutable installation files.
Repeat platform packaging/signature checks after binary changes. No user database
or model cache needs modification to perform these size comparisons.

Relevant implementation: `native_build/editarr.spec`,
`native_build/pyinstaller_collect.py`, `scripts/build_release.py`,
`scripts/package_macos.py`, `scripts/package_windows.py`,
`scripts/package_linux.py` and `scanner/runtime/runtime_smoke.py`.
Collection semantics and source-preservation behavior were checked against the
installed hooks and the [official PyInstaller hook documentation](https://pyinstaller.org/en/latest/hooks.html).

## First implementation

The branch retains all supported execution engines and raw Python source required
by their hooks. Its first changes are:

- Shared link-aware accounting in `native_build/artifact_size.py`, used by the
  runtime builder and all platform packagers. Existing tree-size fields now count
  regular file paths without dereferencing links, identified explicitly by the
  additive `size_accounting` field. Runtime inventories include per-package size
  breakdowns and stabilize their own final byte count. Hard links are counted once
  per regular path in this portable payload metric; the separate audit also
  reports unique inode and allocated-file bytes.
- A project imageio-ffmpeg hook omits its second FFmpeg executable. An early runtime
  hook directs imageio-ffmpeg and its transitive consumers to the inventoried
  `helpers/ffmpeg` (`ffmpeg.exe` on Windows), preserving explicit operator overrides.
  A missing bundled helper fails instead of falling back to a host executable.
  The frozen smoke now checks that imageio and the application resolve the same
  executable. The model manifest's package patterns describe the upstream wheel;
  its bundled-artifact entries are provenance declarations, not native layout
  validators. The frozen helper's actual path/hash remains in the runtime inventory.
- Explicit collection excludes Torch's header tree and protobuf compiler binaries.
  MLX headers remain until their runtime compilation requirements are verified.
- Explicit submodule discovery filters test/corpora namespaces before analysis,
  and the redundant second native-library collection call is removed.
- Runtime content pruning moved into `native_build/runtime_content.py` to keep the
  release entry script below 500 lines. Pruning unlinks excluded directory aliases
  without following them.

To inspect a built runtime or an application bundle from the checkout:

```console
.venv/bin/python -m native_build.artifact_size /path/to/Editarr --duplicates > size-audit.json
```

The audit hashes only same-sized regular files of at least 1 MiB and distinguishes
real copies from symlinks and hard links. Download size remains the actual length
of the DMG, installer, or AppImage file.

### Measured macOS comparison

Both native arm64 freezes completed with the same installed dependency environment,
validated frontend, helper inputs and scanner source, comparing the original
collection spec/policy against this branch's policy. Both received the same final
runtime-content pruning and new inventory writer before measurement.

| Metric | Original policy | New policy |
| --- | ---: | ---: |
| Regular-file payload bytes | 1,567,382,467 | 1,470,231,280 |
| Regular-file count | 20,809 | 10,773 |
| Symlink count | 137 | 137 |
| Old dereferencing size calculation | 2,086,279,795 | 1,989,128,608 |

The actual payload saving is **97,151,187 bytes (92.65 MiB, 6.20%)**. The main
directory deltas are imageio-ffmpeg (49,081,856 bytes), Torch (38,461,062 bytes),
and the frozen executable/code archive (9,136,912 bytes). The final candidate has
one inventoried FFmpeg helper, no imageio package binary, and no Torch include tree.

The old accounting overstates each build by **518,897,328 bytes** through repeated
symlink targets. That correction must not be presented as an additional download
or payload reduction. This run did not regenerate DMGs or installers, so it makes
no measured compressed-download-size claim.

Local evidence is under `/private/tmp/editarr-size-reduction/`:

- `baseline/Editarr` and `candidate/Editarr`: generated frozen runtimes, each with
  its own size-accounted runtime inventory.
- `baseline-size-audit.json` and `candidate-size-audit.json`: full breakdowns,
  largest files, link accounting and duplicate-content hashes.
- `baseline-inputs/native_build` and `candidate-inputs/native_build`: captured
  collection inputs. Their root/frontend constants point to the shared checkout
  and isolated frontend input; the candidate's project hooks live in the checkout.
- `baseline-native.log` and `candidate-native.log`: successful target-native freeze
  logs; `tests.log` records 121 passing focused tests, including all three
  platform packaging suites, runtime/bootstrap checks, installed-verifier tests,
  size-accounting regressions, and generated-video FFmpeg routing.

The normal build wrapper initially stopped at pnpm's dependency-directory purge
confirmation. For this local comparison, the existing frontend tools were invoked
directly (`tsc -b` and `vite build`), the output passed the release frontend
validator, the pinned Go backup helper was built, and PyInstaller froze the two
captured specs against those shared inputs. This is dependency-reuse evidence,
not a fresh clean-install or release-certification claim. Sandboxed freeze attempts
were stopped when MLX discovery could not access Metal; the successful freezes
ran with host Metal access and did not have that omission.

Both generated runtimes passed the no-download frozen import/helper smoke with
isolated HOME/data/cache roots and a system-only PATH, plus encrypted backup
helper round trips. The original-policy runtime received an explicit
`IMAGEIO_FFMPEG_EXE` pointing to its inventoried helper for the newly strengthened
smoke; the candidate passed using its automatic runtime hook with no such override.
The candidate log ends with `Editarr frozen runtime smoke passed`. The generated
video routing regression also passed real frame counting through the copied helper.

The Windows/Linux native-package workflows now include the new accounting and
collection tests. Their target-native execution, compressed installer/DMG sizes,
real downloaded-model inference, and full installed-app lifecycle acceptance
remain unverified by this macOS comparison. The next packaging change should use
these inventories as its baseline and retain those release gates.
