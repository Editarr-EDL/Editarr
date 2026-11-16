# Editarr retrospective feature and design specification

Status: **Authoritative server and web baseline**

Baseline date: **September 20, 2026**

This document reconstructs Editarr's product, feature, interaction, and
technical design from the implemented backend, web application, and automated
tests. It is the baseline for subsequent feature planning. New work should
preserve these rules unless a request explicitly changes this specification.

The older [implementation plan](implementation-plan.md) is a historical
execution checklist. Where the two documents disagree, this specification is
authoritative.

## Named scans and saved reviews baseline (September 16, 2026)

The named-scans/saved-reviews/EDL-import implementation is complete through the
catalog and import workflows. Scan names are optional display metadata, trimmed with
Unicode-safe validation, non-unique, mutable, and limited to 100 Unicode code points;
stable scan UUIDs and per-item scan numbers remain identity. Scan origin is one of
`generated`, `manual`, or `imported`, and imported scans retain a safe EDL basename and
import timestamp without creating queue ownership or active EDL state.

Saved reviews are persistent aggregates scoped to a media item and physical version.
They own ordered memberships and review-local filter snapshots, including selection,
content, timing, action, and provenance. Review resource revisions guard mutations;
content revisions change for membership/filter changes but not name-only rename. Duplicate
names are valid. The active EDL stores review ID/content revision and every contributing
scan role; a content change after export is `Outdated`, while deleting the sidecar clears
the marker. Deleting a marked review clears only review provenance and retains the sidecar
and scan links.

EDL import accepts a bounded UTF-8 Simple EDL browser upload,
supports actions 1 (mute) and 3 (skip), preserves recognized `category: label` comments,
uses safe generic fallbacks, aggregates unsupported-action warnings, and creates one
completed imported scan plus one saved review atomically. The browser never parses EDL
content or treats an upload filename as a host path. The accepted limits are 1 MiB
content, 4,096 Unicode code points per line, and 10,000 action rows.

NSR-12 completed final integration. Scan findings remain immutable evidence; saved reviews
are the only editable selection/content authority and the only browser-driven EDL export
input. Legacy scan-level selection, finding mutation, preview, and export routes and web
helpers are absent.

The v1 persistence work used the approved fresh-schema development path without a
data-preserving migration. This records the schema policy and does not imply that an
installed/user database was migrated or wiped.

## Movie Content Filter core baseline (September 18, 2026)

This section supersedes EDL-only taxonomy, defaults, status, and command wording later in
this retrospective document. Simple EDL remains supported; MCF is an additional strict
format and the primary classification taxonomy.

- Every classified finding has a registry-valid `category` family and exact MCF
  `subcategory` token. `severity`, `mcf_channel`, and `mcf_comment` are independent of
  evidence source, EDL action, label, details, and confidence. New generated, manual, and
  MCF-imported findings require classification. Only unresolved legacy EDL findings may
  retain a null pair until edited in a review.
- Scan findings and imported `ScanMcfDocument` metadata are immutable evidence. A saved
  review owns isolated editable filter snapshots and a `ReviewMcfDocument` copy containing
  title, year, type, season, episode, source URL, IMDb URL, release, comment, and material
  bounds. Duplicate reviews receive independent copies.
- Filter defaults are ordered structured rules over canonical fields plus one
  severity default for every subcategory. The UI calls a `deny` rule Select and an
  `allow` rule Keep. Select (deny) wins. Creating snapshots fills missing
  severity/channel and evaluates selection. **Apply Filter Defaults** recomputes those
  draft values but never classification or source evidence; export never applies defaults.
- Review `revision` guards every mutation. `content_revision` audits content generally;
  `edl_content_revision` and `mcf_content_revision` advance only for fields rendered by
  that format. Membership, order, selection, timing, category, and subcategory affect both.
  EDL action/label affect EDL; severity/channel/cue comment/document metadata affect MCF.
  Name-only changes affect only the resource revision.
- Import accepts strict bounded MCF 1.0.0 and 1.1.0 by browser upload or absolute
  scanner-host `.mcf` path. One document atomically creates one completed imported scan,
  one source finding per payload line, one saved review, immutable source metadata, and
  isolated snapshots. MCF channel maps to legacy playback behavior without erasing channel:
  audio is mute, video is skip, and both/default is visual skip.
- The **Export** menu offers EDL and MCF. MCF preflight reads the persisted review and
  identifies incomplete document/filter fields without echoing content. Export emits
  deterministic UTF-8 MCF 1.1.0, groups only exactly equal ranges, and never uses EDL
  overlap normalization. EDL continues to emit Kodi/MPlayer Simple EDL from any
  EDL-capable saved review.
- `.edl` and `.mcf` may coexist beside one physical media version. `ExportStatus` always
  contains independent `edl` and `mcf` summaries with presence, time, attributed review,
  exported format revision, current/outdated state, and all contributing scan roles.
  Exporting or deleting one format never changes the other. Review deletion retains files
  and scan provenance while clearing attribution; any active format or saved review blocks
  deletion of its scans.
- Catalog, saved-review, and media-version surfaces show non-color-only
  `FORMAT · Current/Outdated` badges and format-specific provenance. Import, metadata,
  filter editing, preflight issues, export menus, and confirmed deletion are keyboard
  accessible, remain usable at 320 CSS pixels, and preserve dirty-draft guards.
- MCF parse/validation failures use safe line-aware review errors. Unsupported or malformed
  input is rejected atomically; export replacement and deletion preserve the previous file
  and database state on failure. Authentication, same-origin, and CSRF policy applies to
  every unsafe import, settings, review, export, and delete request.

The fresh v1 schema stores canonical MCF fields, document rows, structured defaults,
format-specific revisions, and format-keyed exports directly. There is no data-preserving
migration and no legacy EDL response/status/default-filter compatibility authority.

## MCF timing and credits detection baseline (September 20, 2026)

MCF synchronization maps portable source cues to the selected physical version without
changing their meaning. One shared affine endpoint mapping is authoritative for import and
later review adjustment. Imported scan document bounds and cue times remain immutable source
evidence; only the initial review snapshots are mapped to the user-selected target interval.
Mapping never clamps, merges, splits, reorders, classifies, selects, or changes actions.

- Every MCF import requires an editable target START, target END, and END mode. Before
  credits is the default and begins blank. File end prefills trusted physical duration when
  available but remains editable. The upload resolves runtime and
  media identity on the server and create the scan, review, timing state, snapshots, and any
  accepted credits attachment atomically.
- Saved reviews persist their synchronization interval separately from canonical MCF
  material bounds. In Before credits mode the synchronization END is the canonical material
  END. In File end mode they may differ, and MCF preflight remains blocked until a valid
  before-credits material END exists. EDL export continues to use selected review cue times.
- **Adjust Cue Times** is available on every saved review. It maps imported and otherwise
  unverified cues, including disabled ones, from the current synchronization interval to
  the proposed interval. Verified local scan cues and manual filters keep their times.
  Applying is non-persistent; repeated adjustment starts from the
  current draft and normal Save, Discard, dirty guards, and optimistic revision checks remain
  authoritative.
