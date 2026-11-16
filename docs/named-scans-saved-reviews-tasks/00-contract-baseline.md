# NSR-00 — Freeze interfaces and baseline

Status: **DONE**

Dependencies: none. Deliverable: frozen schema/API/UI contracts and baseline evidence;
no product implementation.

Read the [task-pack README](README.md),
[parent plan](../named-scans-saved-reviews-edl-import-plan.md), and repository `AGENTS.md`
before starting.

## Why this task exists

Saved reviews replace the current browser-only scan combination and scan-owned editable
selection. Database, repository, API, EDL, and frontend agents need identical field names,
revision rules, and transition semantics before they work independently. This task turns
the approved product decisions into exact implementation interfaces and records a clean
baseline.

## Scope

1. Inspect the current scan, finding, Manual scan, source-resolution, EDL, and frontend
   review paths listed in the README code map. Record pre-existing worktree changes.
2. Run the baseline verification in this task using test-owned temporary data. Do not
   delete or recreate any database; NSR-01 owns the approved reset.
3. Add a `Contract record` section to this file fixing the exact names/types for:
   - `Scan.name` and scan origin values (`generated`, `manual`, `imported` unless an
     existing type makes one spelling demonstrably safer);
   - saved-review, membership, review-filter, EDL-review-provenance, import request, import
     warning, and scan-reference DTOs;
   - resource revision versus content revision and the operations that increment each;
   - the normalized media-version key (`NULL` externally, one canonical non-null storage
     representation internally);
   - list/create/get/save/rename/duplicate/delete/preview/export/import route methods and
     payloads;
   - safe error codes/statuses for stale revision, invalid membership, reference-blocked
     deletion, invalid import, no supported actions, and import size limits;
   - frontend draft identity and removed-snapshot cache rules.
4. Use the route direction in the README: source-aware review collection operations,
   review-ID item operations, review-ID export, and separate upload/path import endpoints.
   Reuse existing source-resolution helpers; do not accept a browser-provided catalog path.
5. Record transition strategy: old scan-level selection/edit/export endpoints may remain
   only until the new frontend is complete; identify NSR-12 as their removal owner.
6. Update only documentation/test baselines. Do not add database tables, production DTOs,
   routes, or UI.

## Required contract decisions

The record must make these implementation details exact so later tasks do not rename them:

- Review summary includes stable ID, name, target/version, both revisions, timestamps,
  exported marker, and `outdated`.
- Review detail includes role-aware scan references and review-local filters. A filter's
  source scan ID is required; source finding ID may be nullable for staged manual input
  until save assigns it.
- Review filter preview routes take review ID/filter ID, resolve media through the source
  scan, and render the review snapshot's current range. They never read source finding
  timing as the preview authority.
- Name-only rename increments resource revision and `updated_at`, but not content revision.
  Membership, selection, filter content/timing/action, and new manual filters increment
  both; export changes neither.
- Duplicate creates a new review with copied content at initial revisions and requires a
  supplied name. Review names are never identity.
- Import target identifies `jellyfin`, `radarr`, `sonarr`, or `manual`, item ID, and version
  ID as needed. Catalog metadata/path is resolved by the server.
- Browser upload uses multipart form data; scanner-path import uses JSON. Both share the
  same options/result DTO and parser/service.
- Set explicit bounded import limits. Default contract unless the repository exposes a
  stricter established limit: UTF-8 only, 1 MiB maximum content, 10,000 action rows, and
  4,096 Unicode code points per line.
- Generic uncommented metadata is `other: Imported mute` or
  `other: Imported skip`.

If existing framework behavior makes one spelling or envelope materially better, record
the exact deviation and why. Do not change approved product behavior.

## Verification

Run and record:

- `uv run ruff check scanner tests`
- `uv run pytest -q tests/test_edl.py tests/test_editarr_integration.py tests/test_auth_schema.py`
- in `web/`, `pnpm test` and `pnpm build`
- `git diff --check`

If the broad focused pytest selection is too slow, it still must run once for this
baseline. Do not use the checkout database or external services.

## Acceptance criteria

