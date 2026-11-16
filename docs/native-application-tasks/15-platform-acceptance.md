# NATIVE-15 — Run cross-platform model and lifecycle acceptance

Status: **NOT STARTED**

Dependencies: NATIVE-12, NATIVE-13, and NATIVE-14. Required hosts: macOS Apple
Silicon, Windows x64, and Linux x86_64, using the packaged artifacts.
Primary scope: release-candidate acceptance evidence and verification-harness fixes.

## Background

Unit tests and raw frozen-runtime tests cannot prove that native installers preserve
Editarr's process, model, filesystem, and upgrade contracts. This checkpoint exercises
the actual `.app`/DMG, Windows installer, and AppImage on their target operating systems.
It also prevents the accepted first-release gaps from turning into scan failures:
Windows and Linux guarantee CPU fallback and omit Qwen enrichment. Compatible NVIDIA
hosts may additionally use CUDA for packaged backends that pass runtime detection, while
their supported detector findings must still complete and remain useful.

## Instructions

1. Create a versioned acceptance matrix that identifies each artifact by version and
   checksum and each host by OS release, architecture, relevant runtime versions, and
   graphics/CPU capabilities. Never substitute a source checkout or borrowed runtime.
2. Run the NATIVE-11 installed-artifact verifier against all three installed/package
   forms from arbitrary working directories and read-only installation locations.
3. Run representative real-media scans with real downloaded models. On macOS cover the
   complete supported pipeline, including MLX Qwen enrichment. On Windows and Linux cover
   faster-whisper, filters, NudeNet, Image Safety Gate, and SigLIP2 on CPU, plus the
   advertised CUDA path on a compatible NVIDIA host.
4. On Windows and Linux, prove that flagged visual segments retain detector findings and
   complete without Qwen. Confirm the capability API and UI disclose Qwen as unavailable,
   report CUDA only after a usable provider/driver probe, and fall back to CPU without an
   NVIDIA driver. Do not weaken or hide `NATIVE-GAP-01`, or misrepresent the revised/
   closed `NATIVE-GAP-02`, to make the matrix pass.
5. Exercise on-demand model installation for first download, progress, cancellation or
   interruption, retry, checksum/revision validation, corrupt partial recovery, low-disk
   handling, offline reuse, and explicit removal. Verify that no model writes enter the
   installed application tree.
6. On every target, exercise first launch, second launch while running, readiness/browser
   behavior, `--no-browser`, HTTP-generation restart during an active scan, full Quit
   during a scan, relaunch/recovery, sleep/wake, Unicode and space-containing media paths,
   external-media loss, network-media loss, and stale singleton/process metadata.
7. Install a previous compatible development artifact, create representative settings,
   queue history, and model cache state, then upgrade and uninstall. Verify upgrades
   preserve per-user data and uninstall does not silently erase it.
8. Run at least one 24-hour soak with queued work, restarts, and idle periods. Record
   bounded logs, peak memory/disk use, orphan-process checks, and final queue integrity.
9. Fix only defects in the portable acceptance harness within this task. Route product,
   freezer, or package defects back to the owning prerequisite task, change this task to
   `BLOCKED`, and name the failing assertion and smallest reproduction.
10. Store compact, non-sensitive evidence in the repository or CI artifacts as defined by
    NATIVE-00. Do not commit media, model weights, databases, or unbounded logs.

## Fixed decisions

- Acceptance uses actual target-host packages and real models; mocks cannot satisfy it.
- macOS validates the full supported pipeline. Windows/Linux validate the documented CPU
  baseline, optional CUDA faster-whisper path, and successful detector-only behavior when
  Qwen is absent.
- The absence of Windows/Linux Qwen is an accepted, visible gap. CUDA is additive and
  backend-specific; lack of a compatible GPU/driver must exercise CPU, not fail a scan.
- Additional architectures remain `NATIVE-GAP-03` and are outside this matrix.
- Release-candidate approval is blocked until the `license-review-required` entries for
  MLX Whisper, the CompVis safety checker, and the imageio-ffmpeg bundled executable are
  cleared or removed from the release contract. Testing may continue while review is
  pending, but this task cannot record an accepted release candidate.

## Acceptance criteria

- [ ] The installed-artifact verifier passes for the macOS, Windows, and Linux packages.
- [ ] Real model scans pass for every capability advertised as available on each target.
- [ ] Windows/Linux flagged visual scans complete with preserved detector findings,
      truthful pre-scan Qwen/CUDA disclosures, and CPU fallback without an NVIDIA driver.
- [ ] Download, interruption, corruption, low-disk, offline-reuse, and removal cases pass.
- [ ] Singleton, browser, restart, Quit/recovery, sleep/wake, path, and media-loss cases
      pass without orphaned owned processes or corrupted queue state.
- [ ] Install/upgrade/uninstall preserve per-user state according to the frozen contract.
- [ ] A 24-hour soak passes and its bounded resource/process evidence is retained.
- [ ] Every matrix cell records artifact checksum, host identity, command/test case, and
      outcome; waivers are explicit and none contradict the confirmed product decisions.

## Definition of done

All acceptance criteria pass on all required target hosts with the actual packaged
artifacts, focused harness tests and `git diff --check` pass, and evidence is sufficient
for another agent to reproduce failures. Any missing host, required real-model run, or
unresolved package/product defect makes this task `BLOCKED`, never `DONE`.

## Handoff requirements

Return the completed matrix, artifact versions/checksums, host specifications, model
revisions, scan fixtures and expected outcomes, installer/upgrade/soak results, evidence
locations, failures routed to prerequisite owners, and exact commands needed to resume.
Explicitly restate `NATIVE-GAP-01`, the closed/revised `NATIVE-GAP-02`, and
`NATIVE-GAP-03`.

## Handoff record

```text
Status: NOT STARTED
Last completed substep: Task specification created.
Files changed: None.
Verification run and result: Not run.
Artifact/host evidence: All three target-host packaged-artifact matrices required.
Known failures or risks: Real-host, real-model, and 24-hour soak evidence does not yet exist. Release approval is also blocked by the three `license-review-required` entries recorded in `docs/model-artifact-notices.md`.
Next exact action: Assemble checksummed artifacts and hosts after NATIVE-12/13/14 are DONE.
Next eligible task: None until all package prerequisites are DONE.
Decisions or deviations from the parent plan: None.
User approval required: None.
```
