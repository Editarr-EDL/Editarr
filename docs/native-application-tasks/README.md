# Native application coding tasks

Parent: [Editarr native application distribution plan](../native-application-plan.md).

Status: **NATIVE-00 through NATIVE-12 DONE. NATIVE-12 packages an intentionally unsigned
macOS app/DMG and validates the end-user local ad-hoc self-sign path; Developer ID signing,
notarization, stapling, and Gatekeeper acceptance are not release prerequisites in this
phase. NATIVE-13 is BLOCKED and
needs revisit after its bounded Windows repair cycle: run `34379530289` passed 106 tests,
the intentional CTranslate2 CUDA/native-payload gate, x64 PyInstaller build, frozen smoke,
and most raw NATIVE-11 lifecycle checks, then the verifier evaluated POSIX-only
`signal.SIGKILL` during Windows teardown. The unsigned installer stage was not reached and
production Windows signing authority is unavailable. The 2026-09-09 decision still
guarantees CPU fallback while permitting verified optional CUDA. NATIVE-14 is BLOCKED and
needs revisit after its repeated bounded Linux cycle. Its Linux x86_64 CPU lock installs on
Rocky 8/glibc 2.28; run `34431827885` built the frozen runtime and passed 144 focused tests,
but raw NATIVE-11 failed because a cancelled fake worker never reconciled its queue entry
within 30 seconds. The retained private evidence is
`native14-linux-x86-64-evidence-34431827885`; AppImage and real-model acceptance evidence
remain pending. NATIVE-15
through NATIVE-17 are NOT STARTED.**

Post-test housekeeping disclosure: an earlier flawed NATIVE-06 source test created
`~/Library/Logs/Editarr` with four task-looking files after a child inherited
the developer HOME. Cleanup remains awaiting the user's explicit authorization. This is
not a code or required-host-evidence blocker; do not represent the directory as deleted
or untouched.

This pack is written for a Terra-High coding subagent receiving one bounded task
without conversation history. Before changing code, read this README, the parent plan,
the assigned task, repository `AGENTS.md`, and every prerequisite task's handoff. The
parent plan and confirmed decisions below are authoritative.

## Assignment prompt

Assign exactly one task with a prompt of this form:

> Use the Terra model with high reasoning. Implement NATIVE-XX from
> `docs/native-application-tasks/XX-NAME.md`. Read that file, the task-pack README,
> parent plan, repository AGENTS.md, and prerequisite handoffs. Work only within the
> assigned scope, preserve unrelated changes, satisfy every acceptance criterion and
> definition-of-done item, update the task handoff and master checklist, and return a
> concise checkpoint status. Do not begin a dependent task, publish artifacts, use real
> user data, restart a user's Editarr instance, or silently decide a new product or
> architecture question.

