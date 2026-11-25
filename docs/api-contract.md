# Editarr API contract

This file describes Editarr’s API behavior and where to find its schemas.
For permission evaluation and export layout, see the
[multi-user contracts](multi-user-contracts.md).

Editarr publishes its live OpenAPI document at `/openapi.json`. All paths below are
application-relative: with Base URL `https://example.com/editarr`, `/api/scans` means
`/editarr/api/scans` publicly. All payloads are JSON unless noted otherwise. The Jellyfin
server API key is write-only and never appears in a response.

## Integration API key

Complete Jellyfin setup, then generate your account's key in
**My Profile > API Key** (open your avatar at the top of the sidebar). Each account has at most one key, and every
request it signs is decided by that account's current role, status, and permissions.
Send `X-Api-Key: <key>` to the installation URL,
including its configured prefix. No query-string key or Bearer-token alias is
accepted. Missing/invalid credentials return 401; duplicate headers and malformed
keys are rejected without falling back to browser or external authentication.

| Method and route | Behavior |
| --- | --- |
| `GET /api/auth/api-key` | `{configured, created_at, revealable}` for the caller's own key; never the key or its digest. |
| `POST /api/auth/api-key/reveal` | Body `{reauthentication: {password}}`. Returns `{api_key, created_at}`. |
| `POST /api/auth/api-key` | Body `{reauthentication: {password}}`. Creates or replaces the key and returns `{api_key, created_at}`. |
| `DELETE /api/auth/api-key` | Revoke the caller's key; return `{configured: false, created_at: null, revealable: false}`. |
| `POST /api/users/{user_id}/api-key/reveal` | The owner reveals any account's key. |
| `POST /api/users/{user_id}/api-key` | The owner creates or replaces any account's key. |
| `DELETE /api/users/{user_id}/api-key` | The owner revokes any key; an admin revokes ordinary users' keys. |

Reveal and issue need a browser session with unsafe-request verification and the
account's Jellyfin password in the same request. An API key gets 403
`interactive_session_required` on them, and on every other route whose actions all
need fresh interactive authentication, such as ownership transfer. A password
Jellyfin refuses gets 403 `reauthentication_failed`; repeated failures for one account get
429 `reauthentication_throttled` with `Retry-After: <seconds>`. The integer is
the rounded-up remaining wait until both account and client-address failure
buckets admit another attempt. The same header survives Users and global
access-denial responses. When Jellyfin does not answer, the request gets 503
`jellyfin_unavailable`, nothing changes, and the attempt does not count toward
the limit. A key whose wrapped copy cannot be opened answers
reveal with 409 `api_key_unrecoverable`; a missing key
answers 404 `api_key_not_configured`. A busy database answers 503 `database_busy`
with `Retry-After: 1`. Responses are no-store.

Key-bearing requests retain
origin checks whenever browser source headers are supplied. Headless clients do not
need cookies or CSRF; cookie-only mutations still require both. Operation-specific
password and explicit-confirmation checks remain in force. Browser setup, sign-in, and status
retain their session lifecycle semantics; setup must finish before key access works.

API keys work wherever the account's permissions allow, except interactive-only
actions. The database keeps the key's SHA-256 digest and an AES-256-GCM copy under
`secrets/api-key-wrap.key`, bound to that digest; keys are not logged or audited.
Rotation/revocation affects subsequent requests
and does not revoke forms sessions; already authorized in-flight requests may finish.
Full backups include the digest, the wrapped
copy, and the wrap key; restoring one may restore an older credential.

Migration `0015_sealed_api_keys` keeps every key. Startup and restore activation
wrap any stored plaintext value and rewrite the database file. A digest-only key
from before `0009_readable_api_key` keeps working but cannot be revealed; generate a
replacement to show it.

## Users and profiles

`/api/users` lists the directory for admins and the owner (`offset`, `limit` up to
100, `search`), and `/api/users/{user_id}` reads and edits one profile: an account's own, or another
account within the caller's management scope. Profiles carry an optional display
name, display language (English only until translations ship), an
uploaded avatar re-encoded as PNG, and creation and last sign-in times. Linked
accounts keep their Jellyfin name and avatar. Status, session revocation, roles,
overrides, role defaults, and local media access have their own routes, and
`POST /api/users/bulk` applies one of them to many accounts with the same
per-account checks. Every permission or media-access change needs the caller's
Jellyfin password in the request (`reauthentication`), so API keys cannot make
them. The live OpenAPI document lists request and response schemas.

Jellyfin setup, a first Jellyfin sign-in, or an import creates every account.
Editarr has no route that creates a local account, sets or resets a password,
or changes registration. `POST /api/auth/login`, `PUT /api/auth/account`,
`POST /api/auth/register`, `GET` and `PUT /api/settings/registration`,
`POST /api/users`, and `POST /api/users/{user_id}/password-reset` answer 404.

Account details expose `disable_cause` and `read_only_fields`. A disabled
account's cause is `local`, `upstream_disabled`, `upstream_deleted`, or
`unlinked`. Migration `0030_remove_passwords` gives the cause `unlinked` to every
account that had no Jellyfin link and moves its username to an empty display
name, leaving `username` null. `PATCH /api/users/{user_id}` accepts no
`username`; sending one answers 422. `PUT /api/users/{user_id}/status` and bulk
enable refuse such an account with 409 `account_link_required`. Linking it to a
Jellyfin user enables it. A Jellyfin-linked account's `username`,
`display_name`, and `avatar` come from Jellyfin and answer 409
`linked_profile_field` when edited. `successor_refusal` is `null` when ownership
transfer to the account would be accepted now, or the `successor_disabled` or
`successor_credentials_required` code the transfer would refuse with. A Jellyfin
link to a replaced server cannot prove a successor.

## Jellyfin accounts

Jellyfin Quick Connect signs in without a password. `GET
/api/auth/jellyfin/quick-connect` reports `{enabled}`, false when Jellyfin is
not configured, unreachable, or has Quick Connect off. It reuses Jellyfin's
answer for ten seconds, and the false from a failed check for three seconds;
concurrent first checks share one request to Jellyfin. `POST .../initiate`
returns `{state: "pending", code}` and sets an HttpOnly handle cookie for this
browser; Jellyfin's secret stays on the server. `GET .../status` asks Jellyfin,
at most once a second per code, and returns the state: `pending`, `approved`, `expired`, `cancelled`, or
`consumed`. The sign-in page keeps a code on screen through a network error
or 5xx from status, as while Editarr restarts, asking again after 1, 2, 4, and
then every 8 seconds; after 30 seconds it reports which server is unreachable.
It waits out a 429's `Retry-After`. `POST .../complete` with `{remember_me}` spends an approved
attempt once and then admits and signs in exactly as Jellyfin password sign-in
does, with the same refusals. `POST .../cancel` ends the attempt. Starting
codes is limited to ten per client address per ten minutes, counting an IPv6
/64 as one address, and to 30 per minute across all clients (429
`quick_connect_throttled`). Editarr holds at most 1024 codes and never drops a
waiting or approved one to make room; when all are waiting, starting answers
503 `quick_connect_busy`. Setup does not offer Quick Connect.

`POST /api/auth/jellyfin/login` signs in with Jellyfin credentials. A valid
Jellyfin user without an Editarr account gets one, as an ordinary user or an
admin when Jellyfin calls it an administrator. That starting role is the only
one Jellyfin sets; the owner can change it, and later sign-ins and syncs keep the
owner's choice. Admins list and import
Jellyfin users with `/api/jellyfin/users` and `POST /api/jellyfin/users/import`,
and refresh every linked account with `POST /api/jellyfin/sync`, which also runs
every five minutes and at each Jellyfin sign-in. `GET /api/jellyfin/sync` shows
each linked account's verification state. Jellyfin disabling or deleting a user
disables its Editarr account and ends its sessions; its key and data stay. During
a Jellyfin outage, linked accounts keep their last verified access for 24 hours,
then only their own profile and key. Jellyfin's answers apply in the order
their reads began, counted by the database rather than the clock, so a slow
sign-in or an older listing never undoes a newer change; such a sign-in is
refused with 503 `jellyfin_unavailable` and can be retried. If the host clock
steps back, linked accounts lose last-verified access until each verifies
again with a read begun after the step; a sign-in or import whose Jellyfin read
began before the step is refused with 503 `jellyfin_unavailable` and can be
retried. The same holds for a sign-in or import whose Jellyfin read began before
a backup restore was activated; a sync read begun before it changes nothing.

