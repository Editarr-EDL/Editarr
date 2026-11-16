# NATIVE-14 — Package Linux AppImage

Status: **BLOCKED**

Dependencies: NATIVE-11. Required host: Linux x86_64 at the supported glibc baseline.
Primary scope: native frozen build, AppDir/AppImage, desktop metadata, and distro tests.

## Background

Linux has no single binary ABI across every distribution. The first release promises a
Linux x86_64 AppImage built on the oldest supported glibc baseline, with host kernel,
graphics stack, and drivers remaining external. It is a per-user desktop artifact, not a
systemd service package.

## Instructions

1. From NATIVE-00/10 evidence, record the oldest supportable glibc/distribution baseline
   for all required Python/native wheels. Stop and report any dependency that raises it.
2. Build the shared PyInstaller runtime natively on Linux x86_64 at that baseline. Include
   the CPU dependencies/FFmpeg baseline and only the justified CTranslate2 CUDA runtime;
   exclude MLX, unrelated GPU stacks, GPU drivers, model weights, and local data.
3. Run NATIVE-11 against the raw runtime before AppImage assembly.
4. Create a reproducible AppDir and AppImage with AppRun, Editarr icon, `.desktop`, AppStream
   metadata, version, categories, and correct executable/symlink layout. AppImage mount or
   extraction must not become a writable data root.
5. In a graphical session, implement normal browser launch/relaunch behavior. In headless
   or `--no-browser` mode, start correctly and print/log the URL without a desktop error.
6. Do not install systemd units, write `/etc` or `/var`, require root, or mutate desktop
   integration outside normal optional AppImage mechanisms.
7. Test on the baseline distribution and at least one newer supported distribution,
   including FUSE execution and extract-and-run fallback, CPU models, offline reuse,
   arbitrary CWD, restart, Quit during scan, and XDG path overrides.
8. Produce checksum/signature metadata according to NATIVE-16's eventual release format;
   do not publish it here.

## Fixed decisions

- Linux x86_64 AppImage is primary; deb/rpm/systemd are deferred.
- CPU is guaranteed; compatible NVIDIA hosts may use verified CTranslate2 CUDA. Qwen is
  unavailable and GPU drivers are host-provided.
- The app respects XDG data/config/state/cache/runtime locations.

## Acceptance criteria

- [ ] AppImage runs without system Python/Node/root on baseline and newer distributions.
- [ ] Raw runtime and AppImage pass NATIVE-11.
- [ ] Graphical and headless launch modes behave correctly.
- [ ] Advertised CPU models succeed; CUDA is advertised only when runtime probing proves
      the packaged provider and compatible host driver; Qwen stays visibly unavailable.
- [ ] FUSE and extract-and-run paths do not write into AppDir/AppImage.
- [ ] XDG overrides/defaults and durable-data preservation pass.

## Definition of done

Linux lint/tests, verifier, real CPU model smoke, AppImage metadata validation, baseline/
newer distro runs, and `git diff --check` pass on native x86_64 hosts. A missing baseline
host or unresolved glibc issue means `BLOCKED`.

## Handoff requirements

Record distro/kernel/glibc versions, build/AppImage tool versions, AppDir layout, artifact
hash/size, verifier/model/headless/FUSE results, declared minimum, and portability risks.

## Previous bounded run

