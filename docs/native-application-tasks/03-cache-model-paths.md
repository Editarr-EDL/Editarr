# NATIVE-03 — Move models, caches, frames, and helpers to runtime paths

Status: **DONE**

Dependencies: NATIVE-02. Primary scope: regenerable model/cache/temp paths, FFmpeg
resolution abstraction, cleanup semantics, and focused pipeline tests.

## Background

Qwen, Image Safety Gate, and SigLIP2 define their own `./data/models` roots. Frame and
pipeline caches also assume checkout-relative paths, while `imageio-ffmpeg` locates an
embedded helper indirectly. Frozen artifacts need one early cache policy and an explicit
helper lookup that never writes into the application bundle.

## Instructions

1. Inventory all model, Hugging Face, frame, scene, preview, pipeline cache, and external
   helper paths. Include native library environment variables that affect downloads.
2. Derive all model/download roots from `RuntimePaths.model_cache`. Configure Hugging
   Face/Transformers caches before importing those libraries; do not override an
   explicit supported operator setting silently.
3. Put disposable extraction/work directories under the runtime/temp root with unique
   per-scan ownership and bounded cleanup. Preserve any files needed for restart-tolerant
   checkpoints; do not delete another worker's directory.
4. Keep retained Qwen replay inputs in the durable location established by NATIVE-02.
5. Add one application helper resolver for FFmpeg/ffprobe that supports source installs
   and future frozen bundle resources. Keep command construction argument-safe.
6. Add tests proving cache deletion leaves durable data intact, model roots are
   platform-correct, concurrent scans cannot collide, stale temp cleanup is safe, and
   operation is independent of CWD.
7. Remove production `Path("./data...")` model roots and equivalent CWD assumptions.

## Fixed decisions

- Model weights are on-demand cache content, not durable data and not installer payload.
- NATIVE-08 owns model revisions/manifests/download state; do not redesign downloads here.
- NATIVE-10 owns actually collecting FFmpeg into frozen artifacts.

## Acceptance criteria

- [x] `rg` finds no production model/support path rooted in `./data` or `Path.cwd()`.
- [x] Cache/model environment is established before dynamic model imports.
- [x] Temp cleanup is ownership-safe and does not break scan resume semantics.
- [x] Source-checkout FFmpeg behavior remains functional through the new resolver.
- [x] Durable state survives cache and stale-temp cleanup tests.

## Definition of done

Focused pipeline/cache/frame/audio/model tests and Ruff pass, `git diff --check` passes,
and the handoff gives NATIVE-04/07/08/10 stable path/helper interfaces.

## Handoff requirements

Record final path APIs, environment variables, cleanup lifecycle, helper resolver,
inventory/search evidence, tests, and any package that still performs an unavoidable
external cache write.

## Handoff record

