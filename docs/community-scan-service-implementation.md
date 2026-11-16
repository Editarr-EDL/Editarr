# Community scan service: foundations and implementation sequence

Status: **Proposed delivery plan**. Read the [main plan](community-scan-service-plan.md)
for approved requirements and open decisions, and the
[contracts](community-scan-service-contracts.md) for service behavior.

The user requested the complete long-term plan before deciding launch scope. The
phases below express dependencies, not an approved release commitment or schedule.
October 3 [G-LICENSE](community-scan-service-tasks/decisions.md#g-license) selects
standing reuse permission, retained public notes, no license picker and no author
withdrawal. CSS-46 implements explicit preview/permission approval; CSS-63 owns
service enforcement and approved operator moderation, not author revocation.
Production activation remains deferred. Cloudflare Access for operators is confirmed.

October 5 deployment decision: [Node 24 + SQLite in Docker on a Mac mini](community-service-docker.md),
including the operator UI. All service tests and builds now use Node/SQLite;
completed D1 handoffs are historical evidence, not a deployment instruction.
CSS-66/67/68/69 and OPS-01/02 own the remaining Node API, image/host recovery,
capacity, independent backup and live-ingress checks listed in main plan section 4.

The [coding task pack](community-scan-service-tasks/README.md) decomposes these phases
into focused assignments with dependencies, acceptance criteria, verification and
required handoff summaries. Its [decision gates](community-scan-service-tasks/decisions.md)
identify questions that must be answered before affected implementation proceeds.

## 1. Database versioning and repository guidance

Replace the opening development-data section of `AGENTS.md` as the first implementation
change. Proposed replacement, leaving organization/maintainability guidance intact:

> Database schema changes require versioned, data-preserving migrations. Include
> upgrade tests from supported schema versions and verify a recoverable backup before
> applying migrations to an existing database. Never wipe a database or silently stamp
> an unknown schema. A destructive development reset requires an explicit request for
> that specific reset and is not a substitute for a migration.

No schema changes or database reset occur as part of writing this plan. The old
instruction to ask about skipping migrations does not require a wipe for this work.
The user's new requirement is to preserve data going forward.

### Local SQLite

Use the accepted Alembic approach with the existing SQLAlchemy models, with packaged
migration files under `scanner/persistence/migrations/`. SQLite table recreation
needs deliberate treatment of named checks, indexes, foreign keys, and defaults.
[Alembic SQLite batch migrations](https://alembic.sqlalchemy.org/en/latest/batch.html)

Establish an initial baseline without pretending the unversioned development database
has a known history:

1. Capture the verified current schema, its constraints and required seed data as the
   accepted support floor (D14). Do this on copies, not by modifying the active database
   while planning. Freeze a schema fingerprint and representative migration fixture.
2. On an empty database, run the complete migration chain and explicit seed operations.
3. On an unversioned database, create a consistent pre-upgrade backup and compare
   tables/columns/types/indexes/constraints to a known baseline. Stamp only an exact
   supported shape after integrity validation; then apply subsequent migrations.
4. For older unversioned shapes outside this baseline, stop with a diagnostic and
   retain the original. Add support only as explicitly scoped future work. Neither
   `create_all()` nor blindly stamping the newest revision is an upgrade strategy.
5. Track applied revisions in the migration history. App startup checks the schema
   before ordinary queries, queue recovery, or workers can write. Reject a database
   newer than the binary supports; do not attempt an automatic downgrade.

Use one migration owner and an interprocess maintenance lock covering HTTP startup,
CLI tools, and scan workers. Workers verify the supported revision but never race to
migrate it themselves. Inspect all `init_db()` call sites. During a schema upgrade,
quiesce active writes and either finish/checkpoint workers or wait safely before
changing tables. Existing API shutdown deliberately allows workers to survive, so
ordinary shutdown is not sufficient protection.

Automatic upgrades may run before service readiness after a successful safety snapshot;
provide a CLI preflight/upgrade/recovery path as well. A failed migration leaves the
app in a clear maintenance state and preserves the pre-upgrade generation. Do not
assume all SQLite DDL failures roll back cleanly; exercise recovery using real files.
Do not combine a migration with irreversible media sidecar writes.

Test fresh install and every supported upgrade fixture against the same final model
schema. Preserve scan UUIDs/numbers, saved reviews, finding provenance, selections,
retiming, exports, connection settings, administrator credentials, and new community
identity links. Separate seeds from the current metadata `after_create` event so both
creation and migration paths install required defaults exactly once.

### Community service SQLite

Reuse the independent numbered SQL chain in `community-service/migrations` through
the Node runtime's schema verifier and explicit offline maintenance commands.
`PRAGMA user_version` plus the exact known schema identifies supported runtime
databases; do not reinterpret a D1 `d1_migrations` table as automatic adoption.
Initialization refuses existing paths. Startup refuses missing, unknown, future or
outdated schemas and never upgrades automatically, unlike the local-app flow above.

Stop the service before backup/upgrade/restore. Hold the exclusive writer lock,
verify a new recoverable pre-upgrade snapshot, apply supported migrations atomically,
then verify schema, integrity and foreign keys before restarting compatible code.
Preserve populated rows and exact signed BLOBs; failed upgrades roll back without a
reset. Test every supported source version, rollback and second-writer refusal in
real file-backed SQLite, including the shared regression suite and image journeys.

The Node SQLite adapter provides atomic batches; conditional result checks
still matter. Nonce, operation, catalog, BLOBs, counters and publication state commit
together. WAL/FULL durability and local-volume locking are part of the contract;
never copy a live main file alone or use SMB/NFS/cloud-synchronized database storage.

Existing D1 stores and CSS-60/61 archives are not directly supported runtime inputs.
CSS-67 owns a conditional, separately approved offline import/cutover procedure with
known-source verification, a recoverable source backup, complete inventory and
isolated disabled destination. Preserve the original store and test rollback; no
blind stamping, source deletion or activation accompanies conversion.

## 2. Version provenance

The app already declares `0.1.0`; establish a release policy rather than claiming
versioning is entirely absent. Proposed separate authorities:

| Version | Meaning / persistence |
| --- | --- |
| Editarr release | User-facing semantic version; `pyproject.toml` is the proposed authority; build checks/generated metadata keep Python, frontend, native packages consistent |
| Build identity | Commit/build identifier, prerelease marker and dirty-development status; useful even when a semantic version is unchanged |
| Scanner behavior | Explicit analysis/pipeline revision, advanced for changes that affect findings or interpretation; captured for every scan |
| Model provenance | Backend/library version, model revision/artifact digest/quantization and effective settings per detector/enrichment stage |
| Local database schema | Alembic revision chain; not inferred from the release number |
| Service database schema | Numbered SQL migrations plus verified SQLite schema/user_version, upgraded offline independently of local Alembic |
| Community API / artifact | `/v1` contract and payload schema versions with explicit compatibility rules |
| Taxonomy / fingerprint / matching | Versioned category registry, file-fingerprint algorithm, and matching policy |
| Backup format | Versioned manifest/container and supported restore range |

Capture the actual execution version/configuration when each stage runs, not just
when a queued job was created or later shared. A resumed scan must use compatible
checkpoints or record mixed-stage provenance; invalidate incompatible checkpoints.
Record model availability/failed enrichment separately from the requested settings.

Do not backfill unknown old scans with the current release/model versions. Expose
`unknown/legacy`, allow policy-based sharing with that label, and distinguish creation
version from upload-client version. The service records author claims without claiming
to attest that a particular binary or model actually executed.

Add a small local version/capability endpoint and Settings/About display. Builds fail
if exposed versions drift. Record the tested Node/base-image version and image identity;
retain the Worker compatibility date for regression tests and lock dependencies.
Preserve old artifact readers for an agreed support window; incompatible
schema downloads produce an upgrade message and leave local data unchanged.

## 3. Local backup and restore

Confirmed scope: full backups and settings/identity-only exports, each supporting
unencrypted and encrypted output. Encryption is optional and off by default (D06,
updated September 27, 2026), including when credentials or signing keys are included.
Treat portable identity as durable state, not a reconstructible cache.

| Content | Full backup | Settings/identity export |
| --- | --- | --- |
| Completed scans, findings, saved reviews, provenance, synchronization | Include | Exclude |
| Connections, credentials, path mappings, filter defaults | Include | Include with secret inventory |
| Community contributor/installation IDs, signing key, key history, endpoint | Include | Include |
| Local admin/auth settings | Include; expire all sessions on restore | Explicit restore choice; retain target protection by default |
| Deployment/diagnostic preferences stored outside SQLite | Include with portability flags | Include with portability flags |
| Pending operations/queue metadata | Include for reconciliation; never blindly resume | Exclude |
| Model downloads, extracted audio, thumbnails, cache, logs, source videos | Exclude | Exclude |
| Media-adjacent EDL/MCF sidecars | Record provenance; separate optional copy/restore scope | Exclude |

The [CSS-14 private-file inventory](community-scan-service-tasks/decision-evidence/14-local-key-storage.md)
names the portable current and pending community key files and manifest that
both backup kinds must capture consistently in either encryption mode. A pending replacement
is not a request to rotate or activate on restore; reconcile its recorded
operation with the authoritative service in CSS-32.

CSS-29 additionally retains its version-1 enrollment/profile journal inside that
private manifest in both backup kinds. Pending confirmation secrets are disclosed
as `community-enrollment-confirmation` in the secret inventory; profiles and exact
rename retry IDs/names travel with the same identity. Restore does not send
requests or approve enrollment. The first local access expires stale enrollment
authority, and only explicit administrator/CSRF confirmation sends completion.
This identity-bound journal is distinct from scan-linked publication/receipt/vote
outboxes, which remain excluded from settings-only backups.

The completed [CSS-30 settings UI](community-scan-service-tasks/30-connection-settings.md)
links to the existing full/settings backup flow before offering enrollment.
It makes no fabricated backup-freshness claim: status is explicitly not verified.
Encryption remains optional and initially unchecked. Network-free local status
restores pending actions, while signed profile refresh verifies current access;
neither page load nor restore approves an enrollment or rotates a key.

[CSS-32](community-scan-service-tasks/32-manual-key-rotation-ui.md) extends the private
manifest with a version-1 rotation journal and active `key_file` pointer. Candidate
private bytes, operation, audience and exact dual proofs are atomically stored
before send; activation writes the inactive existing slot and atomically switches
to the verified replacement. Full/settings exports follow the selected slot and
retain a pending candidate in either encryption mode. Completed-generation exports
exclude the inactive old private slot. Restore never activates or sends an operation.
The backend repairs only the matching completed rotation's interrupted SQLite
projection, preserving sharing consent. Explicit Check/Retry actions reconcile
with the prepared replacement/original proofs, never create another candidate on
reload, and retain uncertainty. The fresh-backup reminder cannot verify freshness.

Include a manifest with format/app/schema versions, creation time, backup
kind, identity fingerprint, file checksums/sizes, and included/omitted secret inventory.
For encrypted output, encrypt the manifest and contents, leaving only minimal
encryption-format/KDF metadata outside authenticated ciphertext. For unencrypted
output, the manifest and included credentials/keys are readable archive contents;
show that fact in the existing backup summary. Never put secrets in filenames or logs.

When encryption is enabled, use a reviewed authenticated-encryption container and
passphrase-based key derivation with random salt, documented parameters, and bounded
memory/CPU. Require a passphrase only for encrypted backups; never store it in the
backup or logs. Unencrypted export/restore requires no passphrase and must preserve
the same selected settings, credentials and identity. Never silently fall back to
unencrypted output when requested encryption fails. Evaluate
streaming behavior on large databases before freezing the format. Existing cryptography
and Argon2 dependencies may be reused, but do not design a novel encryption scheme.
Bound attacker-controlled KDF parameters on restore to prevent memory/CPU exhaustion.
Both modes retain checksums, schema/size/path validation, consistent snapshots and
atomic output/restore. Unencrypted checksums detect accidental corruption, not an
attacker rewriting both content and checksums; do not claim cryptographic authenticity.
Initial restore ceilings for both modes: 256 regular-file archive members, a
1 MiB manifest, 256 UTF-8 bytes per member path, 64 GiB for the SQLite database
member, 64 MiB per other member (except the stricter manifest cap), and 80 GiB
total member data. Enforce these
as streaming validation and disk-space limits, not in-memory allocations. Reject
duplicate or unexpected names, links, special files and trailing data, without
changing the active installation; refuse to export a backup that exceeds them.

Environment-supplied credentials/keys need explicit inventory. Do not dump the entire
environment into an archive. Indicate which values are omitted and must be restored
through deployment configuration; optionally include specific supported secrets only
when explicitly selected. A backup must not appear identity-complete when its key is
actually external and missing.

### Backup creation

1. Authorize through the current local admin policy. Let the user choose full or
   settings-only, destination, and encryption (off by default); request a passphrase
   only when encryption is enabled. Show included identity, secret types and protection mode.
2. Obtain a short application snapshot lock to coordinate database and external
   configuration/key state. Postpone key rotation and settings mutations during capture.
3. Use SQLite's online backup API for a transactionally consistent database snapshot;
   never copy only the live main database file and ignore its journal/WAL. Build a
   consistent settings export from a read transaction when that option was chosen.
4. Read the manifest-selected external files under the same generation contract,
   checksum, package into a temporary private destination with the selected encryption
   mode, flush, and atomically publish.
5. Verify the resulting archive/manifest can be read, record success locally without
   its passphrase, and clean temporary plaintext on success or failure.

SQLite provides a purpose-built online backup API for live databases.
[SQLite backup documentation](https://sqlite.org/backup.html)

Automatic pre-migration safety copies also contain credentials. Store them in an
owner-only data location with an explicit protection/retention policy; they are not
a substitute for a portable backup. October 6 CSS-65 selects optional local-app
interval scheduling with explicit hours/destination/retained-count configuration,
oldest-first pruning only after a verified success, and optional encryption through
a separately provisioned owner-only external passphrase file. Do not save a manual
export passphrase. Schedule state/credentials stay machine-local and are excluded
from portable archives; restore holds pause execution. See the
[CSS-65 policy and handoff](community-scan-service-tasks/65-optional-scheduled-backups.md).
This selects no community-service backup schedule or CSS-64 retention policy.

### Restore and transfer

1. Authenticate to the target or use an offline CLI with filesystem authority. Preview
   archive kind, identity, schema version, contents, and conflicts without modifying
   active data. Detect/validate the archive protection mode; require and validate a
   passphrase/authentication only for encrypted input. Validate limits, checksums, file allowlist,
   path traversal, symlinks, and unsupported future versions.
2. Restore into an isolated temporary generation, run integrity/foreign-key checks,
   and apply supported migrations there. An incompatible/corrupt archive leaves the
   active installation untouched.
3. Obtain an explicit restore confirmation covering replacement of local work and
   whether the community identity should move. Offer a safety backup of the target.
   Settings-only restore previews credential/connection/path-mapping replacements.
4. Enter maintenance, stop queue admission, and cooperatively finish/checkpoint workers
   or wait for safe shutdown. Hold the instance lock until all database users close.
   A process that survives API shutdown must not reopen the old database mid-swap.
5. Activate a complete validated database/config/key generation with a recoverable
   journal or generation switch. Keep the prior generation for rollback; a collection
   of unrelated file replacements is not an atomic restore.
6. Expire local sessions, clear PID/PGID/worker-token/lock/lease data, and reconcile
   pending community operations from durable service IDs. Retain the restored local
   admin credentials only according to the chosen restore mode; do not resurrect
   old sessions or disable the target's authentication silently.
7. Validate media locations and path mappings. Re-evaluate port/bind/proxy settings
   on another machine before activating them. Mark exports unverified until checked;
   never overwrite media-adjacent sidecars merely because a database was restored.
8. Verify the restored signing key against its public identity. Refresh server key
   state and counters when online; server reputation is authoritative. Revoked keys
   stay revoked. If the key is missing, report incomplete identity rather than enroll
   a new identity silently. Resume eligible work only after local checks pass.

Explain move versus copy in the UI: using the same backup on two running installations
shares the same contributor, votes and history. There is no reliable physical-machine
enforcement in the accepted portable model. Rotate after a deliberate move if the old
copy must lose authority, but only through the user's explicit Rotate key action in
Editarr's UI. Restore/transfer never initiates rotation automatically. The old key
loses authorization immediately when the replacement is activated. Prompt for a new
backup after success; an old backup may hold a retired key. Encryption remains optional.
Separately enrolled installations have independent keys under the same contributor.
Rotation/revocation/recovery affects only the selected installation, not those other keys.

### Restore during initial setup

Offer **Restore from backup** alongside fresh administrator setup, before requiring
creation of a new administrator. [CSS-71](community-scan-service-tasks/71-initial-setup-restore.md)
reuses the supported restore services and UI for full/settings archives in both
protection modes, with passphrase input only for encrypted archives. It adds bounded
setup-only authorization, preview/confirmation and a safe transition after activation.
Under the resolved [G-SETUP-RESTORE decision](community-scan-service-tasks/decisions.md#g-setup-restore),
restore included administrator credentials and require a fresh sign-in; show
administrator creation only when none are included. Malformed credentials fail validation.
The setup entry point closes once an administrator exists; competing setup/restore
attempts cannot overwrite the winning state. General backup APIs stay protected.
Failures preserve a usable setup/retry path, and successful restore retains normal
session invalidation, reconciliation holds and the prohibition on automatic rotation/sharing.

## 4. Hosted service backup and operation

The operator also needs service recovery, distinct from users exporting local state.
The [Docker runbook](community-service-docker.md) is the current command contract:

- Stop the service and use offline maintenance to create a new private SQLite backup,
   preserving catalog, original signed BLOBs, all security/operation/accounting facts
   and schema version. Verification covers known schema, integrity, foreign keys and
   streamed per-table row/BLOB fingerprints before success is reported.
- Mount only data in the API; mount data and a separate backup volume in maintenance,
   without service secrets or a listener. Existing destinations are never overwritten.
   Snapshots are plaintext and a same-Mac backup volume is not disaster recovery.
- Protect configuration, image identity and service secrets separately, retaining
   receipt-signing public-key history and required recovery material. Obtain explicit
   off-device destination, protection, key-custody and recovery-objective decisions;
   no backup schedule or retention policy is implied by the Docker pivot.
- CSS-67 rehearses restoration to a new disabled data volume and schema-compatible
   image rollback. CSS-68's capacity/off-device drill was canceled by the user
   October 7; CSS-69 records unverified recovery risks and their launch disposition.
   Any future drill needs separate authorization and recovery objectives. Preserve
   original volumes and reconcile stale revocations, moderation and operation
   history before separately approved activation. Any derived-counter rebuild must
   preserve source facts and exclusions; current maintenance does not invent one.
- Keep restored environments private, without production ingress/callbacks or client
   enrollment. Recovered usage remains historical application data, not verified
   provider billing. Provider-ledger reconciliation stays deferred.

Legacy D1 tooling: the [CSS-61 local/mock restore drill](community-scan-service-tasks/61-hosted-restore.md)
consumes CSS-60's complete checkpoint and matched age archives into a new private,
disabled SQLite/configuration/secret generation. It verifies original signed/schema
inventories before shared import/vote reconciliation, preserving all source facts,
moderation restrictions and usage periods with unknown provider freshness. No live
D1 target, migration, callback, deployment or production activation occurs. Hosted
import, downtime, provider reconciliation and cutover remain separate acceptance gates.
CSS-60/61 archives do not replace the current Docker snapshot procedure, and their
output must not be installed/stamped as a supported runtime database without CSS-67's
separately approved conversion and preservation checks.

Confirmed operator interface: a small hosted dashboard plus CLI maintenance/recovery
tools. Both reuse the same protected service operations to view usage, quarantine/
restore revisions, suspend/unsuspend contributors, exclude/restore individual votes,
inspect/resolve reports and view backup/reconciliation status. CSS-58 implements
fresh reasoned UI confirmations and exact-action retries locally/mock; the user
explicitly deferred separate enrollment/publishing pauses on October 4, 2026.
No owner withdrawal, key rotation or accounting-policy write is added. CSS-59
implements the explicitly approved human cloudflared Access CLI locally/mock,
using the same exact allowlist, mutation guards and audited actions; private
exact-action journals and bounded page checkpoints support explicit resumption.
Live Access/session acceptance remains OPS-02. Inspection pages are not backups.
Historical CSS-60 provider export is retired. The CLI refuses the old `backup`
command before credentials or network work; current backups use offline Docker
maintenance with verified SQLite snapshots. CSS-61 remains an offline decoder for
already-created legacy archive pairs, tested with synthetic SQLite archives rather
than provider mocks. Its configuration is inert and cannot activate a Docker service.
Protect configuration/signing secrets separately; machine-local storage does not
verify off-device recovery. Any legacy-to-runtime conversion needs separate review.
See [the format, setup and recovery handoff](community-scan-service-tasks/60-hosted-backup.md).
Future CLI workflows handle restores and counter rebuilds with progress/resume support. No contributor
account/password administration is needed. Set the operator allowlist, retention,
sharing terms, and report-handling expectations before public launch (D13/D15/D17).

Dashboard pages: overview/usage, contributions/reports, contributors/keys, operation
history, and backup/recovery status. Use bounded paginated lists and safe inspection
of untrusted text; show current status, reasons, and impact before mutations. Test
keyboard/mobile access, expired sessions, insufficient privileges, stale updates,
direct API requests, and alternate-host/asset-route bypass attempts.

## 5. Repository organization

Planned touchpoints under confirmed D11/D16; exact module splits remain implementation details:

| Area | Placement / reuse |
| --- | --- |
| Community service | `community-service/` shared feature modules plus `src/runtime/` Node HTTP/SQLite/server/maintenance adapters; Dockerfile/Compose, numbered migrations and Node unit/runtime/container tests |
| Local community behavior | New focused `scanner/community/` package for transport, identity lifecycle, matching integration, publication, import, receipts |
| Local identity primitives | `scanner/security/` and existing private-file practices; backend-only secrets |
| Public schemas | `scanner/modeling/` and `scanner/formats/`; versioned shared schemas/fixtures under `contracts/community/`, with JSON Schema and/or Protobuf definitions selected after the encoding evaluation |
| Local persistence | `scanner/persistence/rows/`, focused repository modules, `scanner/persistence/migrations/`; keep `scanner/db.py` a thin facade |
| Backup | Focused `scanner/backup/` package; CLI entry points under `scanner/commands/`; local routes in `scanner/http/` |
| Source identity | Extend `scanner/integrations/` and share one normalization/matching module across sources |
| Find Filters / sharing | Components/hooks under `web/src/scans/`, integrated into catalog and Other Files views; API types/functions under `web/src/api/` |
| Settings | Community identity/sharing and backup views, including the manual Rotate key action and backup reminder, under `web/src/settings/`; shared primitives in `ui`, styles in `styles` |
| Hosted operator UI | `web/src/settings/community-operator/` with its own build entry/output bundled in the community image and served behind Node Access verification; API helpers in `web/src/api/`, shared UI/styles reused; never assume local Editarr session/runtime URLs |
| Tests | Backend `tests/`, feature-adjacent frontend unit tests, app integration `web/src/test/`, browser tests `web/e2e/` |

Document the local-versus-global media identity boundary in a new ADR. Explicitly
supersede outdated parts of `docs/adr/0001-editarr-domain.md` rather than treating its
Jellyfin-only identity and single-scan export language as the new community contract.

CSS-47 extracted local Find Filters into `web/src/scans/use-find-filters.ts` and
`find-filters-dialog.tsx`; `media-detail.tsx` supplies selected-version metadata and
history reload for Jellyfin/Radarr/Sonarr. Read the
[controller/dialog handoff](community-scan-service-tasks/47-find-filters-extraction.md)
before expanding it or adding the Other Files entry. CSS-48 adds protected
`http/community_lookup_api.py`, `integrations/community/lookup.py` and
`lookup_targets.py`, strict `modeling/model_community_lookup.py`, browser
`api/api-community-lookup.ts` and `scans/use-community-lookup.ts`. The composed
controller exposes discriminated local-first results and independent remote status. Read the
[CSS-48 DTO/cancellation handoff](community-scan-service-tasks/48-local-lookup-adapter.md)
for selected-target race/privacy rules. CSS-49 now wires exact catalog versions
and the reused audio selector into `scans/community-filter-results.tsx`,
`community-filter-row.tsx` and `community-result-state.ts`. Its explicit lookup,
channel-bound selections, compatibility-first filters/sorts and honest provenance
labels are documented in the
[CSS-49 UI handoff](community-scan-service-tasks/49-find-filters-results.md).
CSS-50 adds strict root-aware `model_community_downloads.py`, protected
`http/community_downloads_api.py`, `integrations/community/local_downloads.py`,
browser download API/controller/panel, local reconciliation and target-specific
review controls. Its [flow/state-machine handoff](community-scan-service-tasks/50-find-filters-downloads.md)
records exact-byte reuse, independent receipts, cancellation drain, explicit bounds
and saved-review attribution proof. No automatic enrollment/library polling is added.
CSS-51 adds focused `integrations/community/receipts.py` with clean-session
post-commit sending, original-intent retry and bounded missing-queue recovery.
Hosted `contributions/imports.ts` / `http/imports.ts` use approved immutable
contributor/revision facts and atomic counting under numbered SQLite 0012; native
admission/current membership/services and aggregate unknown-aware telemetry apply.
Read the [receipt/counter handoff](community-scan-service-tasks/51-import-receipts.md)
before votes, hosted backup/restore or retention work. No reputation formula is added.
CSS-52 adds hosted `contributions/votes.ts` / `http/votes.ts`: exact-import eligible
contributor/revision state, signed idempotent mutation, own private reads and public
totals, with transactional nonce/operation/vote/aggregate writes under unapplied
0013. No upload-tier voting gate, reason codes, local client/UI or formula is added.
Access loss retains votes; bounded independent reconciliation preserves imports.
Full hosted backups must include `revision_votes`, vote operations/statistics and
`vote_usage_observations`. Read the
[vote DTO/policy handoff](community-scan-service-tasks/52-votes-service.md) before
CSS-53/54/56/61/64; production remains disabled.
CSS-53 adds pure `community-service/src/reputation/ranking.ts` and local
`modeling/community_ranking.py` verification under `community-ranking-wilson-1`.
Catalog quality is secondary to both compatibility bands. No new database or
artifact fields are written; CSS-52 vote counters are reread and ranked on each
lookup. Cursor/cache identity includes ranking policy and changed vote/visibility
snapshots fail closed. Lookup models and browser API carry validated ranking DTOs,
with legacy null support. Author boosts/tiers are explicitly deferred and local usage
unchanged. Read the [formula/cursor handoff](community-scan-service-tasks/53-ranking-policy.md)
before CSS-54/57/61; UI ranking controls/displays remain downstream.
CSS-43 extracted the former
`repository_imports.py` catch-all into focused EDL/MCF workflows, metadata preparation
and uncommitted staging/rollback helpers; its stable facade remains intact. Read the
[CSS-43 transaction handoff](community-scan-service-tasks/43-import-repository-refactor.md)
before extending shared import behavior. CSS-44 uses focused
`integrations/community/downloads.py` and repository `community_imports.py`,
`community_import_projection.py`, `community_import_targets.py`; public attribution
types live in `modeling/model_community_imports.py`. Read its
[import/reuse and post-commit receipt handoff](community-scan-service-tasks/44-verified-local-import.md)
before CSS-50/51/62 integration. Avoid growing near-limit `model_scans.py`, `arr.py` (377), or
`api-service.ts` (475) into catch-all modules. Preserve stable facade imports and tests.

## 7. Implementation phases and acceptance

Discord membership is required under D19. D20 confirms retaining installation keys,
signed API requests, and signed contribution artifacts. P2/P3 must integrate Discord
enrollment with key registration and eligibility checks while preserving D18's manual
UI rotation and immediate old-key retirement. Keys are separate per installation;
lost-key recovery uses fresh Discord authorization and explicit UI replacement. Check
membership on use after 15 minutes and pause community access when expired checks cannot
refresh. Verified rejoin restores access unless separately suspended. CSS-70's October 1
decision selects single-member bot REST; live minimal-permission verification is OPS-01.

Discord prerequisites to complete before community enrollment is released:

- Create the Editarr Discord server; record its server ID and an approved invite URL.
- Configure membership-only admission and decide community invitation/moderation policy.
- Register the hosted OAuth callback and mount protected client/bot secrets in Docker;
   existing server/application IDs remain recorded in OPS-01.
- Verify the selected bot REST method live, including the 15-minute/outage rules and leave/ban cases.
- Finalize credential issuance/recovery, and privacy disclosure.

See the [Discord design](community-scan-service-discord-enrollment.md) for details.
These are to-do items; this planning change creates no Discord or Cloudflare resources.

| Phase | Work | Depends on | Completion evidence |
| --- | --- | --- | --- |
| P0 — Decisions and fixtures | Resolve remaining membership/recovery and launch policies; plan/create Discord community and application before enrollment release; capture current baseline schema and media fixtures; evaluate JSON versus Protobuf savings in SQLite | User policy choices; confirmed D20 signing design | Accepted architecture/contract; Discord prerequisites owned; measured encoding comparison; explicit unresolved launch choices |
| P1 — Data durability | Update AGENTS guidance; migration baseline/adoption; pre-upgrade snapshot/recovery; full/settings backup CLI foundation | D06/D14/D16 | Fresh/upgrade parity; failed migration/restore preserves original data |
| P2 — Provenance and identity | Version authority, execution-time metadata, coverage, portable key storage/backup, manual UI rotation with immediate retirement, shared artifact and request fixtures | P1, D02/D16/D18/D20 | Python/Node signing vectors; old provenance remains unknown; key survives restore; rotation recovery and immediate retirement verified |
| P3 — Self-hosted service and limits | Node 24 Docker image, SQLite/numbered migrations, Discord OAuth/bot membership, installation enrollment, unrestricted user usage, contributor/admin auth, operator CLI, usage instrumentation | D04/D10/D11/D17/D19/D20, P2 | Real Node HTTP/SQLite parity; nonmembers/revoked/replayed/unauthorized requests rejected; Tunnel/Access boundary independently verified |
| P4 — One complete sharing path | Manual donation preview, atomic publication and idempotent retries, indexed lookup, verified idempotent local import | P2/P3, sharing terms | Two clean installs share one scan; service outage does not corrupt local state |
| P5 — Find Filters and multi-source matching | Rich result UI, release/episode/audio identity, uncertain band, local/remote selection, multiple imports and review reuse | P4, D05/D09 | Responsive/key-accessible flow; multi-episode/dub/wrong-cut fixtures stay distinct |
| P6 — Reputation and operator dashboard | Import receipts, vote changes, report/withdrawal, ranking, contributor totals, dashboard moderation/usage/backup status, operator reversal | D12/D13/D15/D17, P4/P5 | No duplicate counts/self-votes; new revision does not inherit votes; rank respects compatibility; dashboard/admin auth and audit tests pass |
| P7 — Complete backup UX and automation | Backup/restore settings screens, initial setup restore option, transfer preview, opt-in donation automation, supported backup scheduling if chosen | P1/P2/P4, D07 | Cross-machine and fresh-install restore drills; no unintended sharing after restore; recoverable pending operations |
| P8 — Pilot and release | Target-Mac/container load and disk/WAL tests, live ingress/provider checks, image/architecture/cold-boot verification, independent recovery, conditional legacy cutover and explicit launch scope | Approved required phases | CSS-66/67/68/69 and OPS-01/02 evidence; remaining long-term features explicitly deferred |

P1 establishes backup safety before community identities exist; P7 adds the complete
UI and transfer/automation experience. Do not release community identity creation to
ordinary users before they have a supported portable identity backup path with optional encryption.

Recommended launch choice after planning: use P4 as an internal pilot, then select a
public release boundary after validating realistic scans. This is a recommendation,
not a decision to defer requested features. Advanced perceptual alignment, automatic
consensus merging, public profiles/comments, automatic
library-wide polling, and hardware attestation are separately scoped follow-on work.

## 8. Verification matrix

| Concern | Required scenarios |
| --- | --- |
| Persistence | Empty/current/supported-versioned/unsupported-old/unknown/future schemas; named constraints; upgrade interruption; concurrent startup; real-file recovery; packaged migration resources |
| Backup | Full/settings × unencrypted/encrypted round trips; encryption off by default; no passphrase for plain mode; wrong password/authentication failure in encrypted mode; no encryption fallback; corruption; future format; traversal/symlinks; huge KDF/body; missing external key; identity equality; cross-OS paths; active workers; activation rollback |
| Auth | Python/TypeScript signature vectors; altered body/path/query/encoding; stale timestamp; wrong audience; replay across isolates; manual UI rotation only; immediate retirement across instances/caches; lost rotation response recovery; no rotation on startup/upgrade/restore/transfer; revocation; malicious enrollment bursts |
| Discord | Wrong/replayed state; handoff theft/substitution; denied consent; nonmember; leave/ban/rejoin; expired/revoked provider grant; provider outage/rate limit; same account on new installation; no anonymous fallback or duplicate reputation; no silent credential replacement |
| Operator access | UI and API protection; invalid/expired/wrong-audience operator token; contributor cannot elevate; CSRF; alternate hosts; asset/SPA routing; mutation audit and stale-write conflict |
| Storage | Duplicate retries; SQLite batch/trigger rollback; missing payload; concurrent publication; immutable digest mismatch; lost success response; historical observation provenance; Node unit/source/image parity |
| Node runtime (CSS-66) | Every contributor/operator API family over real Node HTTP/SQLite, enabled built-image configuration, exact headers/body/cookies/redirects, bounded requests, hourly cleanup wiring and process restart |
| Live ingress (OPS-01/02) | Real Discord callback/member/nonmember/outage, Tunnel HTTPS Host/trusted-IP boundary and direct-origin isolation, Access browser/CLI session revocation/JWKS rotation/CSRF |
| Service deployment (CSS-67) | ARM64/supported AMD64 image/mount isolation, replacement without data loss, known-schema upgrades with verified backup, graceful/hard restart, Mac/FileVault/Docker/tunnel cold boot and conditional approved D1/archive import/rollback |
| Canceled service recovery/capacity audit (CSS-68) | Explicit user cancellation October 7 for local Docker on Mac mini; no new benchmark/drill. CSS-69 records unverified hardware/recovery risks and launch dispositions; existing storage-failure and backup safety remain |
| Matching | Renamed file, re-encode/remux, wrong cut, runtime rounding, missing IDs, hash-version mismatch, changed file, series/episode namespace, specials, double episode, alternative numbering, dubbed/commentary track |
| Artifact | Untrusted URLs/text, bad times/NaN, excessive counts, unsupported taxonomy/schema, compression bomb, summary mismatch, incomplete coverage, unknown model metadata, valid zero findings, derivative lineage |
| Reputation | Self-vote/import, duplicate receipt, vote change/neutral, ineligible key, concurrent counters, revision/derivative abuse, moderation reversal, compatibility before popularity |
| UI | Local-only/offline/disabled/unavailable states; stale results; multiple downloads with partial failure; accessible modal/focus; narrow/wide layouts; donation preview; explicit uncertain selection; restore errors; initial setup restore and credential transition; competing setup/restore requests |
| Existing workflows | Queue/resume, named scans, saved-review isolation, provenance, EDL/MCF import/export, retiming/credits, login/CSRF, native packaging and subpath/reverse-proxy deployment |

Run targeted tests during each phase, then the appropriate existing backend/frontend
suite and production browser/build checks at integration. Add migration fixtures and
end-to-end service tests that verify user outcomes and failure boundaries, not merely
copies of implementation logic. Never use the active development database as a test fixture.

Pilot acceptance requires two installations to enroll using eligible Discord accounts
without Editarr-managed passwords, donate and
discover a scan, compare metadata, import multiple contributions without duplication,
reuse them in saved reviews, preserve provenance, and back up/restore the same identity.
Where voting is in the selected launch scope, counts/ranking and moderation must work
under retries/concurrency. Invalid scans, storage failures, interruptions, and service
outages must leave completed local work intact. Record measured self-hosted headroom
and demonstrate atomic failure on constrained disposable storage without usage-admission refusal. Record
live ingress, supported-platform/cold-boot, independent recovery and any legacy-cutover
gaps explicitly; passing local unit tests does not authorize public activation.
