# Shared implementation context

Current deployment decision, October 5, 2026: **Docker on a Mac mini, Node 24 + SQLite**,
including exact signed artifact bytes and operator assets. Read the
[Docker runtime and recovery contract](../community-service-docker.md) before the
historical [D1 storage scope](d1-storage-scope.md). No Worker/D1 deployment, bucket,
automatic existing-data transfer, public activation is implied.

Read this with the selected task and its predecessor handoffs. It makes the task
pack usable in a fresh agent session without this conversation.

## Purpose and authority

Build Editarr's community scan service: a separately deployed TypeScript service
bundled for Node 24 in Docker, with one SQLite database containing catalog and immutable
artifact BLOBs. Shared regressions also use Node and the production SQLite adapter.
Local Editarr discovers,
shares and imports AI scans and manual/derived filters. Local scanning/review remains
usable without the community service.

This pack began as planning; individual task handoffs now record completed local
implementation, including D1-only publication under migration 0010. Completion of
local code does not imply live resource creation, existing-database migration,
Discord setup or deployment. When assigned a task, do its authorized scoped work.
Read actual repository instructions and preserve unrelated changes. Do not create
new app tasks, spawn agents, publish, or deploy merely because dependencies exist.

Authoritative design:
- [Main plan and decision register D01–D25](../community-scan-service-plan.md)
- [Service contracts](../community-scan-service-contracts.md)
- [Persistence, backup and implementation design](../community-scan-service-implementation.md)
- [Discord enrollment and signing](../community-scan-service-discord-enrollment.md)
- [Open decisions and questions](decisions.md)

Later explicit user answers prevail. Record them in the decision log and update
affected briefs before implementation. A recommendation is not a selected policy.
If documents conflict, ask about material behavior; do not choose silently.

## Confirmed requirements to carry into every task

1. Self-host the community API/operator UI in Docker on a Mac mini. Store catalog
   and exact signed artifact parts in one SQLite data volume. Preserve authority checks and passive observations,
   per-operation validation bounds; do not infer paid upgrades, sharding or new usage-admission policy.
   Payload counters are passive. Tunnel/Access remain external ingress/operator dependencies;
   Workers/D1 pricing is historical, not the current capacity target.
2. Discord OAuth2 and membership gate community access. Discord IDs are private,
   contributor IDs stable, and contributor identity/reputation do not reset on reinstall.
   Cloudflare Access protects operators separately.
   Recheck membership on use after 15 minutes; if an expired check cannot refresh,
   pause community access. Membership alone suffices; no screening/approval role.
   Verified rejoin restores access unless separately suspended; CSS-70's October 1
   decision selects single-member bot REST, not retained user OAuth credentials.
3. Keep local Ed25519 keys, signed requests and signed artifact bytes. No certificates,
   hardware-derived keys, shared embedded secret, or bearer-token replacement.
   Each installation has its own key/history under the shared contributor. Restored
   copies retain their logical installation ID/key; new installations register separately.
4. Rotate only after an explicit action in Editarr's UI; retire the replaced key
   immediately without grace. No rotation on startup, upgrade, restore, transfer,
   normal Discord login or a schedule. Preserve historical public keys.
   Fresh Discord authorization plus explicit UI recovery may replace a lost installation
   key immediately, without affecting other installations' keys.
5. Full and settings/identity-only portable backups support optional encryption, off
   by default. Both modes preserve selected keys, credentials, settings and identity.
   Plain mode needs no passphrase; encrypted mode requires one and must not silently
   downgrade on failure. Do not save an export passphrase to enable automation.
6. Use data-preserving Alembic local-app migrations and numbered SQL migrations for
   the community SQLite runtime. Local-app unversioned adoption remains limited to
   its verified baseline. Community startup never initializes/migrates or adopts D1:
   explicit offline upgrades require a verified recoverable backup and known schema.
   Stop safely on unknown/future schemas. Never use an existing database as a test
   fixture or wipe it; any D1/archive import needs separate approval and verification.
7. One Editarr release version plus distinct scanner behavior/schema/protocol versions.
   Capture effective execution provenance; old unknown metadata stays unknown.
