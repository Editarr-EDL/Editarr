# CSS-67 — Package the features and write operational user guidance

Status: **DONE for local packaging, October 7, 2026**. The user selected fresh
deployment without legacy import and explicitly retained unavailable native/AMD64/
target-Mac cold-boot checks as open release gates. Those checks are not passed or
excluded from launch. See the [artifact/platform matrix](decision-evidence/67-packaging-matrix.md).

- Prerequisites: [CSS-06](06-migration-startup-cli.md), [CSS-07](07-version-authority.md), [CSS-19](19-backup-cli.md), [CSS-30](30-connection-settings.md), [CSS-32](32-manual-key-rotation-ui.md), [CSS-33](33-identity-recovery.md), [CSS-50](50-find-filters-downloads.md), [CSS-58](58-operator-dashboard-actions.md), [CSS-59](59-operator-cli.md), [CSS-61](61-hosted-restore.md), [CSS-62](62-automatic-donation.md), [CSS-63](63-sharing-policy-withdrawal.md), [CSS-66](66-integration-and-abuse.md), [CSS-71](71-initial-setup-restore.md)
- Decision gates: [G-OPERATIONS](decisions.md#g-operations), [G-SERVICE-BACKUP](decisions.md#g-service-backup); explicit approval for any existing-data import/cutover.
- Parent sections: implementation §2/4/5; main §9.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Local Editarr must remain installable without a source checkout. The community
service is a separate Node 24 + SQLite Docker image including its operator UI,
not a Worker deployment or part of the Python/native app. The existing
[Docker runbook](../community-service-docker.md) and October 6 local image checks
are a starting point, not target-machine/platform recovery certification.

## Starting points

scripts/build_release.py; native build inputs; README.md; docs/release-build.md;
community-service/Dockerfile, Dockerfile.dockerignore, compose.yaml;
community-service/src/runtime/ and test/container/; docs/community-service-docker.md.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Verify migration assets, codecs, version metadata and supported backup/key formats ship in Python/native/container builds.
2. Document connect/share/find/review/backup/transfer/manual rotation/recovery and operator moderation/backup/restore. Include Restore from backup during initial setup, restored administrator sign-in (creation only when credentials are absent), and passphrase prompts only for encrypted archives.
3. Publish compatibility/support-window/error guidance using approved policies; document deferred optional features accurately.
4. Verify the community image/Compose installation independently of the local app:
  Node 24, bundled operator assets and numbered migrations, non-root UID, read-only
  root, loopback port, private readable config/secrets, resource/log limits and
  separate persistent data/backup volumes. Assert no scanner/media/private configuration
  enters the build context, no backup mount enters the API, and maintenance has no
  listener or service-secret mount. Verify ARM64 and AMD64 on supported target hosts;
  unavailable Intel verification stays open or is explicitly excluded from launch scope.
5. Rehearse fresh explicit initialization, disabled health/readiness, reviewed
  configuration and separate protected configuration activation in disposable deployments.
  Verify image replacement retains data, supported upgrades require a verified
  pre-upgrade snapshot, unknown/outdated schema startup is refused, and failed
  upgrades recover without reset. Document schema-compatible image rollback and
  isolated snapshot restore; never suggest `docker compose down -v` as recovery.
6. With explicit operator authorization, test Mac cold boot, FileVault unlock/login,
  Docker engine startup, tunnel startup/reconnection, graceful stop and hard restart.
  Record whether human intervention is required; `restart: unless-stopped` is not
  proof of recovery after host reboot. Document UPS/shutdown expectations, independent
  monitoring, permissions, maintenance downtime and exact rollback steps.
7. Establish whether a legacy D1 store or CSS-60/61 archive actually needs import.
  If not, record fresh-install scope; do not invent data or an unnecessary migration.
  If required, obtain approval for source/export, downtime, destination and cutover
  before implementing a separate versioned offline import path. Existing runtime
  commands do not adopt D1 history or CSS-61 output. Require a verified recoverable
  source backup; validate known source schema/history, complete table/BLOB/signature
  inventories, foreign keys, counters, key history, revocations, moderation and
  replay/operation state in a new disabled volume. Explicit conversion may remove
  known D1-only bookkeeping only after verification; never stamp an unknown schema.
  Test failure/restart and preserve the original source/volume. Stage and rehearse
  restore/rollback using CSS-66 fixtures before a separately authorized cutover.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] An isolated Python artifact starts/migrates and runs fixture backup/import
  without checkout application imports or Node. The separate image supplies Node 24.
  Native target certification is explicitly an open release gate, not wheel evidence.
