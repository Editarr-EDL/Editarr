# Media Access

Feature `media-access` (MU-11). `scanner/security/media_access` decides which
Jellyfin items an account may reach and changes local scopes. MU-15's management endpoints
and MU-20's catalog routes call it. The real-app journeys are
[account access controls](multi-user.md#account-access-controls-mu-30) for
scope editing, [catalog access](catalog-access.md) for the
catalog routes, and [identity isolation](identity-isolation.md) for six
accounts with different local scopes and Jellyfin policies.

## What exists

- `decide_media(subject, targets, *, user, catalog, libraries, items, now)`
  returns one `MediaDecision` per target. `load_subject(db, account)` reads the
  owner, local scope, or linked identity. Build targets with
  `jellyfin_target` from the server's record; path correspondence tests build a
  path-only `MediaTarget` directly. For a linked account pass three reads of the linked server, or the
  adapter's `Unverified`: `user` from `adapter.verify_user(identity)`,
  `catalog` as `JellyfinCatalogIndex(adapter.catalog(server_id), resolve)`, and
  `libraries` as `LibraryRoots(adapter.library_folders(server_id), resolve)`.
  Any missing, failed, foreign, or over-24-hour read denies every linked target.
  The catalog is two listings sorted by `DateCreated,SortName`, both with
  `CollapseBoxSetItems=false` so a collection never replaces its movies or
  series: every media file item, selected by `MediaTypes=Video,Audio,Photo,Book`
  so local trailers, extras, songs, and unknown kinds are indexed, and every
  `Series` for episode context. A listed `PartCount` above one is matched by
  `/Videos/{id}/AdditionalParts`, whose paths join that item's versions. Items
  of media type `Unknown` (folders, series, collections, playlists) are not
  media files Editarr scans or serves and are never targets. The adapter
  rereads each listing's first page after its last; a changed total or first
  page, or parts that disagree with their count, makes the catalog `Unverified`.
- A file inside a library folder must lie only in libraries the user's
  Jellyfin policy opens and be a listed Jellyfin item's file, and every
  matched item must allow the user. A file proven to lie outside every folder
  that matches nothing in the complete listing is `unmatched`. A library folder
  missing from this host (an unmounted library) only stops that proof: such a
  file is `unresolved`, and files in mounted folders keep their decisions. A `boxsets` library is Jellyfin's
  Collections library (`/config/data/collections`), which holds collection
  metadata and no media. `parse_library_folders` leaves it out, so it never
  stops the proof and never contains a target.
- `management.set_default_scope` and `set_user_scope` check the caller with
  fresh interactive authentication.
- Revision `0014_media_access_exceptions` added owner exceptions for Other
  Files; `0026_archive_manual_files` moves them and Other Files scope grants to
  `archived_media_access_rows` and drops the table.

## Deterministic checks

Every Python run sets `PYTHONPATH` to the checkout and scratch `TMPDIR`,
`EDITARR_DATA_DIR`, and `EDITARR_LOG_DIR`. The fixture writes empty synthetic
files under `TMPDIR` and removes them at exit.

```bash
.venv/bin/python -m pytest -q tests/test_media_access_matrix.py \
  tests/test_media_access_local.py \
  tests/test_media_access_containment.py tests/test_media_access_libraries.py \
  tests/test_media_access_storage.py tests/test_media_access_migration.py \
  tests/test_media_access_evidence.py tests/test_media_access_review3.py \
  tests/test_media_access_recorded_listing.py tests/test_media_access_aliases.py \
  tests/test_media_access_grouping.py tests/test_media_access_live_cleanup.py
```

- The containment tests build a library folder and an outside folder under the
  pytest temp directory. They prove that symlinked, case, and mapped spellings
  of a library folder contain its files, that a hard link is judged by where it
  lies, and that a missing, relative, unreadable, or zero-ID path, or an
  unreadable or zero-ID folder, is `unresolved`. A folder missing from the host
  leaves files in mounted folders decided as usual, closed libraries included,
  and turns an unmatched outside file into `unresolved`. Two cases replace the library folder or move the
  target into it between the two identity reads and expect `unresolved`.
  The case cases skip on a case-sensitive filesystem;
  the unreadable cases skip when run as root. The Windows key case swaps in
  `ntpath` rules on a POSIX host; it is not a native Windows run.
- The library tests drive the real adapter's `/Library/VirtualFolders` read
  over a scripted transport, including a server replaced mid-read, and pin the
  current, stale, foreign, failed, and missing read outcomes. Over the recorded
  folders, an unmounted Collections library leaves outside files `unmatched`
  while an unmounted Songs library makes them `unresolved`, and a
  file in a mounted collections folder is outside while one in Movies is
  `not_in_scope`.
- The evidence tests reject every incomplete or inconsistent `/Items` listing,
  drive the adapter's paged listing over a scripted transport, deny foreign,
  stale, or missing catalog and user reads, and show a closed library
  restricting a file no catalog item names.
- `test_media_access_review3.py` holds the third review's probes.
- `test_media_access_aliases.py` links a file in a closed library to an
  outside file, as a Trailer, Video, MusicVideo,
  Audio, AudioBook, Photo, Book, and an unknown kind, and reads the catalog
  through the adapter over a transport that filters `/Items` by type and
  media type as Jellyfin does. Every alias keeps its denial.
- `test_media_access_grouping.py` adds Jellyfin's collection grouping and
  stacked parts to that transport. An outside copy of a collected
  restricted movie and an outside file aliased by a restricted movie's second
  part keep their denials, a collected series keeps its episodes' context, and
  parts that disagree with `PartCount` are refused.
- `test_media_access_live_cleanup.py` drives the live helper's cleanup against
  an in-memory Docker. It removes only resources whose run label and recorded
  container ID or volume creation time both match, keeps and reports a
  recorded name now labelled for another run, a recreated resource under a
  recorded name, and an unrecorded labelled resource, reports a failed
  removal, and the doctor refuses an unexpected mount without adopting it.
- `test_media_access_recorded_listing.py` replays
  `tests/fixtures/multi-user/jellyfin-catalog-v3/recording.json`
  (`editarr-jellyfin-catalog-3`), unmodified server-key responses recorded
  from the pinned Jellyfin 12.0.0 image by the live helper below. The replay
  answers only the exact recorded path and query. It also holds the library
  folders, with the `boxsets` Collections library. The unfiltered listing
  counts one item it never delivers; the file listing holds trailers, extras,
  a song, and a photo but no folders; the grouped controls replace Film A and
  Film B and the series with collections; the stacked movie's second part
  appears only in its `AdditionalParts`; both listings build whole and in
  pages of three, and a changed or failed first-page reread of either is refused.
- The matrix runs the real `JellyfinUserAdapter` against the MU-08 local
  boundary (`editarr-jellyfin-users-1`) for Casey and Blake. That boundary is
  synthetic; passing it is not live Jellyfin evidence.
- Storage tests use real accounts, sessions, API keys, role defaults, and
  password confirmation from `tests/multi_user_access_support.py`.
- Migration tests upgrade every supported revision, inject a failure and a
  hard exit inside 0014, refuse partial, modified, and unknown schemas, recover
  the verified generation, and restore the accounts.

## Real-app proof

`verify.mjs upgrade` shows a launched source instance upgrading a populated
older database to the current head, which includes 0014. It does not exercise
media decisions.

The live adapter check is
[scripts/media_access_live.py](../scripts/media_access_live.py), with its
library and checks in `scripts/media_access_live_drive.py` and its Docker
ownership in `scripts/media_access_live_docker.py`. It needs Docker
and the pinned image
`jellyfin/jellyfin@sha256:baba630419915985442f315f08b0cf46d9f4c8a0cc4bd38e94a6d35751dd5ef5`
(Jellyfin 12.0.0); it never pulls or starts Docker. It is opt-in and not part
of pytest. From a fresh checkout with the shared `.venv`:

```bash
S=$(mktemp -d /private/tmp/media-access-live.XXXXXX)
PYTHONPATH=$PWD EDITARR_DATA_DIR=$S/data EDITARR_LOG_DIR=$S/logs \
  .venv/bin/python .github/skills/verify-editarr/scripts/media_access_live.py run
rm -rf "$S"
```

- Launch. It records `instance.json` before creating anything, refuses a
  volume name that already exists, creates a `/cache` volume and one
  container labelled `editarr.verify.run=<run>`, and saves the volume's
  creation time and the container ID the moment each exists. `/config` is a
  directory in the marked scratch. The checks map only `/media`, so Jellyfin's
  Collections library folder (`/config/data/collections`) is missing on this
  host, as on a host that mounts only media. FFmpeg in
  the same image writes the synthetic media into that scratch.
- Doctor. `doctor.json` requires `/health` `Healthy`, version 12.0.0, a
  running labelled container, and no mounted volume the run does not own; an
  unexpected mount fails the run and is never adopted for removal.
- Drive. Movies, Family, Shows, Home, and Songs libraries hold clips, a
  photo, a song, a local trailer, a behind-the-scenes extra, a featurette, a
  theme video, a special, a stacked movie, a movie merged into another, and
  three symlinks (a Movies trailer, the stacked movie's second part, and a
  Songs track) to files outside every library. Film A and Film B, and Show X,
  form two collections; the run waits until Jellyfin's grouped listings show
  them, re-adding members every 15 seconds. Only Family and Home are open to
  `casey`. `results.json` requires `/Library/VirtualFolders` to report the
  Collections library as `boxsets` at `/config/data/collections`, the grouped
  control to hide Film A and
  Film B, both catalog listings to deliver their totals, the catalog to hold
  Movie, Episode, Series, Video, Trailer, Audio, and Photo items, every
  library file and part to be indexed, Casey's 15 decisions to be right (all
  three outside aliases and the
  closed extra are `not_in_scope`; the Family trailer and the photo are
  allowed), the unaliased outside file to be `unmatched`, an unmounted Music
  library to change only it to `unresolved`, and a movie added mid-listing to make the
  catalog `Unverified` until it settles.
