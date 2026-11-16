# Named scans, saved reviews, and EDL import plan

Status: **Implemented and verified**

Plan date: **September 12, 2026**

Last updated: **September 14, 2026**

Completion evidence: NSR-12 and its independent repair reviews removed the legacy competing
authorities and passed Ruff, 845 backend tests, 100 frontend tests, the production build,
31 Chromium browser tests, and temporary file-backed SQLite restart persistence verification.

Task tracker: [Named scans, saved reviews, and EDL import coding tasks](named-scans-saved-reviews-tasks/README.md)

This plan covers three related capabilities:

1. user-visible names for scans;
2. named, persistent reviews that can be selected from a saved-review list; and
3. importing a Simple EDL into a named saved review, with corresponding imported
   audio and/or video scan data.

The plan extends the current authoritative baseline in
[feature-design-spec.md](feature-design-spec.md). The product decisions were confirmed on
September 12–13, 2026 and are recorded in section 4.

## 1. Pre-implementation behavior and constraints

The baseline replaced by this completed plan had these relevant characteristics:

- A scan has a stable UUID and a per-media-item, monotonically increasing scan number,
  but no user-visible name. Scan creation is exposed for Jellyfin, Radarr, Sonarr, and
  Other Files, and scan metadata appears in item detail, the global Scans panel, candidate
  matching, and EDL provenance.
- "Scan Review" is not a stored resource. The browser temporarily combines selected
  audio and video scan IDs for one media version.
- Finding selection and human edits are persisted directly on each `scan_findings` row
  and guarded by the owning scan's `selection_revision`. Consequently, a finding currently
  has only one saved selected state and one saved content/timing value across every review
  in which that scan appears. The approved review model replaces this with isolated,
  review-owned filter snapshots.
- EDL export accepts independently selected audio and video scan IDs. It writes selected
  findings from those scans and stores all contributing scans as provenance for the
  active EDL of one media item/version.
- The current exporter emits Kodi/MPlayer Simple EDL rows with action `1` for mute and
  action `3` for skip. It does not import or parse EDL files.
- Manual findings use a special completed scan per media item/version. That scan may hold
  audio and video findings together. Imported data will need an explicit relationship to
  this behavior so that imported scans are not accidentally presented as AI or ordinary
  manual scans.
- Scan deletion is blocked while the scan contributes to an active EDL. No saved-review
  dependency exists yet.
- Review UI is shared by catalog-backed media detail pages, while Other Files has a
  separate workspace using the same review component.
- Per repository guidance, schema changes during v1 do not receive data-preserving
  migrations. The user approved skipping migrations and wiping/recreating the development
  database when implementation begins. No database is wiped by this planning change.

## 2. Confirmed outcomes

Implementation must deliver these outcomes:

- A user can optionally supply a human-readable name for any scan, including the Manual
  scan. Scan names are retained, returned, and can be changed later. Duplicate names are
  allowed.
- Scan names are displayed consistently wherever a scan is identified, while the stable
  scan number remains visible for disambiguation and support.
- A review becomes a persistent, named resource for a specific media item and physical
  media version. It owns an isolated snapshot of its filters, including selections, content,
  timing, and selection state; changes in one review do not alter another review.
- The UI shows the saved reviews applicable to the selected item/version, can sort them by
  name or modification date, and supports opening, renaming, duplicating, and deleting
  them.
- Opening a version loads its most recently saved review. If no saved review exists, the
  UI creates an empty unsaved working review. Saving remains explicit.
- Switching reviews preserves the existing unsaved-change guard; user edits are never
  silently discarded.
- EDL export identifies the saved review that produced the active on-disk EDL. Content
  changes made after export mark that review `Outdated`; deleting the EDL clears the
  marker.
- A user can import an EDL into a selected media item/version, must provide the new
  review's name during import, and receives a saved review backed by imported scan
  findings.
- An import containing mute rows creates audio scan data; one containing skip rows creates
  video scan data; a mixed file creates one completed combined audio/video scan.
- Import is available for Jellyfin, Radarr, Sonarr, and Other Files and accepts either a
  browser upload or an absolute path readable on the scanner host.
- One or more unsupported EDL actions are ignored and reported through one visible
  warning; they do not prevent supported mute and skip actions from importing.
