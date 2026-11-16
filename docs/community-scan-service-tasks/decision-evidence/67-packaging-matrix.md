# CSS-67 packaging and operational evidence

October 7 successor update: CSS-68 was canceled by the user for local Docker on
Mac mini. [CSS-69](../69-pilot-and-launch-scope.md) records the selected disabled
local-only pilot, its current image/staging evidence and future open release gates.
The inventories below remain dated CSS-67 evidence, not new measurements. Its
CSS-25 audit is now [complete locally](../25-worker-request-auth.md); old open-gate
entries below do not override the later handoff or CSS-68 cancellation.

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

October 7, 2026. **Local packaging verified; open release gates remain.** No release
publication, public activation, existing database change, host reboot, live provider
action or legacy import occurred. Source baseline is
`b5964fe34dfc215dcaf1ab4bc8346f1a3eb6bb25` plus uncommitted CSS-67 changes.

The user selected **fresh deployment, no legacy import**, and **record unavailable
checks as open release gates**. They separately approved repairing the context
allowlist after its sentinel test exposed unlisted configuration admission.
Native, AMD64 and target-Mac recovery are not certified or excluded from launch.

## Artifact inventory

| Artifact | Identity and contents | Boundary |
| --- | --- | --- |
| Python wheel | Editarr 0.1.0; SHA-256 `4b7fc9570ca3e31e48152b7c05572526c2039faec900dba50cda7ce76518a465` | Local app/UI, not community operator/service |
| Source distribution | SHA-256 `74763e98c77200badf6fd889d5c6b67d690028ab6753b066208d66568ca45e38` | Built local UI and build metadata; wheel rebuild needs no Node |
| Community image | `editarr-community:local`, ID `sha256:f90026951dba76d211acb8096338938f1eb3cbd6ddccd18ba1c2c90d2ff5b092`, linux/arm64, 80,953,719 bytes | Node 24.21.0, bundled service/operator assets, SQL migrations 1-14; no scanner/media |
| Local migration resources | Frozen baseline plus Alembic revisions through `0007_community_publications` | Known-shape adoption only; recoverable pre-upgrade snapshots |
| Community formats | Artifact v1/v2, request/artifact signatures, receipt verification and original byte preservation | No schema, protocol, key or backup format change in CSS-67 |
| Local portable state | Backup age prompt helper, key rotation/recovery, approved publication/import/vote intents, setup restore and local scheduler | Encryption optional; machine-local scheduler credentials are not portable |
| Native collection policy | Existing macOS/Windows/Linux collection and inventory policy tests passed | Not a built native artifact or target certification |

Final archives remain at `/tmp/editarr-css67-release-49473e46/final`, with identical
copies in `final-repeat`. The parent directory retains the isolated wheel installation
and `installed-evidence.json`. Temporary artifacts
are reproducible verification outputs, not a published release. The embedded source
identity describes the known dirty worktree; it does not claim a committed release.

## Executable matrix

| Check | Fixture or command owner | Result / limitation |
| --- | --- | --- |
| Isolated wheel/sdist inventory and migration startup | `tests/test_migration_packaging.py` | Passed; sdist rebuild, application imports inside wheel, no operator/service/private files in wheel inventory |
| Installed initial setup restore | Existing `test_setup_restore_api.py`, copied into isolated wheel verification root | Passed full/settings, plain/encrypted, included/absent administrator and failure/competition cases; Node absent from runtime PATH |
| Installed verified import | Existing `test_community_verified_import.py` plus two public golden vectors and two schema helpers | Passed exact original bytes, attribution, reuse, review/export, uncertainty and failure recovery; no checkout application imports |
| Actual wheel command | `scripts/verify_installed_release.py` | Final installed wheel setup readiness 200, zero failures, 2.402 seconds; `legacy-wheel`/`legacy-unverified`, dependencies reused explicitly |
| Reproducibility | Two `scripts/build_release.py --wheel` runs and `cmp` | Wheel and sdist byte-identical; no cross-toolchain reproducibility claim |
| Native/platform policy | Existing native runtime, installed verifier and macOS/Linux/Windows package unit tests | Combined packaging suite 83 passed; target-native binaries not built |
| Docker context allowlist | New synthetic sentinel scratch-image test in `test/container/runtime.test.mjs` | Passed actual Docker parser; excludes scanner/native/data/media, unlisted container configuration, dependency/output and nested private files; explicitly includes operator favicon |
| Actual Compose isolation | New private synthetic disabled configuration and secrets; unique test project | Passed UID 1000, read-only root, capabilities, security options, loopback binding, CPU/memory/PID/log limits and read-only input mounts; API has no backups, maintenance no listener/secrets/environment |
| Image replacement | Disposable derivative image with a distinct image ID | Passed populated identity/BLOB/revocation preservation and verified pre-change snapshot; schema-compatible replacement only, not a database downgrade |
| Packaged migrations | Built-image fixtures for versions 1-13, failed v7 name backfill and unknown schema | All 13 upgrades passed; occupied destination refused before migration; failed upgrade leaves v7 plus recoverable snapshot; outdated/unknown server startup refused |
| Isolated snapshot restore | Separate test volume, read-only snapshot mount, no network/listener/secrets | Passed v13 copy without overwrite, backup-first upgrade to 14, exact key bytes/revocation and disabled policy; source snapshot unchanged |
| Graceful stop/hard restart/single writer | Existing container lifecycle fixture | Passed; no host/FileVault/tunnel inference |
| Enabled image workflows | CSS-66 source fixtures run against rebuilt Node 24 image | Six HTTP/SQLite journeys passed with synthetic external providers; no live ingress |
| Legacy import | User's fresh-install scope | Not applicable; no import implementation or existing-data adoption |

