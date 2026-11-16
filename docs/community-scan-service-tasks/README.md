# Community scan service implementation tasks

Current deployment decision, October 5, 2026: **Docker on a Mac mini, Node 24 + SQLite**,
including exact signed artifact BLOBs and the operator UI. Read the
[Docker runtime and recovery contract](../community-service-docker.md). It supersedes
Workers/D1 deployment, not signing, identity, moderation or retention policy; usage admission is retired.
The [D1 storage record](d1-storage-scope.md) is historical architecture evidence;
no bucket or automatic D1/archive transfer is required or authorized.

Status: **Planning complete; per-task implementation status is recorded below.
Docker direction and local-only pilot verification updated October 7, 2026. Unanswered product questions remain
explicit decision gates.** Originally prepared September 26, 2026.

This pack breaks the full [community service plan](../community-scan-service-plan.md)
into **71 primary work items plus the approved CSS-24A and CSS-35A/B/C subtasks**
(CSS-40 is retained only as a retired record), sized as focused assignments for a Terra- or Sol-level
coding agent with the stated prerequisites available. Most are coding tasks; encoding
and backup experiments, baseline capture, capacity review and launch selection are
explicit preparation/review tasks. This is the full long-term backlog, not a selected
first-release scope or time estimate.

The user can choose launch scope now that the full backlog is reviewable; they do not
need to implement every task before making that choice. Final integration/release
checkpoints list full-plan dependencies until a scope is selected. After selection,
record explicit deferrals and adjust those checkpoint dependencies to the selected
features while retaining their necessary safety/authentication/backup prerequisites.

## Start here

1. Read [shared context, repository map and definition of done](context.md).
2. Read [decision gates](decisions.md). Major choices require an explicit user answer.
3. Pick one task whose dependency handoffs are complete; read its named parent sections.
4. Work only that task, verify its acceptance, and write its on-disk handoff summary.
5. Mark DONE here only after that summary and all required evidence are complete.

For runtime/storage work, read the Docker contract before historical predecessor
evidence. Task IDs and completed Worker/D1 handoffs remain stable: they prove the
shared mechanisms, not Docker deployment readiness. CSS-60/61 remain legacy D1
archive tooling; use Docker offline maintenance for current service backups/upgrades.
Do not mark the release checks complete from predecessor mock/host unit results.

Every active task file includes context, source sections, code starting points, dependencies,
decision gates, work boundaries, acceptance criteria, targeted verification, definition
of done, and a required handoff for its dependents. A fresh agent should not need this
chat to reconstruct the design. Follow the [operator setup checklist](operator-setup.md)
for external prerequisites; mocked tests do not mean those resources exist.

Assign one task with this prompt, substituting the exact filename:

```text
Read docs/community-scan-service-tasks/README.md and context.md, then complete
docs/community-scan-service-tasks/<task-file>.md only. Read its predecessor handoffs
and decision gates first. Ask the user about unresolved major choices rather than
choosing defaults. Implement the scoped work, run its relevant checks, update status,
and write the required handoff summary with the next exact action.
```

## How to sequence the work

Start with CSS-00. After it, CSS-01 (schema fixtures), CSS-02 (maintenance), CSS-07
(version metadata), CSS-22 (Worker scaffold), CSS-47 (local UI extraction), and CSS-70
(membership-method comparison) are
independent branches subject to shared-file ownership. These are scheduling options,
not a request to spawn agents.

The numeric order groups related work; **follow dependencies rather than numeric
order**. For example, CSS-12 uses the Worker test scaffold from CSS-22. CSS-20 provides
the supported backup UI with optional encryption before CSS-30 exposes ordinary enrollment.
CSS-71 extends that UI to initial setup after CSS-18/20; its number does not postpone
it until after launch review. CSS-39
publishes immutable artifacts before CSS-42/CSS-44 download and import them.

Do not run concurrent changes against the same migration chain, public facade, shared
contract fixtures, frontend root, or package/build metadata without a coordinated owner.
Read upstream handoffs after they change. Security and data-loss failure tests are
part of each owning task, not postponed to the final integration task.

## Master checklist

An unchecked item is not started unless its task file says IN PROGRESS or BLOCKED.
Links in Dependencies point to prerequisite tasks; gate details live in decisions.md.

### A — Database durability and provenance