- Import validates the entire input before committing. Invalid or partially supported
  content must not leave a review, scan, or finding fragment behind.
- Imported findings participate in the ordinary review, preview, edit, selection, and
  export experiences. Deleting a review never deletes its scans, including imported scans.
- Export writes Kodi-compatible `##` comment lines carrying filter category/label data;
  import uses those comments to retain useful filter metadata during a supported
  export/import round trip.
- Catalog-backed media and Other Files retain equivalent review and EDL behavior.
- Existing authentication, same-origin/CSRF, path resolution, media-version isolation,
  atomic EDL replacement, and accessibility requirements remain in force.

## 3. Domain design

This section identifies the resources and boundaries implementation is expected to use.

### 3.1 Scan naming

Add a nullable normalized display-name field to `ScanRow`, the Python `Scan` response,
the TypeScript `Scan`/`ScanQueueItem` interfaces, and every scan creation request. Trim
outer whitespace, reject control characters, preserve case, and limit names to 100
Unicode code points. An omitted or blank normalized value leaves the scan unnamed. Names
are not unique and can be changed after creation, including on the Manual scan.

The display convention retains `Scan #N` even when a name exists. Use one shared formatter
throughout the UI, such as `Dialogue pass · Scan #4`, rather than allowing each surface to
invent its own order or punctuation.

### 3.2 Saved review

A saved review needs its own stable ID, media-item ID, media-source/version identity,
display name, optimistic resource revision, content revision, timestamps, and ordered
scan-role membership. It also owns a snapshot of the filter state used in that review.
This permits two reviews to select and edit the same source finding differently.

Likely persistence concepts are:

- a `saved_reviews` row for identity, version scope, name, revision, and timestamps;
- review-to-scan membership carrying `audio`, `video`, or `both` participation; and
- review-owned filter rows that retain source scan/finding provenance while copying the
  editable category/word, label, description, timing, action, and selected state.

AI, imported, and Manual scan findings remain source evidence. Review edits do not mutate
those source rows. Adding a completed scan to a review snapshots its applicable findings,
and duplicating a review copies its current snapshots. If a scan is removed and re-added
before the review draft is saved, the browser restores the snapshot from that editing
session. Saving the removal discards that stored snapshot; adding the scan in a later
editing session creates a fresh snapshot from its source findings.

### 3.3 Active EDL provenance

The active per-version EDL record currently points to all contributing scans. It must
additionally refer to the saved review and the review content revision used for the
successful export. Scan provenance remains so the output is reproducible and scan-deletion
protection remains enforceable.

The review marker changes only after the sidecar replacement succeeds. Failed export
leaves both the previous sidecar and previous review marker unchanged. A content-affecting
review change increments the content revision and makes the marker read `Outdated`; a
name-only change does not make the EDL outdated. Deleting the active sidecar clears the
marker. Deleting the marked review is allowed: the sidecar and complete scan provenance
remain active, while the now-invalid review association is cleared.

### 3.4 Imported EDL representation

EDL parsing belongs in `scanner/edl.py` beside export normalization, but parsing must not
write to the database. A repository/service operation should validate media scope and
duration, then create the review, imported scan record or records, findings, memberships,
and initial selection state in one transaction.

Imported scans need explicit provenance in the API/UI, preferably a stable scan-origin
value rather than overloading the existing `manual` and `edited` flags. An import creates
one completed scan: audio-only for mute rows, video-only for skip rows, or combined for a
mixed file. Its initial name is the required review name and can be changed later. The
safe EDL source basename is used as the imported scan's source/version display name, and
the import time is stored and displayed as its timestamp, never as a scan duration.
Review deletion never deletes this scan.

## 4. Decision record

### D1. Scan-name contract — confirmed

- Scan names are optional, mutable after creation, and do not need to be unique.
- The Manual scan can be named and renamed.
- An unnamed scan continues to use its existing `Scan #N` identity in the UI.
- A named scan retains its scan number as a secondary stable identity.
- Names preserve display case. Trim outer whitespace, reject control characters, and
  limit names to 100 Unicode code points. Blank scan-name input is normalized to no name;
  a saved review name is required.

### D2. Saved-review ownership — confirmed

- A saved review owns its selected audio/video scan membership and a private snapshot of
  all reviewable filter fields.
