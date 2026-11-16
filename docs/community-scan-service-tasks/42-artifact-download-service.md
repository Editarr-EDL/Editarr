# CSS-42 — Deliver verified bytes and contributor-bound receipts

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** October 3, 2026 in local/isolated-test scope. Signed authorized
D1 downloads preserve original bytes, use current lifecycle/key fences on cache
hits and issue the user-approved contributor-bound service Ed25519 receipt.
Additive 0011 telemetry is tested but unapplied to existing databases. Production
remains disabled and real key provisioning/hosted limits remain operational gates.

- Prerequisites: [CSS-13](13-artifact-signatures.md), [CSS-25](25-worker-request-auth.md), [CSS-27](27-membership-eligibility.md), [CSS-39](39-publication-service.md)
- Decision gates: [G-DOWNLOAD-RECEIPTS](decisions.md#g-download-receipts), resolved October 3.
- Parent sections: contracts §6–7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Storage contract: [D1-only scope and schema](d1-storage-scope.md).

Authorized D1 payload access preserves current authorization. Cached artifact bytes cannot bypass moderation or reuse another contributor's receipt.

## Starting points

community-service/src/contributions/storage.ts (`readPublication`); catalog
lifecycle queries; receipt signing/verification helpers; historical public keys.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Authorize current key/contributor/membership and contribution lifecycle
   before D1 payload/cache access; record attributable request/read/write usage.
2. Reuse `readPublication` to retrieve exact artifact, manifest and signature bytes;
   check lengths/digests against the immutable revision and preserve gzip semantics.
3. Return contributor-bound receipts using the existing signature contract. Cache
   only immutable bytes; current authorization/moderation and receipt issuance still run.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Downloads return original signed bytes; missing/corrupt payloads fail safely.
- [x] Withdrawn/quarantined or unauthorized contributions are denied even on cache hits.
- [x] Cached bytes cannot reuse another contributor's receipt or inflate popularity.
- [x] No unauthenticated artifact URL is returned; responses preserve digest and
  compression semantics and obey configured D1/query/body limits.

## Verification

- Test exact BLOB round trips, digest/length mismatch, missing payloads, changed
  transport encoding, storage-failure handling and cross-user cache isolation.
- Race moderation against downloads and verify receipt attribution/idempotency.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide download/receipt/key-history contracts and secret inventory requirements to CSS-44/51/60.

## Handoff summary for the next task

Completed October 3, 2026 by GitHub Copilot; uncommitted workspace changes.
The [download/receipt handoff](decision-evidence/42-artifact-download-contract.md)
records actual modules, exact wire/signing bytes, trust/key history, limits,
secret/backup inventory, migration and evidence for CSS-44/51/60.

Implemented signed `GET /v1/contributions/{revisionId}/artifact`: current
requester/member/lifecycle checks before D1/cache access, original
gzip/manifest/signature buffers in bounded JSON/base64, exact transport digest,
registered historical author keys and fresh contributor-bound no-expiry receipt.
Cache stores only immutable parts; final authority/moderation fences deny raced
retirement/revocation/withdrawal. Downloads never mutate popularity or import facts.

The user selected G-DOWNLOAD-RECEIPTS. Existing CSS-12/13/25/27 and D1-only rules
remain unchanged. Separate service signer secret/public-history bindings have no
defaults. `0011_download_usage.sql` adds only daily partial read/write observations
and explicit unknown counts; no existing database was migrated. The independently
Node-generated shared vector is `tests/fixtures/community/download-receipt-v1.json`.

Verification passed: 490 Worker tests/74 files, including 15 new download/receipt/
usage tests, populated 0010 BLOB preservation and supported earlier upgrade chains;
generated types/typecheck, editor diagnostics and dry-run build (738.08 KiB /
gzip 127.69 KiB). Known limitations: no hosted CPU/capacity or live transport/
Discord verification; observations are partial, not billing; production remains
disabled. Apply 0011 to an existing store only after explicit authorization and
independently verified recoverable backup/restore. Service secrets and full public
verification/revocation history need separately protected backups. Receipt
verification/acknowledgment, local imports and counting policy remain downstream.

Remaining task work: none in local mechanism scope.
Next exact action: **CSS-43**, read its brief and local import transaction tests,
then extract reusable transactions without changing local EDL/MCF behavior.
CSS-44/51 first consume this bundle/vector and trust requirements; CSS-60 first
inventories 0011, exact BLOBs and service secret/public-key history.

Direct dependent tasks: [CSS-44](44-verified-local-import.md), [CSS-51](51-import-receipts.md), [CSS-60](60-hosted-backup.md).