- **Credits** is available alongside Audio and Video whenever a scan can be queued; it does
  not require a saved review. The shared scan queue exclusively owns its status, progress,
  cancellation, and history; review and import surfaces cannot start or monitor detection.
  The detector combines chapter and tail visual/text evidence and returns its best candidate
  with method data and confidence. Completed candidates can be reviewed with a bounded
  protected preview clip. Low confidence is shown as a textual warning rather than hiding
  the result.
- Detection never changes a draft merely by completing. The user must accept or edit an
  exact candidate timestamp. Acceptance retains both detected and accepted boundaries,
  detector method, confidence, time, and source scan; it adds that scan and a selected review
  snapshot while leaving the immutable source finding unchanged.
- Accepted credits use `dispensable_scenes/dispensable`, video Skip, description and MCF
  comment `End Credits`, detector confidence, and the configured `dispensable` severity.
  The source finding starts at the detected boundary and ends at trusted physical file END;
  an edited accepted boundary changes only the review snapshot. MCF exports the canonical
  selected payload and EDL exports the selected Skip through physical file END.
- Qwen assessment is optional. A missing selected artifact cannot be acquired until an
  informed confirmation names the model, expected storage/download size, and platform
  capability. Declining continues with chapter and local visual/text evidence. No supported
  workflow depends on Qwen or claims real-film accuracy from the generated regression corpus.
- Import timing, Adjust Cue Times, detection consent/progress/clip/acceptance, provenance,
  scan history, and export blocking are keyboard and screen-reader accessible and remain
  contained at 320 CSS pixels. Authentication, same-origin, CSRF, cancellation, restart
  recovery, runtime-path ownership, and path-safe media delivery apply throughout.

Implemented baseline: [single-administrator local authentication](local-authentication-plan.md)
protects the web application and API. The linked plan and its task pack retain the
approved security rationale and release checklist; this specification describes the
product behavior that implementation now provides.

## 1. How to use this specification

The document distinguishes three kinds of statements:

- **Baseline:** implemented behavior that remains the intended design.
- **Corrective baseline:** a confirmed design decision tracked in section 18.
  Checked items are implemented requirements, not optional future ideas.
- **Constraint:** a boundary that subsequent changes must respect.

When adding a feature:

1. Identify the affected user journey, domain resources, APIs, states, and
   acceptance rules in this document.
2. Extend the relevant sections rather than creating a disconnected plan.
3. Call out intentional changes to an existing rule before implementation.
4. Add automated coverage proportional to the changed behavior.
5. Keep unresolved product questions explicit; do not silently choose between
   inconsistent implementations.

## 2. Product definition and scope

Editarr is a self-hosted media-review service. It discovers video files through
Jellyfin, or accepts an absolute path on the scanner host;
runs AI audio and visual analysis; lets a user review and edit the resulting
filters; and writes a Kodi/MPlayer Simple EDL sidecar beside the selected media
file.

The primary user is a person who manages their own media library and wants to
mute audio ranges or skip video ranges without modifying the source media.

### 2.1 In scope

- A server-owned connection to Jellyfin.
- A read-only archive of scans, reviews, and exports from the removed Radarr,
  Sonarr, and Other Files sources.
- Catalog browsing and media metadata supplied by Jellyfin.
- Safe remote-to-local path translation.
- Serial, restart-tolerant audio and video scan execution.
- Scan history, result review, manual filters, previews, and selection drafts.
- Reuse of suitable filters found for another copy of the same title.
- Per-file-version EDL export status, provenance, re-export, and deletion.
- The React web application and FastAPI service/API.
- One local administrator, mandatory first-run setup, forms sessions, and account
  recovery. Every client signs in individually; the former local-address and
  external-authentication bypasses were removed for multi-user support.
- Development and evaluation tools for the scanner models.

### 2.2 Out of scope

- Editing, transcoding, replacing, or otherwise mutating source video files.
- Changing metadata or monitored state in Jellyfin.
- Radarr and Sonarr. Editarr no longer connects to them; their former records
  are archived read-only.
- Scanner-host files outside Jellyfin, the former Other Files source. Its former
  records are archived read-only.
- Reading an EDL or MCF import from a scanner-host path. Imports are browser uploads.
- Download management.
- Additional user accounts, roles, invitations, self-registration, federated identities,
  and machine-client credentials. Editarr has exactly one local administrator.
- Data-preserving database migrations during v1 development. See section 17.

## 3. Product vocabulary

Use these terms consistently in UI copy, APIs, tests, and future plans.

| Term | Meaning |
| --- | --- |
| Source | Jellyfin, the only media source. |
| Archive | The read-only records of media from the removed Radarr, Sonarr, and Other Files sources. |
| Catalog item | A movie, series, season, episode, or generic video returned by a source. |
| Media item | Editarr's stable local identity for a catalog item. |
| Media version | One physical file/source entry for a media item. A Jellyfin item may have several. |
| Scan | One immutable execution identity and its saved options, status, progress, and findings. A scan can contain audio analysis, video analysis, or both. |
| Scan number | A monotonically increasing, never-reused number scoped to one media item. UI copy uses `Scan #N`. |
| Finding / filter | A detected or human-authored time range with an action: audio findings mute; video findings skip. The UI generally calls these filters during review. |
| Manual scan | The special completed scan that stores human-authored filters for one media item and version. It can contain audio and video filters together. |
| Linked result | A review-level composite connecting overlapping findings while preserving every constituent finding, category or word, scan, and editable identity. |
| Selection draft | Unsaved selected/unselected finding state for the scans currently included in review. |
| EDL export | The active sidecar for one media version plus the complete set of scans that contributed selected findings. |
| Queue | Ordered scans that are queued, scanning, cancelling, or orphaned. |
| History | Completed, failed, and cancelled scans. |

## 4. Design principles

### 4.1 Server-owned trust boundary

- Browser clients call Editarr only. They never call source services directly.
- A fresh Editarr data directory has no administrator. It exposes only first-run setup
  until the singleton administrator is created, then requires a session or API key for
  every web view and application API.
- Forms authentication uses server-side sessions in an `HttpOnly`, `SameSite=Lax` cookie.
  Session tokens are not stored in browser persistent storage. Unsafe forms requests
  require an exact configured-origin check and session CSRF header before any endpoint
  side effect runs.
- No client address or deployment mode replaces individual sign-in.
- Forwarding headers are untrusted by default. Only a direct peer in an explicitly
  configured trusted-proxy network may supply one validated forwarding-header family,
  which only keys failed-sign-in throttling; malformed or ambiguous metadata falls back
  to the direct peer.
- The Jellyfin API key is a write-only secret. Responses,
  browser state, logs, artwork URLs, and error messages must not expose them.
- Editarr proxies catalog data and artwork with its server-side credentials.
- Source paths are resolved and validated by the backend. Catalog scan forms do
  not accept a user-entered filesystem path.
- The data directory is service-owned and should be mode `0700` where the
  filesystem supports it.

