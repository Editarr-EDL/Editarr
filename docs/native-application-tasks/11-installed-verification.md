# NATIVE-11 — Create installed-artifact verification

Status: **DONE**

Dependencies: NATIVE-10. Primary scope: portable black-box verifier, frozen resource and
process checks, production browser fixture integration, and macOS runtime evidence.

## Background

The existing installed-release verifier installs a wheel and may borrow dependency
site-packages, so it cannot prove native self-containment. Platform packaging tasks need
one harness that accepts an already-built executable/application, isolates every writable
path, suppresses the real browser, and returns machine-readable evidence.

## Instructions

1. Create or refactor a verifier that accepts an executable/app runtime and target
   metadata without importing Editarr from the checkout or adding dependency paths.
2. Clear inherited `EDITARR_*`, Python, Hugging Face, and library cache variables; assign
   isolated runtime paths and dynamic ports. Do not mutate global user folders.
3. Exercise first-run startup/readiness, setup/login/logout, protected assets, CSRF,
   capabilities/model states, Base URL prefix, saved-port restart, failed-bind recovery,
   second-launch singleton behavior, full Quit, and no writes under the runtime.
4. Exercise scan worker launch/cancel/reconcile with deterministic lightweight fake model
   adapters packaged only for a test build. Do not claim real inference; NATIVE-15 owns it.
5. Inspect the process tree to prove children run from the packaged application and no
   system `python`, `node`, Vite, or checkout module is used.
6. Reuse the production browser fixture for real bundled UI behavior. Browser launching
   by the application itself must be injected/suppressed during automation.
7. Emit bounded JSON/text evidence: target/version, paths, process roles, checks, failures,
   artifact hash, and duration. Redact all generated credentials/tokens.
8. Run it against NATIVE-10's macOS arm64 runtime from a copied directory whose path has
   spaces/Unicode and whose program files are read-only.

## Fixed decisions

- A borrowed virtualenv or site-packages path is not native verification.
- Fake models prove lifecycle/UI only; real models are mandatory later in NATIVE-15.
- The verifier never installs, signs, publishes, or opens the user's real browser.

## Acceptance criteria

- [x] Verifier runs without checkout imports or external language runtimes.
- [x] It covers auth, UI, restart, singleton, worker, Quit, and path isolation.
- [x] Process/resource inspection proves packaged origins and immutable program files.
- [x] Output is machine-readable, bounded, and secret-free.
- [x] macOS runtime passes from a read-only Unicode/spaced path.

## Definition of done

Verifier unit tests, macOS black-box run, relevant production browser checks, Ruff/web
build where affected, and `git diff --check` pass. Provide a stable command contract to
all platform package tasks.

## Handoff requirements

Record verifier CLI/schema, fixture mechanism, macOS artifact/hash/host, exact command and
results, intentionally deferred real-model assertions, and platform integration notes.

## Handoff record