8. Manual sharing first; optional automatic sharing needs explicit opt-in. Include AI,
   manual and derived contributions, with source attribution and no inherited votes.
   Partial and older unknown-provenance scans are allowed with explicit coverage/unknown labels.
   Bounded public free-text event notes are allowed and must appear in the sharing preview.
9. Compatibility ranks before quality; uncertain matches stay separate and require
   explicit review. Downloads/uploads are visible statistics, not automatic trust.
10. Find Filters supports metadata comparison, provenance/coverage/counts, multiple
    selected downloads, idempotent reuse and existing local review workflows.
11. Include both operator dashboard and CLI maintenance/recovery tools. Contributor
    authentication never grants operator authority.
12. Launch scope remains explicitly deferred. October 3 G-LICENSE selects standing
   reuse permission, retained public notes and attribution, per-donation rights /
   exact-preview confirmation, no license picker and no author withdrawal beyond
   operator moderation. Automatic donation still needs separate explicit opt-in.
    JSON is selected: indexed summary JSON text and exact gzip artifact BLOBs in
   SQLite, with original manifest/signature bytes. Storage changes do not reopen encoding.
13. October 2 stream/track extension: choose one AI audio input; preselect a single
   track or unambiguous flagged default, otherwise require selection. Remember
   only for that file/version and revalidate. Preserve actual analyzed language
   and description/title; descriptions are supporting metadata, not equality
   proof. Submit only the scanned individual identified audio track, not the
   alternate-track inventory. Mixed reviews choose one track and visibly exclude
   other-track audio without mutating the saved review; historical unknown audio
   remains shareable with unknown labels and required review. Independent
   compressed audio/video fingerprints are CSS-35B, not a reinterpretation of
   CSS-35. Perceptual re-encode matching is deferred; stream sampling/normalization
   needs approval. Fixed inclusive 2.0-second measured-duration tolerance and
   conservative per-channel bands are approved; automatic retiming is not.
   CSS-35B's format is now approved. The October 2 CSS-36 answer adds exact known
   playback-relative stream start/duration equality, without alignment tolerance;
   missing timing or actual candidate audio stays review-required. Equivalent
   rational timebases may match; timing conflicts affect only their channel.

## Repository map and existing behavior

At planning inspection the checkout was /path/to/samwise.
All code paths in briefs are relative to its root; inspect the actual assigned checkout.