The expanded container suite passed four tests. The replacement check was repeated
after strengthening its distinct-image assertion and passed. Initial context test
attempts hit export-buffer limits, then the synthetic test identified a real parent
allowlist flaw. Excluding parent contents fixed it; the production build subsequently
exposed the formerly implicit favicon, now explicitly permitted. Final context/build
and lifecycle checks pass. No validation failure is being reported as a pass.

Final documentation checks passed eight documents/170 relative links, task states,
Markdown fences and whitespace. Editor diagnostics found no errors in all 12 touched
files. The final archives were rebuilt twice after the README correction and compared
byte-for-byte; the final wheel was reinstalled and its readiness verifier rerun.

## Reproduction

Run from the checkout with provisioned dependencies; fixture limits and synthetic
secrets are not production configuration:

```sh
UV_OFFLINE=1 .venv/bin/python scripts/build_release.py --wheel --out-dir /tmp/editarr-css67-release-49473e46/final
UV_OFFLINE=1 .venv/bin/python scripts/build_release.py --wheel --out-dir /tmp/editarr-css67-release-49473e46/final-repeat
cmp /tmp/editarr-css67-release-49473e46/final/editarr-0.1.0-py3-none-any.whl /tmp/editarr-css67-release-49473e46/final-repeat/editarr-0.1.0-py3-none-any.whl
cmp /tmp/editarr-css67-release-49473e46/final/editarr-0.1.0.tar.gz /tmp/editarr-css67-release-49473e46/final-repeat/editarr-0.1.0.tar.gz
.venv/bin/python -m pytest -q tests/test_migration_packaging.py tests/test_native_runtime_build.py tests/test_installed_artifact_verifier.py tests/test_macos_package.py tests/test_linux_package.py tests/test_windows_package.py
.venv/bin/ruff check tests/test_migration_packaging.py
docker compose -f community-service/compose.yaml build
pnpm --dir community-service test:container
COMMUNITY_RUNTIME_IMAGE=editarr-community:local node --test community-service/test/runtime/journeys.test.mjs community-service/test/runtime/service.test.mjs
node --test --test-name-pattern='real Compose' community-service/test/container/runtime.test.mjs
```

For a new reproduction, use a new output path rather than overwriting an evidence
directory. The black-box install used `uv venv --offline --python .venv/bin/python`,
`uv pip install --no-deps` for the wheel, then the installed verifier with explicit
`--editarr`, `--dependency-site-packages` and `--json-out` paths. Its only actual
wheel-mode check was setup readiness; it did not certify native execution or a clean
network dependency installation. The expanded packaging test supplies the isolated
restore/import evidence instead of inferring it from this readiness check.

Build host: macOS 27.0.1, Apple Silicon; Docker engine 29.4.0, Python 3.11.16,
pnpm 11.22.0, pinned Hatchling, Node 24.21.0 in image. Release-builder default
`SOURCE_DATE_EPOCH=315532800`. The default Node base was resolved during Docker
build; it is not a newly approved permanent base-digest pin or published image.
The existing local frontend 545.43 kB chunk advisory remains.

## Open release gates

| Owner | Evidence still required |
| --- | --- |
| Native packaging / CSS-69 | Real target-native artifact with tracked relocatable ffprobe and required toolchain/helper inputs; installed native migration/backup/import/setup checks. Policy/unit tests and Python wheel evidence do not satisfy it. |
| CSS-67 platform follow-up / CSS-69 | AMD64 deployment on its supported target host; base-image availability or ARM emulation is not certification. |
| CSS-67 host follow-up / OPS-02 / CSS-69 | Authorized target-Mac cold boot, FileVault unlock/login, Docker start, tunnel reconnect, independent HTTPS monitor, UPS/graceful shutdown and recorded human intervention. No host reboot/tunnel change was authorized. |
| OPS-01/02 | Real Discord callback/bot/member checks and Tunnel/Access browser/CLI/JWKS/session behavior; no production setup was performed. |
| CSS-25 | Current-route comprehensive accounting/auth audits remain in progress; packaging does not close them. |
| CSS-64 / CSS-68/69 | Retention was explicitly deferred; no destructive cleanup or replacement policy is inferred. |
| CSS-68 | Target capacity/WAL/disk-full headroom and separately authorized off-device snapshot/configuration/secret-key-history restore, downtime and recovery objectives. Same-Mac volumes are not disaster recovery. |
| CSS-69 | Explicit launch scope, disposition of every open check and separate activation authorization. No architecture/feature silently excluded. |

## Runbooks and next action

- [User connection/sharing/find/review/identity guide](../../community-user-guide.md).
- [Local release/support and artifact evidence](../../release-build.md#css-67-community-packaging).
- [Docker replacement/rollback/restore and host checklist](../../community-service-docker.md#image-replacement-and-rollback).
- [Human operator CLI and moderation](../../../community-service/README.md#operator-cli).
- [Live setup prerequisites](../operator-setup.md).

Next-state update: **CSS-69** completed the selected local-only disabled pilot.
CSS-68 is canceled, not an active follow-up. A future persistent deployment or
public rollout needs a new explicit action/scope decision using CSS-69's gate
matrix. Do not reopen deferred CSS-64 retention or activate by inference.