### 4.2 Source-owned metadata, Editarr-owned review state

- Jellyfin remains authoritative for catalog identity,
  hierarchy, descriptive metadata, artwork, and current file information.
- Editarr owns scans, queue state, immutable source findings, saved-review memberships
  and editable filter snapshots, review revisions, and EDL-export state.
- A source outage must not erase locally retained scan history.

### 4.3 Operational clarity

- The interface uses a persistent source/settings navigation area, compact
  operational tables, clear state text, and detail pages that keep scan
  creation, history, review, and EDL actions together.
- State is never communicated by color alone.
- Destructive operations require explicit confirmation and report success or a
  safe actionable error.

### 4.4 Reproducible user ownership

- AI findings are suggestions. Saved review selection and human edits are
  user-owned state protected by optimistic revisions.
- Default filters are an explicit review-time action. They do not modify scan
  inputs or findings automatically.
- EDL output is derived only from saved selections, never an unsaved draft.

## 5. System architecture and ownership

### 5.1 Components

| Component | Responsibility |
| --- | --- |
| React web app | Hash-based navigation, source browsing, settings, queue/history panel, review drafts, previews, and EDL controls. |
| FastAPI service | Public Editarr API, source adapters, validation, artwork proxying, path resolution, queue coordination, review persistence, and EDL lifecycle. |
| SQLite/SQLAlchemy store | Settings, local media identities, scans, queue rows, findings, associations, progress/checkpoints, and export state. |
| Dedicated scan worker | Runs one reserved scan in a tokenized process, emits heartbeats/progress, persists checkpoints, and survives API reloads. |
| Isolated model workers | Release heavyweight visual/audio model memory between pipeline stages and allow process-tree cancellation. |

### 5.2 Stable domain relationships

- A local media identity is `(source server identity, source item ID)`.
- Former Other Files items keep server identity `editarr-manual` and are archived
  with `archived_source` `manual_files`.
- A media item owns zero or more scans. A scan belongs to one primary media
  item and can also be explicitly associated with other matching items.
- A scan owns zero or more findings and at most one active queue row.
- A completed Manual evidence scan is reused per media item and media version.
- A saved review belongs to one media item/version, owns ordered scan roles and private
  filter snapshots, and may reference source evidence without mutating it.
- Active EDL state is scoped to a media item **and media version**.
- Active EDL state retains every contributing scan ID, not only one primary
  scan ID.

### 5.3 Supported API direction

The supported API is item-first: clients identify a Jellyfin item or an Other
Files path, and the backend resolves the file.

The legacy path-first `/api/videos`, `/api/scan`, scan-queue, finding-review,
media, and EDL endpoints and their retired models/tables/code are removed.
They do not remain in OpenAPI as compatibility surfaces.

## 6. Information architecture and global shell

### 6.1 Navigation

The canonical desktop hierarchy is:

```text
Editarr
├── Sources
│   └── Jellyfin Libraries
│       └── <one entry per configured library>
├── Archive
└── Settings
    ├── Connections
    ├── Filters
    └── General
```

- Sources, Jellyfin Libraries, and Settings are accessible disclosure groups.
- Archive appears only for admins and the owner.
- The current route is visibly and semantically identified.
- When Jellyfin is configured, the root route opens the first library.
- Each Jellyfin library remembers its most recently visited route locally, so
  returning to it can restore the user's drill-down context.
- On narrow screens the sidebar becomes a top section; content and tables must
  remain usable without page-level horizontal overflow.

### 6.2 Scans floating panel

The floating Scans control is the **only canonical global Queue and History
interface**.

- It remains available over every application route.
- Its badge shows the queue count.
- Its progress ring represents the current scanning item.
- Opening it shows the ordered queue and recent history together.
- Queue rows show item, scan number, status, live progress, activity/stall
  indication, elapsed duration, options, and relevant actions.
- History rows show item, scan number, options, terminal status, finding count,
  and duration. The current implementation shows the 12 most recent rows in
  this compact surface.
- It closes with its close button, Escape, or an outside pointer action.
- It polls queue state every 1.5 seconds and history while expanded.

The standalone Queue and History routes, components, and route-specific tests
are removed. The floating panel supersedes them; their former deep links are
not supported.

## 7. Settings

### 7.1 Connections

Connections contains the Jellyfin card.

The card must provide:

- Server address and password-style API-key fields.
- A configured indicator without revealing the saved key.
- Test Connection, which validates proposed values without persisting them.
- Save, which tests and normalizes a connection before persisting it.
- A blank key meaning “retain the saved key” during ordinary edits.
- Safe feedback for invalid URL, rejected key, timeout, unavailable server,
  malformed response, save success, and disconnect success.
- A confirmed Disconnect/Clear action that removes the stored key and makes
  the source unavailable without revealing the old value. It also invalidates
  relevant catalog/artwork caches.

Server addresses accept only HTTP(S), reject embedded credentials, query
strings, and fragments, and normalize trailing slashes.

### 7.2 Filter defaults

Settings stores separate ordered Allow and Deny lists for Video and Audio.
Empty rows are discarded; meaningful entries are trimmed and retained.

The purpose of these lists is **review-time selection**, not scan-time
detection:

- They do not change scan options.
- They do not change which models run.
- They do not generate, suppress, select, or deselect findings when a scan is
  created, queued, or executed.
- Changing defaults does not mutate any scan or saved selection.
- In the current review, `Apply Filter Defaults` re-evaluates all findings from every
  currently selected scan and updates only the current selection draft.
- Applying defaults does not save automatically. The result remains subject to
  Save Changes or Discard.
- Allow means a matching finding is deselected; Deny means a matching finding
  is selected. Deny wins when the same normalized value is in both lists.
- Audio matching is case-insensitive and punctuation-insensitive against the
  finding's detected word/root.
- Video matching is case-insensitive against every category attached to the
  finding or linked result.
- A linked result retains constituent selection state. Its group checkbox must
  support selected, unselected, and mixed states; toggling it applies one state
  to all constituents.

**Corrective baseline implemented:**

- Replace the outdated “applies only to new scans” and evaluation-order copy.
- Place Apply Filter Defaults in the Review results toolbar with the result sort and
  visibility controls.
- Remove worker-time application of Audio Allow/Deny defaults, including the
  behavior where Deny words add detections independently of the chosen audio
  filter.
- Implement equivalent Audio and Video review-time evaluation.

### 7.3 General: base path mappings

- A mapping translates an absolute remote source prefix to an absolute path on
  the scanner host.
- Users can add, edit, reorder, delete with confirmation, undo a deletion, and
  test a remote path.
- Resolution uses a component-safe prefix, never substring replacement.
- The longest matching remote prefix wins; configured order resolves ties.
- If no mapping matches, Editarr uses the normalized source path unchanged.
- POSIX and Windows-style remote paths are supported.
- Mapping resolution is shared by scanning, previews, candidate matching, EDL
  export, and EDL deletion.
- A resolution preview reports the translated path but does not claim the file
  is readable unless readability was actually checked.

## 8. Source catalogs

