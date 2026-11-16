# Editarr historical implementation plan

> This is the original execution checklist and is retained as historical
> context. The authoritative server/web baseline and corrective plan is now
> [the retrospective feature and design specification](feature-design-spec.md).
> Its scope explicitly excludes native Apple clients, and it supersedes this
> checklist wherever the two disagree.

This plan supersedes the path-first Samwise product plan. Editarr is the
primary web application and backend: it discovers media through Jellyfin and
coordinates scans and EDLs. Samwise remains the name of the iOS and macOS
clients; those apps become alternative Editarr API clients rather than
independent path-first front ends.

The interface should borrow the information hierarchy and operational clarity
of Radarr/Sonarr: a persistent navigation rail, library/series drill-down,
compact operational tables, obvious state badges, and detail pages that keep
the current action and history together. It is not a clone of either product.
Jellyfin remains the source of media identity, artwork, and metadata. The
Editarr database owns scan state, finding selections, and EDL-export state.

## Confirmed product decisions

- [x] **Jellyfin authorization:** Editarr connects with the configured
  Jellyfin server address and server API key. The key is used server-side only;
  Editarr clients never contact Jellyfin or receive the key.
- [x] **Scan ID scope:** `scan_id` increments per movie/episode or other
  scan-capable Jellyfin video item. A deleted scan number is never reused for
  that item.
- [x] **EDL ownership:** an item’s `EDL Exported` badge means the current
  sidecar EDL alongside its media file was exported from the displayed scan ID,
  even if newer scans exist.
- [x] **Deletion behavior:** Delete EDL removes the on-disk sidecar after a
  confirmation dialog and clears the export association.
- [x] **Supported libraries:** Editarr shows every Jellyfin library and
  supports every item backed by a video file, including video-capable items in
  non-Movie/non-TV libraries. Non-video items cannot be scanned.
- [x] **Credential storage:** follow the Radarr/Sonarr download-client model:
  persist the server-side integration setting, classify its API-key field as
  sensitive, mask it in the UI, redact it from API responses and logs, and
  protect the Editarr data directory with the service account’s filesystem
  permissions. The stored value is write-only through Editarr APIs.

## Shared acceptance rules

Unless a task says otherwise, a completed user-facing task must include:

- Automated unit/API tests plus the relevant web component or native XCTest
  coverage; flows that cross the browser UI require Playwright coverage.
- Keyboard-only operation, visible focus, correctly named controls, semantic
  labels/landmarks, status/error announcements, and color-independent state.
- Empty, loading, disconnected, unauthorized, forbidden-path, and server-error
  states where applicable.
- Responsive web behavior and the project’s lint, type-check, and build checks.

## 1. Product and migration foundation

- [x] **1.1 Establish Editarr naming and compatibility boundaries.** Rename
  backend/web product-facing text, titles, documentation, env-variable/API
  namespace, and deployment examples to Editarr. Do not preserve the
  path-first Samwise API or data model at runtime; Samwise remains only as the
  native-client name. Remove obsolete database rows during the one-way
  Editarr upgrade and remove all runtime/API reliance on them. Acceptance:
  no primary web surface or supported server endpoint calls the app Samwise;
  current Editarr migrations and APIs are tested and documented.

- [x] **1.2 Define the Editarr domain/API contract.** Write OpenAPI-backed
  request/response models and an architecture decision record for `Library`,
  `MediaItem`, `Scan`, `ScanFinding`, `QueueItem`, `EdlExport`, and
  `PathMapping`. Media identity is the immutable Jellyfin item ID plus server
  identity; a scan is a distinct record with an immutable increment-only
  per-item scan ID. Acceptance: contract covers movie, generic video, series,
  season, episode, queue,
  selection draft/save/discard, EDL lifecycle, pagination, sorting/filtering,
  and conflict/error responses; contract tests validate representative JSON.

- [x] **1.3 Evolve Editarr persistence during v1 development.** Do not add
  data-preserving migrations for development schema changes. Before a schema
  change that would require one, ask for confirmation to wipe the development
  database and start fresh. Acceptance: newly-created Editarr data uses the
  current schema and the v1 database-reset decision is documented.

