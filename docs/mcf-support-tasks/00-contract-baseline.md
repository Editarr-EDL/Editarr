# MCF-00 - Freeze contracts and baseline

Status: **DONE - canonical taxonomy revision**

Dependencies: none. Deliverable: exact shared contracts and baseline evidence; no product
implementation.

Read the task-pack README, parent plan, existing named-review contract task, repository
`AGENTS.md`, live models/OpenAPI, and current backend/frontend tests first.

## Scope

1. Record pre-existing worktree changes and run the baseline with test-owned data. Do not
   create tables or wipe a database.
2. Add a `Contract record` to this file freezing exact Python/JSON/TypeScript spellings for:
   - all MCF category tokens and family/display metadata from 1.0.0/1.1.0;
   - severity, channel, work type, source version, and export format enums;
  - canonical finding/review/manual classification, severity, channel, and comment fields;
   - scan-source and editable-review document metadata;
   - resource, aggregate, EDL, and MCF revision fields and the complete field-to-revision
     matrix;
   - structured selection rules, severity defaults, settings revision, normalization,
     wildcard/AND, and Deny precedence semantics;
   - generic export summary/status/provenance and EDL transition aliases;
   - MCF parse result, import request/result/notices, preflight, and export response;
   - exact methods/routes for defaults, preflight, import upload/path, export, status, and
     deletion for catalog and Other Files;
   - safe error codes/statuses and line/filter-reference details.
3. Freeze import compatibility mappings, metadata seeding/source-choice requests, full-save
   behavior, no-op behavior, duplication, deletion, and scan-reference shapes.
4. Decide routine representation details only where the parent plan permits it. If a
   choice changes product semantics, destructive lifecycle, trust boundary, or format
   output, stop and ask the user.
5. Identify the temporary EDL response/status compatibility adapter and assign its removal
   to MCF-11.
6. Add contract-level tests or fixtures that later tasks can satisfy without adding
   production behavior.

## Required precision

- `category` and `subcategory` are the primary MCF hierarchy and form a registry-valid
  pair. Null is reserved for unclassified legacy EDL imports; new/manual/MCF findings
  require both. Existing `severity` is canonical MCF severity.
- Omitted imported channel is stored canonically according to the contract while retaining
  MCF `both` semantics. Export must have one deterministic omission/emission rule.
- Metadata distinguishes immutable imported source provenance from editable review copy.
- Exact equal-range grouping and review-order payload ordering are part of output identity.
- MCF preflight references stable review-filter IDs and document fields without exposing
  content or local paths.
- Default application never writes source evidence and never runs at export.
- Settings updates are optimistic and cannot overwrite concurrent rule edits.
- The revision matrix is exhaustive across every persisted editable field, including
  metadata, ordering, membership, selection, labels/details, confidence, and MCF fields.
- Confirm from the specifications whether a valid document may contain zero cues and
  record the strict behavior; this is standards research, not a product guess.

## Verification

- Ruff and focused schema/contract/OpenAPI tests affected by test additions.
- Existing EDL and saved-review focused tests.
- Frontend type/tests only if contract fixtures are added there.
- `git diff --check`.

## Acceptance criteria

- [x] Every cross-layer field, route, revision, rule, error, and transition owner is exact.
- [x] The complete MCF registry is checked against both supported specification versions.
- [x] Later tasks can implement without inventing a second DTO or persistence authority.
- [x] Baseline commands, counts, warnings, and unrelated worktree state are recorded.
- [x] No production behavior, schema, database, media, sidecar, or service changed.

## Contract record

This is the revised implementation contract for MCF-01 through MCF-12. It supersedes the
earlier additive legacy/MCF taxonomy in this file. It records planned
interfaces, not behavior present at the MCF-00 baseline. JSON uses `snake_case`; UUIDs are
strings; timestamps are timezone-aware UTC ISO 8601 strings; revisions are non-negative
integers; finite seconds use JSON numbers. New write DTOs reject unknown fields.

