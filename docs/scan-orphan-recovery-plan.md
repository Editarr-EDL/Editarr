# Scan deletion and orphan recovery plan

Status: DONE. This plan follows the wave 9 B2 fix, which made runtime
SQLite enforce foreign keys and refused deletion of scans with community
evidence. The product owner answered the two open B2 questions on 2026-10-25.

## Product decisions

1. A scan with community imports or operations can be deleted when nothing uses
   it. Saved reviews and active exports still block deletion as before. A
   community operation for the scan that is still `pending` or `submitted` also
   blocks it, with a message that names the wait. Nothing else community-related
   blocks it.
2. Installs upgraded from `main` may hold rows that point at deleted scans. Startup,
   backup, and restore must handle them gracefully instead of failing closed.
   Saved reviews that lost a scan are flagged and must be resolved by a person
   before anyone can use them.

## Background facts

- `main` never enabled `PRAGMA foreign_keys`. Its `delete_scan` checked only
  review memberships and deleted findings, queue rows, and media associations by
  hand. Every scan deletion on `main` therefore left the CASCADE-declared
  children behind: `scan_stage_coverage`, `credits_detection_runs`,
  `scan_mcf_documents`, `community_imports` (and its
  `community_import_targets`). It also left `community_operations.scan_id`
  dangling. Review filters and accepted credits can point at a scan that is not
  a membership, so reviews could lose a scan too.
- `_inspect_database` in `scanner/persistence/migrations/adoption.py` runs
  `PRAGMA foreign_key_check` before any migration, so such a database cannot
  even be upgraded today.
- `community_imports` keeps `service_url`, `contribution_id`, `revision_id`,
  `digest`, artifact, manifest, and signature bytes, `event_ids_json`, and
  `receipt_json`. `event_ids_json` maps local finding ids to artifact event ids,
  which gives an exact old to new finding mapping after a fresh import.
- A community import always creates a new scan id (`uuid4`) and autoincrement
  finding ids. An orphan `community_imports` row would collide with a new import
  of the same revision on `uq_community_import_revision`.
- Review filters copy finding content (start, end, category, label, action).
  `review_filters.source_scan_id` is NOT NULL. Reviews may have zero memberships.

## Data shape

Migration `0020_review_scan_repairs` adds `review_scan_repairs`:

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `review_id` | `saved_reviews.id`, ON DELETE CASCADE. |
| `missing_scan_id` | The scan id that no longer exists. Unique with `review_id`. |
| `detected_at` | When the migration found it. |
| `preserved_json` | The exact removed rows, grouped by table, for this review and scan. |
| `community_json` | Residual community identity, or null when none exists. |
| `community_unavailable_reason` | Set when a re-download definitively failed. |

`community_json` holds the identity copied from the orphan `community_imports`
row (service URL, contribution, revision, digest, artifact type, event id map,
receipt) or, failing that, from a `community_operations` row with the same scan
id. The orphan import's artifact, manifest, and signature bytes are preserved in
the repair record too, so no imported evidence is lost.

A review needs repair exactly when it has at least one `review_scan_repairs` row.
After migration 0020 the database is foreign-key clean, and runtime foreign keys
prevent new orphans, so the migration is the only producer of repair rows.

## Migration 0020

The migration freezes its literals and imports no scanner code. In one pass:

1. Find missing scan ids: ids referenced from any child foreign key to `scans`,
   plus scans of findings referenced through `scan_findings`, that have no
   `scans` row. A finding whose scan is missing counts as missing.
2. For each saved review with rows that point at a missing scan or finding
   (`review_scan_memberships`, `review_filters`, `review_accepted_credits`,
   `review_policy_evidence`, and `media_item_export_scans` of that review's
   exports), move those rows into `preserved_json` of one repair row per
   (review, missing scan), then delete them from their tables.
3. Copy residual community identity into the matching repair rows, then delete
   the orphan `community_imports` rows (their targets cascade).
4. Set `community_operations.scan_id` to NULL where the scan is missing, and
   record those operation ids in the matching repair rows.
5. Delete the remaining CASCADE-declared children of missing scans and any
   orphan `scan_findings`, `scan_queue`, and `scan_media_item_associations`
   rows that no review referenced. These rows would have been deleted with their
   scan if foreign keys had been enforced.

The existing safety snapshot runs before the migration. After it, the strict
foreign-key check must pass.

Before head, foreign-key violations are tolerated only where the child row
points at `scans` or `scan_findings`, or where `community_import_targets` points
at an orphan `community_imports` row. Every other violation still fails closed.
This narrow tolerance applies at every pre-migration check site: adoption
inspection, safety snapshot create and validate, recovery export, and restore
staging before its upgrade. Checks at head stay strict.

## Review behavior

- Review lists return every review. `SavedReviewSummary.repair` is null for a
  healthy review, otherwise a `ReviewRepairSummary`.