A linked account's username is Jellyfin's name, refreshed at every sign-in, sync,
link, and import. While another account holds that name, or it is not a valid
username, the account keeps its current one (none for an account created then),
and the first read that finds this stores `account.jellyfin.state` with
`username: unavailable`; see
[provider snapshots](multi-user-contracts.md#accounts-sessions-and-provider-snapshots).

Deleting a linked account with `jellyfin_user: block` blocks its Jellyfin user.
Jellyfin password sign-in and Quick Connect then answer 403
`jellyfin_user_blocked`, import reports the user as `blocked`, and linking shows
the preview blocker `jellyfin_user_blocked` or, when the block arrives after the
preview, answers 409 `jellyfin_user_blocked`. `GET /api/jellyfin/blocks` lists the
blocks and `POST /api/jellyfin/blocks/unblock` (owner by default, with
`reauthentication`) removes one, so the user's next sign-in creates a fresh
account.

## Local community API

> **Master filter sets:** The [local community API](community-local-api.md)
> defines the routes for finding, downloading, applying, voting on, sharing and
> updating community master filter sets. Under
> [ADR 0002](adr/0002-community-master-filter-sets.md), it supersedes the
> revision-based subsections below: Target lookup, Community Downloads,
> Import Acknowledgments, Hosted Votes, Revision Quality Ranking, Local
> Revision Feedback, and the artifact-v2 parts of Publication jobs and
> Manual Sharing. They will be removed in the removal phase. Connection,
> enrollment, profile and key routes are unchanged.

Community access is disabled unless `EDITARR_COMMUNITY_SERVICE_URL` (canonical
HTTPS origin, no trailing slash) and `EDITARR_COMMUNITY_SERVICE_AUDIENCE` (exact
authority) are configured. No community request starts merely on startup.
All `/api/community/*` routes require a local administrator session or API key.
Session mutations
require the existing same-origin and CSRF checks; header keys follow the policy above. Public paths inherit Base URL.

| Method and route | Request / behavior |
| --- | --- |
| `GET /api/community/connection/status` | Network-free safe local status, including durable enrollment/rename state and last saved profile; not proof of current membership. |
| `GET /api/community/connection` | Safe connection status and signed profile refresh when enrolled; unconfigured status is not an error. |
| `POST /api/community/enrollment/start` | Persist confirmation secret privately and return safe hosted browser `authorizationUrl`. |
| `POST /api/community/enrollment/complete` | `{ "state_id": "<64 hex>", "confirmed": true }`; explicit administrator approval, signed backend completion. |
| `DELETE /api/community/enrollment` | Clear the local pending enrollment, without changing signing keys. |
| `GET /api/community/profile` | Refresh current signed identity/profile. |
| `PATCH /api/community/profile` | `{ "display_name": "...", "operation_id": "..." }`; persist exact rename intent for retries. |
| `DELETE /api/community/profile` | Clear local rename intent only; cannot undo a hosted rename. |
| `POST /api/community/keys/rotate` | `{ "confirmed": true }`; explicit action, durably prepare one candidate/proof body/operation, then submit. |
| `POST /api/community/keys/rotation-status` | Replacement-signed reconciliation of the saved operation; never generates or sends a new rotation. |
| `POST /api/community/keys/rotation-retry` | `{ "confirmed": true }`; probe first, resend the saved rotation only if not committed. |

Responses are no-store and contain `configured`, `connected`, nullable public
`profile`, nullable `pending` (`stateId`, Unix-second `expiresAt`,
`awaitingConfirmation`), and optional public `pendingRename` (original operation
ID/name), and configured public `serviceUrl`/`identity` (`installationId`, `keyId`,
`lastKnownKeyState`). Start may add `authorizationUrl`. No private key, confirmation secret,
Discord ID or provider token appears. Names are untrusted text, not credentials.
Errors contain `code`/`retryable` (409 for nonretryable conflicts, 503 for
retryable/unconfigured errors; existing auth policy retains 401/403).
Network/membership failures never become an empty lookup result.
The connection settings panel reads local state first, then verifies a
connected profile with the signed refresh.
Outages leave cached labels explicitly unverified and name editing disabled.
Hosted verification displays only the public enrollment reference; returning or
refreshing never confirms enrollment without the administrator's local dialog.

The rotation response includes nullable public `pendingRotation`
(`operationId`, `newKeyId`, `state: "pending"`) and `rotationCompleted` (`operationId`, `keyId`, Unix-millisecond `committedAt`).
A noncommitted probe adds `rotationStatus: "not_committed"`; this is not cancellation.
Private candidate bytes and detached proofs never appear in local DTOs. Pending
rotation blocks other identity mutations/ordinary old-key profile refresh. Reload
displays pending state without sending/probing. Activation requires the exact
authoritative hosted outcome; outage/conflict retains the same journal. Success
prompts for a fresh optional-encryption backup without claiming backup freshness.
No CLI, scheduler, startup, restore or ordinary Discord login rotates keys.

### Retired revision routes

Migration 0037 and [ADR 0002](adr/0002-community-master-filter-sets.md) retire the
revision-based lookup, download, import-receipt, vote and review-repair flows. These
local routes still require authentication, same-origin and CSRF, and their
registered capability (`community.search` for lookup, `community.feedback.submit`
for votes, `community.download` for the others; a caller without it gets `403
capability_denied`). They now answer `410 {"error":"protocol_retired"}` with
`Cache-Control: no-store`:

| Retired route | Replacement |
| --- | --- |
| `POST /api/community/lookup` | Find ([local community API §2](community-local-api.md#2-find)) |
| `POST /api/community/downloads`, `POST /api/community/downloads/status` | Master-set download and apply (§3) |
| `POST /api/community/import-receipts/recover`, `POST /api/community/import-receipts/{import_id}/retry` | Master snapshot receipts (§3) |
| `GET`/`POST /api/community/votes/{revision_id}`, `POST /api/community/votes/{revision_id}/retry` | Receipt-bound filter votes (§6) |
| `POST /api/reviews/{review_id}/repair/community` | None; use `repair/recover` |

Retired imports and their scans remain local data (local community API §10.2).

### Master filter set routes

The [local community API](community-local-api.md) defines the request and
response shapes. Every route requires authentication; unsafe methods and every
`POST` also require same-origin and CSRF, and responses are no-store. Links,
votes, preferences and sync notifications belong to the calling account; another
account's link is `404`. The master view is read-only: filter decisions happen in
the saved reviews that include a set, and the local filter decision and update
review routes are retired (local community API §5, §9).

| Routes | Capability |
| --- | --- |
| `POST /api/community/find`, `GET /api/community/aliases`, `GET /api/community/masters`, `GET /api/community/masters/{link_id}`, `GET /api/community/masters/{link_id}/active-filters`, `GET /api/community/preferences` | `community.search` |
| `POST /api/community/masters`, `POST …/masters/{link_id}/alignment/preview`, `POST …/alignment/approve`, `PATCH /api/community/preferences`, `POST …/masters/{link_id}/check`, `GET /api/community/sync`, `POST /api/community/sync/run`, `POST /api/community/sync/notifications/read` | `community.download` |
| `POST …/masters/{link_id}/filters/{filter_id}/vote`, `POST …/vote/retry`, `POST …/masters/{link_id}/reports` | `community.feedback.submit` |

### Publication jobs

The publication outbox persists exact backend-approved signed publication bytes before network work.
`EDITARR_COMMUNITY_PUBLICATION_REQUEST_BYTES` has no default; an explicit positive
bound, at most 3,000,000 bytes, is required for queue/send. This is a client safety
ceiling, not approval of a production payload limit. Status/cancel and owned
result reconciliation do not require that bound. No startup/background sender or
automatic enrollment occurs. The [manual sharing flow](#manual-sharing) defines
the preview and approval boundary.

| Method and route | Request / behavior |
| --- | --- |
| `GET /api/community/publications/{operation_id}` | Network-free local job status. |
| `POST /api/community/publications/{operation_id}/check` | Signed owned-operation reconciliation; never uploads. |
| `POST /api/community/publications/{operation_id}/retry` | `{ "confirmed": true }`; check first, explicitly retry exact saved bytes under current identity/consent. |
| `DELETE /api/community/publications/{operation_id}` | Persist cancellation only; cannot delete a remote publication. |

Hosted permission and attribution refusals (`sharing_permission_required`,
`source_permission_unknown`, `source_permission_incompatible`,
`source_attribution_mismatch`) are preserved as terminal `rejected` local outcomes.
Exact signed evidence remains stored; retry reconciles owned status without another
upload for that rejected intent. No author withdrawal or remote deletion API exists.

Each action requires its own/other publication capability, authorized source media,
and access to the original contributor. A publication of a removed
Radarr, Sonarr, or Other Files source has no source media. Only its status and check routes
answer. They require `archive.view` in place of source media access. A caller
without `archive.view` receives 404, even one who sent the publication. A caller
with `archive.view` also needs a check capability for the account that sent it.
The sender needs check own. A sender without it receives 403
`capability_denied`. Anyone else needs check other. A caller without it
receives 404, so the denial does not reveal another account's publication. The
route also returns 404 when the sending account is no longer active. Retry and
cancel return 404 for everyone. Mutations require
same-origin/CSRF. Responses are no-store and expose only
`ownerId`, `actorId`, `contributorId`, `operationId`, `contributionId`, `revisionId`, `artifactDigest`, `state`, `phase`,
`cancelRequested`, safe `errorCode` and nullable validated public `result`.
States are `pending`, `paused`, `published`, `rejected`, `cancelled`,
`cancel_pending`. Phase reports persisted pending/submitted/acknowledged/failed
state, not invented percentage progress. A paused/rejected job is an ordinary
safe status response, distinct from endpoint/identity/auth errors.
Published result binds original operation/root/revision/digest and Unix-second
`committedAt`; remote fields outside its exact schema are rejected.
No buffers, consent tokens, paths, private source snapshots/IDs or keys are returned.
Cancelling before send prevents future uploads. Submitted cancellation remains
uncertain even after a not-found probe; already-running bounded attempts may
finish. Recovered publication wins over cancellation, retaining the cancel flag.
Outages, service/membership failures and revoked consent retain evidence for an
explicit retry/check; source edits never rebuild an in-flight artifact.
New local schema 0007 and full backups retain payloads. Existing installations need an authorized backup-verified upgrade first.

### Manual Sharing

Manual sharing accepts settled scan evidence and persisted saved reviews. Each route is scoped to
the current account's capabilities and media access; mutations require
same-origin/CSRF and all responses are no-store.

| Method and route | Request / behavior |
| --- | --- |
| `GET /api/community/sharing/sources/{kind}/{source_id}` | Public source choices, confirmed typed media association, applicable source terms, current signed access check and up to 50 recent durable jobs. No enrollment or upload. |
| `POST /api/community/sharing/sources/{kind}/{source_id}/preview` | Explicit public `name`, strict `identity_confirmed: true`, optional `media` override and `audio_source_scan_id`; locally freezes exact artifact/manifest/public preview. No signing, consent enablement, outbox job or upload. |
| `POST /api/community/sharing/previews/{preview_id}/approve` | Strict `{ "confirmed": true }`; same authenticated session/root/service, unexpired preview, fresh source/file/key and original source permission checks, and current signed access. Queues exact approved bytes and records standing permission in one transaction. Never uploads. Repeated approval returns the same queued job. |
| `DELETE /api/community/sharing/previews/{preview_id}` | Discards only the same session's private in-memory preview; cannot cancel or remove a durable publication. |

Source context includes `sourceOwnerId` (the saved-review owner, or null for a
scan). Its jobs are filtered through each operation's check authority, media,
and original contributor; another user's hidden operation is omitted. The
read accepts an applicable publish or publication-check capability and requires
source-view authority. It remains usable when new-publication permission is
removed. Preparing and approving a new preview still require publication
authority. Persisted `ownerId`, `actorId`, and `contributorId` identify different
facts; managing an old job never substitutes the current actor's contributor.

`kind` is `scan` or `review`. A `media` override contains `provider` (`tmdb` or
`tvdb`), `entity_type`, supported ID-bearing `reference` URL or positive typed ID,
and optional `series_reference` / typed `episode_numbers`. URL and ID normalize
identically; conflicting URL/explicit episode context is rejected. No provider
lookup, IMDb-only association, media-path request or file-identity proof is used.
Existing confirmed associations and numbering/spans remain typed. Only actually
analyzed eligible source tracks are selectable; imported/historical unknown tracks
remain unknown. Preparation uses the mixed-track exclusions without editing
the review. Private captures and consent tokens never cross the browser boundary.

Preparation requires explicitly configured `EDITARR_COMMUNITY_PREVIEW_BOUNDS`
(JSON with positive `compressed_bytes`, `expanded_bytes`, `events`, `text`, `depth`),
`EDITARR_COMMUNITY_PREVIEW_SOURCE_LIMIT` (positive, at most 1000), and the upload
request bound above. None has a production default. A random preview ID is valid
for 15 minutes and bound to the session and installation/service scope. The
process-local private cache is capped at 16 previews and 64 MiB and prunes expired
entries on access/insertion; restart requires a fresh preview, not a rebuilt queued
artifact. Source reads and fingerprint preparation run off the HTTP event loop.

`community-reuse-1` is the approved standing permission to copy/share/modify public
contributions and notes, preserving original attribution and change history.
Per-donation confirmation asserts rights to share and approval of the exact preview;
there is no license picker, film/media ownership claim or author withdrawal.
Operators may moderate availability. Imported community sources currently require
the same known permission; missing/corrupt/incompatible original permission fails
closed and original signed claims are never rewritten. A saved local publication
consent is not automatic-sharing opt-in. Editarr sends contributions after explicit approval.

The Share command approves, then explicitly invokes the saved outbox retry only
when the acting account has its retry capability. Reopen
only rehydrates jobs; check/retry/cancel remain explicit and published outcome wins
even if a cancellation response arrives later. Changing a preview explicitly
discards its cache entry. Unmount or authentication-lifetime expiry aborts client
work without emitting cleanup requests; remaining bounded previews expire.
A stale preview clears consent and refreshes the source; 403/404 clears private
source state. Membership/service/offline/stale-source failures preserve local
scanning/review and approved durable jobs. No new database schema or secret is added.

## Typed Media Identity

Jellyfin items/versions add nullable
`media_identity`: typed work/series IDs with provenance, explicit episode numbering
and spans, separate release labels/duration/audio, and an actionable nullable
`input_requirement`. This DTO excludes local IDs, paths and credentials.
Supported identity providers are only TMDB and TVDB, never IMDb. The existing
local discovery fields remain unchanged. Upstream episode aliases are conservative,
and a linked series record must match the explicit source series ID.

Editarr title IDs are independent of Jellyfin item IDs. An Editarr version records
the legacy sampled MD5 and file size for the identified media. The fingerprint reads
files through 50 MiB in full and reads the first and last 10 MiB of larger files.
A unique sample and size match within a title can follow a file move. Distinct
known version IDs with the same sample remain separate. The sample offers no
comparison for bytes outside the selected ranges. Scene and credits cache entries
include the sample, file size, and saved version ID when a scan has a version pin.

The migration preserves existing version IDs, scans, reviews, and published files.
Older whole-file pins use a surviving verified observation of the current file
generation. When that evidence is unavailable, the original version stays in
history and can be used in the existing verification flow. Signed Community
artifacts keep their existing fingerprint and signature formats.

See [works, versions, aliases and match keys](community-scan-service-contracts.md#3-works-versions-aliases-and-match-keys).

## Named review API

Protected review routes are the saved-review authority: list/create routes
under `/api/library-items/{item_id}/reviews`; item GET/PUT/PATCH, duplicate, delete, review-ID EDL/MCF
export, and review-filter preview routes under `/api/reviews/{review_id}`; and shared
EDL/MCF import transports. Review
writes use expected resource revisions and return the complete authoritative aggregate.
List `sort=name` uses Unicode `casefold()` and applies direction only to that primary
key; stable review ID ascending breaks equal folded names. Date sorting applies direction
only to `updated_at` and uses the same ID tie-breaker. Default opening chooses greatest
`updated_at`, independently of presentation sort.
Each export reads review snapshots and records active review, format revision, and policy
provenance. A retained personal or item-default publication is also `outdated` if its
published bytes lack a mandatory cue newly matched by inferring the MCF channel from
the cue's source. The status change preserves the published file and leaves replacement
to an explicit republish. Existing sidecars remain continuously present until one atomic
replacement. A later database failure restores the previous
sidecar, and a failed restoration retains its recovery backup. Import creates a named completed imported
scan and saved review atomically without queueing or changing active EDL state.

### MCF and export contracts

`ScanFinding` and `ReviewFilter` expose nullable `category`, `subcategory`, `severity`,
`mcf_channel`, and `mcf_comment`. New generated/manual/MCF data requires a registry-valid
family/subcategory pair; null classification is reserved for unresolved legacy EDL input.
When exporting a review, a missing MCF channel derives from the filter source (audio or
video), and a missing MCF comment derives from its description. Explicit values on
imported or previously saved filters take precedence.
`ScanMcfDocument` adds immutable `source_version` and finite material bounds to the MCF
metadata fields. `ReviewMcfDocument` is the review-owned editable copy.

`SavedReviewSummary` contains `revision`, `content_revision`, `edl_content_revision`,
`mcf_content_revision`, `exports`, and nullable `repair`. A repair is present exactly
when persisted `review_scan_repairs` rows exist. It contains `message`, ordered
`missing_scan_ids`, earliest `detected_at`, and `recover` counts (`kept_cues`,
`removed_memberships`, `removed_accepted_credits`). The message is
"This review uses a scan that no longer exists."

Flagged reviews remain listed, but ordinary review operations return
`409 {detail: {code: "review_needs_repair", message, repair}}` with
`Cache-Control: no-store`. Delete and the explicit recover command remain
available under their normal authority. Hidden reviews still return 404.
Recover preserves copied cue values as manual/null-finding cues, removes missing
memberships and accepted credits, rebuilds policy evidence, and clears repairs.
It returns `SavedReview`; repeating a successful recovery is harmless. The
community repair route is [retired](#retired-revision-routes).
Existing sidecars are not removed by migration or repair.

`ExportStatus.exports` always has `edl` and `mcf`
keys. Each `ExportSummary` contains `format`, `present`, `exported_at`, `review_id`,
`exported_format_revision`, `current`, `outdated`, `exported_scans`, and
`exported_review`. `ReviewExportResponse` contains `format`, `review_id`,
`exported_format_revision`, `exported_at`, `exported_scans`, and `exported_review`.
There are no EDL-only response/status aliases.

`SavedReview` adds required `synchronization: ReviewSynchronization` and nullable
`accepted_credits`. `ReviewSave` requires the complete synchronization state. Its
`start_seconds`, `end_seconds`, and `end_mode` (`before_credits` or `file_end`) are distinct
from canonical `ReviewMcfDocument` bounds.

`GET /api/mcf-registry` returns registry-ordered families and exact category tokens.
`GET/PUT /api/settings/filters` reads/replaces `FilterDefaultsSettings` with optimistic
`revision`, ordered `selection_rules`, and registry-complete `severity_defaults`.
`POST /api/settings/filters/apply` accepts complete review filters and returns copied
filters plus selection/severity/channel change counts; it does not persist. It applies
the caller's effective defaults, including any enforced policy.
`GET /api/settings/filters/personal` returns the caller's effective defaults with the
origin of each rule and severity; `PUT` replaces the values of the caller's current mode,
and `PUT /api/settings/filters/personal/mode` chooses it. `/api/settings/filters/users/{user_id}`
offers the same per account, plus `PUT .../policy` (owner, Jellyfin password in the request) to
enforce instance rules and subcategory severities. `GET/PUT /api/settings/filters/default-mode`
reads and sets the instance default mode. See "Filter preferences and enforced policy" in
`docs/multi-user-contracts.md`.

| Method and route | Request | Success |
| --- | --- | --- |
| `POST /api/mcf-imports/upload` | Multipart `.mcf` file plus JSON-string `options` (`target`, `name`) | `201 McfImportResult` |
| `POST /api/reviews/{review_id}/retime` | Complete `ReviewRetimeRequest` draft | `200 ReviewRetimeResult` |
| `POST /api/reviews/{review_id}/repair/recover` | None; review-save authority | `200 SavedReviewSummary` |
| `POST /api/reviews/{review_id}/repair/community` | Retired | `410 {"error":"protocol_retired"}` |
| `POST /api/credits-detections` | Authoritative target reference and optional Qwen model | `202 CreditsDetectionStartResult` |
| `GET /api/credits-detections/{scan_id}` | None | `200 CreditsDetectionStartResult` |
| `POST /api/credits-detections/{scan_id}/model-consent` | Explicit acquire or continue-without-Qwen decision | `202 CreditsDetectionStartResult` |
| `POST /api/credits-detections/{scan_id}/accept` | Review/import context and exact accepted boundary | `200 CreditsAcceptanceResult` |
| `GET /api/credits-detections/{scan_id}/clip` | Exact finite candidate boundary query | Bounded path-safe `video/mp4` |
| `GET /api/reviews/{review_id}/mcf/preflight` | None | `McfPreflight` |
| `POST /api/reviews/{review_id}/mcf` | Expected review `revision` | `ReviewExportResponse` |
| `POST /api/reviews/{review_id}/edl` | Expected review `revision` | `ReviewExportResponse` |
| `GET /api/library-items/{item_id}/exports` | Optional `media_source_id` | `ExportStatus` |
| `DELETE /api/library-items/{item_id}/exports/{format}` | Optional `media_source_id`; format `edl` or `mcf` | `204` |

MCF upload accepts MIME hints `text/plain` and `text/mcf+vtt`; extension and content remain
authoritative. MCF parsing accepts exactly 1.0.0/1.1.0 and export emits 1.1.0. Preflight
issues identify ordered document fields or review-local filter IDs/positions without
echoing content, comments, URLs, or paths.

The named-review schema was delivered under the approved v1 fresh-schema development
policy, without a data-preserving migration. This contract does not claim that an
installed/user database was migrated or wiped.

## Public surface and mounted routes

The only anonymous production frontend routes are the canonical application entry `/`,
`/assets/{asset_path}`, and these fixed icons: `/apple-touch-icon.png`,
`/editarr-app-icon.png`, `/favicon-16.png`, `/favicon-32.png`, `/favicon.ico`, and
`/samwise-app-icon.png`. A deployment prefix is prepended once to each route. Entry HTML
is `Cache-Control: no-store`; content-hashed assets are immutable and named icons are
`no-cache`.

The entry contains an inert, public bootstrap before its relative module script:

```json
{ "version": 1, "basePath": "/editarr", "apiBasePath": "/editarr/api" }
```

It contains no secret, persisted override, upstream port, or authentication policy.
`basePath` is `/` or the canonical configured prefix and `apiBasePath` is exactly that
path plus `/api`. Missing or malformed production bootstrap data is a client-visible
startup error, not a fallback to root.

All other application APIs, generated artwork and previews, OpenAPI (`/openapi.json`),
Swagger (`/docs` and `/docs/oauth2-redirect`), Redoc (`/redoc`), and unknown application
routes remain protected under the existing setup/authentication policy; the small
authentication bootstrap exception is documented below. In particular, a prefix deployment
does not offer a second root-mounted API: requests must preserve the prefix.

## Authentication and access

Editarr has exactly one owner account and any number of admin and user accounts
(see [the permission matrix](multi-user-permissions.md)). On a fresh database, the only useful
application endpoints are `GET /api/auth/status`, `POST /api/auth/setup`, and the
setup-only restore workflow described below; every other
catalog, settings, scan, queue, preview, image, export, OpenAPI, Swagger, Redoc, and
unknown application route is denied with `423 setup_required`. Creating the owner
is atomic, so a concurrent second setup request receives `409 setup_already_complete`.

During first-run setup only, a same-origin browser may `POST /api/setup-restore/flow`
to obtain a 30-minute `HttpOnly`, `SameSite=Strict` flow cookie and an in-memory
request-verification token. Starting a flow ends the flow named by the browser's
current flow cookie, so a browser holds one flow. Unfinished jobs of an ended or
expired flow are cancelled when a flow or job starts; a job never starts for a
flow ended after its request was verified, and activation is never cancelled.
`POST /api/setup-restore/jobs` starts a restore-only job;
`PUT /jobs/{id}/archive`, `GET /jobs/{id}`, `POST /jobs/{id}/preflight`,
`POST /jobs/{id}/activate`, and `DELETE /jobs/{id}` share the
`/api/setup-restore` prefix. Unsafe follow-up calls require
`X-Setup-Restore-Token` and the flow cookie. Every request checks that no
administrator exists; job IDs are inaccessible from a different flow. The
activation lease checks setup state again before any switch. These endpoints
reuse the bounded upload, redacted preview, consent, and journaled
activation; export and download are never available during setup. The setup
preflight checks that the backup's owner has a Jellyfin link. After activation,
that owner signs in with Jellyfin. If the backup has no accounts, Jellyfin setup
remains required. The setup flow is not a login session, and
restored sessions are removed.

After setup, every authentication status response reports:

| Field | Values and meaning |
| --- | --- |
| `state` | `setup_required`, `unauthenticated`, or `authenticated`. |
| `setup_required` | The authoritative first-run flag; it is true only while no owner exists. |
| `display_name` | The signed-in account's profile display name, else its username; `null` when signed out. |
| `jellyfin_user_id` | The Jellyfin user ID the signed-in account is linked to, as 32 lowercase hex digits; `null` when signed out or when the account has no Jellyfin link. |
| `access` | The signed-in session's `CapabilityPayload`: contract version, `permission_revision`, principal summary (`user_id`, `role`, `credential`, `status`, `link_state`), and the capabilities it may attempt. `null` when signed out. Clients hide controls from it and refetch status when the revision changes. |
| `sign_in` | `{"jellyfin": bool}`: whether Jellyfin sign-in is offered. |
| `csrf_token` | Present only for a valid forms session. It is stable for that session and is never placed in a cookie, URL, or browser persistent storage. |
| `restart_required` | Always `false`. |

Every application route other than status, setup, and the Jellyfin sign-in routes
requires a forms session or an API key. No client address, forwarded header, or deployment setting replaces
one. `EDITARR_AUTH_TRUSTED_PROXY_NETWORKS` only lets failed-sign-in throttling use a
forwarded client address; malformed, conflicting, or untrusted forwarding metadata
falls back to the direct peer.

### Authentication resources

| Endpoint | Access and request | Result |
| --- | --- | --- |
| `GET /api/auth/status` | Public state probe. A valid forms cookie makes this an authenticated status read. | The status object above. It returns the session's stable CSRF value only to an accepted request; browser requests must satisfy the same-origin policy. It is `Cache-Control: no-store`. |
| `POST /api/auth/setup` | Only while setup is required. Body: `server_url`, the Jellyfin administrator's `username` and `password`, optional `remember_me`, and optional internal `return_to`. | `201` plus authenticated status, a forms cookie, and CSRF value. Editarr signs the user in to Jellyfin, requires `Policy.IsAdministrator`, mints an `Editarr` key with `POST /Auth/Keys`, stores the URL and key, creates the owner linked to that Jellyfin user, and ends Jellyfin's sign-in session. Refusals store nothing: `400 jellyfin_bad_url`, `401 invalid_credentials`, `403 jellyfin_not_administrator`, `502 jellyfin_unreachable`, `502 jellyfin_not_jellyfin`, `502 jellyfin_key_refused`, `502 jellyfin_key_left_behind`, `409 setup_already_complete`, and `429 login_throttled` with `Retry-After`. Every refusal except `setup_already_complete` counts toward the client address's sign-in limit (`EDITARR_AUTH_LOGIN_ADDRESS_ATTEMPTS`, 25 per 15 minutes by default); wrong passwords also count toward the typed name's. Concurrent requests create one owner and one key: the key is minted only under the database write lock after checking that no owner exists. Any failure after the create call revokes every new `Editarr` key; when Jellyfin will not list or delete it, the refusal is `jellyfin_key_left_behind` and asks the operator to delete it under Dashboard > API Keys. Jellyfin's sign-in session ends on every path after Jellyfin issues it. |
| `POST /api/auth/jellyfin/login` | Only after setup. Body: the Jellyfin `username` and `password`, optional `remember_me`, and optional internal `return_to`. | Authenticated status for that account, a newly issued forms cookie, and CSRF value. The same account may hold sessions on several devices. Quick Connect signs in through `/api/auth/jellyfin/quick-connect/...`; see [Jellyfin accounts](#jellyfin-accounts). |
| `POST /api/auth/logout` | Valid forms session; unsafe session request verification is required. | Revokes only that session, clears its cookie, and returns unauthenticated status. |

`return_to`, when supplied to setup or login, must be an internal absolute path beginning
with one `/`; scheme-relative and external URLs are rejected. It is returned for client
navigation but the API never redirects to it.

### Browser session, CSRF, and origin rules

Forms setup and login set the configurable `editarr_session` cookie by default. It is
`HttpOnly`, `SameSite=Lax`, scoped to the active Base URL prefix, and `Secure` for HTTPS
deployments. The built-in `http://localhost:<port>/` mode automatically uses a non-Secure
cookie while retaining source and CSRF checks. An ordinary cookie is a browser-session
cookie; `remember_me: true` adds a 30-day `Max-Age`. The server still enforces the matching
idle and absolute lifetimes. Signing in or completing setup issues a new session; logout,
disabling the account, expiry, or CSRF recovery failure invalidates it.

When a successfully restarted configured Base URL changes canonical origin or prefix,
the persistent session cutoff rejects forms sessions issued before the transition.
Login/logout clear `/`, every recorded legacy path, and the active path before issuing
the new active-path cookie. A browser cannot clear a cookie on a different host; it
expires normally and the cutoff still rejects its old session. A saved-but-unapplied URL
change and an implicit-local or ordinary listening-port-only change do not trigger this
transition. Cookie paths are routing scopes, not isolation between mutually untrusted
applications on one origin.

For every unsafe request made with a forms cookie, send exactly one canonical
`X-CSRF-Token` header using the value from setup, login, or authenticated status. The
request must also have an exact same-origin `Origin` or `Referer` consistent with the
active effective Base URL origin; browser Fetch Metadata that says cross-site is rejected.
Cookies, JSON fields, form fields, query parameters, and repeated/comma-joined headers
cannot replace this header. Setup and login have their own source-origin checks and may
be used by a non-browser client only when it sends no browser Fetch Metadata headers.

The optional `EDITARR_BASE_URL` is a full-URL seed and a saved Base URL takes precedence
over it. With neither value, normal loopback operation derives the effective address as
`http://localhost:<listening-port>/`; that derived local URL is never saved. Editarr does
not derive its origin from `Host`, `Forwarded`, or `X-Forwarded-*` headers. A credentialed
CORS configuration, when enabled, is restricted to the configured effective origin.
`EDITARR_AUTH_PUBLIC_ORIGIN` is not read, validated, aliased, or supported.

### Deployment settings

`GET /api/system/version` is protected and returns `release`, nullable
`build_revision` and `build_dirty` (null means unknown), `scanner_behavior`,
`database_schema` (packaged supported Alembic head), `local_api`, and nullable
`community_api` and `artifact_schema` (unimplemented contracts remain null).
These values describe the running binary, not the provenance of existing scans.

`GET /api/settings/deployment` and `PUT /api/settings/deployment` are protected after
setup by the same forms, external, verified-local, source-origin, and CSRF rules as other
settings routes. Their paths are application-relative, so a deployment at `/editarr/`
uses `/editarr/api/settings/deployment` publicly.

The PUT request is field-selective: `{"base_url": string | null, "port": integer |
null}`. An omitted field is unchanged; `null` clears that explicit saved override; an
empty Base URL is invalid. Base path is derived from Base URL and cannot be edited
separately. Saving only a port never turns the displayed environment seed or derived
localhost URL into a saved Base URL.

Both methods return `DeploymentConfiguration`:

```json
{
  "active": {
    "base_url": "https://example.com/editarr",
    "public_origin": "https://example.com",
    "prefix": "/editarr",
    "listening_port": 3348,
    "base_url_source": "saved",
    "port_source": "saved",
    "cli_port_override": null
  },
  "next_start": {
    "base_url": "https://example.com/editarr",
    "public_origin": "https://example.com",
    "prefix": "/editarr",
    "listening_port": 3348,
    "base_url_source": "saved",
    "port_source": "saved",
    "cli_port_override": null
  },
  "saved": { "base_url": "https://example.com/editarr", "port": 3348 },
  "restart_required": false,
  "restart": {
    "supported": false,
    "mode": "unsupported",
    "generation": 0,
    "in_progress": false,
    "outcome": "idle",
    "target_generation": null,
    "reason": "manual_uvicorn",
    "detail": "Restart Editarr requires the standalone editarr launcher"
  }
}
```

`active` is immutable for the current HTTP generation. `saved` reports only explicit
file overrides (nullable), while `next_start` resolves saved values, the environment
seed, the selected port, and any explicit `--port` override. Thus a CLI port override
can mask a saved port in `next_start` and leave `restart_required` false. Save never
changes routes, origin checks, cookie scope, or the bound listener. A Base URL edit that
changes canonical origin or prefix stages only the active prefix for later cookie cleanup;
Reauthentication applies after the replacement service starts successfully.

Invalid deployment values return `422 {"code":"deployment_configuration_invalid",
"detail":"…"}` without modifying the file. Corrupt, unreadable, or unwritable deployment
storage returns `503 {"code":"deployment_configuration_unavailable","detail":"Deployment
settings are temporarily unavailable"}` without exposing storage paths or content.

The backing `${data_dir}/deployment.json` file is versioned, lock-protected, and atomically
replaced. Its optional public overrides are `base_url` and `port`; its `cookie_scope`
member is non-secret service-managed transition metadata (`not_before`, `legacy_paths`,
and a pending previous path, plus durable active-scope bookkeeping). It is not a third
configuration API. A missing file means no overrides; a corrupt/unreadable file produces
the `503` above or a startup configuration error until corrected offline.

### Service restart

`GET /api/system/restart` reports the current controller capability and generation.
Direct Uvicorn reports `supported: false`, mode `unsupported`, and reason
`manual_uvicorn`. The standalone launcher reports mode `standalone_controller` and an
outcome of `idle`, `restarting`, `applied`, `recovered`, or `failed`.

The capability shape is:

```json
{
  "supported": true,
  "mode": "standalone_controller",
  "generation": 1,
  "in_progress": false,
  "outcome": "idle",
  "target_generation": null,
  "reason": null,
  "detail": null
}
```

Unsupported development reload uses `reason: "development_reload"`; manual direct
Uvicorn uses `"manual_uvicorn"`; a controller communication problem uses
`"controller_unavailable"`. `target_generation`, `reason`, and `detail` are optional.

`POST /api/system/restart` has an empty body. A supported request returns its complete
response before shutdown begins:

```json
{
  "status": "accepted",
  "target_generation": 2,
  "reconnect_url": "https://example.com/editarr/#/settings/general"
}
```

The reconnect URL comes from resolved next-start state. Concurrent requests return
`409 restart_in_progress`; unsupported launches return `409 restart_unsupported`; a
lost controller returns `503 restart_unavailable`. The protected GET can return `401`
after a Base URL scope transition because successful activation invalidates the old
forms session. A generation at or above the target is ready only when its outcome is
`applied`; `recovered` means the requested saved configuration failed and the prior
listener was restored with the changes still pending.

Those restart errors use the normal safe envelope, for example
`{"code":"restart_in_progress","detail":"A restart is already in progress"}`.
A supported restart is accepted only after authorization, source-origin, and CSRF
checks; its `202` is sent completely before shutdown begins. The service attempts the
last working listener if a newly selected port cannot bind. A recovery/failure is never
an applied change merely because its generation advanced.

### Authentication errors

All policy errors use `{"code": "…", "detail": "…"}` and do not include credentials,
tokens, rejected header values, or password-validation internals.

| Status | Code | Meaning |
| --- | --- | --- |
| `401` | `authentication_required` | A protected route needs a valid forms session or API key. |
| `401` | `invalid_credentials` | Jellyfin did not accept the sign-in. The message is deliberately generic. |
| `403` | `origin_failed` | Origin, Referer, or Fetch Metadata verification failed. |
| `403` | `csrf_failed` | An unsafe forms-session request omitted or failed CSRF verification. |
| `409` | `setup_already_complete` | A setup request arrived after the administrator exists. |
| `423` | `setup_required` | Setup has not completed, so the requested route cannot run. |
| `429` | `login_throttled` | The bounded login failure limit for the username or client address was reached. |
| `503` | `database_busy` | Sign-in verified the password but could not write the session before another writer released the database. Nothing was charged against the login limit; retry after `Retry-After`. |
| `422` | FastAPI validation response | The request shape, credentials policy, or `return_to` value is invalid. |

Jellyfin `login_throttled` responses carry `Retry-After: <seconds>`
and `Cache-Control: no-store`, using the remaining username and address window.
Fresh-authentication 429 responses retain `reauthentication_throttled`, not a
401 sign-out signal. The web keeps the current session and shows a countdown
with submission disabled until that wait ends.

## Domain resources

| Resource | Identity and purpose |
| --- | --- |
| `JellyfinLibrary` | Jellyfin virtual-library ID and display metadata. |
| `JellyfinMediaItem` | Immutable Jellyfin item ID plus server-normalized metadata and format-keyed export state. |
| `Scan` | Stable UUID `id`, per-media incrementing `scan_id`, optional `name`, explicit `origin`, snapshot options, state, timestamps, nullable historical release/build/scanner/model revisions, attempt-specific `stage_coverage`, and derived `manual`/`edited` flags. |
| `ScanFinding` | Immutable audio/video evidence belonging to one `Scan`; saved reviews copy findings into review-local filters. `confidence` is nullable when no model score exists. |
| `ScanQueueItem` | A queued `Scan`, its serial order, and transient progress. |
| `SavedReview` | A named, revision-guarded aggregate containing ordered scan memberships and mutable review-local filter snapshots. |
| `ReviewExportResponse` | A successful EDL/MCF write with format revision and all contributing scan provenance. |
| `EdlImportResult` | The saved review, completed imported scan, and optional aggregate warning created atomically from one EDL. |
| `McfImportResult` | The saved review, completed imported scan, source version, and safe metadata notices created atomically from one MCF document. |
| `ExportStatus` | Independent EDL and MCF presence, revision, current/outdated state, review attribution, and scan provenance. |
| `ScanReferences` | `scan_id`, `active_exports`, and saved `reviews` that block scan deletion. |
| `PathMapping` | An ordered remote-prefix to scanner-host-prefix mapping. |

`stage_coverage` contains zero or more records with `stage`, `execution_id`,
`status` (`complete`, `partial`, `failed`, `disabled`, `unsupported`, `unknown`),
`source` (`generated`, `manual`, `imported`), nullable effective release/build/scanner
and model ID/revision/digest/quantization, nullable effective settings, intervals,
and sampling seconds. Multiple attempts of the same stage retain separate records.
An empty list means historical coverage is unknown, not that every detector completed
or was disabled. A scan with no findings may still have recorded coverage. Existing
numeric confidence values remain intact; new MCF/EDL/manual evidence without a model
score has null confidence in both scan findings and editable review filters. Credits
candidate confidence remains a separate numeric detector score. Worker attempts
record effective execution provenance. Requested enrichment remains `unknown`
until observed; no-input enrichment is `disabled` with a skip reason, while
unavailable capability is `unsupported`. Cache reuse has partial/unknown original
model provenance; queued options alone do not prove a stage ran. Versioned
checkpoints may resume only when linked to completed attempt coverage.

### Scan model choices

`GET /api/capabilities` lists the selectable `audio_models`, `audio_filters`,
`video_models`, and `qwen_models` (the Severity and Description model) available on
this host, each as `{id, display_name, recommended}`. Only Qwen3-VL 8B is
`recommended` in the video and description lists; clients label it "(recommended)".
Video models are listed in selection order, so the first entry is the default:
`qwen3-vl-8b` where MLX runs (Apple silicon), otherwise `nudenet`. A scan request that
omits `video_model` uses the same default. The description model keeps the request
field name `qwen_model` for compatibility with stored scan options.

### Audio track selection

Protected `GET /api/media/audio-tracks?item_id=<Jellyfin item ID>&media_source_id=<optional version>`
returns `{file_version, tracks, selection, selection_stale}`. Each track contains
physical `stream_index`, observed nullable `language`, `title`, `codec`, `channels`,
integer `disposition`, nullable `start_seconds`, `duration_seconds`, and rational
`time_base`. Unknown values are not inferred. The 64-hex file version describes
the resolved local path, file metadata, and audio inventory without device IDs.
Only the sole track or one unambiguous flagged default is preselected. A stale
remembered choice returns null selection and `selection_stale=true` unless a
completed scan independently confirms the same exact file and audio track.

Protected/CSRF-checked `POST /api/media/audio-tracks/selection` takes
`{item_id, media_source_id?, selection: {file_version, track}}` and rejects other
fields. It revalidates the exact snapshot,
remembers it only for that canonical file/version, and returns the selection.
Unavailable/stale inputs return 409; malformed bounded DTOs return 422.

All generated audio scan creation requests require this explicit `audio_selection`
snapshot. Path mapping applies to Jellyfin item paths; backend admission independently validates the resolved
file. Video-only/credits scans require no audio selection. Scan/queue DTOs expose
nullable `requested_audio_selection` and `analyzed_audio_selection`. Actual input
is immutable and recorded only after successful analysis; requested input alone
does not prove analysis. Historical/imported unknown input stays null. Stale or
missing queued inputs and unverifiable audio checkpoints stop without substitution.
Device-bound audio selection tokens expire. Start a new scan and choose the audio
track again if a queued selection can no longer be verified.
See [match key types](community-scan-service-contracts.md#32-match-key-types)
for how selected tracks affect community matching.

### Scan naming

All scan creation requests accept optional `name`, including `POST /api/scans`.
Manual filters are staged inside a saved-review create/save
request; if the version's reusable Manual evidence scan must be created, its display name
is `Manual`. Later staged filters reuse that scan without renaming it.

Names reject Unicode `Cc` controls and `Cs` surrogates before trimming outer Unicode
whitespace, preserve case and Unicode spelling, and allow at most 100 Unicode code points
after trimming. Omitted, null, and blank names become null. Names need not be unique.

`PATCH /api/scans/{scan_id}` takes `{"name": "Dialogue pass"}`, where `scan_id` in the
route is the UUID. The nullable `name` field is required and extra fields are rejected.
Null or blank clears the name. The response is the complete current `Scan`; unknown UUIDs
return 404 with `detail` containing `code: "scan_not_found"`, `message: "Scan not found"`,
and null `line_number`/`references`. Invalid names/requests return a 422 validation list
without input values. Authentication, same-origin, and forms-session CSRF checks apply.
Rename is permitted in every state and origin and changes only `name`: scan `updated_at`,
worker identity/options, finding revisions, and content stay unchanged. In particular,
renaming a completed scan does not alter its reported execution duration.

Scan responses, queue/history/item lists, candidate matches, saved-review scan references, and
Export scan references include `name` and `origin` (`generated`, `manual`, or `imported`).
The transitional `manual` flag is derived from `origin == "manual"`; an AI scan of Other
Files remains `generated`. Imported scans also expose their safe `import_source_name` and
UTC `imported_at` and have no execution duration; import creation is a separate workflow.

Recover Scans uses `POST /api/filter-candidates` (search and match, which may hash
media) and `POST /api/filter-candidates/associations`. `POST
/api/filter-candidates/available` takes the same search body and answers `{"available":
bool}`: whether the caller can reach a completed scan of another file of the same work
(TMDB/TVDB provider ID). It reads no media and writes nothing; it needs
`scans.findings.read`, and the web app disables Recover Scans when it is false and the
item has no other local versions, previous versions, pending imports or linked scans with
scans. Because it doesn't hash, a search can still find nothing; the dialog then says
"No recoverable scans matched this file."

## Primary flows

- Settings: `GET`/`PUT /api/settings/jellyfin`, `POST /api/settings/jellyfin/test`.
- Catalog: `GET /api/libraries`, `GET /api/libraries/{library_id}/items`,
  `GET /api/library-items/{item_id}`, and `/children`/`image` variants.
  Library paging uses `start_index` and `limit`; sorting, EDL filtering, and
  title search compose in the same request.
- Scans: `POST /api/scans` accepts a Jellyfin item ID and options. It rejects
  non-video or unreadable mapped items. Repeated scans for the same media item
  receive new per-item scan IDs and enter the serial queue.
  `GET /api/library-items/{item_id}/scans` lists the caller-visible scans stored
  for that Jellyfin item. It takes no path; an unknown query parameter is ignored.
- Queue/history: `GET /api/scans/queue`, `POST /api/scans/queue/reorder`,
  `DELETE /api/scans/queue/{queue_id}`, and `GET /api/scans/history`.
- Credits detection: `POST /api/credits-detections` starts or reconnects one normal
  video-only scan for an authoritative target and exact detector configuration. `GET
  /api/credits-detections/{scan_id}` returns status/result, `/model-consent` explicitly
  acquires or removes optional Qwen use, `/accept` returns a server-derived review/import
  draft without persisting it, and `/clip` returns a bounded path-safe MP4 preview.
  Queue cancellation uses the normal `DELETE /api/scans/queue/{queue_id}` route. A missing
  Qwen artifact never downloads before disclosed consent.
- Scan evidence: `GET /api/scans/{scan_id}/findings` returns immutable source
  findings. `GET /api/scans/{scan_id}/references` returns
  `{scan_id, active_exports, reviews, has_hidden_references, community_in_progress}`.
  Each visible review has stable `id`, display `name`, and `updated_at`.
  `has_hidden_references` reports reviews the caller cannot view.
  `community_in_progress` is true only for a `pending` or `submitted` community
  operation, without exposing service, artifact, or operation details. Its refusal
  message is "A community operation for this scan is still in progress".
  `DELETE /api/scans/{scan_id}` returns `409 scan_referenced` with these caller-safe
  references for a review, active export, or in-progress operation. An otherwise
  unused community scan is deletable: imports and their targets are removed, and
  finished operation history remains with a null `scan_id`.
  The server acquires its SQLite write lock before reading deletion blockers and
  retains it through deletion and commit. Refusal preserves all evidence.
  Scan-level selection, finding mutation, and scan-owned export routes are not
  part of the API.
- Saved reviews: list/create routes are
  `GET`/`POST /api/library-items/{item_id}/reviews`. `GET`/`PUT`/`PATCH`/`DELETE
  /api/reviews/{review_id}` load, save, rename, and delete an aggregate;
  `POST /api/reviews/{review_id}/duplicate` copies it. `POST
  /api/reviews/{review_id}/retime` accepts the current draft memberships, current and
  proposed synchronization, current MCF document, complete source-linked filters, nullable
  accepted-credits reference, and staged manual filters. It maps cues with unknown video
  provenance from the submitted current basis; verified local scan cues, accepted credits,
  and manual cues retain their times. It returns a transformed draft without
  persistence; detector evidence remains immutable and normal Save remains the only write
  boundary. Mutations use the current
  review `revision`; content changes also advance `content_revision` and affected format
  revisions. Saves include the complete editable `mcf_document` and `synchronization`.
- Community filters: `SavedReview.community_sources` lists the community master-set
  snapshots a review includes; create and save requests take `community_memberships`
  (omitted on save keeps them). Including a set adds each served filter to `filters`
  as a normal review filter with `source_scan_id: null`, `community_source_id` and
  `community_filter_id` and read-only `community` provenance (version label,
  confidence, contributor count, `edited`, `removed`). Below-threshold and Low
  Confidence filters start disabled. Community filters are enabled, disabled and
  adjusted like scan filters and go through the same policy, EDL/MCF export, sidecar,
  subtitle and sharing paths. Refreshing a snapshot keeps the user's edits by filter
  ID, takes new master values for unedited filters, adds new ones, and flags filters
  no longer served as removed. Exports need at least one scan or one community filter;
  a community-only review exports beside the review target's file. Including a link
  or refreshing a snapshot needs `community.search` and evidence-edit permission, and
  only the review owner may take a snapshot of their own link; see
  [review community filters](community-local-api.md#saved-review-community-filters).
- Filtered subtitles redact the words of every enabled audio-channel language mute
  that has a word or phrase (its label), whatever the filter's source; a mute without
  one, such as an unlabeled community filter, changes no subtitle.
- Manual filters: `new_manual_filters` in saved-review create/save requests stages
  human-authored audio mute and visual skip filters with required MCF classification. The successful transaction creates
  or reuses the target version's Manual evidence scan and returns assigned provenance;
  a failed or discarded draft creates no source finding. There are no immediate
  Manual-finding endpoints.
- Label provenance: each `SavedReview.filters` and `base_filters` entry is a `SavedReviewFilter`, a
  `ReviewFilter` plus read-only `label_edited: boolean`. It is `true` when the stored
  label differs from the label of the scan finding the filter was copied from (a
  person relabelled it), and `false` when it matches or the source finding is
  unavailable. It does not reflect other edits such as severity, action, or timing, and
  hand-added filters keep their Manual scan finding's label, so it is `false` for them
  (identify those by their source scan's `manual` origin). Clients may echo the field
  back in `ReviewFilterInput`; it is ignored, and the server recomputes it.
- Review previews and export: review-filter preview routes live under
  `/api/reviews/{review_id}/filters/{filter_id}`. `POST
  /api/reviews/{review_id}/edl` takes the expected review `revision`, exports only
  selected review filters with an atomic sidecar write, and returns
  `ReviewExportResponse`. `GET /api/reviews/{review_id}/mcf/preflight` validates MCF
  completeness before its corresponding export. A later format-relevant review change
  makes only that format `outdated`.
- EDL import: `POST /api/edl-imports/upload` accepts multipart file content and
  JSON options. It creates one completed imported scan and one saved review
  atomically and returns `EdlImportResult` without changing the active sidecar.
  Browser upload is the only import transport; Editarr never reads an import file
  from a scanner-host path.
- Import target: every EDL and MCF import `target` is a Jellyfin
  `ReviewTargetRequest`, `{source: "jellyfin", item_id, media_source_id?}`. A
  target whose `source` names anything else returns 400 with `code`
  `jellyfin_item_required` and the message "This file must belong to a Jellyfin
  item. Open its Jellyfin item and import the file there." A Jellyfin target that
  is malformed, such as one with an empty or missing `item_id`, is an ordinary 422
  (`invalid_edl_import` or `invalid_mcf_document`). Nothing is written.
- MCF import: `POST /api/mcf-imports/upload` accepts MCF 1.0.0/1.1.0.
  It requires an explicit target `synchronization`, nullable distinct
  `mcf_material_end_seconds`, and nullable server-validated `accepted_credits` reference.
  Source START/END and cues remain immutable scan evidence; the initial review receives
  affine-mapped cue copies. Before-credits mode requires canonical MCF END to equal target
  synchronization END. File-end mode permits a separate nullable canonical before-credits
  END. Source parsing is independent from target runtime, while target bounds and mapped
  cues are validated against the authoritative resolved media duration.
  Recoverable metadata, material-bound, cue-timing, and payload problems return
  `mcf_import_issues` with an ordered `import_issues` list before anything is persisted.
  Repeating the same request with `ignore_errors: true` skips invalid values/cues and
  atomically creates one imported scan and saved review from the recoverable content.
  Invalid encoding/header/version, source START/END, and resource-limit failures cannot be
  ignored. A valid accepted credits reference adds its existing scan, corrected review
  snapshot, and detector provenance in the same import transaction without changing the
  detector finding.
- Export status/deletion: `/api/library-items/{item_id}/exports` routes return both
  format summaries and delete one selected format. EDL and MCF files and markers are independent.
- Archive: `GET /api/archive?offset=&limit=` returns `{total, offset, limit, items}`,
  one page of the read-only records of removed Radarr, Sonarr, and Other Files
  sources, newest first. `offset` defaults to 0 and `limit` to 50, from 1 to 100.
  It requires `archive.view`, which only admins and the owner hold. It is not
  delegable: no role default or override grants it to an ordinary user or removes
  it from an admin, and such a change is refused with 422 `role_fixed_capability`.
  Any other caller receives 403, the same for a session or an API key. Each item
  has `id`, `source` (`radarr`, `sonarr`, or `manual_files`), `title`, `item_type`,
  nullable `series`, `season`, `episode`, and `last_episode`, nullable `path`, and
  its `scans`, `reviews`, and `exports`. `last_episode` ends a multi-episode file's
  range and is null unless the file name states a higher number than `episode`. For
  an episode, `season` and `episode` come from an MCF document when one recorded
  them, otherwise from a standard `SxxEyy` file name; `series` is the folder above
  an optional `Season N` folder of such a file. They are null when no pattern
  matches and always null for a movie or an Other Files record. `path` and `series`
  are null unless the caller holds `media.paths.view`. Each review includes its
  nullable `owner`. The archive has no write routes. Apart from the status and
  check of a sent publication (see Publication jobs), no other route reads an
  archived item. Migration `0026_archive_manual_files` archives former Other Files
  records with `source` `manual_files`, which the web app labels Other Files, and
  their original file name as `title`.

## Errors and conflicts

Editarr uses standard HTTP errors. Review/import domain failures use
`{"detail":{"code","message","line_number","line_text","references","import_issues"}}`; generated OpenAPI lists
that `ReviewError` shape for every applicable status. Request-shape 422 responses retain
a validation list containing only type, location, and message—never the rejected input.
Line-aware EDL and MCF parser failures include only the offending source line in
`line_text`; aggregate MCF recovery responses instead include each issue's code, message,
line number, and line text in `import_issues`.
`400` means
invalid input, `404` means a resource is not
known, `409` denotes an operational conflict (including a stale review revision,
invalid review membership, invalid queue reorder, or deletion of a scan referenced
by reviews or any active export), and `502` represents a safe Jellyfin upstream
failure. Authentication-specific `401`, `403`, `409`, `423`, and `429` results are
defined above and take precedence before a protected endpoint executes.

MCF import additionally uses `413` for bounded content/line/entry limits and safe
line-aware `400`/`422` errors for unsupported versions, malformed structure, tokens,
metadata, timestamps, comments, or ranges. MCF preflight/export uses ordered issue codes
such as `missing_category`, `missing_subcategory`, `missing_severity`,
`invalid_category_pair`, `invalid_mcf_metadata`, and `invalid_material_bounds`.

The MCF schema and API were delivered under the v1 fresh-schema policy without a
data-preserving migration. This contract does not claim migration or reset of installed
or user data.

## Portable backup jobs

The protected `/api/backups/jobs` workflow uses Origin and CSRF middleware and
checks a separate permission for each action (see
[the permission matrix](multi-user-permissions.md)). `POST /api/backups/jobs` accepts an `action` of `export`,
an optional `kind` of `full` (the only kind), optional `encrypt`, and optional
`passphrase`. Plain is the default. A passphrase is accepted only
for explicitly encrypted export. The response contains a random ephemeral
job ID, redacted state and no passphrase. Jobs are process-local, bounded to
four per process and expire after 30 minutes.

`GET /api/backups/jobs/{id}` reports `waiting`, `uploaded`, `working`,
`activating`, `ready`, `activated`, `failed` or `cancelled`. Export `ready`
includes only redacted protection and secret-type inventory. `HEAD` and `GET`
on `/api/backups/jobs/{id}/download` check readiness; GET streams the private
archive as an attachment with `Cache-Control: no-store`. Clients must not
buffer a large archive as a browser blob.

This API never restores. A configured installation has no restore route.
Restore runs only on an empty installation, through the first-run
`/api/setup-restore` flow described under
[Authentication and access](#authentication-and-access) or the offline
`editarr backup restore` command into an empty data directory. In the setup
flow, `PUT /api/setup-restore/jobs/{id}/archive` sends raw archive bytes
(`application/octet-stream`) in a bounded stream (80 GiB content ceiling plus
container overhead). `POST /api/setup-restore/jobs/{id}/preflight` accepts an
optional passphrase in a JSON body and produces a redacted `plan` at `ready`.
The passphrase and staged bytes remain server-private, never in status
responses. `POST /api/setup-restore/jobs/{id}/activate` requires
`confirmed: true` and offers no replacement choices. Restore accepts only a backup
at the current schema head, `0030_remove_passwords`. An older backup fails with
reason `restore_backup_schema_unsupported` and the message "This backup has database
schema <rev>. This Editarr restores only backups made at schema
0030_remove_passwords." The installation stays empty. A restore brings back
everything except the empty installation's base URL and listen port.
Activation cannot be cancelled. After activation, old sessions are gone and the
queue remains held for reconciliation. Unresolved journals require the offline
`backup recover` command. No API clears them.

`DELETE /api/backups/jobs/{id}` cancels unfinished export work and discards the
archive.

### Scheduled Local Backups

`GET /api/backups/schedule` returns machine-local configuration and redacted durable
status with `Cache-Control: no-store`. `config: null` means unconfigured/disabled.
Status includes Unix-second `next_run`, `last_attempt`, `last_success` (nullable),
`outcome` (`idle`, `running`, `succeeded`, `failed`, `interrupted`), safe `error`,
`retained_count` and `restore_hold`. Recorded verified outputs are counted, not
manual or unconfirmed files. A successful backup may also report a retention error.

`PUT /api/backups/schedule` requires `confirmed: true`, boolean `enabled`, integer
`interval_hours` (1-8760), integer `keep_count` (1-1000), absolute `destination`,
`kind` (`full` default, or `settings`), `protection` (`plain` default, or
`age-scrypt`), and `passphrase_file` (null for plain; absolute path for encrypted).
Enabling validates an existing writable destination outside application data and
served web files, and the external owner-private credential file when encrypted. The passphrase itself
is never accepted by this endpoint or returned. `DELETE /api/backups/schedule`
disables without requiring a currently usable destination or credential. A caller
without `media.paths.view` receives `config.destination` and `config.passphrase_file`
as null.

All three methods require an actual local administrator session. Mutations require the current Origin/CSRF checks. Invalid
configuration returns 422; unavailable destination/credential/state returns a safe
400; an active scheduled run or maintenance owner returns 409. No save/disable
cancels an in-flight snapshot. Responses never contain credential contents.

The application-lifetime runner polls every 30 seconds, records a due attempt and
its next interval before exporting, collapses downtime into one catch-up, and
retries failure at the next interval. Cross-process locks prevent overlap; restore
holds are checked again under the export's existing database/key maintenance lock.
Only a newly verified success permits oldest-first pruning of recorded, unchanged
owned files in its private destination subdirectory. Never overwrite prior output,
prune manual backups, or downgrade missing encryption credentials. Uncertain output
is retained for review. Schedule state/credentials are not portable-backup members.
Encrypted scheduling currently requires POSIX permission validation; unsupported
Windows ACL validation fails closed. No community-service schedule is implied.

## Canonical Review Exports

`POST /api/reviews/{review_id}/exports/{format}` accepts `edl` or `mcf`, the
existing `revision`, `policy_owner_id`, and `policy_revision` tokens, and
`target: "self" | "review_owner" | "item_default"` (default `self`). Paths,
arbitrary user IDs, and unknown fields are refused. Existing review `/edl` and
`/mcf` endpoints publish through the same lifecycle and retain their response
shape. They no longer write legacy adjacent files.

Personal paths are `<format>/<export-id>/<stem>.user-<export-id>.<format>`;
defaults are `<format>/<stem>.<format>`. Export IDs use provider GUIDs or
immutable `local-` IDs. Source review owner, original author, actor, and target
are separate. Choosing another target never changes the policy owner. Source
and personal-target actions, account state, and media access are checked with
fresh authority under the database writer lock. Default publication and deletion
require their respective default capabilities.

Publication returns `id`, `format`, `scope`, `target_user_id`, `target_export_id`,
`actor_id`, `policy_owner_id`, `original_author_id`, `policy_revision`,
`review_id`, `exported_format_revision`, `publication_id`, `publication_revision`, and
`content_sha256`. Review identifiers are projected through current review
visibility. No response includes a destination path or the private snapshot.
`GET /api/exports/{id}` returns that projected status. Deletion uses
`DELETE /api/exports/{id}?revision=N&publication_id=UUID`, requires the current
publication generation and revision, and returns 204. The UUID prevents stale
requests from acting on a reused SQLite row ID. Retained publications remain deletable after source attribution
is detached and the source review is removed.

Replacing another source review returns 409 `export_confirmation_required` with
a 64-hex `confirmation` token. Resubmit it with the same request. The token binds
the incoming review, content, policy, target path, and current publication.
Competing writes invalidate it with 409 `stale_export_confirmation`. Unowned
files, symbolic links, and filenames owned by another media version are never
silently replaced. Legacy adjacent files do not become item defaults.

`POST /api/reviews/{review_id}/exports/{format}/download` accepts the existing
review/policy tokens and returns exactly the prepared publication bytes, with a
canonical personal attachment filename and `Cache-Control: private, no-store`.
It creates no export row, directory, or sidecar. Cross-user downloads also need
the actor's personal download capability.

The `/api/reviews/{review_id}/subtitles/sources`, `/preview`, and `/publish`
operations share the same saved-review export eligibility. This includes users
whose only export permission is to download that review. A published subtitle is
available to everyone who can play that media version in Jellyfin.

Migration `0021_export_snapshots` appends `content_bytes`, `snapshot_json`,
`destination_path`, and `publication_revision` to `media_item_exports`.
Existing rows retain null snapshots and revision zero. The private
`editarr-export-snapshot-1` JSON retains the full saved-review model, base and
effective filters, synchronization, MCF metadata, accepted credits, policy
evidence, immutable source findings, actor/owner/target IDs, source revision,
and the physical media path. Exact output bytes are stored separately. Editarr can
consume these without depending on a surviving source review.
Migration `0022_export_regenerations` adds `media_item_export_regenerations`,
one regeneration job per personal publication keyed by export ID. The
snapshot JSON no longer carries job state.

Callers acquire `publication_lock(db)` before the authorized writer transaction
and hold it through commit and file cleanup. Its per-database kernel lock
serializes canonical writers across processes, including uncertain commit
reconciliation. The lifecycle refuses calls without this lock. Publication,
deletion, and successful audit events share one database transaction.
Atomic file replacement retains a synced sibling backup and version-1 recovery
journal until commit. A subsequent writer reconciles recognized interruptions
against committed bytes. Uncertain commit acknowledgment is resolved through a
fresh database connection. Failed restoration retains the journal and backup
and returns 409 `export_recovery_required`. Unexpected external content requires
operator recovery. This serializes application writers; it does not protect
against a privileged process changing directories during an operation.