### 8.1 Shared catalog behavior

- Catalogs have explicit loading, empty, disconnected, upstream-error, retry,
  and artwork-fallback states.
- Browser clients receive normalized metadata and Editarr-owned EDL state.
- Artwork is proxied through Editarr and may be cached without exposing source
  credentials.
- Catalog cards have consistent poster geometry and keyboard-operable item
  navigation.

### 8.2 Jellyfin libraries

- Every visible Jellyfin virtual library appears in navigation.
- A library root shows Series items when the source represents television;
  otherwise it shows directly video-backed Movie, Video, or MusicVideo items
  and excludes organizational containers.
- The backend loads all relevant Jellyfin pages, normalizes them, and applies
  Editarr filtering/sorting before returning a page of 100.
- Catalog reads are cached until explicit Refresh Library or connection-cache
  invalidation.

The sticky toolbar provides:

- Refresh Library.
- Icon/List view.
- Sort by title, year, rating, length, or type, ascending or descending.
- EDL state filter: all, exported, or not exported.
- Rating and resolution filters derived from the current catalog.
- Debounced, case- and diacritic-insensitive title search.
- Clear Filters when any search/filter is active.
- Per-library persistence of view, sort, direction, and filters.

Icon view uses a fixed-width responsive poster grid with title, year/type, and
an EDL badge. List view shows title, year, resolution, length, rating, type,
and EDL state. Paging reports the visible range and total.

### 8.3 Jellyfin series hierarchy

- Opening a series lists seasons.
- Season view supports Icon/List, adjustable item size, and title/number sort.
- Opening a season lists Episode or Video children in episode-number order.
- Opening an episode uses the shared media-detail and review experience.
- Breadcrumbs support deep-link navigation through library, series, season,
  and item.
- Empty seasons/episodes and missing metadata have explicit fallbacks.

### 8.4 Archive

- Archive lists the media items of the removed Radarr, Sonarr, and Other Files sources,
  newest first, 20 per page, with their source, title, and, for an episode,
  its series and `Sxx Eyy` when recorded data or a standard file name gives
  them.
- Each item lists its scans, saved reviews with their owner, and exports.
- It is read-only. It offers no scan, edit, export, or delete action, and no
  other route returns an archived item.
- Only admins and the owner may open it. The path appears only with
  `media.paths.view`.

## 9. Media detail and file versions

### 9.1 Summary

Directly scan-capable items use one shared detail layout:

- Source breadcrumbs.
- Poster or deterministic fallback.
- Item type, title, year, rating, overview, resolution, format, length, and file
  count with missing-data fallbacks.
- Active EDL state.
- A Files table where versions exist.
- New Scan, Find Filters, and Delete EDL actions; saved-review creation and EDL import
  are grouped with the Saved Reviews table, and manual filters are staged inside the
  current review draft.
- Scans selector, Saved Reviews table, and current Review results below the summary.

### 9.2 Media versions

- The Files table shows scene/source name, release group, edition, resolution,
  format, length, size, and per-version EDL status.
- New Scan requires a valid current source ID when versions are available.
- Saved review management has its own version selector. Only scans of the selected physical
  version can contribute together.
- Switching versions guards and discards an unsaved draft only after user
  confirmation.
- Delete EDL acts only on the version currently selected for review. It does
  not remove other versions' sidecars or export records.

### 9.3 Finding reusable filters

Find Filters helps reuse completed scans from another likely copy of the same
title.

- Exact same resolved-file scans are included automatically in local history.
- Cross-file candidate discovery requires a common external provider identity.
- Candidate reasons, in practical priority order, are scene name plus release
  group, filename plus duration, format plus duration, and quick hash plus
  duration.
- Search does not associate anything automatically.
- A confirmation dialog lets the user select candidates and identifies already
  associated scans.
- Association preserves the scan's original identity and provenance; it does
  not clone findings.

## 10. Scan creation and execution

### 10.1 Scan analysis model

A scan can contain:

- Audio only.
- Video only.
- Both audio and video.

The current catalog New Scan form offers Audio or Video as an
exclusive convenience. That UI does **not** constrain the domain or API:
manual scans already combine types, and subsequent scenarios may create a
single dual-analysis scan.

At least one analysis type is required.

### 10.2 Advertised capabilities

`GET /api/capabilities` retains the legacy `audio_models`, `audio_filters`, and
`video_models` choice lists for compatibility. It also returns `target_id` and a
complete `model_capabilities` list. Each detailed record has `id`, `display_name`,
`stage` (`audio`, `detector`, or `enrichment`), `availability`, nullable `reason`,
`device`, `weight_requirement`, `download`, and `enrichment_behavior`. `download`
is a path-free state object (`state`, nullable byte progress and error fields); its
manifest-backed transitions are managed by model acquisition. Capability reads use
module specifications plus conservative, cached runtime probes; they never load a model,
write a cache, or download weights. Windows/Linux CTranslate2 may be imported only for a
bounded device/compute-runtime probe. The runtime reports CPU unless an accelerator has
been independently verified, so package stubs and bundled DLL names cannot create false
MPS, Core ML, or CUDA claims.

Initial target IDs are `macos-arm64`, `windows-x64`, and `linux-x86_64`. Windows and
Linux guarantee CPU detector/audio backends; faster-whisper reports CUDA only when its
packaged CTranslate2 runtime verifies a compatible host NVIDIA device/driver. They expose
an unavailable `qwen3_vl` enrichment record with `reason: deferred_gap` and
`enrichment_behavior: detector_findings_only`.
Flagged detector findings therefore complete unchanged on those platforms. macOS arm64
advertises MLX Qwen only when its packaged dependencies are available; a missing MLX
dependency rejects visual scan admission before detector work starts. Unsupported OS or
architecture, missing package/provider, and deferred gaps remain explicit reasons.

The backend advertises only selectable models available on the current host in its
legacy choice lists.

### 10.3 Model storage management

Authenticated users can read `GET /api/models`, start `POST /api/models/prefetch`,
cancel `POST /api/models/{artifact_id}/cancel`, and remove `DELETE /api/models/{artifact_id}`.
Unsafe routes use the existing same-origin, CSRF, and session policy. Responses are
path-free and expose only manifest-backed IDs, sizes, source/license metadata, and
stable acquisition codes such as `offline_missing`, `insufficient_space`,
`integrity_mismatch`, `cancelled`, `platform_unavailable`, `acquisition_active`, and
`model_in_use`. Removal is rejected while an artifact is downloading or an active scan
uses its backend. The Model Storage settings section polls only during an active
download and marks `license-review-required` artifacts as release-review blockers.

Audio transcribers:

- Whisper via faster-whisper (`whisper`).
- Whisper via mlx-whisper (`mlx_whisper`) when the optional macOS dependency is
  installed.

Audio profanity filters:

- better-profanity.
- glin-profanity.
- alt-profanity-check.
- Qwen (multilingual), when the Qwen runtime is available.

better-profanity remains the default. The explicit `qwen` filter analyzes the
whole transcript without a dictionary detector; selecting a Qwen model alone
does not enable it. Other filters use Qwen only to categorize their detections.