- [x] Pre-existing changes, host/tool versions, commands, counts, and warnings are recorded.
- [x] Every shared DTO, route, revision rule, error, limit, and transition owner is exact.
- [x] Source-aware empty-review creation and both import transports have a trust-safe shape.
- [x] Later backend and frontend tasks can implement without inventing a second contract.
- [x] No application behavior, schema, database, media, sidecar, or service changed.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Compare the Contract record with every decision in the parent plan and README. Check that
the live baseline OpenAPI has been inspected, that no production file changed, and that
the recorded baseline commands reproduce. Reject the task if a product decision is
missing or if two endpoints could become competing authorities.

## Handoff requirements

Include the complete exact contract, baseline evidence, files inspected, pre-existing
changes, and any routine naming deviation. Name NSR-01 and NSR-05 as the next eligible
tasks. If a new product decision is unavoidable, mark `BLOCKED` and ask only that question.

## Contract record

This is the implementation contract for NSR-01–12. These are planned interfaces, not a
claim that the baseline already implements them. JSON uses snake_case, UUIDs are strings,
timestamps are timezone-aware UTC ISO 8601 strings, and revisions are non-negative
integers. New write DTOs forbid unknown fields. Existing `Scan.id` is the UUID;
`Scan.scan_id` remains the display number. Never exchange these two identities.

### Names, origin, identity, and storage

- Add `Scan.name: str | None = None` and `Scan.origin: ScanOrigin = "generated"`;
  `ScanOrigin` is exactly `generated | manual | imported`. Queue responses inherit both.
  `manual` means the reusable human-authored Manual scan, not an AI scan of Other Files.
  Keep the existing `manual` boolean during transition, derived from `origin == "manual"`;
  imported scans use their explicit origin, not model-name inference.
- Name validation checks the original string for Unicode `Cc` controls and `Cs`
  surrogates before trimming; rejects those characters; trims outer Unicode whitespace;
  preserves case and Unicode spelling without case-folding or normalization; then checks
  at most 100 Unicode code points. Blank/omitted/null scan names become null. Review names
  must contain at least one code point after trimming. No uniqueness constraint applies.
  The shared UI label is `name · Scan #N`, or `Scan #N` without a name; type/origin is a
  separate badge. `ScanNameUpdate` has one required nullable field, `name`.
- NSR-02 user clarification: scan rename changes only the display name and leaves
  `Scan.updated_at` unchanged. This preserves completed scan duration, which the existing
  implementation derives from `updated_at` and worker start time. Review rename rules
  below remain separate and unchanged.
- Add `Scan.import_source_name: str | None = None` and
  `Scan.imported_at: datetime | None = None`. Only imported scans populate these. The name
  is a sanitized basename (strip both slash styles, reject controls, use `import.edl` if
  empty), never the upload/path input. Imported `media_source_name` is this basename too;
  `media_source_id` continues to identify the target physical version. `created_at` and
  `imported_at` are the import timestamp, `scan_duration_seconds` is null, and origin/type
  identify audio-only, video-only, or combined completed, non-queued scans.
- Public version identity remains `media_source_id: str | None`; do not add a parallel
  public `version_id`. The canonical non-null storage representation is `value or ""`,
  matching `MediaItemEdlRow.media_source_id`. Read `""` as null. Resolve catalog IDs on
  the server before normalization; do not trim or invent upstream IDs. Reviews and Manual
  singleton lookup use this same key. Ownership is local `media_item_id` plus that key;
  membership also requires verified physical-path equality, including associated scans.
- `SavedReviewRow` / `saved_reviews`: `id`, `media_item_id`, non-null `media_source_id`,
  `name`, `revision` default 0, `content_revision` default 0, `created_at`, `updated_at`.
  Resolve source/target metadata through the media row and adapter; do not expose stored
  paths as review DTO fields. Names are nonblank and revisions non-negative at the DB
  boundary too.
- `ReviewScanMembershipRow` / `review_scan_memberships`: `review_id`, `scan_id`, `role`,
  `position` (non-negative). Unique `(review_id, scan_id)` and `(review_id, position)`;
  role check is `audio | video | both`. Array order defines contiguous positions.
- `ReviewFilterRow` / `review_filters`: `review_id`, UUID `id`, `source_scan_id`, nullable
  integer `source_finding_id`, `position`, and every `ReviewFilter` content field below.
  Use `(review_id, id)` identity and unique `(review_id, source_finding_id)` when non-null;
  do not deduplicate distinct source findings with identical content. A finding FK, if
  present, must identify evidence belonging to `source_scan_id`. Only review-owned rows
  cascade on review deletion; no review-to-scan cascade is permitted.