- [x] **1.4 Refactor the pipeline around scan records.** Make all scanner
  jobs, progress, findings, and selection state belong to `Scan`, not to a
  one-off video row. Retain source path, resolved local path, effective options,
  timestamps, status/error, duration, and queue order on the applicable
  records. Acceptance: two scans of the same episode coexist with isolated
  findings/selections and a per-item monotonic, never-reused scan ID;
  cancellation/restart
  recovery and queue ordering tests pass.

## 2. Jellyfin and safe path resolution

- [x] **2.1 Implement server-side Jellyfin connectivity.** Add a narrowly
  scoped Jellyfin client/adapter behind Editarr APIs. Validate the configured
  server address, test credentials without exposing the key, normalize base
  URLs, apply timeouts, and map failures to actionable states. Acceptance:
  mocked tests cover success, invalid URL/key, unavailable server, malformed
  response, timeout, and pagination; the API key never appears in client JSON,
  browser storage, logs, or error responses.

- [x] **2.2 Persist and manage Jellyfin connection settings.** Store the
  server address and API key using the Radarr/Sonarr download-client pattern:
  a server-owned settings record with the key marked sensitive, masked in the
  UI, redacted in logs/responses, and protected by the service account’s data
  directory permissions. Permit replacing and clearing a key without returning
  its value. Acceptance: REST serialization tests prove the key is write-only;
  redaction, restart persistence, and configuration precedence are documented
  and tested.

- [x] **2.3 Implement library and metadata synchronization.** Retrieve every
  visible Jellyfin library, its video-capable items, the media/season/episode
  hierarchy where available, metadata, media stream/path data, and primary-image
  URLs through the backend. Support
  stable IDs, server-side paging, deterministic sorting, and resilient poster
  fallbacks. Acceptance: movie and TV fixtures cover absent artwork/metadata,
  multiple pages, and changed/deleted Jellyfin items; catalog APIs never need
  the web client to call Jellyfin directly.

- [x] **2.4 Add remote-to-local base path mappings.** Create ordered mapping
  records with remote and local absolute directory prefixes, validation, CRUD,
  and a safe “test resolution” action. Resolve by the most-specific valid
  directory-prefix match (not arbitrary substring replacement); fall back to
  the Jellyfin path when no mapping applies. Acceptance: mappings handle
  separators/trailing slashes, overlapping prefixes, invalid paths, a missing
  mapping, and a resolved file that cannot be read; scanner and export use the
  same resolver.

- [x] **2.5 Bind a scan request to a Jellyfin media item.** Replace public
  path-first scan creation with creation from any video-backed Jellyfin item ID.
  Resolve the backing media path only on the backend and snapshot the source
  identity/path at queue time. Keep any direct path endpoint internal or
  explicitly legacy-only. Acceptance: the API rejects series/season/non-video
  items, unavailable files, unmapped inaccessible paths, and invalid options;
  a valid video in any supported library queues successfully. Multiple scans
  for one item receive incrementing IDs and run serially through the queue.

## 3. Editarr web shell and settings

- [x] **3.1 Build the Editarr application shell and routing.** Implement a
  persistent accessible sidebar: expandable `Libraries` at the top with its
  Jellyfin libraries, expandable `Scans` with `Queue` and `History`, and
  `Settings`. Routes/deep links change the main view without losing useful
  navigation context. Acceptance: expansion state, current-route indication,
  deep-link loading, mobile navigation, and all keyboard/ARIA tree/disclosure
  interactions are tested.

- [x] **3.2 Build the Settings connection experience.** Add a Jellyfin server
  address/API-key form with test-and-save behavior, clear connection feedback,
  and no secret reveal after saving. Acceptance: validation and failed/successful
  connectivity UI states have component and browser coverage; the API key field
  is appropriately labelled and never prefilled from a read response.