- Selection changes, category/word/label changes, descriptions, and timing edits affect
  only the current review. They do not change another saved review using the same source
  scan.
- Source scan findings remain the provenance from which a new review snapshot is created.
- Review-level optimistic concurrency replaces scan-level selection revisions as the
  authoritative save boundary.

### D3. Review creation and explicit save — confirmed

- Opening an item/version loads the most recently saved review for that version.
- If the version has no saved review, the UI starts with a new empty unsaved review.
- Review changes remain a local draft until the user explicitly saves, consistent with
  the current Save Changes behavior.
- The first save requires a review name and creates the saved review. Subsequent saves
  update that review.
- Saved reviews can be renamed, duplicated, and deleted.

### D4. Review list and names — confirmed

- Reviews are scoped to one media item and physical version, preserving the current
  cross-version isolation rule.
- Review names are required when saved but do not need to be unique.
- The review list offers sorting by name and by date modified. The selected sort direction
  is a UI preference and does not change persistence identity.
- Duplicate names remain distinguishable by modification date and stable internal ID;
  destructive confirmations must include sufficient context to identify the target.

### D5. Active EDL review marker — confirmed

- "Saved scan" in the original request meant **saved review**.
- Exactly one saved review is marked as the source of the active on-disk EDL for each
  media version.
- A content-affecting save after export keeps the review associated with the EDL but marks
  it `Outdated`.
- Renaming a review does not make its EDL outdated because it does not change output.
- Exporting any review transfers the marker to that review and records the exported
  content revision only after atomic sidecar replacement succeeds.
- Deleting the EDL clears the review marker.
- Deleting the marked review is allowed. It leaves the sidecar and scan provenance intact
  but clears the review association because that review no longer exists.

### D6. Import entry points and source coverage — confirmed

- The user can either upload an EDL through the browser or provide an absolute EDL path
  readable by the scanner host.
- Import is supported for Jellyfin, Radarr, Sonarr, and Other Files.
- The user selects the target item/version before import. Existing source adapters, path
  mapping, and media-duration validation establish the trusted target.
- Browser upload and scanner-path import feed the same bounded parser and atomic creation
  operation; they must not diverge in accepted content.

### D7. Imported scan shape and name — confirmed

- One import creates one completed scan.
- Mute-only input produces an audio scan, skip-only input produces a video scan, and mixed
  supported input produces one combined audio/video scan.
- Imported scans never enter the queue or run a model worker.
- Deleting the review does not delete the imported scan.
- The imported scan initially uses the required review name and can be renamed afterward.
- The EDL source basename is its source/version display name, and import time is its scan
  timestamp rather than a duration.

### D8. Accepted EDL syntax — confirmed

- The first release accepts the same whitespace-separated seconds format Editarr exports:
  `start_seconds end_seconds action`.
- Action `1` imports as mute/audio and action `3` imports as skip/video.
- Blank lines and Kodi comment lines beginning with `##` are accepted.
- Other actions, including cut and scene marker, are ignored. If any are encountered, the
  completed import response and UI show one warning that unsupported actions were ignored
  and not imported.
- Comma-separated rows, frame-based positions, and alternate timestamp formats are out of
  scope for this release.
- If no supported rows remain after unsupported actions are ignored, import returns an
  error stating that no supported EDL actions were found and creates nothing.