- Add nullable `review_id` and `exported_content_revision` to `MediaItemEdlRow`. Both are
  null together or both populated, with non-negative exported revision. Review deletion
  explicitly clears both before deleting review rows; it retains the EDL row/scan links.
  NSR-01 alone owns the approved checkout database reset. No migration is introduced.

### Exact shared DTOs

`ReviewSource = jellyfin | radarr | sonarr | manual`,
`ReviewScanRole = audio | video | both`, and the existing
`ContentCategory = nudity | violence | profanity | other`,
`EditAction = mute | skip` are reused.

| DTO | Fields |
| --- | --- |
| `ReviewTarget` (response) | `source: ReviewSource`, `item_id: str`, `media_source_id: str | None` |
| `ReviewTargetRequest` (write union) | Catalog: `source: jellyfin | radarr | sonarr`, required `item_id: str`, `media_source_id: str | None = None`. Manual: `source: manual`, `item_id: str | None = None`, `video_path: str | None = None`, `media_source_id: None = None`; exactly one of item ID or video path is supplied. |
| `SavedReviewSummary` | `id: str`, `media_item_id: str`, `target: ReviewTarget`, `name: str`, `revision: int`, `content_revision: int`, `created_at: datetime`, `updated_at: datetime`, `exported: bool`, `exported_content_revision: int | None`, `outdated: bool` |
| `ReviewScanMembership` | `scan_id: str`, `role: ReviewScanRole` (ordered array; review ID belongs to the enclosing aggregate) |
| `ReviewScanReference` | `scan: Scan`, `role: ReviewScanRole` (same order as memberships, current scan display metadata) |
| `ReviewFilter` | `id: str`, `source_scan_id: str`, `source_finding_id: int | None`, `source: audio | visual`, `category: ContentCategory`, `label: str | None`, `details: str | None`, `group_key: str | None`, `start_seconds: float`, `end_seconds: float | None`, `confidence: float`, `edit_action: EditAction`, `selected: bool` |
| `SavedReview` | All summary fields plus `memberships: list[ReviewScanMembership]`, `scans: list[ReviewScanReference]`, `filters: list[ReviewFilter]` |
| `ReviewFilterInput` | Same fields as `ReviewFilter`; `id` is a review-local UUID supplied by the draft for a new snapshot and retained on later saves. Existing IDs cannot change `source`, `source_scan_id`, or `source_finding_id`. |
| `NewManualReviewFilter` | UUID `id` plus the content fields of `ReviewFilter` (from `source` through `selected`); no source IDs. `end_seconds` is required, `confidence` defaults to 1, `selected` defaults to true. The save assigns a real Manual source scan/finding before returning a `ReviewFilter`. |
| `ReviewCreate` | Required `name: str`; `memberships: list[ReviewScanMembership] = []`; `filters: list[ReviewFilterInput] | None = None`; `new_manual_filters: list[NewManualReviewFilter] = []` |
| `ReviewSave` | Required `revision: int`, `name: str`, `memberships: list[ReviewScanMembership]`, `filters: list[ReviewFilterInput]`, `new_manual_filters: list[NewManualReviewFilter] = []` |
| `ReviewRename`, `ReviewDuplicate` | Required `revision: int`, `name: str` |
| `ReviewExportRequest` | Required `revision: int` (expected resource revision) |
| `ReviewReference` | `id: str`, `name: str`, `updated_at: datetime` |
| `ScanReferences` | `scan_id: str`, `active_edl: bool`, `reviews: list[ReviewReference]` (all references, ordered `updated_at` descending then ID ascending) |
| `EdlScanReference` | Existing `id: str`, display `scan_id: int`, `role: ReviewScanRole`, plus `name: str | None`, `origin: ScanOrigin` |
| `EdlReviewProvenance` | `review_id: str | None`, `exported_content_revision: int | None` |
| `EdlStatus` | Existing `has_exported_edl`, `exported_at`, `exported_scans`, plus both `EdlReviewProvenance` fields (flattened, default null), and `exported_review: SavedReviewSummary | None = None` |
| `ReviewEdlExport` | `review_id: str`, `exported_content_revision: int`, `exported_at: datetime`, `exported_scans: list[EdlScanReference]`, `exported_review: SavedReviewSummary` |
| `EdlImportOptions` | `target: ReviewTargetRequest`, required `name: str` (the review name and initial imported scan name) |
| `EdlPathImportRequest` | All `EdlImportOptions` fields plus required `edl_path: str` |
| `EdlImportWarning` | `code: Literal["unsupported_actions_ignored"]`, `message: str`, `ignored_action_count: int` greater than zero |
| `EdlImportResult` | `review: SavedReview`, `scan: Scan`, `warning: EdlImportWarning | None` |