- [x] **3.3 Build Settings path-mapping management.** Provide a Radarr/Sonarr-
  inspired table/form to add, edit, delete, reorder, and test remote/local path
  mappings. Clearly explain that this translates the Jellyfin server path to
  the scanner host path. Acceptance: confirmation and undo/error handling for
  destructive removal, validation feedback, resolution previews, and keyboard
  table/form behavior are tested.

## 4. Library browsing

- [x] **4.1 Implement the library item data APIs.** Add server-side paged,
  sortable library queries returning the fields needed by both views,
  `has_exported_edl`, exported scan ID, and image URLs. Support filter values
  `all`, `exported`, and `not_exported`, and case/diacritic-insensitive title
  search. Acceptance: sorting/filter/search are deterministic and compose
  correctly across pages; API tests cover all filters and missing metadata.

- [x] **4.2 Implement the pinned library toolbar.** Add an accessible segmented
  Icon/List view switch, fixed collection-sized icons, EDL filter dropdown, and debounced
  real-time title search. Persist sensible per-library display preferences.
  Acceptance: both views sort, all controls work with keyboard and announce
  state, search/filter never cause stale responses to overwrite newer input,
  and unit/component/Playwright tests cover the combinations.

- [x] **4.3 Implement icon and compact-list library views.** Icon view is a
  vertically scrolling, width-filling responsive poster grid with title below
  each poster; list view is a sortable metadata table. Both carry an accessible
  `EDL Exported` badge at the poster’s top-left only when an active export
  exists, with scan ID in text rather than color alone. Acceptance: poster
  fallback, virtualization/paging, image size/row density changes, empty state,
  badge semantics, and navigation to details are tested.

## 5. Jellyfin media details

- [x] **5.1 Implement movie and direct-video detail pages.** Render a
  Radarr-style metadata summary for any directly scan-capable item, its `EDL
  Exported`/scan-ID state, and a primary `Scans` work area. Include the detail
  toolbar with `New Scan` and correctly disabled `Delete EDL`. Acceptance:
  full/loading/missing-item states, metadata fallback, state announcements, and
  Delete EDL confirmation/error/success behavior are covered.

- [x] **5.1a Select a Jellyfin media version for each scan.** Expose every
  Jellyfin media source in the `New Scan` popover, resolve the selected
  source's path through base-path mappings, and persist its ID and display name
  with the scan. Track active EDL exports independently for each version and
  badge only the exported version in the detail table. Acceptance: a selected
  version is validated server-side, determines the actual scanner input, and
  remains visible in queue/history and detail scan metadata.

- [x] **5.2 Implement series, season, and episode drill-down.** Selecting a
  series opens its seasons; selecting a season lists its episodes; selecting an
  episode opens the shared media-detail/scans page. Include accessible
  breadcrumbs/back behavior and EDL status where useful. Acceptance: deep links
  to each level, empty seasons, specials, poster fallback, and keyboard
  navigation are tested.

- [x] **5.3 Build the New Scan popover.** Use the established audio/video
  model/interval/speed-up options in a focused popover with `Cancel` and `Add
  to Queue`. The media item, not a user-entered filesystem path, is implicit.
  Acceptance: focus trapping/restoration, validation, submit prevention,
  multiple scans for the same item can be added to the serial queue, queued
  success feedback, and browser tests pass.

## 6. Scan queue, history, review, and EDLs

- [x] **6.1 Implement global Queue and History views.** Scans > Queue shows
  active/queued/error scans with item identity, options, progress, ordering,
  and cancel/remove controls. Scans > History shows completed/deleted/failed
  scan records with their IDs, timestamp, item, options, and links to detail.
  Acceptance: refresh/polling lifecycle, queue recovery/reordering,
  pagination/filtering, error states, and all destructive actions are tested.