| Area | Existing starting point / constraint |
| --- | --- |
| Database | scanner/db.py calls create_all; seed listener runs after_create; ORM rows in scanner/persistence/rows |
| Startup | scanner/http/api_lifecycle.py initializes DB before auth/queue work; CLI also calls init_db |
| Worker lifetime | Dedicated workers survive API shutdown; maintenance must close all DB users |
| Imports/reviews | repository_imports.py is large; saved reviews are editable snapshots; preserve existing EDL/MCF behavior |
| Catalog | repository_catalog.py and catalog_source_api.py implement local Find Filters |
| Frontend | CSS-47 extracted local Find Filters; CSS-49 adds community-filter-results/row and community-result-state under web/src/scans, reused file-bound track selector, explicit channel selection and responsive provenance/status UI in catalog media-detail.tsx |
| Target lookup/downloads | CSS-48 protected lookup + CSS-49 explicit channel selection feed CSS-50 root-aware local_downloads/community_downloads_api, sequential use-community-downloads/panel and local committed-status reconciliation. Review channel approvals remain target-specific. CSS-51 independently acknowledges committed imports through integrations/community/receipts.py and signed hosted import-receipts, with immutable contributor/revision facts and preserving D1 0012. CSS-52 adds hosted exact-import eligible contributor/revision votes, private own reads/public totals and independent reconciliation under unapplied 0013; no local vote client/UI or ranking. Read CSS-50/51/52 handoffs before feedback/integration work. |
| Sources | Existing Radarr/Sonarr/Jellyfin adapters; verify typed episode IDs using real sanitized fixtures |
| Quality ranking | CSS-53 pure reputation/ranking.ts + local modeling/community_ranking.py verification, versioned revision-only Wilson score after both compatibility bands; zero author boost/no decay/unrestricted local usage. search-2 cursors reject changed votes/visibility. No stored ranks; CSS-54 implements UI sorting/display. |
| Feedback UI/transport | CSS-54 scans/community-feedback + api-community-votes and integrations/community/votes.py provide explicit private fresh reads, exact persisted like/dislike/neutral retry, protected http/community_votes_api and honest snapshot counts/quality within both bands. Existing operations/full backups retain intents; no schema change. Report command stays disabled pending CSS-56 approved fields/API. |
| Hashes | Existing sampled MD5 is cache behavior; do not silently reinterpret it as public SHA-256 |
| State | SQLite, deployment settings and secret files; a database-only backup is incomplete |
| Versions | pyproject.toml, scanner/__init__.py and web/package.json currently declare 0.1.0 |
| Local secrets | Reuse scanner/security and private-file/runtime-path practices |
| Proposed modules | scanner/community, scanner/backup, scanner/persistence/migrations, contracts/community |
| Hosted code | community-service shared auth/catalog/contribution/budget/operator modules; src/runtime supplies Node HTTP, SQLite adapter, environment validation, server and offline management; test/runtime and test/container cover the new deployment |
| Operator UI | CSS-55 isolated web/operator entry and settings/community-operator runtime verify /admin/v1/session; CSS-57 adds bounded /admin/v1/dashboard reads, overview/contributions/reports/contributors/keys/operations/audit/backup hash routes, strict DTO readers, private escaped inspection, separate payload/activity/partial observations and unavailable provider/backup placeholders. CSS-58 moderation-actions provides fresh reasoned confirmations, bounded credential scope, exact action retries, private individual-vote inspection and report review links/resolution; per-feature enrollment/publishing pauses are explicitly deferred. Shared admin/admission preserves limits/accounting; CSS-56 inspection supplies optimistic versions. Worker admin/auth/access + admin/service protect assets/API with Access JWT/current allowlist and same-origin mutations. CSS-59 adds approved human cloudflared CLI, shared status/moderation, private exact-action journals and bounded page checkpoints/opaque byte-preserving transfer adapter; no CSRF exception or backup jobs. Live OPS-02 remains pending. |
| Reports/moderation | CSS-56 contributions/reports + http/reports provide signed private import-eligible structured reports; admin/moderation + moderation-http share Access/CSRF optimistic audited quarantine/restore/suspend/unsuspend/per-vote exclusion/restore/report resolution. Additive unapplied D1 0014 preserves reports/audit/exclusions/resource versions/catalog epoch/admission/usage. Catalog epoch and final fences protect cache visibility; recount honors exclusions. Report UI/local adapter and hosted recovery remain downstream. |
| Legacy D1 backup | CSS-60 operator CLI `backup` and scripts/backup preserve provider export/query lock, durable bookmark, bounded signed/schema inventories and separate age-encrypted snapshot/secret archives. CSS-61 consumes that exact pair without activation. Completed local/mock evidence is historical; these commands are not the Docker SQLite backup/restore path. |
| Docker backup | Offline src/runtime/manage.mjs + schema.mjs create verified private SQLite snapshots, including schema/integrity/FKs and exact row/BLOB fingerprints. API mounts data only; maintenance mounts backups without secrets/listener. Configuration and signing secrets need separate protection. CSS-68 canceled; CSS-69 records unverified off-device recovery risk. CSS-64 retain-first excludes manual-service backup pruning; new schedules/destinations remain gated. |
| Community retention | CSS-64 DONE locally/disposable October 8 after explicit retain-first approval. src/retention shares bounded existing transient selectors and policy; original feature pruners/hourly scheduler remain. Offline retention --dry-run uses verified read-only current-schema snapshot, no identifiers/secrets/writes. Durable signed payloads/lineage/keys/operations/receipts/votes/reports/audits remain indefinite; no schema migration or reclaimed bytes. See its completed handoff for source/image replay/timer evidence and synthetic storage measurements. |
| Local scheduled backup | CSS-65 scanner/backup/schedule, schedule_store and schedule_runner reuse verified export with no-overwrite publication and restore-hold fencing. Protected /api/backups/schedule and General Settings require a real administrator session. Explicit hourly interval, existing destination, retained count and optional external protected passphrase file; machine-local state is not portable backup inventory. CSS-64 does not change this count retention or create a Docker-service schedule. |

