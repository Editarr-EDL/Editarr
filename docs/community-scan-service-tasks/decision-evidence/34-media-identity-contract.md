# CSS-34 media identity contract

Implemented October 2, 2026. This is normalization and local confirmation, not
global-work reconciliation, fingerprinting or compatibility policy.

## Public DTO

Catalog discovery adds nullable `media_identity` to Arr items/media items and
Jellyfin items/versions. Existing local paths, IDs and discovery fields remain
local API behavior; they are not copied into this public matching-input DTO.
Jellyfin seasons and unsupported types have no normalized identity.

`MediaIdentity` contains `entity_type` (`movie`, `series`, `episode`),
`identifiers`, `series_identifiers`, `episode_numbers`, `episode_span`, `release`
and nullable `input_requirement`. Identifier entries contain `provider`,
`entity_type`, string `value` and `provenance`. Supported providers are only
`tmdb` and `tvdb`; upstream IMDb fields are ignored. Number entries contain `scheme`
(`aired`, `absolute`, `dvd`, `anime`), nullable `season` and nonnegative `number`.
Season zero is preserved. Alternative order is never converted to aired order.

Release contains nullable `scene_name`, `group`, `edition`, `quality`,
`size_bytes`, `duration` and `audio`. Duration has `seconds`, `source` and
nullable `precision_seconds`; catalog minutes are explicitly approximate,
Jellyfin ticks remain distinct from an actual `file_probe`. Audio has
`language`, `role`, `codec`, `channels`; adapters leave it unknown unless the
caller supplies evidence for the selected track. Scene names do not select audio.
No file probe or fingerprint is triggered by ordinary discovery/confirmation.

## Alias and join rules

- Upstream movie aliases: explicit TMDB; series aliases: explicit TMDB/TVDB.
  Manual association accepts typed TMDB/TVDB IDs only, never IMDb.
- Upstream episode provider aliases remain conservatively omitted. The episode
  uses an exactly linked series record plus explicit numbering; browser-confirmed
  episode IDs are separately labelled `user_confirmed`, never provider-verified.
- Sonarr joins `seriesId` to series `id`; Jellyfin joins `SeriesId` to series `Id`.
  Foreign or absent context yields an actionable `input_requirement`, not an alias.
- Sonarr spans contain siblings with the same positive `episodeFileId` and series.
  File ID zero does not join missing episodes. Detail and list discovery agree.
- Local row IDs, names, paths and filenames are not global identifiers. Aliases
  do not authorize a contributor to merge global works.
- `known_release_conflicts` reports supplied file-duration/audio differences even
  for identical scene/group names. The caller supplies duration tolerance;
  CSS-34 does not choose a global tolerance or fingerprint band.

## Real evidence

[Allowlisted captures](../../../tests/fixtures/community/media-identity-captures-v1.json)
were obtained with bounded authenticated GETs on October 2, 2026 from Radarr
6.4.4.10685, Sonarr 4.0.20.3014 and Jellyfin 12.1.0. Credentials remained
process-private; no raw response file was saved. Names and IDs are pseudonyms;
ID equality, JSON types, numbering and field presence are retained within each
provider. Pseudonym maps reset between providers and prove no cross-provider link.
Paths, addresses, credentials and unknown provider/stream fields are excluded by
[the capture sanitizer](../../../scanner/integrations/media_identity_capture.py).

The bounded Sonarr sample did not contain a shared-file double episode. The
shared-file, foreign-context, file-zero and alternative-order tests are synthetic,
not represented as observed upstream captures. Official Sonarr
[EpisodeResource](https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/Episodes/EpisodeResource.cs)
and Jellyfin
[Episode](https://raw.githubusercontent.com/jellyfin/jellyfin/master/MediaBrowser.Controller/Entities/TV/Episode.cs)
were inspected to distinguish episode records from series context; unequal IDs
alone are not namespace proof. Jellyfin `IndexNumberEnd` expansion is not adopted.

## Other Files and storage

Post-completion clarification, October 2, 2026: The user requires a TMDB/TVDB
URL or ID for **community uploads** from Other Files. Explicit user association
is sufficient; no proof of the file's work identity is required. Reputation and
recipient comparison/confirmation are the trust basis. The subsequent user
clarification restricts all identity to TMDB/TVDB, implemented in normalization,
strict manual input and the editor. URL input is not yet implemented;
CSS-37/46 must add URL normalization during upload preparation. Ordinary local
scanning remains independent. See [G-MATCHING](../decisions.md#g-matching).

Protected `GET /api/manual-files/identity?video_path=...` is passive, file-bound
and returns unknown movie identity if none is saved. `POST` accepts `video_path`,
`entity_type`, typed `identifiers`/`series_identifiers`, `episode_numbers`,
`episode_span`, nullable `edition` and strict `confirmed: true`. The backend
assigns provenance. Positive numeric TMDB/TVDB IDs are required;
unknown fields, caller provenance and browser measurements are rejected.
At least a work ID, or series ID plus explicit episode numbering, is required.
Legacy saved IMDb entries are omitted during read without rewriting stored bytes;
remaining TMDB/TVDB associations stay usable. An IMDb-only saved confirmation
returns the actionable 409 reconfirmation error. No schema migration or data wipe.
IDs are limited to 64 ASCII characters, lists to 16 IDs/32 numbers, edition to
256 characters. A span must contain the selected numbering. Labels reject paths.

Other Files and selected scanned files reuse one editor. Entity changes clear
draft IDs; alternative numbering and the shared-file checkbox are separate.
Review/Confirm is explicit; Cancel/Escape, navigation and reload never write.
Errors retain drafts; stale file-load/save responses cannot populate another file.
Ordinary local authentication/bypass rules apply, including origin/CSRF for cookie
mutations. Community access and scanning are not prerequisites for confirmation.

Alembic `0004_media_identity`, following `0003_local_community`, adds nullable
`media_items.confirmed_identity_json` without a backfill or rewritten legacy IDs.
Full plain/encrypted backups preserve it; settings-only backups intentionally omit
local media, consistent with their existing contract. No destructive downgrade.
Supported 0001/0002/0003 upgrades preserve rows and verify recoverable snapshots.

With explicit user approval, the workspace was upgraded 0003 to 0004 after the
runner verified a fresh recoverable snapshot. Independent validation succeeded for
`snapshot-c9127afdb094428a8b50f2a22afede73`; the journal records completion.
The earlier approved 0001-to-0003 upgrade has its own preserved snapshot.