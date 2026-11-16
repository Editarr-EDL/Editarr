# CSS-66 — Verify the complete sharing and recovery workflows

Status: **DONE locally with synthetic providers, October 7, 2026**. Real Node HTTP/SQLite,
built Node 24 image, local import/restore and browser checks are recorded in the
[workflow evidence matrix](decision-evidence/66-integration-matrix.md). Live ingress,
platform packaging, capacity and activation remain their named owners’ checks.

- Prerequisites: [CSS-20](20-backup-settings-ui.md), [CSS-30](30-connection-settings.md), [CSS-32](32-manual-key-rotation-ui.md), [CSS-33](33-identity-recovery.md), [CSS-35C](35c-audio-track-selection-ui.md), [CSS-50](50-find-filters-downloads.md), [CSS-54](54-feedback-ui.md), [CSS-58](58-operator-dashboard-actions.md), [CSS-62](62-automatic-donation.md), [CSS-63](63-sharing-policy-withdrawal.md), [CSS-71](71-initial-setup-restore.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §8.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Unit checks do not prove multi-installation identity, replay safety or data durability. This task validates integrated behavior using isolated fixtures, not real personal libraries.

October 5 deployment target: [Node 24 + SQLite in Docker](../community-service-docker.md).
The October 6 review passed 599 legacy-harness tests, 27 Node tests, a Docker build
and Node 24 publication/crash/backup checks. CSS-66 now adds the API-family
journeys, local import bridge and enabled-image restart evidence below. The October 7
tooling update runs all shared regressions on Node/SQLite, but does not substitute them for the
Docker matrix below. Live Discord/Access/Tunnel checks belong to OPS-01/02 and
must be recorded separately from synthetic integration evidence.

## Starting points

tests/ community integration; community-service/test/runtime/ and test/container/;
community-service/src/runtime/; web/e2e/; test scenario documentation.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Run two installations with distinct Discord accounts, plus same-account copies under the approved key policy, through enrollment/donation/discovery/import/review/vote.
2. Exercise manual rotation, transfer/restore through both plain and encrypted full/settings backups, membership loss, moderation, replay and partial network/storage failures. Verify default plain export and no passphrase prompt for plain restore.
3. Verify API/local/operator security boundaries, privacy, uncertainty review and compatibility-first ranking.
4. Restore from initial setup through the real authorization boundary in all four backup kind/protection combinations. Verify restored administrator sign-in, creation only when credentials are absent, competing setup/restore requests and the closure of setup-only access after completion.
5. Exercise multi-track selection through AI execution, provenance/backup, exact donation preview/signature and independent channel lookup. Verify per-file/default selection, stream reorder/mutation, same-video/different-audio and the reverse, language/description retention, mixed-review non-mutating exclusions and historical unknown-track review. No unscanned identified track or alternate-track inventory reaches the contribution.
6. Extend the existing runtime/container fixtures to run real Node HTTP and SQLite
  for enrollment and profile changes, membership freshness/outage/rejoin, rotation,
  revocation and lost-key recovery, availability/match pagination, artifact/receipt-key
  downloads, import acknowledgments, votes/reports, operator dashboard/moderation
  and the human operator CLI. Mock external providers, not the HTTP or database
  adapters. Verify exact BLOBs, current authority, isolation, counters and idempotence.
7. Exercise the built Node 24 image through validated enabled configuration as well
  as its disabled defaults. Test wrong/missing Host and trusted-IP metadata,
  duplicate security headers, fixed-length/chunked body limits, signed-byte tampering,
  cookies/redirects, operator assets, JWT/CSRF rejection, stale caches and lost responses.
  Use synthetic secrets and disposable volumes; do not enable an existing service.
8. Test concurrent mutations, transaction/trigger rollback, graceful shutdown,
  hard termination/restart, second-writer refusal and restart-persistent nonce,
  operation and moderation state. Verify the hourly cleanup invocation and
  failure accounting, not only direct calls to the shared scheduled handler; its
  scope remains existing transient cleanup, not unapproved CSS-64 retention.
9. Maintain a per-workflow evidence matrix distinguishing Node unit, Node source,
  built-image and live-ingress coverage. Feed missing production checks to
  OPS-01/02 and CSS-67/68/69 rather than marking them passed by mock equivalence.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Every required matrix row has an executable test or a specifically documented environment-dependent check.
- [x] Server/local failures preserve completed work, identity and attribution; no extra counts or automatic rotation/sharing.
- [x] Responsive/keyboard/operator flows pass and existing scan/review/EDL/MCF workflows remain intact.
- [x] Initial setup restore preserves identity and the approved administrator outcome; failure/retry leaves usable setup, and stale or competing requests cannot bypass authentication.
- [x] Actual analyzed-track identity survives end-to-end; channel mismatches and unknown history remain explicit and saved reviews are unchanged by submission exclusions.
- [x] Every supported service API family above passes over real Node HTTP/SQLite,
  including an enabled built-image journey and failure/retry cases; exact signatures,
  BLOBs, authorization, counters and operation outcomes agree with shared contracts.
- [x] Restart, concurrent writes and bounded scheduled cleanup preserve durable
  security/accounting facts; repeated valid requests remain allowed across restarts.
- [x] Live provider/ingress checks are identified by OPS owner and status, not claimed
  complete from mocked JWT, membership or tunnel headers.

## Verification

- Run focused backend, Node unit, Node runtime, built-image, component and browser
  suites; reuse existing fixtures and repeat only failures or changed paths.
- Include `pnpm --dir community-service test:runtime`, Docker build,
  `pnpm --dir community-service test:container`, and the Node 24 image invocation
  documented in the Docker runbook. Record actual image identity and architecture.
- Record exact commands/results and any pre-existing failure separately; unresolved regression blocks completion.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide an evidence matrix with fixture IDs, failing/passing scenarios and reproduction commands to CSS-67/68/69.

## Handoff summary for the next task

DONE locally / October 7, 2026 / Codex. Changes remain uncommitted; pre-existing
CSS-65 changes were preserved. No existing database, running service, real identity,
media/model or deployment was changed.

The [evidence matrix](decision-evidence/66-integration-matrix.md) supplies fixture IDs,
exact commands/results and separate source/image/local/live coverage. New focused
`community-service/test/runtime/{journey,identity,sharing,operator,cleanup,restart}.fixture.mjs`
and `journeys.test.mjs` exercise every supported contributor/operator API family
through real sockets and file-backed SQLite. Image tests use the built service
modules; restart tests launch the actual enabled server executable. Synthetic
Discord/Access responses are the external boundary. `tests/community_node_import.py`
verifies actual download bytes, commits/reuses them in two local stores and explicitly
creates/exports reviews before receipt acknowledgment.

`src/runtime/maintenance.mjs` and server wiring preserve hourly transient cleanup,
prevent overlap and drain shutdown. `maintenance.test.mjs`, actual scheduled-handler
fixtures and raw HTTP checks cover failure accounting, bounded cleanup, exact headers
and chunked bodies. Browser verification found and repaired a scoped backup sign-in
link contrast defect and an outdated audio-selection scan fixture. No API/schema/
artifact/backup format changed; usage admission is retired; retention is unchanged.

Verification: 599 shared tests, 34 source runtime tests, six built-image journey/
publication tests, one disposable container lifecycle/verified-backup test, 512
focused Python tests and 297 frontend unit tests passed. Initial browser run 104/106;
all 51 tests in affected files passed after both repairs. Eighteen operator browser
journeys passed. Docker build, TypeScript, frontend build, Python style and final
documentation/whitespace checks passed. Existing bundle-size advisory remains.
Image identity, architecture, test fixture limitations and reproduction commands
are in the matrix rather than inferred from workerd passes.

Decisions reused: current Docker direction, G-KEYS/RECOVERY, G-MEMBERSHIP,
G-BACKUP/SETUP-RESTORE, G-MATCHING, G-REPUTATION and G-OPERATIONS. No new policy
choice was needed. CSS-62 automatic sharing, CSS-64 retention, separate feature
pause controls and public activation remain deferred. CSS-25 stay open for their
own comprehensive audits. Live provider/ingress checks remain OPS-01/02; native/
AMD64/Mac cold boot remain CSS-67, capacity/off-device recovery CSS-68 and launch
selection CSS-69. Wrangler is retained solely in development tooling; the running
Docker image does not require it. See the matrix's dependency note.

Recovery: Fixtures use new private directories/volumes, remove only their own
artifacts, and never migrate an existing user DB. Real offline upgrades/backups
retain the existing verified-snapshot contract. Service crash tests preserve nonce,
operation and moderation state while proving repeated valid requests remain allowed. No new restore/delete/reset command is introduced.

Remaining CSS-66 work: none within the isolated integration scope. Next **CSS-67**:
read its brief, this matrix and the Docker runbook; begin packaged local migration/
asset verification and image/platform checks. Do not infer authorization for an
existing-data import, live provider setup or public activation.

Direct dependent tasks: [CSS-67](67-packaging-and-user-docs.md), [CSS-69](69-pilot-and-launch-scope.md).
