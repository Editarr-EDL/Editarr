# Saved Reviews And Exports

Users create isolated saved reviews, add manual findings, and write EDL/MCF
sidecars for one selected physical version without modifying its media bytes.

## Sub-features

- `review-create`: name and save a review with manual filters or selected scans.
- `review-isolation`: reopen independently named reviews without crossing versions.
- `export-edl`: write and verify the actual EDL sidecar.
- `export-mcf`: satisfy preflight and verify deterministic MCF output independently.
- `review-policy`: retain base values beneath current-owner enforced policy and
  reject stale saves and exports.
- `review-deletion`: delete a published review after an explicit retain or remove
  choice for each visible file; hidden files are retained undisclosed.
- `export-regeneration`: keep personal bytes serving during policy updates, then
  replace them or withdraw on a recorded failure.
- `export-ui`: choose a destination (self, review owner, item default), confirm
  replacing another review's file, delete published files, and pick retain or
  remove per file when deleting a review.
- `plugin-interop`: the public Jellyfin plugin serves each linked user the exact
  EDL and MCF bytes Editarr published for them.

## How to get to it (user POV)

- The saved-review panel of a Jellyfin item/version page.
- `New Review`, an existing `Open review ...` row, and a saved review's `Export`
  button, which opens dialog `Export`.
- Region `Published files` lists every readable publication of the version.
- `Manage exports` on catalog item pages manages only the adjacent
  pre-migration sidecars (`legacy_exports`). It appears when one exists and the
  caller holds `exports.default.delete` (admins and the owner), and deletes
  through the source/format route, which checks the same capability on the
  item. Delete canonical files from `Published files` instead.
- Deleting the last visible review keeps its retained files under
  `Published files` after reload. Selecting another media version shows only
  that version's files.

## Driving it with Playwright

`node .github/skills/verify-editarr/scripts/verify.mjs auth --reviews` runs the
import, rename, duplicate, export, published-deletion refusal, and fresh mobile
reopen on one movie from the labelled synthetic Jellyfin transport through the
real source app. It retains sidecars and hashes
with the normal doctor, screenshots, redaction, and cleanup records.

Preconditions: authenticated owned `serve` instance, writable disposable video with
known duration, no existing adjacent `.edl`/`.mcf`, and a media SHA-256 recorded
before the first action. Baseline contains none of these media inputs.

- **Create.** Scope to `page.locator('.saved-review-panel')`. Click `New Review`;
  in dialog `New Review`, fill `New review name` with `Verification review` and
  click `Start new review`.
- **Add.** Click `Add Manual Filter`; in that dialog fill `Manual filter label`
  with `Verification mute`, then click `Add Filter`. Assign valid family,
  subcategory, severity, channel, cue bounds, and action before saving.
- **Persist.** Click `Save Review`; reload and click button
  `Open review Verification review`. Expand collapsed category/group controls
  before editing filters. Verify the manual cue survived a protected read/reopen.
- **Export.** Click exact button `Export`; in dialog `Export` click
  `Publish EDL`, wait for `POST /api/reviews/{id}/exports/edl`, then `Close`.
  Require `EDL · Current` and independently inspect the canonical personal
  `.edl` bytes in scratch. Copy only the sidecar and its digest to evidence. The
  media SHA-256 must be unchanged.
- **MCF.** Repeat with `Publish MCF`. If `MCF export needs attention` appears,
  the dialog closes; follow the issue to the missing field, save it, and retry.
  Verify `.mcf` bytes and the MCF badge separately; EDL success proves neither.
- **Inspect.** Check `Published files` for the new `EDL` and `MCF` rows.
  `Manage exports` lists only adjacent legacy sidecars.
  Exercise other source/version entry points when shared review behavior changed.

## Gotchas

- Missing media or required MCF metadata is an unmet precondition, not permission
  to mock a successful export or silently omit the format.
- Exports replace canonical personal sidecars. Legacy adjacent files stay unchanged.
  Use only scratch files owned by this run.
