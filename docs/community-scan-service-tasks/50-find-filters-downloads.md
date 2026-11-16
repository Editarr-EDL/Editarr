# CSS-50 — Connect multi-select downloads to local review reuse

Status: **DONE** locally with synthetic/mock verification, October 4, 2026.
No live service activation or production resource limits are implied.

- Prerequisites: [CSS-44](44-verified-local-import.md), [CSS-49](49-find-filters-results.md)
- Decision gates: None beyond the shared rules.
- Parent sections: main §7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

One failed selection must not discard successful downloads. Downloads create immutable evidence and should not overwrite reviews or sidecars.

## Starting points

web/src/scans/find-filters/; local download job endpoints; review/catalog integration.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Wire explicit Download selected with bounded progress/cancel/per-item retry and already-imported reuse.
2. Require deliberate review for uncertain candidates; preserve source timeline and existing review workflow.
3. Reflect downloaded/not-reviewed and pending receipt states without blocking successful local use.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Multiple selections import idempotently with independent outcomes.
- [x] Closing/cancelling UI does not lie about committed work or duplicate it on reopen.
- [x] No automatic filter application, saved-review overwrite or sidecar export happens on download.

## Verification

- Browser journeys for multi-download partial failure, reuse, cancelled request and uncertain match review.
- Verify source attribution remains visible in a saved review assembled from downloaded evidence.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide end-to-end discovery/import flow, per-item state machine and review reuse proof to CSS-54/66.

## Handoff summary for the next task

### Outcome and approved contract

DONE / October 4, 2026 / GitHub Copilot. Baseline:
`4739cb7bf5901193d8849ac6baadb6b3c08c72d3`; changes remain uncommitted.
The user answered **"Yes"** to extending authenticated match responses with the
existing root `contributionId`, including hosted query/projection, strict local
and frontend DTOs and fixtures. [Hosted matches](../../community-service/src/catalog/search.ts)
now expose that root separately from `releaseId`; no root is invented or trusted
solely because the browser supplied it. Existing G-MATCHING, exact stream timing,
CSS-44 service-origin key trust and immutable byte/receipt contracts are unchanged.

### Discovery, import and review flow

1. CSS-48/49 resolve the selected catalog version/audio snapshot after explicit
  lookup. All remote channels start unchecked. The user selects individual
  channels or explicitly selects strong channels; uncertain selections require
  a separate review-confirmation checkbox bound to the current selection.
2. [use-community-downloads.ts](../../web/src/scans/use-community-downloads.ts)
  holds a bounded sequential queue outside the modal. Before sending, it asks
  for local committed status, reuses approved imports and preserves independent
  successes. [community-download-panel.tsx](../../web/src/scans/community-download-panel.tsx)
  provides Download selected, cancellation, per-item retry and status checks.
3. Protected `POST /api/community/downloads` accepts one strict
  `{lookup,choice,confirmed}`; choice contains root/revision/digest and one or two
  unique audio/video channels. [local_downloads.py](../../scanner/integrations/community/local_downloads.py)
  captures the server-resolved target and verifies the current target key/file/track.
  It re-reads authenticated matches (at most six 16-item pages), validates the
  root/revision/digest and recomputes per-channel compatibility. Fresh receipt-key
  history, original signature/receipt verification and exact digest checks precede
  the CSS-44 import transaction. Incompatible selected channels remain refused.
4. [community_imports.py](../../scanner/persistence/repository/community_imports.py)
  atomically imports/reuses whole immutable signed evidence. Existing private
  target evidence JSON retains `selected_channels` and `lookup_target_key`.
  Explicit later approvals can add channels only with unchanged target evidence;
  legacy associations retain their approved whole-artifact scope. Original cues,
  timing, source attribution and all three signed buffers remain unchanged.
5. Receipt intent is independently staged after commit. Local success remains
  usable with pending or unavailable receipts. This task sends no acknowledgment
  and increments no service count. Status also reports whether this target's
  evidence is already included in a saved review, rather than claiming every
  reused source is unreviewed.
6. Successful imports reload catalog history. Safe local `Scan.community_approvals`
  records identify target source/item/version and channels, not resolved-path or
  evidence fields. [community-review-scans.ts](../../web/src/review/community-review-scans.ts)
  restricts the existing picker without mutating original scan metadata.
  Review membership, filter-save and export guards enforce approval server-side.
  Scoped both-channel source cues narrow only in copied review snapshots.
  The review picker retains the original author ID; the user must explicitly
  assemble/save a review. Downloads never apply filters, retime, save a review
  or export sidecars automatically.

### Per-item state and cancellation

```text
explicit start -> queued -> local status reconciliation
   already committed with approved channels -> reused
   otherwise -> verifying -> imported/reused | failed | unknown
cancel -> queued items cancelled; in-flight item unknown until reconciled
reopen/check -> committed exact target/root/revision/digest/channel scope wins
   absent requested approval -> cancelled/not imported by that attempt
failed/cancelled retry -> reconcile first, then send only that item if needed
```

Queue IDs include target key, root, revision, digest and sorted channels; target
changes abort pending work and cannot reuse old approval accidentally. Responses
from old generations are ignored. Controller state survives dialog close; local
persisted status reconstructs committed evidence after a fresh lookup/reload.
`POST /api/community/downloads/status` accepts
`{target,target_key,choices}` (at most 96) and reads local immutable imports,
target approvals, original receipt operations and saved-review membership without
requiring network/community membership. Partial channel approval is not success
for a larger requested scope. Local status checking remains available for stale
results; stale rows still cannot initiate downloads.

