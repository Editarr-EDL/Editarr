# Editarr community service

The intended deployment is now a hardened Node 24 LTS + SQLite Docker image for
a Mac mini. See the [Docker setup and recovery guide](../docs/community-service-docker.md).
The image includes only the community API and isolated operator UI, not the local
scanner. Default configuration is disabled; no public service, existing database
import, retention policy is activated by building it.

Development and tests use the same Node runtime and SQLite adapter as Docker.
`GET /health` is available locally; signed identity requires configured
bot membership and explicitly enabled protected runtime configuration. The OAuth
broker checks trusted ingress and records passive observations. Enrollment completion
requires the prebound backend confirmation secret, trusted edge admission, current
membership and configured policy; CSS-29/30 implement the local admin/client UI
separately from this hosted package.
`GET /v1/capabilities` returns 503
with `{"error":{"code":"service_unavailable"}}`. Unsupported runtime modes return
the same redacted 503 response. Runtime configuration  must both be
explicitly enabled before contributor or operator routes can be served.
The self-hosted entry point separately validates configuration before explicit
activation; identities, signed bytes and transactional publication are shared.

## Local commands

From `community-service/`, with current Node 24 LTS and pnpm 11.22.0:

```sh
pnpm --dir ../web install --frozen-lockfile
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run test
pnpm run build
pnpm run preflight:container
```

`typecheck` checks locally owned Node/runtime contracts without generating files.
`test` builds operator assets and runs Node Vitest, operator CLI and source-runtime
tests. Each shared test file owns a temporary file-backed SQLite database; the
source-runtime journeys also require the local application and archive toolchain
described below.
`test:unit`, `test:operator-cli` and `test:runtime` run those layers separately.
`build` and `build:container` both bundle the Node service and operator assets using
esbuild. Image checks are separate: build Compose, then run `test:container` and the
image journey command in the Docker guide. No provider deployment tool is installed.

`preflight:container` validates reviewed enabled configuration and protected secrets
after a build, without opening a database or activating anything. Disabled defaults
fail closed. Schema, tunnel isolation and launch approval remain separate
checks. For `dev`, explicitly select an initialized disposable database via
`COMMUNITY_DATABASE`; the Node server defaults to loopback port 8787 and never creates
or upgrades a database at startup. Use the Docker guide's maintenance workflow.
Keep actual credentials/configuration outside the checkout. Old private local-state
directories remain ignored for recovery; removing tooling does not delete their data.

## Multi-instance application E2E

From the repository root, after installing both Node dependency sets and building
operator assets (`pnpm --dir community-service assets`):

```sh
pnpm --dir community-service test:e2e
.venv/bin/python -m pytest -q tests/test_community_end_to_end.py
```

Provision Node >=24 <27, the repository Python environment with its application
dependencies, FFmpeg/PyAV, `age` and `expect` on PATH. On macOS, `age` can be
installed with Homebrew; Expect is supplied by the OS. The Node runner defaults
to `.venv/bin/python`; set `COMMUNITY_E2E_PYTHON` to another provisioned interpreter
when necessary. The pytest entry uses its current interpreter and explicitly
skips when Node, age or Expect is absent. A skip is not E2E certification; the
dedicated Node command requires the complete toolchain and fails on missing tools.

The journey starts three independent production Editarr HTTP applications with
separate temporary databases, administrators and signing keys, plus four fresh
applications for full/settings x plain/encrypted archive restoration. They use
the real community HTTP server, production SQLite adapter and migrations. No
existing application data, service database or external account is used.

Covered workflows include distinct-account and same-account enrollment; real
scan processing, exact preview/consent, donation, discovery, verified download
and reuse; selected-track audio and derived-review lineage; byte preservation;
saved reviews and EDL/MCF exports; receipt/vote idempotency under response loss
and application restart; ranking, private reports and operator resolution;
quarantine, contributor suspension and vote exclusion; membership outage/leave/
rejoin; rotation, restored old-copy rejection, lost-key recovery and sibling
revocation; transactional payload failure/retry; and restored remote access.