### Registry and format constants

`McfSourceVersion = "1.0.0" | "1.1.0"`, existing `Severity = "low" | "medium" |
"high"`, `McfChannel = "both" | "video" | "audio"`, `McfWorkType = "movie" |
"series" | "episode"`, and `ExportFormat = "edl" | "mcf"`. Export always writes MCF
`1.1.0`. Importing an omitted channel stores `both`; deterministic export always emits the
canonical channel, including `=both`.

Both supported specifications have the same 97 category tokens. The exact machine-readable
copy is `tests/fixtures/mcf/category-registry-v1.json`; this table is its normative
cross-layer representation:

| Family (`McfCategoryFamily`) | Exact token and display label |
| --- | --- |
| `commercial_content` | `commercial` - Commercial content (General); `advertBreak` - Advert break; `consumerism` - Consumerism; `productPlacement` - Product placement |
| `discrimination` | `discrimination` - Discrimination (General); `adultism` - Adultism; `antisemitism` - Antisemitism; `genderism` - Genderism; `homophobia` - Homophobia; `misandry` - Misandry; `misogyny` - Misogyny; `racism` - Racism; `sexism` - Sexism; `supremacism` - Supremacism; `transphobia` - Transphobia; `xenophobia` - Xenophobia |
| `dispensable_scenes` | `dispensable` - Dispensable scenes (General); `idiocy` - Idiocy; `tedious` - Tedious scene |
| `drugs` | `drugs` - Drugs (General); `alcohol` - Alcohol; `antipsychotics` - Antipsychotics; `cigarettes` - Cigarettes; `depressants` - Depressants; `gambling` - Gambling; `hallucinogens` - Hallucinogens; `stimulants` - Stimulants |
| `fear` | `fear` - Fear (General); `accident` - Accident; `acrophobia` - Acrophobia; `aliens` - Aliens; `arachnophobia` - Arachnophobia; `astraphobia` - Astraphobia; `aviophobia` - Aviophobia; `chemophobia` - Chemophobia; `claustrophobia` - Claustrophobia; `coulrophobia` - Coulrophobia; `cynophobia` - Cynophobia; `death` - Death; `dentophobia` - Dentophobia; `emetophobia` - Emetophobia; `enochlophobia` - Enochlophobia; `explosion` - Explosion; `fire` - Fire; `gerascophobia` - Gerascophobia; `ghosts` - Ghosts; `grave` - Grave; `hemophobia` - Hemophobia; `hylophobia` - Hylophobia; `melissophobia` - Melissophobia; `misophonia` - Misophonia; `musophobia` - Musophobia; `mysophobia` - Mysophobia; `nosocomephobia` - Nosocomephobia; `nyctophobia` - Nyctophobia; `siderodromophobia` - Siderodromophobia; `thalassophobia` - Thalassophobia; `vampires` - Vampires |
| `language` | `language` - Language (General); `blasphemy` - Blasphemy; `nameCalling` - Name-calling; `sexualDialogue` - Sexual dialogue; `swearing` - Swearing; `vulgarity` - Vulgarity |
| `nudity` | `nudity` - Nudity (General); `bareButtocks` - Bare buttocks; `exposedGenitalia` - Exposed genitalia; `fullNudity` - Full nudity; `toplessness` - Toplessness |
| `sex` | `sex` - Sex (General); `adultery` - Adultery; `analSex` - Anal sex; `coitus` - Coitus; `kissing` - Kissing; `masturbation` - Masturbation; `objectification` - Objectification; `oralSex` - Oral sex; `premaritalSex` - Premarital sex; `promiscuity` - Promiscuity; `prostitution` - Prostitution |
| `violence` | `violence` - Violence (General); `choking` - Choking; `crueltyToAnimals` - Cruelty to animals; `culturalViolence` - Cultural violence; `desecration` - Desecration; `emotionalViolence` - Emotional violence; `kicking` - Kicking; `massacre` - Massacre; `murder` - Murder; `punching` - Punching; `rape` - Rape; `slapping` - Slapping; `slavery` - Slavery; `stabbing` - Stabbing; `torture` - Torture; `warfare` - Warfare; `weapons` - Weapons |