`ReviewFilter.source` retains existing `audio` / `visual` spelling; `video` is the scan
membership role, not a new finding source value. Word and description are UI concepts:
audio word maps to `label`, description maps to `details`, and free-form video category
uses `label` with the known category enum or `other`, matching current storage. Import
retains a recognized comment's label and maps its category to the enum (unknown categories
use `other`). There is no second `word` or `description` persistence field.

Validate finite ranges, non-negative starts, end after start and no later than the
trusted media duration when known; confidence is finite in `[0, 1]`. Existing source
evidence may have a null end, retaining the current one-second export/preview fallback;
edited ranges and new Manual/import rows require a finite explicit end. Source and
source IDs are provenance; category/label/details/group/range/confidence/action/selection
are copied content. A filter's role is determined by source, even when its action changes.

`exported` is true exactly when the target's active EDL references this review;
`exported_content_revision` is null otherwise. `outdated` is `exported` and exported
content revision differs from current content revision. Propagate the flattened review
provenance fields and `exported_review` wherever existing item/version DTOs already expose
EDL status. The embedded summary supplies the current review name and computed `outdated`
without an additional fetch. Marked-review deletion leaves `has_exported_edl`, timestamp,
and scans intact but clears both provenance fields and `exported_review`; there is no
remaining review marker or outdated review. Export returns the updated summary with
`exported=true` and `outdated=false`.

### Full-save, snapshot, and revision protocol

All operations validate memberships against complete, reviewable scans owned by or
explicitly associated with the target, compatible with the requested roles, and resolving
to the same physical version. Audio includes `audio` findings; video includes `visual`;
both includes each. Return every referencing review when blocking scan deletion, even
when active EDL provenance also blocks it.

- `ReviewCreate.filters == null` means snapshot all applicable source findings for the
  supplied memberships, assigning fresh review-local UUIDs. An explicit array is the
  complete draft snapshot, permitting edits/deletions before first save. Each supplied
  source finding is revalidated against its scan/role/target before copying edited fields.
- Save is a full replacement of membership and private filter content under one expected
  resource revision. Omitted saved filters are deleted only from this review; missing
  membership deletes that membership's snapshots. Newly included scans use explicit
  source snapshots fetched for the draft. No hidden save resnapshots unchanged members.
- A new snapshot requires a non-null verified source finding; null source-finding IDs in
  response/storage are reserved for staged/internal creation until assignment. The public
  `new_manual_filters` list handles the case where even the Manual scan does not yet
  exist, without inventing a fake scan ID. The transaction creates/reuses the version's
  Manual scan, appends evidence, assigns source IDs, and includes its required role in
  membership (union with any existing Manual role) before returning the saved aggregate.
  It does not implicitly add all older Manual findings. Re-saving the returned aggregate
  cannot create the same staged finding twice. Stale saves create no evidence.
- Create/import/duplicate begin at `revision = 0`, `content_revision = 0`. Duplicate
  checks the source revision, requires the supplied name, copies current snapshots with
  fresh review-local IDs, and begins without export provenance.
- Compare normalized aggregate content before updating. An actual name-only change
  increments `revision` and changes `updated_at`; content revision is unchanged. An
  actual membership/order or filter/order/content/selection change, including new Manual
  filters, increments both revisions once and changes `updated_at`. A combined name and
  content change still increments each only once. A no-op after normalization returns the
  unchanged aggregate and timestamps, but still rejects a stale expected revision.
- Export changes neither revision nor review `updated_at`. Revision comparison, media and
  source validation, sidecar replacement, and provenance update form the export operation;
  any failure preserves previous file/provenance, including DB commit failure. NSR-06
  must strengthen the baseline file-then-commit path to meet that failure guarantee.
  Existing behavior permits a header-only export when scans exist but no filters are
  selected; a review with no scan membership cannot export (`invalid_membership`).