- [x] An isolated installed artifact exposes initial setup restore and completes the approved credential transition with plain and encrypted fixtures; user guidance matches that flow.
- [x] Local package contains no Discord/Cloudflare secrets or accidental operator assets requiring local auth.
- [x] Docs explain membership removal delay, shared reputation, historical signatures and unsupported recovery cases accurately.
- [x] Community image and real Compose mounts pass the local packaging/isolation matrix;
  supported architectures and exact image identity are recorded, with no secret values.
- [x] Image replacement, verified-backup-first upgrades and failed-upgrade/restore
  rollback preserve populated data; startup never auto-initializes or migrates.
- [x] Target-Mac cold boot/FileVault/Docker/tunnel recovery and required manual steps
  are tested or explicitly marked unavailable in the launch evidence; local container
  restart tests alone do not close this operational check.
- [x] Legacy import is either explicitly not applicable to the selected fresh deployment
  or has a separately approved, tested preservation/cutover/rollback procedure.

## Verification

- Run affected existing build/installed-release/native tests; mark platform-unavailable checks as unverified, not passed.
- Run Docker build, Compose configuration/isolation checks and disposable-container
  lifecycle tests. Record actual target-host cold-boot evidence separately.
- For an approved legacy import path, use isolated populated source fixtures including
  signed payloads and security history; prove interrupted import leaves the source intact.
- Review docs against route/CLI help and task evidence; no deployment or release publication here.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every local-scope acceptance criterion above is met, and relevant verification passed; unavailable target checks remain explicit release gates by user decision.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied within the recorded local scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide artifact inventory, reproducible builds and final runbook paths to CSS-69; CSS-68 canceled October 7.

## Handoff summary for the next task

DONE locally / October 7, 2026 / GitHub Copilot. Source baseline
`b5964fe34dfc215dcaf1ab4bc8346f1a3eb6bb25`; changes are uncommitted. No existing
database, service, media/model, real credential, live provider or host boot changed.

Expanded `tests/test_migration_packaging.py` inventories community/backup/key modules
and runs existing setup-restore and verified-import tests against the isolated wheel
with public fixture copies and no Node. Expanded
`community-service/test/container/runtime.test.mjs` tests actual Compose isolation,
distinct-image replacement, all 13 supported image upgrades, rollback/startup refusal,
separate-volume restore and Docker context sentinels. The sentinel check exposed a
real parent allowlist leak; `Dockerfile.dockerignore` now excludes unlisted parent
contents/private files and permits only the required public favicon.

User guidance is `docs/community-user-guide.md`; README first-run guidance is corrected.
`docs/release-build.md` and `docs/community-service-docker.md` contain release evidence,
support/error limits, exact compatible rollback, disabled new-volume restore and
host/FileVault/Docker/tunnel checklist. The matrix supplies artifact hashes/image ID,
inventory, reproduction commands and release-gate owners. No format/schema/API/dependency
or policy defaults changed.

Decisions: existing G-OPERATIONS/G-SERVICE-BACKUP boundaries; October 7 user selected
fresh deployment/no legacy import, unavailable checks retained as open release gates,
and approved the confirmed context fix. No legacy conversion, destructive reset,
paid storage, retention/schedule change or activation is authorized.

Verification: 83 focused Python packaging tests, four actual Docker packaging/lifecycle
tests and six rebuilt Node 24 image journeys passed; strengthened distinct-image
replacement passed on rerun. Ruff passed. Two real wheel/sdist builds were byte-identical;
actual isolated installed command returned setup readiness 200, no failures, explicitly
`legacy-wheel` with reused dependencies. Existing frontend chunk advisory remains.
Documentation/diagnostics/whitespace results are recorded with the final matrix checks.

Unverified: native target artifacts, AMD64 target host, target-Mac cold boot/FileVault/
Docker/tunnel, OPS-01/02 live identity/ingress, capacity/disk-full/off-device recovery
and launch. All remain open, not excluded or satisfied by local tests. Fresh scope
makes legacy import not applicable; discovery of actual legacy data reopens approval.
Snapshots preserve original schema/security history; restoring stale security facts
needs reconciliation before separately authorized activation. Never reset or downgrade
in place; retain original volumes/configuration/key history and verified snapshots.

Remaining local packaging work: none. Target-dependent follow-ups remain named release
gates. October 7 successor update: the user canceled CSS-68 for local Docker on
Mac mini. Next **CSS-69**: audit the CSS-66/67 matrices and open release gates,
record unverified-risk dispositions and obtain explicit launch scope. Keep CSS-64
deferred; do not implement the canceled audit or infer launch/activation.

Direct dependent tasks: [CSS-69](69-pilot-and-launch-scope.md).