The backend registry is the runtime authority and exposes token, display label, family
token, and family display label. OpenAPI and frontend options consume that contract, not
another handwritten list. `ContentCategory` is replaced by `McfCategoryFamily` for the
nine family tokens in the table; `McfCategory` is the 97 exact subcategory tokens.
`category` stores a family and `subcategory` stores a token belonging to that family.
New JSON/database values use this pair; UI uses family and official subcategory labels.
MCF-01 implements the fresh-schema change; MCF-00 mutates no production model or data.

The strict codec accepts UTF-8 without BOM, LF or CRLF input, and the exact case-sensitive
grammar in the selected specification. Limits are 1,048,576 bytes, 4,096 Unicode code
points per line, and 10,000 payload entries. It requires the header, blank line, ordered
optional 1.1 metadata block, required bounds block, hours in every timestamp, and one or
more payload lines in each cue. Comments are non-empty and contain neither a line break nor
`-->`. Version 1.0 rejects metadata. The specifications do not require a cue after the
bounds block, so a zero-cue document is valid. This intentionally differs from the
upstream website's legacy parser regex, which is not the normative specification.

### Classification and parse DTOs

`category: McfCategoryFamily | null`, `subcategory: McfCategory | null`, existing
`severity: Severity | null`, `mcf_channel: McfChannel | null`, and
`mcf_comment: str | null` appear on `FindingCreate`, `ScanFinding`, `ReviewFilter`,
`ReviewFilterInput`, and `NewManualReviewFilter`. Category/subcategory are both null or a
registry-valid pair. New generated/manual/MCF data requires the pair; unclassified legacy
EDL input may use null. `mcf_comment` uses the codec's single-line and `-->` validation.
Classification never aliases evidence `source`, `edit_action`, `label`, `details`, or
`confidence`.

| DTO | Exact fields |
| --- | --- |
| `McfMetadata` | `title: str | null`, `year: int | null`, `work_type: McfWorkType | null`, `season: int | null`, `episode: int | null`, `source_url: str | null`, `imdb_url: str | null`, `release: str | null`, `comment: str | null` |
| `ScanMcfDocument` | all `McfMetadata` fields plus `source_version: McfSourceVersion`, required `material_start_seconds: float`, required `material_end_seconds: float` |
| `ReviewMcfDocument` | all `McfMetadata` fields plus nullable `material_start_seconds: float` and `material_end_seconds: float`; it has no source-version field because its export version is always 1.1.0 |
| `McfCueEntry` | `cue_index: int`, `payload_index: int`, `line_number: int`, `start_seconds: float`, `end_seconds: float`, `category`, `subcategory`, `severity`, `mcf_channel` (canonical, never null), `mcf_comment: str | null` |
| `McfParseResult` | `source_version`, `source_metadata: ScanMcfDocument`, `entries: list[McfCueEntry]` |

`Scan.mcf_document: ScanMcfDocument | null` is immutable imported provenance.
`SavedReview.mcf_document: ReviewMcfDocument` is an isolated editable row available to
every review. Scan metadata remains after review deletion. Review duplication copies its
metadata values. `ScanFindings` and review/manual DTOs carry canonical classification.

Metadata strings are trimmed and must be non-empty when present. `year >= 0`; `season` and
`episode` are positive. The MCF specification makes `TYPE`, `SEASON`, and `EPISODE`
independently optional and defines no additional relationship, so validation does not
invent one. URLs are absolute HTTP(S) URLs; `imdb_url` must use an IMDb host. Populated
bounds are finite; export requires both and validates
`0 <= material_start_seconds < material_end_seconds`; known runtime additionally requires
the end not exceed runtime. Metadata text and URLs obey the 4,096-code-point line bound.

