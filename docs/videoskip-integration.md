# VideoSkip import and export

Editarr accepts VideoSkip `.skp` files as local scan evidence. Open a Jellyfin
item, select the intended version, and choose **Scans → Import → VideoSkip**.
Enter the Exchange listing URL and uploader when you have them. After upload,
use **Verify for this version** to check the original timing or align the cues.
The verification dialog displays the source reference timestamp and description
when present. VideoSkip’s platform offsets are source evidence and require
checking against the actual cut. [VideoSkip synchronization guide](https://videoskip.org/mobile/).

The importer supports skip and mute. It reports malformed cues and actions that
Editarr cannot play, including blur, blank and fast-forward. The scan stores the
source filename, checksum, optional attribution, reference timestamp and
description, platform offsets, and any reference-image checksum. The original
`.skp` is the copy to keep if you need the reference image itself.

API clients can send multipart `file` and JSON-encoded `options` to
`POST /api/videoskip-imports/upload`. Options take the same `target` and `name`
as an EDL import, plus optional `attribution.source_url` and
`attribution.uploader`. The route requires the normal upload and review-content
permissions. A successful upload returns the pending `scan`, a null `review`,
the issue count, and up to 100 line-numbered issues.

## Download a VideoSkip file

Open a saved review and choose **Publish → Files → VideoSkip (.skp) → Export
VideoSkip**. You can add a reference timestamp and description from the same
cut. Use **Check compatibility** to see how many cues make it into the file
and where the format requires a broader category, a default rating, or an
omission. Then choose **Download VideoSkip**.

The file uses the reviewed media timeline with millisecond timestamps. Editarr
exports the effective selected cues, including any cues enforced by the review
owner’s policy. It maps Low, Medium, and High severity to VideoSkip ratings 1,
2, and 3. A skip uses the category and rating alone. A mute adds the `audio`
label. Overlapping cues retain their individual intervals. [VideoSkip cue format](https://videoskip.weebly.com/faq.html)

An optional reference timestamp and description identify a point on the
reviewed cut. A file with no reference starts at its first cue. Its cue times
still use the reviewed cut. An export contains only the generated skip list and
the reference details you enter. Use the original `.skp` to recover imported
reference images or platform offsets.

API clients can `POST` to `/api/reviews/{review_id}/exports/videoskip/preflight`
or `/api/reviews/{review_id}/exports/videoskip/download`. Both take the saved
review and policy revision tokens used for EDL/MCF downloads and optional
`reference_seconds` and `reference_description` fields. The download endpoint
returns a UTF-8 `.skp` attachment. See the [API contract](api-contract.md).

## Local seed datasets

Generate the datasets from the archived source files.

```sh
python -m scanner.commands.external_seed_cli \
  --videoskip research/videoskip-exchange/manifest.json \
  --filmiway research/filmiway-recovered.json \
  --output research/editarr-seed
```

- `videoskip.json` contains one record per listing, including alternative edits
  for the same title. Each record links to the original `.skp`, which carries
  any embedded reference image. Source timings, labels, handling actions,
  regions, offsets and provenance accompany the mapped Editarr filters. Use
  the referenced `.skp` and its attribution for an individual import.
- `filmiway.json` preserves source URLs, edition labels, reported runtimes,
  untimed advisories, all recovered marks, and unavailable guide URLs.
  Load the individual files in `filmiway-mcf/` through **Import → MCF** and
  verify the cut before using them. MCF files use the reported whole-minute
  runtime for their source bounds. Marks beyond those bounds stay in the JSON
  for manual review.

These files are local review candidates. Use the checksum or source ID to trace
a record back to its contributor. Editarr's operator confirmed reuse permission
for VideoSkip based on the [creator's permission comment](https://github.com/rdp/sensible-cinema/issues/29).
Filmiway's redistribution permission is unverified.

## Community database seeding

`research/editarr-seed/catalog-crosswalk.json` maps archive records to TMDB
and TVDB with IMDb cross-references and evidence URLs. Episode work identifiers
use the provider's series ID plus season and episode. The source attribution
retains the archive title and numbering, including for shorts and specials.

Run `python -m scanner.commands.community_seed_cli` to produce the two
`*-community.json` bundles and `community-seed-review.json`. The report links
incomplete cues and actions outside the community format to their source.

The offline seed command stores each source record on its own candidate
timeline, labeled **VideoSkip** or **Filmiway**. It preserves attribution, release
and sync information in `external_seed_submissions`. Each unique filter gets
one counted source attestation. Archive timing remains in source coordinates.
VideoSkip runtime stays unknown where the archive provides no runtime.

Build the community-service image with migration 0005. On the deployment host,
stop the API and run the documented verified-backup upgrade for an existing
database. Mount the reviewed bundles and the non-secret service config
read-only in the maintenance container. Use fresh backup filenames.

```sh
docker compose -f community-service/compose.yaml run --rm --no-deps \
  -v "$PWD/research/editarr-seed:/seeds:ro" \
  -v "$COMMUNITY_CONFIG_FILE:/etc/community/service.json:ro" \
  -e COMMUNITY_CONFIG_FILE=/etc/community/service.json \
  maintenance seed --confirm --destination /backups/before-external-seed.sqlite \
  --service https://community.editarr.org /seeds/videoskip-community.json
```

The command verifies its backup before writing. An identical bundle can be
retried with a fresh backup filename. The command refuses conflicting updates
to previously seeded source records. Repeat with the Filmiway bundle once
publication is cleared, then restart the community API.

## Export to the VideoSkip Exchange

### Planned submission workflow

- Give users the generated file and the matched Exchange title page. Resolve
  films by IMDb ID where available. Confirm year and cut, and confirm season
  and episode for TV. Show the chosen release and credited contributors before
  the handoff.
- Start with manual upload under the contributor’s own VideoSkip account. The
  Exchange requires sign-in to upload. Record the returned listing URL only
  after the contributor confirms the upload. [Exchange posting instructions](https://videoskip.herokuapp.com/exchange/howto/post/).
- For a later direct integration, confirm a supported submission interface,
  authentication and terms with the maintainers. Then add explicit approval for
  each upload, duplicate detection, handling for a missing title or expired
  session, and verification of the resulting public listing. Exercise failure
  and retry handling with a mock or maintainer-approved test account.

## Future investigation for Editarr and players

Evaluate how to attach a user-selected reference image and carry sync offsets
for specific cuts once they have been checked against the same reference.

Investigate preserving and editing VideoSkip's video blanking, video blur, and
fast-forward actions end to end. Blanking and blur can apply to the full frame
or a fixed region while audio continues. [VideoSkip player documentation](https://videoskip.org/mobile/).

- **Editarr.** Evaluate how to store these actions and region coordinates, edit
  and preview them in saved reviews, and preserve them through import and
  export. Check which interchange formats can carry each action.
- **Playback.** Prototype the actions in a compatible player. Check how the
  player scales regions with the picture, restores playback speed and picture
  at cue boundaries, and handles seeking and overlapping cues. Determine how
  Editarr and the player can communicate supported actions.
