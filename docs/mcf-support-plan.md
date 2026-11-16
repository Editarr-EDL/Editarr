# Movie Content Filter support plan

Status: **Implemented through tiered Review tables**

Plan date: **September 17, 2026**

Last revised: **September 18, 2026**

Specification baseline:

- [Movie Content Filter 1.1.0](https://www.moviecontentfilter.com/specification?v=1.1.0)
  is the only export format.
- [Movie Content Filter 1.0.0](https://www.moviecontentfilter.com/specification?v=1.0.0)
  and 1.1.0 are accepted for import.

Task tracker: [MCF support coding tasks](mcf-support-tasks/README.md)

This plan adopts the Movie Content Filter (MCF) category hierarchy as Editarr's primary
classification taxonomy while adding MCF import/export as a superset of Simple EDL.
Every newly generated or manually created finding uses an MCF family/category pair.
Legacy EDL input may remain temporarily unclassified when it contains no recognizable
category, but it remains usable for EDL and can be classified in a saved review.

This plan extends the implemented baseline in
[Named scans, saved reviews, and EDL import](named-scans-saved-reviews-edl-import-plan.md)
and the authoritative behavior in [Feature design specification](feature-design-spec.md).
It does not change code, schemas, databases, sidecars, or user data.

## 1. Required outcomes

Implementation must deliver all of the following:

1. Import strict MCF 1.0.0 and 1.1.0 documents through browser upload or an absolute
   scanner-host path for Jellyfin, Radarr, Sonarr, and Other Files targets.
2. One MCF document creates one completed imported scan and one saved review atomically.
   One payload line becomes one source finding and one initial review snapshot. Multiple
   payload lines in one WebVTT cue therefore become distinct filters sharing a range.
3. Replace Editarr's four-value content taxonomy with the MCF hierarchy. Store the MCF
  family as `category` and exact MCF cue token as `subcategory`, validate the pair from
  one registry, and retain severity, channel, comment, evidence source, EDL action,
  label, and description as distinct concerns.
4. Preserve the imported document's MCF version, metadata, and material bounds as
   read-only scan provenance. Give the created review an independently editable metadata
   copy. Deleting or editing a review must not rewrite imported evidence.
5. Let manual filters and saved-review filter edits enter every relevant MCF field.
6. Show category, subcategory, severity, channel, comment, and document metadata in scan,
  review, grouped-result, editor, and provenance UI. Unclassified legacy EDL findings
  remain visibly incomplete until the user assigns a valid pair.
7. Replace current free-text Audio/Video default lists with structured selection rules
  over canonical classification fields and per-subcategory severity defaults.
8. Apply defaults to new review snapshots as defined in section 7, and let
   `Apply Filter Defaults` update the current draft. Defaults never mutate immutable
   source evidence and never apply silently during export.
9. Export selected, MCF-complete review filters as deterministic MCF 1.1.0 beside the
   media file. Continue exporting any EDL-capable review as Simple EDL even if it has no
   MCF fields.
10. Replace the single `Export EDL` command with an `Export` menu listing EDL and MCF.
    Choosing a format starts that format's validation and export flow.
11. Track `.edl` and `.mcf` sidecars independently by media item/version, review,
    contributing scans, export time, format revision, current/outdated state, deletion,
    and UI badges. The two files may coexist.
12. After the core MCF feature is complete, improve AI-generated subcategorization beyond
    the initial general mappings. At minimum, evaluate and use Qwen summaries to assign
    more specific valid subcategories when reliable, with deterministic general-category
    fallback.
13. Preserve authentication, CSRF, source resolution, physical-version isolation,
    atomic file replacement, dirty-draft guards, accessibility, responsive behavior,
    and catalog/Other Files parity.
14. Present Review findings in collapsible family, subcategory, and finding-group tiers,
    with severity/channel columns and independently sortable Audio and Video tables.

## 2. MCF specification boundary

### 2.1 Accepted document structure

The codec is a pure, strict parser/exporter for the MCF subset of WebVTT. It must not use
a permissive subtitle parser that accepts syntax outside the MCF specification.

- Files use extension `.mcf`, UTF-8 without BOM, and compatibility MIME type
  `text/plain`; `text/mcf+vtt` is also recognized where a MIME type is accepted.
- The header is exactly `WEBVTT MovieContentFilter 1.0.0` or
  `WEBVTT MovieContentFilter 1.1.0` on import. Export always writes 1.1.0.
- Both versions require the `NOTE`, `START`, and `END` material-bound section.
- Version 1.1.0 may additionally contain the ordered metadata fields `TITLE`, `YEAR`,
  `TYPE`, `SEASON`, `EPISODE`, `SOURCE`, `IMDB`, `RELEASE`, and `COMMENT`.
- Every timestamp includes hours and is validated as a WebVTT timestamp.
- Each cue has one time range and one or more payload lines. A payload line is exact MCF
  category, required severity, optional channel, and optional comment.
- Category and literal tokens are case-sensitive. Severity is `low`, `medium`, or `high`.
  Channel is `both`, `video`, or `audio`; an omitted channel has the MCF-defined `both`
  meaning.
- The canonical category registry and display labels are copied from the selected MCF
  specification versions into one backend-owned definition. Backend validation,
  OpenAPI, frontend options, mappings, display, and tests consume that shared contract.
- MCF defines classification, not playback action. It does not add a third EDL action.

Use the established EDL safety bounds unless MCF-00 records a stricter proven bound:
1 MiB content, 4,096 Unicode code points per line, and 10,000 payload entries. Reject BOM,
invalid UTF-8, unsupported versions, invalid section order, unknown tokens, malformed
timestamps, invalid comments, and ranges outside the trusted media duration. Errors are
line-aware and never echo file content or scanner-host paths.

### 2.2 1.0.0 compatibility

MCF 1.0.0 uses the same material-bound, cue, category, severity, channel, and comment
grammar needed by Editarr. It lacks the optional work-metadata block added in 1.1.0.
Import retains source version `1.0.0`, then initializes missing editable review metadata
from trusted target/catalog data where available. Re-export is a new 1.1.0 document.

## 3. Confirmed product decisions

These decisions were confirmed on September 17, 2026 and must not be silently reopened.

### D1. Canonical MCF classification

Replace `ContentCategory` with the MCF hierarchy on immutable `ScanFinding` evidence,
review-owned `ReviewFilter` snapshots, manual-filter input, and Python/TypeScript DTOs:

- `category: McfCategoryFamily | null` stores the MCF top-level family;
- `subcategory: McfCategory | null` stores the exact MCF cue category token;
- `severity: Severity | null` uses the existing `low | medium | high` enum as MCF severity;
- `mcf_channel: McfChannel | null` stores affected media independently of evidence source;
- `mcf_comment: str | null` stores the cue comment independently of label/description.

Category and subcategory are either both null or a registry-valid pair. They are required
for every newly generated, manually created, or MCF-imported finding. Null is reserved for
legacy EDL input whose comment cannot be mapped to MCF. Review editing can resolve it.
Severity is populated by scanners/import/defaults where available and is required for MCF
export. Confidence never becomes a second category or severity field.

### D2. MCF import action mapping

MCF has no skip/mute action, so import creates the legacy compatibility fields as follows:

| MCF channel | Editarr source | EDL action |
| --- | --- | --- |
| `audio` | `audio` | `mute` |
| `video` | `visual` | `skip` |
| `both` or omitted | `visual` | `skip` |

The exact MCF channel remains separately stored. A later review edit may change EDL
action/source-compatible behavior without erasing the MCF classification.

### D3. Initial scanner and EDL classification

MCF import stores each payload token directly as `subcategory` and derives its registry
family as `category`; it never maps back to a smaller Editarr taxonomy. Initialize `label`
with the official subcategory display name. Copy a cue comment to `details` when present
while retaining it independently in `mcf_comment`. Confidence is `1.0`.

Until post-core AI subcategorization is implemented, current producers map as follows:

| Existing finding | Canonical category/subcategory |
| --- | --- |
| Ordinary profanity finding | `language` / `swearing` |
| Finding explicitly marked by a blasphemy producer | `language` / `blasphemy` |
| Nudity finding | `nudity` / `nudity` |
| Violence finding | `violence` / `violence` |

A blasphemy-specific producer assigns `language/blasphemy` directly. Generic profanity
code does not infer blasphemy from label or transcript heuristics. Existing `other`
findings have no MCF fallback. Test-only producers must choose a real registry pair;
legacy EDL `other`, unknown comments, and uncommented rows import with a null pair and
retain their label/details until classified in the review.

EDL import recognizes any exact MCF subcategory token and derives its family. It also
accepts legacy Editarr comment categories `profanity`, `nudity`, and `violence`, mapping
them to the general pairs above. New EDL comments emit the exact canonical subcategory
token, so EDL round trips preserve the most specific known classification.

### D4. One scan per imported document

One MCF file creates one imported scan, including when it contains audio, video, and both
channels. Scan capability flags reflect every channel present. The saved review has one
membership whose role is audio, video, or both according to the document. Shared cue
ranges do not create compound findings: one payload line remains one independently
editable finding/filter.

### D5. Editable review metadata and immutable import provenance

Represent the MCF document metadata separately from finding fields. The imported scan
retains a read-only source snapshot containing:

- imported MCF version;
- title, year, type, season, episode, source URL, IMDb URL, release, and comment;
- material start and end.

The initial saved review receives an editable copy. Imported non-null values win over
catalog values; missing values are seeded from trusted catalog/version metadata and media
runtime where available. A metadata conflict does not create a new physical file identity
or reject import: it is an editable MCF metadata variant on the selected physical version.

Duplicating a review copies its editable metadata. A new review seeds metadata from its
chosen imported scan when exactly one source is selected, then fills gaps from trusted
catalog/runtime data. If included scans offer different MCF metadata, the creation UI
requires the user to choose a source scan or catalog/runtime defaults; it never merges or
chooses by membership order silently.

### D6. Material bounds

For a new non-imported review, material start defaults to zero and material end defaults
to trusted media duration. Both remain editable. MCF export requires finite bounds,
`0 <= START < END`, and consistency with known media runtime. Import preserves valid
source bounds in scan provenance and seeds the review copy from them.

### D7. Import/export versions

Import supports exactly MCF 1.0.0 and 1.1.0. Export supports exactly MCF 1.1.0. A
recognized but unsupported header fails safely; there is no best-effort future-version
parsing.

### D8. Export eligibility and no export-time defaults

Category, subcategory, and severity are required on every selected filter. MCF channel is optional
and omission retains the specification's `both` meaning. MCF metadata and material bounds
must pass 1.1.0 validation. If category/severity cannot be completed by saved review data,
MCF export is blocked by a preflight that identifies each incomplete filter. It never
omits filters, inserts a universal fallback, or mutates the review during export.

Mappings and defaults apply while creating or editing a draft, not at export time. The
user saves those changes before export. This keeps output reproducible from the saved
review alone.

### D9. Deterministic MCF cue output

MCF export uses selected review filters' exact ranges. It does not use EDL merge,
skip-precedence, or mute subtraction rules. Selected filters with exactly equal start and
end timestamps are emitted as payload lines in one cue in review order. Other filters
receive separate cues sorted deterministically by start, end, and review order. The
exporter does not combine merely overlapping ranges.

### D10. Independent exports

`.edl` and `.mcf` may coexist beside one media file. Each format independently records:

- presence and destination format;
- UTC export time;
- source saved review;
- the review's format-specific revision;
- every contributing scan and role;
- current/outdated status.

Exporting or deleting one format does not change the other file or marker. Deleting a
review preserves existing sidecars and scan provenance but clears that review's
attribution from every format marker. Scan deletion is blocked by a reference from any
saved review or active export format.

### D11. Format-specific staleness

Keep the resource revision for optimistic concurrency and an aggregate content revision
for compatibility/auditing. Add independent EDL and MCF content revisions.

- Membership/order, filter order, selected state, and timing changes increment both
  format revisions.
- Category, subcategory, and timing/selection changes increment both EDL and MCF revisions
  because EDL comments emit the canonical subcategory.
- EDL action and label increment the EDL revision.
- Severity, channel, cue comment, and document metadata increment the MCF revision.
- Fields not rendered in a format do not make that format's sidecar outdated.
- Name-only review changes affect only the resource revision.
- A single save increments each affected revision at most once.

MCF-00 must freeze the exact field-to-revision matrix before schema work. Tests compare
every editable field against that matrix.

### D12. Classification and severity defaults

The producer/EDL mappings in D3 are fixed ingestion compatibility rules, not mutable
Settings mappings. Channel defaults from evidence source (`audio` to `audio`, `visual` to
`video`). Every MCF subcategory has a configurable severity default initially set to
`medium`. Confidence is never interpreted as severity.

### D13. Unified default-filter rules

Replace separate Audio/Video free-text Allow and Deny lists with one ordered structured
rule list. A rule has Allow or Deny disposition and optional match clauses for evidence
type, category, subcategory, label, severity, and channel. Blank clauses are
wildcards; populated clauses are ANDed. At least one match clause is required. Existing
audio punctuation/root normalization and visual category matching remain available
through the legacy clauses. If any Allow and Deny rules both match, Deny wins.

Settings also owns severity defaults from D12. The task contract freezes stable rule IDs,
ordering, normalization, wildcard/AND semantics, and exact DTO fields before code.

### D14. When defaults apply

Automatic behavior is limited to a newly created review draft or import:

- New snapshots preserve their canonical category/subcategory, fill missing severity and
  channel, and apply Allow/Deny selection rules.
- MCF import preserves explicit classification/severity/channel and applies selection rules.
- EDL import applies D3 classification, then fills severity/channel and selection defaults.
- New manual filters require category/subcategory, preserve entered values, fill missing
  severity/channel, and apply selection rules.

Changing Settings never mutates a source finding, draft, or saved review. The explicit
`Apply Filter Defaults` action re-evaluates selection and recomputes severity/channel from
current defaults. It never reclassifies category/subcategory. The result is unsaved and
can be saved or discarded.

### D15. Development database policy

The user approved skipping data-preserving migrations and recreating only the checkout's
v1 development database when the persistence task begins. Planning does not wipe any
database. Implementation must resolve and display the exact target, verify that no user
or installed service is using it, and never wipe an installed/user data directory.

### D16. Post-core AI subcategorization

Core MCF work uses the deterministic general mappings in D3 and does not wait for model
research. After MCF-11 completes the import/edit/export feature, MCF-12 evaluates richer
subcategorization for AI findings. It should first use semantically exact model labels
where they map safely, and at minimum must evaluate Qwen summaries as classification
evidence. Any classifier is constrained to the registry family/subcategory pairs, records
its provenance, and falls back to `language/swearing`, `nudity/nudity`, or
`violence/violence` when a more specific result is not reliable.

### D17. Tiered Review results and sorting

Audio and Video Review results use separate three-level hierarchies: MCF family,
subcategory, then the existing finding group (for example, an audio root word). Every
level is collapsible and the leaf finding-group level remains present even when its name
falls back from classification data. Families start expanded; subcategories and finding
groups start collapsed. Families and subcategories remain alphabetical. Description sorts
leaf headers alphabetically, Time sorts them by the first sorted row in each leaf, and
Severity leaves them alphabetical. A legacy finding with no family/subcategory appears
under `Unclassified` family and subcategory sections.

A hierarchy header's only descriptive text is that section's name. Existing disclosure,
bulk-selection checkbox, result count, and selected count may remain, but a header must
not repeat the family, subcategory, severity, channel, comment, or other classification
summary from another level.

Severity and channel are visible table columns. Severity is not a grouping level. Remove
the `ABC | Time` control and make the Time, Description, and Severity column headers sort
buttons using the same `▲`/`▼` glyphs and `aria-sort` behavior as the Saved Reviews table.
Each Audio/Video table has independent sort key and direction state, initially Time
ascending. Clicking an inactive sortable column selects it ascending; clicking the active
column reverses direction. Keep the active glyph immediately beside its label. Sort rows
within each finding group; Time and Description also reorder leaf headers as described
above, without moving or splitting family/subcategory sections. Severity ascending is
missing, Low, Medium, High, and descending reverses that order. Channel is not sortable.

When an existing merged result contains findings from different family/subcategory paths,
repeat the merged row under every applicable path instead of splitting it or assigning it
only to the first path. Put a visible `🔗` after the description to identify every linked
instance. The repeated rows are views of the same underlying result: selecting or
deselecting any instance updates every instance.

## 4. Domain and persistence design

### 4.1 Canonical MCF values

Add closed types for MCF family, subcategory, channel, work type, and specification version.
Reuse the existing `Severity` values. Keep one canonical category registry containing
subcategory token, display label, and family. Do not duplicate handwritten lists between
parser, models, settings, and React.

Replace the existing category column with nullable canonical family/subcategory columns
and persist severity, channel, and cue comment on both `ScanFindingRow` and
`ReviewFilterRow`. Review snapshots copy these values exactly; normal review isolation and
immutable provenance rules continue to apply. Manual evidence stores the values entered
at creation, while its review copy can later diverge. Database/service validation enforces
paired nullability and family/subcategory membership.

### 4.2 Document metadata

Use normalized one-to-one metadata records rather than an opaque JSON string:

- a scan-owned immutable MCF source metadata row, present only for MCF-imported scans;
- a review-owned editable MCF metadata row, available to every saved review.

The contract task freezes nullable fields, URL validation, integer constraints, work-type
relationships, material-bound precision, and source-spec-version provenance. Metadata is
part of review duplication and full-save revision handling. It is not part of source
finding identity.

### 4.3 Export state

Generalize the EDL-only active sidecar persistence into format-keyed export rows and
format-keyed scan provenance. The logical key is media item, normalized media version,
and format (`edl` or `mcf`). Preserve a transition adapter for existing EDL response
fields until the frontend and documentation move atomically to the generic status shape.

Each export row stores the source review and the matching EDL or MCF format revision.
Do not use file existence alone as current-state authority and do not let one format row
overwrite another.

### 4.4 Revision and transaction boundaries

Saved-review create/save/duplicate transactions own filter fields and review metadata.
Imported scan metadata, source findings, the initial review, its metadata copy, membership,
and filter snapshots commit together. A malformed import creates nothing.

Each sidecar export retains the existing filesystem transaction guarantees: validate,
render, write and fsync a temporary sibling, preserve a rollback copy, atomically replace,
then commit format-specific provenance. A database failure restores that format's prior
sidecar without touching the other format.

## 5. Import flow

### 5.1 Entry points and target trust

The Saved Reviews toolbar changes from `Import EDL` to an `Import` command. It opens one
dialog with format choices EDL and MCF, followed by Upload and Scanner path modes. Format
selection controls extension, accepted MIME hints, helper copy, and endpoint. Do not infer
trusted format solely from browser MIME type.

The target item/version is selected before import. Catalog paths, media duration, and
identity are resolved server-side exactly as for EDL. Uploaded names are untrusted display
basenames only. Both transports feed the same pure MCF parser and import transaction.

### 5.2 Parse result and creation

The pure parser returns document version, metadata, material bounds, and ordered cue
entries. It performs no database or filesystem writes.

The import service:

1. resolves and validates the target physical file and duration;
2. parses and validates the complete document;
3. creates one completed `origin=imported` scan with safe basename, timestamp, MCF source
   metadata, channel-derived capability flags, and no queue/worker state;
4. creates one source finding per cue payload line with exact MCF fields and the confirmed
   compatibility mappings;
5. creates one named saved review, role-aware membership, editable metadata copy, and
   isolated filter snapshots;
6. fills only missing MCF defaults and applies unified selection rules;
7. commits everything once and returns the review, scan, source version, and non-blocking
   metadata mismatch notices.

Import does not create, replace, or mark either active sidecar.

## 6. Review, scan, and manual-filter behavior

### 6.1 Scan evidence

Scan finding APIs return canonical category/subcategory, severity, channel, and comment.
Generated/manual/MCF-imported findings have a valid pair; only unresolved legacy EDL
evidence may have a null pair. Scan detail/provenance exposes MCF source document metadata
only when present.

### 6.2 Saved reviews

Saved-review summaries expose independent EDL and MCF export states. Review detail adds
editable MCF document metadata and canonical classification fields. Full-save validation
allows a null pair only on unresolved legacy EDL snapshots and validates every populated
pair strictly before applying the format revision matrix.

When a new review can seed metadata from more than one distinct imported MCF scan, the
backend returns the candidates needed by the UI and requires an explicit seed choice on
create. A duplicate requires no choice because it copies metadata.

### 6.3 Manual filters

The Add Manual Filter dialog retains filter type, label, description, timing, and EDL
action behavior and adds:

- MCF family select and constrained searchable subcategory select;
- severity segmented control (`Low`, `Medium`, `High`);
- channel select (`Both`, `Video`, `Audio`);
- MCF cue comment input with inline spec validation.

Family/subcategory is required for a new manual filter. Severity/channel may be supplied or
filled by defaults. Existing unresolved legacy EDL filters remain editable and EDL-capable;
MCF export preflight blocks until selected filters have a complete valid classification.

## 7. Defaults and mapping design

### 7.1 Settings data

The Filters settings page becomes three work-focused sections:

1. **Selection rules**: one compact editable table with reorder, Allow/Deny disposition,
   optional match clauses, add, duplicate, and delete controls.
2. **Severity defaults**: MCF subcategories grouped by family with a Low/Medium/High selector;
   all begin at Medium.

Saving settings is explicit and revision guarded. Empty invalid rules are rejected. A
settings change has no side effects on existing reviews or export badges.

### 7.2 Shared evaluator

Implement one backend-owned evaluator and matching contract. The frontend may preview
results but cannot invent different matching behavior. Review creation/import applies the
same evaluator used by Apply Filter Defaults. Tests cover normalization, wildcard/AND
semantics, rule order stability, Deny precedence, canonical fields, severity/channel fill,
and explicit source-evidence immutability.

The Apply action updates the local draft only, reports selection/severity/channel changes,
and leaves Save/Discard as authority. It never changes category or subcategory.

## 8. Export and sidecar lifecycle

### 8.1 Export menu and preflight

The review action row uses an `Export` button with an adjacent popover/menu. Items are
`EDL` and `MCF`, each with a short format label and current/outdated state when applicable.
The menu is keyboard navigable, closes on Escape/outside action, restores focus, and does
not use a modal for the simple choice.

EDL keeps current eligibility and normalization. MCF runs preflight against the persisted
review, not the browser draft. Dirty drafts disable Export. MCF preflight reports invalid
metadata and every selected incomplete/invalid filter by stable review-filter ID and
display context. The UI opens or focuses the relevant metadata/filter editor; it does not
silently drop or repair data.

### 8.2 MCF output

Write the source media path with its extension replaced by `.mcf`. Emit UTF-8 without BOM,
LF line endings, exact 1.1.0 header, optional metadata fields in specification order,
required material bounds, then deterministic cues from D9. Validate comments and URLs
before rendering; do not sanitize invalid persisted values into different semantics at
export time.

### 8.3 Badges and deletion

Every current EDL badge surface gains equivalent independent MCF state:

- library table and poster badges;
- movie/series/season/episode rows;
- media/version detail;
- Other Files;
- Saved Reviews table;
- scan/export provenance and deletion conflicts.

Use concise separate badges (`EDL Current`, `EDL Outdated`, `MCF Current`,
`MCF Outdated`) rather than a combined ambiguous `Exported` badge. Each includes a text
label, not color alone. Dense tables may use an icon plus tooltip and screen-reader text;
detail pages show review and scan provenance.

Deletion is format-specific. The detail action becomes `Manage exports` when either
format exists, listing sidecar, timestamp, review attribution, state, and Delete action.
Each delete confirmation names the format and selected media version. Missing files and
filesystem failures preserve database truth according to the existing EDL policy.

## 9. UI design plan

### 9.1 Review results

Keep separate compact Audio and Video tables, but replace the single existing grouping
level with nested collapsible family, subcategory, and finding-group sections. Keep family
and subcategory sections alphabetical; order leaf headers alphabetically for Description
and Severity, or by each leaf's first sorted row for Time. Families are initially expanded;
subcategories and finding groups are initially collapsed. Always retain the finding-group
level, including when its name uses the current classification fallback. Each hierarchy
header uses only its section name as descriptive text; retain the disclosure control,
bulk-selection checkbox, result count, and selected count without repeating classification
summaries in the header. Put legacy findings with no family/subcategory under
`Unclassified` family and subcategory sections.

Use visible Severity and Channel columns instead of placing those values in a section
header or Description summary. Severity is never a grouping level. Exact tokens and
comments remain available in the details editor. Severity and channel remain visible text,
not color-only indicators, and the responsive table must not cause page-level horizontal
scrolling.

Remove the `ABC | Time` segmented control. Time, Description, and Severity are sortable
column headers with Saved Reviews-compatible `▲`/`▼` indicators and `aria-sort` semantics.
Audio and Video keep independent sort key/direction state and each defaults to Time
ascending. An inactive column's first click selects ascending order; subsequent clicks on
the active column reverse direction. Sorting applies only to rows within each finding
group, leaving the alphabetized hierarchy intact. Severity ascending orders missing,
Low, Medium, High; descending reverses it. Channel is a non-sortable column.

If a merged result spans multiple family/subcategory paths, render it under every applicable
path and append a visible `🔗` to each repeated description. These instances stay linked to
one underlying result, so toggling selection in any instance updates all of them. The icon
also needs an accessible text equivalent; it must not be the only indication available to
assistive technology.

The inline filter editor adds an `MCF classification` field group beneath existing fields.
Family uses a select; subcategory uses a searchable selector constrained to that family.
Severity uses a three-option segmented control. Channel uses a select. Comment uses a
single-line input with remaining-length/invalid-sequence feedback. Unresolved legacy EDL
filters show `Needs classification`; new/manual filters cannot clear the required pair.

### 9.2 Review document metadata

Add a collapsible `MCF document metadata` section below the current review title and above
results. Its compact summary shows title/year/type, release, material bounds, and source
provenance. Expanded editing uses ordinary labeled fields, conditional Season/Episode
controls, URL validation, and time controls consistent with review timing inputs.

Show a non-blocking source comparison when imported metadata differs from catalog data.
Label the values as `MCF metadata` and `Catalog metadata`; do not call the MCF variant a
different physical file version. For a new review with conflicting imported sources, use
a required metadata-source select before first save.

### 9.3 Scan and provenance surfaces

Imported scan labels retain scan name/number and imported basename, then add `MCF 1.0.0`
or `MCF 1.1.0`. Scan finding detail shows exact category, severity, channel, and comment.
Generated/EDL/manual scans show MCF fields only when present, avoiding empty decorative
panels.

### 9.4 Import and export interaction

- Replace `Import EDL` with `Import`; the dialog uses format and transport segmented
  controls, then format-specific file/path inputs.
- Preserve required review naming and dirty-draft blocking.
- After MCF import, open the created review, refresh scan/history/export state, and show
  source version plus any metadata comparison notice.
- Replace `Export EDL` with the Export menu from section 8.1.
- Successful export updates only that format's status and announces the destination
  format without exposing a scanner-host path.

### 9.5 Responsive and accessible behavior

All additions inherit the 320px minimum viewport, focus, modal, table, and live-region
requirements in the feature specification. Menus and grouped selectors use semantic
keyboard behavior. Long category labels wrap. No classification state, severity, export
format, or outdated state is communicated only by color.

## 10. API direction

MCF-00 freezes exact DTOs and route names before implementation. The intended direction is:

- replace `ContentCategory` with canonical family/subcategory fields and reuse `Severity`;
- add scan-source and editable-review MCF metadata DTOs;
- expose format-keyed export summaries while retaining an explicit transition for current
  EDL fields;
- add protected review-ID MCF export and source-neutral MCF preflight;
- add protected `/api/mcf-imports/upload` and `/api/mcf-imports/path` transports parallel
  to EDL;
- add format-specific status/delete operations for catalog and Other Files targets;
- replace default-filter lists with revisioned selection-rule and severity-default DTOs;
- preserve structured safe errors, OpenAPI schemas, auth/setup gates, same-origin checks,
  and forms-session CSRF protection.

Likely new safe errors include unsupported MCF version, invalid MCF document, content too
large, invalid metadata, incomplete MCF review, stale settings revision, and unavailable
media. Parser errors include line numbers; preflight errors include stable filter IDs but
never raw content or local paths.

## 11. Documentation and release impact

Update these authoritative surfaces in the implementation tasks:

- `docs/feature-design-spec.md`: source ownership, defaults, review fields, import/export,
  independent badges, deletion, errors, accessibility, and API feature map.
- `docs/api-contract.md`: exact DTOs/routes/statuses and EDL compatibility transition.
- `README.md`: user-facing MCF import, edit, defaults, export, and sidecar behavior.
- Native/release verification: include `.mcf` sidecar permissions and packaged codec
  imports in smoke checks. No new runtime parser dependency is expected.
- Model artifact notices are unaffected unless generated-model mapping later adds a new
  model or dataset.

## 12. Verification strategy

### 12.1 Codec fixtures

Maintain small checked-in fixtures for valid 1.0.0, valid 1.1.0 with all metadata, multiple
payload lines per cue, omitted/default channel, all category tokens, comments, and no-cue
documents if MCF-00 confirms they are specification-valid. Add invalid fixtures for BOM,
encoding, header/version, section order, timestamp, bounds, token case, category, severity,
channel, comment, URL/integer metadata, payload count, and line/content limits.

Round-trip tests compare semantic parsed documents, not insignificant line-ending input.
Export golden tests assert exact deterministic bytes and UTF-8-without-BOM output.

### 12.2 Persistence and service tests

Use temporary databases and media files to prove:

- canonical classification is present on new findings; unresolved legacy EDL remains usable;
- source evidence/review copies isolate edits and duplication;
- MCF 1.0/1.1 import is atomic and creates one correctly typed scan;
- metadata conflict remains a same-file editable variant;
- category/action/text mappings and defaults match decisions;
- manual filters retain every MCF field;
- exact format revision increments and no-op behavior;
- EDL and MCF sidecars coexist, replace, roll back, stale, delete, and clear review
  attribution independently;
- EDL export works from MCF imports and MCF export works from completed manual/generated
  reviews;
- scan deletion reports references from both formats and saved reviews;
- auth, CSRF, source trust, duration/version validation, and safe errors remain enforced.

### 12.3 Frontend tests

Vitest and Testing Library cover category search, severity/channel controls, metadata
editing, source-choice conflicts, unified rule editing, Apply behavior, dirty drafts,
preflight navigation, Import format/transport choices, Export menu keyboard behavior,
independent badge updates, and format-specific deletion.

Playwright covers desktop and 320px mobile flows for catalog and Other Files: import an
MCF fixture, inspect/edit classification, apply defaults, save, export both formats,
observe independent badges, change an MCF-only field, and verify only MCF becomes outdated.
Run axe checks and manual keyboard verification for menus/dialogs/grouped selectors.

### 12.4 Required commands

Each task records focused commands. The final integration task runs at minimum:

- `uv run ruff check scanner tests scripts native_build`
- the complete backend pytest suite with test-owned temporary data
- `pnpm test` and `pnpm build` in `web/`
- relevant Playwright desktop/mobile/axe coverage
- packaged/runtime smoke checks affected by parser/export imports
- `git diff --check`

No verification uses a live catalog, installed database, real sidecar, or user media.

## 13. Rollout and compatibility

1. Freeze exact contracts and baseline tests before schema edits.
2. Implement the fresh schema and generic export state under the approved checkout-only
   reset policy.
3. Land the pure MCF codec independently of database/UI work.
4. Land default-rule evaluation and review-domain support before MCF import/export APIs.
5. Keep existing EDL behavior passing while generic status adapters are introduced.
6. Move the frontend atomically from EDL-only status/default DTOs to the new contracts.
7. Update authoritative docs and remove temporary adapters only after all browser surfaces
   use format-keyed state.
8. After that core checkpoint, implement and evaluate MCF-12 AI subcategorization without
  weakening deterministic general fallbacks.

EDL remains supported throughout. Existing `.edl` files are never renamed or converted.
An MCF import does not alter an active EDL, and adding MCF support does not require MCF
completion before EDL save/export.

## 14. Risks and controls

| Risk | Control |
| --- | --- |
| MCF tokens drift between layers | One canonical registry and exhaustive token tests |
| Severity inferred from confidence | Prohibited; explicit/default severity only |
| Defaults accidentally reclassify findings | Evaluator cannot change family/subcategory |
| EDL and MCF markers overwrite each other | Format-keyed persistence and independent lifecycle tests |
| MCF output accidentally uses EDL normalization | Separate pure exporters and golden fixtures |
| Imported metadata changes target identity | Keep target version authoritative; store a metadata variant |
| Multiple imported metadata sources merge silently | Require explicit source choice |
| Parser accepts generic WebVTT extensions | Strict grammar and invalid fixture matrix |
| Schema reset affects user data | Resolve exact checkout DB, explicit safety checks, never touch installed data |
| Long MCF category lists make review UI unwieldy | Searchable family-grouped selector and compact result summaries |

## 15. Definition of done

MCF support is complete only when:

- all required outcomes in section 1 are implemented for every supported source;
- MCF 1.0/1.1 import and deterministic 1.1 export pass exhaustive codec tests;
- scans, review snapshots, manual filters, defaults, metadata, and UI use the canonical MCF
  hierarchy while unresolved legacy EDL data remains EDL-capable;
- EDL and MCF can be imported/exported across formats as approved;
- both sidecars have independent, accurate current/outdated badges and deletion;
- no parser, API, log, or UI error leaks file content, credentials, or scanner-host paths;
- desktop/mobile/accessibility checks pass;
- authoritative docs and live OpenAPI match implementation;
- the task-pack handoffs contain reproducible passing evidence;
- post-core MCF-12 evaluates richer AI subcategorization, including Qwen summaries, with
  deterministic general-category fallback.
