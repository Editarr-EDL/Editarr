# CSS-39 — Publish signed artifacts with durable D1 state

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Current implementation: [D1-only storage](d1-storage-scope.md), October 3, 2026.

Status: **DONE** October 3, 2026, in the user-approved local/mock scope with
explicit limits and production disabled. No production payload/CPU limit,
deployment, existing-database migration or real-content publication is authorized.

- Prerequisites: [CSS-13](13-artifact-signatures.md), [CSS-25](25-worker-request-auth.md), [CSS-27](27-membership-eligibility.md), [CSS-38](38-d1-catalog-schema.md)
- Decision gates: [G-CONTENT](decisions.md#g-content), [G-LIMITS](decisions.md#g-limits)
- Parent sections: contracts §6.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

All current community storage is in SQLite. Publication stores the original gzip artifact,
manifest and detached signature in `artifact_payloads`, then publishes the catalog
entry and completes its owned operation in the same transaction. Readers must never
see an incomplete or unvalidated contribution.

## Starting points

- `community-service/src/contributions/publication.ts`: signed validation and passive usage
  and atomic publication transaction.
- `community-service/src/contributions/storage.ts`: `publicationStorageStatement`
  and internal `readPublication`; exact immutable bytes and digest checks.
- `community-service/src/contributions/repository.ts`: catalog identity and lifecycle.
- `community-service/src/http/publication.ts`: upload/status routes and safe errors.
- `community-service/migrations/0010_d1_artifacts.sql`: payloads, capacity counters,
  immutable constraints and publication guards.

## Work within this task

1. Validate signature/authenticated author/schema/content policy, enforce explicit
   per-operation bounds, and recompute summaries before the publication transaction.
2. In one SQLite batch insert the operation, nonce, catalog records and all three BLOBs,
   update passive byte observations, recheck active authority, publish and save the completed
   result. Request-attempt telemetry is best effort and passive so
   rejected authenticated work still counts.
3. Keep retries byte-identical and expose only published eligible contributions.
   `reserved` and `object_stored` are schema lifecycle transitions within the same
   transaction. A failed new upload leaves no publication records to recover.

## Acceptance criteria

- [x] Exact gzip, manifest and signature bytes round-trip through D1 without re-encoding.
- [x] Duplicate or concurrent retries converge on one payload and result; they do not
  increase stored-byte counters twice. Conflicting bytes fail without replacement.
- [x] Any publication-batch failure rolls back nonce, operation, catalog, payloads,
  byte counters and publication/result changes together.
- [x] Lost success responses and concurrent identical requests return the saved
  result on retry without another payload or byte-count increase.
- [x] Authority and per-operation validation bounds are enforced. Historical storage
  caps cannot block publication; oversized artifact configuration fails validation.
- [x] Migration 0010 preserves existing records from supported schemas, with no
  reset, unknown-schema stamp, or automatic inference that legacy bytes were copied.

## Verification

The historical Worker suite passed all 462 tests across 69 files, including full
publication rollback, then-current capacity/key refusal, concurrent identical/conflicting requests,
lost responses, and explicit retries of older `reserved`/`object_stored` records.
TypeScript checking and the Wrangler dry-run build pass. Populated schema 0009 and
fresh/0001–0008 upgrade tests apply 0010 and preserve records and foreign keys.
See [the verification record](d1-storage-scope.md#verification).

All database tests use isolated fixtures. This does not establish hosted CPU,
production capacity, backup tooling or deployment readiness.

## Definition of done

- [x] Local publication behavior, migration and relevant verification are complete.
- [x] Wire/signature and client operation contracts remain compatible.
- [x] The task index and current handoff describe D1-only persistence.
- [x] Unimplemented downstream workflows and production gates remain explicit.

## Handoff summary for the next task

DONE locally, updated October 3, 2026. Signed POST `/v1/contributions` and owned
GET `/v1/publication-operations/{id}` keep their existing transport and results.
`publishArtifact` no longer takes a bucket. `publicationStorageStatement` prepares
one immutable BLOB row for the publication transaction. `readPublication` is an
internal exact byte reader: CSS-42 must authorize the caller and current lifecycle before using it.

The retained `object_key` is a D1 storage identifier; new `object_etag` values use
the artifact SHA-256. Migration 0009 remains unchanged and 0010 adds the payload
schema. Per-operation payload/schema bounds remain explicit. Aggregate payload caps and recorded-usage pauses are retired by migration 0015; byte observations remain passive. Storage failures return service-unavailable without partial publication state. Retry with the original operation/content and a fresh nonce; completed retries return their saved result.

CSS-40 is retired: new publications need no recovery worker. Existing pending rows
remain compatible with explicit retries; no schema or historical rows are rewritten.
CSS-42 owns authorized read integrity; CSS-64 owns bounded cleanup and approved
retention. Current triggers forbid immutable payload/revision deletion. CSS-60/61 must
export/restore BLOBs with the rest of D1 and verify digests, signatures, foreign keys
and storage-counter consistency. Existing-store upgrades require a recoverable
backup verified by isolated restore. No deployed R2 data needs transfer.

Production routes remain disabled. Hosted CPU/query/capacity measurements, sharing
license and launch remain separate gates. CSS-25 remain partial for other routes.
The [earlier publication evidence](decision-evidence/39-publication-contract.md)
records the superseded implementation and measurements; use this handoff and the
[current storage scope](d1-storage-scope.md) for new work.

Direct dependent tasks: [CSS-56](56-moderation-service.md), [CSS-59](59-operator-cli.md), [CSS-60](60-hosted-backup.md), [CSS-64](64-retention-cleanup.md), [CSS-41](41-catalog-lookup-api.md), [CSS-42](42-artifact-download-service.md), [CSS-45](45-local-publication-outbox.md), [CSS-63](63-sharing-policy-withdrawal.md).