`McfMetadataSeedRequest` is a discriminated union: `{kind: "scan", scan_id: str}` or
`{kind: "catalog"}`. `McfMetadataSeedCandidate` has `kind`, nullable `scan_id`, safe
`label`, and `mcf_document: ReviewMcfDocument`. `ReviewCreate` adds nullable
`mcf_metadata_seed`; `ReviewSave` adds required `mcf_document`. Exactly one included
imported scan seeds automatically; no imported scan uses catalog/runtime defaults;
distinct imported candidates require an explicit seed. `POST` create with no valid seed
returns 409 `mcf_metadata_source_required` before creating anything and includes ordered
`metadata_candidates` on `ReviewErrorDetail`: imported candidates in membership order,
followed by catalog. Catalog values only fill null fields and runtime only supplies
material end. `ReviewCreate` may omit the seed when there is no conflict. A supplied scan
must be an included imported scan; catalog is always a valid explicit choice. Duplicate's
request shape remains `ReviewDuplicate`, so it cannot accept a seed and always copies the
source review metadata.

### Persistence authorities

MCF-01 owns these fresh-schema authorities: nullable family/subcategory plus severity,
channel, and comment columns on `ScanFindingRow` and `ReviewFilterRow`; one
`ScanMcfDocumentRow` in
`scan_mcf_documents` keyed by `scan_id`; one `ReviewMcfDocumentRow` in
`review_mcf_documents` keyed by `review_id`; `edl_content_revision` and
`mcf_content_revision` columns on `SavedReviewRow`; `FilterSettingsRow` plus ordered
`FilterSelectionRuleRow` and `McfSeverityDefaultRow`;
and format-keyed `MediaItemExportRow` plus `MediaItemExportScanRow`. Metadata values use
normal columns, never JSON. Review-owned rows cascade only with review deletion; scan
metadata cascades only with scan deletion; no review deletion reaches scan evidence.

The structured `FilterDefaultsSettings` completely replaces legacy `video`/`audio`
free-text settings in the fresh schema. The existing `/api/settings/filters` route is
retained but changes to the new DTO in MCF-03. There is no legacy-settings migration under
the approved checkout-only reset. Until MCF-08 updates the frontend, a backend adapter may
read the seeded structured rules into the old response shape, but writes have one
structured persistence authority and the adapter is removed by MCF-11.

### Compatibility mappings

MCF import creates one finding per payload line. `audio` maps to source `audio`, action
`mute`, and membership capability audio. `video` maps to source `visual`, action `skip`,
and video capability. `both`, including an omitted channel canonicalized to `both`, maps
to source `visual`, action `skip`, and both capabilities. Mixed documents have one `both`
membership. Confidence is `1.0`, selected initially enters the default evaluator, label is
the registry display label, and details copies a present MCF comment while `mcf_comment`
retains it independently.

MCF import stores the exact payload token as `subcategory` and derives `category` from the
registry. Current ordinary profanity producers emit `language/swearing`; an explicitly
blasphemy-specific producer emits `language/blasphemy`; nudity emits `nudity/nudity`;
violence emits `violence/violence`. Generic profanity never infers blasphemy from labels or
transcript heuristics. Evidence source defaults channel `audio -> audio` and
`visual -> video`. Confidence never maps to severity.

EDL import recognizes an exact MCF subcategory token and derives its family. Legacy
`profanity`, `nudity`, and `violence` comment categories map to `language/swearing`,
`nudity/nudity`, and `violence/violence`. Exact `blasphemy` maps to
`language/blasphemy`. Unknown, `other`, and uncommented rows use null category/subcategory
and retain their label/details. EDL export emits the canonical subcategory when classified
and the transport sentinel `other` only for an unresolved legacy finding.

MCF import creates the scan, source metadata, findings, review, membership, review metadata,
and review filters in one transaction. A malformed or failed import creates none. Imported
non-null metadata wins; catalog/runtime fills gaps. Metadata mismatch does not change
physical identity. Import and review creation never create an export marker.

