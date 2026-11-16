# NATIVE-07 — Make model capabilities truthful and Qwen optional

Status: **DONE**

Dependencies: NATIVE-03. Primary scope: backend capability model, scan validation,
Qwen fallback, API DTOs, and focused scan/capability tests. NATIVE-09 owns UI.

## Background

The current capability endpoint lists detector choices but not enrichment availability.
Flagged visual segments always invoke Qwen, whose implementation rejects non-macOS.
Therefore a nominally supported Windows/Linux detector can fail only after doing costly
work. The first native Windows/Linux release promises CPU detector findings without
Qwen; macOS arm64 promises MLX Qwen.

## Instructions

1. Implement the NATIVE-00 machine-readable target/model capability contract in one
   backend source of truth. Represent backend ID, stage, availability, reason, device,
   weight/download requirement, and enrichment behavior without importing/loading a model.
2. Detect the actual OS, architecture, packaged dependencies, and supported providers.
   Provide deterministic injection for tests; never claim CUDA merely because hardware
   might exist when the packaged provider is absent.
3. Extend `/api/capabilities` compatibly so clients can distinguish detector support,
   Qwen enrichment, CPU/accelerator device, and deferred/unavailable reasons.
4. Make Qwen summarization conditional on the declared capability. On Windows/Linux,
   preserve the original detector findings and scan completion when Qwen is unavailable.
   Do not fabricate summaries or discard findings.
5. On macOS arm64, treat unavailable MLX/Qwen dependencies as a truthful capability
   failure before scanning, not a late crash. Preserve replay generation semantics when
   Qwen runs.
6. Add platform-matrix tests for macOS arm64, Windows x64, Linux x86_64, wrong
   architecture, missing packages, and provider variations. Test clean and flagged scenes.
7. Update API contract docs and authentication route inventory if its response shape or
   endpoints change.

## Fixed decisions

- Windows/Linux: CPU audio/detectors supported, Qwen unavailable (`NATIVE-GAP-01`), and
  optional CUDA for a backend only after its packaged runtime and host NVIDIA driver are
  verified (`NATIVE-GAP-02` closed/revised).
- macOS arm64 retains the existing MLX Qwen path.
- Capability checks must be fast, offline, and side-effect free.

## Acceptance criteria

- [x] Every UI-advertisable model/stage has a structured availability and reason.
- [x] A flagged Windows/Linux visual scan completes with unsummarized detector findings.
- [x] An unavailable backend is rejected before queueing or is omitted, never failed late.
- [x] Capability inspection performs no download or heavyweight model load.
- [x] Platform/dependency/provider matrix tests cover all initial targets and gaps.

## Definition of done

Focused capability/runner/visual/API tests and Ruff pass, API docs are current,
`git diff --check` passes, and NATIVE-08/09 receive exact stable DTOs/interfaces.

## Handoff requirements

Record capability types/payload examples, platform matrix, Qwen fallback algorithm,
compatibility decisions, test evidence, and remaining acquisition/UI work.

## Handoff record

