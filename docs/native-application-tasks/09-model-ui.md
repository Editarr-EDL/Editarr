# NATIVE-09 — Expose model availability and downloads in the web UI

Status: **DONE**

Dependencies: NATIVE-08. Primary scope: protected model-management API, frontend model
state/download controls, scan-form disclosures, and backend/component/browser tests.

## Background

On-demand weights need visible states rather than a scan appearing stalled. Users must
know what will download, whether it is installed, where disk is used, and why Qwen/GPU
is unavailable on Windows/Linux. Existing scan forms know detector IDs but not download
or enrichment state.

## Instructions

1. Add protected APIs, using NATIVE-07/08 contracts, to list model states and sizes,
   start/prefetch a download, observe progress, cancel an active download, and remove a
   verified managed model. Keep unsafe requests under existing CSRF/origin/auth policy.
2. Prevent duplicate acquisitions and destructive removal while a model is in use.
   Return stable, non-secret error codes for offline, disk, integrity, cancellation, and
   platform-unavailable cases.
3. Add a Model Storage section in Settings showing required backend/stage, device,
   installed/downloading/unavailable status, progress, disk use, source/license link,
   remove action, and cache location/open-folder action where supported.
4. Update every scan entry point to consume capabilities. Before submission, disclose
   downloads that will occur and the Windows/Linux detector-only result. Never advertise
   Qwen or GPU as active when unavailable.
5. Starting a scan may lazily acquire missing supported weights, but the queue/progress UI
   must distinguish download from inference. Cancellation must settle both acquisition
   and scan state deterministically.
6. Add backend authorization/state tests, React tests for every state/error/action, and a
   Playwright journey with mocked model bytes/progress. Include keyboard, narrow viewport,
   and accessibility checks for new controls.
7. Update API and user documentation, including `NATIVE-GAP-01/02` wording.

## Fixed decisions

- Model management is authenticated application functionality.
- Users can remove only regenerable weights/cache, never durable state.
- Windows/Linux scan results remain valid detector findings without Qwen prose.
- Do not put Hugging Face credentials or raw cache paths into public unauthenticated data.

## Acceptance criteria

- [ ] Model list/download/cancel/remove endpoints enforce setup/auth/CSRF policies.
- [ ] UI shows accurate states and survives reload during a download.
- [ ] Scan forms disclose download and Qwen/GPU gaps before queueing.
- [ ] Removal-in-use, offline, corrupt, and insufficient-disk states are actionable.
- [ ] Component and browser accessibility/responsive checks pass without arbitrary sleeps.

## Definition of done

Focused backend tests, frontend tests/coverage/build, relevant Playwright tests, Ruff,
and `git diff --check` pass. API and web contracts are documented for NATIVE-10/11.

## Handoff requirements

Record routes/payloads/error codes, authentication classification, frontend components,
polling/event behavior, scan integration, test commands/results, and frozen-resource
considerations for NATIVE-10.

## Handoff record

```text
Status: DONE
Last completed substep: Added protected manifest-backed model management, storage UI, scan disclosures, and mock browser coverage.
Files changed: scanner/api.py; scanner/auth_policy.py; scanner/models.py; tests/test_model_storage_api.py; web/src/api.ts; web/src/api.test.ts; web/src/App.tsx; web/src/styles.css; web/e2e/app.spec.ts; README.md; docs/feature-design-spec.md; docs/native-application-tasks/README.md; docs/native-application-tasks/09-model-ui.md.
Verification run and result: Earlier NATIVE-09 verification passed as recorded. Corrective review additionally ran `UV_CACHE_DIR=/private/tmp/samwise-uv-cache uv run ruff check scanner/model_acquisition.py scanner/pipeline/runner.py tests/test_model_acquisition.py tests/test_runner_cache.py` (passed), `EDITARR_DATA_DIR=/private/tmp/samwise-n09-test-data UV_CACHE_DIR=/private/tmp/samwise-uv-cache uv run pytest -q tests/test_model_acquisition.py tests/test_runner_cache.py tests/test_audio_progress.py tests/test_qwen_summary.py tests/test_model_storage_api.py` (67 passed), `pnpm test -- --run src/App.test.tsx src/api.test.ts` (124 passed), `pnpm build` (passed), and `git diff --check` (passed). The model-download Playwright journey passed; the broad invocation reported 30 passed and one unrelated Radarr/Sonarr card-layout assertion failure (`0x0` rather than `140x277`).
Artifact/host evidence: Network-free manifest/service doubles cover model state, cancellation, removal conflict, safe response shape, and route inventory. Chromium mock coverage verifies keyboard activation, download-progress presentation, narrow viewport, and axe accessibility. Corrective coverage proves bundled/not-required backendless artifacts do not expose a download action, and a per-isolated-scan `ContextVar` cancellation scope reaches `AcquisitionCancelled` before lazy audio inference without leaking to a later acquisition. No model, user cache, user data, live service, or frozen artifact was touched; test-only runtime state was isolated under `/private/tmp`.
Known failures or risks: The APIs expose no raw cache path and therefore intentionally omit an open-folder control until a supported native shell integration exists. Real packaged-host model inference/acquisition and release approval remain NATIVE-15 work. `mlx-whisper-base`, `stable-diffusion-safety-checker`, and imageio-ffmpeg retain their NATIVE-08 `license-review-required` release blockers.
Next exact action: NATIVE-10 should include the protected routes, manifest resource, and frontend assets in the frozen runtime build.
Next eligible task: NATIVE-10.
Decisions or deviations from the parent plan: `GET /api/models` is authenticated and path-free; `POST /api/models/prefetch` starts a deduplicated daemon acquisition; cancel/removal use exact artifact IDs. Stable codes include NATIVE-08 acquisition codes plus `unknown_backend`, `unknown_artifact`, `platform_unavailable`, `acquisition_active`, and `model_in_use`. Corrective review limits Download to removable artifacts with an actual backend ID; packaged/not-required entries are informational only. Scan-owned child processes install a context-local cancellation check, so every lazy acquisition path—including audio, Qwen, Image Safety Gate, and SigLIP2 service calls—uses the individual scan's event without cancelling concurrent scans or UI prefetch work; cancellation has a five-second cooperative-acquisition grace before the existing process-tree termination fallback.
User approval required: None.
```