Video models:

- NudeNet.
- Image Safety Gate.
- SigLIP2 Moderation — NSFW.
- SigLIP2 Moderation — Violence.

Unknown or unavailable model IDs are rejected server-side even if a client
submits them directly.

### 10.3 Video options

- Fine analysis examines every playable source frame in each scene.
- Optional coarse scanning samples at a positive interval and limits fine
  analysis to flagged scenes; when coarse sampling produces no frames, the
  safe fallback is fine analysis rather than silently skipping the video.
- When available, Qwen receives a configurable positive number of representative
  flagged frames per joined segment. On the declared Windows/Linux Qwen gap, the
  detector findings are retained without an invented summary or replay capture.
- Flagged segments separated by at most the configured non-negative clean gap
  may be joined before Qwen summarization.
- Scene boundaries and extracted media intermediates are cached by video hash
  where safe.
- Attached cover-art streams are not treated as playable video.

### 10.4 Audio behavior

- Audio is extracted once per video hash and reused.
- Transcripts are cached by video hash and transcriber model.
- Long transcription is resumable in overlapping chunks.
- Detected word boundaries remain the raw saved filter range; configurable
  padding affects detection/export behavior only where explicitly implemented.
- Settings Allow/Deny lists are not passed into audio detection. They are
  applied only on explicit request in the current review.

### 10.5 Visual “clean” summaries

Qwen can preserve a result stating that a flagged segment contains “No
objectionable content.” It remains reviewable for diagnostics, but:

- It is persisted unselected at finding creation.
- Hiding it in review must not itself create an unsaved draft.
- The user can reveal and select it explicitly.

### 10.6 Queue and worker lifecycle

Scans execute serially. Queue order is explicit and mutable for work that has
not completed.

```text
queued ──> scanning ──> complete
   │           │  └──> error (resumable when checkpoints exist)
   │           ├─────> cancelling ──> cancelled
   │           └─────> orphaned ──> queued/resumed
   └─────────────────> cancelled
```

- Each scan runs in a dedicated tokenized process with persisted PID, process
  group, heartbeat, start time, and checkpoint state.
- API reload/shutdown does not intentionally terminate the dedicated worker.
- Startup adopts a worker only after verifying its tokenized command; PID reuse
  must never target an unrelated process.
- A missing/unverified live worker becomes orphaned and can be resumed from
  completed visual/audio checkpoints.
- Cancellation is cooperative first and may terminate only a verified worker
  process group when required.
- The scheduler reserves at most one scan and does not launch duplicate work.
- On macOS, the worker prevents idle system sleep while a scan runs.

### 10.7 Progress

- Progress includes percentage, stage, message, timestamps, and elapsed scan
  duration.
- Weighted stages are setup, visual, audio extraction, audio scan, cleanup, and
  finalize, omitting disabled analyses from the percentage denominator.
- The UI treats a scanning heartbeat older than five seconds as stalled while
  preserving the persisted scanning state.

## 11. Scan history and review

### 11.1 Scan identity and selector

- Scan numbers increment within a media item and are never reused after
  deletion.
- Queue entries and retained history appear together on media detail.
- The selector is bounded to approximately four rows and remains keyboard and
  pointer scrollable.
- Each row shows review inclusion, manual/edited state, status, options, source
  version, duration, match reason where applicable, and Delete.
- A scan name and its origin/status form the row title. A compact pencil icon sits
  immediately beside that title; there is no separate text `Rename` action. Activating
  the pencil opens an inline validated name editor with Save and Cancel, and focus returns
  to the pencil when editing closes.
- Delete is a compact destructive text button right-justified at the end of the row.
  Its confirmation identifies active-EDL and saved-review dependencies before removal.
- Only completed scans with an analysis type can be selected for review.
- Opening a version loads its greatest-`updated_at` saved review (ID ascending breaks a
  tie), or starts an empty unsaved review when none exists. One dual-analysis scan can
  fill both membership roles.
- Multiple Audio and multiple Video scans may be selected simultaneously.

### 11.2 Findings

Every source finding preserves:

- Owning scan and stable finding ID.
- Audio or visual source.
- Category, label/word, description/context, and grouping metadata.
- Start/end seconds, confidence, initial selected state, and edit action. Once created,
  this source evidence is read-only through the public API.
- Audio action `mute`; video action `skip`.

Audio words are censored in every ordinary, read-only review display by replacing
each ASCII vowel (`a`, `e`, `i`, `o`, or `u`, case-insensitively) with `*`. This
includes both audio group headings and each audio value in a Description column;
new or rewritten review renderers must preserve this presentation rule. Censoring
is display-only: stored labels/details and editable form fields retain the original
text. Keyboard focus reveals contextual text immediately, while pointer hover must
remain deliberate for two seconds before revealing the same tooltip. Video labels
and descriptions remain plainly readable. Frontend regression coverage must assert
the masked audio Description value and its focus-triggered context reveal.

Audio root normalization must not invent non-word leaf labels. In particular, a
singular word ending in `-er`, such as `murder`, remains complete; only established
inflection handling may shorten a displayed root.

### 11.3 Linked overlapping results

Overlapping findings are connected as a linked result for review without
destroying their individual records.

- The composite time range is the union of its constituents.
- A flagged video result can expose multiple linked categories.
- Every category/word, description, contributing scan number, and finding provenance is
  retained. The review-local snapshots remain individually editable/deletable.
- The UI identifies the relationship as linked instead of presenting unrelated
  duplicate time rows.
- Category/root-word group views may reference the same linked result, but
  should make the shared identity clear.
- A composite selection control supports selected, unselected, and mixed
  constituent state; a direct user toggle selects or deselects all linked
  constituents.
- Applying defaults evaluates constituents and can therefore produce a mixed
  linked result.
- Linkage is review/presentation metadata. EDL normalization independently
  computes the final playback ranges from selected constituent findings.

### 11.4 Review presentation

- Review combines all findings from the selected Audio and Video scans for one
  version.
- It reports selected/total linked results, normalized skipped time, selected
  scan count, and a proportional timeline.
- Audio and Video each use an MCF family, subcategory, and finding-group hierarchy. Family
  and subcategory sections remain alphabetical. Description sorts finding-group headers
  alphabetically, Time sorts them by each group's first row, and Severity leaves them
  alphabetical. Families start expanded; subcategories and finding groups start collapsed.
  Legacy findings use `Unclassified` for both missing levels.
- Time, Description, and Severity sort rows within each finding group. Audio and Video
  retain independent sort key/direction state and default to Time ascending.
- “No objectionable content” results are hidden by default with an explicit
  show/hide control.
- Each result identifies all contributing scan numbers.

The Saved Reviews management surface is part of the review card:

- `New Review` and `Import EDL` appear together directly above the Saved Reviews table.
  New Review opens a naming dialog; it does not expose a permanent creation-name field.
- The table is always present, including an explicit empty row. Its columns are Name,
  Modified, and EDL. The selected review row is visibly distinct, and the review name is
  the control that opens that review.