- Every rename/save/duplicate/delete/export checks expected resource `revision`; scan
  rename has no review revision and never changes any review content/marker.

Repository entry points are `list_saved_reviews`, `get_saved_review`,
`create_saved_review`, `save_saved_review`, `rename_saved_review`,
`duplicate_saved_review`, `delete_saved_review`, `get_scan_references`,
`rename_scan`, `export_saved_review_edl`, `render_review_filter_mute_preview`, and
`render_review_filter_video_preview`. NSR-03 records concrete Python signatures around
these DTOs and a server-resolved target context; HTTP models must not become trusted
filesystem context. The create/save/manual/import transaction must own media upsert and
all rows without helper-level commits.

### Routes and trusted source resolution

All paths are application-relative and inherit existing setup/authentication protection;
all unsafe methods inherit same-origin and forms-session CSRF checks before side effects.
Success defaults to 200 unless stated. Collections return plain arrays; item writes return
the complete authoritative object. No catalog collection/import accepts path or duration
metadata from the browser.

| Method and route | Request | Response |
| --- | --- | --- |
| `PATCH /api/scans/{scan_id}` | `ScanNameUpdate` | `Scan` |
| `GET /api/scans/{scan_id}/references` | None | `ScanReferences`; missing scan 404 |
| `GET /api/library-items/{item_id}/reviews` | Query `media_source_id`, `sort=name|updated_at` (default `updated_at`), `direction=asc|desc` (default `desc`) | `list[SavedReviewSummary]` |
| `POST /api/library-items/{item_id}/reviews` | `ReviewCreate` plus `media_source_id: str | None = None` | 201 `SavedReview` |
| `GET /api/sources/{service}/items/{item_id}/reviews` | Same query; `service=radarr|sonarr`, Sonarr item is an episode | `list[SavedReviewSummary]` |
| `POST /api/sources/{service}/items/{item_id}/reviews` | Same create body | 201 `SavedReview` |
| `GET /api/manual-files/reviews` | Exactly one query `item_id` or `video_path`; same sort/direction | `list[SavedReviewSummary]` |
| `POST /api/manual-files/reviews` | `ReviewCreate` plus exactly one `item_id: str | None` or `video_path: str | None` | 201 `SavedReview` |
| `GET /api/reviews/{review_id}` | None | `SavedReview` |
| `PUT /api/reviews/{review_id}` | `ReviewSave` | `SavedReview` |
| `PATCH /api/reviews/{review_id}` | `ReviewRename` | `SavedReview` |
| `POST /api/reviews/{review_id}/duplicate` | `ReviewDuplicate` | 201 `SavedReview` |
| `DELETE /api/reviews/{review_id}` | Required query `revision` | 204, no body |
| `GET /api/reviews/{review_id}/filters/{filter_id}/mute-preview` | None | MP3 `audio/mpeg`, `Cache-Control: no-store` |
| `GET /api/reviews/{review_id}/filters/{filter_id}/video-preview` | None | MP4 `video/mp4`, `Cache-Control: no-store` |
| `POST /api/reviews/{review_id}/edl` | `ReviewExportRequest` | `ReviewEdlExport` |
| `POST /api/edl-imports/upload` | Multipart `file` (bytes plus untrusted filename), `options` (JSON string conforming to `EdlImportOptions`) | 201 `EdlImportResult` |
| `POST /api/edl-imports/path` | JSON `EdlPathImportRequest` | 201 `EdlImportResult` |

Source-specific create DTO names are `LibraryReviewCreate`, `ArrReviewCreate`, and
`ManualFileReviewCreate`. GET performs no upsert and returns `[]` when a valid target has
no local row. First save resolves/upserts inside the create transaction, even with no
scans. Jellyfin resolves `JellyfinClient.item` plus the selected version; Arr resolves
`ArrClient.item` or `.episode` and the current file ID; all use `resolve_local_path`,
readability, and current duration validation. Supplied stale/unknown version IDs fail;
omitted IDs use the server's unambiguous current/default version. Multiple Jellyfin
versions require a selected ID. Other Files uses its established `editarr-manual` identity
and canonical absolute readable `video_path`; a supplied item ID must resolve an existing
manual item and cannot choose a catalog row. Request paths never become scan display
names. Existing manual item IDs may contain their established path identity; do not add
new raw host-path fields or leak the imported EDL path.

