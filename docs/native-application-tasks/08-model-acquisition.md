# NATIVE-08 — Harden on-demand model acquisition

Status: **DONE**

Dependencies: NATIVE-07. Primary scope: model artifact manifest, pinned acquisition,
locking/atomicity, cache management service, and backend integration. NATIVE-09 owns web UI.

## Background

Several model modules call Hugging Face download APIs directly with repository names and
no pinned revision. Multiple processes may race, interrupted downloads can leave partial
state, and users cannot inspect or remove managed weights. Native releases need
reproducible, on-demand downloads outside the immutable app, followed by offline reuse.

## Instructions

1. Inventory every runtime weight/resource: faster-whisper, mlx-whisper, NudeNet, Qwen
   MLX, WD taggers, CLIP safety checker, NSFL classifier, SigLIP2 encoder/heads, and any
   tokenizer/processor files. Classify bundled-package data versus downloaded artifacts.
2. Add a versioned model artifact manifest containing stable Editarr backend ID,
   upstream repository, immutable revision, required filenames/patterns, expected size
   when knowable, license/source metadata, and integrity data. Never use a moving branch.
3. Implement an acquisition service rooted at `RuntimePaths.model_cache` with
   cross-process locking, disk-space preflight, progress, cancellation, atomic completion
   marker, integrity verification, interrupted-download recovery, offline status, and
   safe per-model removal.
4. Adapt model loaders to require artifacts through that service. Keep lazy download on
   first use and permit explicit prefetch. Do not download during import/capability calls.
5. Ensure a scan does not report model-ready until all required files verify. Concurrent
   API/worker requests for the same model must share or serialize acquisition safely.
6. Make errors actionable without exposing tokens, proxy credentials, or local secrets.
7. Add network-free unit tests with a fake repository plus one isolated real macOS smoke
   for a reasonably sized required model. Record large-model/Qwen verification for
   NATIVE-15 if impractical here; do not mark core locking/integrity work incomplete.
8. Add/refresh third-party model and binary license notices; flag any redistribution or
   download-license blocker explicitly.

## Fixed decisions

- Weights are downloaded on demand and may be pre-fetched, never installer payload.
- Clearing a model cannot affect database, credentials, scan history, or replay inputs.
- CPU/GPU capability and Qwen platform policy remain owned by NATIVE-07.
- No authentication token is required for the declared default model set unless a
  licensing review causes a user decision; do not silently add one.

## Acceptance criteria

- [x] Every downloadable artifact is pinned and represented in the manifest.
- [x] Interrupted/corrupt/concurrent downloads cannot appear installed.
- [x] Verified weights work offline after acquisition.
- [x] Removal is confined to the exact managed model cache subtree.
- [x] Imports and capability reads remain network-free.
- [x] License/source information exists for every bundled or downloaded model component.

## Definition of done

Focused model/audio/visual/acquisition/cache tests and Ruff pass, the fake acquisition
suite covers failure paths, a real-host smoke or explicit NATIVE-15 deferral is recorded,
`git diff --check` passes, and API hooks for NATIVE-09 are stable.

## Handoff requirements

Record the complete manifest inventory/revisions, cache layout, state machine, lock and
integrity scheme, loader changes, licenses, real/fake verification, deferred large model
checks, and exact service methods needed by NATIVE-09.

## Handoff record