- Manual filters can be staged but unsaved. A visible row is not persistence proof.
- Plain `DELETE /api/reviews/{id}` still refuses a review with visible files with
  `published_review_retention_required`. The `Delete review` button and the
  review repair dialog's `Delete the review` option both use `GET` then
  `POST /api/reviews/{id}/deletion` with per-file choices. Deleting an unpublished
  review does not delete shared scan evidence.
- Baseline has no scan findings. Test manual-only reviews first; included-scan and
  imported-retiming cases require their actual separate fixtures and proof.

## Canonical Publication

`auth --reviews --ports=9140-9159` reads canonical
personal EDL/MCF files from the run's scratch database. It independently compares
download, retained content, and disk bytes, verifies the canonical filename, preserves
legacy adjacent bytes, and deletes only its own exports through the
revision-checked API before the policy journey. The existing UI export menu is
exercised through the `Export` dialog; target selection and confirmation are
covered by the export UI recipe below.

Before deletion, the journey reloads the desktop page and reopens the review.
Both current badges must remain visible. Fresh review and discovery GETs must
retain the disk/database publication UUIDs and revisions. A fresh 320px browser
context repeats the visible-current check. Retain
`reviews-publications-after-reload.json` with the screenshots and normal cleanup
records. The discovery contract is documented in `docs/multi-user-contracts.md`.

Run `tests/test_personal_exports.py`, `tests/test_personal_exports_http.py`,
`tests/test_review_repository_export.py`, and `tests/test_mcf_export.py` for
confirmation races, filesystem collisions, live authority, audit rollback,
failed restore/retry, lost commit acknowledgment, and hard exits before/after
commit for both formats. Upgrade checks remain backup-first. This journey does
not certify a live Jellyfin server, plugin installation, or external filesystem
writers. Retain canonical sidecar copies, hashes, screenshots, HTTP statuses,
and cleanup records. Do not edit Python while the journey is running.

Run `tests/test_export_catalog_http.py` for discovery snapshot and privacy
regressions. Real HTTP DELETE and hidden-peer publication reuse the row ID after
discovery returns. Catalog decoration must retain the complete original snapshot
without crashing or disclosing peer provenance. Revoking source-review visibility
must redact review IDs and revisions in both catalog publications and format
summaries, matching direct export status. Run `tests/test_export_windows_http.py`
for failed parent-directory sync followed by a successful durable retry in both
formats and target scopes. These use synthetic provider boundaries, not live
provider or native Windows certification.

## Review Deletion With File Choices

`auth --reviews` ends with
`scripts/lib/review-deletion-journey.mjs` on the same isolated instance. A new
member (`deletion-member`, a Jellyfin user admitted by its first sign-in) imports an MCF review and publishes personal EDL and MCF. The owner
publishes a personal EDL and the item-default MCF from that review, confirming
replacement of the policy journey's files. Then:

- **Preview.** `GET /api/reviews/{id}/deletion` as the member lists only the
  member's two files with `["remove", "retain"]`. The owner's personal and default
  IDs never appear. The owner's preview lists all four.
- **Stale.** The member republishes the EDL; the earlier confirmation returns 409
  `stale_review_deletion` with an unchanged SQL and disk inventory.
- **Smuggled.** Adding the hidden default with `remove` returns 422
  `review_deletion_choice_required` and changes nothing.
- **Execute.** Retain the member EDL and remove the member MCF. The response lists
  only those IDs. The review is 404 for both accounts. Every retained row has a null
  review ID, the member as policy owner and author, its original actor, and
  unchanged content and disk digests. The removed path is gone. `export.retain`
  rows name the three retained files and `export.delete` the removed one.
- **Regenerate.** The owner enforces severity `violence` on the member. Both
  retained personal files reach `succeeded` at the new policy revision under the
  deleted review's owner. The item default is untouched and the media is unchanged.

`review-deletion-result.json` and `review-deletion-http-statuses.json` hold the
proof. Fault injection, hard exits, permission overrides, and hidden-change
non-invalidation are in `tests/test_review_deletion_http.py`.