### Structured defaults

`FilterRuleDisposition = "allow" | "deny"`.
Here `evidence_source` means the finding medium `audio | visual`; it is unrelated to
`ReviewSource`, which identifies `jellyfin | radarr | sonarr | manual` target origin.

| DTO | Exact fields |
| --- | --- |
| `FilterSelectionRule` | `id: UUID`, `disposition`, nullable `evidence_source: audio | visual`, nullable `category: McfCategoryFamily`, nullable `subcategory: McfCategory`, nullable `label_contains: str`, nullable `severity`, nullable `mcf_channel` |
| `McfSeverityDefault` | `subcategory`, `severity` |
| `FilterDefaultsSettings` | `revision: int`, ordered `selection_rules`, registry-ordered `severity_defaults` |
| `FilterDefaultsUpdate` | same complete fields as `FilterDefaultsSettings`; `revision` is the expected revision |
| `FilterDefaultsApplyRequest` | complete ordered `filters: list[ReviewFilterInput]` |
| `FilterDefaultsApplyResult` | complete ordered `filters`, `selection_changes: int`, `severity_changes: int`, `channel_changes: int` |

Array order is the public order; persistence uses contiguous zero-based positions. IDs are
stable through reorder and replacement. Null and normalized blank string clauses are
wildcards, but a rule must contain at least one non-wildcard match clause. Populated clauses
within a rule are ANDed. Enum clauses compare exactly. Text clauses are NFKC case-folded,
split into Unicode letter/digit tokens, and compared as a contiguous token sequence; this
retains punctuation-insensitive audio matching (`"word!"` matches `"word"`). `scan_model`
is trimmed and compared exactly to the backend capability ID.

All selection rules are evaluated. A matching deny means `selected=true`; otherwise a
matching allow means `selected=false`; deny therefore wins regardless of list order. No
match preserves the input selection. A populated subcategory clause requires its registry
family. Automatic create/import application preserves category/subcategory, fills null
severity and channel, then evaluates selection. Explicit Apply recomputes severity from
the subcategory default, channel from evidence source, and selection; it never reclassifies
or writes source findings. Export never invokes this evaluator.

Every registry subcategory appears exactly once in severity defaults; fresh settings seed
`medium`. Settings normalization trims strings, converts blanks to null, rejects duplicate
IDs/subcategories, canonicalizes contiguous order, and compares normalized values for no-op.
PUT uses optimistic revision: a changed update increments once, a normalized no-op returns
unchanged revision, and a stale expected revision always returns 409 without writes.

### Revisions and full-save behavior

`SavedReviewSummary` retains `revision` and `content_revision` and adds
`edl_content_revision`, `mcf_content_revision`, and `exports: ExportStatus`. All four
revisions start at zero. One successful operation increments each affected revision at
most once. A stale expected resource revision is rejected even for a no-op.

| Persisted editable change | `revision` | `content_revision` | `edl_content_revision` | `mcf_content_revision` |
| --- | --- | --- | --- | --- |
| Name | yes | no | no | no |
| Membership scan, role, or order | yes | yes | yes | yes |
| Filter order, selected, start, or end | yes | yes | yes | yes |
| Filter `edit_action` | yes | yes | yes | no |
| Filter `category` or `subcategory` | yes | yes | yes | yes |
| Filter `label` | yes | yes | yes | no |
| Filter `details`, `group_key`, or `confidence` | yes | yes | no | no |
| Filter `severity`, `mcf_channel`, or `mcf_comment` | yes | yes | no | yes |
| Any editable `ReviewMcfDocument` field | yes | yes | no | yes |