```text
Status: BLOCKED
Needs revisit: yes, after the bounded native Linux cycle described below.
Last completed substep: Pushed `codex/native14-linux-package` and ran four bounded native
Linux x86_64 evidence cycles. Repairs aligned pnpm 11.22 with Rocky's Node 22 stream,
installed npm explicitly, made host evidence collection failure-tolerant, and tolerated
Rocky's non-zero `appstream-util --version` status. Run `34390062357` then reached locked
Python dependency installation on the Rocky 8.10/glibc 2.28 baseline and stopped because
uv could not install the locked x86_64 torchvision CPU wheel. Per the bounded Linux
cutoff, no fifth cycle was started.
Files changed: `.github/workflows/native14-linux-package.yml`; `pyproject.toml`; `uv.lock`;
`native_build/linux/AppRun`; `native_build/linux/README.md`;
`native_build/linux/org.editarr.Editarr.desktop`;
`native_build/linux/org.editarr.Editarr.metainfo.xml`; `scripts/package_linux.py`;
`scanner/cli.py`; `tests/test_cli.py`; `tests/test_linux_package.py`; this handoff; task-pack
README. The four approved policy commits also update their recorded capability/build/
Windows integration files on this branch.
Verification run and result: Before pushing, Ruff passed the exact 14-file workflow
selection; the focused package/CLI/installed-verifier/build/capability/audio subset passed
132 tests; `UV_CACHE_DIR=/private/tmp/samwise-native14-uv-cache uv lock --check` resolved
118 packages without changing the lock; Ruby parsed the workflow YAML; and
`git diff --check` passed. After each workflow repair,
`.venv/bin/python -m pytest -q tests/test_linux_package.py` passed 9 tests, Ruby parsed the
workflow, and `git diff --check` passed. Native runs `34389028974`, `34389390147`, and
`34389618340` stopped at the respectively corrected Node/pnpm, missing npm, and
AppStream-version-probe bootstrap defects. Final native run `34390062357` resolved 118
locked packages but failed `uv sync --locked --extra dev` with exit 2: uv 0.12.5 reported
that locked `torchvision==0.24.1` from the PyTorch CPU index had no installable wheel or
sdist for `manylinux_2_28_x86_64` and only saw a `manylinux_2_28_aarch64` wheel.
Artifact/host evidence: GitHub-hosted native x86_64 Ubuntu runner kernel
`6.17.0-1022-azure` / host glibc 2.39 launched the pinned Rocky Linux 8.10 x86_64 image
digest `sha256:e8a49c5403b687db05d4d67333fa45808fbe74f36e683cec7abb1f7d0f2338c6`;
the container asserted x86_64 and glibc 2.28, then recorded Python 3.11.13, uv 0.12.5,
Node 22.23.2, pnpm 11.22.0, FFprobe 4.4.8, and AppStream 0.7.14 before the dependency
failure. Run `34390062357` uploaded only `native14-host-artifact.txt`; no raw runtime,
AppDir, AppImage, checksum, size, verifier JSON, or JUnit artifact exists. AppImage inputs
remain pinned to appimagetool commit
`8c8c91f762b412a19f4e8d2c4b35afb98f2d7c81` / SHA-256
`a6d71e2b6cd66f8e8d16c37ad164658985e0cf5fcaa950c90a482890cb9d13e0`
and type-2 runtime commit `75849dc` / SHA-256
`1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf`.
Known failures or risks: The Linux x86_64 dependency set is not presently installable from
the locked PyTorch CPU source on the chosen baseline. Native PyInstaller build, final ELF
symbol ceiling, raw/AppDir NATIVE-11, AppImage creation, baseline extraction, newer-distro
FUSE and extraction fallback, graphical/headless launch, restart/Quit-during-scan, real
CPU model/offline reuse, and compatible-NVIDIA CUDA evidence therefore remain unproved.
The workflow's fake-model build can prove lifecycle but not real inference. RPM Fusion's
FFprobe repository package is not content-hash pinned; a successful build would record the
copied helper hash in the runtime inventory. Production signing/provenance is NATIVE-16.
Next exact action: On a fresh Linux x86_64 branch/worktree, inspect the PyTorch CPU index
for a CPython 3.11 x86_64 torchvision wheel compatible with glibc 2.28. If 0.24.1 is
published, add the exact `tool.uv.required-environments` Linux/x86_64 constraint and
regenerate/verify the universal lock; otherwise pin the newest compatible torch/
torchvision CPU pair and regenerate the lock. Prove `uv sync --locked --extra dev` inside
the pinned Rocky image before rerunning workflow `native14-linux-package.yml`.
Next eligible task: No dependent task is eligible. NATIVE-12 and NATIVE-13 remain
independently blocked as recorded; NATIVE-15 cannot start until all packaging tasks are
DONE. The parent may proceed only with unrelated work that does not depend on NATIVE-14.
Decisions or deviations from the parent plan: The supported Linux baseline candidate is
Rocky Linux 8 / glibc 2.28 because the current locked required wheels reach
manylinux_2_28. `NATIVE-GAP-02` is closed/revised consistently: CPU is guaranteed, only
CTranslate2 CUDA may be advertised after a successful runtime device/compute-type probe,
unrelated GPU stacks are excluded, and NVIDIA drivers are host-provided. The large
Editarr-owned icon is used instead of the inherited 32-pixel favicon. No schema, migration,
data wipe, systemd integration, root-time application requirement, real user data, or
release publication was introduced.
User approval required: None for this blocked checkpoint. A future owner must obtain the
then-current authorization before pushing any new repair/rerun if the parent's existing
bounded NATIVE-14 authorization is no longer active.
```

## Handoff record

