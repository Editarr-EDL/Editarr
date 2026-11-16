# CSS-38 — Add hosted work, release and contribution records

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Current storage: [D1-only storage](d1-storage-scope.md), updated October 3, 2026.
> Dated completion evidence below may describe the earlier storage implementation.

Status: **DONE** October 3, 2026, local isolated-test scope. Additive D1 0009,
internal repositories and measured indexed lookup are implemented; no existing
database migration or deployment was performed by that catalog task. CSS-39 now
provides local D1-only publication under migration 0010.

- Prerequisites: [CSS-11](11-artifact-contract.md), [CSS-23](23-d1-identity-schema.md), [CSS-36](36-compatibility-policy.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §5.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

D1 indexes searchable summaries and stores immutable event payloads, manifests and
signatures as BLOBs under migration 0010. Contributor-supplied aliases must not merge unrelated works globally.

## Starting points

community-service/migrations/; src/catalog/; src/contributions/ repositories.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add typed work identifiers, releases/fingerprints, immutable contribution revisions and bounded parent relationships.
2. Index lookup paths, use globally unique provider/type/value rules and protect supported alias reconciliation.
3. Add publication lifecycle, digest/storage references and summary columns. Findings
   remain inside the D1 artifact BLOB, not one catalog row per event.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Invalid references/cycles/duplicate revision IDs fail; filenames are never globally unique identifiers.
- [x] Fresh/upgraded schema supports bounded indexed batch lookup and immutable authorship.
- [x] Mutable counts/moderation state remain separate from signed claims.

## Verification

- D1 constraints/index-plan and concurrent insert tests.
- Measure representative storage/index amplification and SQL parameter limits.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide D1 revision, repository APIs, lifecycle states and indexed query plan to CSS-39/41/51/56.

## Handoff summary for the next task

Migration 0009 remains unchanged. Migration 0010 adds immutable artifact payloads,
capacity policy/counters and guards against publishing missing payloads. Read the
current CSS-39 handoff before extending the catalog; exact bytes and catalog now
share one database and backup. Existing stores require a verified recoverable backup.
See [current scope, schema and verification](d1-storage-scope.md).

## Historical completion evidence

DONE locally, October 3, 2026, GitHub Copilot. The
[complete schema/API/lifecycle/index/backup evidence](decision-evidence/38-catalog-schema-contract.md)
is the task-specific handoff to CSS-39/41/51/56.

Implemented outcome: immutable typed work/release/revision evidence, protected
global identifiers versus release-level assertions, published-only bounded indexed
lookups, bounded older-parent DAG and supersedes, historical authorship, durable
owned operation recovery and separate mutable publication/statistics rows.

Files/revision: `community-service/migrations/0009_catalog.sql`,
`community-service/src/catalog/{records,works,releases,lookup}.ts`,
`community-service/src/contributions/repository.ts`, five catalog behavior test
files, nine fresh/upgrade test files and two shared helpers,
`community-service/scripts/measure-catalog.mjs`, package/migration READMEs and
the contracts/index/evidence documents. Existing signed v1 and CSS-37 v2 fixtures
are reused unchanged. Prior migrations and Python/frontend behavior are unchanged.

Contracts/APIs: prepared `workStatements`, `releaseStatements`,
`revisionStatements`, `publicationStatement` allow future atomic admission and
finalization; convenience executors are not authenticated/idempotent routes.
`readReservation` is contributor/operation scoped. Typed-ID, fingerprint and
episode lookup helpers expose only published metadata. Full lifecycle and index
details are in the evidence. All policy caps are explicit inputs; hosted 100-bind
technical limit is enforced per statement.

Decisions: existing CSS-11/13 artifact/signature, CSS-23 identity/operations,
CSS-34/35/36 typed matching and CSS-37 approved v2 rules. No new product gate in
this task. G-LIMITS, license and launch choices are not resolved by storage tests.

Verification: `pnpm --dir community-service exec vitest run` passed 432 tests in
61 files (27 new catalog/upgrade tests); `exec tsc --noEmit`, dry-run `run build`
with `WRANGLER_SEND_METRICS=false`, editor diagnostics and synthetic measurement
at 100/1000 revisions passed. All supported 0001-0008 upgrades preserve prior
application rows. 1000 synthetic revisions plus root use 2969600 table bytes and
954368 index bytes, 3923.968 bytes/requested revision including indexes. These
are local estimates, not hosted billing/capacity. Docs/whitespace validation is
recorded with this handoff.

Limitations/recovery: no known failing checks. No existing database was upgraded;
authorize application only after a verified recoverable backup. Full D1 inventory
includes new catalog tables, operations, historical keys and revision; R2 backup
and reconciliation remain separate. No production CPU/load/storage, live R2,
route/auth integration, upload retries, receipts, moderation authority or
deployment acceptance is claimed. CSS-25 downstream integrations remain partial.

Remaining work in CSS-38: none. Next CSS-39: read CSS-13/24/25/27/38 handoffs and
the publication brief, inspect atomic trusted admission and catalog plan/recovery
APIs, then resolve G-CONTENT/G-LIMITS prerequisites before any upload enablement.

Direct dependent tasks: [CSS-39](39-publication-service.md), [CSS-41](41-catalog-lookup-api.md), [CSS-51](51-import-receipts.md).

