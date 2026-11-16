# CSS-68 — Measure self-hosted capacity and recovery headroom

Status: **CANCELED / RETIRED** by explicit user decision October 7, 2026.

The user stated that this task does not apply to the local Docker deployment on
a Mac mini and requested cancellation and progression to CSS-69. No benchmark
harness or recovery drill is required under this task. The brief below is retained
as historical scope, not active work or evidence of completed measurements.
There are no active prerequisite or dependent edges. Cancellation does not certify
capacity or recovery, change retention policy, or authorize service activation.
CSS-69 records the selected launch scope and the disposition of unverified risks.

- Historical prerequisites: [CSS-39](39-publication-service.md), [CSS-41](41-catalog-lookup-api.md), [CSS-42](42-artifact-download-service.md), [CSS-51](51-import-receipts.md), [CSS-53](53-ranking-policy.md), [CSS-60](60-hosted-backup.md), [CSS-61](61-hosted-restore.md), [CSS-64](64-retention-cleanup.md), [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md)
- Historical decision gates: [G-LIMITS](decisions.md#g-limits), [G-OPERATIONS](decisions.md#g-operations), [G-SERVICE-BACKUP](decisions.md#g-service-backup)
- Parent sections: main §4; implementation §8.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Runtime/storage contract: [Docker on a Mac mini with SQLite](../community-service-docker.md).

Measure the actual Node 24 container and target Mac, not compliance with a Workers
10 ms CPU or D1 free-database ceiling. User usage is unrestricted under the October
7 local-service decision; do not introduce admission allowances. CSS-60/61 supply legacy archive/security
inventories, not proof of current SQLite/off-device recovery. Retention and provider
accounting deferrals remain in force; scope exclusions need explicit decisions.

## Starting points

community-service benchmark scripts; decision-evidence/ capacity report; operator configuration.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Benchmark realistic signed lookup/upload/download/replay, membership refresh,
  operator work and maintenance in the built image. Record target hardware/image,
  CPU, RSS/peak memory, event-loop responsiveness, concurrency, latency percentiles,
  throughput, query counts and logical writes; unknown scanned-row counts stay unknown.
2. Measure SQLite main/WAL/SHM files, indexes, payloads, auth/receipt/operation growth,
  snapshot size and temporary/free-disk requirements separately. Test maximum accepted
  artifacts, sustained traffic and restart/checkpoint behavior within Compose limits.
  Use disposable constrained storage to test disk-full, OOM/termination and recovery;
  never exhaust the operator's actual disk.
3. Verify repeated user actions remain allowed under high recorded activity and
  across restarts. Test explicit disabled configuration and atomic rollback on real
  storage failures. Preserve durable operations/replay state and passive observations.
  Distinguish application observations from retired Workers/D1 billing labels.
  Record Tunnel/Access/Discord constraints separately; provider traffic needs approval.
4. Obtain an explicit independent backup destination, protection/key custody, acceptable
  downtime and recovery objectives before an off-device drill. Use the current offline
  verified SQLite snapshot plus protected configuration/secrets and receipt-key history.
  Restore on an isolated target with public ingress and callbacks disabled, validate
  all inventories/signatures/security facts and measure recovery time/lost-work window.
  Reconcile stale revocations, moderation and operation history before any
  approved activation. A same-Mac backup volume is not disaster-recovery evidence.
5. Report blind spots and operational hardware headroom from measurements. Backup
  schedules/retention, new admission policy, paid components or alternate storage require their
  own decisions; do not implement them merely to make the capacity test pass.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [ ] Report separates target-Mac/container measurements, host unit estimates and
  unknowns; payload bytes are not mislabeled as total disk usage or provider billing.
- [ ] Representative worst cases fit available CPU/memory/disk and per-operation
  validation bounds, or produce a concrete hardware-capacity decision.
- [ ] Repeated valid actions are not refused based on recorded usage; explicit runtime
  disable and essential recovery remain usable, without validation bypass.
- [ ] The report accounts for BLOBs, indexes, WAL/checkpoints, operational/receipt growth,
  image/runtime resources and independent backup storage, with a measured headroom plan.
- [ ] An authorized off-device SQLite/configuration/key-history restore meets approved
  recovery objectives, or missing destination/authorization/hardware remains explicitly
  blocked in CSS-69. Legacy D1 mock restores and same-volume snapshots do not satisfy it.

## Verification

- Exercise high recorded activity and constrained disposable storage; failed
  publication transactions must leave no payload, byte observation or published result.
- Record hardware/OS/Docker/Node/image identity, load shape, CPU/memory/latency/query
  measurements and SQLite/WAL/payload/backup sizes separately. Record any remaining
  provider-documentation check dates without claiming provider-verified billing.
- Preserve the recovery drill's backup verification, protected inventory (no secret
  values), isolated target, measured timings, failure results and rollback evidence.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [ ] Every acceptance criterion above is met, and relevant verification passed.
- [ ] Applicable decision gates have explicit recorded answers; no invented product policy.
- [ ] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [ ] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [ ] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide measured launch capacity, approved limits and any blocking cost/scope decisions to CSS-69.

## Handoff summary for the next task

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

```text
Status / date / implementer: CANCELED / RETIRED; 2026-10-07; GitHub Copilot.
Implemented outcome and last completed substep: Recorded user cancellation for
  local Docker on Mac mini and removed active dependencies. No implementation.
Files changed and relevant revision: This brief, task index, decisions and CSS-69;
  working tree, no commit.
Contracts / APIs / schemas / migrations / fixtures introduced: None.
Decisions used (gate IDs and recorded answers): G-LAUNCH records explicit CSS-68
  cancellation, not launch approval. Other policies remain unchanged.
Verification commands and results: Documentation validation recorded in CSS-69.
Known failures, limitations, unavailable checks, and recovery notes: No target-Mac
  load or off-device recovery evidence was produced; do not claim certification.
Remaining work in this task: None. Reopening requires renewed user approval.
Next task ID and exact first action: CSS-69; audit predecessor evidence and open
  gates, prepare the launch readiness matrix, then obtain explicit launch scope.
```

Direct dependent tasks: None. Next task: [CSS-69](69-pilot-and-launch-scope.md).