- Name and Modified are sortable column headers. Clicking an inactive header selects it
  in ascending order; clicking the active header reverses direction. The active header
  displays `▲` for ascending or `▼` for descending and exposes the equivalent semantic
  `aria-sort` state. There is no separate sort dropdown or direction button. Initial
  ordering is Modified descending.
- Below the table separator, the current review name appears as a left-justified title,
  not a text field. A compact pencil icon immediately beside it opens an inline validated
  name editor with Save and Cancel. Duplicate and Delete Review are right-justified on
  the same title row; Duplicate opens its own naming dialog and Delete remains confirmed.
  On narrow screens the title and right-side actions may stack without page overflow.
- The lower review action row contains Save Review or Save Changes, Discard, and Export
  EDL as applicable. Duplicate, Delete Review, and rename do not also appear there.

The Video and Audio result surfaces use tiered accordion tables:

- Each table fills its parent without forcing page-level horizontal scrolling. Use and
  Time remain compact; Description absorbs available space; Severity, Channel, Scan, and
  the unlabeled utility/player column retain usable space. At narrow widths the table uses
  a labelled, keyboard-focusable internal horizontal scroller without page-level overflow.
- Visible data columns are Use, Time, Description, Severity, Channel, and Scan. The final
  utility column has no visible heading. There is no Action column because action is
  source-defined: audio filters mute and video filters skip.
- Every family, subcategory, and finding-group header has a tri-state Use checkbox. It is
  checked when all unique filters represented below that header are selected, mixed when
  only some are selected, and unchecked when none are selected. Checkbox operation does
  not toggle the adjacent disclosure.
- Hierarchy headers contain only the section name plus result and selected counts. They do
  not repeat classification, severity, channel, or comment summaries. Family sections
  start open while lower levels start closed; every result passes through all three levels.
- Time, Description, and Severity headers use an adjacent `▲`/`▼` and `aria-sort`. Clicking
  an inactive header selects ascending order; clicking the active header reverses it. Rows
  sort within each finding group. Description also orders leaf headers alphabetically and
  Time orders them by the first sorted row in each leaf; neither changes family or
  subcategory order. Severity ascending is missing, Low, Medium, High.
- Every result row retains its own selected/unselected/mixed checkbox. Preview, Edit, and
  Delete are right-justified in the dedicated utility column for each underlying filter,
  including every constituent of a merged result.
- A merged result spanning multiple classification paths repeats under every applicable
  path with a visible and screen-reader-labelled `🔗` after each description. Every
  repeated instance controls the same underlying filter IDs, so selection stays synchronized.
- Edit opens one inline filter editor directly beneath its result. Saving review changes
  persists edits and deletions to the review snapshot only; source findings remain
  immutable.

The timeline uses normalized playback ranges, not overlapping raw bars:

- Adjacent/overlapping skip ranges are one skip span.
- Adjacent/overlapping mute ranges are one mute span.
- Wherever skip and mute overlap, skip wins. The overlapping mute portion is
  omitted because skipped media is never played.
- Selected and unselected styling must remain distinguishable without relying
  only on color.

### 11.5 Draft selection lifecycle

- Loading a saved review starts from its persisted private filter snapshots; a new
  membership snapshots the applicable immutable source findings once.
- Checkbox changes, linked-result toggles, and Apply Filter Defaults affect a
  local draft only.
- Save Changes replaces the complete saved-review aggregate under its current resource
  revision; content changes increment both resource and content revisions once.
- Discard restores the saved-review baseline. Same-session remove/re-add may
  restore cached draft snapshots, while re-adding after a saved removal takes a fresh
  source snapshot.
- Export is blocked while the draft is dirty with the established unsaved
  changes explanation.
- Preview is also blocked while the draft is dirty. Name-only editing is entered
  explicitly through the current-title pencil; ordinary display does not expose an
  editable name field.
- Changing scans, versions, files, or leaving detail guards against losing a
  dirty draft.
- A stale revision returns a conflict and requires reload; it never overwrites
  another client's work silently.

### 11.6 Manual filters and edits

- Add Filter stages review-local input. Saving atomically creates or reuses the special
  Manual evidence scan for the current media item/version, adds immutable source evidence,
  and assigns real provenance; no AI scan is required and a failed save leaks no rows.
- Manual audio filters require a word and become mute ranges.
- Manual video filters require a free-form category and become skip ranges.
- Time fields accept `M:SS` or `H:MM:SS`, including fractional seconds.
- End must follow start and cannot exceed known media duration.
- Editing, selecting, or deleting a filter changes only the current review draft and is
  persisted only through full saved-review save. It never changes the source finding or
  another review.

### 11.7 Previews

- Audio mute preview returns browser-playable MP3 for the exact saved range,
  padded symmetrically to at least one second when needed.
- Video skip preview returns fast-start H.264/AAC MP4 for the exact saved range.
- Preview rendering seeks directly to the short range rather than decoding the
  entire feature.
- Preview is available only for completed contributing scans and reports safe
  per-result errors.
- Preview is initiated from the result row's dedicated utility column. It uses the exact
  review-local filter identity and range; there is no standalone Preview Filter selector.
- A successful audio preview renders a playable native audio control inline beneath that
  filter's buttons. A successful video preview renders a playable native video control in
  the same location. The media object remains constrained to the utility cell and cannot
  widen the table or create horizontal page overflow.
- Replacing a preview or changing the draft removes the stale player and releases its
  browser object URL.

## 12. EDL lifecycle

### 12.1 Export eligibility

- The saved review must contain at least one Audio, Video, or Both scan membership.
- Every contributing scan must be complete, belong or be associated with the
  same media item, and resolve to the same media version.
- Only selected saved-review snapshots contribute. Scan-owned selected flags do not.
  A dirty draft blocks export.
- Empty selected output is valid and produces the Editarr header only.

### 12.2 Output format and normalization

- Destination is the source file path with its extension replaced by `.edl`.
- Content begins with `## Editarr Simple EDL`.
- Kodi/MPlayer Simple EDL action `3` is skip; action `1` is mute.
- Negative starts clamp to zero; a missing/invalid end receives the established
  safe one-second fallback where applicable.
- Adjacent/overlapping same-action ranges merge.
- Skip ranges take precedence over mute ranges. Mute portions covered by a skip
  are removed, including in the timeline calculation.
- Each output row is preceded by one safe `## category: label` comment per contributing
  review filter, preserving original review-filter order through range normalization.
- Output is written to a temporary file in the destination directory, flushed
  and fsynced, then atomically replaced. A failed write leaves the previous EDL
  and database state intact and removes temporary debris.

### 12.3 Export state and provenance

- Active state is per media item and media version.
- A successful export records a UTC timestamp, saved-review ID/content revision, and
  **all contributing scan IDs** with their Audio/Video roles.
- Detail, Files, and review UI identify every contributing scan;
  they must not reduce a combined export to a misleading single scan number.