- Evidence. `web/test-results/verify-editarr/media-access-<run>/` keeps
  `instance.json`, `doctor.json`, `results.json` or `failure.json`,
  `cleanup.json`, and `redaction.json`. Passwords, the session token, and the
  API key stay in memory; `redaction.json` counts their appearances in every
  evidence file and must report 0.
- Cleanup. Always runs. It removes a container or volume, found by recorded
  identity or by this run's label, only while its label names this run and
  its container ID or volume creation time equals the recorded one; the
  container is removed by ID. Anything else is kept and listed under `kept`.
  It then removes the marked scratch. `cleanup.json` lists any container or
  volume still labelled for the run, the kept resources, the unlabelled
  volumes that appeared during the run (`newUnlabelledVolumes`, normally
  empty), and errors; `clean` requires nothing labelled for the run to
  remain. `cleanup EVIDENCE_DIRECTORY` repeats it for a run that was killed;
  it is safe to repeat and never removes a newer resource that reuses a
  recorded name.

`run --record` also rewrites the replay fixture after checking that no
password, token, or API key reached it. `run --inject-failure` stops right
after the doctor to rehearse cleanup: it exits 1 with `failure.json` and a
clean `cleanup.json`. Exit 0 means every check held and cleanup is clean;
2 means Docker or the image is missing, and 64 is a usage error.
