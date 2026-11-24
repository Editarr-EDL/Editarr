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

## Source runtime journeys

`pnpm run test:runtime` runs the real Node HTTP server, SQLite adapter and baseline
migration with mocked Discord and Access providers. The identity, sharing, operator and
cleanup journeys need only this package. The sharing journey submits a signed
`community-artifact-3` artifact, matches it, downloads and verifies the signed master
snapshot and receipt, votes, reports, syncs revisions and quarantines and restores the
version through the operator API.

The `multi-instance` journey drives full Editarr applications through the repository
Python environment (`.venv/bin/python -m tests.community_e2e`, or
`COMMUNITY_E2E_PYTHON`). It exercises the client sharing flow, so it passes only with a
client that speaks the master-set protocol. `pnpm --dir community-service test:e2e`
runs it alone.

## Retention Preview

October 8 approved community-retain-first-1 keeps durable signed submissions,
master revisions, public-key/identity history, operations, download receipts, votes,
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
pnpm operator -- inspect filter filter-id --origin https://operator-host
pnpm operator -- moderate quarantine filter filter-id --expected-revision 2 --reason 'Reviewed evidence' --origin https://operator-host
pnpm operator -- moderate exclude_vote vote filter-id --contributor contributor-id --expected-revision 0 --reason 'Reviewed evidence' --origin https://operator-host
pnpm operator -- list versions --status candidate --origin https://operator-host
pnpm operator -- moderate merge_version version version-id --into target-version-id --expected-revision 1 --reason 'Duplicate cut' --origin https://operator-host
pnpm operator -- moderate approve_alias alias_suggestion suggestion-id --alias theatrical --expected-revision 0 --reason 'Common spelling' --origin https://operator-host
pnpm operator -- recompute all --reason 'MASTER_POLICY changed' --origin https://operator-host
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
`approve_alias` takes `--alias ALIAS_ID`, or `--new-alias ALIAS_ID --label TEXT` to create
an alias. `recompute <VERSION_ID...|all>` is also a dry-run until `--confirm --checkpoint`;
an `all` batch recomputes up to 200 versions and returns `nextAfter`, which the next
run passes as `--after`. A recompute retry resends the same action, which changes
nothing when no input changed.
`page operations --checkpoint NEW_DIRECTORY` saves one bounded inspection page;
repeat with `--resume` to advance. These are not backups or consistent snapshots.
After hard termination, verify no writer is active before manually removing only
the checkpoint's `lock` file; retain all action/page evidence.

`pnpm test:operator-cli` runs Node subprocess/fake-transport tests. Live cloudflared/Access/session
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
procedure. The former `pnpm operator -- restore` command for legacy hosted archives
was removed with the revision-era schema; those archives cannot be restored into the
`community-master-1` lineage.

## Operator Dashboard

The dashboard includes overview/usage, versions (with their master filters and
submissions), the alias suggestion queue, reports on filters and versions,
contributors/keys, operations, a combined moderation audit, a resource lookup for
filters, attestations, submissions and votes, and an all-versions recompute with
continuation. Every action uses the reasoned, fresh-inspection confirmation of §7.8.
`GET /admin/v1/dashboard/{contributors|operations|audit}`, the per-contributor `keys`
and `usage` routes and the per-version `filters` and `submissions` routes return 20
rows per page with opaque cursors; filters show attestation counts only. Overview storage is the stored submission bytes in `artifact_storage_usage`;
database size, provider usage and backups are reported as unavailable. The dashboard
cannot see operator-owned backup files.

## Master filter sets

The contributor routes of [contract §7](../docs/community-scan-service-contracts.md#7-service-endpoints)
are signed requests behind edge admission:

- `POST /v1/match` and `GET /v1/aliases` (`src/http/match.ts`).
- `POST /v1/submissions` (`src/http/submissions.ts`, `src/submissions/`): strict
  `community-artifact-3` verification against the request signer (`src/artifact/`),
  version acceptance (`src/versions/`), raw signed BLOB storage and the master-set
  recompute, all in one guarded batch that is planned again after a concurrent catalog
  write. `SUBMISSION_LIMITS` holds the §5.2 bounds.
- `GET /v1/versions/{versionId}/master[?knownRevision=N]`, `GET /v1/receipt-keys`
  (`src/http/master.ts`, `src/master/download.ts`, `src/master/signing.ts`): the stored
  immutable snapshot with a service signature and a download receipt, both from the
  `DOWNLOAD_RECEIPT_PRIVATE_KEY` signer.
- `POST /v1/filters/{filterId}/votes` and `GET /v1/versions/{versionId}/votes`
  (`src/master/votes.ts`): receipt-bound votes; `VOTE_LIMITS` bounds the request.
- `POST /v1/sync/revisions` (`src/master/sync.ts`) and `POST /v1/reports`
  (`src/master/reports.ts`, `REPORT_LIMITS`).

`src/filters/` is the pure merge engine. `src/master/state.ts` reads a version's inputs
and `src/master/recompute.ts` writes the registry, filters, attestation assignments,
runtime, trust and a new snapshot revision when served content changes. Every
writer guards the work's `catalog_seq`, so concurrent writes are planned again
instead of lost.

## Reports and Reversible Moderation

`POST /admin/v1/moderation` takes the §7.8 envelope with `resource`. Catalog commands
(`confirm_version`, `merge_version`, `approve_alias`, `reject_alias`) run in
`src/versions/moderation.ts` with a recompute extension (`src/admin/catalog-extension.ts`).
Quarantine and restore of filters, attestations, submissions, contributors and versions,
vote exclusion, suspension and report resolution run in `src/admin/moderation.ts` and
`src/admin/master-moderation.ts`. Each action is audited with its outcome, checks
`expectedRevision`, and recomputes every affected version in the same batch, so a
restore returns the served content it removed. `POST /admin/v1/recompute`
(`src/admin/recompute.ts`) applies a changed `MASTER_POLICY` to listed versions or all
versions. `GET /admin/v1/moderation/{resource}/{id}`, `/admin/v1/reports`,
`/admin/v1/versions` and `/admin/v1/alias-suggestions` are the read routes.

## Service structure

- `src/index.ts` routes requests; `src/http/contributor-route.ts` applies the shared
  method, exact-URL, body, signature, error and usage rules of contributor routes.
- `src/security/` verifies request signatures and holds the service signer.
- `src/identity/`, `src/integrations/discord/` and `src/operations/` own enrollment,
  keys, membership and idempotency.
- `src/runtime/` is the Node server, SQLite adapter and offline schema runner, which
  refuses stores from any other schema lineage.

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