Filter IDs, source scan/finding IDs, and evidence source are immutable after snapshot
creation. Target/media identity, scan evidence, and scan MCF metadata are immutable and not
review revision inputs. A combined save increments each applicable revision once. A fully
normalized no-op changes no revision or timestamp. Save remains full replacement: omitted
filters/memberships are removed from only this review, no hidden resnapshot occurs, and
new manual evidence is assigned before the authoritative response. Create/import/duplicate
start all revisions at zero. Duplicate uses the expected source resource revision, creates
fresh filter IDs, copies editable metadata, and has no export attribution. Export changes
no review revision or timestamp.

### Generic export state and references

| DTO | Exact fields |
| --- | --- |
| `ExportScanReference` | existing `EdlScanReference` fields: `id`, display `scan_id`, `role`, `name`, `origin` |
| `ExportSummary` | `format: ExportFormat`, `present: bool`, `exported_at: datetime | null`, `review_id: str | null`, `exported_format_revision: int | null`, `current: bool`, `outdated: bool`, `exported_scans: list[ExportScanReference]`, `exported_review: SavedReviewSummary | null` |
| `ExportStatus` | `exports: dict[ExportFormat, ExportSummary]`; both `edl` and `mcf` keys are always present |
| `ReviewExportResponse` | `format`, `review_id`, `exported_format_revision`, `exported_at`, `exported_scans`, `exported_review` |
| `ReviewReference` | unchanged `id`, `name`, `updated_at` |
| `ScanReferences` | `scan_id`, `active_exports: list[ExportFormat]`, `reviews: list[ReviewReference]` |

`present` reflects the persisted format row, not an opportunistic filesystem probe.
`current` is present with attributed review and equal current/exported format revision;
`outdated` is present with attributed review and unequal revisions. Unattributed retained
sidecars are present but neither current nor outdated. A format row is unique by media item,
normalized media version, and format. Export/delete touches only that format. Review
deletion clears its attribution from every format but retains files, timestamps, and scan
provenance. Scan deletion is blocked by any review or active export reference.

During transition, `EdlScanReference` aliases `ExportScanReference`; `EdlStatus`,
`ReviewEdlExport`, summary `exported`/`exported_content_revision`/`outdated`, catalog
`has_exported_edl`/`exported_at`/`exported_scans`/review fields, and
`ScanReferences.active_edl` are projections of `exports.edl`. They are never a second
persistence authority. MCF-11 removes these aliases after all frontend consumers use
`ExportStatus`.

### Import, preflight, and export DTOs

| DTO | Exact fields |
| --- | --- |
| `McfImportOptions` | `target: ReviewTargetRequest`, `name: ReviewName` |
| `McfPathImportRequest` | all options plus `mcf_path: str` |
| `McfMetadataNotice` | `code: "metadata_mismatch"`, `fields: list[str]`, `mcf_metadata: ReviewMcfDocument`, `catalog_metadata: ReviewMcfDocument` |
| `McfImportResult` | `review: SavedReview`, `scan: Scan`, `source_version: McfSourceVersion`, `notices: list[McfMetadataNotice]` |
| `McfPreflightIssue` | `code`, `field`, nullable `filter_id`, nullable `filter_position`, safe `message` |
| `McfPreflight` | `review_id`, `revision`, `eligible: bool`, ordered `issues: list[McfPreflightIssue]` |

Preflight reads the persisted review. `filter_id` is the review-local `ReviewFilter.id`
UUID and `filter_position` is its zero-based position in `SavedReview.filters`; both are
null for document issues. Document issues follow `McfMetadata` field order. Filter issues
then follow review position and, within one filter, category, subcategory, severity,
channel, comment.
Allowed issue codes are
`missing_category`, `missing_subcategory`, `missing_severity`, `invalid_category`,
`invalid_subcategory`, `invalid_category_pair`, `invalid_severity`,
`invalid_mcf_channel`, `invalid_mcf_comment`,
`invalid_mcf_metadata`, and `invalid_material_bounds`. It reports field names and filter
identity only, never raw comments, content, URLs, or paths.