- [x] **6.1a Make scan execution restart-tolerant.** Launch each scan in a
  dedicated, tokenized worker process that survives an API-server reload. Persist
  its PID, process group, heartbeat, and completed-stage checkpoints. On startup,
  verify the tokenized process command before adopting a live scan; otherwise
  surface it as `orphaned` with an adoption/resume message. Deleting an orphaned
  scan terminates only its verified process group. Acceptance: API reloads never
  duplicate an active scan, stale work is resumable from a completed visual/audio
  checkpoint, PID reuse cannot target an unrelated process, and queue controls
  remain keyboard-accessible and covered by unit/browser tests.

- [x] **6.2 Implement the bounded scans selector on media details.** Show
  current queue entries and scan history together, with parameters, run time,
  status, incrementing per-item ID, and delete action. Bound the scrollable selector to
  four rows’ height, preserving access to the remaining items. Acceptance:
  selection changes the review below it, queue items update in place, delete
  confirmation safely handles an exported scan, and pointer/keyboard scrolling
  are tested.

- [x] **6.3 Add per-scan draft selection editing.** On selecting a scan, load
  persisted selections or default all findings selected when none are saved.
  Reuse and adapt the timeline and grouped audio/video list. Show `Save
  Changes`, `Discard Changes`, `selected/total filters`, and total skipped time
  above the list; do not write changes until Save. Acceptance: selection and
  summary calculations handle grouped changes/overlaps, discard restores the
  saved state, navigation with a dirty draft is guarded, and concurrent stale
  saves return a conflict rather than overwrite silently.

- [x] **6.4 Export and delete sidecar EDLs.** Place `Export EDL` at the right
  of the timeline and write a normalized Kodi/MPlayer Simple EDL alongside the
  resolved source media with the documented filename. Block export while a
  draft is dirty and display: “There are unsaved changes to the filter list.
  Please save or discard the changes before exporting.” Record the exporting
  scan ID and timestamp atomically only after a successful write. Acceptance:
  empty, mute-only, skip-only, mixed, overlapping, failed-write, dirty-draft,
  re-export, and delete tests pass; no partial EDL survives a failed export.

## 7. Samwise native clients

- [ ] **7.1 Migrate the shared Swift API client to Editarr.** Replace
  path-first Video/Queue contracts with Editarr settings, catalog, media
  detail, scans, draft selections, and EDL endpoints. Retain secure user-
  configurable Editarr server URL handling. Acceptance: decoding/encoding,
  errors, API version compatibility, and no-Jellyfin-secret tests pass.

- [ ] **7.2 Pivot the macOS and iOS UI to Editarr navigation.** Make both
  apps alternative API front ends for library browsing, hierarchy navigation,
  scan queue/history, detail review, draft save/discard, and export state.
  Their local server setting addresses Editarr, never Jellyfin directly.
  Acceptance: platform-appropriate accessibility, offline/error states, and
  XCTest/XCUITest coverage for the primary browse-to-export flow pass.

## 8. Documentation, quality, and release

- [ ] **8.1 Document deployment and migration.** Update README/deployment
  guidance for Editarr, Jellyfin API-key setup, server-side proxy behavior,
  secret handling, path mappings, scanner filesystem access, EDL behavior,
  and Samwise native-client connection. Include recovery/rollback instructions.
  Acceptance: a clean-environment setup is validated against the documentation.

- [ ] **8.2 Complete the cross-client release matrix.** Verify that a scan
  queued in Editarr is visible/reviewable in both Samwise apps and vice versa;
  verify EDL selection/export/deletion state after restart and on every client.
  Run backend lint/tests, web unit/e2e/build, Swift tests, macOS/iOS builds,
  and platform UI tests. Acceptance: all commands/results are recorded and a
  manual desktop/mobile accessibility and visual QA pass finds no release
  blockers.

## Reference direction

- Jellyfin libraries are virtual collections and are the source of movie/TV
  media organization: <https://jellyfin.org/docs/general/server/libraries/>.
- The official Jellyfin TypeScript SDK documents library retrieval and the API
  client model: <https://typescript-sdk.jellyfin.org/>.
- Servarr documents Remote Path Mapping as a remote-prefix to local-prefix
  translation, the model used for Editarr mappings: <https://wikiold.servarr.com/Settings_Download_Clients>.
