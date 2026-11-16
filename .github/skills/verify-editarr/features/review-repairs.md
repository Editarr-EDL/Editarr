# Deleted Scan Recovery

Run from the checkout with installed dependencies:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs repairs --ports=9240-9259
```

`repairs` uses the same port pool, launch, doctor, masked captures, redaction,
and cleanup as `auth`. It starts a fresh isolated app for each width, 1440 and
320. It never attaches to installed data. The fixture is the migration suite's
frozen `0009_readable_api_key` shape with its version table omitted, not a schema
invented by the verifier. Exact admission rejects modified or unknown shapes.
Each width also starts a run Jellyfin and mints an Editarr key in it.

## Fixture And Checks

`review_repairs_fixture.py` reuses `build_activity_database` and `_clone` from
the migration tests. It seeds one deleted community scan with an import, target,
finished operation, membership, and copied cue; a deleted local scan referenced
only by a copied filter; and a healthy review. Orphan stage coverage, credits
detection, and MCF rows reproduce the CASCADE children left by historical deletes.
An unused community import and an import with a pending operation survive.
The synthetic queue waits for model consent and never performs inference.
Because 0026 archives Other Files items, the fixture rebinds its one media item
and every version column to `FILM` from `scripts/lib/synthetic-jellyfin.mjs`
under the run's loopback transport URL. It then upgrades the database with the
real migrations to `0029_jellyfin_user_blocks`, the last revision with local
passwords, links user 1 to the run Jellyfin's administrator, and stores that
server's URL and key, which it reads from stdin. Startup therefore applies only
`0030_remove_passwords` from its snapshot, and the owner stays active. The owner
signs in with Jellyfin, reconnects the synthetic transport, and drives the item's
page.

The journey requires a complete real startup migration, a retained snapshot
whose table counts and FK violations exactly match the source, two repair records,
and a clean head FK check. Red exclamation icons and errors occupy separate rows
under their disabled review openers. The healthy review remains enabled and its
full API detail is unchanged after the other reviews are resolved.

The local dialog has two radio options. The community dialog has three. With
explicit resource bounds but no enrolled identity, re-download returns 409
`community_scan_unavailable` with `not_connected`; the option becomes disabled
with the plan's explanation and server reason below it. Recovery retains the cue
on a hand-made `Manual` scan with null finding provenance. The browser opens and
renames the recovered review, and independent API reads verify its unchanged
cues. Delete removes the local review. The scan delete pre-check permits the
unused import and blocks the pending operation with the exact wait message.

Owner-account protection hides owner reviews from ordinary users. For the save
denial probe only, the fixture helper copies one flagged review to
`repair-reader`, a Jellyfin user admitted by its first sign-in, inside the run's
marked scratch database. The helper requires
that marker, verifies the account role, and uses a foreign-key-enforced immediate
transaction. It never edits credentials or grants. Jellyfin sign-in, permission
changes, GET 409, POST recover 403, and deletion all use production APIs.
The temporary copy is removed before the remaining browser actions.

## Retained Evidence

Each printed evidence directory contains `fixture.json`, `migration.json`,
`doctor.json`, `instance.json`, `row-geometry.json`, `flagged-api.json`,
`recover-denied.json`, `community-unavailable.json`, `recovered.json`,
`http-statuses.json`, `result.json`, `axe.json`, screenshots, and ARIA snapshots.
The eight checkpoints are `flagged-list`, `local-options`, `community-options`,
`community-unavailable`, `recovered-editable`, `local-deleted`,
`unused-delete-precheck`, and `pending-delete-blocked`.

`cleanup.json` requires stopped processes and absent scratch. `redaction.json`
requires no retained secret. `repairs-runs.json` names both runs and compares
`cache-before.json` with `cache-after.json`, SHA256 hashes of the recursively
sorted host Editarr cache listing, sizes, modes, links, and mtimes. No cache file
content is opened. The only Docker resource is each run's Jellyfin container,
which stops with that run.

Successful live community re-download is not covered without an approved real
service and enrolled identity. Signed download, mapping, rollback, and retry
behavior remain covered by `tests/test_review_community_repairs.py` fake-transport
tests. This journey does not certify installed packages, real media, or inference.