```text
Status: DONE
Last completed substep: Audited and repaired the capability matrix, conservative device selection, pre-admission validation, and detector-only Qwen fallback across the three target contracts.
Files changed: scanner/capabilities.py; scanner/models.py; scanner/api.py; scanner/pipeline/audio.py; scanner/pipeline/runner.py; scanner/pipeline/visual.py; scanner/pipeline/qwen_summary.py; scanner/pipeline/image_safety_gate.py; scanner/pipeline/siglip2_moderation.py; tests/test_model_capabilities.py; tests/test_editarr_integration.py; tests/test_audio_progress.py; tests/test_visual_models.py; tests/test_qwen_summary.py; tests/test_qwen_replay.py; tests/test_visual_qwen_segments.py; docs/feature-design-spec.md; docs/native-application-tasks/README.md; docs/native-application-tasks/07-model-capabilities.md.
Verification run and result: Under isolated temporary HOME, USERPROFILE, LOCALAPPDATA, TMPDIR, XDG_CACHE_HOME, UV_CACHE_DIR, EDITARR_DATA_DIR, and EDITARR_LOG_DIR roots, `uv run ruff check scanner tests scripts` passed; the focused capability/API/audio/visual/Qwen/runner/cache/scene suite passed 142 tests with 5 existing FastAPI/Starlette deprecation warnings; and `git diff --check` passed. `tests/test_dedicated_worker.py` was also attempted under both long and short isolated temporary roots: 2 tests passed and 3 reached the worker-control setup but could not bind AF_UNIX because the managed sandbox denies socket creation. A broad suite excluding that file reached 528 passes; its 14 failures were 13 sandbox-denied AF_UNIX/loopback binds plus one expected path-precedence assertion caused by the deliberately injected EDITARR overrides, which passed separately with those two overrides omitted.
Artifact/host evidence: Unit-injected target/package/provider matrix covers macOS arm64, Windows x64, Linux x86_64, Intel macOS, Windows ARM64, Linux ARM64, unsupported OS, missing direct dependencies, missing CPU provider, verified Core ML/MPS variations, and injected-but-forbidden Windows/Linux accelerators. Current macOS development-host source tests only; no model load, download, browser, user data, live service, frozen artifact, schema operation, or package artifact was used. The pre-existing `~/Library/Logs/Editarr` directory and four disclosed files retained their 2026-09-06 timestamps and sizes.
Known failures or risks: Side-effect-free current-process probing intentionally reports only the CPU provider because module/package stubs cannot prove that MPS, Core ML, or CUDA can initialize; NATIVE-10 must connect and validate an authoritative accelerator inventory for frozen macOS builds before advertising those devices. Capability declarations do not prove a frozen artifact, model download, or real inference. NATIVE-08 owns manifest-backed download transitions. The three dedicated-worker assertions could not complete in this sandbox because AF_UNIX bind is prohibited; prior NATIVE-06 required-host evidence owns that lifecycle behavior.
Next exact action: NATIVE-08 should replace the provisional `not_downloaded`/`not_required` download states with pinned manifest-backed acquisition states while preserving the `ModelCapability` DTO and side-effect-free reads.
Next eligible task: NATIVE-08.
Decisions or deviations from the parent plan: `GET /api/capabilities` preserves legacy selectable lists and the accepted `alt-profanity-check` request alias while exposing only canonical detailed IDs, and adds `target_id` plus ordered `model_capabilities`. `ModelCapability` is the stable interface: `id`, `display_name`, `stage`, `availability`, `reason`, `device`, `weight_requirement`, `download`, and `enrichment_behavior`; `download` has `state`, nullable byte progress, and nullable error fields. The `alt-profanity-check` distribution is correctly probed by its `profanity_check` import package. Windows/Linux Qwen is `unavailable/deferred_gap/detector_findings_only`, so visual scans return the exact original detector findings with no Qwen import, summary, progress, or replay capture. On macOS arm64, missing Qwen dependencies reject visual scan admission before media metadata, hashing, workspace allocation, or queue status changes. The original handoff forced Windows/Linux inference to CPU; the post-handoff correction below supersedes that only for verified CTranslate2 CUDA. NudeNet's package-bundled model is `not_required`; separately acquired weights remain provisional `not_downloaded` until NATIVE-08. No schema, migration, data wipe, acquisition manifest, UI, or frozen-package behavior changed.
User approval required: None.
```

### Post-handoff additive CUDA correction

The 2026-09-09 product decision closes/revises `NATIVE-GAP-02`. Windows/Linux retain CPU
as the guaranteed fallback. The detailed capability device enum now includes `cuda`, and
faster-whisper reports it only after cached CTranslate2 calls prove both a visible CUDA
device and supported CUDA compute types. Missing or incompatible drivers/runtimes fail
closed to `cpu`; filenames and importable stubs alone never advertise CUDA. The current
ONNX detector stack remains CPU because the locked `onnxruntime` distribution does not
package CUDAExecutionProvider. Qwen remains `NATIVE-GAP-01`.