```text
Status: BLOCKED
Needs revisit: yes, after a native Linux lifecycle owner reproduces and fixes the
fake-worker cancellation/reconciliation failure below.
Last completed substep: Resumed at the user's request on 2026-09-09. Confirmed that the
PyTorch CPU index publishes torchvision 0.24.1+cpu for CPython 3.11/Linux x86_64; added
uv required-environments and regenerated the lock without changing the glibc baseline.
Fixed package inventory reads of not-yet-published output paths, Ubuntu verifier/checksum
working directories, and a graphical test fixture that inherited the headless CI session.
Files changed: pyproject.toml; uv.lock; scripts/package_linux.py;
.github/workflows/native14-linux-package.yml; tests/test_linux_package.py; tests/test_cli.py;
scripts/verify_linux_models.py; tests/test_linux_model_verifier.py;
scripts/verify_installed_release.py; tests/test_installed_artifact_verifier.py;
this task and the task-pack README.
Verification run and result: The pinned Rocky 8.10 image, under local x86_64 emulation,
completed uv sync --locked --extra dev (92 installed packages) and imported Torch
2.9.1+cpu plus torchvision 0.24.1+cpu. This is a dependency preflight, not native-host
evidence. On macOS, the 134-test Linux workflow subset passed outside the socket-restricted
sandbox. The new model-verifier/package/installed-verifier subset passed 45 tests;
Ruff and Ruby YAML parsing passed. git diff --check passes for task-owned files;
the pre-existing unrelated AGENTS.md edit has a trailing-blank-line warning and is preserved.
Artifact/host evidence: Native run 34406970574 on abb552e passed locked dependency
installation and Ruff, then stopped with 133 passed/one graphical-fixture failure.
That fixture is fixed in ecf4829. Run 34407369933 built the frozen runtime and passed
its import/helper smoke before the verifier needed missing /bin/ps. With procps-ng
installed, run 34407922349 passed 143 tests, raw NATIVE-11 (12 checks, no failures,
16.378 seconds), the GLIBC_2.28 payload gate, desktop-file validation, and AppStream
validation. AppImage assembly stopped because appimagetool needed the file utility.
Raw executable SHA-256: 2c8c2931f78b25f5a684f3cae6d63b27bea796794f2bd396eea21f37c7d0e91e;
inventory SHA-256: bd9c456779faef92b73067fa0bedee9ff0462dab9a7b770f12cb1456f5dd61f8.
Private CI artifact native14-linux-x86-64-evidence-34407922349 retains the passing
JUnit/verifier/host records. Commit c03ce74 adds file plus Quit-during-scan verification,
Docker init/reaping, faster orphaned-model failure reporting, and development artifact
retention. Native run 34431827885 completed on `c03ce74`: the Rocky 8.10/glibc 2.28
container built the frozen runtime and passed its import/helper smoke; the focused suite
passed 144 tests. The raw NATIVE-11 run then failed after 35.809 seconds because its
test-only fake worker cancellation never removed the queue item during the 30-second
reconciliation bound (`test-only fake worker cancellation did not reconcile the queue`).
The retained private evidence artifact `native14-linux-x86-64-evidence-34431827885`
contains the passing JUnit report, host record, and bounded failing raw-verifier JSON;
no AppDir, AppImage, checksum, AppImage verifier, FUSE, graphical/headless, or real-model
evidence was produced by that run.
Known failures or risks: This is a runtime lifecycle/queue reconciliation failure rather
than a package-assembly failure. `DELETE /api/scans/queue/{id}` returned cancellation
success after the packaged fake worker was observed active, but the queue item remained
visible through the whole verifier bound. The retained evidence does not establish whether
the worker missed the committed cancellation, failed to remove the row, or the scheduler
continued to report stale state. AppImage, real-model, FUSE, distro acceptance, and the
added Quit-during-scan check remain pending. The new real-model harness rejects absent
baseline backends, runs real
packaged scans on generated colour/tone media, and repeats them with different hashes
after a restart with managed-download proxies blocked and HF offline mode enabled.
It proves execution/cache reuse, not speech/detector accuracy. Shared verifier fixes also
remove the NATIVE-13 Windows-only SIGKILL evaluation and retain failure JSON.
Next exact action: On a native Linux x86_64 host, reproduce the raw frozen
`verify_installed_release.py --test-fake-models` failure with bounded redacted queue-status
and worker/controller-log evidence. Assign any product lifecycle correction to its owner
before rebuilding; then prove the raw verifier and continue AppImage assembly. Do not
start another NATIVE-14 packaging cycle until that exact raw-runtime failure is resolved.
Next eligible task: NATIVE-12 and NATIVE-13 can be revisited independently; NATIVE-14 is
set aside under the repeated Linux dev/test failure rule, and NATIVE-15 remains blocked
until all three packaging tasks are DONE.
Decisions or deviations from the parent plan: No baseline, schema, product contract,
signing policy, data wipe, service integration, or release publication change. User's
existing Editarr and unrelated AGENTS.md edits are untouched.
User approval required: None for the resumed development/package work. The existing
private GitHub repository was verified against the authenticated owner/admin before
the NATIVE-14 repair commits were pushed.
```