Preview resolves a filter only within the identified saved review, validates membership
and the source scan's media, then renders the saved review filter's range/action. Wrong
action is rejected; unsaved drafts must be explicitly saved before using these routes.
No preview reads source finding timing as the authority or mutates either row.

### Safe errors and import codec

Use FastAPI's existing `detail` envelope with a structured value for new domain errors:
`{"detail": {"code": str, "message": str, "line_number": int | null,
"references": ScanReferences | null}}`. Name this inner DTO `ReviewErrorDetail` and the
enclosing DTO `ReviewError`. Include null optional fields consistently. Standard request
shape/name validation may retain FastAPI's 422 validation list, but must redact input
values containing upload text, source paths, or secrets. These statuses/codes are exact:

| HTTP | `code` | Meaning |
| --- | --- | --- |
| 404 | `review_not_found`, `review_filter_not_found`, `scan_not_found` | Safe missing-resource errors, including a filter in a different review |
| 409 | `stale_review_revision` | Expected revision differs; nothing changes |
| 409 | `invalid_membership` | Missing, incomplete, incompatible-role, unassociated, or cross-version scan/filter provenance; also no membership for export |
| 409 | `scan_referenced` | Scan cannot be deleted; `references` contains active EDL flag and every referencing review, and message includes names plus ISO modified dates |
| 409 | `invalid_review_filter` | Invalid edited range/content/action or wrong preview action |
| 409 | `media_unavailable` | Selected version/media cannot be resolved or read safely |
| 409 | `edl_export_failed` | Atomic export failed; safe generic message, diagnostic details only in protected logs |
| 422 | `invalid_edl_import` | Invalid UTF-8, syntax, nonfinite/range/duration violation; 1-based line where applicable |
| 422 | `no_supported_edl_actions` | Message: `No supported EDL actions were found`; nothing created |
| 413 | `edl_import_too_large` | Content, line, or action-row limit exceeded |
| 400 | `edl_import_unreadable` | Path is not absolute or not a readable regular file; message never echoes it |

Existing upstream/source-configuration errors retain their 409/502 statuses with safe
messages. Import resolves the target first, bounds reads in both transports, validates all
content before the single transaction, and never changes active EDL state. Unsupported
integer actions use exactly one warning: `Unsupported EDL actions were ignored and not
imported`, with the count of ignored rows, not a warning per row.

- Limits are UTF-8 only, at most 1,048,576 content bytes, 10,000 non-comment action rows
  (including ignored actions), and 4,096 Unicode code points per line excluding CR/LF.
  Both transports enforce them while reading (size-limit probe may read one extra byte).
- Rows have exactly three whitespace-separated fields. Seconds are plain decimal numbers
  (optional sign, digits with optional fractional part, or a leading decimal point with
  digits); no exponents, NaN/infinity, commas, frames, or clock syntax. Precision is
  bounded by line length and existing finite binary64 storage; conversion must still
  satisfy `0 <= start < end` and trusted duration. Export keeps three decimal places.
  Action is a signed integer. Validate row syntax and finite seconds before classifying
  actions; supported rows additionally require valid increasing, duration-bounded ranges.
- Preserve supported input row order, overlaps, and duplicate rows as separate evidence;
  playback normalization happens on export. A valid but unsupported action clears pending
  comments and increments the aggregate count. It creates no finding.
- The parser accepts LF/CRLF and an optional leading UTF-8 BOM. Trim lines for blank/comment
  classification. An Editarr metadata comment starts with `##` and splits on its first
  colon; trim each side, require both nonempty, map category as above. A contiguous block
  of comment lines may contain generic comments/header, which contribute no metadata.
  A blank or action row ends the block. Only recognized comments in the immediately
  preceding block apply to the next supported row; never carry metadata past another row.
- One supported row becomes one filter per recognized comment, or one fallback filter
  with `other: Imported mute` / `other: Imported skip`. Distinct identical comments remain
  distinct findings. Action 1 means source audio/mute; action 3 means visual/skip.
