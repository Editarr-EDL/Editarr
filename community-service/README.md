# Editarr community service

The community service runs the community API and operator UI on Node 24 LTS
and SQLite. Development tests and the Docker image use the same database
adapter. The default runtime configuration is disabled. `GET /health` checks
liveness, while contributor and operator routes require explicit configuration.

Use the [Docker setup and recovery guide](../docs/community-service-docker.md)
to configure SQLite, Discord, Cloudflare Tunnel and operator Access. See the
[community service contracts](../docs/community-scan-service-contracts.md) for
the contributor protocol and the [community user guide](../docs/community-user-guide.md)
for connection, discovery and sharing in Editarr.

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
The local app’s scheduled-backup policy is separate and unchanged.

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
inspection of a running database. See the [offline retention guide](../docs/community-service-docker.md#retention-and-offline-preview).

## Operator Access

Operator access is separate from contributor signing and the local Editarr
administrator session. Configure `ACCESS_ISSUER` (exact HTTPS
team origin), `ACCESS_AUDIENCE` (Access application's AUD), `OPERATOR_ORIGIN`
(canonical HTTPS origin, no trailing slash) and `OPERATOR_SUBJECTS` (JSON array
of exact verified Access `sub` strings). No values or permissive defaults are
committed. Do not send credentials or
JWTs through chat or tracked configuration.

All `/admin` paths require a verified RS256 application JWT and current allowlist.
`GET /admin/v1/session` returns only `{version:1,subject}`. Unsafe methods require
exact Origin, `X-Community-Operator: 1` and same-origin fetch context when present.
Unknown API paths return JSON errors, not login HTML or an SPA. No CORS/CLI
exception exists. Assertions/cookies are stripped
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
configuration and CLI sign-in require their own checks. Keep the complete SQLite
state in backups and protect deployment configuration separately. See
[Docker configuration](../docs/community-service-docker.md#configure-before-activation).

## Operator CLI

Use `pnpm operator -- --help` from this package. With operator Access configured,
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
and binding-cookie compatibility require a live configuration check.
Use offline Docker maintenance for backups and restores.

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
encrypted archive pairs**, not current Docker snapshots or automatic runtime adoption.
It is network-free dry-run. Explicit `--confirm` requires
the completed backup directory (both encrypted archives and original private
`state.json`), an owned 0600 age identity file, a new private destination outside
the checkout, explicit archive/SQL ceilings and artifact bounds. No `--origin`,
provider token, live target, deployment or activation option exists. Current Node 24 LTS and
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

The dashboard cannot see operator-owned backup or restore files.

## Read-Only Operator Dashboard

The dashboard includes overview/usage, contributions/reports, contributors/keys, operation
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
access.

## Reports and Reversible Moderation

The hosted service accepts signed `POST /v1/contributions/{revisionId}/reports` with
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

## Authorized downloads

The hosted service accepts signed `GET /v1/contributions/{revisionId}/artifact`.
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
See the [download API](../docs/api-contract.md#community-downloads) and the
[SQLite upgrade procedure](migrations/README.md).

## Import Acknowledgments

The hosted service accepts signed `POST /v1/import-receipts`. Original service receipts
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
[offline retention guide](../docs/community-service-docker.md#retention-and-offline-preview).

## Revision Votes

The hosted service accepts signed `POST /v1/contributions/{revisionId}/vote` and signed
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
See the [vote API](../docs/api-contract.md#hosted-votes).

## Revision Quality

The service ranks candidates with `community-ranking-wilson-1`, the Wilson lower bound with z=1.96 and
one positive/negative prior vote, using only that revision's likes/dislikes.
Neutral adds no rating evidence; scores are rounded to nine decimals. Catalog
order remains video compatibility, audio compatibility, descending quality, then
ASCII revision ID. The score has no author boost, age decay or trust tier.
Upload and import volume do not affect quality.

Current counters are reread on cache hits, with no extra ranking query or stored
score. Ranking version invalidates cache/cursor identities. `community-search-2`
cursors preserve the revision ceiling but reject changed votes/visibility with
`invalid_cursor`; restart lookup without a cursor. Signed artifacts and schema
remain unchanged. No new configuration or production activation is added.
See the [ranking API](../docs/api-contract.md#revision-quality-ranking).

## Service structure

- [HTTP dispatch](src/index.ts) routes requests and returns redacted errors.
- [Runtime adapters](src/runtime/) implement the Node HTTP server, SQLite,
  configuration, static assets and offline maintenance.
- [Contributor authorization](src/security/contributor-auth.ts) verifies signed
  requests, active key state, and contributor eligibility.
- [Discord integration](src/integrations/discord/) handles OAuth and bot
  membership checks. It binds OAuth state to an HttpOnly cookie and the
  installation key. The fixed callback is `/v1/discord/callback`.
- [Operation storage](src/operations/repository.ts) coordinates nonce
  admission and idempotency. [Publication](src/contributions/) preserves original
  gzip, manifest and signature BLOBs and commits the revision and result
  atomically. `GET /v1/publication-operations/{id}` lets a contributor
  reconcile an owned publication.
- The [catalog](src/catalog/) indexes typed identity and immutable release
  evidence for signed batch availability and matches. Run
  `node scripts/measure-catalog.mjs 1000` from this package to measure synthetic
  catalog table and index growth in a disposable database.
- [Usage observations](src/budgets/usage.ts) count requests and SQLite logical
  writes on a best-effort basis. Usage does not grant or revoke access.

## Signing key lifecycle routes

Signed key rotation uses `POST /v1/keys/rotate` and
`POST /v1/keys/rotation-status`; revocation uses `POST /v1/keys/revoke`.
`GET /v1/key-operations/{key_rotation|key_revocation}/{operationId}` reconciles
an owned operation. Rotation binds old and new proofs to one intent and retires
the old key in the same commit that activates the replacement. Retain exact
proofs and operation bytes for retries.

Lost-key recovery starts at `/v1/discord/recovery-start`. Candidate-signed
`POST /v1/keys/recover` and `POST /v1/keys/recovery-status` bind fresh Discord
authorization, the installation, the replacement, the operation and the private
confirmation secret. An active owned key can reconcile with
`GET /v1/key-operations/key_recovery/{operationId}`. See the
[enrollment guide](../docs/community-scan-service-discord-enrollment.md).