- [x] [CSS-00: Record the baseline and update database guidance](00-repository-preflight.md) — dependencies: none.
- [x] [CSS-01: Freeze the supported unversioned schema fixture](01-schema-fixtures.md) — dependencies: CSS-00.
- [x] [CSS-02: Coordinate exclusive maintenance with live workers](02-maintenance-coordination.md) — dependencies: CSS-00.
- [x] [CSS-03: Create recoverable pre-upgrade database snapshots](03-safety-snapshots.md) — dependencies: CSS-01, CSS-02.
- [x] [CSS-04: Add packaged Alembic migrations for fresh databases](04-alembic-fresh-database.md) — dependencies: CSS-01.
- [x] [CSS-05: Adopt known databases and recover failed upgrades](05-adopt-upgrade-database.md) — dependencies: CSS-03, CSS-04.
- [x] [CSS-06: Wire migration preflight into startup, workers, and CLI](06-migration-startup-cli.md) — dependencies: CSS-02, CSS-05.
- [x] [CSS-07: Unify release and scanner version metadata](07-version-authority.md) — dependencies: CSS-00.
- [x] [CSS-08: Add migration-backed scan provenance and coverage fields](08-provenance-schema.md) — dependencies: CSS-06, CSS-07.
- [x] [CSS-09: Capture provenance when scanner stages actually run](09-execution-provenance.md) — dependencies: CSS-08.

### B — Formats, signatures and portable keys

- [x] [CSS-10: Measure JSON versus Protobuf before choosing storage encoding](10-encoding-benchmark.md) — dependencies: CSS-09, CSS-22; G-ENCODING resolved to JSON, upload limits still pending.
- [x] [CSS-11: Implement the selected bounded artifact codec](11-artifact-contract.md) — dependencies: CSS-10, CSS-22; gates: G-ENCODING, G-CONTENT. Production limits remain open under G-LIMITS.
- [x] [CSS-12: Freeze and implement the signed-request profile](12-request-signing-profile.md) — dependencies: CSS-00, CSS-22.
- [x] [CSS-13: Sign and verify durable contribution artifacts](13-artifact-signatures.md) — dependencies: CSS-11, CSS-12.
- [x] [CSS-14: Persist portable installation signing keys safely](14-local-key-storage.md) — dependencies: CSS-06, CSS-12.

### C — Backups with optional encryption and local community state

- [x] [CSS-15: Evaluate the backup archive and optional encryption](15-backup-format-evaluation.md) — dependencies: CSS-03; gates: G-BACKUP.
- [x] [CSS-16: Export full and settings-only backups with optional encryption](16-encrypted-backup-export.md) — DONE: four modes, coordinated writers, bounded validation, crash recovery and bundled native age sidecar; non-macOS release tests remain unrun. Dependencies: CSS-03, CSS-08, CSS-14, CSS-15; gates: G-BACKUP.
- [x] [CSS-17: Preview and stage backups without changing active data](17-restore-validation.md) — dependencies: CSS-16; gates: G-BACKUP; isolated four-mode preflight, no activation.
- [x] [CSS-18: Activate restored state with crash recovery](18-restore-activation.md) — dependencies: CSS-02, CSS-05, CSS-17; journaled switch and offline rollback, pending reconciliation.
- [x] [CSS-19: Expose safe backup and restore CLI workflows](19-backup-cli.md) — dependencies: CSS-16, CSS-17, CSS-18; offline export, preflight, confirmed restore and journal recovery.
- [x] [CSS-20: Build backup and restore settings with optional encryption](20-backup-settings-ui.md) — dependencies: CSS-19; protected streamed jobs, redacted preview and confirmed restore.
- [x] [CSS-71: Offer restore from backup during initial setup](71-initial-setup-restore.md) — dependencies: CSS-18, CSS-20; setup-bound flow, validated credentials and confirmed restore.
- [x] [CSS-21: Add durable local community records and operations](21-local-community-state.md) — dependencies: CSS-06, CSS-08, CSS-14; migration, transactional provenance/outbox, backup identity inventory.

### D — Shared service, Discord and signed access