MCF output uses LF, UTF-8 without BOM, the 1.1.0 header, present metadata in specification
order, bounds, and selected filters only. Equal start/end pairs form one cue and preserve
review payload order (ascending zero-based filter position). Cues sort by start, end, then
the minimum review position among their payloads. Thus `[1,5]`, `[1,6]`, and `[2,5]` are
three cues; only exact equality of both endpoints groups payloads. MCF does not use EDL
merge, subtraction, or action precedence.

### Exact HTTP routes

Existing review collection/item/preview routes remain unchanged. Every route is under the
configured application root, requires setup/authentication, and every unsafe method uses
the existing same-origin and forms-session CSRF policy.

| Method and route | Request | Success response |
| --- | --- | --- |
| `GET /api/settings/filters` | none | `FilterDefaultsSettings` |
| `PUT /api/settings/filters` | `FilterDefaultsUpdate` | `FilterDefaultsSettings` |
| `POST /api/settings/filters/apply` | `FilterDefaultsApplyRequest` | `FilterDefaultsApplyResult` (pure result, no persistence) |
| `POST /api/mcf-imports/upload` | multipart `file` plus JSON-string form field `options: McfImportOptions` | 201 `McfImportResult` |
| `POST /api/mcf-imports/path` | `McfPathImportRequest` | 201 `McfImportResult` |
| `GET /api/reviews/{review_id}/mcf/preflight` | none | `McfPreflight` |
| `POST /api/reviews/{review_id}/mcf` | existing `ReviewExportRequest` | `ReviewExportResponse` |
| `POST /api/reviews/{review_id}/edl` | existing `ReviewExportRequest` | `ReviewExportResponse` plus transitional EDL response aliases |
| `GET /api/library-items/{item_id}/exports` | query `media_source_id` | `ExportStatus` |
| `DELETE /api/library-items/{item_id}/exports/{format}` | query `media_source_id` | 204 |
| `GET /api/sources/{service}/items/{item_id}/exports` | query `media_source_id`; service is `radarr | sonarr` | `ExportStatus` |
| `DELETE /api/sources/{service}/items/{item_id}/exports/{format}` | query `media_source_id` | 204 |
| `GET /api/manual-files/exports` | required `item_id` | `ExportStatus` |
| `DELETE /api/manual-files/exports/{format}` | required `item_id` | 204 |

Catalog list/detail/version DTOs and saved-review summaries embed the same `exports` map.
Catalog imports never accept a path or duration from the browser. Scanner-path import
requires an absolute readable `.mcf`; upload requires a `.mcf` basename and accepts MIME
hint `text/plain` or `text/mcf+vtt` without treating MIME as format authority.

### Safe errors

Errors retain `ReviewError {detail: ReviewErrorDetail}`. `ReviewErrorDetail` retains
`code`, safe `message`, nullable `line_number`, nullable `line_text`, and nullable
`references`; it adds
`filter_ids: list[UUID] = []`, `document_fields: list[str] = []`, and
`metadata_candidates: list[McfMetadataSeedCandidate] = []`, plus
`import_issues: list[McfImportIssue] = []`. Only
`mcf_metadata_source_required` populates candidates. Line-aware import parser failures
include only the offending source line in `line_text`. Recoverable MCF import failures use
`mcf_import_issues` and populate ordered issue objects with the offending line; retrying
with `ignore_errors: true` skips invalid metadata values and cues. Other errors never
include payload text, metadata values, credentials, source paths, scanner paths, or
sidecar paths.

| Status | Code and use |
| --- | --- |
| 400 | `mcf_import_unreadable` for path/extension/readability; existing `media_unavailable` for invalid target input |
| 404 | `review_not_found`, `scan_not_found`, or `export_not_found` |
| 409 | `stale_review_revision`, `stale_settings_revision`, `invalid_membership`, `mcf_metadata_source_required`, `scan_referenced`, or unavailable/current target conflicts |
| 413 | `mcf_import_too_large` |
| 422 | `unsupported_mcf_version`, `invalid_mcf_document`, `invalid_mcf_metadata`, `mcf_import_issues`, or `incomplete_mcf_review` |
| 500 | `export_delete_failed` or `{format}_export_failed`; previous sidecar/provenance is restored |
| 502 | existing catalog/source `media_unavailable` failures |