Only external Discord/Access answers and model inference are mocked. A synchronous
test worker invokes the real scan pipeline; it does not certify dedicated-worker
lifecycle or model accuracy. A real-socket loopback transport preserves the signed
HTTPS authority while forwarding locally over HTTP, so TLS/Tunnel ingress is not
tested. The report and revocation checks use the real signing client because those
operations have no local UI adapter. Browser interaction remains covered separately
by the existing mocked browser suites, not by this HTTP journey.

`test:runtime` and the default `test` include this source journey. The service-only
Docker image deliberately excludes Python and runs its existing image journeys
instead. Native packaging, live providers and cross-platform certification remain
separate gates.

## Retention Preview

October 8 approved community-retain-first-1 keeps durable signed artifacts,
lineage, public-key/identity history, operations, receipts/deduplication, votes,
reports and audits indefinitely. Existing hourly transient expiry and log rotation
remain; no payload deletion or automatic manual-service-backup pruning is added.
CSS-65's local-app scheduled-backup policy is separate and unchanged.

After separate operator authorization, stop the service and inspect offline:

```sh
docker compose -f community-service/compose.yaml stop community
docker compose -f community-service/compose.yaml run --rm --no-deps maintenance retention --dry-run
docker compose -f community-service/compose.yaml up -d
```

The command requires a known current schema, uses a read-only SQLite transaction
and returns bounded candidate counts without identifiers, secret values, deletion
or observations writes. It needs no service secrets/network/listener and works
while runtime configuration is disabled. There is no --confirm/delete/activation
variant. Counts are not reservations, scanned-row totals or disk-reclamation
estimates; payloadBytesReclaimed is zero. Existing exclusive-writer locking refuses
inspection of a running database. See the [retention matrix and evidence](../docs/community-scan-service-tasks/64-retention-cleanup.md).

## Operator Access

