# NATIVE-12 — Package and self-sign macOS

Status: **DONE**

Dependencies: NATIVE-11. Required host: supported macOS on Apple Silicon. Primary scope:
`Editarr.app`, lifecycle surface, unsigned DMG, end-user self-sign UX, and macOS install
tests.

## Background

The macOS deliverable is one normal application, even though an `.app` contains private
runtime files. It must run outside App Sandbox to scan user-selected local, mounted, and
network media. MLX, Torch, ONNX, Python extensions, FFmpeg, and the executable are nested
code. Under the current product policy, Editarr distributes that bundle unsigned and the
end user ad-hoc-signs it locally before launch.

## Instructions

1. Use the bundle identifier/version/icon contract from NATIVE-00. Do not reuse the
   obsolete `com.samwise.*` identity. Ship the bundle and DMG unsigned; no Developer ID
   identity, notary profile, hardened-runtime entitlement, stapling, or Gatekeeper claim
   is part of this phase.
2. Wrap NATIVE-10's runtime as `Editarr.app` with correct `Info.plist`, resources,
   executable permissions, minimum macOS version, architecture declaration, and no App
   Sandbox entitlement.
3. Provide the contracted user lifecycle surface (menu-bar agent or equivalent) for Open
   Editarr, Open Logs, status/start/stop, Launch at Login if included in NATIVE-00, and
   Quit. It must call NATIVE-06's adapter and never duplicate server logic or obsolete
   native-client APIs.
4. Ensure Finder first launch and reopen events follow singleton/readiness/browser rules.
   Normal launch must not show a terminal or create a Dock window unless contractually
   intended.
5. Package an unsigned DMG and include an exact, local-only activation procedure: copy
   `Editarr.app` to `/Applications`, clear only its quarantine attribute, ad-hoc-sign it
   with `codesign --force --deep --sign -`, then run strict signature verification. The
   package inventory and user-facing macOS README must carry the same commands.
6. Exercise that self-sign procedure against a current copied package without a terminal
   window from the app itself. Gatekeeper/quarantine acceptance, notarization, stapling,
   and a clean-user `/Applications` installation are explicitly outside this phase.
7. Preserve Application Support and model cache on ordinary app replacement/removal;
   document explicit manual data removal.

## Fixed decisions

- Target is Apple Silicon only for the first release.
- The app is per-user, non-sandboxed, browser-based, and self-contained.
- Model weights remain outside the app and download on demand.
- The first Apple Silicon release supports macOS 26.2 and later.
- Distribution is unsigned. End-user local ad-hoc signing is required before first launch;
  it is not a publisher identity, notarization, hardened-runtime, or Gatekeeper claim.
- Do not revive the Samwise client as the application UI.

## Acceptance criteria

- [x] A current unsigned DMG contains the contracted `Editarr.app`; its package inventory
      declares unsigned end-user-self-sign-required distribution and no production claim.
- [x] Applying the documented end-user ad-hoc-sign sequence to a copied app makes the
      bundle and nested code pass strict signature verification.
- [x] The NATIVE-11 verifier passes against that self-signed bundled app.
- [x] Reopen/menu actions/restart/Quit/login item behave as contracted.
- [x] The package contains no durable state and the copied verifier proves runtime
      immutability, so ordinary app replacement/removal cannot delete durable data;
      NATIVE-15 owns the real upgrade/removal acceptance matrix.

## Definition of done

An unsigned package, strict verification after the documented end-user self-sign sequence,
the NATIVE-11 verifier (including immutable runtime evidence), lifecycle evidence, focused
tests, and `git diff --check` pass. Real MLX/Qwen and broad upgrade/removal acceptance
remain owned by NATIVE-15. Developer ID
credentials, notarization, stapling, `spctl`, and clean-user Gatekeeper evidence are not
requirements and must not be claimed.

## Handoff requirements

Record macOS/build versions, bundle ID, minimum OS, architecture, unsigned distribution
state, exact end-user self-sign commands, DMG hash/size, verifier/lifecycle results, and
the boundary that no publisher-signing or Gatekeeper claim is made.

