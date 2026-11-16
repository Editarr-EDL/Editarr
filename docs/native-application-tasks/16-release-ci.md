# NATIVE-16 — Build release CI and supply-chain outputs

Status: **NOT STARTED**

Dependencies: NATIVE-15. Required hosts: protected CI runners for macOS Apple Silicon,
Windows x64, and Linux x86_64.
Primary scope: release workflows, macOS unsigned/self-sign distribution integration, manifests, SBOMs,
checksums, provenance, and retained verification evidence.

## Background

Native artifacts must be built on their target platforms, from the same source revision,
without relying on a developer machine. The release pipeline must preserve macOS's
intentional unsigned/self-sign distribution policy and distinguish it from protected
Windows signing. It must not turn the
existing wheel/sdist process into the user-facing product, leak signing material, bundle
model weights, or publish an artifact that has not passed the installed acceptance gate.

## Instructions

1. Define a release manifest and immutable artifact naming convention containing Editarr
   version, target OS, architecture, package type, source revision, and signing state.
   Record toolchain and dependency-lock identities needed to reproduce each build.
2. Add CI jobs that build the locked React production assets once per release source
   revision, build/freeze natively for each target, and assemble DMG, Windows installer,
   and AppImage using NATIVE-12/13/14 entry points. Do not cross-compile native runtimes.
3. Run unit/lint/frontend checks, raw frozen-runtime verification, package validation, and
   NATIVE-11 smoke checks before an artifact can reach Windows signing or release staging. Reuse
   NATIVE-15 evidence or run an appropriately bounded release-candidate subset; never
   replace the target-host acceptance gate with source-only tests.
4. Build macOS releases unsigned, record that state in the manifest, and validate the
   documented end-user local ad-hoc-sign sequence on a copied app. Do not configure or
   claim macOS code signing/notarization/stapling/Gatekeeper validation in this phase.
   Configure protected Windows code-signing jobs instead. Secrets must enter only through
   the CI secret store, must not be printed or persisted in artifacts/caches, and must
   never be available to untrusted pull request code. Linux checksum/signature keys follow
   the release policy from NATIVE-14.
5. Make pull-request/manual builds non-publishing and differently named. Production
   release jobs must require the protected release environment and an immutable
   version/tag-to-source check; macOS remains unsigned under both paths.
6. Generate SHA-256 checksums, a machine-readable release manifest, license/third-party
   notices, SBOMs for the frozen Python/native runtime and web assets, and supported
   provenance/attestation for every distributable artifact. Include the model manifest
   metadata, but never download or include model weights.
7. Ensure release notes are derived from the capability matrix and explicitly include
   `NATIVE-GAP-01` (Windows/Linux Qwen), `NATIVE-GAP-02` (closed/revised: optional
   backend-specific CUDA with CPU fallback and host-provided drivers),
   `NATIVE-GAP-03` (additional architectures), and `NATIVE-GAP-04` (future older-macOS
   support) until their owning work removes them. Release notes must state that macOS 26.2
   is the current Apple Silicon floor and that lowering it requires rebuilt MLX binaries
   plus renewed Mach-O, installed-package self-sign, and real MLX/Qwen evidence.
8. Add cache keys that include lockfiles, platform, architecture, Python/runtime version,
   and build-tool version. Cached output must not bypass tests, signing, checksum, SBOM,
   or provenance generation.
9. Add a dry-run/staging mode that produces and verifies all non-secret outputs without
   publishing. Publishing must be a separate protected step and is not authorized by this
   task; test the workflow without creating a public release.
10. Document credential names, protection requirements, certificate/key rotation, local
    unsigned reproduction, CI retention, retry behavior, and how to invalidate a bad
    staged artifact. Do not record any secret value.

## Fixed decisions

- Target-native builds are mandatory; one source revision feeds all platform jobs.
- Windows signed installers are the update mechanism when signing authority is available.
  macOS currently distributes an unsigned bundle that the end user signs locally. In-app
  self-update remains deferred.
- Wheels and sdists may remain developer artifacts but are never attached as end-user
  native releases or described as the normal installation path.
- This task stages and validates release outputs; it does not publish a public release.

## Acceptance criteria

- [ ] CI builds the three correctly named native artifacts on their target runners from
      one immutable version/source revision and without external Python/Node at runtime.
- [ ] Tests and installed-artifact verification gate Windows signing and all staging.
- [ ] The macOS release is visibly unsigned and carries the exact end-user self-sign
      commands; protected Windows signing jobs cannot expose credentials to untrusted
      changes, and logs/artifacts contain no secret material.
- [ ] Every artifact has a checksum, release-manifest entry, license notices, SBOM, and
      provenance/attestation bound to its exact bytes and source revision.
- [ ] Non-publishing artifacts are visibly distinct and cannot publish.
- [ ] Release notes truthfully preserve the open gaps and the closed/revised
      `NATIVE-GAP-02` CUDA contract.
- [ ] A full non-publishing dry run succeeds, or Windows production signing alone is
      explicitly `BLOCKED` with exact credential/protection requirements and all other
      work complete.
- [ ] No installer, model weight, credential, or transient build cache is committed.

## Definition of done

Workflow/config lint, project tests, a complete non-publishing target matrix, checksum/
SBOM/provenance verification, secret-safety review, and `git diff --check` pass. The
release workflow is documented and repeatable from a clean source revision. Missing
Windows signing credentials may block its protected-signing evidence but must not be
papered over; the task cannot be `DONE` until the acceptance criteria's required protected
path is met. No macOS signing credential is required under the current policy.

## Handoff requirements

Record workflow/job names, runner images and architectures, toolchain versions, cache and
artifact keys, artifact names/hashes/sizes, gate results, SBOM/provenance locations,
credential variable names and protections (never values), dry-run identifier, retention,
known nondeterminism, and the exact protected action needed to stage a release.

## Handoff record

```text
Status: NOT STARTED
Last completed substep: Task specification created.
Files changed: None.
Verification run and result: Not run.
Artifact/host evidence: Protected CI target-runner dry run required.
Known failures or risks: CI runner and signing-secret availability are not yet proven.
Next exact action: Inventory current CI and package entry points after NATIVE-15 is DONE.
Next eligible task: None until NATIVE-15 is DONE.
Decisions or deviations from the parent plan: None.
User approval required: None.
```