This pack defines work; it does not itself authorize launching agents or creating
sidebar tasks. A dependency is complete only when its task status is `DONE` and its
handoff includes passing evidence. Each agent owns its assigned task file and checklist
row. Allowed statuses are `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, and `DONE`.

## Confirmed product decisions

1. The release is one user-visible download/application per OS. Private runtime files
   and helper executables inside the app/install are allowed; a literal one-file
   installed executable is not required.
2. Keep Python/FastAPI/SQLAlchemy and the React web UI. Freeze CPython and dependencies;
   do not rewrite the product to obtain native packages.
3. End users do not install Python, pip, Node.js, pnpm, or Editarr dependencies. Wheels
   and sdists are not end-user release artifacts.
4. Initial targets are macOS Apple Silicon, Windows x64, and Linux x86_64.
5. The first release is per-user and runs while that user is logged in. Machine services
   and unattended boot-time execution are deferred.
6. Model weights download on demand to a managed cache and are not bundled in installers.
7. Windows/Linux CPU scanning is the guaranteed first-release baseline. Qwen enrichment
   remains deferred gap `NATIVE-GAP-01`. `NATIVE-GAP-02` is closed/revised: CUDA is an
   optional backend-specific accelerator only when its packaged runtime and a compatible
   host NVIDIA GPU/driver are verified; GPU drivers are not bundled. Additional
   architectures are `NATIVE-GAP-03`.
8. Windows/Linux visual findings must still complete without Qwen. Capabilities and UI
   must disclose the missing enrichment before scan execution.
9. PyInstaller `onedir` is the first freezer candidate. A bounded Nuitka comparison is
   allowed only if NATIVE-10 records a concrete PyInstaller blocker.
10. Windows installer updates use signing when signing authority is available. In-app
    self-update is deferred.
11. The initial Apple Silicon macOS release supports macOS 26.2 and later.
12. macOS releases currently ship an unsigned bundled app/DMG. The end user must clear
    its quarantine attribute and locally ad-hoc-sign the app before launch; do not claim
    Developer ID signing, notarization, stapling, hardened-runtime coverage, or Gatekeeper
    acceptance. This changes only the macOS policy; Windows signing remains its own task.

### Deferred tracked gaps

- `NATIVE-GAP-04`: supporting an older macOS floor is future work, not a new current
  target. It requires rebuilding or replacing `_internal/mlx/core.cpython-311-darwin.so`,
  `_internal/mlx/lib/libjaccl.dylib`, and `_internal/mlx/lib/libmlx.dylib`, proving all
  packaged Mach-O deployment commands meet the proposed floor, and repeating
  installed-package self-sign plus complete real MLX/Qwen checks on that macOS release.

Do not ask the user to approve these decisions again. Stop and ask only if implementation
requires a materially different architecture, supported target, persistence model, or
security boundary.

## Master checklist and dependency order

| Done | ID | Task | Prerequisites | Required host |
| --- | --- | --- | --- | --- |
| [x] | NATIVE-00 | [Freeze contracts and baseline](00-contract-baseline.md) | None | macOS development host |
| [x] | NATIVE-01 | [Create early runtime-path resolution](01-runtime-paths.md) | NATIVE-00 | Any |
| [x] | NATIVE-02 | [Move durable state and logs to runtime paths](02-durable-path-consumers.md) | NATIVE-01 | Any |
| [x] | NATIVE-03 | [Move models, caches, frames, and helpers to runtime paths](03-cache-model-paths.md) | NATIVE-02 | Any |
| [x] | NATIVE-04 | [Add frozen executable bootstrap and internal roles](04-frozen-bootstrap.md) | NATIVE-03 | Any |
| [x] | NATIVE-05 | [Implement cross-platform worker ownership](05-process-lifecycle.md) | NATIVE-04 | macOS and Windows x64 evidence complete |
| [x] | NATIVE-06 | [Implement single-instance launch, readiness, browser, and logging](06-launcher-lifecycle.md) | NATIVE-05 | macOS and Windows x64 evidence complete |
| [x] | NATIVE-07 | [Make model capabilities truthful and Qwen optional](07-model-capabilities.md) | NATIVE-03 | Any |
| [x] | NATIVE-08 | [Harden on-demand model acquisition](08-model-acquisition.md) | NATIVE-07 | Any; real macOS model smoke before DONE |
| [x] | NATIVE-09 | [Expose model availability and downloads in the web UI](09-model-ui.md) | NATIVE-08 | Any |
| [x] | NATIVE-10 | [Create the PyInstaller runtime build](10-pyinstaller-runtime.md) | NATIVE-06, NATIVE-09 | macOS arm64 proof; target tasks validate other hosts |
| [x] | NATIVE-11 | [Create installed-artifact verification](11-installed-verification.md) | NATIVE-10 | macOS development host; portable harness |
| [x] | NATIVE-12 | [Package and self-sign macOS](12-macos-package.md) | NATIVE-11 | macOS Apple Silicon |
| [ ] | NATIVE-13 | [Package and sign Windows](13-windows-package.md) | NATIVE-11 | Windows x64 |
| [ ] | NATIVE-14 | [Package Linux AppImage](14-linux-package.md) | NATIVE-11 | Linux x86_64 |
| [ ] | NATIVE-15 | [Run cross-platform model and lifecycle acceptance](15-platform-acceptance.md) | NATIVE-12, NATIVE-13, NATIVE-14 | All target hosts |
| [ ] | NATIVE-16 | [Build release CI and supply-chain outputs](16-release-ci.md) | NATIVE-15 | CI target runners |
| [ ] | NATIVE-17 | [Finish release documentation and audit](17-release-audit.md) | NATIVE-16 | All evidence available |

Default to sequential execution. NATIVE-07 may run alongside NATIVE-04/05 only when
agents coordinate ownership of `scanner/api.py`, `scanner/cli.py`, `scanner/config.py`,
and process-related tests. NATIVE-12, NATIVE-13, and NATIVE-14 are intentionally
parallelizable after NATIVE-11; each owns its platform packaging files and must not edit
the shared PyInstaller spec without returning the issue to NATIVE-10.

## Checkpoints

| Checkpoint | Tasks | Safe exit state |
| --- | --- | --- |
| 0 — contract | NATIVE-00 | Baseline and shared contracts are recorded; no behavior changed. |
| 1 — paths | NATIVE-01–03 | No production support/model path depends on the checkout or CWD. |
| 2 — lifecycle | NATIVE-04–06 | Source and frozen process roles have cross-platform ownership, logging, singleton, and readiness behavior. |
| 3 — models | NATIVE-07–09 | Advertised models are executable; downloads are managed; Windows/Linux scans succeed without Qwen. |
| 4 — frozen runtime | NATIVE-10–11 | A runtime with no external Python/Node passes a portable installed-artifact suite. |
| 5 — native packages | NATIVE-12–14 | All three target artifacts install/open and pass platform smoke tests. |
| 6 — acceptance | NATIVE-15 | Real CPU/model and lifecycle matrices pass on target hosts; gaps remain visible. |
| 7 — release | NATIVE-16–17 | Signed release pipeline, provenance, docs, and final audit are complete. |

Do not proceed past a checkpoint with a `BLOCKED` prerequisite. A task that only works
on the current development host is not evidence for another OS.

## Current code map and known hazards

- `scanner/config.py` constructs global settings at import time and defaults durable and
  temporary paths to `./data`. Path resolution must occur before this import.
- `scanner/pipeline/qwen_summary.py`, `image_safety_gate.py`, and
  `siglip2_moderation.py` contain independent relative model roots.
- `scanner/api.py` launches `sys.executable -m scanner.worker`, uses `ps`, and manages
  POSIX process groups. These assumptions do not survive a Windows/frozen launch.
- `scanner/restart_service.py` and `scanner/pipeline/runner.py` use multiprocessing with
  the `spawn` context. Frozen entry points require early `multiprocessing.freeze_support()`.
- Scan workers intentionally survive an HTTP-generation restart. Preserve that behavior.
  A full application Quit must stop owned processes and leave interrupted scan state
  recoverable; it must never corrupt or silently discard queue history.
- `scanner/pipeline/visual.py` calls Qwen enrichment for flagged segments, while
  `qwen_summary.py` rejects non-macOS. NATIVE-07 owns the fallback, not packaging tasks.
- Model downloads use the pinned, integrity-checked NATIVE-08 manifest and acquisition
  service. NATIVE-09 owns protected management APIs and visible download controls.
- `scripts/build_release.py` produces wheel/sdist artifacts and stages the built frontend.
  NATIVE-10 should reuse its validation logic without leaving the wheel as the product.
- The `native/SamwiseNative` prototype is a retired client, not the new server launcher.
  Do not revive its obsolete API flow. Reusing icons or isolated platform scaffolding is
  allowed only when it does not confuse Samwise and Editarr product identities.
- Existing authentication, reverse-proxy, Base URL, restart, queue, EDL, and source
  contracts remain in force. Native work must not weaken them.

## Safety and scope rules

Read repository `AGENTS.md`. Do not add a schema migration solely to preserve v1
development data. If a task discovers that a database schema change is genuinely
required, stop before changing schema and ask the user to confirm skipping migration
and wiping the exact development database. Never wipe data without that explicit
confirmation. The parent plan expects no schema change.

Use isolated temporary data/model caches and dynamic ports for tests. Do not open the
user's browser during automated checks; use `--no-browser` or an injected browser
opener. Do not start, stop, install, or upgrade the user's real Editarr instance.

Preserve unrelated worktree changes. Do not commit generated apps/installers, model
weights, downloaded native binaries, signing certificates, credentials, databases,
private keys, `node_modules`, or local caches. Build output belongs in ignored or
temporary directories. Do not publish a release from a task session.

macOS releases are intentionally unsigned in this phase. Validate the documented
end-user local ad-hoc-sign sequence, preserve its exact commands in package metadata and
user documentation, and do not claim a production signature, notarization, stapling,
hardened-runtime coverage, or Gatekeeper acceptance. Platform signing tests for Windows
may use CI secrets only through the release environment.

Run focused checks proportional to the task. NATIVE-15–17 own broad matrices. A mock is
appropriate for external services and most inference unit tests; claims about frozen
imports, real model inference, process ownership, installers, signing, or AppImage
execution require the actual artifact/host named by the task.

## Shared definition of done

Every task must additionally satisfy all of the following:

- All task acceptance criteria are checked with evidence and no unapproved deviation.
- Focused lint/tests pass; unrelated failures are named rather than hidden or repaired
  outside scope.
- `git diff --check` passes.
- User data and live services are untouched.
- Relevant tests and documentation are updated with the implementation.
- The assigned task file contains a current handoff and its master checklist row matches
  the handoff status.
- The final response names status, files changed, verification, risks, and the next
  eligible task. `DONE` is forbidden when a required-host or real-artifact check was
  skipped.

## Required handoff record

At every interruption, blocked exit, or completion, replace the assigned task's handoff
placeholder with:

```text
Status: NOT STARTED | IN PROGRESS | BLOCKED | DONE
Last completed substep:
Files changed:
Verification run and result:
Artifact/host evidence:
Known failures or risks:
Next exact action:
Next eligible task:
Decisions or deviations from the parent plan:
User approval required:
```

Record commands and result counts, but do not paste secrets or enormous logs. If blocked,
identify the missing host, credential, dependency, approval, or failed assertion and the
smallest exact action that can resume work.