## Export Choices And Deletion UI

`auth --reviews` ends with
`scripts/lib/export-ui-journey.mjs` on the same instance, after the review deletion recipe.
A new member (`export-member`, a Jellyfin user with default permissions)
imports MCF reviews
`Export A` (cue 1-2 s) and `Export B` (cue 2.5-3.5 s) on the synthetic movie. Every check reads SQL rows and parses the EDL rows or MCF cue timings
on disk; badges are never the proof.

- **Member destinations.** Dialog `Export` for `Export A` offers one radio,
  `Your personal files`, and no item default. `Publish EDL` and `Publish MCF`
  write the member's personal files with cue 1-2.
- **Replacement.** On `Export B`, `Publish EDL` answers 409
  `export_confirmation_required`; alert `Confirm replacement` names `Export A`
  at publication revision 1. The journey republishes A through the API, so
  `Replace EDL` answers 409 `stale_export_confirmation`, alert
  `Replacement changed` appears, and the disk EDL still holds cue 1-2. `Retry EDL`
  shows revision 2; `Replace EDL` writes cue 2.5-3.5. The MCF keeps cue 1-2 and
  both reviews still read 200.
- **Reload and account switch.** After reload, `Published files` shows the EDL
  from this review and the MCF from `Export A`. Signing out and in as the owner
  shows three radios. `Review owner's personal files (export-member)` names the
  member as destination and policy source and shows the member's files. The
  owner publishes the item-default MCF and a personal EDL, confirming replacement
  of the retained files from review deletion. SQL shows default scope with no target user, owner
  target and actor, and the member as policy owner; both files hold cue 2.5-3.5.
- **Cross-user delete.** The owner sees `Delete` on every row and deletes
  `MCF · Personal · export-member`: the file and row are gone and the member EDL
  bytes are unchanged. Signed back in, the member sees one row and one `Delete`.