```text
Status: DONE
Last completed substep: Independently reviewed and hardened manifest validation, managed-path containment, exact loader acquisition, license blockers, and network-free failure/concurrency coverage after the real macOS smoke; split and audited the three package-supplied profanity notices so no combined entry asserts an unverified shared license.
Files changed: scanner/model-manifest-v1.json; scanner/model_acquisition.py; scanner/capabilities.py; scanner/pipeline/audio.py; scanner/pipeline/image_safety_gate.py; scanner/pipeline/qwen_summary.py; scanner/pipeline/siglip2_moderation.py; tests/test_model_acquisition.py; tests/test_audio_progress.py; tests/test_qwen_summary.py; tests/test_visual_models.py; README.md; docs/model-artifact-notices.md; docs/native-application-tasks/README.md; docs/native-application-tasks/08-model-acquisition.md; docs/native-application-tasks/15-platform-acceptance.md; docs/native-application-tasks/17-release-audit.md.
Verification run and result: Under isolated HOME, USERPROFILE, LOCALAPPDATA, TMPDIR, XDG_CACHE_HOME, and UV_CACHE_DIR roots, `uv run ruff check scanner tests scripts` passed and the focused acquisition/capability/audio/visual/Qwen/cache/runner/frame/API suite passed 178 tests with 5 existing FastAPI/Starlette deprecation warnings. A final independent acquisition/capability/audio/visual/Qwen review after the package-notice split passed 88 tests; its only console note was uv's non-fatal inability to create its project-environment lock under the isolated HOME. Root review then split the imageio-ffmpeg wrapper and binary license records, removed metadata-based content-hash reuse, and passed Ruff plus 166 focused tests with the same 5 warnings. The full suite passed 550 tests and had 16 known sandbox-only failures: AF_UNIX listener binding and loopback-port binding are denied in this environment, which cascades into native-lifecycle, dedicated-worker, process-control, and restart-service tests; it had the same 5 existing deprecation warnings. The fake suite covers atomic verified reuse, same-size corruption, offline failure/reuse, interruption, secret-redacted failures, cancellation/retry, disk preflight, thread and spawned-process serialization, exact removal, stale state, and cache/control/artifact symlink defenses. `git diff --check` passed. The JSON manifest is inside the wheel's configured `scanner` package and loaded with `importlib.resources`; a local wheel-build attempt reached the existing release hook but correctly stopped because the production frontend had not been staged, and no network/install was authorized.
Artifact/host evidence: On the macOS arm64 development host, the approved isolated real smoke downloaded only `Systran/faster-whisper-base@ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`: 147,882,941 bytes across four declared files, including `model.bin` SHA-256 `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9`. It reached `ready`, then a second `offline=True` acquire returned the same path. The exact temporary cache `/private/tmp/editarr-native08-real-smoke-W85zn2` was removed by the root agent after the smoke; no other weights were downloaded. Two metadata-only HEAD/API checks corrected malformed inherited SigLIP2 integrity strings without downloading content.
Known failures or risks: Real inference and real acquisition for MLX Whisper, all WD/CLIP/NSFL/SigLIP2 artifacts, and the 5.78 GB Qwen artifact remain NATIVE-15 packaged-host work. `mlx-whisper-base` and `stable-diffusion-safety-checker` have no declared repository/model license, and the imageio-ffmpeg executable has separate FFmpeg license and source-offer obligations. They remain development/on-demand components marked `license-review-required`; NATIVE-15 cannot approve a release candidate and NATIVE-17 cannot return GO until each is cleared or removed. Git blob SHA-1 is used for small Git-served Hub files and upstream LFS SHA-256 for LFS files; final release evidence must revalidate the complete manifest on target hosts.
Next exact action: NATIVE-09 should add authenticated model-management routes and UI using `list_artifacts`, `backend_status`, `prefetch_backend`, `request_cancel`, and `remove`, while retaining lazy loader acquisition.
Next eligible task: NATIVE-09.
Decisions or deviations from the parent plan: Managed layout is `${RuntimePaths.model_cache}/artifacts/<artifact-id>/<revision>` plus private `.staging`, `.locks`, `.state`, and `.cancel` siblings. Per-artifact thread locks wrap POSIX `flock` or Windows `msvcrt.locking`; downloads stream into unique staging trees, update atomic state, verify every exact size/hash, write `.complete.json`, and rename into place before `ready`. Acquires reverify content before offline reuse; fast status requires a matching manifest/revision marker and unchanged file metadata. Recovery removes stale exact-artifact staging only while holding its lock. Loaders receive only verified local paths, and capability/import reads perform no network or directory creation. The complete downloadable inventory is Faster-Whisper base; MLX Whisper base; five WD v3 variants; CompVis CLIP safety checker; Image Safety Classifier S; SigLIP2 encoder plus NSFW/violence heads; and Qwen3-VL 8B 4-bit, with exact revisions/files/sizes in the manifest and human-readable inventory in `docs/model-artifact-notices.md`. NudeNet, each of Better Profanity, GLiN Profanity, and alt-profanity-check, and imageio-ffmpeg are independently classified as package-supplied. Only the configured base Whisper checkpoint is supported rather than permitting an unpinned arbitrary size/repository setting.
User approval required: None.
```