- [x] [CSS-22: Scaffold the separately deployed TypeScript Worker](22-worker-scaffold.md) — dependencies: CSS-00.
- [x] [CSS-23: Add D1 identity and operation migrations](23-d1-identity-schema.md) — dependencies: CSS-22; gates: G-KEYS.
- **RETIRED** [CSS-24: Accounting-admission work](24-budget-ledger.md) - removed October 7 for unrestricted local Docker usage; no active prerequisites or downstream dependency.
- [x] [CSS-24A: Trusted ingress and passive broker observations](24a-broker-admission.md) - DONE; trusted Tunnel metadata, redacted failures, passive observations and bounded transient cleanup; dependencies: CSS-22, CSS-23.
- [x] [CSS-25: Enforce request signatures and mutation replay protection](25-worker-request-auth.md) — DONE locally October 7: complete route/auth inventory, verified clock diagnostics, real HTTP warm-cache retirement/member fences and lost-response hard-restart replay/idempotency evidence; live OPS-01/02 remain separate. Dependencies: CSS-12, CSS-23.
- [x] [CSS-26: Implement the hosted Discord authorization broker](26-discord-oauth.md) — DONE; local Worker broker, trusted ingress and passive observation wiring, upgrades and mocked OAuth-to-key-proof verified; downstream enrollment and live setup separate; dependencies: CSS-22, CSS-23, CSS-24A.
- [x] [CSS-70: Compare bot and OAuth membership rechecks before selection](70-membership-method-comparison.md) — DONE; official-source report, user selected bot REST; dependencies: CSS-00.
- [x] [CSS-27: Enforce continuing Discord membership](27-membership-eligibility.md) — DONE locally; bot REST, 900-second evidence, fail-closed refresh; live setup remains OPS-01/02; dependencies: CSS-25 shared hook, CSS-26, CSS-70; gates: G-MEMBERSHIP, G-ADMISSION.
- [x] [CSS-28: Register Discord-bound keys and contributor profiles](28-enrollment-profile.md) — DONE hosted local/mock scope; confirmed enrollment/retry/reconnect, unique three-word names and signed profiles; local confirmation client/UI remain CSS-29/30; dependencies: CSS-23, CSS-25 shared hook, CSS-26, CSS-27; gates: G-KEYS.
- [x] [CSS-29: Add the signed local client and enrollment transport](29-local-service-client.md) — DONE local/mock scope; authority-bound transport, durable explicit enrollment/profile operations, administrator/CSRF routes and typed local wrappers; CSS-30 UI and live setup separate; dependencies: CSS-14, CSS-21, CSS-25 shared hook, CSS-28.
- [x] [CSS-30: Build Discord connection and identity settings UI](30-connection-settings.md) — DONE local/mock scope; explicit hosted-reference approval, durable enrollment/rename retries, safe current/cached profile distinction and verified backup access; live setup remains separate; dependencies: CSS-20, CSS-28, CSS-29.
- [x] [CSS-31: Rotate and revoke keys atomically on the service](31-key-rotation-service.md) — DONE local/mock hosted scope; atomic dual-proof replacement, immediate retirement/revocation and replacement-signed reconciliation; local trigger remains CSS-32; dependencies: CSS-25 shared hooks, CSS-28; gates: G-KEYS.
- [x] [CSS-32: Add user-triggered key rotation and recovery of interrupted attempts](32-manual-key-rotation-ui.md) — DONE local/mock scope; explicit confirmation, atomic private generation, replacement-signed recovery and fresh-backup reminder; dependencies: CSS-14, CSS-29, CSS-30, CSS-31.
- [x] [CSS-33: Implement the approved additional-installation and lost-key flow](33-identity-recovery.md) — DONE local/mock scope; fresh Discord-bound atomic recovery, same-candidate reconciliation, confirmed local UI and four-mode backups; dependencies: CSS-28, CSS-29, CSS-30, CSS-31, CSS-32; gates: G-KEYS, G-RECOVERY.

### E — Matching, publication and verified import

