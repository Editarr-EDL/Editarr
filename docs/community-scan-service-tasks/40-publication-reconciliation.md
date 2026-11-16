# CSS-40 — Retired: publication recovery

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **RETIRED** October 3, 2026. No standalone implementation is required.
This is a scope removal, not a claim that a recovery worker was implemented.

## Why this task is no longer needed

[CSS-39](39-publication-service.md) now commits nonce, operation, catalog records,
all three signed BLOBs, capacity counters, published state and saved result in one
D1 transaction. A failed new publication leaves no partial publication to recover.
The historical `reserved` and `object_stored` states are transitions inside that
transaction. No background publication sweeper, stale-reservation expiry workflow,
cross-store compensation or orphan cleanup is required.

A successful commit can still lose its network response. The client retries the
same operation and original bytes and receives the saved result. That is ordinary
idempotency, already supported by CSS-39 and consumed by CSS-45.

Existing schema and historical rows are preserved. An explicit retry may complete
an older pending record; this compatibility path does not introduce a scheduled
recovery service. No deployed R2 data needs transfer.

## Remaining responsibilities

- [CSS-39](39-publication-service.md): atomic publication, rollback, concurrent
  retries, authority checks and saved results.
- [CSS-42](42-artifact-download-service.md): authorized reads and payload integrity.
- [CSS-45](45-local-publication-outbox.md): durable local retry state and handling
  lost responses or interrupted local acknowledgments.
- [CSS-64](64-retention-cleanup.md): bounded transient cleanup and approved retention,
  reusing the existing scheduled cleanup where appropriate.
- [CSS-60](60-hosted-backup.md) / [CSS-61](61-hosted-restore.md): consistent D1 backups,
  restore drills and verification of payloads, references and counters.

No active task depends on CSS-40. See the [D1 storage scope](d1-storage-scope.md)
and [task index](README.md) for the current plan.
