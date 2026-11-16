# CSS-69 — Review the pilot and obtain an explicit launch scope

Status: **DONE for the selected local-only Docker pilot**, October 7, 2026.
User selected this scope and authorized disabled disposable ARM64 staging, which
passed and was cleaned up. No public release, live setup, runtime activation or
public ingress is authorized. Open future release gates remain unverified.

- Prerequisites: [CSS-61](61-hosted-restore.md), [CSS-63](63-sharing-policy-withdrawal.md), [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md)
- Decision gates: [G-LAUNCH](decisions.md#g-launch)
- Parent sections: main D03; implementation §7–8.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The user requested a complete long-term plan before deciding the first release. Scope discussion can happen now; the prerequisites above govern final readiness evidence for the full plan, not permission to discuss a smaller release. After the user selects scope, explicitly defer excluded features and adjust final-checkpoint dependencies without dropping necessary prerequisites. Finishing these documents or coding tasks does not authorize public deployment.

October 7: The user canceled [CSS-68](68-free-tier-load-audit.md) as inapplicable
to local Docker on Mac mini and requested progression to this task. Do not implement
its benchmark/recovery harness or retain it as an active prerequisite. Record the
cancellation separately from passed evidence; independent recovery and other
unverified deployment risks still need an explicit launch disposition.

## Starting points

task index/status; pilot acceptance report; launch checklist and operator setup runbook.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Assemble a concrete pilot result and feature readiness matrix, including optional CSS-65 decision and all open gates.
2. Present launch choices and remaining risks/capacity evidence; record the user's selected boundary and deferred work.
3. Prepare exact rollout/migration/backup/rollback steps and Discord/Access setup requirements; execute live actions only within explicit authorization.
4. Audit the [Docker direction](../community-service-docker.md) explicitly: CSS-66
  real Node API matrix; OPS-01 live Discord enrollment/member/nonmember/outage checks;
  OPS-02 Tunnel Host/IP isolation and Access browser/CLI session/JWKS checks;
  CSS-67 supported images, mounts and Mac/FileVault/Docker/tunnel cold boot;
  canceled CSS-68 capacity/recovery work and remaining independent-recovery risk.
  Track each as passed, failed,
  unverified or explicitly excluded by the user, with an owner and evidence reference.
5. Record fresh-database versus approved legacy-import deployment. For import, require
  CSS-67's known-schema preservation and rollback evidence; current startup/manage
  commands cannot adopt D1/CSS-60/61 output. Keep original volumes and signing-key
  history. Require separate authorization for existing-data writes or public cutover.
6. Stage the exact reviewed image/configuration disabled, check liveness/readiness and
  restore/rollback, then record explicit permission for protected configuration activation,
  runtime activation and public ingress. Passing local tests, selecting Docker or
  owning the hostname does not authorize any of those live actions.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] No unfinished required dependency, unresolved acceptance failure or missing recovery evidence is labeled done.
- [x] Launch scope/license/operator setup/self-hosted capacity policy are explicit;
  local usage remains unrestricted and no paid resource/public path appears by assumption.
- [x] The next implementation/release task can start from a precise approved state with owners and rollback instructions.
- [x] Every Docker verification gap listed above has an evidence-backed disposition;
  mock/host unit results are never labeled live or target-Mac certification.
- [x] Fresh install or approved legacy cutover, independent recovery, supported
  architectures and cold-boot limitations are part of the explicit launch decision.

## Verification

- Audit every selected-scope task's handoff and gate decisions against the pilot matrix.
- Rehearse rollout/rollback in an isolated environment if authorized; otherwise mark that live check pending.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met for the selected disabled local-only pilot, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for the selected scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide the final readiness report, exact selected launch scope, remaining backlog and next authorized rollout action.

## Pilot Readiness Review

Reviewed October 7, 2026. This is a documentation audit of recorded predecessor
results, not a new execution of their tests. All reported API/provider identities,
secrets and databases were synthetic and disposable. The separately authorized
disabled Compose rehearsal below is new execution evidence; no live pilot has run.