- [x] [CSS-34: Normalize typed media and release metadata](34-media-identity.md) — dependencies: CSS-08; DONE October 2, 2026, locally verified captures/discovery/manual confirmation.
- [x] [CSS-35: Implement the approved versioned file fingerprint](35-file-fingerprints.md) — DONE October 2, 2026; approved nine-position SHA-256, optional full hash, mutation-checked cache and Python/Worker vectors; synthetic macOS evidence only. Dependencies: CSS-34; gates: G-MATCHING sampling resolved.
- [x] [CSS-35A: Bind AI audio scans to a selected track](35a-audio-track-provenance.md) - DONE locally; durable exact input/actual provenance, protected memory/API, 0005 migration and backups verified; existing DB upgrade not applied. Dependencies: CSS-06, CSS-09, CSS-34.
- [x] [CSS-35B: Fingerprint selected audio and video streams separately](35b-stream-fingerprints.md) - DONE locally; approved bounded three-window AAC-LC/H.264 v1, actual-only audio and Python/Worker vectors; synthetic/storage/timing limits explicit. Dependencies: CSS-34, CSS-35, CSS-35A; gates: G-MATCHING.
- [x] [CSS-35C: Select the audio track for an AI scan in the UI](35c-audio-track-selection-ui.md) - DONE locally; shared catalog/Other Files selection, actual metadata and 320/1440 browser/axe/focus verification. Dependencies: CSS-35A.
- [x] [CSS-36: Implement deterministic compatibility bands and explanations](36-compatibility-policy.md) - DONE locally; approved exact playback-relative stream timing, independent channel bands, actual-only candidate audio and complete DTO parity; 162 Python/69 Worker tests. Dependencies: CSS-34, CSS-35, CSS-35B; gates: G-MATCHING.
- [x] [CSS-37: Build immutable donation payloads and privacy previews](37-donation-artifact-builder.md) — DONE locally October 3: approved artifact v2, strict v1 support, immutable exact-byte consent/signing, actual-only audio, mixed-review exclusions and unknown/partial labels; no publication route/UI; dependencies: CSS-09, CSS-11, CSS-13, CSS-21, CSS-34, CSS-35, CSS-35A, CSS-35B; gates: G-CONTENT.
- [x] [CSS-38: Add hosted work, release and contribution records](38-d1-catalog-schema.md) - DONE locally October 3: additive D1 0009, immutable v1/v2 revision/operation/lineage records, bounded indexed published-only lookup, 0001-0008 preserving upgrades and synthetic storage evidence; no existing migration/routes/deployment. Dependencies: CSS-11, CSS-23, CSS-36.
- [x] [CSS-39: Publish signed artifacts with durable D1 state](39-publication-service.md) - DONE October 3 in approved local/mock scope: signed bounded uploads/status, one atomic D1 transaction for nonce/operation/catalog, BLOBs, counters and published result under migration 0010 (superseding the earlier R2 implementation); production limits/CPU/deployment remain gated. Dependencies: CSS-13, CSS-25 shared auth, CSS-27, CSS-38; gates: G-CONTENT resolved, G-LIMITS local scope approved.
- **RETIRED** [CSS-40: Publication recovery](40-publication-reconciliation.md) — atomic D1 publication eliminates this standalone task; no implementation required.
- [x] [CSS-41: Serve bounded authenticated availability and match queries](41-catalog-lookup-api.md) — DONE locally October 3: batched indexed availability, compatibility keyset pages, bounded public metadata cache and current authority/moderation checks; 475 Worker tests, typecheck/dry-run and local 50-item cost evidence; production remains disabled. Dependencies: CSS-25, CSS-27, CSS-36, CSS-38, CSS-39.
- [x] [CSS-42: Deliver verified bytes and contributor-bound receipts](42-artifact-download-service.md) — DONE locally October 3: exact D1 byte bundles, current authority/moderation fences, immutable-only cache and approved no-expiry service Ed25519 receipts; additive unapplied 0011 telemetry, 490 Worker tests/typecheck/dry-run; production remains disabled. Dependencies: CSS-13, CSS-25 shared auth, CSS-27, CSS-39.
- [x] [CSS-43: Extract reusable import transactions without behavior changes](43-import-repository-refactor.md) — DONE locally October 3: focused EDL/MCF workflows, metadata preparation, uncommitted row staging and preserved rollback/facade behavior; 254 scoped tests plus installed wheel/sdist smoke and Ruff passed. Dependencies: CSS-06.
- [x] [CSS-44: Verify and import downloaded community evidence atomically](44-verified-local-import.md) - DONE locally October 3: fresh service-origin receipt-key trust, exact signed evidence, atomic deduplicated imports/target reuse and separately recoverable original receipt intents; additive unapplied local 0006, four-mode backup/restore and installed-package checks, 491 Worker tests/typecheck/dry-run passed. Backend regression run: 886 passed, 8 pre-existing audio-selection fixture failures disclosed in handoff. Dependencies: CSS-13, CSS-21, CSS-42, CSS-43, CSS-29.