- Pure codec names are `parse_edl_content(content: bytes, *, duration_seconds: float |
  None = None) -> ParsedEdl`, `ParsedEdlFilter` (content fields matching an imported
  `ReviewFilter` plus `line_number`), and `ParsedEdl` (`filters`, `ignored_action_count`).
  `EdlParseError` carries `code`, safe `message`, nullable `line_number`. The parser raises
  the no-supported error rather than returning an empty successful result. The codec
  imports no DB, filesystem, or FastAPI code.
- Keep `export_edl_content` and `normalized_edl_ranges` call-compatible with source
  findings and review filters. Contributor order is original filter array order, preserved
  through merging/subtraction; each distinct contributor emits once per surviving output
  piece. Sanitize each comment field by replacing controls/newlines with spaces, collapsing
  whitespace and trimming; replace category colons with spaces so the first colon remains
  the delimiter. Missing/empty category and label use the fallback above. Preserve useful
  Unicode. Emit header, contributor comments, and sorted three-decimal action rows.

### Browser identity and transition ownership

The browser draft is keyed by source/item/version plus saved review UUID or a fresh
unsaved-session UUID. Filter UI identity is `(draft identity, review-local filter UUID)`;
source finding integers never identify editable UI state. New source snapshots and staged
Manual filters receive browser UUIDs which are retained when saved. On a successful save,
replace local state with the returned `SavedReview`, including newly assigned provenance.

Maintain a session-only removed-snapshot cache keyed by scan UUID and source role, holding
the exact review filter IDs/content/selection/order. Role removal and re-add restores that
role's cached snapshot in the same unsaved session. Cache survives a failed save; clear it
after any successful save, discard, or approved review/version/file/route switch. After a
saved removal, a later add reads current source findings into fresh review-local IDs.
Discarding staged Manual filters makes no request and creates no source finding.

Open the greatest `updated_at` review, tie-breaking ID ascending, independently of the
chosen list presentation sort. List name sort uses case-folded name with ID ascending as
tie-breaker; date sort uses `updated_at` with ID ascending as tie-breaker. Apply requested
direction only to the primary key. Use explicit dirty-state confirmation before replacing
a draft, and retain it on failed loads/saves. Export always uses the last saved aggregate
and requires resolving dirty changes through the existing explicit-save interaction.

NSR-08 introduces DTOs and draft helpers; NSR-10/11 move all workflows to these helpers.
NSR-12 owns removal of `PUT /api/scans/{scan_id}/findings/selection`, scan finding PUT/
DELETE, immediate Manual-finding POST routes for all sources, scan-ID EDL POST, their web
helpers, and obsolete scan-owned editable-state tests/revisions. Read-only source finding
GET remains for fresh snapshots; source preview routes may remain read-only evidence
tools, but review UI uses review previews. Transitional routes never write review rows;
no new workflow uses them. At NSR-12 there is one writable authority for review edits.

## Baseline evidence

Worktree was clean before NSR-00 (`git status --short` produced no entries). Inspected
`scanner/models.py`, `scanner/db.py`, `scanner/repository.py`, `scanner/api.py`,
`scanner/edl.py`, `scanner/arr.py`, `scanner/jellyfin.py`, `web/src/api.ts`,
`web/src/App.tsx`, `tests/test_auth_schema.py`, the
task-pack README, parent plan, root `AGENTS.md`, `docs/api-contract.md`, and the NSR-01,
03, 04, and 05 scopes to check downstream interfaces. No nested AGENTS.md was found in
scanner/web/tests. Source adapters are reached through the existing API client calls;
no live catalog or installed service was contacted.

Terra baseline, September 13, 2026:

| Check | Exact invocation/context | Result |
| --- | --- | --- |
| Backend lint | `UV_CACHE_DIR=/private/tmp/nsr00-uv-cache uv run ruff check scanner tests` | All checks passed |
| Backend focused baseline | With `EDITARR_DATA_DIR` set to a directory created by `mktemp -d /private/tmp/nsr00-test-data.XXXXXX` and `UV_CACHE_DIR=/private/tmp/nsr00-uv-cache`: `uv run pytest -q tests/test_edl.py tests/test_editarr_integration.py tests/test_auth_schema.py` | 64 passed, 5 warnings, 1.99 s |
| Frontend unit baseline | In `web/`: `pnpm test` | 9 files, 126 tests passed, 7.33 s |
| Frontend production build | In `web/`: `pnpm build` | TypeScript and Vite passed; 36 modules, 429 ms; output HTML 0.98 kB, CSS 39.43 kB, JS 373.94 kB |
| Whitespace | `git diff --check` | Passed, no output |