### Evidence And Open Gates

| Surface | Disposition | Evidence / owner / next action |
| --- | --- | --- |
| Signed contributor/operator API workflows | Passed locally, not live | [CSS-66 matrix](decision-evidence/66-integration-matrix.md): real Node HTTP/SQLite and Node 24 ARM64 image journeys, publication/replay, catalog, downloads, receipts/votes/reports and audited moderation. OPS-01/02 own live boundaries. |
| Current-route signing and retirement fences | Passed locally, not live | [CSS-25 audit](25-worker-request-auth.md) supersedes older CSS-66/67 notes calling that audit unfinished; signatures, warm-cache membership/key fences and dropped-response hard-restart reconciliation verified. |
| Sharing permission and attribution | Passed locally | [CSS-63 handoff](63-sharing-policy-withdrawal.md): signed standing community-reuse-1, exact parent permission/author checks, original attribution, no author withdrawal; reversible operator visibility moderation preserves evidence. G-LICENSE remains unchanged. |
| Local setup restore and verified import | Passed in isolated wheel | [CSS-67 matrix](decision-evidence/67-packaging-matrix.md): installed full/settings, plain/encrypted restore and exact signed import; setup readiness 200. Reused dependencies, not a clean-install/native certification. |
| Optional local scheduled backups | Implemented; no live schedule enabled | [CSS-65](65-optional-scheduled-backups.md): configurable hours/destination/count, optional external protected passphrase file. Windows encrypted scheduling fails closed without ACL validation. This does not schedule community-service backups. |
| Docker mounts, image replacement and snapshot rollback | Passed on disposable ARM64 fixtures | CSS-67 matrix: API data-only, maintenance no secrets/listener, non-root/read-only constraints, distinct-image replacement and disabled separate-volume restore. Same-Mac recovery only. |
| Exact local pilot artifact/configuration | Passed disabled staging | CSS-69 rehearsal below: recorded current schema-16 ARM64 image ID/tag, Node 24.21.0, private synthetic disabled configuration and empty secrets; no public release artifact selected. CSS-67's historical schema-14 hashes are not this image. |
| Fresh versus legacy deployment | Fresh selected; legacy import excluded | G-SERVICE-BACKUP / CSS-67 user answer: initialize a new database only, no D1/CSS-60/61 adoption. Discovering legacy data requires renewed approval; never stamp or overwrite it. |
| Live Discord enrollment/membership | Unverified; setup partly complete | [OPS-01](operator-setup.md#ops-01--create-the-editarr-discord-community-and-application): server/application exist, callback/bot installation, member/nonmember, outage, leave/rejoin and token-rotation checks pending. Operator authorization required. |
| Tunnel and Access browser/CLI | Unverified; no setup acceptance | [OPS-02](operator-setup.md#ops-02--configure-docker-ingress-and-operator-access): exact Host/IP isolation, admin-path Access, subjects, session/revocation/JWKS and human cloudflared CLI checks pending. Selected hostname alone proves none of these. |
| AMD64 and native client releases | Unverified; open release gates | CSS-67 platform/native follow-ups and operator: real target artifacts/checks required unless explicitly excluded by a later scope decision. ARM64/wheel evidence is not certification. |
| Mac mini cold boot / FileVault / Docker / tunnel | Unverified; open release gate | CSS-67 host follow-up + OPS-02: operator-present rehearsal, intervention timings, UPS and independent HTTPS monitoring. No reboot or startup-security change authorized. |
| Target capacity, disk-full and load audit | Explicitly excluded by user cancellation | [CSS-68](68-free-tier-load-audit.md) canceled October 7 for local Docker on Mac mini. No benchmark harness, new allowances or measured launch throughput. Hardware headroom remains unknown, not failed or certified. |
| Independent snapshot/configuration/key-history recovery | Unverified risk; CSS-68 drill canceled | G-SERVICE-BACKUP / CSS-69: no independent destination, custody or recovery objectives selected. Existing same-Mac snapshot and legacy [CSS-61](61-hosted-restore.md) mock recovery are not off-device evidence. Obtain an explicit launch disposition, not a replacement drill by assumption. |
| Retention and service backup automation | Deferred October 7; retain-first completed October 8 | [CSS-64](64-retention-cleanup.md#october-8-completion) successor: indefinite durable evidence, bounded existing transient cleanup and read-only offline preview passed source/image checks. No manual-service backup pruning or new schedule; future deletion/automation/paid storage requires separate decisions. |
| Optional automatic donation and author ranking | Explicitly deferred | CSS-62 manual-only sharing; G-REPUTATION revision-only Wilson ranking after both compatibility bands, zero author boost, no author tiers or decay. Separate enrollment/publishing pauses remain deferred. |
| Activation and public ingress | Not authorized | G-LAUNCH / operator: scope selection, protected configuration activation, runtime activation and ingress permission are separate. Do not infer any from local passes or cancellation. |

Recorded evidence is not uniform: CSS-66's initial browser run was 104/106; the
affected-file rerun was 51 passed after repairs, plus 18 separate operator journeys.
CSS-67 records 83 packaging checks, four container checks, six Node 24 image
journeys and reproducible local archives. CSS-25 records later route-audit results.
These dated inventories are not additive totals or fresh certification of the
unselected launch image. Native/AMD64/host recovery were explicitly retained as
open gates in CSS-67; this review has not silently excluded them.

October 8 successor evidence: CSS-64 was explicitly reopened and retain-first
approved/completed on disposable fixtures. Its handoff records a separate fresh
Node 24 ARM64 test image and synthetic main/WAL/payload sizes; this does not replace
the October 7 pilot artifact selection or certify public/target-hardware capacity.
CSS-68 cancellation, local-only pilot scope and remaining operator/release gates
are unchanged. No existing database, runtime or ingress was activated.

### Launch Choices

**Selected October 7:** Local-only Docker pilot. The user separately authorized a
disabled disposable ARM64 rehearsal with synthetic protected configuration and
new unique project/volumes; build current image, verify health 200/readiness 503 and
disabled APIs, then remove only rehearsal resources. No existing data, actual
secrets, provider traffic, runtime activation, public ingress or CSS-68 work.
The existing Compose fixture also verifies backup-first schema-compatible image
replacement without activating either image; this remains same-Mac staging evidence.
There is no client/native release or promised public compatibility-support window
in this pilot. Existing native/AMD64/host/live/independent-recovery gaps remain
unverified for any future public release, not globally canceled or certified.

The choices presented were:

1. Local-only Docker pilot: keep public ingress and callbacks disabled, use only
   disposable synthetic workflows for any separately authorized staging. This is
   not a real Discord community pilot or public release.
2. Prepare a public community pilot: manual sharing, local-first lookup, verified
   downloads/imports, revision voting and audited operator moderation; remain
   disabled until required OPS/platform/recovery gates have explicit dispositions
   and setup/activation actions are separately authorized.
3. Readiness review only: leave launch scope undecided and runtime disabled.

Options 2/3 were not selected; public launch remains unauthorized. Existing deferrals
remain intact. Usage is unrestricted; operation validation,
signatures, membership, replay, Access/CSRF and physical Docker bounds remain.
No paid component, new retention policy or throughput commitment is proposed.

### Proposed Rollout And Rollback

Only the disabled disposable staging/image-replacement subset was executed in the
existing fixture below. All real host/provider/activation steps remain unauthorized.
The operator owns those actions; CSS-69 records scope and authorization. Use the
exact commands in the [Docker runbook](../community-service-docker.md),
not legacy archive CLI commands. Replace placeholders only after review:

1. Record selected client versions/platforms, image tag and immutable ID, architecture,
   schema 16, Compose project, new data/backup volume names and rollback generation.
   Obtain explicit permission for staging/protected-file provisioning. Keep private
   values outside tracked documents and use approved secret channels, never chat.
2. Review request/artifact/query/page/cache/lineage bounds and receipt signer/public
   history consistency. Stage protected configuration with enabled=false and
   trustedTunnel=false, without live callback/provider use. Preserve UID 1000
   readability, API data-only mounts, maintenance no secrets/listener, loopback bind
   and Compose resource/security limits. Test fixture credentials are not production.
3. For the selected fresh install, use a new Compose project/volume and the runbook's
   maintenance init --confirm only after authorization. It must refuse an existing
   database. Start the exact reviewed image disabled: /health must be 200, /ready
   and contributor/operator APIs must be 503. Record image/configuration identities.
4. Before any existing-data upgrade/replacement, stop writers, preserve image and
   volume identities, and run offline maintenance backup --confirm --destination
   /backups/NEW_PRECHANGE.sqlite. Require known-schema/integrity/FK/exact row/BLOB
   verification; never overwrite a destination. Use maintenance upgrade only for
   supported schemas 1-15 with its recoverable backup; unknown schemas stop safely.
5. With separate rehearsal approval, restore a verified snapshot into a new volume
   using the runbook's network-none, no-listener/no-secret copy and disabled Compose
   override. Verify inventories, original schema and receipt-key history; no source
   volume overwrite. Reconcile stale revocations, moderation, membership and durable
   operation/replay state before any activation. This is not off-device certification.
6. For public scope, obtain OPS-01/02 authorization for the named callback/bot,
   Tunnel route and admin-only Access policy. Complete member/nonmember/outage,
   Host/IP/direct-origin isolation, browser/CLI/JWKS/revocation/CSRF checks and the
   selected platform/host follow-ups. Preserve contributor versus operator authority.
7. Record separate permission for protected configuration activation, runtime
   activation and public ingress. Only then set reviewed enabled/trustedTunnel
   fields true, recreate the exact image, verify readiness and approved workflows,
   and open ingress. Approval to stage or discuss public scope is not activation.
8. On failure, disable/recreate and close ingress; preserve logs without secrets,
   original volumes, configuration and receipt-key history. A compatible prior image
   may reuse the unchanged schema. Otherwise restore its matched verified snapshot
   into a new disabled volume, never downgrade/stamp in place. Review security facts
   and uncertain committed operations before separately authorized reactivation.

Unresolved before public launch: public boundary/support window, exact release
artifact, owner-confirmed OPS-01/02 and platform/host dispositions, independent
recovery risk disposition, and explicit live-action permissions. Cancellation of
CSS-68 alone resolves none of these choices or authorizes replacing that task.

### Review Verification

The documentation audit initially passed 20 documents/472 relative links, new
report anchors, 76 task dependency records and git diff --check. No application
suite was rerun; the only code change adds disabled HTTP assertions to the existing
Compose fixture. Its editor diagnostics are clean. Final documentation validation
is recorded in the handoff.

### Authorized Disabled Rehearsal

Executed October 7 on the available local Docker engine, not target-Mac cold-boot
or capacity certification. Unique image retained as reproducible staging identity:

- Tag: editarr-community:css69-pilot-2cd58813-9d99-4a74-b019-55a95a30649f.
- Image ID: sha256:7000068a69a96259af89f47ff9f896a9787fe43c1f1cce9cb34c24a445bc5f20.
- Runtime: linux/arm64, Node v24.21.0, 80,950,475 image bytes; schema 16 resources
  present. This image size is not database size, capacity or provider billing.
- Synthetic configuration: enabled=false, trustedTunnel=false, requestBytes=8192,
  variables={}; secrets={}. Owner-only temporary files, read-only UID 1000 mounts.
  These values are disabled test inputs, not production validation bounds/secrets.
- New UUID-named Compose project and data/backup volumes, random loopback port;
  /health 200, /ready, /v1/identity, /admin/ and /v1/discord/callback all 503.
  No ingress/callback/provider configuration or runtime activation.
- API data-only; maintenance has no secrets/listener; read-only root, capabilities
  dropped, no-new-privileges, CPU 2/memory 2 GiB/PID 128/log limits verified. These
  unchanged resource bounds are not measured throughput/headroom commitments.
- Backup-first distinct-image replacement preserves revoked key/public bytes,
  schema 16 and legacy disabled policy rows. Both images remain runtime-disabled.
  No existing database or volume is opened; no downgrade or off-device drill.
- The fixture removes its containers, derivative image, new volumes and temporary
  files. A separate Docker query confirmed no containers for the pilot image and
  no fixture-prefixed data/backup volumes remained. Only the tagged pilot image is
  intentionally retained; no pilot server remains running.

Actual command sequence (the build's UUID is the tag above):

```sh
pilot_tag="css69-pilot-$(uuidgen | tr '[:upper:]' '[:lower:]')"
COMMUNITY_IMAGE_TAG="$pilot_tag" docker compose -f community-service/compose.yaml build community
COMMUNITY_TEST_IMAGE="editarr-community:$pilot_tag" node --test --test-name-pattern='real Compose' community-service/test/container/runtime.test.mjs
docker image inspect "editarr-community:$pilot_tag"
docker run --rm --network none --read-only --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges:true "editarr-community:$pilot_tag" node -p 'JSON.stringify({node:process.version,platform:process.platform,architecture:process.arch})'
```

Build passed; focused Compose test **1 passed / 0 failed**, approximately 4.01 s.
Image/runtime inspection and cleanup assertions passed. This does not certify a
public community pilot, supported native/AMD64 release, real Discord/Access/Tunnel,
Mac/FileVault cold boot, load/disk-full behavior or independent disaster recovery.
Those risks remain with the owners above for any separately selected public scope.

Next authorized state: no further live action. The selected local rehearsal is
complete and removed. A persistent local installation, OPS-01/02 work or public
pilot requires a new request specifying concrete resources and permissions; do
not activate the retained image or resume deferred CSS-64/CSS-68 by inference.

## Handoff summary for the next task

```text
Status / date / implementer: DONE for selected disabled local-only pilot;
  2026-10-07; GitHub Copilot. No public rollout approval.
Implemented outcome and last completed substep: Audited CSS-61/63/66/67 handoffs,
  CSS-25 current-route evidence and gates; prepared local pilot readiness matrix,
  launch choices and authorization-gated rollout/rollback steps. Retired CSS-68;
  local-only pilot selected; disabled disposable staging/image replacement passed
  and cleaned up. Exact image/configuration and open gate owners recorded above.
Files changed and relevant revision: Task briefs/index, decision register and
  affected current release guidance plus disabled assertions in the existing
  container fixture; working tree, no commit.
Contracts / APIs / schemas / migrations / fixtures introduced: None.
Decisions used (gate IDs and recorded answers): G-LAUNCH explicit CSS-68
  cancellation and local-only pilot; disabled disposable ARM64 staging authorized.
  G-SERVICE-BACKUP fresh/no legacy import; prior open platform/host gates and
  G-RETENTION deferral preserved. Public launch remains unauthorized.
Verification commands and results: Separately tagged Compose build and focused
  node --test --test-name-pattern='real Compose': 1 passed; Node 24.21.0/ARM64
  identity, cleanup and fixture diagnostics passed. Final Node documentation
  assertions: 25 documents, 549 relative links, report anchors and 76 task
  dependency records passed; git diff --check passed. An initial overly literal
  scope-wording assertion was corrected and rerun; no runtime failure.
Known failures, limitations, unavailable checks, and recovery notes: No live pilot,
  provider setup, target-native/AMD64/cold-boot or off-device certification. No
  existing runtime/database/configuration changes, activation or public ingress.
  Only new synthetic disposable staging was executed; no server left running.
Remaining work in this task: None for selected local-only scope. All future public
  live/platform/recovery gates remain open, not DONE or silently excluded.
Next task ID and exact first action: User-selected operational follow-up or
  future public-scope CSS-69 review; first obtain explicit action/resource scope.
  OPS-01/02 and CSS-64 remain open/deferred; CSS-68 remains canceled.
```

Direct dependent tasks: the user-approved rollout or remaining backlog.