### F — Sharing, Find Filters and reputation

- [x] [CSS-45: Upload approved donations with durable retry state](45-local-publication-outbox.md) - DONE locally October 3: exact approved-byte outbox, owned check-first retries, honest cancellation and protected job APIs; additive unapplied local 0007, 295 backend/33 Worker publication checks and four-mode backups/installed-package tests passed. No live upload or existing migration. Dependencies: CSS-21, CSS-29, CSS-37, CSS-39.
- [x] [CSS-46: Build manual donation preview and sharing controls](46-manual-sharing-ui.md) - DONE locally October 3: protected exact preview/approval, shared scan/review/Other Files controls, durable retry/check/cancel and standing reuse permission with retained notes; 199 backend, 194 frontend and 6 responsive browser checks passed. No license picker/author withdrawal/automatic donation, live upload or existing migration. Dependencies: CSS-30, CSS-35C, CSS-37, CSS-45; gates: G-LICENSE resolved, G-CONTENT.
- [x] [CSS-47: Extract the existing local Find Filters workflow](47-find-filters-extraction.md) - DONE locally October 4: reusable controller/dialog, preserved Jellyfin/Radarr/Sonarr association and review reuse, explicit async focus restoration and responsive wrapping; 205 frontend tests, 4 Chromium journeys and build passed. No remote behavior or API/schema change. Dependencies: CSS-00.
- [x] [CSS-48: Combine local discovery with remote lookup state](48-local-lookup-adapter.md) - DONE locally October 4: protected signed one-target lookup, private bounded evidence, independent channel DTOs and optional local-first remote state with cancellation/refresh/pagination/stale errors. 261 backend tests, 221 frontend tests, 4 Chromium journeys and build passed. Catalog rendering/remote selection/downloads remain CSS-49/50; no live activation/schema change. Dependencies: CSS-29, CSS-34, CSS-35, CSS-35A, CSS-35B, CSS-36, CSS-41, CSS-47.
- [x] [CSS-49: Display scan candidates, provenance and compatibility](49-find-filters-results.md) - DONE locally October 4: explicit selected-version/track community lookup, responsive local-first rows/provenance, independent bands and channel-safe selection, compatibility-first filters/sorts and honest unavailable fields. 229 frontend tests, 8 Chromium journeys and build passed. No downloads, feedback formula, live activation or schema change. Dependencies: CSS-35C, CSS-48.
- [x] [CSS-50: Connect multi-select downloads to local review reuse](50-find-filters-downloads.md) — DONE locally/mock: approved catalog root identity, protected channel-scoped imports/status, independent explicit download queue, cancellation/reopen reconciliation and saved-review attribution. Dependencies: CSS-44, CSS-49.
- [x] [CSS-51: Count unique successful imports with retry-safe acknowledgments](51-import-receipts.md) — DONE locally/mock October 4: approved contributor/revision facts, self exclusion, atomic signed acknowledgment, post-commit outbox/recovery and unapplied preserving D1 0012. Dependencies: CSS-25 shared hooks, CSS-27, CSS-38, CSS-42, CSS-44; counting gate resolved, votes/ranking separate.
- [x] [CSS-52: Implement contributor votes and revision-safe aggregates](52-votes-service.md) — DONE locally/mock October 4; approved exact-import eligibility/no reasons, signed private/current and public aggregate reads, atomic votes/retries/reconciliation; preserving D1 0013 unapplied, production disabled. Dependencies: CSS-25, CSS-27, CSS-51; gates: G-REPUTATION.
- [x] [CSS-53: Implement the approved reputation and ranking formula](53-ranking-policy.md) — DONE locally/mock October 4; revision-only Wilson quality after both compatibility bands, fresh-score DTOs/versioned cursors; zero author boost/no decay/unrestricted local usage, author boosts/tiers explicitly deferred, production disabled. Dependencies: CSS-36, CSS-41, CSS-51, CSS-52; gates: G-REPUTATION.
- [x] [CSS-54: Add voting, report controls and reputation displays](54-feedback-ui.md) — DONE locally/mock October 4; protected exact-intent vote outbox/private fresh feedback, compatibility-first quality/count displays, four-mode backups and responsive browser checks; report submission explicitly deferred to CSS-56, production disabled. Dependencies: CSS-50, CSS-51, CSS-52, CSS-53; gates: G-REPUTATION.