Host/tools: macOS 27.0 (26A428), arm64; uv 0.12.5; Python 3.11.16;
Ruff 0.16.3; pytest 9.1.1; FastAPI 0.141.1; Pydantic 2.13.4;
SQLAlchemy 2.0.52; Node v26.7.0; pnpm 11.22.0; TypeScript 5.9.3;
Vitest 4.1.10; Vite 6.4.3; Git 2.54.0.

The five backend warnings are one `StarletteDeprecationWarning` from the deprecated
httpx/TestClient integration, and four FastAPI `on_event` deprecation warnings (startup,
shutdown, and two internal FastAPI calls). No frontend/build warning was reported.

The first pytest invocation without a temporary data override failed during collection:
importing settings attempted to acquire the default installed deployment configuration
lock and received `PermissionError`, before any test ran. No DB was opened or user
service started. The command was then rerun successfully with the isolated temporary
data/cache directories above. All subsequent verification must set `EDITARR_DATA_DIR`
before importing `scanner.api`; isolation is necessary even without calling startup.

The real `api.app.openapi()` was inspected in a fresh Python process with temporary
`EDITARR_DATA_DIR`, using `from scanner import api; api.app.openapi()`, without
TestClient/lifespan/startup. Result: title `Editarr`, version `0.2.0`, 61 paths,
74 operations, 66 schemas. One startup and one shutdown handler were registered but not
run; no scanner database or non-main thread was created. Import created only a contained
deployment lock in the temporary directory; generating OpenAPI added no files.

OpenAPI confirms no review or import resource. `Scan` lacks name/origin; `ScanFinding`
uses global integer IDs; `ScanFindings` is `{revision, findings}`. Existing selection and
content writes are scan-scoped PUT/DELETE operations, and previews use scan/finding IDs.
`POST /api/scans/{scan_id}/edl` accepts optional `{audio_scan_ids, video_scan_ids}` and
returns `{id, exported_at, exported_scans}`; its references contain UUID `id`, integer
`scan_id`, and role. Existing `EdlStatus` has no review provenance. These observations
confirm that the new routes/DTOs add distinct resources and that NSR-12 must retire the
old editable authority. No production file, schema, database, media, sidecar, or service
was changed; frontend build output is ignored generated output.

## Handoff

Status: DONE
Last completed substep: Terra independently reviewed the complete contract, verified the
  embedded EDL review-summary correction, and passed the final contract/diff check.
Files changed: docs/named-scans-saved-reviews-tasks/00-contract-baseline.md and README.md.
Schema/API/contract changes: Exact planned DTOs, storage names, routes, revision/snapshot
  semantics, import limits/codec, trust boundaries, errors, and transition ownership above.
  No production schema/API changed.
Verification commands and results: Backend Ruff passed; focused pytest 64 passed/5
  deprecation warnings; web Vitest 9 files/126 tests passed; pnpm build passed;
  final diff check passed. Exact commands, environment and host versions are above.
  No task-required verification was skipped. Full backend/browser suites are NSR-12 scope.
Manual or artifact evidence: Actual app.openapi() reports 61 paths, 74 operations,
  66 schemas; isolated import/startup side effects inspected; no DB/lifespan started.
Known failures or risks: Initial unisolated pytest import was denied access to default
  deployment lock before collection; temporary EDITARR_DATA_DIR rerun passed. Always
  supply that override. Existing filesystem-then-DB export requires NSR-06 failure recovery.
Acceptance criteria still unmet: None.
Next exact action: NSR-01 agent reads this contract/handoff and verifies the recorded
  checks using temporary EDITARR_DATA_DIR before implementing persistence foundations.
Next eligible task: NSR-01 and NSR-05; user-requested sequence proceeds to NSR-01.
Decisions or deviations from the parent plan: No product deviations. Preserve current
  media_source_id/empty-string version normalization, audio/visual sources, and detail
  envelope; use separate staged Manual inputs until the save assigns real source IDs.
User approval required: None.