## Handoff record

```text
Status: DONE
Last completed substep: Replaced the former Developer ID/notarization release policy with
the approved unsigned bundle/DMG and end-user local ad-hoc self-sign path. Packaging now
fails closed if its output already passes strict verification before user action.
Files changed: `scripts/package_macos.py`; `tests/test_macos_package.py`;
`native_build/macos/README.md`; `docs/native-application-plan.md`;
`docs/native-application-tasks/README.md`; this file;
`docs/native-application-tasks/16-release-ci.md`; and
`docs/native-application-tasks/17-release-audit.md`.
Verification run and result: `UV_CACHE_DIR=/private/tmp/samwise-native12-selfsign-uv-cache
uv run ruff check scripts/package_macos.py tests/test_macos_package.py` passed. The same
cache with `uv run pytest -q tests/test_macos_package.py
tests/test_installed_artifact_verifier.py tests/test_cli.py` passed (65 tests). A fresh
regular macOS arm64 runtime was built with `scripts/build_release.py --target macos-arm64`
and default-packaged at `/private/tmp/editarr-native12-selfsign-release-package`; the
fake-model guard prevents a fake-model runtime from being packaged without the explicit
test-only flag. Its package guard confirmed strict verification fails before user action.
Applying the documented `xattr -dr com.apple.quarantine`, `codesign --force --deep --sign
-`, and `codesign --verify --deep --strict` sequence to the regular copied app passed;
`hdiutil verify` passed. Separately, the marker-gated test package at
`/private/tmp/editarr-native12-selfsign-package-r2` was self-signed and passed the final
isolated NATIVE-11 verifier in 11.487 seconds with `failures: []`, covering copied
Unicode/space paths, immutable runtime, bundled UI, setup/login/CSRF/logout, Base URL,
restart and occupied-bind recovery, singleton, full Quit/relaunch, and fake worker
cancellation/reconciliation.
Artifact/host evidence: Apple Silicon macOS 27.0 host; `org.editarr.app`, version `0.1.0`,
`LSUIElement=true`, non-sandboxed, arm64-only, and `LSMinimumSystemVersion=26.2`. Fresh
regular unsigned-policy package inventory: app tree SHA-256
`6c0144dd73483a98d99d2717d847b4e6c6941ba2d8e99b9b1829b7bf33d0eac4`, 2,081,611,995
bytes; DMG SHA-256 `1ce67258a75626a25730fc16e4d497273a1c0d20e4a81ae9080a0b81ee31b0f7`,
747,395,006 bytes; runtime inventory SHA-256
`ea2a0504668abc540982a6ff6af5fbc6289fba5c7a2131a405d7e039de3828ee`; source revision
`f478e9f849e9d290c7532f33439c2153c90dc19b`. The generated artifacts and 5.2-KiB fake
verifier JSON at `/private/tmp/editarr-native12-selfsign-r2-verifier-evidence.json` are
disposable, uncommitted local evidence.
Known failures or risks: This intentionally does not claim a publisher identity, Developer
ID signature, hardened-runtime coverage, notarization, stapling, or Gatekeeper acceptance.
The end-user action is a local ad-hoc signature, not proof of publisher provenance.
Real MLX/Qwen model acquisition/inference, clean-user upgrade/removal, and broad lifecycle
acceptance are NATIVE-15 responsibilities. `NATIVE-GAP-04` still requires rebuilt/replaced
MLX objects and repeat installed-package self-sign plus real MLX/Qwen checks before any
older macOS floor is supported.
Next exact action: NATIVE-13 and NATIVE-14 remain the independent package prerequisites;
when all three are DONE, NATIVE-15 runs the real-model and broad target-package matrix.
Next eligible task: NATIVE-13 or NATIVE-14 (NATIVE-15 remains blocked by them).
Decisions or deviations from the parent plan: Per the user's confirmed current policy,
macOS distribution is unsigned and end-user self-signing is required. This is an approved
product decision, not a signing-credential waiver. Windows signing policy is unchanged.
User approval required: None.
```