[community_downloads_api.py](../../scanner/http/community_downloads_api.py) requires
a real administrator session, same origin and CSRF even in local/external bypass
modes. Both endpoints are in the protected route inventory and return no-store.
Disconnect sets cooperative cancellation before task cancellation. Threaded
capture/import is shielded and drained before the request Session closes. Once
commit starts, cancelling cannot revoke it: the UI reports unknown, not rollback,
and reconciliation recovers actual persisted truth. The shared modal now handles
Escape for the topmost dismissible dialog after asynchronous focus loss.

### Files, persistence and operational limits

New focused modules: strict [download models](../../scanner/modeling/model_community_downloads.py),
local adapter/API above, [browser API](../../web/src/api/api-community-downloads.ts),
queue/panel, and review projection. Existing lookup DTOs/fixtures, catalog/history
DTOs, repository review guards, auth inventory, settings and modal are extended.
No database columns, migrations, new secrets or artifact/signature formats are
introduced. Existing full-database backups retain the existing target JSON rows;
backup/restore suites were not rerun for these metadata keys. Existing data was
not migrated or used as a fixture.

`EDITARR_COMMUNITY_DOWNLOAD_BOUNDS` is an explicit JSON ResourceBounds object
(`compressed_bytes`, `expanded_bytes`, `events`, `text`, `depth`).
`EDITARR_COMMUNITY_DOWNLOAD_RESPONSE_BYTES` is explicit and at most 3,000,000.
Both default to unset; sending fails `downloads_not_configured` without them.
Synthetic fixture limits are not production defaults or G-LIMITS approval.
The 96-choice/one-at-a-time client queue and bounded match traversal are technical
safety bounds, not hosted spending or popularity policy. File/demux checks are
cooperative, not preemptive hard native-read/CPU deadlines.

Coordinate Worker/local-client release: new strict DTOs require `contributionId`,
and older extra-forbid clients can reject its addition. An old/mismatched catalog
fails closed; do not fabricate a root to maintain apparent compatibility.
No live Discord/service/provider/media/model test, existing-database migration,
deployment, receipt sending, rating formula, feedback control, Other Files entry
or automatic enrollment/library polling was added. Other browser engines/native
packages and real hosted performance remain unverified. Frontend-only preview:
`http://127.0.0.1:5183/` (HTTP 200 verified); no backend was started incidentally.

### Verification evidence

```sh
.venv/bin/python -m pytest -q tests/test_community_local_downloads.py tests/test_community_import_channels.py tests/test_community_verified_import.py tests/test_community_lookup.py tests/test_community_api.py tests/test_auth_policy.py tests/test_review_repository_create.py tests/test_review_repository_save.py tests/test_review_repository_export.py tests/test_review_repository_integrity.py
pnpm --dir web exec vitest run
pnpm --dir web build
pnpm --dir web exec playwright test e2e/find-filters.spec.ts e2e/community-results.spec.ts e2e/community-downloads.spec.ts
pnpm --dir community-service exec vitest run test/catalog-search.test.ts test/catalog-http.test.ts test/catalog-lookup.test.ts
pnpm --dir community-service exec tsc --noEmit
```

Results: **185 backend tests**, **235 frontend tests / 36 files**, **12 Chromium
journeys** at 320x900/1440x900, **15 hosted catalog tests**, hosted typecheck and
frontend production build passed. Ruff check/format passed for 19 changed Python
files. Browser journeys cover partial failure, per-item retry/reuse without another
download, explicit uncertain approval, cancellation/late-commit reopen, no implicit
review/export writes, signed source-author display and video-only saved membership.
They include axe/overflow/focus checks. Generated
`web/test-results/css50-downloads-{width}.png` and `css50-reconciled-{width}.png`;
mobile download and desktop reconciliation screenshots were visually inspected.
Backend tests use existing frozen signatures/receipts and disposable file-backed
SQLite, including lost response after commit, root/digest/target substitution,
malformed channel scopes, saved-filter widening and cancellation-drained workers.
The compatibility-channel routing test injects outcomes; no real-media accuracy
claim is made. Existing Vite chunk warning remains (approximately 530 kB JavaScript).

Final diagnostics: 37 changed Python/frontend/hosted files reported no errors.
Documentation checks passed 175 relative links, balanced fences and matching
completed task/master status; `git diff --check` passed. After tightening explicit
reuse to consult persisted status instead of cached success, the three controller
tests and four download browser journeys passed again; frontend build and hosted
typecheck also passed again.

Resolved development failures: direct adapter import exposed a facade cycle
(dependency moved inside target helper functions); verified summary was a dict,
not a Pydantic model; import rows use `scan_id`, not a `scan` relationship. Browser
Escape after focused-control removal required the topmost-modal fallback; the
review-write assertion was corrected to observe real item-scoped review routes.
All affected checks above were rerun successfully.

### Next exact action

CSS-51: read [receipt task](51-import-receipts.md), CSS-42/44 handoffs and
[G-REPUTATION](decisions.md#g-reputation) before implementing acknowledgment/counting.
Start with `queue_import_receipt`, the retained original receipt/operation ID,
and the CSS-50 pending/unavailable projection. Ask about any unresolved gate;
never count a transfer or send before local commit.

CSS-54: extend the existing result comparator only after compatibility and wire
feedback separately; do not replace root/source author attribution or infer a
trust score from downloads. CSS-66: reuse the actual signed temporary-import
fixtures and browser journeys for cross-process/restart/abuse checks. Exercise
release skew, status outage, native-read cancellation limits and concurrent retries
without using personal media or deploying anything incidentally.

Direct dependent tasks: [CSS-54](54-feedback-ui.md), [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md).

