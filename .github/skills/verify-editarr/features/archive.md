# Archive

Editarr no longer connects to Radarr or Sonarr, and every media item now comes
from Jellyfin. Migration `0025_archive_arr_sources` moves every former Radarr and
Sonarr media item, with its scans, saved reviews, and exports, into a read-only
archive (`media_items.archived_source`). It keeps `arr_settings` rows but drops
their `api_key` column, and moves Radarr and Sonarr media-access grants to
`archived_media_access_rows`. Migration `0026_archive_manual_files` does the same
for Other Files items (`server_id` `editarr-manual`, archived source
`manual_files`): it cancels their unfinished scans and drops them from the queue,
and moves Other Files scope grants and every unmatched-media exception to
`archived_media_access_rows` before dropping `media_access_exceptions`.

## Sub-features

- `archive-view`: `#/archive` lists each archived item with its source label
  (Radarr, Sonarr, or Other Files), title (an Other Files item keeps its original
  file name), the series and `Sxx Eyy` of a Sonarr episode (from an MCF document
  or a standard Sonarr file path), and its scans, reviews (with owner), and
  exports, 20 items per page with **Previous page** and **Next page** past the
  first page. The path appears only with `media.paths.view`.
- `archive-access`: `GET /api/archive` and the **Archive** sidebar entry require
  `archive.view`. Only admins and the owner hold it, and the owner cannot grant
  it to an ordinary user (422 `role_fixed_capability`). An ordinary user gets 403
  and sees no sidebar entry.
- `archive-read-only`: the view offers no scan, edit, export, or delete action,
  and no other route returns an archived item.
- `archive-orphan-import`: an EDL or MCF import aimed at a former Other Files
  path (`{"source": "manual", "video_path": ...}`) gets 400
  `jellyfin_item_required` and writes nothing.

## How to get to it (user POV)

- The **Archive** entry in the sidebar, or `/#/archive` directly.

## Run the recipe

`verify.mjs upgrade` seeds an Other Files item at `0009_readable_api_key` (see
the [skill](../SKILL.md)). Its first run upgrades it at startup with an unlinked
owner, so it checks only that the database archives it. Its second run migrates
the same seed offline to `0029_jellyfin_user_blocks` and links user 1 to the run's
Jellyfin administrator (`scripts/review_repairs_fixture.py`), so startup applies
only `0030_remove_passwords`. That run proves the Other Files parts of steps 3 and
4 through `GET /api/archive`, the orphan upload refusal, and Archive at 1440 and
320 signed in with Jellyfin. The Radarr and Sonarr seed and steps 5 and 6 still
need a manual run. A seeded owner without a Jellyfin link comes out disabled with
cause `unlinked` and nobody can sign in, so link user 1 the same way before
startup. Launch a seeded data directory yourself as below, and record each step as
the [feature map](./README.md) describes.

1. Seed a database at the parent revision. From the repository root, with a
   scratch directory `$SCRATCH`, run
   `tests.test_arr_archive_migration._with_arr_records(Path("$SCRATCH"), "0024_account_dispositions")`
   under `.venv/bin/python` with `PYTHONPATH` set to the checkout and
   `EDITARR_DATA_DIR` and `EDITARR_LOG_DIR` under `$SCRATCH`. It writes
   `$SCRATCH/data/scanner.db` at `0024_account_dispositions` with owner
   `fixture-admin`, one Radarr movie, one Sonarr episode (`Pilot`), four scans,
   two saved reviews, and three exports. For Other Files records,
   seed a second scratch with
   `tests.activity_migration_support.build_activity_database(Path("$SCRATCH/data/scanner.db"), Path("$SCRATCH/media"), "0009_readable_api_key", queued_status="awaiting_model_consent")`:
   one Other Files item `Movie.mkv`, three scans (one queued), two saved
   reviews, and two legacy exports, with the same owner.
2. Start the source app on that data directory with
   `EDITARR_DATA_DIR=$SCRATCH/data EDITARR_LOG_DIR=$SCRATCH/logs .venv/bin/python
   scripts/run_development.py dev --web-host 127.0.0.1 --web-port WEB_PORT
   --api-host 127.0.0.1 --api-port API_PORT`, with two unused ports from your
   assigned range. Startup upgrades the database through snapshot-backed
   adoption. Require `alembic_version` to read `0030_remove_passwords`.
3. Sign in as the owner at 1440x900 and open **Archive**. Require one row per
   seeded item with its source label (Radarr, Sonarr, or Other Files) and title,
   each with its scans, reviews and owners, and exports. The Other Files row is
   titled `Movie.mkv` and its queued scan reads `cancelled`. Capture a
   screenshot and ARIA snapshot. Repeat at 320x900 and require no horizontal
   overflow.
4. Require no button or link that scans, edits, exports, or deletes. Call the
   live item, scan, review, and export routes with an archived ID and require a
   refusal and an unchanged read-only database inventory. For the Other Files
   seed, `POST /api/edl-imports/upload` and `POST /api/mcf-imports/upload` with
   target `{"source": "manual", "video_path": "$SCRATCH/media/Movie.mkv"}` must
   each get 400 `jellyfin_item_required` with the inventory unchanged.
5. Sign a new Jellyfin user from the linked server in, and require
   no **Archive** sidebar entry, `/#/archive` showing **Page unavailable**, and
   `GET /api/archive` returning 403.
6. Open the database read-only. Require the `arr_settings` rows to remain with
   no `api_key` column, no `media_access_exceptions` table, and `scan_queue`
   holding no archived item's scan.
7. Stop the process group you started and remove `$SCRATCH`, keeping evidence.

## Proof

Record the seeded and upgraded inventories, the archive response, and screenshots
at both widths. `tests/test_arr_archive_migration.py` and
`tests/test_archive_api.py` cover the same rules without a browser; they do not
replace this recipe.