- **Review deletion.** The member republishes B's MCF, opens `Delete review`,
  sees two groups (the owner's EDL and the default stay hidden), keeps the EDL,
  and removes the MCF. The response retains the member EDL and removes the MCF.
  The MCF file is gone; the member EDL, owner EDL, and default MCF keep their
  bytes with a null review ID and the member as policy owner. The row reads
  `From a deleted or unavailable review.`
- **320 and regeneration.** A 320px member context shows the retained row and the
  export dialog without page overflow; axe runs on each dialog. The owner then
  enforces severity `violence` on the member. Polling reloads until the row reads
  `Current` at the new policy revision; any `Regenerating` reading must say the
  previous file stays available. Observations are recorded with the API state.
- **Last visible review.** The member publishes A's MCF, deletes `Export A`
  with Keep selected, and reloads both the 1440 and 320 contexts. The panel
  reads `No saved reviews yet.` while `Published files` still lists the EDL and
  MCF with `Delete`. Discovery returns those two IDs with a null review ID and
  `serving`; SQL agrees, and each disk hash equals its `content_sha256` and is
  unchanged by the deletion.
- **Second item.** The synthetic transport adds a second movie over a scratch
  copy `zzz-export-second`, and the owner imports an EDL review for it, hidden
  from the member. Its page shows no saved reviews, no `Published files`, and no
  delete buttons; its discovery is empty and its review reads 404 for the
  member. Opening the first movie again restores both retained rows.

`export-ui-result.json`, `export-ui-http-statuses.json`, and the matching
screenshots hold the proof. Component tests in `web/src/review/*.test.tsx`
cover every condition label, admin targets without default rights, and
deletion retries.

## Automatic Personal Regeneration

Run the existing executable entry point on an isolated source instance:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs auth --reviews --ports=9140-9159
```

The imported `scripts/lib/export-regeneration-journey.mjs` extends the current-owner
policy journey. It uses authenticated policy removal, not a test endpoint or DB
mutation. The instance owner's personal EDL and MCF are sourced from a different
member's review. Queued discovery must stay serving. Poll until both publications
succeed at the member's new policy revision. Record disk hashes during polling,
then compare downloaded, stored, and on-disk bytes independently. Require new
publication UUIDs/revisions, unchanged saved review and source snapshots, preserved
source-owner attribution, and system audit events. The surrounding journey
reopens at 1440 and 320 pixels and verifies unchanged media bytes.

Retain `export-regeneration-result.json`, `review-policy-result.json`, screenshots,
ARIA snapshots, HTTP status records, `doctor.json`, `cleanup.json`, and
`redaction.json` under the printed evidence directory. Cleanup must stop the
recorded group, remove scratch, and preserve evidence; a stopped doctor must
fail because that group is absent. Keep the run’s evidence directory.
Do not edit Python during measurement; source reload invalidates the sample.

Run `tests/test_export_regeneration.py` and
`tests/test_export_regeneration_recovery.py` for injected preparation/write/audit
failures, personal withdrawal with unchanged default fallback, explicit retry,
policy/owner/UUID races, deletion and reused IDs, cancellation, retained deleted
reviews, preserved publication scan links, and process exits before/after DB commit. They use
real scratch bytes and SQLite. Fault injection is deterministic pytest evidence,
not a browser failure claim. `tests/test_audit_visibility_regressions.py` proves
current action-capability removal hides regeneration details and counts.
No live provider, installed plugin, external writer, or new export UI is proved.

Run `tests/test_export_regeneration_failures.py` for two-job poison-media,
foreign-byte, unsafe-path, and journal isolation, edited or incomplete live drafts,
transient infrastructure retries, bounded fair attempts, unlocked startup discovery
with lock-time rechecks, configured data-directory maintenance, and constant SQL
statement counts for idle claims and scheduler ticks. Regeneration uses the
published snapshot, not later live edits. Claims retry at most five times with
backoff. Startup and hourly sweeps discover stale stamps outside writer locks;
ordinary ticks select only due open jobs. These are deterministic checks, not a
latency benchmark. No new retry route is involved.

Run `tests/test_export_regeneration_boundaries.py` for a redirected media library
refused before any journal recovery, a journal that cannot be inspected staying
local to its publication, a denied withdrawal keeping its owned bytes until removal
is durable, and an idle claim whose SQLite VM work and query plan stay indexed and
flat at 1, 101, and 1,101 publications. Migration `0022_export_regenerations` moves
job state into `media_item_export_regenerations`; the journey reads it there.
`tests/test_export_regeneration_migration.py` and
`tests/test_export_regeneration_backup.py` cover exact 0021 job transfer, refusal
with snapshot recovery, hard-exit recovery, and full, settings, and restored backups.

During polling, a status GET can return `409 stale_export_revision` when a
publication changes during authorization. Retry only that exact read conflict;
all other errors still fail, and the poll deadline stays unchanged. Run
`node --test .github/skills/verify-editarr/scripts/lib/export-regeneration-journey.test.mjs`
for the helper's synthetic HTTP-boundary checks. Preserve failed live evidence
before rerunning the real-app command.

## Write Authority And Imported Fields

Write-boundary regression tests use the established synthetic Jellyfin adapter
with real HTTP handlers and isolated databases. Run
`tests/test_review_write_boundaries.py` for rename, create, and duplicate races:
commit a restrictive account snapshot immediately before the writer lock, then
require refusal, a fresh hidden read, and unchanged counts/revisions. Generation,
clock-fence, and restore-epoch changes must refresh evidence outside the lock;
continuous changes must refuse after at most three attempts.

Run `tests/test_review_import_permissions.py` and the MCF credits-denial cases in
`tests/test_review_access_workflows.py` for member and admin uploads and own/admin
copies. A denied content-field capability must refuse atomically rather than
importing a partial review; accepted credits additionally require credits-edit.
Positive controls must preserve imported/copied credits and ordinary content.
These checks complement `auth --reviews`; its browser journey does not prove
linked-account authority races or every nonowner capability denial. Run
`tests/test_scan_http_write_boundaries.py` and `tests/test_scan_job_*.py` after
changes to the shared scan/review write boundary.

## Current-owner policy

Run `node .github/skills/verify-editarr/scripts/verify.mjs auth --reviews`.
The existing launch, doctor, authentication, evidence, and cleanup sequence owns
the new journey too.

The extension creates a member as a Jellyfin user on the run's Jellyfin
(`addJellyfinUser`), whose first Jellyfin sign-in creates the account. That
member imports a synthetic MCF and saves an
unselected, Low-severity base. The instance owner enforces a Deny rule and High
severity for the member. Protected reads must show the enforced result and the
unchanged base. Stale save and export tokens must return 409. The instance owner
then exports the member's review in both formats under the member's policy.
Independent read-only SQL checks saved and source values. The instance-owner
browser saves, exports both formats, and duplicates with the member's current
policy tokens. After enforcement is removed, a stale browser save must return
409, reload current detail, and allow an explicit retry with fresh tokens.
A second review with removed evidence must remain readable and renameable,
refuse both formats, reject membership removal and an unrelated-time repair,
still refuse both formats, and accept a genuine repair save. Actual sidecar bytes and hashes,
a 1440px reopen, and a 320px reopen prove the result and restoration without
changing media bytes. The saved-review journey also drives real member upload, timing, saved-policy
locks, non-enforced personal severity edits, and locked mode visibility at 1440
and 320. After enforcement removal it saves Copy and Live mode through the
member UI and checks fresh protected reads. Owner copy changes policy owner,
preserves original authorship, and refreshes effective values. A stale browser
draft remains visible beside fresh detail until an explicit save with new tokens.

For all catalog review entry points, run
`VERIFY_PORT_MIN=9120 VERIFY_PORT_MAX=9139 node .github/skills/verify-editarr/scripts/scan-ui.mjs --reviews`.
This uses synthetic provider transports with real authenticated UI, HTTP handlers,
scratch databases, FFmpeg clips, and completed chapter-based credits detection.
It proves upload, retime/save, credits accept/save, copy, rename, delete, and
guessed private-review refusal for Jellyfin at both widths.
It does not certify live providers, model inference, or installed runtimes.
Both runners accept `VERIFY_EVIDENCE_ROOT` for an evidence directory for the isolated run.
Do not edit Python during measurement; retain failures and require zero reloads.

A review detached before enforcement must remain valid after enforcement changes.
Current bindings and currently required detached tombstones define the evidence
floor; optional historical membership does not. Copies evaluate retained
tombstones under the destination owner's policy. Coverage requires exact interval
containment after numeric reconciliation, channel supersets, and Skip covering Mute.
Source-backed bindings retain immutable source coordinates and source synchronization;
copies preserve that basis and ownership changes do not retime it. Their exact outward
floor never moves between saves. Endpoints within
`16 * machine_epsilon * maximum_absolute_operand` snap to that floor in preparation
and plain saves as well as retime. The operands are the submitted endpoint, fixed
floor endpoint, and target synchronization endpoints. Read/export preparation does
not rewrite base rows; accepted saves store the floor, never an inward endpoint.
Run `tests/test_review_policy_roundoff.py` for the permanent
`test_head_optional_retime_then_enforcement` and
`test_legacy_nearest_import_cues_under_source_basis_binding` regressions, including
real 0018-to-0019 upgrade and recovery. Both require successful read, save, and
outward EDL/MCF export. The same module checks original 422 runtime messages and
retime filter IDs. Optional imports and retimes retain original nearest values.
Saved-metadata-only requirements
compare exactly against the previously stored interval. Synchronization changes use exact
rational affine minimums rounded outward to floats. Every inverse-mapped stored
interval must cover its predecessor, so repeated saves cannot erode the original
requirement. No minimum synchronization span is imposed. Unrepresentable outward
coverage outside the runtime refuses with `409 review_policy_timing_precision`.
Run `tests/test_review_policy_saved_floor.py` for the named authenticated
30-save regression, five seeded random sub-tolerance sequences with actual
EDL/MCF bytes, and repeated Adjust-cue-times saves with and without widening.
The named `test_near_zero_and_hour_scale_there_and_back` covers 24 seeds at
millisecond and six-hour cue positions, including twelve-hour synchronizations.
It compares ten chained previews with the server-mapped floor, then returns,
saves, and checks exact stored endpoints and EDL/MCF bytes. The same module
checks that the operand-scale roundoff bound stays below `1e-9` seconds at
twelve hours, while same-synchronization one-ULP narrowing still refuses.
Run `tests/test_review_policy_mapping.py` for
`test_compressed_round_trips_narrow_the_saved_floor`, which submits the reviewer's
two-save tiny-window sequences and requires coverage rather than erosion. The
same module performs two 2,000-save adversarial walks using server-only values.
Every accepted save must cover the original interval under an independently
computed exact inverse transform. Open-ended floors and unrepresentable outward
coverage have separate checks. Round-four through round-nine reviewer probes
retain their refusal and retime cases with outward-floor numeric expectations.
Run `tests/test_review_policy_producers.py` for the named
`test_imported_initial_binding_preserves_source_after_return`, with twelve-hour
MCF input, one-second/10-ms/one-microsecond targets, and both two-second and
one-millisecond cues. Independent exact inverses must cover immutable source
endpoints at binding, imported snapshot, and final save. Real EDL and parseable
MCF bytes must cover the source both while compressed and after returning.
The module enumerates every current binding/filter constructor, exact-mapper
consumer, and lifecycle guard for creation, imports, save, duplicate/transfer,
retime, credits, Apply, and both exports. A new producer requires an explicit
audit and inventory update. Required output intervals and MCF material bounds
round outward to milliseconds without changing base rows; optional output and
retime behavior remain unchanged. `tests/test_review_policy_formats.py` checks
that runtime precision refusals retain previous sidecars and export revisions.
Run `tests/test_review_policy_migration.py` and the backup tests after 0019
changes; source-basis backfill, all supported upgrades, interruption, unknown
schemas, verified recovery, and full/settings backup restoration must pass.
The journey deletes the repaired unpublished review and a separate imported
review plus its scan through authenticated production routes. Independent
read-only SQL must find no orphaned bindings or integrity failures, and schema
admission must still accept the database before unrelated defaults are edited.
After policy edits and repaired-review deletion, repeat the foreign-key and
ledger checks. Mutable-default schema admission is a separate baseline defect
until the coordinator integrates the schema-seed repair. Retain
`review-policy-deletion-admission.json` with the normal evidence.

Retain `review-policy-result.json`, `policy.edl`, `policy.mcf`, masked screenshots,
ARIA snapshots, and the normal cleanup and redaction records. This journey does
not certify the nonowner UI, live providers, inference, installed runtimes, or the
plugin installation. Run `tests/test_review_policy_*.py` for manual and import origins,
metadata and evidence bypasses, copy and owner transitions, policy races, preview,
Apply, retiming, credits, and community artifact preparation.

## Plugin Interoperability

Run from this checkout with the public plugin checkout beside it, or name it:

```bash
VERIFY_PLUGIN_CHECKOUT=../jellyfin-edl-public VERIFY_PLUGIN_PORT=9239 \
  node .github/skills/verify-editarr/scripts/verify.mjs auth --plugin-interop --ports=9220-9229
```

Prerequisites are the `auth` ones plus Docker, `python3`, the cached
`mcr.microsoft.com/dotnet/sdk:10.0` image, and the digest-pinned Jellyfin 12.0.0
image, as the plugin's verify-jellyfin-edl skill lists. A missing plugin helper,
Docker daemon, or image exits 2 BLOCKED. A BLOCKED run is not live evidence.
`VERIFY_PLUGIN_LABELS=KEY=VALUE[,...]` adds Docker labels to identify the isolated run.

`scripts/lib/plugin-interop-journey.mjs` builds a media tree in the run's scratch
(`Movies/{Interop Movie, Versions 1080p and 2160p, Policy, Transfer}` and
`Restricted/Hidden`, all `synthetic-av-v1`), writes distinct legacy adjacent
`Interop Movie.edl` and `.mcf`, and launches the plugin's Jellyfin with
`verify.py launch --interop` over that tree at the same absolute path. Editarr's
first-run setup then connects to that Jellyfin as `jfedl-admin`, which becomes the
Jellyfin-linked owner and confirms every fresh action with its Jellyfin password.
The run starts no other Jellyfin. The journey drives only production HTTP routes:

Alice and bob are the plugin Jellyfin's `jfedl-userA` and `jfedl-userB`, admitted
by their first sign-in; carol and dave are new users created on that Jellyfin.
There is no account linking.

1. The owner publishes item defaults; alice and bob import reviews. In the
   `before-personal` phase users A and B and the API key receive the default.
2. In the `publishing` phase, alice and bob publish personal files under their
   Jellyfin user IDs as export IDs; setup linked the owner to `jfedl-admin`.
   Polls during publishing must move each user from the default straight to its
   personal file with no other answer. The `after-personal` matrix covers
   both formats for each user, the API key, the server admin, cross-user `ETag`
   reuse, a selected 1080p version, the unpublished 2160p version, a foreign media
   source, and a library hidden from user B.
3. The owner exports dave's review to itself (policy owner dave). Removing dave's
   enforced policy regenerates both files while polls see only the old and new
   bytes. Re-enforcing with the media moved away withdraws both files
   (`write_failed`). A plugin poll runs through that transition. Its answer
   timeline must show the personal file, then only the default. The default
   must first arrive after the policy change was sent and after every Editarr
   poll that still reported the file serving, so a failure faster than Editarr's
   first poll still passes. The server admin then gets the item default.
4. Bob enforces a drugs Deny with high drugs severity on himself, while the
   owner's policy cannot be enforced. The owner copies bob's two-cue review whose
   drugs cue is saved unselected and publishes the copy to itself (policy owner
   the owner, original author bob). Both files must select only the violence cue
   at low severity. Carol enforces high violence severity and alice the drugs
   policy; carol's account is deleted with transfer to alice, who publishes the
   transferred review (policy owner alice, original author carol). Both files
   must select violence at low and drugs at high severity. The parsed cues are
   checked before the plugin compares bytes.
5. Bob deletes his review retaining both files, and the owner deletes alice's
   account in retain mode. Every retained file keeps serving the same bytes.
6. An invalid personal EDL and MCF return 422 and a mode 000 personal MCF returns
   500 (or its bytes where the server can read it), never the default; restored
   files serve again. Legacy files keep their digests and are never served.

Each expectation is the published file's SHA-256, checked against the
publication's `content_sha256` and the export-layout path first. Evidence is
`plugin-interop-result.json` (both commits, plugin DLL hash, image digest,
synthetic media, export-layout manifest, and legacy digests, every phase and
publication, the copy and transfer policies and parsed cues),
`plugin-matrix-*.json`, `plugin-interop-regeneration.json`,
`plugin-interop-withdrawal.json` (Editarr state spans and the plugin timeline,
with epoch-millisecond send and receipt times), `plugin-launch.log`, `plugin-interop-http-statuses.json`, and
`plugin-interop-cleanup.json`. A phase passes only when its helper exits 0 and
writes a new passing record naming exactly its cases. Cleanup runs
`verify.py cleanup-label editarr.verify.run=<run>`, so a launch that failed or
timed out partway is still reclaimed. Ctrl-C or SIGTERM kills every running
plugin helper's process group at once and starts that cleanup. The plugin run keeps its own evidence and a
`counterpart.json` naming the Editarr commit. Run
`node --test .github/skills/verify-editarr/scripts/lib/plugin-interop-support.test.mjs`
for the helper boundary. The journey does not certify other Jellyfin versions,
Windows or Linux hosts, or plugin behavior that `verify.py run` already drives.
