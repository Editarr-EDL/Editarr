# NATIVE-17 — Finish release documentation and audit

Status: **NOT STARTED**

Dependencies: NATIVE-16. Required host: documentation work may run anywhere; all target
artifact, acceptance, platform-signing-policy, and CI evidence must be available.
Primary scope: user/operator/developer documentation, final contract audit, and release
readiness report. This task does not publish the release.

## Background

The last checkpoint turns the implementation evidence into an installable product users
can understand and maintainers can reproduce. Existing release documentation emphasizes
Python wheel/sdist construction; native packages must become the primary end-user path
without removing useful developer workflows. The audit must also ensure the deferred
model/platform gaps remain visible and that no stale checkout-relative, pip-install, or
machine-service assumption escaped earlier tasks.

## Instructions

1. Update the README and release/install documentation with supported targets, native
   download selection, install/open/upgrade/uninstall instructions, browser behavior,
   per-user execution, and how to find the local URL when browser launch is disabled.
2. Document the exact default data, config, state/log, cache/model, temporary, and
   singleton/runtime locations on macOS, Windows, and Linux, including supported
   overrides and what upgrades/uninstalls preserve.
3. Document on-demand model installation/removal, expected download size/integrity and
   offline behavior, CPU expectations, disk-space failures, and recovery from interrupted
   or corrupt downloads. Do not imply that models are bundled.
4. Add troubleshooting for port conflicts, stale singleton state, browser-launch failure,
   external/network media loss, logs, safe restart versus full Quit, interrupted scans,
   AppImage FUSE/extract fallback, Windows security/signature checks, and the macOS
   unsigned-package/local-self-sign procedure (without claiming Gatekeeper notarization).
5. Make native installers the normal user installation path. Clearly label wheel/sdist,
   source checkout, and frontend build instructions as developer-only where retained;
   remove any claim that end users need pip, Python, Node.js, or pnpm.
6. Publish a maintained capability/support matrix. Explicitly retain `NATIVE-GAP-01`
   (Windows/Linux Qwen), `NATIVE-GAP-02` (closed/revised: optional backend-specific CUDA,
   CPU fallback, and host-provided drivers), `NATIVE-GAP-03`
   (additional architectures), and `NATIVE-GAP-04` (future older-macOS support), with
   user-visible effects and future acceptance gates. State macOS 26.2 as the current
   Apple Silicon floor; do not imply lower-version support until the MLX rebuild and
   verification requirements of `NATIVE-GAP-04` are complete.
7. Audit source, package metadata, tests, and docs for stale product names, CWD-relative
   production paths, writes into install roots, direct `python -m` worker assumptions,
   POSIX-only lifecycle code on Windows paths, hidden model downloads, and user-facing
   wheel/sdist artifacts. Fix documentation/audit tooling only; route product defects to
   their owning task and mark this task `BLOCKED`.
8. Review every NATIVE-00–16 handoff. Confirm statuses/checklists agree, required-host and
   real-artifact evidence exists, deviations were approved, and no blocked item was
   silently waived. Verify the four gap IDs appear in API/UI/tests/release notes where
   applicable; `NATIVE-GAP-04` is a release documentation/audit gap rather than a current
   capability API claim.
9. From a clean checkout or equivalent isolated workspace, run the full supported lint,
   backend test, frontend build/test, end-to-end test, release dry-run, and documentation
   link/check suite. Reconcile outcomes with NATIVE-15/16 evidence rather than pretending
   the current host substitutes for another platform.
10. Write a concise release-readiness report listing source revision, version, artifact
    checksums, evidence locations, supported targets/capabilities, known gaps, rollback or
    recovery guidance, and a go/no-go result. Do not publish artifacts or a release.

## Fixed decisions

- Native packages are the end-user distribution; Python packaging remains developer-only.
- Per-user launch and browser UI are the v1 native operating model; services and in-app
  updating stay deferred.
- CPU scanning is guaranteed on Windows/Linux; compatible NVIDIA hosts may additionally
  use verified CUDA for supported backends. Qwen and extra architectures remain gaps.
- A documentation edit cannot waive missing executable, host, required platform-policy,
  model, or soak evidence. No database migration or data wipe is expected or authorized.
- A `GO` verdict is prohibited while MLX Whisper, the CompVis safety checker, or the
  imageio-ffmpeg bundled executable remains `license-review-required`; each must receive
  written clearance or be removed from the release contract first.

## Acceptance criteria

- [ ] User docs cover install, first launch, URL/browser behavior, upgrade, uninstall,
      storage locations, logs, models, backup/recovery, and platform troubleshooting.
- [ ] Native artifacts are the primary user path and no user-facing instruction requires
      Python, pip, Node.js, pnpm, wheels, or sdists.
- [ ] The support matrix and release notes accurately describe all targets, capabilities,
      and `NATIVE-GAP-01` through `NATIVE-GAP-04`, including macOS 26.2 as the current
      Apple Silicon floor and the lower-floor verification required by `NATIVE-GAP-04`.
- [ ] Source/documentation audits find no unresolved native-release contract violation.
- [ ] Every prerequisite handoff/checklist is consistent and includes its required-host,
      real-artifact, applicable signing/self-sign, model, and soak evidence.
- [ ] Full backend/frontend/end-to-end/documentation checks and the non-publishing release
      dry run pass; target-only results are traceable to NATIVE-15/16 evidence.
- [ ] The release-readiness report has a justified `GO` result and exact artifact hashes,
      or the task is `BLOCKED` with a concrete `NO-GO` reason.

## Definition of done

All documentation and audit acceptance criteria pass, link and spelling/format checks are
clean, the repository's full supported test suite and `git diff --check` pass, every prior
task is `DONE`, and the release-readiness report says `GO` without an unapproved waiver.
No public release is created by this task.

## Handoff requirements

Return the readiness-report path and verdict; source revision/version; final artifact
names, hashes, and evidence locations; documentation files changed; complete verification
commands and counts; audit queries and results; unresolved risks; all three gap statuses;
and the exact separately authorized release/publish action that would follow a `GO`.

## Handoff record

```text
Status: NOT STARTED
Last completed substep: Task specification created.
Files changed: None.
Verification run and result: Not run.
Artifact/host evidence: All NATIVE-00 through NATIVE-16 handoffs required.
Known failures or risks: Final signed artifact and cross-platform evidence do not yet exist. The three `license-review-required` components in `docs/model-artifact-notices.md` are explicit `NO-GO` blockers until cleared or removed.
Next exact action: Review every completed handoff and update user/release documentation.
Next eligible task: None; this is the final checkpoint before separately authorized release.
Decisions or deviations from the parent plan: None.
User approval required: None.
```