- Every other review route (detail, save, retime, rename, duplicate, previews,
  EDL and MCF export, MCF preflight, credits accept, filter apply, community
  sharing) returns `409 review_needs_repair` with the `repair` summary. Delete and
  the repair routes below are the only allowed actions. Existing exported files
  stay where they are until the review is resolved.
- Recover (`POST /api/reviews/{review_id}/repair/recover`, review save
  permission) removes every reference to the missing scans. Filters copied from
  a missing scan stay in the review as manual cues: they move to the review's
  manual scan, created when needed, with `source_finding_id` set to null.
  Memberships, accepted credits, and policy evidence of the missing scans are
  dropped. Policy evidence is rebuilt. The repair rows are deleted.
- Re-download (`POST /api/reviews/{review_id}/repair/community`, review save
  permission plus `community.download` and the existing download gates) uses the
  residual identity and the existing verified download and import path. It
  creates a new scan and maps old finding ids to new ones through the preserved
  and new event id maps, falling back to exact content matching only when no map
  exists. It restores the preserved rows on the new scan and finding ids, rebinds
  the `import-<receiptId>` operation to the new scan, and deletes the repair rows,
  all in one transaction. Any unmatched reference fails the whole attempt. A
  definitive failure (not found, revoked, digest or mapping mismatch) is stored in
  `community_unavailable_reason`. A transient failure (offline, identity not
  connected, downloads disabled) returns the reason without storing it.
- Delete uses the existing review delete route and its existing rules.

`ReviewRepairSummary`:

```ts
interface ReviewRepairSummary {
  message: string;                 // "This review uses a scan that no longer exists."
  missing_scan_ids: string[];
  detected_at: string;
  recover: { kept_cues: number; removed_memberships: number; removed_accepted_credits: number };
  community: { available: boolean; unavailable_reason: string | null } | null;
}
```

Error bodies for `409 review_needs_repair` and `409 community_scan_unavailable`
follow the existing error envelope and carry `repair`.

## Scan deletion

- `ScanReferences.community_retained` is replaced by `community_in_progress`,
  true when a `pending` or `submitted` community operation references the scan.
- `delete_scan` refuses only for reviews, active exports, or an in-progress
  community operation. Otherwise, under the write lock, it deletes the scan's
  community imports (targets cascade), sets `scan_id` to NULL on its finished
  community operations, and deletes the scan as today.

## Accepted refusals

The product owner accepted two refusals on 2026-10-25 instead of adding repair
rules. No installation upgraded from a release can reach either one.

- Policy evidence that no surviving reference ties to a missing scan fails the upgrade closed.
  Migration 0020 reads a gone finding's ID from a review filter or accepted-credit row
  that still names the missing scan. It keeps that finding's policy evidence in the
  scan's `review_scan_repairs` record, and the upgrade succeeds. When the finding and
  scan are both gone and no such row names the finding, the strict foreign-key check
  at head reports `child=review_policy_evidence, parent=scan_findings`, the upgrade
  journal records the failure, and the safety snapshot keeps the original database.
  `review_policy_evidence` first appears in schema 0019, which no release ships, and
  runtime foreign keys prevent this orphan from then on. `main` has no such table.
- Deleting the legacy file of an export for a named media version whose scan links
  are gone fails with `500 export_delete_failed` ("Active export provenance is
  incomplete"). Editarr does not guess the version's file from the item path. The
  export record and its file stay. `main` already refused to delete a scan with
  active export links, so an upgrade from `main` cannot leave these links missing.

## Web behavior

- A flagged review row shows a red exclamation icon and the error text on a new
  line below the row's existing content, plus a Resolve button. The row does not
  open the review.
- Resolve opens a modal dialog with three radio options: delete the review,
  recover by removing the missing scan's references (showing the kept and
  removed counts), and re-download from the community when `community` is not
  null. Options the user lacks permission for are disabled with a reason.
- When re-download fails, the dialog returns to the option list with that
  option disabled and this text under it: "The missing scan could not be
  retrieved from the community. Choose one of the remaining options." A server
  reason is shown after it when present. A stored `unavailable_reason` disables
  the option when the dialog opens.
- If a flagged review is ever loaded in the review workspace, the whole editor
  is greyed out and read-only (a disabled fieldset) under an alert that offers
  Resolve.
- The scan delete dialog shows "A community operation for this scan is still in
  progress" instead of the old retained-evidence text.

## Work split

- Backend (`samwise-mu-b2r`): migration, tolerance, repair service and routes,
  scan deletion, contracts, API docs, route inventory, OpenAPI, tests.
- Web (`samwise-mu-b2r-ui`): list row, dialog, read-only workspace, scan delete
  text, API client and types, Vitest.
- After both merge, one delegate extends the verify-editarr upgrade journey with
  an orphan-seeded `main`-shaped database and proves the flows in the real UI.
