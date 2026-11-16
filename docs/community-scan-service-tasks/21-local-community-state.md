# CSS-21 — Add durable local community records and operations

Status: **DONE**, September 27, 2026. Implementation evidence and handoff follow.

- Prerequisites: [CSS-06](06-migration-startup-cli.md), [CSS-08](08-provenance-schema.md), [CSS-14](14-local-key-storage.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §5–6; implementation §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Remote identity, provenance and retry state cannot live only in React memory. Local IDs are not global media identifiers.

## Starting points

scanner/persistence/rows/ community tables; focused repository modules; Alembic revision.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add endpoint/contributor/installation/key metadata, imported revision/digest linkage, publication intents, pending receipts/votes and consent-state storage; include the installation ID in backup inventory regardless of protection mode.
2. Define uniqueness for service/revision/digest and durable operation IDs; separate private key files from queryable metadata.
3. Provide transaction APIs for scan-plus-provenance commits and pending-state reconciliation after restore.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Migration preserves existing data and fresh/upgrade parity; duplicates conflict or reuse by contract.
- [x] Restoring retry records cannot invent new contribution IDs/counts; secrets do not enter normal settings responses.
- [x] Default sharing is disabled and no network behavior starts merely because tables exist.

## Verification

- Test migration, transaction rollback, duplicate import/publication intents and restored pending state.
- Run affected repository/auth regressions.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide table constraints, transaction APIs and outbox state diagrams to CSS-29/44/45/51/62.

## Handoff summary for the next task

Status / date / implementer: DONE, September 27, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Added local community identity
metadata, imported-scan links and persistent publish/receipt/vote intents. No
community network action or automatic sharing occurs. A database retaining an
enrolled contributor cannot silently mint a replacement installation key if
the matching private identity files are gone. Full backups include pending
operations; settings backups include connections but omit scan-linked imports
and operations. Plain and age-scrypt manifests both inventory the installation
ID and restore checks it against the validated identity files. Older archives
without this new optional inventory field remain restorable.

Files changed and relevant revision: `scanner/persistence/migrations/versions/0003_local_community.py`
and the schema/adoption/row/repository modules, `scanner/db.py`, backup archive
and restore, focused migration/backup/repository tests, this brief and task
index. Versioned upgrade recognizes 0001, 0002 and 0003; before touching an
existing 0001/0002 database it validates its exact packaged shape and creates
and verifies a recoverable pre-upgrade snapshot. Unknown schemas still fail.

Contracts / APIs / schemas / migrations / fixtures introduced:

- `community_connections`: primary key `service_url`; contributor,
  installation and public key ID; `sharing_enabled` defaults false; consent
  version/time. Private key files remain outside SQLite and settings responses.
- `community_imports`: unique `scan_id` FK (cascade on delete), unique
  `(service_url, revision_id, digest)`, plus contribution/author/type. Check
  `find_import` before creating a scan. `attach_import` flushes within the
  caller's transaction alongside `create_scan(..., commit=False)`; reuse the
  existing scan for an identical remote artifact, reject conflicting metadata
  or a second artifact on the same scan. Commit only after both writes succeed.
- `community_operations`: immutable primary key `operation_id` and original
  contributor/installation/service, remote identifiers, digest, scan ID,
  consent version, exact receipt or vote, state and timestamp. `enqueue_operation`
  returns a same-ID identical intent, rejects conflicting reuse; `set_operation_state`
  advances it without mutating identifiers, and cannot reopen acknowledged work.
  `pending_after_restore` only lists existing pending/submitted/failed records
  matching the supplied restored identity; publish also requires current
  sharing consent. Neither function sends requests or generates remote IDs.
  `load_local_identity(db, data_dir)` must precede signing-key initialization
  after persistence is loaded; it rejects missing or mismatched identity files
  when a contributor is recorded.

Local outbox state diagrams for CSS-29/44/45/51/62:

```text
publish (consent required): pending -> submitted -> acknowledged
                                        |            |
                                        v            v
                                      failed      terminal
receipt / vote:         pending -> submitted -> acknowledged
                           ^           |
                           +-- failed <-+
```

On restore, treat submitted as uncertain; query the service with the *same*
operation ID before retrying. Failed and pending may be reconciled only under
the original identity; never infer a new contribution ID or increment a count
locally. The service decides whether a receipt/vote was counted. Revoked sharing
pauses publication, not already queued receipts/votes. CSS-44 must verify
artifacts and stage local scans using the transaction API; CSS-45/62 supply
approved bytes and consent, CSS-51 supplies the service-signed receipt, and
CSS-29 supplies transport and remote-state reconciliation.

Decisions used (gate IDs and recorded answers): Existing D06 optional backup
encryption, D18 manual-only key rotation, and approved default-off sharing.
No new product gate decided here.

Verification commands and results: `.venv/bin/python -m pytest -q` on
`tests/test_community_identity.py`, `tests/test_auth_crypto.py`,
`tests/test_community_repository.py`, fresh/existing/preflight/packaging
migrations, version info, portable backup, backup restore/activation/API:
150 passed, including full-restore pending-operation staging.
`.venv/bin/ruff check` on touched Python files passed. No live database was
opened for testing.

Known failures, limitations, unavailable checks, and recovery notes: A copied
backup with the same logical installation may need explicit operator recovery
if identity files are missing; do not create a replacement key. The repository
APIs are transaction-scoped and do not expose transport, signer or count logic;
downstream features must pass a verified artifact/receipt and explicitly commit.
macOS tests do not establish native Windows restore behavior.

Remaining work in this task: None.

Next task ID and exact first action: CSS-29, after CSS-25 and CSS-28; read
this handoff and the signed-request profile, then bind service operations to
the durable operation ID and the guarded local identity loader. CSS-44/45/51/62
should consume the transaction and outbox interfaces when their prerequisites
are complete.

Direct dependent tasks: [CSS-29](29-local-service-client.md), [CSS-37](37-donation-artifact-builder.md), [CSS-44](44-verified-local-import.md), [CSS-45](45-local-publication-outbox.md), [CSS-62](62-automatic-donation.md).