CSS-55 implements the operator boundary locally, separate from contributor signing
and the local Editarr administrator session. Configure `ACCESS_ISSUER` (exact HTTPS
team origin), `ACCESS_AUDIENCE` (Access application's AUD), `OPERATOR_ORIGIN`
(canonical HTTPS origin, no trailing slash) and `OPERATOR_SUBJECTS` (JSON array
of exact verified Access `sub` strings). No values or permissive defaults are
committed; real identities and policy remain OPS-02. Do not send credentials or
JWTs through chat or tracked configuration.

All `/admin` paths require a verified RS256 application JWT and current allowlist.
`GET /admin/v1/session` returns only `{version:1,subject}`. Unsafe methods require
exact Origin, `X-Community-Operator: 1` and same-origin fetch context when present.
Unknown API paths return JSON errors, not login HTML or an SPA. No CORS/CLI
exception exists. CSS-56 adds protected moderation operations described below.
Assertions/cookies are stripped
before internal asset fetches; protected responses use no-store and strict CSP.

The isolated `web/operator` / `web/src/settings/community-operator` entry builds
into `web/dist-operator`. Test/build/dev scripts build it first;
install both package dependency sets. The Node asset adapter serves only this output
as `OPERATOR_ASSETS`, behind service authorization with no HTML/SPA fallback.
Only the configured HTTPS operator origin is accepted.
The frontend never imports local bootstrap/auth and never receives deployment
credentials. `pnpm --dir ../web build` still builds the independent local app.

`pnpm --dir ../web exec playwright test --config playwright.operator.config.ts`
checks only the operator frontend with synthetic session responses. Live Access
configuration, session lifecycle, key rotation, provider account configuration and administrative
CLI auth remain unverified. CSS-55 adds no private key or persistent state;
CSS-56's SQLite state belongs in full service backups. Protect deployment configuration
separately in the future hosted backup inventory.
Runtime activation is explicit and disabled by default.
See the [CSS-55 handoff](../docs/community-scan-service-tasks/55-operator-access.md).

## Operator CLI

CSS-59 adds a human Access CLI without a server-side authentication exception.
Use `pnpm operator -- --help` from this package. After approved OPS-02 setup,
log in with `cloudflared access login https://operator-host/admin/`, then run:

```sh
pnpm operator -- status --origin https://operator-host
pnpm operator -- list operations --origin https://operator-host
pnpm operator -- inspect revision revision-id --origin https://operator-host
pnpm operator -- moderate quarantine revision-id --expected-revision 2 --reason 'Reviewed evidence' --origin https://operator-host
```

Values above are placeholders. `EDITARR_OPERATOR_ORIGIN` may replace `--origin`.
The CLI privately retrieves the human application token from cloudflared; Access
must forward the assertion to the existing exact-subject service allowlist. No token
argument, contributor/deployment authority, alternate host or CSRF bypass exists.
Redirects and unbounded/invalid responses fail closed; output is escaped JSON.

Moderation defaults to fresh-state dry-run. Add `--confirm --checkpoint NEW_DIRECTORY`
to approve and durably save the exact UUID/body before sending. Keep these private
0700/0600 directories outside the checkout. `retry --checkpoint DIRECTORY --confirm`
reuses an uncertain intent; stale/conflicting actions require fresh review. Applied
retries read current state rather than resend or project historical state.
`page operations --checkpoint NEW_DIRECTORY` saves one bounded inspection page;
repeat with `--resume` to advance. These are not backups or consistent snapshots.
After hard termination, verify no writer is active before manually removing only
the checkpoint's `lock` file; retain all action/page evidence.

`pnpm test:operator-cli` runs Node subprocess/fake-transport tests. The shared service
comparison is `test/operator-cli-service.test.ts`. Live cloudflared/Access/session
and binding-cookie compatibility remain OPS-02; do not weaken Access settings.
No remote backup/restore jobs, reset, key rotation or deferred feature
pauses are exposed. Current backups use offline Docker maintenance.
See the [CSS-59 commands, recovery and CSS-60/61 adapter handoff](../docs/community-scan-service-tasks/59-operator-cli.md).

### Independent Service Backup

Stop the community container, then run offline maintenance `backup --confirm
--destination /backups/NEW_FILE.sqlite` as described in the
[Docker backup and recovery guide](../docs/community-service-docker.md#persistence-and-recovery).
It verifies schema, integrity, foreign keys and exact row/BLOB fingerprints before
completing a new snapshot. Protect configuration and receipt signing-key history
separately. Snapshots are plaintext until independently encrypted; the same-machine
backup volume does not prove disaster recovery. No schedule or retention is added.
The former CLI `backup` command refuses with `container_maintenance_required` and
cannot read provider credentials or make export requests. The dashboard cannot
attest to operator-owned files and continues to report backup status unavailable.

### Isolated Service Restore

For current SQLite snapshots, use the Docker guide's disabled new-volume restore
procedure. `pnpm operator -- restore` is retained **only for already-created legacy
CSS-60 archive pairs**, not current Docker snapshots or automatic runtime adoption.
It is network-free dry-run. Explicit `--confirm` requires
the completed backup directory (both encrypted archives and original private
`state.json`), an owned 0600 age identity file, a new private destination outside
the checkout, explicit archive/SQL ceilings and artifact bounds. No `--origin`,
provider token, live target, deployment or activation option exists. current Node 24 LTS and
age with POSIX `/dev/fd/3` are required; other platforms remain unverified.

The command validates ciphertext/manifest pairing, exact SQL, known 0014 schema,
all original table inventories, BLOBs/signatures/FKs and completed publication links.
Shared import/vote reconciliation updates derived statistics only, honoring vote
exclusions and preserving suspended voters, neutral votes, key restrictions,
deduplication facts and moderation history. Stale storage counters fail closed.
Untrusted SQL cannot attach other databases or load extensions. No network binding
or production credential environment reaches the isolated import subprocess.

Output is a private SQLite copy, inert disabled configuration record, plaintext
service secrets and provenance report. Protect the entire directory: neither the
configuration nor preserved secrets are installed in a running service. Original
usage periods and unknown fields stay historical, not current provider totals.
Identical `--resume` retries incomplete imports from the same immutable snapshot;
completed/validated outputs are digest checked before cleanup/completion. No reset,
automatic migration, source deletion or production cutover occurs.

See the [CSS-61 command, safety bounds, failure recovery and drill report](../docs/community-scan-service-tasks/61-hosted-restore.md).
Local/mock completion does not certify hosted recovery, off-device protection,
large-database capacity, downtime or live provider costs. Dashboard backup status
remains unavailable. Production cutover needs separate review and authorization.

## Read-Only Operator Dashboard

CSS-57 adds overview/usage, contributions/reports, contributors/keys, operation
history, audits and backup-status hash routes in the isolated hosted frontend.
Every API stays behind Access; no local Editarr bootstrap, login or contributor
credentials are used. Session failure clears protected data; Sign in reloads the
current document while retaining page/selection/cursor context. Refresh failures
label retained snapshots stale. Names, private report text and reasons are escaped.

GET `/admin/v1/dashboard/overview` returns separate payload-storage observations,
partial daily application activity and overlapping download/import/vote/report
observations, each with source/freshness and explicit unknown values. Total SQLite
size, provider account totals and hosted backup/recovery remain unavailable;
payload bytes are never presented as measured total database capacity.
GET `/admin/v1/dashboard/{contributions|contributors|operations|audit}` and
`/admin/v1/dashboard/contributors/{id}/keys` return at most 20 safe rows and a
bounded opaque keyset `nextCursor`; pass it as encoded `after`.
`/admin/v1/dashboard/contributors/{id}/usage` supplies distinct payload and daily
service-passive activity. Existing report queue and moderation inspection are reused.
Operation results, artifact bytes, private Discord IDs and key bytes are excluded.

Dashboard reads share explicit `MODERATION_LIMITS` body/query bounds and
passive request/write observations with moderation. Missing configuration or
unavailable authority verification fails closed; high observations never refuse
access. No mutation controls, provider credential or
backup format is added. CSS-58 owns reviewed controls; CSS-60/61 own approved
backup/recovery sources. Live Access/production remain disabled/unverified.
See the [CSS-57 read contract and verification](../docs/community-scan-service-tasks/57-operator-dashboard-read.md).

## Reports and Reversible Moderation

CSS-56 adds local-only signed `POST /v1/contributions/{revisionId}/reports` with
`{version:1,reason,note?}`. Reasons are spam/inaccurate/privacy/rights/other;
note is private plain text <=1,000 characters. Current member/active key, counted
exact-revision import and non-self authorship are required. One open report per
contributor/revision, exact-operation retries, no automatic quarantine or vote change.
`REPORT_LIMITS` configures only positive requestBytes<=8192 and maxQueries<=64.
There are no daily or maximum-open allowances; one open report per exact
contributor/revision remains a duplicate-intent invariant.

Behind Access/CSRF, `POST /admin/v1/moderation` accepts reasoned actionId/command/
targetId/expectedRevision, with revisionId only for exclude_vote/restore_vote.
Quarantine/restore, suspend/unsuspend and resolve_report use the same audited
service. `GET /admin/v1/moderation/{kind}/{targetId}` (vote adds /{revisionId})
reads one state/version snapshot and <=20 audit rows; `/admin/v1/reports?after=...`
reads <=20 open reports. `MODERATION_LIMITS` explicitly configures positive
requestBytes<=8192 and maxQueries<=64. Missing bounds or unavailable verification
fail closed. Daily request/write observations are passive and partial, not
provider-verified account usage or operator admission limits.

Suspension hides all authored revisions; restore never overrides quarantine,
suspension or untrusted historical author keys. Votes are excluded explicitly,
never deleted; ordinary suspension/access loss does not change totals. Atomic
recalculation honors exclusions. A persisted catalog epoch invalidates visibility
caches/cursors across isolates; final authority checks still apply. No local
downloaded evidence, signing key, source artifact or historical attribution changes.

Additive 0014 is **not applied to existing stores**. Full backups must include
reports, moderation_actions, vote_exclusions, moderation_versions, moderation_epoch,
operator_request_counts, report_usage_observations and original operations/source
facts. Preserve exclusions during reconciliation and restore into a disabled
environment. Existing upgrades require approval and a verified recoverable backup.
Report UI/local transport, CLI authentication, live Access, hosted backup/recovery
and production activation remain downstream. See the
[CSS-56 handoff](../docs/community-scan-service-tasks/56-moderation-service.md).

## Authorized downloads

CSS-42 adds local-only signed `GET /v1/contributions/{revisionId}/artifact`.
It returns exact original gzip/manifest/signature buffers in a bounded JSON/base64
bundle, registered historical author keys and a fresh contributor-bound service
Ed25519 receipt. Downloads do not update popularity. Every immutable-byte cache
hit still requires current authorization, membership and publication state.

No default `DOWNLOAD_LIMITS`, `DOWNLOAD_RECEIPT_PRIVATE_KEY` or
`DOWNLOAD_RECEIPT_KEYS` is configured. The private key is a separate service-only
container secret; public verification/revocation history is trusted configuration.
Do not use synthetic fixture keys or provision real credentials through chat or
tracked files. Known schema through additive 0011 and a separately protected
secret/history backup inventory are required before operational activation.
See the [download/receipt contract](../docs/community-scan-service-tasks/decision-evidence/42-artifact-download-contract.md)
for wire framing, errors, limits and upgrade/backup rules. Production remains disabled.

## Import Acknowledgments

CSS-51 adds local-only signed `POST /v1/import-receipts`. Original service receipts
verify against configured `DOWNLOAD_RECEIPT_KEYS`; no private signer is needed.
Atomic SQLite 0012 facts count one acknowledged import per contributor/revision,
excluding the author, across installations and retries. Equivalent valid issuances
use stable identity; counters are statistics, not trust or playback proof.
The legitimate client sends only after local commit.

`IMPORT_LIMITS` explicitly supplies positive `requestBytes` (at most 8192) and
`maxQueries` (at most 64). These are schema ceilings, not production defaults.
Trusted ingress and current membership/key status apply;
production remains disabled. Before applying additive 0012 to an existing store,
authorize the upgrade and verify a recoverable full SQLite/key-history backup.
The `community-retain-first-1` policy retains immutable import facts indefinitely;
daily observations are not account totals. See the
[retention policy](../docs/community-scan-service-tasks/64-retention-cleanup.md).

## Revision Votes

CSS-52 adds local-only signed `POST /v1/contributions/{revisionId}/vote` and signed
`GET` for the caller's current vote/eligibility; `GET` on the plural `/votes` path
returns only public likes/dislikes. All responses are private/no-store. Writes
require an acknowledged successful import of that exact revision, current member
and active key; self-votes fail. Upload restrictions do not restrict voting.
Suspension/nonmembership denies access but retains votes. No reason codes,
individual histories, local vote UI, reports, tier definitions or ranking is added.

`VOTE_LIMITS` explicitly supplies positive `requestBytes` (<=8192) and `maxQueries`
(<=64); no defaults or production activation. Additive 0013 stores one mutable
contributor/revision vote and daily partial observations. Nonce, owned retry result,
vote and aggregate changes commit atomically. Old retries never overwrite a newer
vote; current state requires a separate read. Preserve vote rows and referenced
operations/keys in full backups. The retain-first policy keeps these indefinitely. Applying 0013
to an existing store requires authorization and a verified recoverable backup.
See the [CSS-52 policy/DTO handoff](../docs/community-scan-service-tasks/52-votes-service.md).

## Revision Quality

CSS-53 adds `community-ranking-wilson-1`: the Wilson lower bound with z=1.96 and
one positive/negative prior vote, using only that revision's likes/dislikes.
Neutral adds no rating evidence; scores are rounded to nine decimals. Catalog
order remains video compatibility, audio compatibility, descending quality, then
ASCII revision ID. Author boost is zero, no age decay, no usage-based admission; author
boosts/tiers are explicitly deferred. Upload/import volume never affects quality.

Current counters are reread on cache hits, with no extra ranking query or stored
score. Ranking version invalidates cache/cursor identities. `community-search-2`
cursors preserve the revision ceiling but reject changed votes/visibility with
`invalid_cursor`; restart lookup without a cursor. Signed artifacts and schema
remain unchanged. No new configuration or production activation is added.
See the [CSS-53 formula/DTO handoff](../docs/community-scan-service-tasks/53-ranking-policy.md).

## Boundaries

- `src/index.ts` handles HTTP dispatch and the redacted error envelope.
- `src/config.ts` declares SQLite and policy/identity runtime dependencies. Missing
  configuration is reported by name only; values and secrets are never logged.
- `src/runtime/contracts.ts` owns the database and asset interfaces;
  `src/runtime/` implements Node HTTP, SQLite, configuration, schema checks and maintenance.
  Dockerfile/Compose bundle only the service/operator UI and publish a loopback origin.
- `test/service.test.ts` checks fail-closed production responses and local
  SQLite bindings. `migrations/0001_identity_operations.sql` provides the
  dormant identity/operation tables; `test/identity-operations.test.ts` checks
  their SQLite constraints and repository transactions. CSS-28 adds local confirmed enrollment.
- `src/budgets/usage.ts` records allowed/denied/retried request and logical-write
  observations. `serviceUsage` aggregates anonymous and contributor observations
  without policy/caps/pauses. Telemetry is best effort, not permission to mutate.
  Historical accounting migrations remain unchanged for preserving upgrades and
  legacy archive recovery. Migrations 0015/0016 retire usage enforcement; removed
  application APIs must not be reintroduced. CSS-24 is retired. Migration 0017
  rewrites stored Radarr and Sonarr provenance to generic values.
- `src/security/contributor-auth.ts` resolves the current key and contributor,
  verifies exact signed bytes, and requires a membership eligibility hook.
  `GET /v1/identity` uses it to return only the signed contributor's status,
  using the CSS-27 bot membership hook. Missing bot configuration fails closed.
  `src/operations/repository.ts` and
  `migrations/0004_request_admission.sql` provide atomic nonce/operation
  admission for future mutations. Do not treat a valid signature alone as
  permission to bypass membership or restart an existing operation.
- `src/integrations/discord/oauth.ts` implements a locally testable browser
  start, explicit confirmation and the fixed `/v1/discord/callback` route.
  Browser state is bound to an HttpOnly cookie and an installation public key;
  a successful callback verifies Discord identity and membership and records
  a short-lived enrollment handoff, not an enrolled account. Configure
  `PUBLIC_ORIGIN`, `DISCORD_CLIENT_ID`, `DISCORD_GUILD_ID` and a secret
  `DISCORD_CLIENT_SECRET` for local integration tests. No live callback is
  enabled: non-local traffic still returns 503, and CSS-28 must finish
  local confirmation client/UI integration before contributors can sign in normally.
  `src/integrations/discord/service.ts` supplies trusted pre-handler admission and
  passive observations. An explicit trusted actor resolver is mandatory. The
  tunnel resolver reads canonical `CF-Connecting-IP`, ignores
  forwarding aliases and rejects `CF-Worker` subrequests. The protected origin
  listener is the trust boundary, not arbitrary headers or runtime metadata.
  Missing configuration returns 503. High or unknown observations never refuse
  requests; replay and revocation remain durable across restarts. No hosted
  namespace or resource was provisioned.
  Token and membership requests reject redirects to avoid forwarding credentials.
  Bounded parsing, cancellation consumption and full-schema OAuth-to-signed-key
  proof tests do not constitute live enrollment or deployment acceptance.
- `migrations/0006_oauth_usage.sql` and `src/integrations/discord/usage.ts` add
  bounded daily anonymous counters and passive aggregate reads across contributor
  observations. No IPs, browser state or Discord IDs are stored in counters.
  Mutation metadata measures known broker/cleanup rows written; accounting
  includes its own row. Native-denied traffic and reads are not recorded;
  outages can leave observations partial. Committed OAuth responses survive
  accounting failure with only `Community-Usage-Status: unavailable` added.
  Monthly byte observations are conservative estimates, not current inventory
  or total database storage. Hourly local-only cleanup bounds deletions to 1000 rows
  per table. Fresh/populated 0001-0005 upgrades are tested; existing-store
  application needs verified recoverable backup and explicit authorization.

`src/integrations/discord/member-provider.ts` and `membership.ts` implement
single-member bot REST using secret `DISCORD_BOT_TOKEN` and `DISCORD_GUILD_ID`.
Positive and negative evidence expires after 900 seconds; expired checks refresh
on use, never grant outage grace, and preserve operator suspension. Shared SQLite
leases/cooldowns bound provider concurrency. No Gateway or retained user tokens
are added. `0007_membership.sql` stores evidence/coordination in Unix seconds;
guild or bot credential rotation invalidates previous authority. Cleanup preserves
active leases/cooldowns. Fresh/populated versions 0001-0006 are tested through
0007; no existing database was migrated. Membership request mutations are not
fully represented in usage counters; recorded observations remain partial.
See the task pack's operator setup for minimal-permission live verification.

CSS-28 adds `POST /v1/enrollments/complete`, signed `GET /v1/identity` and
`PATCH /v1/identity`. Public OAuth start requires `confirmationHash` bound before
OAuth; completion requires the backend-only secret after local administrator
confirmation. Signatures, fresh membership and trusted ingress are mandatory;
registration/key/nonce and rename/operation/name-event commits are atomic. Same active-key/grant
retries reconcile within ten minutes and return current profiles. Reconnecting
the same active installation/key preserves identity; another key for it returns
`recovery_required`, never silently replaces credentials. Names are curated
three-word labels or validated 3-40-code-point custom text, unique among current
NFC/lowercase/NFC keys, with unrestricted valid name changes. Clients must
escape labels, refresh signed profiles and replace cached names/revisions on
responses. `0008_enrollment_profiles.sql` needs a verified backup and authorized
upgrade for existing stores; legacy normalization/collision cases stop for review.
The shared fixture is `tests/fixtures/community/enrollment-profile-v1.json` at
repository root. Local administrator/CSRF transport/UI is not implemented here.

Production configuration is intentionally absent. Later tasks must create
separate deployment configuration with approved policy and secret management,
replace placeholder bindings, and validate Discord and Access identity before
enabling contributor or operator routes. A configured local `APP_ENV` is never
proof that production prerequisites have been met.

CSS-33 adds a distinct `/v1/discord/recovery-start` flow and candidate-signed
`POST /v1/keys/recover` / `POST /v1/keys/recovery-status`, with active-owned
`GET /v1/key-operations/key_recovery/{operationId}` reconciliation. Fresh Discord
ownership, installation/candidate/operation binding and the backend-held confirmation
secret are required; ordinary enrollment cannot replace a key or consume a recovery
grant. The existing SQLite operations/key tables suffice: no new migration was added.
Atomic replacement preserves the contributor and sibling installations, and bounded
cleanup retains committed evidence. The local backend/UI implement explicit approval
and same-candidate recovery; no standalone CLI or operator trigger was introduced.
See the [exact recovery contract and runbook](../docs/community-scan-service-tasks/decision-evidence/33-key-recovery-contract.md).

CSS-31 adds signed key lifecycle routes behind the same trusted ingress and
membership checks: `POST /v1/keys/rotate`,
`POST /v1/keys/revoke`, `POST /v1/keys/rotation-status` and signed contributor-scoped
`GET /v1/key-operations/{key_rotation|key_revocation}/{operationId}`. Rotation binds
old/new proofs to one durable intent and atomically commits nonce, old retirement,
new activation and operation. Historical public bytes and unrelated
installations are preserved. The replacement signs reconciliation without the
retired old private key, using retained public proofs and exact original bytes.
No new schema or live migration is needed. Read the
[versioned proof/recovery contract](../docs/community-scan-service-tasks/decision-evidence/31-key-lifecycle-contract.md)
and [task handoff](../docs/community-scan-service-tasks/31-key-rotation-service.md)
before adding a client. UI triggers/private pending journal remain CSS-32;
lost-key recovery remains CSS-33. Neither a lookup nor normal Discord enrollment
can reactivate retired/revoked credentials.

CSS-38 adds the dormant `0009_catalog.sql` schema and internal repository APIs:
`src/catalog/{records,works,releases,lookup}.ts` for typed identity, immutable
release evidence and bounded indexed published-only queries, and
`src/contributions/repository.ts` for v1/v2 verified-artifact revision planning,
owned durable-operation recovery, bounded immutable lineage and publication
transitions. Signed catalog HTTP routes provide batch availability and matches.
See the [user guide](../docs/community-user-guide.md) for discovery and reuse.
Caps are explicit inputs, not production policy.

Run `node scripts/measure-catalog.mjs 1000` from this package for synthetic local
table/index amplification. It creates its own temporary database and verifies
its pre-upgrade backup; it never migrates an existing store. Local measurements
are not hosted capacity or billing. Read the
[schema/API/backup handoff](../docs/community-scan-service-tasks/decision-evidence/38-catalog-schema-contract.md)
before using these helpers. Existing-store migration needs explicit authorization
and a verified recoverable backup; referenced operations/keys are durable data.

CSS-39 adds signed local `POST /v1/contributions` and owned signed
`GET /v1/publication-operations/{id}` with native admission, active key/member
checks and strictly explicit `PUBLICATION_LIMITS`
JSON. No limits are supplied by default; unconfigured or disabled runtime
requests remain 503. High recorded usage does not pause uploads. No contributor role was added.

`src/contributions/{publication-input,publication,storage}.ts` and
`src/http/publication.ts` preserve original v1/v2 gzip, manifest and detached
signature. One SQLite transaction inserts the nonce, operation, catalog and all three
exact BLOBs, checks byte integrity and key authority, and commits publication plus the
completed result. A failed new publication leaves no partial records to recover.
Identical retries return the saved result even after a lost response; conflicts
never overwrite bytes. Older pending records can still complete on explicit retry.
Failed envelopes are observed and cached success never creates another payload.
Telemetry is best effort and partial, never an admission control.
CSS-40 is retired; no background publication recovery worker is needed.

Migration `0010_d1_artifacts.sql` adds immutable `artifact_payloads`, storage policy
and byte counters. Historical object key/etag columns and retired accounting rows
remain preserved for schema compatibility; there is no R2 binding or runtime use.
Combined parts remain bounded to 1.9 MB as an inherited application policy. The default
aggregate payload admission policy is retired by 0015; monitor overall SQLite size
separately because these counters exclude indexes and operational tables.
Read the [current SQLite storage scope](../docs/community-scan-service-tasks/d1-storage-scope.md)
and [migration procedure](migrations/README.md) before upgrading an existing store.
Production payload/CPU, license, live setup and deployment remain separate gates.
`validationWallMs` is cooperative wall time, not a hard CPU cutoff.