### G — Operator tools, recovery and optional automation

- [x] [CSS-55: Protect the operator UI and API with Cloudflare Access](55-operator-access.md) — DONE locally October 4: verified Access JWT/subject allowlist, protected Worker-first assets/API, same-origin mutation guard and isolated frontend; live OPS-02 configuration/deployment remains gated.
- [x] [CSS-56: Add reports and audited reversible moderation](56-moderation-service.md) — DONE locally October 4: import-eligible private reports, Access/CSRF audited optimistic actions, suspension-aware cache visibility and explicit vote exclusion/restoration; preserving 0014 unapplied, production disabled. Dependencies: CSS-39, CSS-52, CSS-55; gates: G-OPERATIONS resolved for local moderation policy.
- [x] [CSS-57: Build the operator overview and inspection pages](57-operator-dashboard-read.md) — DONE locally/mock October 4: bounded protected reads, isolated overview/lists/inspection, distinct payload/usage measurements, unavailable provider/backup status and context-preserving session recovery. Dependencies: CSS-41, CSS-53, CSS-55; live Access/production remain gated.
- [x] [CSS-58: Wire reviewed moderation and operational controls](58-operator-dashboard-actions.md) — DONE locally/mock; fresh reasoned confirmations, exact retries, vote/report review; separate enrollment/publishing pauses explicitly deferred. Dependencies: CSS-56, CSS-57.
- [x] [CSS-59: Add operator maintenance CLI using shared admin services](59-operator-cli.md) — DONE locally October 4: approved human Access, shared status/moderation, private exact-action retry journals and resumable paginated adapters; no CSRF exception or downstream backup jobs, live Access unverified. Dependencies: CSS-39, CSS-55, CSS-56; G-OPERATIONS resolved for CLI mechanism.
- [x] [CSS-60: Export consistent D1 service backups](60-hosted-backup.md) — DONE locally/mock October 4: approved provider export/query lock, protected provider credential, fixed-bookmark resume, isolated signed BLOB/schema/counter validation and separate age-encrypted archives. Live provider authorization/cost/off-device/hosted drill remain unverified. Dependencies: CSS-39, CSS-42, CSS-59; gates: G-SERVICE-BACKUP resolved for local scope.
- [x] [CSS-61: Restore a hosted backup into an isolated environment](61-hosted-restore.md) — DONE locally/mock October 4: matched encrypted archives, safe resumable disabled local restore, exact signed/schema inventories, source-preserving reputation rebuild and historical usage provenance. No live resource, existing-database write, migration, deployment or cutover; hosted acceptance remains separate. Dependencies: CSS-51, CSS-52, CSS-53, CSS-56, CSS-60; gates: G-SERVICE-BACKUP local scope approved.
- [x] [CSS-62: Add explicit opt-in automatic donation](62-automatic-donation.md) — DONE by explicit user deferral October 4; manual sharing only, no automatic preparation/scheduling/upload. Reopening requires renewed scope approval. Dependencies: CSS-21, CSS-37, CSS-45, CSS-46; gates: G-LICENSE, G-CONTENT.
- [x] [CSS-63: Enforce standing reuse and operator moderation](63-sharing-policy-withdrawal.md) - DONE locally/mock October 4: signed standing permission, compatible parent permission/original attribution, terminal local refusals and preserved moderation evidence; no author withdrawal, destructive retention or deployment. Dependencies: CSS-39, CSS-46, CSS-56; gates: G-LICENSE resolved October 3.
- [x] [CSS-64: Implement approved retention without breaking deduplication](64-retention-cleanup.md) — DONE locally/disposable October 8; explicitly approved retain-first, indefinite durable evidence, shared bounded transient cleanup and read-only offline preview. 570 shared tests plus source/schema and Node 24 ARM64 image preservation/replay/timer/restart checks passed; no durable deletion, backup pruning or existing-database/live changes. Dependencies: CSS-39, CSS-51, CSS-52, CSS-56, CSS-60, CSS-63.
- [x] [CSS-65: Add unattended backups only if scheduling is selected](65-optional-scheduled-backups.md) — DONE locally October 6; approved hourly intervals, existing destination, verified-success oldest-first retention and optional protected-file encryption, administrator-only settings and crash/restore safety. 205 backend/297 frontend/12 browser checks; no live schedule. Encrypted Windows ACL/native validation remains unavailable. Dependencies: CSS-19, CSS-20; gates: G-SCHEDULING.

