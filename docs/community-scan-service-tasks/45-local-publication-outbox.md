# CSS-45 — Upload approved donations with durable retry state

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally** on October 3, 2026. Only isolated synthetic/mock publication
was exercised. No existing installation database was migrated, no live service was
contacted, and sharing license/production limits remain deferred.

- Prerequisites: [CSS-21](21-local-community-state.md), [CSS-29](29-local-service-client.md), [CSS-37](37-donation-artifact-builder.md), [CSS-39](39-publication-service.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §6; main §7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Donation may be interrupted after the service committed. An outbox must reconcile the original submission, not rebuild different bytes or double-publish.

## Starting points

scanner/community/ publication/outbox; local publication repositories; privileged HTTP routes.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Store exact approved signed bytes/digests and stable submission/operation IDs before network work.
2. Submit bounded uploads, recover service operation status, and retry with fresh request signatures while retaining immutable payload identity.
3. Expose cancel/pending/published/rejected status and safe progress; cancellation after commit must not falsely claim remote deletion.

The remote commit is one D1 transaction. Keep original artifact, manifest and
signature bytes in the local outbox until acknowledgment; use owned operation status
to recover a lost success response. Test whole-publication rollback and response loss;
clients do not inspect storage keys, intermediate parts or backend-specific etags.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Restart/timeout/lost response does not duplicate contributions or lose accepted publication status.
- [x] Changed local scan or consent creates a new explicit preview/revision instead of altering an in-flight upload.
- [x] Offline/service/membership failures pause safely without automatic donation enrollment.

## Verification

- Fault tests before send, during the D1 batch, after remote commit and before local acknowledgment.
- Verify outbox cleanup does not remove evidence needed for a pending operation.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for local/mock scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide publication job/status API and retry/cancel semantics to CSS-46/62.

## Handoff summary for the next task

DONE locally / October 3, 2026 / GitHub Copilot. Changes are uncommitted.

### Durable approval and source identity

`scanner/integrations/community/publications.py` exposes `PublicationOutbox(client,
request_bytes=...)`. `queue(prepared, consent_token=..., consent_version=...,
operation_id=..., contribution_id=..., scan_id=...)` accepts the private backend-only
CSS-37 `PreparedDonation`, never browser-supplied signed bytes or source snapshots.
Use a clean caller-owned Session. It verifies current enrolled service/author/key,
source identity and connection consent; revalidates preview/source/file generation
through `approve_donation`; signs; checks the bounded complete JSON/base64 request;
then commits intent and exact original gzip/manifest/signature buffers together.
It performs no network work. Do not expose preparation objects or consent tokens
in ordinary API responses/logs. Authentication/CSRF approval remains mandatory at
the future CSS-46 queue/preview boundary; a consent token is not authority.

Scan sources retain their existing restrictive scan FK. Authored reviews retain
their actual private `source_kind/source_id` instead of needing an arbitrary scan
link; `scan_id` is optional for review publication. The generic repository accepts
`authored_review_id` only with a persisted review and active matching consent.
Reviews still obey CSS-37's real parent-lineage and synchronization requirements;
no source-free derived artifact exception was introduced. No review or source
finding is edited. Source changes after queueing do not alter saved bytes; a new
approval requires a fresh preview/submission and operation ID.

`scanner/persistence/repository/community_publications.py::stage_publication` is the
transaction-scoped flush-only adapter. Duplicate operation IDs must match all
original intent fields, buffers, source, approval, key and hashes. Callers commit
before sending; never rebuild an existing outbox payload. Legacy publish intents
without exact evidence fail `publication_evidence_unavailable` rather than fabricating
approval or retry bytes.

Additive **unapplied** Alembic `0007_community_publications` follows 0006. The new
`community_publications` table has a restrictive operation FK, three BLOBs,
manifest/signature/preview SHA-256 values, consent token, author key/source reference,
cancel flag, safe error code and validated completed-result JSON. Artifact digest,
service, contributor, installation, submission/root/operation IDs and consent version
stay on `community_operations`. Existing rows and the original four-state check
remain unchanged. Downgrade refuses to discard approved evidence. Supported
unversioned and 0001-0006 upgrades preserve all rows and verify recoverable snapshots;
the populated 0006 WAL test retains signed import evidence and target associations.
Applying this revision to an existing installation requires ordinary authorized
preflight/upgrade with a verified recoverable backup. This task did not apply it.

### Explicit network actions and safe local API

`run(operation_id, resend=False)` checks the owned signed
`GET /v1/publication-operations/{id}` first. It never uploads by default. A completed
published response must exactly match original operation/root/revision/artifact
digest and the bounded result schema before local acknowledgment. `resend=True`
may submit only the saved complete JSON/base64 body to `POST /v1/contributions`,
after a noncompleted/not-found probe and fresh local identity/consent checks.
CSS-29 supplies three bounded attempts, fresh nonce/signature each time, exact
body/operation ID, response limits, no redirects and a 35-second deadline.
No storage identifiers, etags or intermediate parts enter this client.

`EDITARR_COMMUNITY_PUBLICATION_REQUEST_BYTES` is optional, positive and at most
3,000,000; it has **no default**. An explicit caller/test bound is required for
queue/send. This safety ceiling is not a production payload policy. Without a
configured bound, passive status, cancel and owned result reconciliation still
work, but no upload can occur. Ordinary service origin/audience settings remain
required for HTTP client construction.

Local administrator-session/CSRF routes in `scanner/http/community_api.py`:

| Route | Semantics |
| --- | --- |
| `GET /api/community/publications/{operation_id}` | Network-free safe job status. |
| `POST /api/community/publications/{operation_id}/check` | Owned-result reconciliation only; never sends. |
| `POST /api/community/publications/{operation_id}/retry` | `{confirmed:true}`; explicit check-first retry of saved bytes. |
| `DELETE /api/community/publications/{operation_id}` | Persist local cancellation; never remote deletion. |

Responses are no-store, with `operationId`, `contributionId`, `revisionId`,
`artifactDigest`, `state`, `phase`, `cancelRequested`, safe `errorCode`, nullable
validated `result`. `state` is `pending`, `paused`, `published`, `rejected`,
`cancelled` or `cancel_pending`; phase is the persisted operation state, not an
invented byte/percentage progress estimate. Payloads, previews, paths, source IDs,
private keys, consent tokens and remote diagnostics are excluded.

Before sending, the operation durably becomes `submitted`. Network/timeouts,
task cancellation and local acknowledgment failure keep its outcome uncertain.
Offline, service outage, membership, key/recovery and consent failures pause, never enroll.
Known validation/content conflicts are rejected without changing the original
intent. A future explicit check can still reconcile a committed result. Source
evidence integrity is checked before any upload. Cancel before submit prevents
future sends; cancelling a submitted job stays `cancel_pending` even after a
not-found probe, since an in-flight request may commit later. Already-running
bounded transport attempts may finish. A recovered published result always wins
over cancellation and retains `cancelRequested`; there is no remote delete call.

All payloads remain retained, including cancelled/rejected/acknowledged jobs; no
cleanup, background sender, scheduler or startup/restore network action was added.
Full backups retain exact BLOBs, retry identities, cancellation and result/error
state. Settings-only backups exclude scan-linked outbox data. Both plain and
age-scrypt four-mode restore previews verify this without activating the target.

### Verification and limitations

Final combined backend command: **295 passed**.

```sh
.venv/bin/python -m pytest -q tests/test_community_publications.py tests/test_community_repository.py tests/test_community_api.py tests/test_community_transport.py tests/test_community_client.py tests/test_community_donations.py tests/test_community_donation_reviews.py tests/test_community_donation_vectors.py tests/test_community_verified_import.py tests/test_fresh_migrations.py tests/test_existing_migrations.py tests/test_migration_preflight.py tests/test_migration_packaging.py tests/test_version_info.py tests/test_portable_backup.py tests/test_backup_restore.py tests/test_backup_activation.py tests/test_community_rotation_backup.py
pnpm --dir community-service exec vitest run test/publication.test.ts test/publication-failures.test.ts test/publication-http.test.ts test/publication-legacy.test.ts test/publication-safety.test.ts test/publication-storage.test.ts test/publication-validation.test.ts --reporter=dot
```

Worker publication regression: **33 passed / 7 files**, including whole-D1-batch
rollback, concurrent/idempotent publication, original payload storage, owned reads
and lost responses. No Worker source/protocol/schema changed, so no new wire fixture
or Worker build was needed. Python tests include exact-byte rollback/reopen,
three fresh request signatures, committed-response loss followed by a source edit
and restart, crash before local acknowledgment, task cancellation, concurrent local
cancel versus committed result, revoked consent, corrupt bytes, bound refusal,
legacy missing payload, authored-review source identity, local bypass/admin/CSRF
and four-mode backup/restore. Installed wheel/sdist reconstruction/startup tests
verify packaged migration and repository modules.

Changed-file `.venv/bin/ruff check` and formatting checks, editor diagnostics,
documentation link/fence/status checks and `git diff --check` passed. Temporary
review-fixture failures were corrected against the existing lineage/synchronization
contract before the final passing run; no production expectations were weakened.
No live service/Discord, user media, model, existing DB, deployment or frontend
was exercised. Production license, bounds/CPU and other-platform execution remain
unverified. CSS-46 owns the preview/queue UI boundary; no browser queue route was
invented here. G-LICENSE remains deferred; current D1-only/local/mock G-LIMITS,
default-off explicit consent, CSS-12/13 signing and CSS-37 approval rules were reused.

Remaining CSS-45 work: none within local/mock scope. Next eligible task:
[CSS-46](46-manual-sharing-ui.md). Read CSS-30/35C/37/45 handoffs and G-LICENSE,
then wire a protected exact-preview/approval boundary to `PublicationOutbox.queue`
and the safe status/check/retry/cancel routes. Ask before selecting a sharing license
or enabling public donations. CSS-62 must reuse this same durable queue, never
rebuild or auto-enroll while retrying, and preserve explicit opt-in/consent checks.

Direct dependent tasks: [CSS-46](46-manual-sharing-ui.md), [CSS-62](62-automatic-donation.md).