```text
Status: DONE
Last completed substep: Correctively audited the completed copied frozen-runtime proof.
The verifier now obtains version and target from the packaged runtime inventory, rejects
missing/malformed inventory and a requested-target mismatch before launch, and records the
inventory hash/source revision with the evidence. No artifact rebuild was required because
the retained r4 runtime already contains the canonical build-generated inventory.
Files changed: scripts/verify_installed_release.py; scripts/proxy_fixture.py;
scripts/production_browser_fixture.py; web/e2e/run-production-e2e.mjs; scanner/api.py;
scanner/pipeline/runner.py; scripts/build_release.py; native_build/editarr.spec;
native_build/native-test-fake-models.marker; tests/test_installed_artifact_verifier.py;
tests/test_native_runtime_build.py; this file.
Verification run and result: `UV_CACHE_DIR=/private/tmp/samwise-native11-uv-cache uv run
ruff check scripts/verify_installed_release.py tests/test_installed_artifact_verifier.py
scanner/test_fake_models.py scanner/api.py scanner/pipeline/runner.py` passed. The final-code metadata/build subset `UV_CACHE_DIR=/private/tmp/samwise-native11-uv-cache uv run pytest -q tests/test_installed_artifact_verifier.py tests/test_native_runtime_build.py` passed (30 tests). The broader lifecycle suite passed 68 tests with one existing FastAPI/Starlette deprecation warning before the final added target-mismatch evidence assertion; one restricted-sandbox attempt had six expected `Operation not permitted` socket-bind failures before the approved passing rerun. A requested final broader rerun was interrupted after the artifact proof had passed; the changed verifier/tests subset remained green. The earlier production check `EDITARR_PRODUCTION_EDITARR=/private/tmp/editarr-native11-test-build-r4/Editarr/Editarr pnpm test:e2e:production` passed (one production Chromium test); the metadata-only correction did not affect that fixture. `git diff --check` passed. The final valid command was `UV_CACHE_DIR=/private/tmp/samwise-native11-uv-cache uv run python scripts/verify_installed_release.py --runtime /private/tmp/editarr-native11-test-build-r4/Editarr --target macos-arm64 --test-fake-models --json-out /private/tmp/editarr-native11-r4-fake-evidence.json`; it passed in 17.181 seconds. It proved packaged metadata, readiness, bundled UI/protected routes, setup/login/CSRF/logout, Base URL prefix, saved-port restart, occupied-bind recovery retaining pending changes, singleton, authenticated launcher Quit and pending-origin relaunch, runtime immutability, and fake-worker queue POST 201/scanning/authenticated 0600 control socket/cancel 200/reconciliation. Process evidence contains only copied controller/http-child/worker processes plus the worker-PID-bound macOS `caffeinate` helper.
Artifact/host evidence: `/private/tmp/editarr-native11-test-build-r4/Editarr`, macOS arm64, SHA-256 `d2bd48955cb429ecf93b12c6b38ea43c679a5546d0313f12ca30db2973245adf`. The verifier copied it to a mode-0555 path named `Editarr verified ✓ runtime` beneath a temporary directory with spaces, used only isolated writable roots, and retained the 5.2-KiB bounded JSON evidence at `/private/tmp/editarr-native11-r4-fake-evidence.json`. Schema `editarr-installed-artifact-v1` now includes top-level packaged `version` (`0.1.0`), `target`, `source_revision`, and `runtime_inventory_sha256`; the recorded version matches canonical `[project].version` in `pyproject.toml`, failures are `[]`, and generated credentials/worker token are redacted. Stable CLI: `verify_installed_release.py --runtime RUNTIME --target TARGET --json-out EVIDENCE`; runtime identity always comes from `editarr-runtime-inventory.json`, while optional `--target` is only a mismatch guard. `--test-fake-models` requires the separately marker-gated frozen test build. The production browser fixture accepts a frozen executable without borrowed site-packages.
Known failures or risks: Direct HTTP prefix testing deliberately sets only the verifier’s isolated `EDITARR_AUTH_COOKIE_SECURE=0`; real TLS secure-cookie behavior passed in the production proxy/browser fixture. The fake adapter is lifecycle/UI-only and requires both the test-build marker and `EDITARR_TEST_FAKE_MODELS=1`; real inference/model acquisition is deferred to NATIVE-15. Windows and Linux package tasks must run this runtime-only CLI natively against their own raw/package artifacts; no cross-platform artifact claim is made from the macOS run.
Next exact action: NATIVE-12, NATIVE-13, and NATIVE-14 may independently package their target-native runtimes and run the shared verifier before installer/AppImage work.
Next eligible task: NATIVE-12, NATIVE-13, or NATIVE-14.
Decisions or deviations from the parent plan: The fake adapter requires both
`EDITARR_TEST_FAKE_MODELS=1` and an immutable marker collected only by
`scripts/build_release.py --test-fake-models`; ordinary source and production frozen
runtimes fail closed to the real acquisition/inference path.
User approval required: None.
```

### Post-handoff shared Windows layout correction

The stable `verify_installed_release.py --runtime RUNTIME --target TARGET` contract now
reads the packaged inventory first and resolves the target-specific raw entrypoint:
`Editarr.exe` for `windows-x64`, `Editarr` for macOS/Linux, and the existing
`Editarr.app/Contents/MacOS/Editarr` wrapper for macOS bundles. The test-only bundled
FFmpeg lookup likewise uses `.exe` on Windows. Missing, wrong-target, symlinked/non-
executable, or ambiguous candidates fail before launch. This platform-neutral correction
does not add Windows runtime or verifier evidence; NATIVE-13 still owns that native-host
proof.