- Re-export atomically replaces the sidecar without a rename-away gap and then replaces
  its complete provenance. Database failure restores the previous sidecar; if restoration
  itself fails, the known-good recovery backup is retained rather than deleted.
- A scan that contributes to an active EDL cannot be deleted until that
  version's EDL is deleted or replaced without it.
- A scan referenced by a saved review cannot be deleted until those references are
  removed. The confirmation lists every referencing review with modified time and stable
  ID, plus the active EDL dependency when present.

### 12.4 Delete EDL

- Delete EDL requires confirmation naming the selected version/file and active
  provenance.
- It removes only the selected version's sidecar and export record.
- Other versions of the item remain untouched.
- Missing export state returns a clear not-found result; filesystem failure does
  not clear database state.

### 12.5 Import EDL

- Import EDL is available beside New Review immediately above the Saved Reviews table for
  Jellyfin targets.
- The dialog requires a review name and a bounded browser upload; it offers no
  scanner-host path. It owns keyboard focus while open and restores focus to its trigger
  when closed.
- A successful import creates and opens the returned saved review, adds its completed
  imported scan to history, refreshes affected scan/review state, and reports aggregate
  warnings without silently dropping supported ranges.
- Import is disabled while the current review draft is dirty; it never overwrites that
  unsaved draft.

## 13. Error, empty, and conflict behavior

APIs use safe human-readable detail without secrets, raw upstream bodies, rejected request
values, or scanner-host paths. Review/import domain failures use the structured
`ReviewError` detail (`code`, `message`, optional line number, optional scan references),
and live OpenAPI declares that schema for each applicable status. Request validation keeps
its 422 issue list but returns only type, location, and message.

| Status | Meaning |
| --- | --- |
| 400 | An EDL path is not absolute/readable or has the wrong extension; the response never echoes it. |
| 404 | Requested local/source resource, review, or review filter does not exist. |
| 409 | Operational conflict: stale review revision, duplicate/invalid membership or provenance, invalid review filter, unavailable media, failed review export, invalid reorder, or referenced scan. |
| 413 | EDL content, line length, or action-row limit is exceeded. |
| 422 | Invalid EDL UTF-8/syntax/range, no supported EDL actions, or path-safe request validation. |
| 500 | Local operation failed after valid input, such as a sidecar deletion failure. |
| 502 | Configured Jellyfin request failed safely. |

Every user-facing data surface must provide the applicable loading, empty,
disconnected, unauthorized-source, unavailable-file, retryable upstream error,
validation error, conflict, and success state. Failed refreshes must not
silently present stale data as newly loaded.

## 14. Accessibility and responsive behavior

All subsequent work inherits these acceptance requirements:

- Keyboard-only operation for navigation, disclosures, tables, scrolling
  regions, dialogs, grouped results, previews, and destructive actions.
- Visible `:focus-visible` treatment.
- Semantic landmarks, headings, tables, lists, progress elements, labels,
  `aria-current`, disclosure state, selected/pressed state, and live status or
  alert announcements.
- Sortable table headers expose `aria-sort`; icon-only pencil controls retain explicit
  accessible names and tooltips; tri-state category and linked-result checkboxes expose
  mixed state programmatically.
- Modal dialogs move focus inside, trap Tab/Shift+Tab, close with Escape when
  safe, and restore previous focus.
- Dirty-draft and destructive confirmations are operable without a pointer.
- State and timeline actions are not color-only.
- The layout supports a 320px minimum viewport. Toolbars wrap, general-purpose wide tables
  use contained horizontal scrolling, and the saved-review results table instead shrinks
  and wraps its columns within its parent. The sidebar becomes a top section at the mobile
  breakpoint, and the floating panel remains within the viewport.
- Primary views should pass automated axe checks; automated results complement
  rather than replace manual keyboard and screen-reader review.

## 15. Supported API feature map

This is a behavioral map, not a replacement for live OpenAPI.

| Feature | Supported endpoints |
| --- | --- |
| Connections | `GET/PUT /api/settings/jellyfin`, `POST .../test`. |
| Default filters | `GET/PUT /api/settings/filters`; **required:** review-time apply operation may remain client-side or gain a dedicated API, but must use one shared evaluator. |
| Path mappings | CRUD, reorder, and resolve under `/api/settings/path-mappings`. |
| Jellyfin catalogs | `/api/libraries`, library items, item detail, children, seasons, and proxied image. |
| Archive | `GET /api/archive`, paged, read-only. |
| Scan creation | `POST /api/scans`. |
| Queue/history | `/api/scans/queue`, reorder, cancel/remove, orphan resume, and `/api/scans/history`; consumed by the floating panel. |
| Review | Source-aware saved-review list/create; review get/save/rename/duplicate/delete; review-filter audio/video preview; immutable scan-finding reads for new snapshots. |
| Manual filters | `new_manual_filters` on saved-review create/save for Jellyfin targets. |
| Filter reuse | Candidate search and explicit scan-to-item association. |
| EDL | Saved-review export and upload import plus per-version catalog delete and provenance reads. |
| Capabilities | `GET /api/capabilities`. |
| Model storage | `GET /api/models`, `POST /api/models/prefetch`, `POST /api/models/{artifact_id}/cancel`, and `DELETE /api/models/{artifact_id}`. |

### 15.1 Deployment, serving, and restart

- A normal packaged `editarr` launch serves the React UI and FastAPI application from
  one configurable upstream listener. One frontend artifact works at root or the one
  prefix derived from the effective Base URL; it is not rebuilt per deployment.
- With no saved Base URL or `EDITARR_BASE_URL` seed, the permanent normal deployment is
  `http://localhost:<listening-port>/` on the loopback bind, defaulting to port 3348.
  This local HTTP mode has ordinary forms authentication and automatic non-Secure
  localhost cookies; it is not a temporary setup state.
- A Base URL is an optional full HTTP(S) public address. Its path derives the prefix;
  saved values win over the environment seed and an explicit root URL is a saved choice.
  The public URL port is distinct from the upstream listening port. The removed
  `EDITARR_AUTH_PUBLIC_ORIGIN` does not supply a compatibility route.
- The application accepts only prefix-preserving proxies. A public request at
  `/editarr/api/scans` reaches the upstream at the same path. It does not infer a
  mount from proxy headers or support prefix stripping. TLS terminates at the external
  proxy; the service continues to apply its own narrow trusted-peer and forwarding
  validation policy.
- Only the entry shell, built assets, and enumerated icons are public. Application APIs,
  docs/OpenAPI, artwork, previews, media, and unknown routes remain setup/auth gated.
  The entry carries a public inert runtime bootstrap containing only the configured base
  and API paths; it contains no secrets or upstream listener data.
- Deployment settings write explicit Base URL/Port overrides to a small atomic
  `deployment.json` file under the data directory. Save produces pending next-start
  state; it never hot-applies routes, cookies, origin checks, or the listener. General
  Settings shows active versus next-start state and keeps Base URL input separate from
  a derived local address.