### H — Integration, packaging and launch review

- [x] [CSS-66: Verify the complete sharing and recovery workflows](66-integration-and-abuse.md) — DONE locally October 7: real HTTP/SQLite API families, Node 24 image/restarts, local import/restore and browser regression evidence; live OPS/platform/capacity checks remain separate. Dependencies: CSS-20, CSS-30, CSS-32, CSS-33, CSS-35C, CSS-50, CSS-54, CSS-58, CSS-62, CSS-63, CSS-71.
- [x] [CSS-67: Package the features and write operational user guidance](67-packaging-and-user-docs.md) — DONE locally October 7: isolated wheel restore/import, reproducible artifacts, hardened context/Compose/image upgrades and recovery runbooks; user selected fresh deployment, unavailable native/AMD64/cold-boot checks remain open release gates. Dependencies: CSS-06, CSS-07, CSS-19, CSS-30, CSS-32, CSS-33, CSS-50, CSS-58, CSS-59, CSS-61, CSS-62, CSS-63, CSS-66, CSS-71.
- **CANCELED / RETIRED** [CSS-68: Measure self-hosted capacity and recovery headroom](68-free-tier-load-audit.md) — explicit user cancellation October 7 for local Docker on Mac mini; no active dependencies or implementation claim.
- [x] [CSS-69: Review the pilot and obtain an explicit launch scope](69-pilot-and-launch-scope.md) — DONE for user-selected local-only pilot October 7; disabled disposable ARM64 Compose staging passed and cleaned up, readiness/rollback/open-gate matrix recorded. No public launch/activation; dependencies: CSS-61, CSS-63, CSS-66, CSS-67. CSS-68 canceled.

## Checkpoint exit evidence

| Checkpoint | Evidence required before dependent work is considered complete |
| --- | --- |
| Database | Current-schema adoption, unknown-schema refusal and failed-upgrade recovery on real SQLite files |
| Portable identity | Durable Ed25519 storage plus full/settings export and restore in both protection modes; encryption off by default; no automatic rotation |
| Discord access | Bound OAuth flow, approved key/recovery policy and membership freshness; setup verified separately |
| Signed artifacts | Approved encoding, byte/signature vectors, privacy/coverage/count validation and immutable lineage |
| Sharing | Atomic SQLite BLOB/publication commit, gated downloads, verified local import and no incomplete visibility; Node source/image HTTP checks, not only unit tests |
| Find Filters | Local-first discovery, explicit uncertain selection, metadata comparison and partial multi-download recovery |
| Reputation | Approved counting/ranking policy, concurrency-safe votes, no self/duplicate inflation and moderation reversal |
| Operations | Live Tunnel/Discord/Access boundary, audited dashboard/CLI actions and independent SQLite/configuration/signing-key recovery drill |
| Release | Docker API matrix (CSS-66), images/Mac cold boot and conditional legacy cutover (CSS-67), and user-selected pilot/activation with explicit unverified-risk dispositions (CSS-69); CSS-68 canceled by the user |

October 6 local review evidence: 599 shared tests, 27 Node tests, Docker build,
TypeScript, Node 24 publication and disposable-container crash/backup checks passed.
CSS-66 now adds broader Node API workflows and enabled-image restart evidence.
Live ingress/provider sessions, target-machine load,
off-device recovery and Mac cold boot remain unverified. These are explicit open
checks for disposition in CSS-67/69 and [operator setup](operator-setup.md), documented in CSS-66's
evidence matrix rather than marked complete by its local tests.

## Handoff and interruption rules

The next agent must receive more than “done.” Each task's **Handoff summary** must
identify the actual files, contract/schema versions and public entry points, tests
and results, unresolved issues, decisions used, and the next task ID with its exact
first action. Include migration/restore recovery notes where applicable.

Record NOT STARTED, IN PROGRESS, BLOCKED or DONE in the task file. Mark removed
work RETIRED (or CANCELED / RETIRED for explicit cancellation) and remove its active
dependency edges; do not present it as implemented.
Never check a partially implemented item. A blocked policy branch does not stop unrelated ready
tasks. A gate may be deferred only by the user; do not implement a guessed default.

The [coverage map](coverage.md) traces the original requirements to owning tasks.
No application code, actual AGENTS.md rule, active database, or external resource was
changed while creating this task pack.