```text
Status: DONE
Last completed substep: Repaired the reopened workspace ownership defect: invalid scan options now fail before allocation, and marker-write allocation failure removes its own just-created directory.
Files changed: scanner/runtime_paths.py; scanner/runtime_support.py; scanner/runtime_workspaces.py; scanner/config.py; scanner/pipeline/cache.py; scanner/pipeline/runner.py; scanner/pipeline/frames.py; scanner/pipeline/scenes.py; scanner/pipeline/audio.py; scanner/pipeline/qwen_summary.py; scanner/pipeline/image_safety_gate.py; scanner/pipeline/siglip2_moderation.py; scanner/mute_preview.py; scanner/api.py; tests/test_cache_runtime_paths.py; tests/test_runtime_paths.py; tests/test_durable_runtime_paths.py; tests/test_frames.py; tests/test_scene_progress.py; tests/test_visual_models.py; tests/test_qwen_summary.py; tests/test_editarr_integration.py.
Verification run and result: `uv run ruff check scanner tests` passed. Initial NATIVE-03 final isolated suite passed 111 tests in 1.16s, with 5 existing FastAPI/Starlette deprecation warnings; NATIVE-02 owner separately ran Ruff and full suite: 463 passed. After reopening, the focused workspace regression suite (`test_runner_cache.py`, `test_runner_speed_up.py`, cache/runtime, frame, scene, audio, visual-model, and Qwen tests) passed 86 tests in 1.00s. `git diff --check` passed. Production inventory `rg -n 'Path\\(("|\\x27)\\.?/?data|Path\\.cwd\\(|\\./data|data/models' scanner --glob '*.py'` returned only `scanner/api.py:535: cwd=Path.cwd()`; it is a child-process lifecycle CWD inherited by NATIVE-05/06, not a model/support path. No `./data` or `data/models` production root remains.
Artifact/host evidence: macOS development host only; no real model, browser, live service, frozen artifact, schema operation, or package artifact was used. The initial artwork test created two mock files (12-byte `.image` and 9-byte `.mime`) under `~/Library/Caches/Editarr/source-artwork` at 2026-09-05T16:07:08-0500. The user authorized root to remove exactly that cache directory; root verified it absent. The later NATIVE-02 TestClient isolation repair covered the native audit/log paths. Final metadata-only check after all focused tests returned `ABSENT:~/Library/Caches/Editarr` and `ABSENT:~/Library/Logs/Editarr`.
Known failures or risks: The reopened review found and repaired a workspace leak: `run_scan()` formerly allocated before option validation and before its cleanup scope. Validation now occurs before duration/hash/status/workspace work; the regression covers disabled scans, invalid coarse mode/interval, invalid scene threshold, and invalid Qwen count/gap, asserting no workspace root exists. After successful allocation, the immediately following operation is the `try` block; marker write failure is handled inside `create_scan_workspace()` by removing the just-created markerless directory. Source fallback for FFmpeg delegates to imageio-ffmpeg discovery only when neither `IMAGEIO_FFMPEG_EXE` nor an immutable bundled helper exists. That library may use its own package-managed helper/cache outside Editarr in an unusual source-install environment; NATIVE-10 must provide `${resources_dir}/helpers` in frozen artifacts. Actual Windows filesystem/long-path and installed-artifact verification remain NATIVE-11/15 scope. The focused suite retains 5 existing FastAPI/Starlette deprecation warnings.
Next exact action: NATIVE-04 may implement frozen executable bootstrap/internal roles using the stable cache/helper/workspace interfaces below.
Next eligible task: NATIVE-04.
Decisions or deviations from the parent plan: No acquisition state/revision, capability, frozen-collection, lifecycle, schema, or durable replay behavior was changed. `RuntimePaths.model_cache` derives as `${cache_dir}/models`; `configure_model_cache_environment()` establishes unset `HF_HOME=${model_cache}/huggingface`, `HUGGINGFACE_HUB_CACHE=${model_cache}/huggingface/hub`, `TRANSFORMERS_CACHE=${model_cache}/huggingface/transformers`, `TORCH_HOME=${model_cache}/torch`, and `CT2_CACHE_DIR=${model_cache}/ctranslate2` before dynamic model imports, preserving nonempty operator values. Cache consumers use `${cache_dir}/videos` and `${cache_dir}/source-artwork`; Qwen/Image Safety Gate/SigLIP2 roots are below `settings.model_cache`; retained Qwen replay inputs remain `${data_dir}/qwen-replays`. Workspaces are unique `scan-*` directories under `${temp_dir}/editarr-scans`, marker-owned, removed at scan completion/cancellation/error, and stale-cleaned only when marker-owned, older than 24 hours, and the owner PID is not live (maximum 20 per pass). `resolve_ffmpeg()` honors `IMAGEIO_FFMPEG_EXE`, then immutable `${resources_dir}/helpers/ffmpeg`, then source imageio discovery; `resolve_ffprobe()` uses immutable `${resources_dir}/helpers/ffprobe`, an FFmpeg sibling, or PATH. Commands continue to use argument lists. `scanner.api`'s lifecycle `Path.cwd()` was intentionally left to NATIVE-05/06.
User approval required: None.
```