- **Restart Editarr** is at the top right of General Settings. It applies only saved
  values, never dirty drafts, through the standalone parent-controller launch. It
  acknowledges before child shutdown, preserves independent scan workers, and reports
  bind recovery/failure rather than a false success. Direct Uvicorn and reload/Vite
  development modes report restart as unsupported.
- An applied configured Base URL origin/prefix transition requires forms reauthentication
  through a persistent monotonic cutoff and cookie-path cleanup. A port-only transition,
  including implicit localhost port changes, retains sessions. Docker/Compose and native
  Windows/macOS/Linux executable delivery remain future requirements, not implemented
  packaging choices.

## 16. Scanner development tools

These are supported developer workflows rather than end-user web features.

### 16.1 Qwen prompt replay

- Each flagged scene can retain representative frames and detector metadata in
  `data/qwen-replays/<scan-id>/scene_*`.
- `editarr-replay-qwen` reruns the current or supplied prompt without frame
  extraction or safety-model execution and prints findings as JSON.

### 16.2 Video model evaluation

- A manifest pairs frames with expected categories: nudity, violence,
  profanity, other, or clean.
- `editarr-evaluate-video-model` runs warmups and repeated timed evaluation and
  reports frame predictions, overall/per-category precision, recall, F1,
  throughput, latency distribution, and peak process memory.
- `editarr-add-video-model-corpus-range` samples a reviewed video time range and
  appends labeled frames to the corpus manifest.
- Evaluation corpora need reviewed clean frames as well as positives to measure
  false positives.

### 16.3 MCF subcategorization

- AI findings first receive their producer's deterministic MCF fallback. Exact NudeNet
  buttocks and genitalia labels may select the corresponding canonical subcategory.
- Qwen receives only the valid subcategory tokens for detector families present in a segment
  and must return the versioned JSON schema. Malformed, incomplete, duplicate, unknown, or
  out-of-family output fails closed to the producer fallback.
- Visual Qwen-specific categories also require a measured acceptance threshold. No threshold
  is enabled until a representative reviewed corpus supports it, so visual Qwen output
  enriches descriptions and selection but does not make classifications more specific.
- `editarr-evaluate-subcategorization` reports exact-token precision/recall/F1, fallback and
  invalid-output rates, latency, and family-preservation regressions for a reviewed response
  corpus.
- Audio findings start at `language/swearing` with `medium` severity. On hosts with Qwen
  available, a separate versioned text-only classifier uses each flagged word and its
  transcript context to attempt one supported MCF Language token and a severity. It requests
  `low` for flagged words that are not generally vulgar or swearing, `high` for extremely
  offensive or crass expressions (including irreverent references to named deities), and
  `medium` otherwise. Both fields are accepted together only from complete, valid output;
  malformed, incomplete, out-of-family, or unavailable model output retains the detector
  category and severity. This is an uncalibrated categorization attempt and does not claim
  reviewed-corpus accuracy.
- Subcategorization affects newly generated findings only. It does not rewrite imports, manual
  classifications, saved-review edits, replay manifests, or immutable old scans.

### 16.3 Configurable performance controls

- Extracted visual frames default to a 512px maximum height.
- Visual batch size defaults to eight for models that support batching.
- Detection thresholds, scene threshold, audio model, profanity padding, and
  model-specific safety thresholds are deployment settings under the
  `EDITARR_` environment namespace.

## 17. Persistence and v1 database policy

- Newly created databases use the current SQLAlchemy schema directly.
- Do not add or maintain data-preserving migrations solely for development
  data during v1.
- Before implementing a schema change that would ordinarily require a
  migration—especially multi-scan EDL provenance or removal of legacy
  tables—ask the user to confirm skipping the migration and wiping the
  development database.
- Never wipe the database without that explicit confirmation.
- This policy does not make schema changes optional; it defines how v1 local
  development adopts them.

## 18. Required corrective baseline

These confirmed corrections were completed before new features build on the
affected behavior. Ordering reflects dependency, not a release promise.

### 18.1 Domain and API cleanup

- [x] Remove public legacy path-first APIs, schemas, repositories, queue code,
  tables, OpenAPI entries, and web client helpers.
- [x] Remove standalone Queue/History routes, pages, and route-specific tests;
  retain and expand floating-panel coverage.

### 18.2 Review-time defaults

- [x] Rewrite Settings copy around explicit review-time application.
- [x] Remove Audio defaults from scan detection/execution.
- [x] Implement one shared normalized evaluator for Audio and Video defaults.
- [x] Add Apply Filter Defaults to the Review results toolbar.
- [x] Apply to every selected scan's draft without auto-saving; support linked
  mixed selection and Deny precedence.
- [x] Persist “No objectionable content” visual findings unselected so hiding
  them does not create a draft mutation.

### 18.3 Linked normalization

- [x] Represent and present overlapping findings as linked composites while
  retaining constituent categories/words, identities, edits, selection, and
  provenance.
- [x] Render normalized timeline spans with skip-over-mute precedence.
- [x] Keep EDL normalization and skipped-time calculations consistent with the
  timeline for adjacent and overlapping ranges.

### 18.4 EDL versioning and provenance

- [x] Store and return every scan contributing to an active export.
- [x] Show complete provenance wherever exported state is displayed.
- [x] Scope Delete EDL to the currently selected version.
- [x] Add EDL state and confirmed deletion to Other Files.
- [x] Prevent deletion of any scan still referenced by active export
  provenance.

### 18.5 Connection lifecycle

- [x] Add confirmed Disconnect/Clear actions to Jellyfin, Radarr, and Sonarr.
- [x] Clear relevant caches and update disconnected/empty states immediately.

## 19. Acceptance baseline for subsequent features

A change is not complete merely because its happy-path UI renders. Unless a
request explicitly narrows scope, applicable work includes:

- Backend model/repository/API tests for validation, state, conflicts, failure
  atomicity, and secret redaction.
- Pipeline unit tests for detection, normalization, caching, cancellation, and
  restart behavior.
- React component tests for loading, empty, error, success, dirty, and
  destructive states.
- Playwright coverage for cross-screen browser workflows and modal focus.
- Keyboard and accessible-name assertions plus automated axe checks on primary
  affected views.
- Responsive verification at desktop and narrow viewport sizes.
- `uv run ruff check scanner tests`.
- `uv run pytest -q` and coverage appropriate to the change.
- `cd web && pnpm build && pnpm test && pnpm test:e2e`.
- `git diff --check`.

## 20. Implementation evidence

The retrospective baseline was derived from:

- `scanner/api.py`, `models.py`, `db.py`, and `repository.py` for domain and API
  behavior.
- `scanner/jellyfin.py` and `arr.py` for source integration.
- `scanner/pipeline/*`, `worker.py`, `edl.py`, and `mute_preview.py` for scan,
  restart, normalization, export, and preview behavior.
- `web/src/App.tsx`, `api.ts`, and `styles.css` for information architecture,
  user flows, accessibility mechanics, and responsive presentation.
- `tests/`, `web/src/App.test.tsx`, and `web/e2e/app.spec.ts` for asserted
  acceptance behavior.
- Product decisions confirmed during the retrospective review, including every
  item marked Required correction above.