CSS-61's [restore handoff](61-hosted-restore.md) adds the local-only operator
`restore` command and focused backup archive/job/worker/counter modules. It requires
CSS-60's complete checkpoint and both encrypted archives, writes a new private
disabled SQLite/config/secret generation, verifies source inventory before shared
import/vote rebuild and preserves all non-statistics tables. Original usage periods
remain historical with unknown provider freshness. No live target, migration,
callback, deployment or activation; hosted cutover and dashboard attestation stay gated.
That output is not directly accepted by the Docker runtime: CSS-67 owns any
separately approved known-schema legacy import and isolated cutover rehearsal.

October 7 CSS-67 packaging is verified locally; see its
[artifact/platform matrix](decision-evidence/67-packaging-matrix.md) and
[user guide](../community-user-guide.md). The user selected fresh deployment/no
legacy import and retained unavailable native/AMD64/target-Mac checks as open release
gates. Context privacy, actual Compose isolation, distinct-image replacement and
backup-first image upgrades are tested; no existing data or live service was changed.
October 7: the user canceled CSS-68 for local Docker on Mac mini. Do not implement
its benchmark/recovery harness. CSS-69 owns the readiness report and explicit launch
disposition of unverified risks; cancellation is not certification or activation.

October 7 [CSS-25's completed authorization audit](25-worker-request-auth.md)
inventories all current routes and verifies real HTTP warm-cache membership/key
fences, contributor/operator separation and dropped-response hard-restart recovery.
Verified out-of-window signatures return no-store `401 clock_skew`; the local client
preserves the diagnostic without automatic retry. Live OPS-01/02 remain separate,
and no usage-admission, key-policy, retention or public activation decision changed.

October 8 [CSS-64's completed handoff](64-retention-cleanup.md#october-8-completion)
supersedes its previous deferral after the user's explicit retain-first approval.
Shared/source and fresh Node 24 ARM64 image checks preserve durable evidence through
bounded transient cleanup/replay; offline preview refuses live-writer/unknown/old
schema inspection. No existing database or live service was changed. CSS-68 remains
canceled and CSS-69 remains complete only for its selected disabled local pilot.

Keep scanner entry points/facades thin. Implement behavior in feature packages.
Frontend feature code belongs in the matching feature folder; tests beside features,
app integration under web/src/test and browser tests under web/e2e. Aim below 400
lines per file; split approaching 500-line catch-alls before extending them.

Old task packs document historical, explicitly authorized wipes. Those are not
authorization for this work. CSS-00 updates only the obsolete development-data part
of AGENTS.md under the user's already stated migration requirement.

## Working a bounded task

- Read the named parent sections and predecessor handoffs before editing.
- Confirm dependencies are actually DONE, not merely present on disk. Small isolated
  scaffolds/fixtures can proceed when the brief permits, but cannot count as a
  completed dependent integration.
- Use one task as the normal assignment unit. Numeric IDs are stable references,
  not a requirement to execute strictly in numeric order.
- Ordinary module names, test organization and equivalent internal implementations
  are engineering choices. Identity rules, cryptographic/backup formats, licenses,
  spending, publication policy and launch scope are decision gates.
- Ask the user when a gate lacks an answer. Prepare concrete comparisons/evidence
  before asking when measurements are required. Never treat silence as agreement.
- Test fixtures may use explicit small synthetic limits. They are not production
  defaults. Do not activate an externally accessible service with unset policy.
- Do not grow scope to downstream features. If a task proves too large, split it into
  smaller named subtasks with their own acceptance/handoffs and update dependencies.
- Shared migrations, contract fixtures, facades and build metadata need one owner
  at a time. Independent dependency branches can be scheduled separately, but this
  pack does not request parallel agents or shared-file concurrent edits.

## Verification

Use commands observed in the repository; select focused tests for each change.
Do not run every expensive check after every task. Broaden at integration/packaging
checkpoints, or when a new failure or change justifies it.

| Area | Command pattern / evidence |
| --- | --- |
| Python | uv run pytest -q tests/<actual-file>.py; existing .venv interpreter is acceptable when dependencies are already provisioned |
| Python style | uv run ruff check <changed-python-paths> |
| Frontend unit | pnpm --dir web test -- <actual-test-file> |
| Frontend build | pnpm --dir web build |
| Browser | pnpm --dir web test:e2e -- <actual-spec>; production route checks use test:e2e:production |
| Worker | `pnpm --dir community-service typecheck`, `pnpm --dir community-service test`, `pnpm --dir community-service build` (dry run only); use focused Vitest paths when appropriate |
| Node runtime | `pnpm --dir community-service test:runtime`; real HTTP/SQLite coverage for every service API family in CSS-66, not only publication |
| Docker | `docker compose -f community-service/compose.yaml build`; `pnpm --dir community-service test:container`; `COMMUNITY_RUNTIME_IMAGE=editarr-community:local node --test community-service/test/runtime/service.test.mjs`; disposable fixtures/volumes only |
| Packaging | Existing scripts/build_release.py, installed-release verifier and native test instructions; read current flags before use |
| Docs | Validate links, fences, dependencies and whitespace; no application tests required for planning-only edits |

Angle-bracket entries above are placeholders to replace, not literal commands.
Tests in this repository use focused fixture modules; do not assume a global
tests/conftest.py exists. Use fake models/temporary databases for service tests.
Never fetch model weights, perform a real Discord login, load-test a shared account
or deploy resources as an incidental test.

Meaningful verification must cover behavior and failures, not mirror implementation:
- Migrations/restores: file-backed SQLite, WAL, existing data, crash boundaries.
- Auth: altered exact bytes, replay/concurrency, cache retirement, wrong identity.
- Service persistence: exact SQLite BLOB round trips, immutable constraints,
   whole-publication rollback, concurrent retries, unrestricted usage despite legacy caps, populated
   schema upgrades, WAL/crash recovery and second-writer refusal; run shared Node/SQLite regressions.
- Imports: malicious artifacts, immutable provenance, rollback and deduplication.
- UI: loading/error/empty/partial/offline, keyboard/focus and narrow/wide layouts.
- Capacity: target-Mac Node/Docker CPU, memory, latency, disk/WAL/backup headroom;
   provider/load/off-device/cold-boot checks only when authorized, with estimates labeled.
- Deployment: OPS-01/02 own live Discord/Tunnel/Access verification; CSS-67 owns
   image/platform/cold-boot and conditional legacy cutover, and CSS-69 the explicit
   readiness/activation decision including canceled CSS-68 and unverified recovery.
   Local test passes close none
   of these live checks automatically.
- Platform-specific checks unavailable on the current host remain explicitly unverified.

## Definition of done

Every task also has its own acceptance criteria and verification. Completion requires:

1. Scoped behavior is implemented and all acceptance items are satisfied, or an
   explicitly optional feature has a recorded user decision to defer/decline.
2. Necessary decision gates have answers recorded with date/source; no major
   unapproved policy is encoded. A pending question keeps affected work blocked.
3. Relevant tests/checks passed with actual commands/results recorded. No known
   regression is hidden by changing expectations or claiming an unavailable run passed.
4. Schema changes include migration/upgrade evidence and update fresh/upgrade fixtures.
   New persistent/secret state has backup/restore inventory coverage. Protocol changes
   update cross-language fixtures and compatibility/version documentation.
5. Existing behavior/public imports remain compatible; module organization, secret
   handling, privacy, passive activity observations and local auth/CSRF rules are preserved.
6. Documentation and task status match implemented behavior. Production deployment,
   account setup or platform verification still outstanding is clearly identified.
7. The task's handoff summary is written on disk before marking DONE. Include changed
   files/revision, contracts/migrations/fixtures, used decisions, exact verification,
   failures/limitations, recovery notes and the next task's exact first action.
8. Update the index checkbox only after all of the above. On interruption write a
   partial handoff and status IN PROGRESS or BLOCKED; do not mark success.

The handoff is a required deliverable for the next agent, not just a final chat reply.
Never put passwords, private keys, access tokens or personal media inventories in it.