Kodi documents `##` as the EDL comment-line prefix and ignores those lines during
playback: [Kodi Edit decision list](https://kodi.wiki/index.php?title=Edit_decision_list).
The association between a comment and the following action is an Editarr convention and
must be emitted and parsed consistently.

### D9. Round-trip comments and imported filter metadata — confirmed

- Export adds a Kodi-compatible `##` comment immediately before each emitted action row.
- The comment carries filter metadata in a human-readable `category: label` form.
- Import associates contiguous `##` comments immediately preceding a supported action
  with that action and uses them to populate review metadata.
- Imported review filters are editable and deletable like other review-owned filters.
- Export writes one comment per contributing filter before the normalized action row. If
  normalization merges multiple differently labelled filters, all of their comments are
  retained in original review-filter order even though their original pre-normalization
  time boundaries are not. That range-data loss is accepted.
- Import creates one review filter for each recognized comment associated with a supported
  row, using the normalized row's range. A supported row without a recognized comment uses
  category `other` and label `Imported mute` or `Imported skip`, according to its action.
- The safe source basename and import timestamp are retained and displayed. No response
  exposes an unintended scanner-host path.

### D10. Deletion and dependency policy — confirmed

- Scan deletion is blocked while any saved review references the scan.
- The error and confirmation UI list all referencing review names. Because review names
  need not be unique, each entry also includes modification date or equivalent context.
- The existing active-EDL scan dependency remains enforced independently.
- Deleting a review never deletes any source scan, including an imported scan used only by
  that review.
- Add Filter appends a source finding to the saved Manual scan and snapshots it into the
  current review, making that manual scan available for reuse by later reviews. Subsequent
  edits to the current review's snapshot remain isolated.

### D11. Development database reset — approved

- Implementation may skip data-preserving migrations and wipe/recreate the v1 development
  database for these schema changes, in accordance with `AGENTS.md`.
- The wipe is authorized for implementation, not performed by this planning update.
- The implementation handoff must state when the database was recreated and how to start
  with the new schema.

### D12. Final interaction and round-trip details — confirmed

- Scan and review names use the validation contract in D1.
- Imported scans initially use the review name and remain renameable.
- Deleting a marked review is allowed and preserves the active sidecar plus scan
  provenance while clearing the review association.
- Removing and re-adding a scan restores its draft snapshot only within the same unsaved
  editing session. Once the removal is saved, that snapshot is forgotten.
- Export writes one `## category: label` comment per contributing filter. Accepted loss of
  original pre-normalization time boundaries is explicit.
- Import retains and displays the safe source basename and import timestamp.
- Supported uncommented actions use category `other` and the generic label `Imported
  mute` or `Imported skip`.
- A file with no supported mute/skip action is rejected without creating data.
- New manual filters are saved into the Manual scan for later reuse and copied into the
  current review's isolated snapshot.

## 5. Implementation workstreams

### 5.1 Update the authoritative product and API contracts

- Add scan-name, saved-review, imported-scan, and review-owned selection vocabulary to
  `docs/feature-design-spec.md`.
- Replace the current review/selection/export rules that the approved decisions change.
- Document import limits, validation, atomicity, provenance, conflicts, deletion rules,
  and the on-disk EDL marker lifecycle.
- Extend `docs/api-contract.md` with the finalized endpoint shapes.
- Record the approved answers from section 4 as the decision record for implementation
  and testing.

### 5.2 Add persistence models and repository invariants

- Add scan naming and explicit imported provenance to `ScanRow` as approved.
- Add saved-review identity, version scope, membership, resource/content revisions, and
  review-owned filter snapshot tables required by D2.
- Add active-EDL-to-review ID and exported-content-revision provenance.
- Define database constraints for version scoping, role values, required review names,
  and no duplicate review membership. Do not add name uniqueness constraints.
- Implement repository operations for list/get/create/update/rename/delete/duplicate,
  using a review-level optimistic revision for atomic membership and filter-snapshot
  updates.
- Enforce scan/review/EDL dependency rules in repository transactions, not only the UI.
- Keep imported review creation atomic and make import retries safe from partial rows.
- On review save, atomically append new manual source findings to the reusable Manual scan
  and create their review snapshots. Discarding a draft must not leave source findings
  behind.
- Follow the approved v1 database-reset path; do not add a preservation migration unless
  the user changes the repository policy.

### 5.3 Extend scan APIs and all scan creation paths

- Add validated scan-name input to shared scan options so Jellyfin, Radarr, Sonarr, and
  Other Files behave consistently.
- Persist and return the name in queue, history, item scan lists, candidate scans, manual
  finding results, and EDL provenance where approved.
- Add a scan rename endpoint. It accepts duplicate names and clears the name when the
  approved normalization treats input as blank.
- Ensure the dedicated worker and checkpoint lifecycle preserve but do not depend on a
  mutable display name.
- Update user-facing validation to distinguish invalid and overlong names without
  exposing filesystem or upstream details.

### 5.4 Add saved-review APIs

- Add source-neutral saved-review service/repository operations, then expose routes that
  align with the approved public item/version identity model.
- List reviews for the current media item/version with enough summary data to render the
  switcher and export marker without loading every finding.
- Load one complete review with ordered audio/video scan memberships, private filter
  snapshots, resource/content revisions, timestamps, and export state.
- Create and update a review atomically; reject scan membership from a different media
  item/version or an incomplete/unreviewable scan.
- Apply optimistic concurrency at the review aggregate. A stale save returns `409` and
  never overwrites another browser's edits.
- Add rename, duplicate, and delete endpoints with the approved non-unique naming and
  scan-retention behavior.
- Retire the scan-level selection-save and scan-finding edit endpoints as authoritative
  review state. Avoid retaining two writable sources of truth; source scan findings are
  evidence, while review filter snapshots are editable.

### 5.5 Implement strict EDL parsing and import orchestration

- Add a pure parser with line-aware validation for the exact syntax approved in D8.
- Define bounded upload/text size, row count, numeric precision, finite-number checks,
  non-negative starts, strictly increasing end times, media-duration bounds, encoding,
  comments, and duplicate/overlap behavior.
- Return actionable validation errors with line numbers and no raw local paths or unsafe
  file content.
- Resolve the selected target media/version using existing source adapters and path
  mapping. Verify that the associated media file remains readable before creating data.
- Map mute and skip rows plus recognized immediately preceding `## category: label`
  comments to imported source findings and review snapshots.
- Ignore unsupported actions and return one warning flag/count regardless of how many
  unsupported rows were encountered.
- Reject an import with no supported mute/skip rows after parsing, using the approved
  no-supported-actions error and leaving no data behind.
- Create one correctly typed completed scan, its source findings, the saved review,
  membership, and review snapshots in one transaction.
- Import does not change active-on-disk EDL state. A review receives that marker only
  after Editarr successfully exports it.
- Ensure import does not queue or invoke AI workers.

### 5.6 Export from a saved review

- Change the export command to identify a saved review rather than accept an arbitrary
  browser-only scan combination. The saved review is the export source of truth.
- Revalidate review revision, scan completion, association, media-version identity, and
  persisted selection state on the server immediately before export.
- Continue deriving playback rows through the existing normalization rules and add the
  approved Kodi-compatible metadata comment convention.
- Atomically replace the sidecar first, then update active EDL scan and review provenance
  together. Preserve previous state on any filesystem or transaction failure.
- Expose enough state to identify both the saved review and every contributing scan.
- Compare current and exported content revisions for the `Outdated` state, and apply the
  approved marked-review deletion behavior.

### 5.7 Update the web application

- Add the scan-name control to every New Scan flow and show the approved label in the
  global Scans panel, item scan lists, Other Files, matching candidates, finding
  attribution, delete confirmations, and EDL provenance.
- Add a keyboard-accessible saved-review selector/list scoped to the selected version.
- Implement empty-unsaved/open-most-recent/save/rename/duplicate/delete interactions.
- Keep removed scan snapshots in the current browser draft so remove/re-add restores them;
  discard that cache when the removal is saved or the editing session ends.
- Preserve unsaved-change confirmation when switching reviews, versions, files, routes,
  or scan membership.
- Make loading and error states explicit; do not leave a failed review switch looking as
  if it succeeded.
- Add Import EDL to every source workflow. Require the review name and selected
  version before submission, show validation warnings/errors, and open the newly created
  saved review after success.
- Mark the review associated with the active on-disk EDL and show `Outdated` after a
  content-affecting save.
- Keep scan numbers and type/origin badges alongside names so similarly named scans
  remain distinguishable without relying on color.

### 5.8 Cleanup and documentation

- Remove obsolete browser state and API helpers once saved reviews replace ephemeral scan
  combinations; do not leave parallel persistence paths.
- Update README user workflows for naming scans, managing reviews, importing, exporting,
  and recovering from validation errors.
- Update OpenAPI assertions and examples.
- Document the database reset required for the schema change and the fact that no v1
  preservation migration is supplied.

## 6. Test and verification plan

### 6.1 Backend unit tests

- Scan/review-name normalization, limits, optional-versus-required behavior, duplicate
  names, clearing an optional scan name, and renaming.
- EDL parser coverage for blank/comment lines, decimals, ordering, mute, skip, mixed
  actions, unsupported actions, malformed fields, NaN/
  infinity, negative or reversed ranges, duration overflow, encoding, size/row limits,
  and line-number errors.
- Import mapping to audio-only, video-only, and combined single-scan structures.
- Export/import round trips for supported rows and `## category: label` comments,
  including the finalized overlap-normalization behavior.

### 6.2 Repository and API integration tests

- Review create/list/load/update and approved management actions for every source type.
- Media-item/version isolation and rejection of cross-version scan membership.
- Independent selection, content, and timing behavior across two reviews using the same
  scan.
- Review and scan optimistic-concurrency conflicts.
- Transaction rollback on any import validation or persistence failure.
- Imported scans are immediately complete, never queued, have correct origin/type/name,
  and work with review, preview, edits, and export.
- Active EDL marker changes only on successful atomic export and clears/retains exactly as
  D5 specifies.
- Scan deletion lists every referencing review and cannot bypass active EDL protection;
  review deletion leaves ordinary and imported scans intact and follows the finalized
  marked-review rule.
- Authentication, CSRF, safe error envelopes, and OpenAPI shapes cover every new unsafe
  route.

### 6.3 Frontend component tests

- Scan name entry, validation, display, and any rename interaction across shared and
  Other Files workflows.
- Saved-review ordering, empty state, keyboard switching, active selection, rename/
  duplicate/delete actions as approved, and export marker presentation.
- Dirty-state guards for review, scan, version, file, and route switching.
- Import dialog requires a review name and target version, submits the approved payload,
  reports parser errors, and opens the created review.
- Multiple saved reviews restore the correct scan membership plus isolated selection,
  content, and timing state.
- Accessible names include both scan name and scan number where needed.

### 6.4 End-to-end acceptance

Exercise Jellyfin (including multiple versions), Radarr, Sonarr episode, and Other Files
paths as approved:

1. create named audio and video scans and complete them;
2. create at least two named reviews using different combinations/selections;
3. switch between reviews without state leakage;
4. export one review and verify the exact sidecar plus marker;
5. export the other and verify atomic replacement plus marker transfer;
6. import mute-only, skip-only, and mixed EDL fixtures into named reviews;
7. edit and re-export imported findings;
8. verify invalid import leaves no partial data;
9. verify active-EDL and saved-review deletion protections; and
10. restart the service and confirm names, reviews, selections, import provenance, and
    export state survive.

Run the focused backend/frontend suites first, then the repository's full backend tests,
frontend unit tests, production build, and relevant browser tests. Schema recreation must
use the explicitly approved development-only procedure.

## 7. Dependency-ordered implementation sequence

Implement in this dependency order:

1. Amend the feature specification and API contract with the confirmed decisions.
2. Add persistence models and repository invariants, then use the approved D11 procedure
   to recreate the development database without a preservation migration.
3. Deliver scan naming through backend creation/listing and all existing UI surfaces.
4. Deliver saved-review CRUD and review-owned filter snapshot behavior.
5. Rewire EDL export and active marker state to saved reviews.
6. Deliver strict EDL parser/import APIs and imported scan creation.
7. Complete the shared media-detail and Other Files review/import UI.
8. Remove superseded scan-level selection paths and ephemeral-state assumptions.
9. Complete integration, accessibility, browser, restart-persistence, and full regression
   verification.

Do not start a later step while an earlier aggregate has two competing sources of truth.
In particular, saved reviews must be authoritative before the old selection/export path
is removed, and imports must use that same final review aggregate rather than introduce a
temporary third representation.

## 8. Definition of done

This feature set is complete only when:

- every approved scan creation surface persists and displays names consistently;
- multiple named saved reviews survive restart and switch without leaking selection,
  content, or timing edits;
- the active on-disk EDL marker follows the exact D5 lifecycle;
- importing an approved EDL subset atomically creates the named review and appropriate
  completed imported scan data without starting a worker;
- saved reviews and imported scans use the same editing, preview, export, conflict, and
  deletion rules as ordinary review data, except for explicitly approved differences;
- no stale scan-level selection or export path can disagree with a saved review;
- backend, frontend, browser, OpenAPI, accessibility, and restart-persistence tests pass;
  and
- the authoritative feature specification, API contract, and user documentation match
  the shipped behavior.