Parser failures use one-based `line_number` and the matching `line_text` when a source line
owns the error. Recoverable imports report every issue before persistence; malformed
encoding/header/version and resource-limit failures remain non-recoverable. Unsupported
version identifies line 1. Import validation and all target failures roll back the whole
transaction. `incomplete_mcf_review` includes ordered `filter_ids` and `document_fields`
matching preflight but omits the invalid field values. Authentication/setup/CSRF failures
retain existing global envelopes and execute before parsing or filesystem/database side
effects.

### Baseline evidence

The worktree was clean before MCF-00. On September 17, 2026: `uv 0.12.5`, Node
`v26.7.0`, and pnpm `11.22.0`; the unqualified `python` command was unavailable (exit 127),
while project commands ran through `uv`. `uv run ruff check scanner tests` passed.
`uv run pytest -q tests/test_edl.py tests/test_editarr_integration.py
tests/test_auth_schema.py` passed 72 tests in 1.90 seconds. In `web/`, `pnpm test` passed
100 tests in 10 files and `pnpm build` succeeded with 38 modules transformed.
`git diff --check` passed. All tests owned their data; no development/user database,
service, media, or sidecar was touched.

The registry and grammar were checked against both official specification pages on
September 17, 2026. Both pages list the same 97 category tokens; only 1.1.0 defines the
ordered metadata section. Neither page requires at least one cue after the bounds block.

## Handoff

Status: DONE
Last completed substep: Replaced the superseded additive legacy/MCF taxonomy with one
canonical MCF family/subcategory contract and reran focused contract verification.
Files changed: `docs/mcf-support-plan.md`, `docs/mcf-support-tasks/README.md`, MCF task
files 00/01/03/04/05/07/08/09/11/12, `tests/test_mcf_contract.py`, and
`tests/fixtures/mcf/editarr-classification-v1.json`.
Schema/API/contract changes: `category: McfCategoryFamily` and
`subcategory: McfCategory` are now primary; existing `Severity` is canonical MCF
severity. New/manual/MCF findings require a registry-valid pair. Legacy EDL may retain a
null pair. Fixed core mappings are profanity to language/swearing, explicit blasphemy to
language/blasphemy, nudity to nudity/nudity, and violence to violence/violence. Category-
assignment settings and duplicate `mcf_category`/`mcf_severity` fields are removed from
the plan. MCF-12 owns post-core AI subcategorization and must evaluate Qwen summaries.
Verification commands and results: Original baseline evidence above remains valid. Revised
`uv run pytest -q tests/test_mcf_contract.py` passed 2 tests in 0.01s; `uv run ruff check
tests/test_mcf_contract.py` passed. Final `uv run pytest -q tests/test_mcf_contract.py
tests/test_edl.py` passed 11 tests in 0.08s; final Ruff passed; `git diff --check` passed.
Manual or artifact evidence: The existing official 97-entry MCF registry fixture remains
unchanged. A separate Editarr classification fixture freezes canonical fields, initial
producer mappings, EDL aliases, and the post-core Qwen requirement.
Known failures or risks: The checkout currently has no explicit blasphemy producer; core
generic profanity therefore maps to swearing. MCF-12 owns evaluated richer classification.
Acceptance criteria still unmet: None.
Next exact action: Implement MCF-01 fresh persistence and producer mappings against this
revised contract, using only the approved checkout database-reset boundary if needed.
Next eligible task: MCF-01 and MCF-02.
Decisions or deviations from the parent plan: This revision intentionally supersedes and
may ignore the earlier completed MCF-00 additive-taxonomy contract, per user direction.
User approval required: None. Checkout-only reset approval for MCF-01 remains recorded;
installed/user data remains excluded.
