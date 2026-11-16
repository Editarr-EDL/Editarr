# Multi-user contracts

MU-01 froze version 1 after independent integrated verification of
a4bb9fe57f558fb43f58a11886e7960b7282914f. The [permission matrix](multi-user-permissions.md),
[decision log](multi-user-decisions.md), and [plan](multi-user-plan.md) remain the
product authority. This file records the machine-readable form and the technical
choices made inside that authority.

## Versions

| Contract | Version | Pin |
| --- | --- | --- |
| Capability inventory | `editarr-capabilities-9` | `contract_digest()` `a3c9b242cf4785d533e79843e3e77688c412430759f27924fc489174c245a9d4` (JF-06: removed `profile.password.change.own`, `accounts.local.create`, `accounts.password.reset.other`, `settings.registration.edit`, and the `change_registration_mode` owner power with local passwords, and moved registration and admission history to `connections.edit`; version 8 was db28fc6cc2a9218711619f0033181f556a577185cef2266bc27349f6654c4173; JF-05 fix round: added `accounts.jellyfin.unblock`, owner-only by default and delegable, fresh interactive; version 7 was 93dda9f7d661a36b632dbea1676f12ceba423129cce604758350f015ecab0e20; JF-05: removed `settings.jellyfin_admission.edit` and the `change_jellyfin_admission_mode` owner power with import-only admission; version 6 was e454916ac4442dff6240bd18058258ebedd43b306ba90e20ea22a22732fd305d; JF-02: removed `reviews.import.server_path` with EDL and MCF path import; version 5 was 383640b07e76aa0f4be475a856a867d158315f721f0fad8bd6c9b3932d91eea3; JF-02: removed `scans.start.server_path`, `media_access.exceptions.edit`, and `media.identity.view` with Other Files; version 4 was 36ec098b3dd9112a1054eceee813a3553ed8cfe24084572000d05f361318bd46; JF-01: Radarr and Sonarr removed from the browse and connection matrix rows and the Radarr-or-Sonarr settings row; version 3 was 680ea337dc13878d03e5e3254591d275a7205c37d2470390f31c6b00c0c0cd65; wave 14: MU-40 removed `backups.restore.upload`, `.preflight`, and `.activate` and renamed the owner power to `download_backups`; version 2 was bd429d4bdd78a9a40f264a7eaabd23367d0ef515a30e2997a760fc3f32761389; wave 13: MU-38 makes `accounts.delete.other` require fresh interactive authentication; version 1 was a2c7fb61dfa952dbd89f42027fea0028e4abecef512ef4ca20378d594b578ac3) |
| Route/action inventory | `editarr-route-actions-22` | 211 registered method and path pairs (JF-06: removed `POST /api/auth/login`, `PUT /api/auth/account`, `POST /api/auth/register`, `GET` and `PUT /api/settings/registration`, `POST /api/users`, and `POST /api/users/{user_id}/password-reset`; version 21 had 218; JF-05 fix round: added `GET /api/jellyfin/blocks` and `POST /api/jellyfin/blocks/unblock`; version 20 had 216; JF-05: removed `GET` and `PUT /api/settings/jellyfin-admission` and `POST /api/users/{user_id}/role-sync`; version 19 had 219; JF-04: added the five public Jellyfin Quick Connect sign-in routes; version 18 had 214; JF-02: removed the eight Other Files scan, identity, review, export, and exception routes and the two EDL and MCF path import routes, and dropped the Jellyfin item-scan list's `source_path` parameter with its `media.paths.view` gate; version 17 had 224; JF-01: removed the 16 Radarr and Sonarr source, settings, review, export, and scan routes; wave 15: MU-42 narrowed the three legacy adjacent-sidecar deletes to `exports.default.delete` and added `media.paths.view` to the two item-scan lists, which use it for `source_path`; wave 14: MU-40 removed the in-app restore archive, preflight, and activate routes; wave 13: MU-38 added the account deletion preview and confirm routes and bulk deletion; wave 12: MU-35 added the two account-link proof and link routes and MU-36 added the review deletion preview and confirm routes; wave 11: MU-32 lets the publication-check capabilities admit sharing context reads; wave 10: MU-31 added the six personal/default export publication, download, discovery, and deletion routes, MU-30 added the owner-only role-sync reset, and MU-29 reclassified existing community routes; B2R adds two explicit review-repair routes; wave 9: MU-28 added the two audit history routes; MU-26 declared `GET /api/capabilities` and `GET /api/mcf-registry` evaluated; wave 8: MU-18 added one Users route and MU-23 added 18 community identity routes; MU-19, MU-20, MU-21, and MU-22 reclassified existing routes; MU-13 added key reveal and the three per-account key routes; MU-17 lets `POST /api/backups/jobs` exercise `backups.restore.upload`; MU-14 added the default-mode, personal, and per-account filter routes; version 5 combines the separate MU-14 and MU-17 version 4 changes; MU-15 added the 24 Users, permissions, registration, and media-access routes; MU-16 added Jellyfin sign-in and the six Jellyfin account routes) |
| Export layout and fixtures | `editarr-export-layout-1` | manifest.json SHA-256 c3063858e3ecf2f8982c66a1140f52ab7d806e737ff5c7854b413cdecccf987e |
| Jellyfin user fixtures | `editarr-jellyfin-users-1` | manifest.json SHA-256 c4fd21e52fa957d1e5794b4de53305ea35b095a53a673ca2a32e7b7c02d9af29 |

A contract change bumps its version and its pinned test. The pins live in
`tests/test_multi_user_capabilities.py`, `tests/test_multi_user_export_layout.py`,
and `tests/test_jellyfin_user_adapter.py`.

## Where each contract lives

Import from the defining module. The package `scanner/security/access` has no
runtime wiring yet.

- `capability_ids.Capability` names 134 capabilities. Values are persisted in
  overrides and audit rows, so they never change meaning.
- `catalog.CAPABILITIES` maps each capability to a `CapabilitySpec`.
  `catalog.DELEGABLE` holds the 121 that accept role defaults and overrides.
  `catalog.ACCOUNT_PROTECTION`, `catalog.INVARIANT_ROWS`, and
  `catalog.contract_digest()` complete the inventory.
- `vocabulary` defines `Role`, `Target`, `TARGET_RULES`, `Authentication`,
  `OwnerPower`, and `AccountClass`.
- `overrides.parse_override` and `overrides.parse_role_default` are the only
  way to turn a request into a storable override or default. A role default
  must be an actual boolean, and an effect or capability must be its exact
  string value.
- `routes` and `route_table.ROUTE_RULES` hold the route/action inventory and
  `inventory_gaps`, the fail-closed checker.
- `identity` defines `UserId`, `PermissionRevision`, `CredentialKind`,
  `Account`, `ProviderIdentity`, `ProviderLinkState`, and `Principal`.
- `records` defines `ReviewOwnership`, `ExportTarget`, `ExportProvenance`,
  `MediaDecision`, `AuditEvent`, and `AUDIT_RETENTION`.
- `api_shapes` defines `AccessErrorCode`, `ACCESS_ERROR_STATUS`,
  `Reauthentication`, and `CapabilityPayload`.
- `scanner/formats/export_layout.py` defines export IDs and sidecar paths.
- `tests/fixtures/multi-user/export-layout-v1/` holds the cross-repository manifest
  and thirteen payloads. `.gitignore` ignores `*.edl` everywhere except its
  `payloads/` directory, so every payload the manifest names is committed.

## Capabilities

Each matrix row expands into individually testable capabilities. A row with an
own/other split becomes a pair such as `scan_jobs.cancel.own` and
`scan_jobs.cancel.other`. Three rows marked Excluded or Outside scope map to an
`Invariant` instead.

A `CapabilitySpec` carries these fields:

- `granted` lists the delegated roles that hold it by default. The owner
  holds every capability and is never listed.
- `target` says what the capability acts on. `TARGET_RULES` decides whether the
  evaluator also applies the media decision and target-account protection.
- `authentication` is `any` or `fresh_interactive`.
- `owner_power` names one of the ten nondelegable powers, or is empty.
  A capability with an owner power cannot have a role default, and
  `parse_override` and `parse_role_default` refuse it.
- `matrix_rows` cites the exact matrix text. Only owner powers from the
  matrix's closing paragraph may have none.

Target-account protection applies to `other_account`,
`other_account_resource`, and `scoped_records` targets. Acting on an ordinary
user needs nothing extra. Acting on an admin also needs
`accounts.admins.manage`. Acting on the owner needs the owner power
`manage_owner_account`, which no other account can hold.

The tests read the expected defaults from the human documents. Each cell is
read through the capability's facet. For example, an `Own` cell grants the own
capability and denies the other one. `Ordinary users` grants the other
capability and relies on target-account protection. The decision log's
settings table is checked by minimum role. The nondelegable and
fresh-authentication phrases are matched in the matrix's closing paragraphs.

The owner powers are the eight in the matrix's first closing paragraph.
JF-05 removed Jellyfin first-login admission mode, and JF-06 removed
registration mode. 12 capabilities are owner-only and 15 need fresh
interactive authentication.

## Route inventory

`ROUTE_RULES` maps every registered method and path to one rule:

- `Public` covers `GET /api/auth/status`, `POST /api/auth/setup`, the Jellyfin
  sign-in and Quick Connect routes, and the seven setup-restore pairs. JF-06
  removed `POST /api/auth/login` and `POST /api/auth/register`. The setup-restore
  pairs are anonymous only while owner setup is required. The tests check them
  against `auth_routes` and `_setup_restore_request`.
- `AnyPrincipal` covers sign-out, the OpenAPI documentation, `/api/mcf-registry`,
  `GET /api/settings/filters`, which every account inherits live, and (MU-14)
  `GET /api/settings/filters/personal` and `GET /api/settings/filters/default-mode`,
  the caller's own effective defaults and the mode new accounts start in.
- `Gated` lists every capability a handler may exercise. Own and other variants
  are both listed, and the handler checks the one that matches the resolved target.

The anonymous static frontend is middleware-owned and stays in
`auth_routes.PUBLIC_STATIC_ROUTE_PATHS`. Editarr has no health route. The
checker fails on a registered pair with no rule and on a rule with no
registered pair. 9 capabilities have no route yet, for example
`accounts.link.own` and `ownership.transfer`. Their owning tasks add
routes and table rows together.

## Identities and provenance

- `Principal` is the actor. It wraps the current `Account`, the
  `CredentialKind` used (`session` or `api_key`), and the `PermissionRevision`
  read for the request. Durable jobs store the initiating `UserId` and
  revision, then recheck when they run.
- `Account` keeps the immutable `UserId`, role, status, disable cause, a
  permanent `LocalExportId`, and an optional `ProviderIdentity`. The type
  refuses an owner whose status and link disagree, because an owner is active
  exactly when it has a provider identity. It also refuses a provider without a
  linked state.
- `ProviderIdentity` is a Jellyfin server ID plus a canonical Jellyfin user ID.
  It is never matched by name.
- `ReviewOwnership.owner` is the current owner and the policy owner.
  `original_author` never changes on copy or transfer.
- `ExportProvenance` records the format, the `ExportTarget`, the actor, the
  source review and revision, the policy owner at export time, the original
  author, and the policy revision. `ExportTarget` is `PersonalTarget(user_id,
  export_id)` or `ItemDefaultTarget()`. Both targets publish under the current
  review owner's enforced policy. Only personal targets are regenerated when
  that policy changes.
- `MediaDecision` is `MediaAllowed(basis)` or `MediaDenied(reason)`. A denied
  target answers `not_found` unless its existence is already visible. MU-11
  added the reason `unresolved` for evidence that could not be read or parsed.
- `AuditEvent` stores the actor, credential, action, outcome, target user,
  media key, resource, and details. Details are a read-only snapshot taken at
  construction, so later changes to the caller's dict cannot add a key. Values
  are strings, integers, booleans, or null, and `dict(event.details)` is JSON.
  Detail keys that name a secret are refused. Retention is 90 days.

### Audit history reads

MU-28 exposes `GET /api/audit` and `GET /api/audit/{event_id}` through
`scanner/http/audit_history_api.py`. Both require an admin or owner holding
`audit.read`. Ordinary users receive 403 even if that capability was granted.
Successful responses are `private, no-store`, with `Vary: Cookie, Authorization`.

- `AuditFilters` accepts `page` (1 to 1000000), `page_size` (1 to 100, default
  25), optional `category`, registered `action`, `outcome`, positive `actor_id`
  and `target_user_id`, opaque `media_key`, and timezone-qualified `since` and
  `until`. Times are inclusive; start must not follow end. Unknown query fields
  and invalid values answer 422.
- `AuditEntry` carries `event_id`, UTC `occurred_at`, `category`, `action`,
  `outcome`, `actor_kind`, nullable `actor_id`, `credential` (type, not value),
  `target_user_id`, `media_key`, `resource`, and registry-validated `details`.
  Actor is attribution. Live action capabilities, target account, and media
  determine record visibility; an owner actor does not hide an ordinary
  target's history. Unknown or corrupt stored events are excluded, not returned
  as raw JSON. No response contains a host path or credential value.
- `audit.visibility.ACTION_CAPABILITIES` maps every `AuditAction` to its
  governing capability, all-of capability set, or an explicit owner-only rule. Own-account and
  detail variants distinguish key creation from rotation, personal exports
  from item defaults. Admins must
  currently hold every matching capability through live role defaults and
  explicit allow or deny overrides, in addition to account and media scope.
  Since JF-06, stored events of removed actions read under live capabilities:
  a credential change with `password` or `password_and_username` under
  `profile.edit.own`, an `account.create` with method `managed` under
  `accounts.jellyfin.import`, `account.password.reset` under
  `accounts.sessions.revoke.other`, and `settings.registration.change` and
  `settings.jellyfin_admission.change` under `connections.edit`, so only the
  owner reads them. Path create/update/reorder additionally require
  `media.paths.view`, matching the real dependency's response-disclosure check;
  deletion does not. Nondelegability comes from the capability registry's
  `owner_power`, not owner-only default grants: explicitly delegated path
  mutations and backup deletion can be read. Enforced `instance_defaults`
  policy events require both `filters.instance_defaults.edit` and nondelegable
  `filters.policy.enforce`, so remain owner-only. Mutation authorization is
  unchanged. This conjunctive requirement supersedes the second review's
  synthetic defaults-edit-only expectation by coordinator ruling.
  Nondelegable operations, unmapped actions, and missing or unknown variants
  remain hidden from admins; no historical fallback guesses an operation.
  A completeness test fails when a new action has no table entry. Reading
  history does not require the mutation's fresh-password proof.
- Refusal writers retain known allowlisted operation codes, never submitted
  values: credential `changed`, key-issue `replaced`, managed-create `method`,
  account `status`, media-scope and permission `change`, connection/path `change`,
  and community identity `change`. Shared initial, hidden-target, dependency,
  and write-lock refusals preserve these codes when the producer knows them.
  Ambiguous existing nondelegable refusals remain owner-only. No event schema
  or detail allowlist changes. Mechanical tests compare active dependency
  closures, operation descriptors, and conditional checks with visibility.
  Login history's `accounts.list` and background Jellyfin state history's
  `accounts.jellyfin.sync` are explicit history-policy choices, not mutation
  capabilities held by anonymous/system actors. MU-31 now writes `export.publish`
  and `export.delete`. Own personal publication uses
  `exports.personal.publish.own`, not review-view authority alone. Publication
  history also requires the live source-review view capability and, for another
  user's source, `reviews.export.other`. Personal-target and default capabilities
  remain separate. The four reserved regeneration and community-publication
  mappings have no current writers and still require a producer audit.
- `AuditPage` contains `events`, scoped `total` and `pages`, `page`, `page_size`,
  `retained_since`, and `retention_days` (90). Scope and validation precede
  filters, counts, and pagination. An empty selection has zero pages. Reads
  exclude `occurred_at <= now - 90 days` even before physical retention runs.
- `audit.history.read_history` collects MU-11 media evidence outside the shared
  write lock and uses `fresh_authority_write` to recheck principal, account
  protections, referenced media, and provider authority under that lock.
  Consistency compares only visibility authority and referenced accounts and
  media, not every retained event ID. Rows are re-read under the lock, so
  unrelated audit writes do not exhaust the evidence retries. Authority
  changes still fail closed. Missing media denies nonowners. Hidden, absent,
  expired, and invalid event UUIDs answer 404 when the caller has audit access;
  loss of `audit.read` answers 403. Malformed UUID syntax answers 422.
  Visibility changes that exhaust the bounded retries answer 503
  `audit_history_busy`, with `Retry-After: 1` and `private, no-store`.
- The web route is `#/audit`, with `#/audit/{event_id}` for a detail deep link.
  It is separate from diagnostic downloads and settings pages. Results live
  only in the current mounted view. New reads clear previous results, abort
  abandoned requests, and ignore late replies. MU-19's identity/revision
  boundary discards the whole view on an account or permission change. Nothing
  is persisted in browser storage or a shared filtered cache. A busy response
  clears stale events and offers Refresh without claiming permission loss.

## Jellyfin provider snapshots

MU-08 reads verified Jellyfin state through
`scanner/integrations/jellyfin_users.JellyfinUserAdapter`. The types live in
`jellyfin_user_snapshot`. MU-16 persists sync state (see Jellyfin accounts and
admission) and MU-11 owns media decisions.

- A `ProviderIdentity` uses the server ID from `/System/Info/Public` and the
  user GUID Jellyfin returns, both canonical 32-digit lowercase hex. A name only
  selects the account to try at sign-in.
- Every operation reads the server ID again. An adapter refuses any server
  other than the first one it saw, and every `UserDto` and
  `AuthenticationResult` must name the same `ServerId`. Otherwise the result
  is `server_mismatch`.
- `JellyfinUserSnapshot` holds the identity, display name, avatar tag,
  administrator and disabled flags, a `MediaPolicy`, and `verified_at`. It has
  no email or timezone, so applying it cannot overwrite those preferences.
- `verify_user` returns `JellyfinUserSnapshot`, `UserDeleted`, or
  `Unverified(reason)`. Only the first two carry a time. Deletion requires the
  linked server ID, a 404 for the user, a valid user list without the ID, and
  the linked server ID read again after that list. A different server ID is
  `server_mismatch`, never deletion.
- `item_access` returns `allowed` only when Jellyfin opens the item for that
  user (`/Items/{id}?userId=...`). A server-key query labels the rest
  `restricted` or `not_found`; it never allows. Jellyfin 12's batch
  `/Items?Ids=...&userId=...` ignores library access, so it is not used. The
  server ID is read again before the decisions are returned.
- `authenticate` returns `CredentialProof(user, session)` or `Unverified`.
  The sign-in result must carry a nonempty `AccessToken`, the server's
  `ServerId`, and a valid `User`. The adapter then logs out, retrying a
  timeout, unreachable server, or server error up to three attempts in all.
  `session` is `ended` or `may_remain`. With `may_remain`, Jellyfin keeps that
  session until Editarr's next sign-in replaces it or an administrator ends it.
  `Unverified` makes no claim about a session. Neither the password nor the
  access token is kept.
- No request follows a redirect or runs an HTTPX auth flow, because the
  adapter builds its own client; callers and tests may replace only its
  `transport`. Like the catalog client, the adapter honors `HTTP_PROXY`,
  `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, and `SSL_CERT_DIR`,
  so a proxy the environment names carries its requests, passwords included.
  A `.netrc` file never adds credentials. A 3xx is `redirected`.
  `ResponseLimits` caps wire and decoded bodies at 8 MiB for JSON and 5 MiB
  for images, and each exchange at 30 seconds. Gzip and deflate bodies are
  decoded in bounded pieces; another content coding, a corrupt stream, or one
  without its end is `invalid_response`. A larger body is
  `response_too_large`. A reply that completes after its deadline, including
  an empty body or a 404, is `timeout`. JSON nested too deeply, or text that
  is not valid Unicode, is `invalid_response`.
- `avatar` reads the server ID again before returning an image or `None`.

Fixture `editarr-jellyfin-users-1` in
`tests/fixtures/multi-user/jellyfin-users-v1/` is synthetic and shaped after
Jellyfin 12.0.0. `tests/jellyfin_user_boundary.JellyfinBoundary` serves its
states and faults over local HTTP. Passing it is not live-server evidence.

## Media access

MU-11 decides which items an account may reach in `scanner/security/media_access`.
Catalog, review, worker, and management code use it; no route calls it yet.

- A `MediaTarget` is built from the Jellyfin server's own record by
  `jellyfin_target`. It carries a `MediaKey` (source kind, server GUID, item
  GUID), its local `SourceRef`, and resolved local paths. `MediaKey` parsing
  refuses anything that does not name exactly one item.
- Matching reads only server-reported paths (including every Jellyfin
  version). A target without its own Jellyfin item matches by path alone.
  Scan associations are never read. Paths
  compare as given and as their real path in the host's case rules, so
  symlinked spellings correspond; file identity is not used. A provider ID
  shared across libraries matches every copy. A Jellyfin target of another
  server is `unresolved`.
- Linked decisions rest on three reads of the linked server, each checked
  against the server ID before and after: `verify_user` for the user's
  `JellyfinUserSnapshot` (its `MediaPolicy` names the libraries the user may
  open), `JellyfinUserAdapter.catalog(server_id)` for a `JellyfinCatalog`, and
  `library_folders(server_id)` for `LibraryFolders` (library GUID to folders).
  Only `parse_catalog` builds a `JellyfinCatalog`, from the pages of complete
  server-key `/Items` listings and the later parts of stacked items: every
  page must echo its `StartIndex` and its listing's `TotalRecordCount`, the
  items must add up to that total, no item may repeat, every episode's series
  must be listed, every listed `PartCount` above one must be matched by that
  many parts with paths, and any malformed item rejects the whole catalog.
  There are two listings, both with `CollapseBoxSetItems=false` (otherwise
  Jellyfin replaces a collection's movies and series with its `BoxSet`) and
  `EnableTotalRecordCount=true`. `FILE_LISTING` selects
  `MediaTypes=Video,Audio,Photo,Book`: every media file item whatever its
  type, including local trailers and other extras, songs, photos, and kinds
  Editarr does not know. `SERIES_LISTING` selects `Series` for episode
  context. Items of media type `Unknown` (folders, series, collections, and
  playlists, whose `.m3u` file only lists other items) are not media Editarr
  scans or serves and are never targets. A stacked movie lists only its first
  part; `read_catalog(read, ...)` reads `/Videos/{id}/AdditionalParts` for
  each and adds the part paths to that item's `versions`. Jellyfin 12.0.0 can
  count a folder in an unfiltered listing without delivering it
  (`tests/fixtures/multi-user/jellyfin-catalog-v3`, recorded from the pinned
  image). A server-key listing includes local trailers; a user-token listing
  omits them. Pages follow
  `DateCreated,SortName`; Jellyfin has no sort by ID. After each listing's
  last page the adapter reads its first page again and returns `Unverified`
  if its total or item IDs changed, so the caller retries. Only `parse_library_folders` builds
  `LibraryFolders`. It leaves out every `boxsets` library: Jellyfin's
  Collections library (`/config/data/collections`) holds collection metadata
  and no media, so its folder neither contains a target nor counts as
  missing. A merged version leaves the listing; its path stays indexed
  through the primary item's `MediaSources`, but its own provider ID is not
  listed, so an outside copy matched only by that ID is unmatched (residual
  accepted on 2026-10-22). Callers wrap them as `JellyfinCatalogIndex(catalog,
  resolve)` and `LibraryRoots(folders, resolve)` with the Jellyfin path
  mappings.
- `load_subject` reads nothing for the owner, the effective local scope for a
  local account, or the link state for a linked account.
  Unreadable stored values grant nothing.
- `decide_media(subject, targets, *, user, catalog, libraries, items, now)`.
  For a linked account, a `SERVER_MISMATCH` from any of the three reads denies
  every target as `server_replaced`. A read that is missing, failed, of another
  server or user, or otherwise not the sealed type denies every target as
  `unresolved`; one older than 24 hours denies every target as
  `verification_expired`. This includes the decision of 2026-10-22: when the
  linked server's library folders cannot be read, all of that user's media is
  denied, matched Jellyfin items included. A library folder that a complete
  read names but this host lacks is not a failed read (see Placement). The
  owner and local accounts need none of these reads.
- Placement. Every target without its own Jellyfin item is placed against the
  library folders. Each mapped folder, and every directory above each target
  path as spelled and above its real path, is identified by `(st_dev,
  st_ino)`. Folder and target identities are read twice; any difference
  between the reads, an unreadable or zero-ID folder, a missing, relative,
  unreadable, or zero-ID path, or a target with no path, is `unresolved`. A
  path that shares an identity with a folder lies in that folder's libraries.
  A folder missing from this host (not found, or not absolute after mapping),
  often a library Editarr does not mount, is skipped under the coordinator
  ruling of 2026-10-22: files in the other folders are placed as usual, and a
  target outside them is `OutsideMounted` rather than proven outside.
- Correspondence. A target inside a library the user's policy does not open
  is `not_in_scope` without an item read. A target inside only open libraries
  must be the file of a listed Jellyfin item (a path match); otherwise it is
  `library_unmatched`. A target outside every folder that matches a listed
  item by path is decided by those items; one that matches nothing in the
  complete listing is `Unmatched`. An `OutsideMounted` target is decided by its
  matches the same way, but one that matches nothing is `unresolved`.
- Owner: always `owner`. Local: the target's source must be in the scope, which
  is the account's own selection or else the live default. A Jellyfin item
  without a known library is `unresolved`.
- Linked: a replaced server or expired verification denies everything without
  a read. `Unmatched` is always denied as `unmatched`. A matched target needs Jellyfin to positively allow every
  matched item through one item read per call; any restriction denies it as
  `not_in_scope`, because the per-item route does not say whether a library or
  parental limit applied, and any other answer is `unresolved`. Fresh
  `SERVER_MISMATCH` from the item read denies every target in the call. Item
  evidence older than 24 hours is `verification_expired`. The basis is
  `jellyfin_grace` during an outage grace period.
- Caller obligations (MU-16, MU-20). Pass the three reads, or the adapter's
  `Unverified`; MU-16 may cache each for up to 24 hours and must keep the last
  complete catalog or pass `Unverified` after a failed refresh. The reread
  catches a changed total and any change on the first page; a removal and an
  addition beyond the first page that keep the total can still drop an item.
  Inside a library that only denies; outside every folder it can lose a
  provider-ID denial. `decide_media` does not read a snapshot's
  `is_disabled`; MU-16's link states decide what a disabled Jellyfin user may
  reach. A decision is a bare value: callers must bind the
  eventual read or sidecar write to the authorized file and recheck before
  I/O. Placement compares directories, so a hard link outside the folders is
  outside, and a second mount of a library share with a different device ID is
  outside; path mappings must name the mount Jellyfin uses.
- `management` changes scopes after `require` with fresh interactive
  authentication: `media_access.defaults.edit` for the default and
  `media_access.user.edit` for another local account. A missing account is
  checked as a plain user before `not_found`. Scope changes refuse a linked
  account with `UnsupportedMediaAccessChange`. Every change is idempotent and bumps `permission_revision` for
  each account whose access changed. MU-15 exposes them over HTTP.

Fixture `mu11-media-access-4` in `tests/media_access_support.py` adds paths,
provider IDs, media types, complete `/Items` listings, the baseline users'
policies, and two library folders to the `editarr-jellyfin-users-1` items,
materializes the synthetic files under a private temporary root, and adds
path-only targets inside and outside the folders.
`jellyfin_items(records, boxsets, parts)` answers `/Items` by filtering on
`IncludeItemTypes` and `MediaTypes`, grouping each collection's members into
its `BoxSet` unless `CollapseBoxSetItems=false`, and answers
`/Videos/{id}/AdditionalParts`, as Jellyfin 12.0.0 does.

## Export layout

For `/media/Movies/Movie/Movie.mkv` and export ID `<id>`:

| Scope | EDL | MCF |
| --- | --- | --- |
| Personal | `edl/<id>/Movie.user-<id>.edl` | `mcf/<id>/Movie.user-<id>.mcf` |
| Item default | `edl/Movie.edl` | `mcf/Movie.mcf` |
| Legacy, never selected | `Movie.edl` | `Movie.mcf` |

A Jellyfin export ID is the user GUID as 32 lowercase hex digits, which matches
the plugin's `Guid.ToString("N")`. Provider input may use N or D form in any
case. Stored values and path segments must already be canonical. The nil GUID
is invalid. A local export ID is `local-` plus 32 random lowercase hex digits,
allocated once and never reused. The hyphen cannot appear in a canonical
Jellyfin ID, so the two namespaces never collide.

The media stem is the basename without its final extension. A name with no
dot keeps its whole name. `.mkv` and `Movie.` are refused as
`unsupported_media_name` because Python and .NET derive different stems for them.

Selection runs per format and never reads the legacy file. The plugin
`lstat`s the personal path. `ENOENT` and `ENOTDIR` mean absent. A regular file
is read and validated. A read failure is `personal_unreadable`, and a
validation failure is `personal_invalid`. Any other entry type, including a
symbolic link or directory, is `personal_invalid`. Only an absent personal file
lets the plugin try the default path, under the same rules with `default_*`
outcomes. If neither exists, the outcome is `none`. No error falls back.

The manifest lists the principals, ID inputs, path segments, media names,
exact POSIX and Windows paths, payload digests, EDL grammar vectors, and 19
selection cases. Every valid EDL payload is Editarr exporter output. Every
valid MCF payload survives a parse and export round trip unchanged, and
`personal-a.mcf` is a copy of `tests/fixtures/mcf/exported-1.1.mcf`.

EDL validity is the public plugin's `EdlParser.Validate` at commit 7797442,
applied to `File.ReadAllLinesAsync` output. The manifest's `edlGrammar`
section states the rules and 20 vectors derived from that source, and names
the inputs it leaves unpinned. Editarr's import parser differs: it ignores an
unsupported action row, so it accepts `0 1 1\n0 1 999\n`, which the plugin
rejects. Selection therefore never uses the import parser, and that payload
at a personal path is `personal_invalid` with no fallback. The Python
reference in `tests/test_multi_user_export_layout.py` is read from the plugin
source. MU-05 runs the same vectors on .NET; nothing here is .NET evidence.

### Canonical publication discovery

MU-31 exposes authenticated, reloadable canonical status for MU-37. No new
migration follows `0021_export_snapshots` in this fix round.

- `GET /api/reviews/{review_id}/exports` returns `{publications: [...]}` for
  the visible source review and its physical media version.
- `GET /api/exports?media_item_id=<persisted-id>&media_source_id=<version-id>`
  returns the same shape for one persisted media version, including retained
  publications after source-review deletion. An omitted or empty
  `media_source_id` selects the unversioned source, not every version.
- `GET /api/exports/{export_id}` returns one publication. Canonical POST
  publication responses use that same record. Compatibility POST responses
  add `publication` without removing their review fields.

`ExportPublication` contains `id`, `publication_id` UUID, `publication_revision`,
`media_item_id`, `media_source_id`, `format`, `scope`, `target`, `target_user_id`,
`target_export_id`, `actor_id`, `policy_owner_id`, `original_author_id`,
`policy_revision`, `review_id`, `exported_format_revision`, `content_sha256`,
`current`, and `outdated`. It never contains destination paths, snapshot JSON,
or retained content. Rows are ordered by export ID. Formats, users, defaults,
and physical versions remain distinct.

`target` records the publication-time choice `self`, `review_owner`, or
`item_default`. It does not relabel a row relative to the reader. The existing
snapshot JSON retains this choice. Earlier 0021 snapshots infer the equivalent
destination choice from scope, actor, and target user. `target_user_id` identifies
whose personal destination it is. A publication is current when its source
review exists, the exported revision matches that format's current content
revision, and its current-owner policy matches. Retained personal snapshots can
also become current after successful regeneration. Queued, running, failed, or
withdrawn publications are outdated. EDL and MCF revisions remain independent.

Discovery uses the direct status endpoint's live media, target-account, and
capability rules. Personal reads exercise `exports.personal.download.own` or
`exports.personal.delete.other`; defaults exercise `exports.default.publish`.
Unauthorized rows are omitted. A hidden review or media ID returns the same
404 as an absent ID. No list count reveals omitted rows. Review discovery also
requires source-review visibility. Direct status and version discovery redact
source identifiers when that review is hidden, without exposing its snapshot.

Authenticated review, catalog-item, and version-status responses add
`ExportStatus.publications` from this discovery service. `legacy_exports` keeps
the format-keyed adjacent-sidecar summaries. The compatibility `exports` map
uses the caller's own canonical row for each format when present and otherwise
retains legacy status. It never substitutes another user's row or an item
default. Consumers use `publications`, not that compatibility map, for target
selection and canonical deletion. Deletion removes the row from subsequent
discovery and still requires its UUID plus publication revision.

Catalog discovery keys persisted media by server, provider item ID, and item
type. Sonarr series never receive episode publications or format badges.
An optional per-item decoration that cannot authorize its stored media keeps
the already filtered catalog record and its legacy status, with no canonical
publications. Direct Jellyfin status routes check item
visibility before reading local status. Hidden and absent IDs both return 404.
Discovery reads account authority and publications in one independent SQLite
read snapshot. It does not acquire a writer transaction or commit a GET.
The internal `AuthorizedPublication` freezes the projected publication and
compatibility summary as JSON values inside that snapshot. Catalog and review
consumers unwrap these values without rereading export rows by integer ID.
Publication UUID, media version, format, target, and scan provenance therefore
remain bound to the authorized snapshot after concurrent deletion or row-ID reuse.
Both values use the direct status endpoint's source-review visibility projection.
Hidden source-review IDs and exported revisions are null in catalog responses too.

File fsync remains mandatory. All canonical publication, deletion, rollback,
and reconciliation directory syncs use `scanner.runtime.durability.sync_directory`.
Every publication attempt syncs all canonical ancestors through the media
directory, including entries left behind by a failed attempt. Existence alone
does not prove directory durability. The same
public helper serves recovery snapshots. It fsyncs POSIX
directories and avoids unsupported Windows CRT directory opens. Supported
filesystem failures still propagate.

### Personal regeneration (MU-34)

Migration `0022_export_regenerations` follows `0021_export_snapshots`. Table
`media_item_export_regenerations` (row `ExportRegenerationRow`) keeps one job per
personal publication, keyed by `export_id` with `ON DELETE CASCADE`. Columns are
`state`, `due_at` and `last_attempt_at` (Unix seconds), `attempts`, desired
`policy_owner_id` and `policy_revision`, `claim_token`, and `failure_reason`.
States are `queued`, `running`, `withdrawing`, `succeeded`, `failed`, and
`withdrawn`. Only `queued`, `running`, and `withdrawing` have a due time, and the
partial index `ix_export_regenerations_due` holds exactly that work. Failure
reasons are `regeneration_failed` (withdrawing or withdrawn) and
`retries_exhausted` (failed, file untouched). CHECK constraints enforce each pairing.
The upgrade moves every 0021 JSON job into the table exactly, maps an old failed
job to `withdrawn` when its content is null and to `failed` otherwise, removes the
JSON `regeneration` and `recovery_required` keys, and refuses unknown job shapes
for snapshot recovery. Backups carry the table.
Queue admission occurs inside the policy-change transaction. Scheduler
reconciliation also detects stale policy/owner stamps after restart or transfer.
A job always follows the publication's current policy identity. When the
publication's stamps already match it (an owner change reverted, or a restored
policy revision), any job for another identity is dropped, or marked `withdrawn`
again if the file was already withdrawn. A claim realigns a job whose identity is
out of date before spending an attempt, so such a job never exhausts or loops.
The current review owner controls policy, never the publishing actor or target.
Deleted-review exports use the retained policy owner and validated saved base,
source findings, evidence coordinates, and MCF metadata. Missing proof fails
closed. Shared defaults remain outside the regeneration guarantee.

`scanner.persistence.repository.export_regeneration` exposes
`queue_regenerations(db, export_ids=None, review_ids=None)` (stage only),
`claim_regeneration(db, export_id=None, now=None)`
(commit a fresh claim), and `retry_regeneration(db, export_id, publication_id)`
(commit an explicit failed-job retry). These are internal service operations,
not authorization boundaries or HTTP routes. Successors must admit the live
actor and destination capability before user-triggered use. Retry does not
restore withdrawn bytes. It requeues the publication's current policy identity
and resets the attempt budget and delay. A new policy
revision can supersede a failed job. Writers changing review ownership must queue
the affected review IDs in the same transaction. A copied review has no existing
publication; it cannot change its source review's publications.

`RegenerationJob` pins the export ID, publication UUID/revision, destination,
policy owner/revision, and token. `settle_regeneration(db, job, cancelled=None)`
returns `succeeded`, `failed`, `stale`, or `retry`. It rereads all pins under MU-31's
publication OS lock and SQLite writer lock. A reclaimed delivery, replacement,
withdrawal/deletion, reused integer ID, or newer policy/owner cannot be
overwritten by an old worker. Cancellation leaves durable work for restart.
`ExportRegenerationScheduler(session_factory, data_dir=settings.data_dir)` owns the two-second poll,
starts only after setup/recovery admission, and drains or cancels on shutdown.
Each tick holds the maintenance worker lease for the configured data directory,
including when the database URL points elsewhere. An idle claim is one indexed
lookup of the earliest due job, without the publication or writer lock, so its
work does not grow with retained publications. Due work is rechecked under both
locks. Claims take the earliest due time and then ID; new and retried-by-request
jobs are due at once. Claims persist their next attempt
before work starts, with exponential delays starting at five seconds and a
five-attempt limit. Transient SQLite and maintenance errors retain the old bytes
and delayed claim. Exhausted claims become failed without modifying files.

Retained rendering reuses the canonical policy and format
serializers. Publication, replacement, withdrawal, rollback, and hard-exit
recovery use the existing durable sidecar journal. Old bytes stay active during
preparation. Regeneration always renders the published review snapshot under the
current source owner's policy. It never publishes later live edits or advances
`exported_format_revision`, source links, sources, or the retained review snapshot.
An incomplete live draft cannot withdraw a regenerable publication. Successful
replacement advances the publication UUID/revision and records system `export.regenerate`.
Confirmed failure removes only the personal file, retains its provenance row
with null content, advances its UUID/revision, and records `write_failed`.
Withdrawal never erases ownership evidence: the snapshot keeps `owned_sha256`, the
digest of the last sidecar Editarr wrote at the retained path, until a later
publication or successful regeneration replaces it. Publication, retry, deletion,
and regeneration treat bytes matching that digest (or the served content) as
Editarr's and may replace or remove them; any other bytes refuse with
`export_file_collision` and stay untouched.
Withdrawal acts only on the retained destination, and only while its directory
still resolves to itself. A destination resolved from media is compared with the
retained path before any journal recovery, so a redirected library is refused
without changing either directory. Foreign bytes at the retained path complete
withdrawal without touching any file. While the media file exists, regeneration
recreates a missing export directory through the same validated creation and
ancestor sync as ordinary publication. Absence is proven only by ENOENT from
`lstat` at the retained path itself (and its recovery journal); no containing-directory
or media heuristic is used. That withdrawal finishes `withdrawn` without a file
operation and audits `retained_path_absent`. Any other error (EACCES, EIO, ENOTDIR,
an unreadable or invalid journal, a redirected or symlinked path, unlink or write
denied) stays `withdrawing`, keeps the served bytes and its due backoff, and each
retry regenerates first, then withdraws. An ENOENT that was only an unmounted share
stays recoverable: the hourly sweep re-queues a `withdrawn` publication whose
owned bytes reappear at the retained path for the current policy, so the claim
replaces them with the compliant file when the media resolves and removes them
otherwise. Deleting a publication acts on the retained path without requiring the
media file, and owned bytes there are removed with the row. ENOENT there keeps the
row as a deleted record: null content, `owned_sha256` kept, its job `withdrawn` with
reason `deleted`, the same publication UUID/revision, and one `export.delete` audit.
Repeating that DELETE while the path is still ENOENT returns 204 and changes nothing.
A media folder that cannot be resolved (a symbolic-link loop or a file in its
place, ELOOP or ENOTDIR) is not absence: DELETE refuses with 409
`export_destination_unavailable` and changes nothing.
A deleted record is never regenerated or retried. The sweep removes its owned bytes
when they reappear and keeps the record; deletion removes them with the record, and
publication to the same key replaces them and reuses the record without a
replacement confirmation. Publishing another version to the same path replaces a
deleted record of the other version in the same transaction. Foreign bytes still
refuse with `export_file_collision` and stay untouched. Migration 0022 (amended
before its first merge) accepts reason `deleted` only for state `withdrawn`.
Journal recovery receives the committed content and the ownership digests of the
row and any replaceable deleted record at the path. A hard exit after replacing
returned owned bytes but before the commit leaves null committed content; recovery
admits it only when the journal's old digest is one of those digests and the file
matches a journal digest, then removes the file so the next claim, publication, or
DELETE proceeds. Foreign bytes, an unowned old digest, and symbolic files still
refuse with `export_recovery_required`.
Audit errors propagate with durable delayed
work rather than claiming success. `sweep_regenerations(db)` discovers stale
stamps, journals, and returned owned bytes outside the publication and SQLite
writer locks at startup and hourly. It rechecks each candidate under both locks. A
journal that cannot be
inspected or recovered is logged for that row, which is still queued for its own
claim; it never blocks other rows or claims. A failed sweep retries after a minute
without delaying claims. Ordinary idle ticks do not sweep
retained publications. Default fallback is unchanged.

`ExportPublication` adds `regeneration_state` (also `idle` for existing rows),
`regenerating`, `failed`, `failure_reason` (only `write_failed`), `retryable`,
`serving`, and `desired_policy_revision`. `content_sha256` becomes nullable for
withdrawn publications. `withdrawing`, `failed`, and `withdrawn` jobs all read
as `failed`, except a deleted record, which reads `idle`, not serving, not
retryable, with no desired policy revision. `serving` stays true until a withdrawal is durable, because it
describes retained committed bytes, not an external filesystem health probe.
`retry_regeneration` accepts any of the three except a deleted record. These fields use the existing redacted
`AuthorizedPublication` discovery snapshot. No paths, snapshots, exception
messages, notifications, or retry route are added. Audit list, counts, and
details still require the action's current source and destination capabilities.
Backups retain regeneration jobs and provenance.

### Review deletion with file choices (MU-36)

`GET /api/reviews/{review_id}/deletion` returns `ReviewDeletionPreview`:
`review_id`, `revision`, `confirmation` (64 hex), `files`, `legacy_formats`, and
`scan_ids`. Each file is `{publication: ExportPublication, choices}` with choices
from `retain` and `remove`. `POST` to the same path takes
`{revision, confirmation, files: [{export_id, choice}]}` and returns
`ReviewDeletionResult` `{review_id, retained, removed}`. Both require review DELETE
(own or other) and answer a hidden or absent review with the same 404. API keys
may call them, as they may call plain DELETE.

A file is listed when the direct export status rules let the caller read it.
`remove` is offered when `require_export(..., deleting=True)` passes:
`exports.personal.delete.own`/`.other` or `exports.default.delete`. Every other
publication of the review is hidden. It is never listed, counted, or covered by
the confirmation, and it is always retained, so changes to it neither invalidate
nor reveal anything. Legacy adjacent sidecars are always retained; only their
formats are listed. A deleted record (an explicit export DELETE that found its
retained path absent) has no file to choose for. It is never listed, counted, or
covered by the confirmation, does not block plain DELETE, and is not reported in
`retained` or `removed`. Deleting the review detaches it like a retained file and
writes no new audit event, because its `export.delete` was already recorded. It
keeps its ownership digest, so owned bytes that reappear at its path are still
recognized and removed.

The confirmation is a SHA-256 over the review ID, revision, owner, the owner's
policy revision, and each visible file's ID, publication UUID, publication
revision, and permitted choices, plus visible legacy rows. Preview and execution
compute it in the same `ReviewAccess.writing` gate, with media evidence for the
review, its scans, and each publication gathered before the writer lock. A
changed review returns `stale_review_revision`; any other difference returns 409
`stale_review_deletion`. `files` must name each listed file once: a missing,
duplicate, or unlisted ID (including a hidden one) returns 422
`review_deletion_choice_required`. An unpermitted `remove` returns 403
`capability_denied`. Nothing changes on any refusal.

Retaining clears `review_id` and `exported_format_revision` (the existing CHECK
pairs them) and keeps the bytes, snapshot (`source_review_id`, review revision,
retained base and sources), `policy_owner_id`, `original_author_id`, actor, job,
and publication UUID/revision. Discovery by media version keeps listing it, and
MU-34 regeneration and withdrawal continue under the retained policy owner. A
personal publication whose stamped policy owner differs from the review's owner
(an ownership change still regenerating) records the review's owner in
`intended_policy_owner_id` (0024), visible or hidden, so it keeps regenerating
for that owner; no refusal discloses it (MU-38 fix 1, closing R1).
`export_regeneration.policy_owner` resolves the live review owner, else the
intended owner, else the stamp; a new publication clears the intended owner.
Removal is MU-31/MU-34 deletion: owned bytes at the
retained path are removed with the row. When a listed file's path is ENOENT at
execution, it becomes a deleted record detached from the review, is reported in
`removed`, and stages `export.delete`.

All row changes, the audit events, and the review deletion commit once. Removed
files go through the sidecar journal, are restored if anything fails before the
commit, and a hard exit is reconciled by the next writer at that path. Each
retained canonical file stages `export.retain` and each removed file
`export.delete`, with the deleting user as actor and the review revision.
`export.retain` uses the export detail registry; reading it requires
`exports.personal.download.own` or `exports.personal.delete.other` for personal
targets and `exports.default.publish` for defaults.

`review_disposition.plan_deletion(db, review, authorize)` and
`execute_deletion(db, plan, choices, access)` are the reusable service for MU-38.
`authorize(row)` returns the permitted `FileChoice` set or None for hidden. Call
both inside one writer transaction, under `publication_lock` when removing.
`review_deletion.preview_review_deletion` and `delete_review` are the caller-scoped
HTTP entry points.

### Plugin routes

The public plugin serves the layout above from two routes. MU-09 added
`GET /Plugins/Edl/ByItem/{itemId}?mediaSourceId=` and MU-12 added
`GET /Plugins/Edl/Mcf/ByItem/{itemId}?mediaSourceId=`. The requester comes
from the Jellyfin session. A signed-in user gets the personal file, then the
item default only when the personal file is absent. A server API key gets the
default only and never learns whether personal files exist. A request with
neither gets 403. Responses are the exact file bytes (EDL as
`text/plain; charset=utf-8`, MCF as `text/vtt; charset=utf-8`) with an ETag
over format, requester, item, version, and content, and
`Cache-Control: private, no-cache`. Status codes: 304, 404 for an invisible
item, a foreign version, or no export, 422 for an invalid file (including a
link, directory, or named pipe at the final path, or more than 1 MiB), and 500
for an unreadable file. MCF validity is Editarr's strict parser (ASCII digits)
without a media duration; the plugin is looser only in two URL host checks.

## API shapes

Errors keep the existing access-policy envelope `{"code": ..., "detail": ...}`.
`ACCESS_ERROR_STATUS` maps each `AccessErrorCode` to a status. Only
`authentication_required` and `invalid_api_key` use 401. Reauthentication
failures use 403 so a client never treats them as a signed-out session.

A fresh-interactive request carries `Reauthentication(password)` and must use a
session. The password is the account's Jellyfin password. The server verifies
it with Jellyfin in that same request
(`reauthentication.verify_linked_password`). An account without a Jellyfin link
cannot reauthenticate. An API key gets
`interactive_session_required`. Repeated wrong passwords for one account or
client address get 429 `reauthentication_throttled` (MU-13).
That response carries `Retry-After: <seconds>` and `Cache-Control: no-store`.
Seconds are rounded up from the remaining sliding-window wait until both the
account and address buckets admit another attempt. Error codes and bodies do
not change. `AccessDenied(..., retry_after=seconds)` carries that header through
the global exception handler, API-key responses, and Users responses.

`CapabilityPayload` returns the contract version, the permission revision, a
principal summary, and the capabilities granted before target narrowing.
Clients hide controls from it and refetch when the revision changes.

## Activation and route coverage (MU-42)

MU-42 removed the temporary owner-only integration barrier, its
`EDITARR_MULTI_USER_VERIFICATION` launch opt-in and data-directory marker, the
`multi_user_unavailable` error code, and the `app.state.evaluated_routes`
declarations. Every launch admits every active account.

- Admission. `access.decisions.admission_error(status)` refuses a disabled
  account with `account_disabled`. It runs after Jellyfin proves the credentials
  at sign-in, for every
  session in the access middleware and auth endpoints, for every API key, and
  for every queued job (`scan_jobs.authority.decide_job`).
- Defaults. Every valid first Jellyfin sign-in creates a linked
  account; JF-05 removed import-only admission, and revision
  `0028_jellyfin_admission` records one system `settings.jellyfin_admission.change`
  event (`mode` `first_login`, `previous` `import_only`) where it was on. JF-06
  removed local registration with revision `0030_remove_passwords`, which records
  no event.
- Route coverage. The middleware authenticates and checks interactive-only
  routes, origin, and CSRF. It no longer authorizes by
  route. Each `Gated` route's handler resolves its principal through
  `request_access` and decides each target it touches.
  `tests/test_multi_user_route_actions.py::test_every_gated_handler_resolves_its_principal`
  walks every gated route's FastAPI dependency tree and fails for a handler that
  never resolves a principal. JF-06 removed `PUT /api/auth/account`, which was
  the one named exception. Together with the inventory check, an
  unclassified route and an unchecked gated handler both fail deterministically.
- Historical audit rows. Sign-in refusals that development builds stored with
  reason `multi_user_unavailable` still read back; no new event uses it.
- Background work. A handler starts work that outlives its request only through
  `scanner/http/background.py` (`start_thread`, `submit`), naming its authority:
  the initiator's gate or a `SystemWork` reason (`scan_dispatch`,
  `setup_restore`). `scan_jobs.operation_gate.OperationGate` is the
  instance-operation counterpart of `JobGate`: it carries the initiating user
  and the capability the request was decided on, reloads both on each check,
  and raises `JobRefusedError` for a disabled account or a lost capability.
  `POST /api/models/prefetch` runs its acquisition under an `OperationGate` for
  `models.download`, checked before fetching, on progress, and under the write
  lock at install. `POST /api/backups/jobs` checks `backups.create` before the
  export and before the archive becomes ready; a refusal ends the job `failed`
  with "Stopped: the account that started this backup may no longer create
  backups." and removes the archive. While the HTTP server runs
  (`model_acquisition_authority.request_server`), acquisition with no bound gate
  is refused with `download_not_permitted`; unbound acquisition remains the
  owner-run principal of CLI and worker processes.
  `tests/test_background_submissions.py` classifies every thread, process,
  executor, and asyncio-task site in `scanner/` and fails for an unclassified
  site or detached work started under `scanner/http` outside the helper.

## Local sessions

MU-07 binds every browser session to one user and removes both authentication
bypasses. No client address, forwarded header, persisted `auth_settings` row, or
environment variable admits a request without a session or API key. The
`auth_settings` table remains, unread, until a later migration drops it. Trusted
proxy networks still let the login limiter key its address bucket by the
forwarded client.

- `AuthenticatedSession` carries the account's current `role` and `status`,
  read with the session on every request.
- `revoke_user_sessions(db, user_id)` ends one user's sessions and never an API
  key. Sign-out revokes only the presented session.
- JF-06 removed local sign-in (`POST /api/auth/login`), the own-account change
  (`PUT /api/auth/account`), the `password_change_required` state, and its 403
  code. Every session comes from Jellyfin setup, Jellyfin password sign-in, or
  Quick Connect.
- MU-19 removed the status response's constant `configuration`, and JF-06
  removed `administrator` and `password_change_required`. A signed-in status
  carries `display_name` (profile name, else the Jellyfin name or username) and
  `access`, the session's `CapabilityPayload`; a signed-out status has
  `access: null`. Every status carries `sign_in: {jellyfin}`, which is true when
  a Jellyfin server is configured.
  Setup and Jellyfin sign-in responses return the same shape. `PUT /api/auth/security`, the `editarr auth` CLI,
  and `EDITARR_AUTHENTICATION_METHOD`/`EDITARR_AUTHENTICATION_REQUIRED` are
  removed.
- `auth_policy.session_now()` supplies the time for session issue, expiry,
  and refresh in the middleware and auth endpoints. Tests replace it to fix
  idle and absolute boundaries; there is no runtime clock override.

### Browser state ownership

MU-19 parses status into `Session`, with `setup_required`, `signed_out`, and
`signed_in` states. JF-06 removed the `password_change_required` state. The
signed-in state uses `sessionKey(session)`, the principal's
`<user_id>:<permission_revision>`. The protected App remounts when that key
changes.
Sign-out, disablement, and a revoked session unmount their private state.
The App owns catalog caches, queries, previews, review drafts, settings forms,
API-key dialogs, and backup credentials.
MU-30 completed bulk-access receipts have a narrower exception. The in-memory
`auth/operation-receipts` provider sits outside the revision-keyed App but inside
the signed-in identity boundary. It retains target IDs, captured display names,
outcomes, and refusal codes across same-user permission refreshes. Sign-out
and identity replacement unmount it. It does not
persist receipts or retain drafts, credentials, queries, or previews.
Pending bulk-mode work checks the gate's `isAuthenticationCurrent` lifetime
before each GET and again before a PUT after an awaited GET. Unmount aborts
pending reads. A replacement identity or permission revision stops queued work.
An already-started PUT may finish. An outcome observed only after abandonment
is recorded as unknown inside the initiating identity's receipt boundary.
Previously observed receipts remain intact; unstarted targets get no receipt.
The gate restores the neutral document title whenever the session kind or key
changes, at the same boundary that invalidates callbacks and binds user storage.
`review/review-preview` owns each allocated object URL before React commits it
and revokes it on replacement, invalidation, unmount, or a late response.

`AuthenticationGate` updates the in-memory CSRF token only after accepting a
status. A response discarded as stale cannot change the next request's token.
Callbacks from a previous gate lifetime cannot accept a later response. Setup
and sign-in forms unmount when the gate leaves their state.

`shared/user-storage` is the only private persistence boundary. It namespaces
values by user ID and removes user values and legacy catalog keys whenever
the identity or revision changes, including sign-out. Credentials remain in
memory, never localStorage or sessionStorage.

Jellyfin 429 `login_throttled` responses carry the remaining `Retry-After`
seconds and no-store policy. `ApiError.retryAfterSeconds` exposes the integer
header, or null when absent or invalid. `denial_response` renders the no-store
error envelope and optional retry header for sign-in and access denials. Sign-in
and fresh-password forms show the remaining wait and disable submission until the countdown ends.

## Personal API keys and fresh authentication

MU-13 gives each account one key that follows its current permissions.

- Storage. `api_keys.token_digest` (SHA-256 of the 64-hex key) authenticates.
  `secret_envelope` is `v1.<wrap key id>.<nonce>.<ciphertext>`, AES-256-GCM over
  the 32 raw key bytes with associated data bound to the digest, under
  `secrets/api-key-wrap.key` (`api_key_crypto`, same private-file contract as
  the CSRF key, no environment override). Revision `0015_sealed_api_keys` adds
  the column and the check `ck_api_keys_one_secret` (plaintext or wrapped copy,
  never both). `api_keys.seal_plaintext_api_keys` runs at startup and after
  restore activation (`seal_database_api_keys`): it wraps each matching legacy
  value, drops one that does not match its digest, then runs `VACUUM` and a
  truncating WAL checkpoint. It never binds plaintext as a SQL parameter.
  `<database>.api-key-cleanup`, created before the sealed rows commit, keeps
  that file cleanup pending; each later seal retries it and removes the marker
  only after a checkpoint with busy 0 and every frame checkpointed. The
  pre-upgrade migration snapshot still holds the
  old plaintext, like every pre-upgrade copy. `stored_api_key` returns
  `StoredKey(created_at, value)`; `value` is None for a digest-only key or one
  wrapped under a wrap key this installation no longer has.
- Backups carry `secrets/api-key-wrap.key` as secret type
  `local-api-key-wrap-key`, and every restore brings it back with `api_keys`.
- Threat model. The wrap key sits beside the database in the data directory. A
  leaked database file alone yields only digests and envelopes. A leaked data
  directory, backup archive, or pre-upgrade migration snapshot reveals every key.
- Routes (`scanner/http/api_key_api.py`). Own: `GET /api/auth/api-key`
  (`profile.view.own`; `{configured, created_at, revealable}`),
  `POST /api/auth/api-key/reveal`, `POST /api/auth/api-key` (create or rotate),
  `DELETE /api/auth/api-key`. Other accounts: `POST /api/users/{user_id}/api-key/reveal`,
  `POST /api/users/{user_id}/api-key`, `DELETE /api/users/{user_id}/api-key`,
  each deciding `ActionPair(own, other)` against the resolved account. Reveal
  and issue take `{reauthentication: {password}}`, where `password` is the
  caller's Jellyfin password, and return `{api_key, created_at}`.
- Fresh guard. `scanner/http/reauthentication.ReauthenticationGuard.authorize(db,
  access, action, target, proof, *, address)` returns the capability or raises
  `AccessDenied`. It decides without a proof first, so an account without the
  capability or an API key is refused before any password check. It then throttles wrong passwords per account and per
  client address on the sign-in window and limits, and verifies the password
  through `access.fresh.verify_reauthentication`. A throttle carries the
  remaining `Retry-After` seconds through every access-denial renderer.
  MU-15, MU-16, MU-18, and
  MU-40 call it for permission changes, linking, ownership transfer, and
  restore. Since JF-06, every account proves its Jellyfin password through
  MU-16's `verify_linked_password`. An account without a Jellyfin link cannot reauthenticate.
- Interactive-only routes. `access.route_match.interactive_only(app, scope)` is
  true when every capability of the matched route needs fresh interactive
  authentication. The middleware answers an API key there with
  `interactive_session_required` before the handler runs. Today that is the
  four reveal and issue routes.
- The middleware records only `AuthenticatedCredential`; the
  `api_key_authenticated` and `api_key_user_id` request flags are gone.
- A sign-in whose verified session write exhausts SQLite's busy wait raises
  `AuthenticationBusyError` and answers 503 `database_busy` with
  `Retry-After: 1`, without a failed-attempt charge or audit row. Key issue and
  revoke answer the same way and change nothing (`persistence.busy.is_busy`).

## Settings and operations authorization

MU-17 decides every settings and operations route through the evaluator.

- `scanner/http/operations_access.py`. A handler depends on
  `operation(capability, audited, paths=, fresh=)`, which loads the live
  principal, decides the capability against the instance before the body is
  validated, and stores a denied event when `audited` names an action.
  The returned `Operator` has
  `may(capability)` for response shaping, `sees_paths()` for server paths,
  `require_fresh` for the password, `stage` for a database change's event,
  and `record` for a change kept in files or memory.
  `scanner/api.py` installs `install_access_errors`, so production refusals use the
  `{"code", "detail"}` envelope.
- Server paths. `Operator.sees_paths()` is the live `media.paths.view` grant;
  settings paths belong to no media item. Without it, log tails and path
  mapping list, resolve, create, update, and reorder answer 403
  `capability_denied` (audited for mapping changes), and backup schedule
  responses carry `config.destination` and `config.passphrase_file` as null.
- Roles follow `catalog_operations` defaults. Owner: connection test, edit, and
  disconnect; path mapping create, update, delete, and reorder; deployment view
  and edit; backup download; backup job deletion. Admin: connection status, path mapping view and resolve, model
  inventory, cancel, and delete, log view and settings, restart, backup
  creation, schedule, and status. Everyone: About (`GET /api/system/version`)
  and `POST /api/models/prefetch`.
- `POST /api/backups/jobs` needs `backups.create` and starts a full export
  (`action: export`; `kind` may only be `full`). Restore exists only during
  setup; see "Restore into an empty installation (MU-40)". Setup-restore routes
  answer 409 `setup_already_complete` once an owner exists.
- Scheduled backup destinations may not lie under a served web root: the
  packaged frontend directory or the active `frontend_assets.root`
  (`production_frontend.served_roots`).
- Diagnostic redaction also covers quoted keys and quoted values, as in
  structured JSON log records.
- `ModelActionResult.status` is the inventory only when the caller may
  `models.inventory.view`, otherwise null. A user's download response is
  `{"status": null, "accepted": ..., "removed": false}`. Per-backend
  download state stays visible to scanning users through `GET /api/capabilities`.
- Connection reads keep `api_key_configured` and never return a key. No
  settings response carries the removed authentication bypass, and MU-19
  removed the status response's constant `configuration`.
- Restart's `reconnect_url` is the next start's base URL, which an admin may
  see in this response although deployment settings are owner-only.

## Catalog authorization

MU-20 decides every catalog read through MU-11's `decide_media` with MU-16's
evidence.

- `catalog_viewer(capability)` loads the live principal and refuses a caller
  without the capability (403 `capability_denied`) before any read. The
  `CatalogViewer` opens `evidence_cache.evidence` at most once per request and
  closes it with the request. `visible(records, target_of)` decides every
  record in one `decide_media` call and keeps order; `require(target)` refuses
  a direct request with 404 `not_found` (the `{"code", "detail"}` envelope).
  Confirmed provider absence uses the same status, body, and headers as a
  denied direct ID. `JellyfinNotFound` and `ArrNotFound` distinguish absence
  from transport, credential, and malformed-response errors, which remain 502.
  Jellyfin answering 400 or 404 to a library listing (`ParentId`) or an item
  read (`Ids`) is `JellyfinNotFound`, so every caller's read of an unknown
  library or item ID, the owner's included, is 404 `not_found` (wave 16 fix).
  The owner's records pass without a decision or extra decision evidence.
  Adapter normalization still costs the owner one `/Library/VirtualFolders`
  read when Jellyfin's item omits `LibraryId` (see Jellyfin targets).
- Capabilities. `media.browse` for `GET /api/libraries`, library items, item,
  children, seasons, and both image routes; `scans.findings.read` for
  `POST /api/filter-candidates`; `media.identity.edit` for
  `POST /api/filter-candidates/associations`; `scans.start` for both
  audio-track routes.
- Order. Records are decided before totals, rating and resolution facets,
  export decoration, sorting, and paging, so a hidden item changes no count or
  page boundary. Library listings first need `library_visible`: a local
  account's scope holds the library, or a linked account's last verified
  `MediaPolicy` opens it; otherwise 404. Children and seasons need their
  parent first. Artwork is fetched or read from the Arr artwork disk cache only
  after the item's decision.
- Jellyfin targets. A linked account's Jellyfin item is keyed by its linked
  server and decided by Jellyfin's per-item read. A local account's item gets
  its library from the listing that returned it, from its authorized parent,
  or else from its server path inside exactly one library's reported location
  (`integrations.jellyfin_paths.library_of`, Jellyfin's own path namespace); no
  single library denies it. Containment compares whole folder names and
  splits by path form. A `/`-rooted path splits only on `/`, because `\` is an
  ordinary name character on a POSIX server. A drive (`C:\`, `C:/`) or UNC
  (`\\server\share`, `//server/share`) path splits on both `\` and `/`, and its
  drive or `//server/share` stays a boundary. A path or location with a `.` or
  `..` segment is not collapsed: it has no library and is denied, so
  `/media/a/../b/film.mkv` is denied to every library-scoped account, while
  `/media/a/x\..\..\b/film.mkv` stays in the library of `/media/a`. A relative
  or drive-relative path, or a mixed `/\` or `\/` prefix, also has no library.
  Names compare case-sensitively, and the path is never resolved against
  Editarr's filesystem. The owner is not library-scoped and is unaffected.
  Host path resolution (`resolve_local_path`, for Jellyfin paths) splits a
  server path by the same forms through the shared
  `scanner.server_paths.parse_server_path`, so the scan of
  `/media/a/x\..\..\b/film.mkv` reads that literal folder under `/media/a`.
  Path mappings store and match the `/` spelling of drive and UNC paths and
  keep `\` inside POSIX names. A remote path without one of the three forms,
  such as a relative path, a `\`-rooted or `///` path, or a stream URL, is
  refused. A name the host would split (`\` on a Windows host) is refused only
  when it remains after the matched mapping prefix is replaced, or anywhere in
  the path when no mapping matches, so `/remote/a\b` mapped to `C:/Media/A`
  resolves `/remote/a\b/film.mkv` to `C:\Media\A\film.mkv`. Every refusal
  raises `UnresolvablePath`, a `ValueError`; the split-name refusal is its
  subclass `UnrepresentablePath`. Any unresolvable server path leaves only its
  own file unresolved: its catalog target has no source or path and is denied
  to every scoped account as `unresolved`, while the owner still lists it; the
  Jellyfin evidence index, including scan evidence, keeps the item's provider
  identities but no spelling for that file; a library folder so named counts
  as missing, which only stops files being proven outside every library; audit
  history leaves the media undecided, so readers other than the owner see its
  events as unresolved media, and the owner still reads them; audio requests
  answer 409, review targets `media_unavailable`, and a community import
  `invalid_target`. Path-mapping create, update, and test requests still answer
  400 with the input error. It does not deny dot segments; the host resolves
  them.
  Owner and local keys use `connection_key(server_url)`.
  Jellyfin 12 omits `LibraryId` from `/Items`, so `JellyfinClient.item` places
  such an item by the same rule, reading `/Library/VirtualFolders`. The library
  that scan start, imports, credits, and community lookups store in
  `media_items.library_id` and decide through `ScanMedia` is therefore the one
  that authorizes a local account (wave 16 fix). This holds for media rows
  written or refreshed by scan start, import, or review-media writes after the
  fix. Older rows stored with an empty library are not repaired automatically;
  a library-scoped local account cannot see their jobs until such a write
  refreshes the row. A catalog read alone does not refresh it.
- Caches. `LIBRARY_ITEMS_CACHE`, `JELLYFIN_ITEM_CACHE`, the library list, the
  Arr listings (`catalog_targets.Listings`, five minutes), and the artwork disk
  cache hold only unfiltered server-key reads; no filtered result is cached.
  Browser artwork URLs and ETags are partitioned by user and permission revision
  through `catalog_artwork`. Both image routes return `Cache-Control: private,
  no-store` and `Vary: Cookie, Authorization`, including Arr disk-cache hits.
  The URL's `principal` query value is only a cache key, never access proof.
  Arr item and detail DTOs expose the optional scoped `image_url` for the UI.
  Jellyfin item decisions are reused for five minutes
  (`catalog_access.RecentItems`). Fast reuse and MU-16 outage fallback must both
  be no older than the user's last verified snapshot. MU-16 still bounds cached
  fallback to 24 hours and orders publication by read generation. Fresh provider
  answers may finish after the request's clock sample. A sync, local scope
  change, or permission change applies to the next request of an existing session.
- Paths. Without `media.paths.view`, records lose `path`, every version's
  `path`, any media stream `Path`, and a library's `locations`; a path-mapping
  error reads `Unsupported path`. Candidate scans also lose stored free-form
  `error_message` text, which can contain a server path. Opaque IDs (item,
  version `id`) remain.
- Opaque selection. `ScanMatchSearch` needs only `source` and `item_id`;
  `media_source_id` or `source_path` selects one of the versions the server
  reports, and an unreported one is 404. Name, type, duration, scene, release,
  format, and provider ID come from the server record; the request's values
  are ignored. The media item row is written from the server record. Each
  candidate scan's media is rebuilt from its source server
  (`catalog_files.stored_targets`) and kept only when reachable.
  `GET /api/media/audio-tracks` takes `item_id` and optional
  `media_source_id`; `AudioSelectionRequest` takes the same fields and rejects
  any other.
- Associations. Both the media item and every scan are rebuilt from their
  source records and must be reachable, or the request is 404 and nothing is
  written. Associations are never read by a media decision. Candidate discovery
  and association handlers live in `scanner/http/catalog_candidates_api.py`;
  `catalog_source_api` keeps its existing exports and includes that router.
- Cost. A linked account's first view of a library reads Jellyfin once per
  item (MU-08's per-item route); later views within five minutes reuse the
  decisions.

## Scan jobs, history, and workers

MU-21 authorizes the scan routes in `scanner/http/scan_jobs_api.py`,
`scan_create_api.py`, and `credits_api.py` (start, status, model consent)
through `scanner/http/scan_access.py`. Credits acceptance and clips stay owner-only for MU-22.

- A scan row is the durable job. Its owner is `scans.initiator_id`, the
  account that submitted it, recorded with that request's permission revision.
  Every route passes the acting principal to `create_scan(initiator=...)`;
  `activity_owner` remains only for imports and fixtures without a principal.
- A scan's media is rebuilt from `ScanMedia`
  (`persistence/repository/scan_jobs.py`): the `media_items` server and item
  keys and library, and the scan's own `resolved_local_path`.
  `security/scan_jobs/media.scan_target` turns it into an MU-11 `MediaTarget`
  for its Jellyfin item. Jellyfin keys use the linked server's ID for a linked
  account and `UNKNOWN_SERVER` otherwise, since owner and local decisions never
  compare server keys.
  `decide_scans` decides a request's scans with one `MediaEvidence`.
- Visibility. A caller sees a scan when `scan_jobs.view.own|other` allows its
  `ResourceRef(initiator, media)`, or when it is `complete` and
  `scans.findings.read` allows its media (shared evidence). Everything else is
  `scan_not_found` (404), or `Queued scan not found` on queue routes. Job
  routes then ask the evaluator for the own/other action, so a visible scan
  the caller may not act on answers 403 `capability_denied`.
  `GET /api/scans/{id}/findings` separately requires `scans.findings.read`
  on that media, even when the initiator can view its own job.
- Queue order. `POST /api/scans/queue/reorder` needs `scan_jobs.reorder` and
  the exact set of queue IDs the caller sees; it orders them within the slots
  they hold, and hidden jobs keep their places.
- Queue position. Every `ScanQueueItem` response (`GET /api/scans/queue`,
  scan creation) carries `sort_order` as the number of jobs ahead of it that
  the caller may view as jobs, so visible entries number 0..n-1 in global
  order. A caller who sees every job gets the global order as 0..n-1. The
  stored `scan_queue.sort_order` never leaves the server (wave 16 fix).
  Accepted limitation: `queue_id` (including the credits response's outer
  `queue_id`) is one global sequence and `Scan.scan_id` numbers count per
  media item, so their gaps may reveal how many jobs other users created.
  Hidden jobs' content, owners, titles, paths, progress, errors, and positions
  stay hidden. Identifiers are not renumbered, so cancel, resume, and reorder
  keep stable addresses.
- Submission is decided on the live source record before any file probe:
  `scans.start` on the Jellyfin item. Credits detection reuses only the caller's own active job.
- Model consent continues a waiting credits job: it needs the job's
  `scan_jobs.resume.own|other` and `models.download`.
- Responses for a caller without `media.paths.view` replace error and
  progress messages with fixed status text. This projection applies to scan
  creation, history, credits start/status, and model-consent responses.
  Model-acquisition progress on a
  queued job shows only with `models.progress.view.own` (own) or
  `scan_jobs.view.other`.
- Deletion. `DELETE /api/scans/{id}` is refused while any review, active export,
  or pending/submitted community operation references the scan.
  `ScanReferences.community_in_progress` is a caller-safe boolean shared by the
  pre-check and the 409 `scan_referenced` response. It reveals no service,
  artifact, or operation details. The refusal says "A community operation for
  this scan is still in progress". Imports alone and finished operations do not
  block: imports/targets are deleted and finished operations retain history
  with null `scan_id`. SQLite deletion checks and writes
  hold the same write lock from before the first read through commit.
  The 409 `scan_referenced` body and
  `GET /api/scans/{id}/references` list only reviews the caller may view
  (`reviews.view.own|other` on each review's own target media,
  `repository_review_operations.scan_reference_facts`); the message says
  "reviews you cannot view" instead of naming the rest.
- Worker boundaries. `security/scan_jobs/authority.decide_job` loads the
  initiator with `load_access` (a non-session credential), refuses a disabled
  account, and decides `scans.start` on the scan's media. The scheduler decides in
  `_launch_scan_worker` before reserving. The worker's `JobGate.require`
  takes no write lock. It reuses a read-only decision for at most 250 ms on
  both the wall and monotonic clocks, checking stored authority on each poll.
  A changed account, effective grant, media scope, stored Jellyfin policy,
  read generation, clock fence, or restore epoch invalidates it immediately.
  `JobGate.run` and submission's `ScanCaller.starting` use the shared
  `security/access/write_boundary.fresh_authority_write` helper, as do saved
  review writes.
  `JobGate.run(db, operation, *args, download=False, **kwargs)` materializes
  the linked catalog, folders, and item answer before taking
  `persistence.write_lock.write_lock`. Inside the lock it reloads stored
  authority and compares it with the state behind that evidence. A mismatch
  releases the lock and retries with new evidence, at most three times, then
  refuses. A matching state gets a fresh media and capability decision from
  the captured answer, with no network-capable item reader, and runs the write
  in that same transaction. Worker findings, status including
  empty completion, metadata, audio provenance, progress, checkpoints, stages,
  and credits results use this boundary. A previously loaded ORM row is expired
  under the lock. Stored Jellyfin restrictions apply without waiting for a
  permission revision or a time interval. Filter-enforcement policy does not
  affect scan execution.
- Refusal. A refused job ends `failed` with a `REFUSAL_MESSAGES` text
  (`account_unavailable`, `capability_denied`, `media_unavailable`), its queue
  row removed and worker fields cleared, so it never blocks the queue. It is
  not retried; starting a new scan, authorized afresh, is the retry. There
  are no quotas.
- Model downloads. `modeling.model_acquisition_authority.scoped_job_acquisition`
  binds the durable scan gate to one acquisition context. Dedicated workers
  pass `EDITARR_WORKER_SCAN_ID` to pipeline children, which reload the same
  initiator from their database. The transfer checks live authority at start,
  before each fetch, and in cancellation and progress callbacks. Ready-artifact
  publication runs through `JobGate.run` under the database write lock. An
  existing artifact is renamed aside; deletion of that old directory
  happens after the publishing transaction commits. Pipeline children retain
  their gate between callbacks but reload its stored authority on each check.
  Credits progress writes coalesce to one per 250 ms plus the final update.
  An installed model needs scan authority but not `models.download`; acquiring a
  missing model additionally needs that capability or raises
  `download_not_permitted`. Credits acquisition checks before the thread's work
  and guards progress and requeue through the same boundary. Revocation ends
  the job `failed` and dequeues it; user cancellation remains `cancelled`, and
  ordinary acquisition failure remains terminal with a path-safe message.

## Catalog and scan UI access

### Deleted-scan review repair

Migration 0020 preserves removed review rows in `review_scan_repairs`; its presence,
not a runtime heuristic, defines repair state. Lists expose nullable `repair` on
`SavedReviewSummary`. Visible flagged reviews refuse detail, mutations, previews,
exports, credits accept, filter apply, and community sharing with no-store
`409 review_needs_repair` and the repair summary. Visibility is checked first;
repair routes also return 404 for hidden IDs. Delete retains its existing rules.

`POST /api/reviews/{review_id}/repair/recover` requires the review's own/other save
authority on its current media. `POST .../repair/community` additionally requires
`community.download` on that media, connected identity, enabled downloads, verified
artifact/receipt keys, and unchanged target-file evidence. Authority is rechecked
inside the write boundary. No unavailable identity or download gate is bypassed.
Both return `SavedReviewSummary` and are idempotent after success. Community
failure is `409 community_scan_unavailable` with `repair`, `reason`, and `transient`;
only definitive unavailability is persisted. The community command's compound permission is
enforced by the shared review decision boundary.

### Catalog Projection

MU-26 consumes MU-19's principal and permission-revision lifetime, MU-20's safe
catalog records, and MU-21's scan decisions. No migration or route is added.

- `Scan.is_own` is a nullable, caller-relative response projection. It reveals
  no initiator ID. `null` means unknown ownership and hides mutations.
  `ScanCaller.present` computes it for creation, queue, and history responses.
- `ScanReferences.has_hidden_references` reports a deletion blocker without
  hidden review IDs or names. The frontend disables deletion when the flag,
  `community_in_progress`, visible reviews, or active exports block it. Server
  deletion still checks all references, including those the caller cannot view.
- Audio inventory and confirmation accept opaque `item_id` and optional
  `media_source_id`. The server authorizes the current Jellyfin record
  before resolving and probing its file. Catalog clients do not resolve paths.
- `GET /api/capabilities` requires `scans.start` through `catalog_viewer`, whose
  target is media rather than `INSTANCE`. This is scan-option availability,
  not global model management. `GET /api/mcf-registry` requires `request_access`
  as reference data.
- Catalog history filters MU-21's caller-safe history and queue by item and
  physical version. Settings reads require `connections.view`; path rendering
  requires `media.paths.view`. Account or permission revision changes discard
  protected catalog caches, pending selections, and late asynchronous results.
- Without `media.paths.view`, `ScanCaller.present` derives error and progress
  messages from scan status before an allowlist of active stages. Queued,
  interrupted, consent-waiting, cancelled, and failed jobs do not report active
  transcription merely because their last stage was transcription. It never reuses arbitrary
  worker diagnostics, even when they name model-cache, temporary, Windows, or
  relative paths. Unknown progress stages become null. Existing progress
  visibility gates still apply. Callers with path permission retain the original
  detail, and response projection does not alter stored diagnostics or protected
  exception logs. Item-history handlers use the same projection.
- An item-scoped 403/404 media denial clears the owning catalog workspace and
  browse caches even when principal capabilities and revision are unchanged.
  API requests carry an inferred `MediaTarget { source, itemId }` into denial
  delivery. Watchers belong to the request's original workspace lifetime; late
  responses cannot invalidate a reopened workspace. Missing exports and unrelated
  targets do not count as media loss. Explicit `media_unavailable` and coded
  identity/audio-track GET refusals establish denial. Other coded item-scoped
  403/404 operation failures require a fresh uncached item GET; only its coded
  refusal revokes the workspace. Unclassified responses, network errors, and
  server failures do not establish denial. Concurrent failures share only a
  pending item read within the original target workspace. A settled read is
  discarded, and a replacement workspace never inherits that read.
  Review-target creation authorizes the destination before reporting membership
  errors, including when a membership scan is absent. A denied destination gives
  the same response regardless of membership contents. After destination
  authorization, absent, hidden, and unusable membership scans give byte-identical
  404 `scan_not_found` responses, including headers, at admission and the
  write-fence recheck. Destination-media refusals remain unchanged.
  This is an opt-in at the review-target creation boundary; other
  `ReviewAccess.create` callers retain their existing denial behavior. Such
  failures preserve the dirty draft and navigation discard prompt.
  Confirmed media loss discards even an unsaved draft, with the explicit notice
  "Your unsaved review draft could not be kept because this media is no longer
  available." Retry remounts through a fresh authorized
  item read, without restoring old results, dirty state, selections, or dialogs.
- `scanActionAllowed` chooses own or other action capabilities from known
  ownership. Global reorder separately requires `scan_jobs.reorder`. Own model
  acquisition feedback stays within the authorized job projection.

JF-02 removed the Other Files identity routes that MU-42 placed behind the
matrix row "View/edit manual media identity/source associations", and with them
`media.identity.view`. `media.identity.edit` still gates filter-candidate
associations.

## Account storage

Revision `0010_user_accounts` replaces the singleton tables.

| Table | Holds |
| --- | --- |
| `users` | Stable AUTOINCREMENT ID, role, status, disable cause, optional canonical username, permanent `local-` export ID, permission revision |
| `user_provider_identities` | One Jellyfin identity per user, unique by server ID and canonical user ID, link state, last verification |
| `role_capability_defaults` | Changed admin or user defaults only; a missing row is the catalog default |
| `user_capability_overrides` | `allow` or `deny`; inherit is no row |
| `auth_sessions` | Existing session columns with `user_id` |
| `api_keys` | One key per user: digest plus a wrapped copy (`secret_envelope`, revision 0015); `token_value` holds legacy plaintext only until the application seals it |

A partial unique index allows one owner. Revision `0030_remove_passwords` drops
`password_hash`, `local_credential`, and `password_changed_at`, and
`ck_users_owner_state` now keeps the owner active unless its disable cause is
`unlinked`. Revision `0010_user_accounts` copied the administrator to owner ID 1
with the same hash, kept the key's digest, time, and value, and revoked every
session. A key from an installation without an administrator keeps a null
holder; owner setup assigns it. A role-default change must bump
`permission_revision` for every user of that role in the same transaction.
SQLite DDL here is not transactional under the driver, so a failed or
interrupted upgrade relies on the verified pre-upgrade snapshot and the journal,
which blocks admission until recovery.

## Users and profiles

MU-15 adds the account management API. Handlers live in `scanner/http/`
(`users_api`, `users_admin_api`, `permissions_api`, and `media_access_api`,
composed by `users_routes`); storage in
`scanner/security/account_repository.py` and `account_permissions.py`;
validation in `account_profiles.py` and `account_avatars.py`.

- Storage. Revision `0017_user_profiles` adds three empty tables.
  `user_profiles` holds optional `display_name` (1 to 100 characters), `email`
  (3 to 254, never mailed), `timezone` (IANA name), `language`, and
  `last_login_at`, which `issue_session` sets on every sign-in. `user_avatars`
  holds one PNG per account (`digest` is its SHA-256, at most 512 KiB), re-encoded
  from the decoded pixels without EXIF, ICC profiles, or other metadata.
  `account_settings` is one row. Revision `0030_remove_passwords` drops its
  `registration_mode` column. All three are carried by every backup.
  Avatars live in the database rather than
  in data-directory files, so they are private, atomic with the profile, and
  carried by every backup and restore without a new archive member.
- Target resolution. `/api/users/{user_id}` names any account. An own target
  decides the own capability; another account decides `.other` with
  target-account protection, so admins manage ordinary users and only the owner
  manages admins. Actions that only manage others (status, sessions, role,
  overrides, media access) answer 409 `own_account` for the
  caller's own ID. Reading another account needs `accounts.list`.
- Hidden account IDs. Every route that takes `{user_id}` (Users, API keys, and
  `/api/settings/filters/users/{user_id}`) resolves it through
  `access.authorization.resolve_account(db, access, user_id, *actions)` before
  anything else looks at the account. Another ID resolves only for a caller who
  holds `accounts.list` or the other-account capability of one of the route's
  `actions`. Anyone else gets `HiddenAccount` (404 `not_found`) for an existing,
  protected, or disabled account and for an ID that names nothing, so only
  directory readers and delegated managers learn which IDs exist. Profile and
  avatar reads first require `accounts.list`, and the middleware gates the
  per-account key routes, so those answer 403 `capability_denied` for every ID.
  A refused mutation of an existing hidden account still stores its `not_found`
  event.
  `tests/test_account_target_resolution_http.py` probes every such route found
  in the production app.
- One operation per target. `account_operations.Operation` names a capability,
  its audit action, and the change. `run` decides it for one target through
  `Proofs`, which verifies the request's password at most once
  (`ReauthenticationGuard.prove`), stores a refusal, or commits the change with
  its event. Single routes and `POST /api/users/bulk` share it, so a bulk target
  is refused exactly as it would be alone. A wrong or throttled password stops a
  bulk request; earlier targets stay applied. Slow work runs in
  `Operation.prepare` after the decision. The change
  then holds the SQLite write lock (`scanner.persistence.write_lock`) and
  re-reads the target; if its role moved since the decision, `run` decides again
  and answers that decision, such as 403 `protected_account` for an account
  promoted meanwhile.
- Linked accounts. A user with a `user_provider_identities` row takes its
  username, display name, and avatar from Jellyfin (`read_only_fields`), refreshed
  at every sign-in, sync, link, and import: changes through `PATCH /api/users/{user_id}` or the
  avatar routes answer 409 `linked_profile_field`. The username is Jellyfin's `Name`. While another account
  holds that name (compared case-insensitively), or the name is not a valid
  username, the account keeps its current username; the read that first finds
  this stores `account.jellyfin.state` with `username: unavailable`, later reads
  only log it, and each later sign-in or sync tries again. Revision
  `0030_remove_passwords` clears every unlinked account's username, so a name
  only an unlinked account held comes with the next sign-in or sync. An account created
  while its name was held has no username until then; Users and the profile show
  `None (Jellyfin name: <name>)` rather than the holder's username. A rename after the
  first snapshot stores `username: refreshed`. Renames from Jellyfin end no session.
  Email, timezone, and language stay editable. An account without a Jellyfin
  link has no username and stays disabled until it is linked. No route assigns
  a local username.
- Roles. A Jellyfin administrator starts as `admin` and everyone else as `user`
  when the account is created, at first sign-in or import. Afterwards only the
  owner changes the role; no sign-in, sync, or link changes it.
  Details also return `disable_cause`: `local`, `upstream_disabled`,
  `upstream_deleted`, or `unlinked`, or null for an active account.
- Disabled without a link. Revision `0030_remove_passwords` disables every
  active account that has no `user_provider_identities` row, with cause
  `unlinked`, bumps its permission revision, and keeps all of its rows. It
  then clears the username of every account without a link, the owner
  included, and copies it to the profile display name when that is empty.
  `PUT /api/users/{user_id}/status` and bulk `enable` refuse such an account
  with 409 `account_link_required`, so an active account always has a link.
  A completed link (MU-35) of an `unlinked` account makes it active, clears the
  cause, and bumps its permission revision. A `local` disable survives linking.
- Language. `account_profiles.SUPPORTED_LANGUAGES` is `("en",)` until a
  translation ships; detail reads return it as `available_languages`.
- Removed routes. JF-06 removed `POST /api/auth/register`, `POST /api/users`,
  `POST /api/users/{user_id}/password-reset`, and `GET` and
  `PUT /api/settings/registration`. Each answers 404. Jellyfin setup, a first
  Jellyfin sign-in, or an import creates every account.

| Route | Capability | Notes |
| --- | --- | --- |
| `GET /api/users` | `accounts.list` | `offset`, `limit` (1 to 100), `search` (username or display name); approved columns only |
| `POST /api/users/bulk` | per action | `{action, user_ids, role?, media_access?, reauthentication?}`; actions `disable`, `enable`, `revoke_sessions`, `set_role`, `set_media_access` |
| `GET /api/users/{user_id}` | `profile.view.own` / `accounts.list` | adds email, timezone, language, `read_only_fields`, `link_cleanup` |
| `PATCH /api/users/{user_id}` | `profile.edit.own` / `accounts.profile.edit.other` | display name, email, timezone, language; `username` answers 422; a linked account's display name answers 409 `linked_profile_field` |
| `GET PUT DELETE /api/users/{user_id}/avatar` | view or edit pair | PUT takes the raw image (PNG, JPEG, WebP, GIF, at most 5 MiB and 4096 px a side); 413 `avatar_too_large`, 422 `avatar_not_image` or `avatar_dimensions` |
| `PUT /api/users/{user_id}/status` | `accounts.disable.other` / `accounts.enable.other` | disabling ends sessions; enabling an `unlinked` account answers 409 `account_link_required` |
| `DELETE /api/users/{user_id}/sessions` | `accounts.sessions.revoke.other` | |
| `PUT /api/users/{user_id}/role` | `roles.assign`, fresh | `admin` or `user` |
| `GET PATCH /api/users/{user_id}/permissions` | `permissions.overrides.edit`, fresh | `{overrides: {capability: allow|deny|inherit}}`; owner powers 422 `owner_only_capability`; `archive.view` 422 `role_fixed_capability` |
| `GET /api/roles/defaults`, `PATCH /api/roles/{role}/defaults` | `roles.defaults.edit`, fresh | `{defaults: {capability: true|false|null}}`; null restores the catalog default; owner powers 422 `owner_only_capability`, `archive.view` 422 `role_fixed_capability` |
| `GET PUT /api/media-access/default` | `media_access.defaults.edit`, fresh | `{all_sources, sources}` |
| `GET PUT /api/users/{user_id}/media-access` | `media_access.user.edit`, fresh | GET returns `{linked, scope}`; PUT takes `{inherit}` or `{all_sources, sources}`; 409 `linked_account` |

Routes whose every capability needs fresh authentication are interactive-only,
so the middleware refuses API keys there, GETs included. Role defaults and
overrides bump `permission_revision` for each affected account in the same
transaction; so do status, role, and media changes.

Role-default responses include `capability`, `granted`, `catalog_default`, and
`explicit` for every delegable capability in each role. `explicit` reports whether
a stored role-default row exists, even when its value equals the catalog default.
Sending null removes that row and returns `explicit: false`. Saving the same
explicit value preserves it without advancing permission revisions.

## Ownership transfer and owner recovery

MU-18 moves the owner role in one place and keeps every other path off it.

- `POST /api/users/{user_id}/ownership` (`scanner/http/ownership_api.py`,
  capability `ownership.transfer`, owner-only and fresh, so interactive-only)
  takes `{reauthentication: {password}}` and answers
  `{owner_id, former_owner_id, former_owner_role: "admin"}`. The successor must
  be active (409 `successor_disabled`). It must also have a Jellyfin link that
  is not `server_replaced` (409 `successor_credentials_required`). The owner
  confirms every fresh action with its Jellyfin password, which Editarr checks
  against the configured server under the linked user ID. The caller's own ID
  answers 409 `own_account`.
- `account_ownership.transfer_ownership(db, owner_id, successor_id, now=)` runs
  inside `account_operations.run`'s write lock. It checks the successor, demotes
  the owner row (`WHERE role = 'owner'`) to `admin`, then promotes the successor,
  and bumps both permission revisions. Both changes and the `account.ownership.transfer` event (actor the former
  owner, target the successor, `successor_role` admin or user) commit together.
  Refusals store the access code or the successor code as `reason`.
- The successor never inherits enforced filter policy as owner. In that same
  transaction, transfer clears its rule and severity enforcement, preserves its
  personal filter values, and advances its `policy_revision` once if enforcement
  changed. Both permission revisions still advance once. A policy change stages
  `filters.policy.change` with the former owner as session actor, the successor
  as target, `cause: ownership_transfer`, the new `policy_revision`, and zero
  `rules` and `severities`. No enforcement means no policy revision or event.
- `account_operations.locked` takes `BEGIN IMMEDIATE` before
  `access.write_authorization.revalidate` reloads the caller's principal and
  resolves the target's current protected role. The lock stays held through the
  protected read or write and its audit commit. `run`, profile and avatar writes,
  role-default edits, and key issue/reveal/revoke share this boundary. Filter
  preference saves, personal and assigned filter modes, and per-user policy
  enforcement also share it.
  A stale request on these routes cannot continue with its previous authority.
  Ordinary operations answer 403 `capability_denied`, or the current evaluator's
  refusal such as `protected_account`, without applying the operation. Refusals
  can still write their denied audit event. Password verification runs before
  the lock and its proof stays bound to the original request.
- A logout that commits after a sensitive request's password check does not
  cancel that in-flight request. Its proof is request-bound, not a credential
  cancellation lease. Subsequent requests still need a live session and a
  password Jellyfin accepts. This ruling supersedes the round-1 password and
  logout cancellation probes; they are observations, not required refusals.
- Ownership transfer retains 409 `permission_revision_stale` when the caller's
  authority changed. Of two concurrent transfers, one commits and the other gets
  this code.
- The former owner keeps its overrides and data and is decided as any admin
  (role defaults plus delegable overrides). Its sessions and key stay.
- Owner protection. Managing the owner is the owner's own, nondelegable power:
  admins get `protected_account` or `capability_denied`, and the owner gets 409
  `own_account` for status, role, sessions, overrides, and media access on
  itself, alone or in bulk. Disabling through `set_disable_cause` refuses an
  owner row with `protected_account`, and `assign_role` never matches one.
  Only revision `0030_remove_passwords` disables an owner, with cause
  `unlinked`, when the owner has no Jellyfin link. Nobody can sign in as that
  owner. Jellyfin sync never changes the owner (`states.transition`). `ck_users_owner_state` and the
  single-owner index remain the storage guards. Account deletion (MU-38) and
  linking (MU-35) must keep these refusals.
- If historical data already has enforcement on the owner, an instance-default
  edit that would change it returns 409 `owner_filter_policy` and rolls back.
  It never exposes an unhandled `TargetMismatch`. New ownership transfers clear
  enforcement before committing, so they do not create this state.
- Offline recovery. JF-06 removed `editarr admin create`, `admin
  reset-password`, and `admin rename`, and with them the writer of
  `account.credentials.recover`. Stored events of that action still read.
  Editarr has no lockout recovery. An owner whom 0030 disabled for having no
  Jellyfin link reinstalls, or recovers the pre-upgrade snapshot with
  `editarr migration recover-export` and links it with the previous release;
  a restore accepts only backups at the current schema, which hold the same
  unlinked owner. Without these commands nothing takes the
  service lock exclusively, so `runtime.service_lease` and
  `<data>/maintenance/service.lock` are gone too. `editarr admin
  write-export` (MU-38) runs beside the API; see "Account disposition (MU-38)".

## Jellyfin accounts and admission

MU-16 creates, refreshes, and restricts linked accounts in
`scanner/security/jellyfin_accounts/`. `states.transition(account, evidence,
now)` is the one pure rule; `store.observe` is the one writer of Jellyfin
evidence and applies it under the SQLite write lock with its audit event;
`admission`, `sync`, `evidence`, and `reauthentication` use it.

- Storage. Revision `0018_jellyfin_account_sync` adds `jellyfin_account_sync`
  (and `account_settings.jellyfin_admission`, which 0028 dropped), one row
  per linked account: `upstream_state` (`enabled`, `disabled`, `deleted`), the
  last verified `jellyfin_name`, `avatar_tag`, `is_administrator`, and
  `media_policy` (canonical JSON of `MediaPolicy`), `verified_at`, and the last
  attempt (`last_attempt_at`, `last_outcome`, `last_failure`, a failure only for
  `unverified` and `server_replaced`). `user_provider_identities.last_verified_at`
  moves only with a verified snapshot, and `user_provider_identities.observed_generation`
  (null until the first) records the read generation of the newest applied
  observation. `account_settings.jellyfin_read_generation` (0 until the first
  read) and `jellyfin_read_at` hold the last generation taken and its wall time,
  and `jellyfin_clock_fence` (0 until a read finds the clock stepped back, never
  above the counter) holds the generation of the read that last found it.
  All of them are carried by every backup. `jellyfin_read_epoch` (null, or
  32 lowercase hex digits; checked) never survives a restore: every restore
  activation clears it. The same revision adds
  `users.disable_cause` (`identity.DisableCause`): `local`, `upstream_disabled`,
  or `upstream_deleted`, and null while the account is active (checked). Every
  account disabled before 0018 becomes `local`; a disabled row without a cause
  reads as `local`. `account_repository.set_disable_cause(db, user_id, cause,
  now=)` is the one writer: a cause disables and ends sessions, None enables
  and restores no session. `PUT /api/users/{user_id}/status` and bulk
  enable/disable record `local`.
- Transitions. A snapshot is `verified` (or `disabled` when Jellyfin disables
  the user) and stores it. `UserDeleted` is `deleted`. `Transition.disable_cause`
  is the account's cause afterwards. Jellyfin disabling the user sets
  `upstream_disabled` and deleting it sets `upstream_deleted`; both end its
  sessions and keep its key and data. Jellyfin enabling the user again, at the
  next sync or sign-in, enables an `upstream_disabled` account: its key works
  again, its revoked sessions stay revoked. A `local` cause never changes with
  Jellyfin, and `upstream_deleted` never returns on its own. An admin enabling
  an account Jellyfin still disables is disabled again at the next sync. The
  `account.jellyfin.state` event carries `disabled` or `enabled`. Any other
  `Unverified` is `unverified`: link state `outage_grace` while the last
  success is at most 24 hours old,
  else `outage_expired`. `server_mismatch` and no configured connection are
  `server_replaced`, which the same server answering again clears. Jellyfin
  never changes a role after creation, and the owner is never disabled. Status, and link-state changes bump `permission_revision` and store
  `account.jellyfin.state` (system actor).
- Ordering. Observations are ordered by a read generation, never by the wall
  clock. `reads.begin_read(db, clock, *, cancelled=None) -> ReadStart`
  increments `account_settings.jellyfin_read_generation` in its own committed
  write transaction (creating the settings row as `managed` if absent) before
  Jellyfin is asked: sync once per account as its read starts, a sign-in before
  `authenticate`, and an import before the listing. `ReadStart` is
  `(generation: ReadGeneration, at: datetime, epoch: ReadEpoch)`;
  `ReadGeneration` is a distinct integer type and `ReadEpoch` a distinct string. The counter is in the database, so it orders reads across
  threads, processes, and restarts. `store.observe(db, user_id, evidence, read,
  avatar)` applies an answer only if its generation is greater than the
  account's `observed_generation`, then stores `read.at` as the attempt time
  and, for a snapshot, as `last_verified_at` and `verified_at`. An older or
  equal answer of any kind (snapshot, deletion, outage, server mismatch)
  returns None and changes nothing: a sync leaves that account out of its
  results, an import reports it `existing`, and a sign-in is refused 503
  `jellyfin_unavailable` to be retried, which also refuses the slower of two
  simultaneous sign-ins of one user. `sign_in(..., clock=, issue=)` stages
  the session through `issue` in the write-lock transaction that admits the
  account, so a disable either commits first and refuses the sign-in or
  commits after and revokes the new session.
- Clock steps. `begin_read` reads `clock` under the write lock and stores it as
  `jellyfin_read_at`. A clock behind the stored time means the clock stepped
  back, so verification ages measured before it are unknown: the read clears
  every `last_verified_at` (logged) and records its own generation as
  `jellyfin_clock_fence` in the same transaction, and each linked account has
  last-verified access again only after its next successful verification by a
  read numbered at or above the fence. `store.observe` never takes a
  verification from a read numbered below the fence, which was timed on the old
  clock (`states.before_clock_step`): its verified answer changes nothing, so a
  delayed sign-in is refused 503 `jellyfin_unavailable` and creates no account,
  and an import whose listing predates the fence is refused 503
  `jellyfin_unavailable` before its next new account (accounts it already
  created stay). A disable, deletion, outage, or server mismatch such a read saw
  still applies, since it only reduces access, but stores no snapshot,
  verification time, or role. Until a read
  notices the step, `load_access` treats any `now` before `jellyfin_read_at` as
  expired. A verification time after `now` is never within grace
  (`link_state.elapsed_within`), and the media evidence cache rereads a copy
  timed after `now`. Last-verified access therefore never outlasts 24 hours of
  elapsed time on one clock; a backward step fails closed. Wall times only time
  attempts, verification, and grace.
- Restores. A restore replaces the counter, fence, and accounts with the
  backup's while a read begun before it may still wait for Jellyfin; nothing
  stops that read, because `begin_read` commits before Jellyfin is asked and so
  holds no maintenance lease. Each read therefore takes
  `account_settings.jellyfin_read_epoch` with its generation; the first read
  after the epoch is null sets 32 random hex digits, and every restore
  activation (setup or CLI) clears it in `backup.activation_state._sanitize_db`. `store.observe`
  applies nothing from a read of another epoch (`reads.read_epoch`), whatever
  its generation, since its generation and account ID were numbered in the
  replaced database: a sync leaves it out of its results, a sign-in is refused
  503 `jellyfin_unavailable` and creates no account, and an import is refused
  503 `jellyfin_unavailable` before its next new account. A sync run ends at
  its first read in another epoch than its first read's, since the accounts it
  listed belong to the replaced database; the next run reads the restored ones.
  Recovering an interrupted activation returns the exact database reads were
  numbered in, so it keeps the epoch.
- Effective link state. `access.link_state.effective_link_state(stored,
  last_verified_at, now, *, last_read_at)` expires `verified` and
  `outage_grace` more than `VERIFICATION_GRACE` (24 hours, also used by
  `decide_media`) after the last success, whatever the scheduler last stored;
  exactly 24 hours is still within. A `last_verified_at` after `now`, or a
  `now` before `last_read_at`, is expired. `load_access` reads
  `jellyfin_read_at` in its one SELECT and applies the rule at
  `link_state.link_clock()` and, for a
  nonowner in `outage_expired` or `server_replaced`, keeps only `PROFILE_ONLY`:
  own profile view and edit, and own key create, rotate, reveal, and revoke.
- Admission. `admission.sign_in` refuses a username or password that is not
  UTF-8 encodable before contacting Jellyfin, proves the credentials, and finds
  the account by server and Jellyfin user ID only. It refuses a Jellyfin user in
  `jellyfin_user_blocks` (403 `jellyfin_user_blocked`, stored as an `auth.sign_in`
  denial with that reason), and with no account it refuses a
  Jellyfin-disabled user before creating one; creation and lookup share the write lock, so overlapping
  sign-ins create one identity. New accounts have no username, a `local-`
  export ID, the starting role, and the Jellyfin name as
  `display_name`; sync never writes email, timezone, or language. A
  `CredentialProof` whose logout was not confirmed (`may_remain`) is accepted:
  the credentials were proven and Jellyfin replaces that session at the next
  sign-in. `import_users` creates the selected or all listed users, skips
  Jellyfin-disabled ones, reports blocked ones as `blocked` with an
  `account.create` denial (`method: jellyfin_import`, `reason: jellyfin_user_blocked`),
  refreshes linked ones (`existing`), and reports missing IDs as `not_found`,
  never as deletions.
- Blocks. `jellyfin_user_blocks` (0029) holds one row per blocked Jellyfin server
  and user ID with Jellyfin's name for the user, `blocked_by`, and `blocked_at`.
  Only a confirmed account deletion with `jellyfin_user: block` adds one, in the
  deletion's transaction and only for a linked account, so whoever may delete the
  account (`accounts.delete.other`) may block its Jellyfin user. Password sign-in,
  Quick Connect, import, and linking check it under the write lock that would
  create or link the account, so a blocked identity never has an account. Linking
  previews it as blocker `jellyfin_user_blocked` (the proof step then also stores a
  `link_blocked` denial) and checks it again when it switches the identity,
  refusing a block added since the preview with 409 `jellyfin_user_blocked`. `POST /api/jellyfin/blocks/unblock` removes the
  row; the user's next sign-in creates a fresh account with its starting role.
- Sync. `sync.SyncRegistry.run` verifies every linked account with one adapter
  and never overlaps (`SyncInProgress`). `runtime.jellyfin_sync_scheduler`
  starts with the scan queue, runs at once and then when five minutes have
  passed since the last scheduled start (`tick` reads an injected clock).
  `stop(timeout=DRAIN_SECONDS)` (35 seconds: one 30-second Jellyfin exchange
  and the write) cancels the run, which numbers and reads no further account and
  writes nothing once cancelled (checked under the write lock), and returns
  whether the thread ended. One account's verification can make more than one
  exchange, so the run can outlast that wait; it still writes nothing.
  Application shutdown keeps a scheduler whose thread has not
  ended and logs a warning. A sign-in syncs its own account. The production adapter
  times verifications with `link_state.link_clock()`. Avatars are fetched only
  when Jellyfin's tag changes; one Editarr cannot re-encode shows none.
- Fresh authentication. `access.fresh.verify_reauthentication` proves every
  account through `reauthentication.verify_linked_password`: Jellyfin must
  accept the password for the stored Jellyfin name and answer with the linked
  identity, enabled. JF-06 removed the local password branch.
- Media evidence for MU-20, MU-21, and MU-22. `evidence.evidence_cache.evidence(db,
  account, resolve, now)` returns a `MediaEvidence` whose `user`, `catalog`,
  `libraries`, and `items` are `decide_media`'s keyword arguments; close it
  after the decision. The user is the stored snapshot. The catalog and library
  folders are re-read when older than five minutes; a failed re-read reuses the
  last complete copy while it is at most 24 hours old, and `server_mismatch` or
  a missing connection answers `server_mismatch` for all three. Item decisions
  fall back per item the same way. Each read is numbered as it begins, and its
  answer replaces a cached one only if that one's read began earlier, so a
  slow older answer never becomes the outage fallback. The cache is in process
  memory, so after a
  restart during an outage linked media is `unresolved` until Jellyfin answers
  (decided 2026-10-22; profile access remains).

| Route | Capability | Notes |
| --- | --- | --- |
| `POST /api/auth/jellyfin/login` | Public | `LoginRequest`; 401 `invalid_credentials`, 403 `account_disabled` or `jellyfin_user_blocked`, 409 `jellyfin_not_configured`, 503 `jellyfin_unavailable` (also when a newer Jellyfin read already changed the account), 429 `login_throttled` |
| `GET /api/auth/jellyfin/quick-connect` | Public | `QuickConnectAvailability` `{enabled}`; false when Jellyfin is not configured, unreachable, or has Quick Connect off; Jellyfin's answer is reused for ten seconds, and the false from a failed check for three seconds; 423 `setup_required` |
| `POST /api/auth/jellyfin/quick-connect/initiate` | Public | 201 `QuickConnectAttemptResponse` `{state: pending, code}` and an HttpOnly handle cookie that replaces the browser's earlier attempt; 403 `quick_connect_disabled`, 409 `jellyfin_not_configured`, 429 `quick_connect_throttled` (ten per client address or IPv6 /64 per ten minutes, 30 per minute in all), 503 `jellyfin_unavailable` or `quick_connect_busy` (1024 codes are waiting; none is dropped) |
| `GET /api/auth/jellyfin/quick-connect/status` | Public | `QuickConnectAttemptResponse` `{state: pending, approved, expired, cancelled, or consumed, code}` for the browser's handle, asking Jellyfin at most once a second per code; 404 `quick_connect_not_found`, 503 `jellyfin_unavailable` (the attempt stays pending) |
| `POST /api/auth/jellyfin/quick-connect/complete` | Public | `QuickConnectSignInRequest` `{remember_me, return_to}`; spends the approved attempt once, then the same admission, refusals, and session as Jellyfin password sign-in; 401 `invalid_credentials` when Jellyfin refuses the code, 404 `quick_connect_not_found`, 409 `quick_connect_pending`, `quick_connect_expired`, `quick_connect_cancelled`, or `quick_connect_consumed` |
| `POST /api/auth/jellyfin/quick-connect/cancel` | Public | 204; ends the browser's attempt and clears its handle cookie |
| `GET /api/jellyfin/users` | `accounts.jellyfin.import` | listed users with any linked `user_id` and `blocked` |
| `POST /api/jellyfin/users/import` | `accounts.jellyfin.import` | `{all: true}` or `{user_ids: [...]}` (at most 1000); per-user `imported`, `existing`, `upstream_disabled`, `blocked`, `not_found`; 503 `jellyfin_unavailable` when the clock was found stepped back after the listing (retry) |
| `GET /api/jellyfin/sync` | `accounts.jellyfin.sync` | `running`, `last_run`, and each linked account's effective `link_state`, `upstream_state`, `last_verified_at`, `grace_expires_at`, and last attempt |
| `POST /api/jellyfin/sync` | `accounts.jellyfin.sync` | per-account `outcome`, `link_state`, `role`, `status`, `failure`; 409 `sync_in_progress` |
| `GET /api/jellyfin/blocks` | `accounts.list` | `{blocks: [{server_id, jellyfin_user_id, name, blocked_by, blocked_at}]}`, newest first |
| `POST /api/jellyfin/blocks/unblock` | `accounts.jellyfin.unblock` (owner by default; fresh interactive) | `{server_id, jellyfin_user_id, reauthentication}`; the remaining blocks; 404 `jellyfin_user_not_blocked`; both IDs must be GUIDs (422 otherwise); stores `account.jellyfin.unblock` with `resource` `<server_id>:<jellyfin_user_id>` when it unblocks and when it answers 404 `jellyfin_user_not_blocked`; a capability or reauthentication refusal stores the event without a `resource` |

JF-06 removed the sign-in status field `administrator`.

## Account linking

MU-35 links an account without a Jellyfin link to a Jellyfin identity and
relinks a linked account whose stored server differs from the configured one.
Since JF-06, linking is how an account disabled with cause `unlinked` becomes
active again. Code lives in
`scanner/security/account_links/` (`proofs`, `plan`, `transition`),
`scanner/persistence/repository/export_identity_moves.py`, and
`scanner/http/account_link_api.py`.

- Proof. `POST /api/users/{user_id}/link/proof` (`accounts.link.own` or
  `accounts.link.other`, fresh) proves the target account and one Jellyfin
  identity. Jellyfin proof is `adapter.authenticate`; a rejection is 422
  `jellyfin_proof_failed`. The target account is proven on a `ProofBasis`
  (user ruling wave12-mu35-relink-proof):
  - `actor_authentication`. The caller's own account is proven by its fresh
    authentication.
  - `admin_attested`. Every other account, on a first link or a relink, needs
    no target password: the caller's fresh interactive authentication and the
    new Jellyfin sign-in suffice. Who may link whom is unchanged: admins link
    themselves and ordinary users, never another admin or the owner; the owner
    links admins.
  JF-06 removed the `account_password` basis and the request's
  `account_password` field, which now answers 422 as an unknown field. Stored
  audit rows with basis `account_password` still read.
  Failed proofs are throttled per target and address (429 `link_throttled`). The
  result is `{proof, expires_at, preview, confirmation}`. `ProofRegistry` keeps
  only the token's SHA-256 in memory for ten minutes and binds it to the actor,
  its session, the target, the basis, and a `ProofBinding`: the target's
  permission revision, its stored identity, and the proven identity. A token
  presented by another actor, session, or target is destroyed (409
  `link_proof_invalid`); expiry is 409 `link_proof_expired`; a changed binding
  is 409 `link_proof_stale` and spends the proof. A restart voids every proof.
- Preview. `LinkPlan.preview()` reports `kind` (`link`, `relink`, or null),
  current and proven identity, current and new export ID, `proof_basis`, role (`current` and
  `after` are equal: linking never changes a role), media basis (`local`, `jellyfin`,
  `suspended`) and the proven user's library and parental summary, synced name and
  avatar, `status` (`current` and `after`, each `active` or `disabled`, and the
  current disable `cause`; `after` is `active` when the cause is `unlinked`),
  one entry per personal publication (`move` with content, `record` without, and its
  `conflict`), and `blockers`. `confirmation` is the SHA-256 of the canonical
  preview JSON. Blockers: `already_linked` (same server; no user switch and no
  unlink), `jellyfin_identity_in_use` (another account, including an imported
  one, holds the identity; resolution is explicit deletion or transfer),
  `jellyfin_user_disabled`, `account_disabled`, `link_in_progress`, and per export
  `export_regeneration_pending` (queued, running, or withdrawing job),
  `export_recovery_required` (a journal at the old path), `export_filename_collision`
  (another publication row has the new path, or two moves share it),
  `export_file_collision` (any entry or journal at the new path, for record-only
  moves too),
  `export_destination_changed`, and `export_destination_unavailable`.
- Link. `POST /api/users/{user_id}/link` (`{proof, confirmation}` plus fresh
  authentication) rereads the proven identity with `verify_user` in a numbered
  read, then under the export publication lock recovers the account's open
  transition, rebuilds the plan under the write lock, rereads the proof binding
  (409 `link_proof_stale`), refuses blockers (409 `link_blocked` with the preview)
  or a different confirmation (409 `link_preview_stale` with the preview), and
  spends the proof. The result is `{state: completed | removing, preview}`. A
  proven identity without an avatar removes the account's local avatar, as the
  preview's `avatar: none` says.
- Transition. `account_link_transitions` (0023) records `kind`, the old and new
  identity, `from_export_id`, actor, and `state`; `account_link_export_moves`
  records each publication's old and new path, the old ownership digest, the
  new content digest, `new_file_id`, and `requeued` (see Recovery). `begin` commits `writing` with its moves before any file is
  written. `write_files` places each new file (refusing any existing entry)
  through one reserved staging name beside it, `.<name>.editarr-link.tmp`. It
  creates the staging file exclusively, fsyncs it, commits its inode number, read
  from the creating descriptor, as the move's `new_file_id`, and only then places
  it with a hard link, which refuses
  an existing entry atomically, before removing the staging name. A file system
  without hard links falls back to a check then a rename. The planned digest is
  not evidence of a write: the new path is Editarr's only when its identity is the
  recorded `new_file_id` and its bytes match the new digest.
  `switch`, under the write lock after the files are written, first rereads the
  proof binding and refuses a change (`link_proof_stale`), an identity another
  account now holds (`jellyfin_identity_in_use`), a disabled account, or a newer
  Jellyfin read; each refusal rolls back as `refused` and keeps the old exports.
  An access refusal from revalidating the actor there (for example, the target
  became an admin) also rolls back as `refused`. Otherwise it writes the identity (a relink resets `observed_generation`), applies the
  fresh snapshot through `store.observe` (role, name, avatar, media policy,
  verification), points each publication at the new ID and path, advances its
  `publication_revision` (UUID kept), bumps the permission revision, and enters
  `removing` in one transaction with the `account.link` event. A record-only move
  (withdrawn or deleted publication) drops `owned_sha256` from the moved row:
  ownership evidence belongs to the path Editarr wrote, and the move row keeps
  the old path and digest. `finish` removes each old file only when its bytes
  match the old digest (`removed`, `absent`, or `kept`), settling each move in
  its own commit, and completes. Any other filesystem error keeps `removing`.
  Owned bytes of a withdrawn, undeleted record re-queue the moved publication
  (MU-34's returned-bytes rule), so it serves again under the new ID. The re-queue
  commits before the unlink, because the unlink destroys its only trigger: a crash
  or a directory fsync error after it leaves the job queued, and one before it
  leaves the bytes for the next pass. The re-queue commits with the move's
  `requeued` mark, so owned bytes that cannot be removed (an unwritable export-ID
  directory) re-queue the publication once, not on every pass; the mark clears
  once a pass finds no owned bytes there.
- Confined paths. Every cleanup that reads or removes a file (old-path removal,
  reconciliation, rollback of new files, staging cleanup, and pruning an empty
  export-ID directory) first applies `safe_destination`'s component rule
  (`export_publication.check_export_components`): the media directory resolves to
  itself and no entry below it is a symbolic link. A path redirected through a link
  is never read or removed, even with matching bytes, and its move settles `kept`.
  Export paths resolve through `export_publication.resolve_directory`, which
  raises OSError (ELOOP) for a symbolic-link loop where Python 3.11 raises
  RuntimeError. A loop or any other unresolvable media directory therefore
  defers like any filesystem error other than ENOENT: the move stays unsettled or
  keeps its outcome, the sweep goes on to other transitions and publications, and
  the link preview reports `export_destination_unavailable`.
- Recovery. `transition.recover` runs before every link of the account and at the
  start of `sweep_regenerations` (startup, then hourly). `writing` rolls back:
  a new file is removed only when it is the recorded `new_file_id` with the new
  digest, so a file that appeared at the new path before placement survives even
  with matching bytes; old files and the identity are untouched (`rolled_back`,
  `interrupted`); a regular file at a staging name is removed whatever its bytes,
  since the name is reserved for link writes.
  The identity omits the device number, because a reboot or share remount after
  the crash can renumber it, and the confined path already fixes the file system
  (ruling wave12-mu35-new-file-identity). On a file system whose inode numbers
  also change across mounts (for example CIFS with `noserverino`), rollback cannot
  prove the placed file is Editarr's and keeps it. The account's next link then
  reports `export_file_collision`. The preview omits host paths, so the operator
  locates the file through the rolled-back transition's move row
  (`account_link_export_moves.new_path`), which is the destination export ID's
  EDL or MCF path beside the media. That file is the interrupted link's copy of
  the user's export. The remedy is to remove it and link again.
  Every removal also follows MU-38's shared removal rule (rulings
  wave13-mu38-failsafe-recovery and wave13-mu38-defer-restore,
  `export_identity_moves.removable`): the file is Editarr's, no publication records
  its path, and the publication's committed bytes stay at the path it records now
  (`serves`; a deleted or withdrawn publication keeps nothing on disk). A file the
  rule keeps stays, but the transition still rolls back and the move still settles
  (an old file settles `kept`). Recovery never writes export bytes.
  `removing` finishes. A failed write rolls back at once (`write_failed`, 409). An
  uncertain switch commit is resolved by rereading the transition from a fresh
  connection. Afterwards `reconcile_old_paths` revisits every completed move
  settled `absent` or `kept`: one ENOENT is not permanent removal (MU-34
  wave11-mu34-ownership-evidence), so a regular file that returns there with the
  old digest is removed and the move becomes `removed`, re-queueing a withdrawn,
  undeleted publication as `finish` does; other bytes stay, and a path a
  publication records again is left to it. The publication may have moved on (a
  later relink or transfer): the bytes go once the removal rule allows it. A settled
  move never reopens.
- Transfer interlock. A link preview (proof) first recovers the account's open
  account-transfer moves (MU-38) under the publication lock. Moves that recovery could
  not settle (a filesystem error), then or at the link itself, block the link with
  `account_transfer_unsettled`.
- Cleanup read. Every account detail read (`GET /api/users/{user_id}` and the
  other routes returning `AccountDetail`) carries `link_cleanup`
  (`scanner/security/account_links/cleanup.py`, MU-39 fix ruling
  wave13-mu39-recovery-read): null when the account has no `removing` or
  `completed` transition, else the latest such transition's `state` and its
  moves' `pending` (unsettled), `removed`, `absent`, and `kept` counts. It
  names no path, digest, proof, or credential and changes no link behavior.
- Sync ordering. `store.observe(..., identity=)` and `store.apply(...,
  identity=)` refuse evidence read for an identity the account no longer has;
  `sync_accounts` passes each listed identity, so a read begun before a relink
  never applies to the new identity.
- Audit. `account.link` (security; user or system actor; target required) with
  `step` (`proof`, `link`, `recovery`), `kind`, `state`, `exports`, `role`, and
  `reason`, plus `basis` once the request has established it: every refusal
  after the link route has read a valid proof, including access refusals under
  its locks and provider refusals from the fresh Jellyfin read
  (`jellyfin_unavailable`, `jellyfin_user_unavailable`), names the proof's basis
  and is recorded once. Every reason the
  routes and `SwitchRefusal` emit is registered. No password, Jellyfin
  credential, or proof token is stored. Visibility
  is `accounts.link.own` / `accounts.link.other`.
- Unreachable server. With the configured server permanently unreachable, nobody
  can sign in, including the owner, and nobody can prove a new identity (503
  `jellyfin_unavailable`). Linked non-owner accounts stay profile-only. Once a
  replacement server is configured, an admin or the owner relinks each account on
  `admin_attested`; accounts not yet relinked stay suspended.
- Not provided. Unlinking, automatic merges, name matching, and data transfer
  between accounts.

## Account disposition (MU-38)

An admin or the owner deletes another account after previewing what the chosen
mode does to it: `retain`, `transfer`, or `delete` (permanent). Fix round 1
added transfer under the 2026-10-28 rulings (decision `wave13-mu38-transfer`).
Fix round 2 applied `wave13-mu38-transfer-visibility` and bound transfer
recovery to the confirmed transfer. Fix round 3 named each visible publication in
the consent, and fix round 4 made those publications follow the export rules. Fix
round 6 applied `wave13-mu38-defer-restore`: transfer and link recovery keep their
round-2 decisions, every removal follows the shared removal rule kept from
`wave13-mu38-failsafe-recovery`, recovery never writes export bytes, and the owner's
`editarr admin write-export` puts a publication's committed bytes back. Detecting
and blocking restored move records that disagree with the disk is MU-40's.

- Routes. `GET /api/users/{user_id}/deletion?mode=retain|transfer|delete`
  (`&destination=ID` for a transfer) previews, `POST /api/users/{user_id}/deletion`
  applies, and `POST /api/users/bulk-deletion` applies up to 100 targets. All need
  `accounts.delete.other` per target (admins: ordinary users; owner: any other
  account). The capability is `fresh_interactive`: API keys get 403
  `interactive_session_required` from the middleware on all three routes, and a
  POST without `reauthentication.password` answers 403
  `reauthentication_required` (wrong password: `reauthentication_failed`). A
  preview needs only the session. Bulk verifies the password once; a wrong or
  throttled password stops the request. The caller's own account answers 409
  `own_account`; the owner and, for an admin, another admin answer 403
  `protected_account`. Hidden and absent accounts answer 404 alike.
- Visibility. Responses name only the account's reviews the caller may view and
  scans or jobs it may see with its current media evidence; the rest are
  `hidden` counts. Account management never grants access to private media.
- Consent. `account_disposition.disposition_gate` captures media evidence for
  every review, scan, and publication of the account (and the destination's
  evidence for the reviews' media), then builds one `AccountImpact` under the
  writer lock for preview and execution. `account_consent.preview` projects it:
  `reviews {count, hidden, ids}`, `files` (delete mode: each visible publication
  with its MU-36 choices), `scans {hidden, items}` (delete mode), `jobs {waiting,
  running, hidden}`, `personal_files`, `publications {hidden, items}` (every
  mode: each personal publication of a review the caller may view whose file the
  export rules let it see, `export_discovery.require_export` as MU-36 and MU-37
  apply it, as an `ExportPublication`; this includes every visible file a
  transfer moves),
  `transfer {files_moved, policy_files}`, `credentials`, `community`, and
  `blockers`. `confirmation` is the SHA-256 of that body, with each file reduced
  to review, row, publication UUID, revision, and choices, each listed
  publication to row, UUID, revision, and content SHA-256, plus each review's
  MU-36 plan digest. Any change to a count, a visible ID, a publication's
  identity, revision, or content, a job's state, a credential, or a file
  inventory asks for a new confirmation (409 `stale_account_deletion`).
  Publications of hidden reviews, or hidden by the export rules (for example
  without `exports.personal.delete.other`), are a count only, so changing one
  without changing the count keeps the confirmation.
- Blockers, each with a count only: `link_in_progress` (an open MU-35 transition
  of the account or the destination); `account_transfer_unsettled` in every mode
  (moves of the account's transfer that recovery could not yet settle, which
  happens only on a filesystem error); delete mode
  `account_jobs_running` and `scans_outside_access`; delete and transfer
  `reviews_outside_access` (reviews the caller may not view, for every
  destination including the caller); transfer
  `transfer_media_inaccessible` (reviews on media the destination may not access;
  no partial transfer) and each MU-35 move conflict code (`export_filename_collision`
  for a destination publication at the same path, `export_regeneration_pending`,
  `export_recovery_required`, `export_file_collision`, ...). A blocked request
  answers 409 `account_deletion_blocked`.
- Every mode. Sessions are revoked, the key is deleted, the username is
  cleared, the Jellyfin identity and its sync row are released, and
  the account becomes `disabled` with cause `local`. The request's `jellyfin_user`
  (`allow`, the default, or `block`) decides whether a linked account's Jellyfin
  user may come back: `allow` lets its next sign-in create a fresh account,
  `block` stores it in `jellyfin_user_blocks` first (see Blocks). A local account
  ignores it. The result and the `account.delete` event carry `jellyfin_blocked`,
  and a retry replays only with the same choice. The row stays as a
  tombstone for audit and provenance references. `resolve_account` never
  resolves it and the Users list omits it, so every account route answers 404.
  Waiting and running jobs stop through the MU-21 job gate, which refuses a
  disabled initiator. Community identity, keys, operations, and publications
  stay in place; nothing contacts the community service.
- Retain. Reviews, scans, exports, regeneration jobs, filter preferences, and
  the profile stay unchanged, so the plugin keeps serving retained files. When a
  re-admitted user later receives the released Jellyfin ID, its first publication
  at a retained path answers 409 `export_confirmation_required` and replaces the
  retained publication only with that confirmation
  (`export_publication._released`).
- Transfer. The destination is any active, undeleted account other than the
  target that the caller can resolve (including the caller and the owner); any
  other destination answers 409 `transfer_destination_unavailable` alike. The
  account's reviews get the destination as `owner_id` (`original_author_id`
  kept, `revision` advanced); scans keep their initiator. Every personal
  publication targeting the account moves to the destination's export ID through
  MU-35's move machinery: the publication keeps its UUID, advances its revision,
  and gets the destination as `target_user_id`. Detached publications whose
  policy owner is the account record the destination in
  `intended_policy_owner_id`. Publications of other accounts' live reviews move
  but keep following their review owner's policy (MU-34). Every affected
  publication is re-queued, so regeneration applies the destination's policy.
- Transfer files. Under the publication lock: the consent is confirmed and one
  `account_transfer_moves` row per move commits, recording the confirmation;
  each new file is staged, its inode committed, then placed; the consent is
  confirmed again and the switch commits with the disposition record; old files
  Editarr recognizes are removed. A write failure discards the placed files the
  removal rule allows, drops the rows, and answers 409 `export_write_failed` (503
  `database_busy` for a busy database), audited, with nothing changed.
- Move recovery. `account_transfer_files.recover` decides from the binding alone:
  rows whose `confirmation` equals the account's committed `transfer` disposition
  switched, so their old files are removed; any other rows (no disposition, or a
  retain, delete, or other transfer recorded later) never switched, so their placed
  files are discarded by recorded identity and digest and the rows dropped. Every
  removal, here and in MU-35 link recovery (`transition.roll_back`, `finish`,
  `reconcile_old_paths`), follows one shared removal rule (rulings
  wave13-mu38-failsafe-recovery and wave13-mu38-defer-restore,
  `export_identity_moves.removable`): the file is Editarr's by recorded inode and
  digest (placed file) or ownership digest (old file), no publication row records
  its path, and the publication's committed bytes are at the path it records now
  (`serves`; a withdrawn or deleted publication keeps nothing on disk). A restored
  database can disagree with the disk, and without the rule recovery could remove
  the only usable copy (review round 3 R3-F1). A file the rule keeps stays, but its
  record still closes: an undone move's row is dropped and an old file settles
  `kept`. Settled `absent`/`kept` old paths stay watched, so owned bytes there go
  once the rule allows it. Recovery never writes export bytes, and a settled move
  never reopens. A refused transfer or link whose new files were never placed
  leaves nothing open (R6-A), and a user's edit or deletion of their own sidecar
  never blocks (R6-B).
- Owner remedy. `editarr admin write-export <export_id>` (`scanner/commands/admin.py`,
  `export_committed.write_committed`) creates the publication's recorded file from
  its stored committed bytes, for example after a restore or a hand deletion left
  that path empty or the bytes at another path. It asks for the owner's Jellyfin
  password ("Owner's Jellyfin password: ", or `--password-stdin` from standard
  input) and proves it with `verify_linked_password` against the configured
  server and the owner's linked user ID. It refuses with "No owner is
  configured" when no owner exists, names Jellyfin and backup restore when the
  owner has no Jellyfin link, says so when Jellyfin is unreachable, and answers
  "Jellyfin did not accept the owner's password" with an audited
  `reauthentication_failed` when Jellyfin refuses. It records `export.write_committed` with that account as actor,
  `succeeded` or `failed` with the refusal as `reason`. It never replaces a
  directory entry. Under the export publication lock and SQLite's write lock it
  first confirms the account is still the owner (`capability_denied` otherwise),
  then checks the recorded path by name: it must be the layout path that the
  publication's media path and export ID name, with no symbolic link below the
  media directory (`safe_destination`), and no other publication may record it
  (`export_filename_collision`). These checks write nothing. Missing export
  directories below the media directory are then created by pathname
  (`prepare_export_directory`, the same empty-directory window MU-31 and MU-34
  have: a directory swapped for a link at that moment can receive an empty
  directory, never bytes). The command never runs export recovery, which writes
  by pathname. It then opens the media directory and
  each export directory below it as descriptors that follow no symbolic link, and
  inspects the entry through them. Any entry at the publication's recovery journal
  name (`.<name>.editarr-recovery.json`) is `export_recovery_required`: an
  interrupted publish or regeneration is pending. The operator lets MU-31's
  recovery finish it, through the export sweep the running app performs at start
  and hourly (`sweep_regenerations`) or the next publish of that review, and then
  reruns the command. A journal that recovery cannot use is logged by the sweep
  as needing manual sidecar recovery, as MU-31 documents. The committed bytes already there are a success that writes nothing
  ("already present"). Any other file, link, or entry is `export_file_collision`.
  An empty path is created without replacing anything: a hidden temporary file
  in that directory, named `.editarr-write-export-<32 hex>.tmp` so its length
  does not depend on the export name, is hard-linked to the name, or, where the
  filesystem has no hard links, the name is created exclusively and written in
  place. An entry that appears meanwhile makes it refuse, and the placed entry is
  verified through the descriptor that wrote it. The longest name a publish can
  record is 232 bytes, since MU-31's journal name adds 23; the command writes
  every such name. A directory
  swapped for a link after the name checks is `export_destination_changed`, and a
  platform without no-follow directory descriptors (Windows) is refused with
  `export_destination_unavailable` rather than written by pathname. The success
  event is staged before the file is placed. Any failure or interrupt
  (`KeyboardInterrupt`, `SystemExit`) from the creation of the file until the
  commit returns removes the file it created, when it is still that file holding
  the committed bytes or a prefix of them. The temporary name is unlinked once,
  only while it is still the command's file. If the commit fails the command
  exits 1 saying whether the file was removed; an edit made in that window is left
  in place and named. Any other database failure exits 1 with a short message and
  no file. A hard crash (a killed process or lost power) after the file is created
  and before the commit leaves the file without its event. An interrupt that
  lands as the commit returns removes the file while its succeeded event stands.
  In both cases a rerun converges. With hard links the export name only ever
  holds the complete bytes, so the rerun records them as already present. Without
  hard links a hard crash, or an interrupt in the instant between creating the
  name and recording its descriptor, can leave a partial file at the name; the
  rerun refuses it as `export_file_collision`, and the operator deletes it and
  reruns. A crash
  can also leave a hidden temporary file. After a successful run, never after a
  refusal, the command removes each regular file with its temporary name in that
  directory that holds a prefix of the committed bytes, which a crashed run's
  temporary always does. Any other file under that name is left alone. The
  publication lock keeps runs against one data directory apart; another Editarr
  instance sharing the media directory has its own lock, and at worst its cleanup
  makes this run fail with `write_failed` and nothing left.
  It never creates the media directory. Its connections hold the maintenance
  lease like every database user (`runtime.maintenance`), so it refuses during a
  backup, restore, or other database maintenance. A refusal exits 1 with the code
  (`not_found`, `capability_denied`, `export_withdrawn`, `export_file_collision`,
  `export_filename_collision`, `export_destination_changed`,
  `export_destination_unavailable`, `export_recovery_required`, or
  `write_failed`) and creates no file. Editarr bytes from an older version are a
  collision, not something it replaces. The moves themselves need nothing: a
  `kept` old copy goes at the next reconciliation once the recorded path
  verifies. Editarr never chooses between copies left at unrecorded paths; those
  stay for the operator.
- Restored moves (rulings wave13-mu38-defer-restore and
  wave14-fresh-install-restore). A backup captured during a transfer or link and
  restored later can bring back move records that disagree with the disk.
  Recovery keeps every usable copy: the restored records close, a copy at an
  unrecorded path stays without a record, and the publication may record an
  empty path until the owner runs `write-export`. The restore report lists each
  disagreement and blocks nothing; see "Restore into an empty installation
  (MU-40)". Review rounds 3 to 6 (R5-C, R6-C) hold the cases.
- Unfinished transfers. Before any mode is previewed or applied, the account's
  unfinished transfer is recovered under the publication lock (a preview takes
  the lock only when move rows are open). Moves recovery cannot settle block
  every mode with `account_transfer_unsettled`. Confirmed retain, delete, and
  transfer hold the publication lock through their commit. A preview otherwise
  runs without it: the confirmed attempt recomputes the impact under the lock and
  any difference is `stale_account_deletion`. The MU-34 sweep also recovers.
- Delete. Each review is deleted through `review_disposition.stage_deletion`
  with the caller's per-file choice; hidden and legacy publications are retained
  as MU-36 does. Then each of the account's scans is removed unless a surviving
  review, any remaining publication, or pending community work references it
  (`scan_deletion.remove_unreferenced_scan`). The profile and avatar are removed.
  Filter preferences stay so retained files keep their policy.
- Record. `account_dispositions` (0024) is keyed by `user_id` and stores
  `actor_id`, `mode`, `destination_id` (only and always for `transfer`), the
  `confirmation`, `impact_json` (exactly the confirmed preview body), and
  `result_json` `{"result": ..., "inventory": ...}`: the caller's result and the
  full internal inventory (every review, retained, removed, moved, and policy
  file ID, scan, and stopped job). The result lists visible `reviews_deleted`,
  `reviews_retained`, or `reviews_transferred` with `reviews_hidden`,
  `files_retained`, `files_removed`, `files_moved`, visible `scans_deleted` and
  `scans_kept` with `scans_hidden`, visible `jobs_stopped` with `jobs_hidden`,
  `personal_files_kept` (counted after the commit), the community counts, and
  the credential outcomes. Backups carry it with `users`, so a restored
  tombstone stays deleted.
- Retries. A POST naming a deleted account by the actor who deleted it, with the
  stored mode, destination, and confirmation, returns the stored result with
  `replayed: true` and changes nothing. Any other request about it answers 404.
- Interruption. A failure or hard exit before the commit leaves every row as it
  was; MU-36 removal journals restore staged file removals, transfer recovery
  discards placed files, and the same confirmation applies on retry. After the
  commit the record exists and journals or transfer recovery finish the files.
- Refusals. Every refusal of a confirmed attempt stores one `account.delete`
  event with its reason, after rollback. Expected operational failures map to
  `export_io_failed`, `export_destination_unavailable`, `export_write_failed`,
  `export_recovery_required`, MU-31 export codes, or 503 `database_busy`.
  Previews are reads and are not audited.
- Bulk. Targets run in order, each authorized, confirmed, and committed alone.
  Each result is `applied` with its result or `refused` with its code, including
  an operational failure of that target after it rolls back; later targets still
  run. A confirmation is bound to its account ID, so substituting one target's
  confirmation for another is refused as stale.

## Restore into an empty installation (MU-40)

Ruling wave14-fresh-install-restore. A restore fills an empty installation with
everything a full backup holds. It never merges into a configured installation,
so no account mapping, per-restore choice, or comparison with the target exists.

- Only full backups exist. `export_backup` and `POST /api/backups/jobs` have no
  `kind` choice (`kind` may be sent only as `full`), and `editarr backup export`
  has no `--kind`. A saved `backup-schedule.json` whose config says `"settings"`
  loads as `"full"` and sets `notice: "settings_schedule_now_full"` in the
  schedule status until the schedule is saved again. The API refuses a new
  `kind: "settings"` with 422.
- Source schemas. `export_backup` accepts every supported source schema, from
  the unversioned baseline (manifest `schema_revision` `unversioned-v1`) through
  head. It classifies its private snapshot with `adoption._inspect_database` and
  refuses any other schema with `ValueError`, leaving the source unchanged.
  Restore upgrades an older archive only in staging.
- Entry points. Onboarding (`/api/setup-restore/...`, anonymous only while owner
  setup is required) and `editarr backup restore ARCHIVE [--password-stdin]
  [--confirm]`. `editarr backup inspect` prints the same plan. Settings has no
  restore or inspection controls, and `/api/backups/jobs` has no archive,
  preflight, or activate route.
- Empty target (`backup.restore_target.require_empty_installation`). A missing
  database is empty. Otherwise the database must pass the same read-only
  admission as an upgrade (`adoption._inspect_database`: a supported revision
  or the frozen unversioned baseline, exact schema, integrity and foreign keys),
  so a rollback can always put it back; anything else, including an empty
  file, is `restore_target_unsupported_schema` and is never stamped or changed.
  `users` must have no row. Each of `SEEDED_TABLES` (`community_identities`,
  `filter_settings`, `mcf_severity_defaults`, `media_access_scopes`) must hold
  exactly the rows the packaged migrations seed at that revision, compared
  without `created_at`, `updated_at`, and `credential_ref`; the reference is
  built by running those migrations in memory. Every other table must have no
  row, and neither `secrets/community/` nor any home may hold identity files. A
  refusal raises `RestoreRefused` with `reason` `restore_target_has_accounts`,
  `restore_target_has_work` (the message names the table, including a
  customized seeded table), `restore_target_has_identity`, or
  `restore_target_unsupported_schema`, before anything is written. `POST
  /api/setup-restore/flow` and `POST /api/setup-restore/jobs` answer 409
  `{"detail": {"code", "message"}}`; preview and activation check again, and
  activation checks under the exclusive maintenance lock, so an owner created in
  between refuses the switch. A job refused this way is `failed` with `reason`.
  The CLI exits 1 and prints the reason.
- Archive checks (`preview_restore`), all before activation. The manifest kind
  must be `full`; an old settings archive is refused with
  `restore_settings_backup`. The staged database is upgraded only in staging; an
  unknown schema is refused without stamping. `account_family.check_accounts`
  needs exactly one owner that holds a Jellyfin link, or no accounts at all
  (work awaiting owner setup). JF-06 removed the Argon2 hash check. Every `USER_REFERENCES` column must name
  a `users` row of the archive. Two have no foreign key,
  `media_item_exports.intended_policy_owner_id` and
  `media_item_export_regenerations.policy_owner_id`. A test fails when a column
  with a foreign key to `users`, or any integer `*_id` column without a foreign
  key, is neither registered nor listed as not a user reference. Reasons:
  `restore_owner_count`, `restore_owner_credential`,
  `restore_unknown_account_reference`, and `restore_unknown_identity_home`.
- Identity homes. Archive members are `secrets/community/{identity.json,
  current.key,pending.key}` for the owner (or the identity awaiting the owner)
  and `secrets/community-identities/<credential_ref>/...` for every other account
  whose home holds identity files. Each staged home must validate and belong to
  a non-owner account of the archive. Activation validates every enrolled
  `community_connections` row against the identity its `credential_ref` names.
  Pending community operations restore with their database rows and stay
  recoverable through `pending_after_restore` with the restored identity.
- Plan. `{format, kind, protection, source_schema, staged_schema,
  source_identity, account_identities, omitted_environment,
  included_secret_types, administrator_included, backup_deployment}`.
  `backup_deployment` is `{base_url, port}` from the archive's `deployment.json`
  (`null` when the backup used the default); an invalid file fails preview. It
  holds no secret, path, or target state.
- Activation (`activate_restore(preview, database, data_dir, confirmed=True)`).
  One path. The journal lists every member name (database first, the external
  files except `deployment.json`, the incoming identity names, and three names per archived
  home); the database entry is null when the target had none. The staged
  database becomes the target database after `_sanitize_db` deletes every
  `auth_sessions` row, marks `orphaned` every scan that was `scanning` or
  `cancelling` and clears its five worker fields (`worker_pid`, `worker_pgid`,
  `worker_token`, `worker_started_at`, and `worker_heartbeat_at`), clears the
  same five fields on any other scan whose `worker_pid`, `worker_pgid`, or
  `worker_token` is set, marks `orphaned` every `queued` scan with a `scan_queue`
  row, clears `jellyfin_read_epoch`,
  assigns awaiting work to a restored owner, and stages one `backup.restore`
  event (`actor_kind` `system`, outcome `succeeded`, details `stage: restored`
  and `move_disagreements`; audit history shows it to holders of
  `backups.download`, the owner). A scan that was neither `scanning` nor
  `cancelling` and whose `worker_pid`, `worker_pgid`, and `worker_token` are
  already null, such as a settled scan, keeps every worker field, including
  `worker_started_at`, which the API reports as `scan_started_at`.
  External files come from the archive, or stay as
  the target had them when the archive omits them. `deployment.json` is never
  restored: the empty installation keeps its own base URL and listen port
  (ruling wave14-mu40-deployment-binding). API keys and
  `secrets/api-key-wrap.key` come back together and are sealed afterwards.
  Restored Jellyfin user IDs are trusted as recorded. An interrupted switch rolls
  back on the next start or `editarr backup recover`, leaving the empty
  installation, and the target's prior files stay under `restore-recovery/`.
- After activation. `restore-reconciliation.json` pauses the queue as before and
  also records `connection_review_required: true`. Onboarding shows "Restore
  complete" with the request to review media paths and source connections and
  the backup's base URL and port (from the confirmed plan), which the user may
  adopt under Deployment in Settings; the CLI prints the same. It then
  signs in with the restored owner or continues to administrator creation.
- Restore report (`backup.move_report.move_disagreements`, read-only).
  `restore-report.json`, also returned by `activate_restore`, is `{"version": 1,
  "move_disagreements": [...], "backup_deployment": {base_url, port}}`; each
  entry is `{table, move_id, user_id, export_id, finding, path}` for
  `account_transfer_moves` and `account_link_export_moves`. `finding` is
  `recorded_path_missing` (the move's publication records this path and no file
  is there) or `unrecorded_copy` (a file sits at a move path that no publication
  records; an old path settled `kept` is expected). Every file is kept, no
  account is blocked, and no migration is added. The setup job's `report` and
  the CLI print the count.

## Activity storage

Revision `0011_activity_ownership` records ownership and policy provenance.
Revision `0012_personal_filter_values` adds personal filter values. A null owner,
initiator, or actor is the awaiting-owner state. Owner setup assigns it in the
same transaction that creates the owner. A restored backup without accounts stays
awaiting owner setup; restore assigns awaiting work only when the backup holds an
owner.

| Table or column | Holds |
| --- | --- |
| `saved_reviews.owner_id`, `original_author_id` | Current owner, who is also the policy owner, and the author that copy and transfer keep; both null or both set |
| `scans.initiator_id`, `initiator_permission_revision` | Who started the job or created the imported or manual scan, and that user's permission revision then |
| `media_item_exports.scope` | `legacy` (adjacent `Movie.edl` or `Movie.mcf`), `default`, or `personal` |
| `media_item_exports.target_user_id`, `target_export_id` | Set only for `personal`; the export ID fixes the path |
| `media_item_exports.actor_id`, `policy_owner_id`, `original_author_id`, `policy_revision` | Publisher, review owner and author at export time, and the policy revision applied; required for `default` and `personal` |
| `filter_settings.default_mode` | `live` or `copy`, the mode new accounts start in |
| `user_filter_preferences` | A user's mode, the edit revision that guards the user's personal values, and the enforced-policy revision; every account has a row from creation (revision `0031_explicit_filter_modes` added one for each account without) |
| `user_filter_enforcements` | Instance rule IDs (`selection_rule`) and subcategories (`severity`) the owner enforces on a user |
| `user_filter_copy_rules` | A user's independent copy of the ordered selection rules: the `filter_selection_rules` fields and checks, keyed by user and rule ID, unique by user and position |
| `user_filter_copy_severities` | A user's independent copy of the subcategory severities: the `mcf_severity_defaults` fields, keyed by user and subcategory, unique by user and position |
| `user_filter_severity_overrides` | A user's live severity for one subcategory in place of the instance default |
| `user_filter_rule_overrides` | Revision `0016_personal_filter_rules`: a live-mode user's change to one inherited instance rule, keyed by user and rule ID; `disabled`, and the instance rule fields when edited in place (non-null `disposition`) |
| `user_filter_personal_rules` | Revision 0016: a live-mode user's own rules, the `filter_selection_rules` fields and checks, unique by user and position |
| `media_access_scopes`, `media_access_scope_sources` | The local default (ID 1, no user, seeded to all sources) and individual overrides; sources are Jellyfin libraries |
| `archived_media_access_rows` | Revision `0026_archive_manual_files`: the JSON of each Other Files scope source and of every row of the dropped `media_access_exceptions` table, kept and never read by a decision |

The current plugin serves adjacent `Movie.edl` files only. The multi-user plugin
selector will ignore legacy adjacent sidecars.

Two partial unique indexes allow one personal export per target user, media
version, and format, and one `default` and one `legacy` record per media version
and format. Existing exports become `legacy` records with the owner as actor and,
when they came from a review, as policy owner and author. Their policy revision
stays null because no enforced policy was applied. Current sidecar publishing,
status, and deletion read and write only `legacy` records until MU-31.

Neither 0011 nor 0012 writes a preference, value, or enforcement row, and 0016
only adds two empty tables, so every user starts with the instance defaults live.
The value tables reference the user's `user_filter_preferences` row, which must
exist first. A writer of any value increments that row's `revision` in the same
transaction. Copied rules and severities have no link to instance rows, so
instance edits never change them. Subcategories are checked against the MCF
registry by the row classes, and rules also by the same family check the instance
rules carry. MU-14 owns every writer; see "Filter preferences and enforced policy".

Backups and restores carry the filter, enforcement, value, and media access
tables. Runtime SQLite foreign keys are off, so `ON
DELETE CASCADE` never runs in the application. A consumer that deletes a user,
preference, or scope must delete its dependent rows itself; startup refuses a
database with foreign-key violations.

`scanner.persistence.repository.activity_ownership.activity_owner` credits new
reviews, scans, and exports to the owner only when no request principal started
them (upgrade, restore, and owner-run commands). MU-21, MU-22, and MU-31 gave
every request path its principal.

## Saved review authorization

MU-22 exposes 23 evaluated review, import, and credits routes through
`scanner/http/review_access.py`.
There is no schema migration.

- `scanner/persistence/repository/review_access.py` owns `ReviewFacts`,
  `ReviewAccess`, and the own/other action pairs `VIEW`, `SAVE`, `RENAME`,
  `COPY`, `DELETE`, `PREVIEW`, `TIMING`, `CREDITS`, and `EXPORT`.
  A review's `owner_id`, not a contributing scan's initiator or original author,
  chooses the own/other capability. Admin operations remain media-scoped and
  retain protected-account rules.
- Hidden and absent review IDs return the same 404 status, no-store headers,
  and `{"code":"not_found","detail":"Not found"}` body. List routes authorize
  row IDs before summary conversion or sorting. `list_saved_reviews(review_ids=)`
  accepts the resulting allowlist; trusted internal callers may omit it.
- `ReviewAccess.writing` uses the shared
  `scanner/security/access/write_boundary.fresh_authority_write` helper.
  It gathers closed provider evidence outside the write lock, then compares
  stored authority after `BEGIN IMMEDIATE`. The comparison covers the account
  and permission revision, media scope, stored Jellyfin policy, read generation,
  clock fence, read time, observed generation, and restore epoch. A mismatch
  rolls back and gathers new evidence. After three unsuccessful attempts, the
  operation refuses without a write. `ReviewAccess.run` also compares stored
  review and scan facts, then re-decides media and capabilities with captured
  evidence before invoking the repository operation. Successful `run` and
  `create` callbacks commit before returning, including read-only callbacks,
  so response projection cannot inherit a writer transaction. Provider calls
  never occur inside that write transaction.
- `scanner/http/review_targets.py` captures an authorized `ReviewTarget` before
  locking. `run_review` uses it for edits, copies, timing, previews, and exports.
  `ReviewTarget.store` checks path mappings and physical readability inside the
  transaction, then updates only the captured source record. Existing physical
  membership/version validation and optimistic revision checks still apply.
- General saves require save/edit plus the capability for each changed field.
  Evidence, metadata, synchronization, credits, filters, and cues retain their
  separate checks. Filter selection/classification and cue actions/comments
  are independent. Stale revisions remain 409 `stale_review_revision`.
- Creation and both upload imports attribute the review and imported evidence
  to the acting user. A duplicate belongs to the actor, preserves
  `original_author_id`, and references the same immutable scan evidence.
  The current review owner is the policy owner for MU-24; this task does not
  implement the enforced-policy view.
- Imports by upload or server path, copies, and duplicates require all five
  destination-own content capabilities: `reviews.evidence.edit.own`,
  `reviews.filters.edit.own`, `reviews.cues.edit.own`,
  `reviews.metadata.edit.own`, and `reviews.timing.edit.own`.
  Imports require all five regardless of the file's contents. Copies and
  duplicates use the same `COPY` action. Carrying accepted credits also requires
  `reviews.credits.edit.own`. These checks run before persistence and do not
  replace the operation's create, import, or copy capability checks.
- Imports are browser uploads gated by `reviews.import.upload`; JF-02 removed
  EDL and MCF path import and `reviews.import.server_path`, and stored grants of
  it are ignored. The import target must be a Jellyfin item; a target naming
  another source is 400 `jellyfin_item_required` with the message "This file must
  belong to a Jellyfin item. Open its Jellyfin item and import the file there."
  and nothing is written. Responses without `media.paths.view` omit path
  fields and remove path-bearing diagnostics.
  Export summaries never disclose the attribution ID or embedded summary of a
  private review the caller cannot view.
- Credits acceptance checks the review before reading its target, then requires
  credits-edit and readable candidate evidence in the locked transaction.
  Import acceptance checks create/upload and credits-edit on its media.
  Clips require own-preview capability and readable completed evidence; unfinished
  diagnostics require job visibility. Existing boundary, revision, and physical
  version checks still compute the result. MU-21 start/status/consent is unchanged.
- Review deletion follows [Review deletion with file choices](#review-deletion-with-file-choices-mu-36).
  Plain `DELETE /api/reviews/{review_id}` still returns 409
  `published_review_retention_required` while the caller can see a published or
  legacy file, and leaves the review, files, and export rows intact.

Fixtures `tests/test_review_access_http.py` and
`tests/test_review_access_workflows.py` reuse `mu04-access-builders-2` and MU-21's
`ScanApp`. They exercise two users and an admin behind the real middleware,
with a synthetic Jellyfin boundary and controlled local credits computation.
The real `verify.mjs auth --reviews` journey covers Jellyfin item EDL/MCF imports,
independent review copies, actual sidecars, deletion refusal, and fresh mobile
reopen. It does not certify a live Jellyfin adapter.

## Review policy preparation

MU-24 uses `scanner/persistence/repository/review_policy.py` for review content.
`prepare_review_policy` returns `PreparedReviewPolicy` with `owner_id`,
`policy_revision`, `base_filters`, effective `filters`, `required_ids`, and
`missing_evidence`.
It calls MU-14's `enforce_filter_policy`, not a separate matching engine.

- `SavedReview` exposes `base_filters`, `policy_owner_id`, `policy_revision`, and
  `policy_missing_evidence`. The last field lists missing required findings or
  copies whose source provenance cannot be verified. Reads, rename, and draft
  edits remain available. Restoring the missing findings clears the diagnostic.
  The current review owner supplies policy, even when an administrator exports
  the review. Copying uses the destination owner's policy and copies base values.
- Review detail also projects `owner_id`, `original_author_id`,
  `policy_required_filter_ids`, and `policy_locked_severities`. These display
  fields come from the same server preparation and immutable source
  classification; browsers do not re-evaluate enforcement. They do not alter
  stored base filters or authorize a mutation.
- `review_policy_evidence` retains current source-role bindings and detached
  tombstones only while the current owner's policy requires them. Removing or
  narrowing optional membership is legitimate and cannot become mandatory later.
  Removing an editable filter while membership survives does not remove its
  immutable binding. Copies inherit current bindings and tombstones still
  required by the destination owner's policy. Transfers evaluate retained
  evidence against the new owner's policy. No previous owner's effective
  selection or severity becomes underlying content.
- Selection is the saved selection plus enforced Deny matches against saved
  metadata or immutable `ScanFindingRow` evidence. Enforced Allow never unselects.
  Enforced source severity survives reclassification of its editable copy.
  While enforcement is active, missing provenance or removed required evidence
  refuses EDL, MCF, MCF preflight, review previews, and community output preparation with
  `review_policy_evidence_missing`. Read and edit preparation does not raise that
  error, so an incomplete review can be repaired.
- Saves and other policy-sensitive requests carry `policy_owner_id` and
  `policy_revision`. Missing tokens are compatible only at policy revision zero.
  A tokenless effective-basis save of an owned review must also leave each
  existing filter's selection and severity equal to its saved base values.
  An explicit base-basis save submits intentional underlying edits instead.
  This prevents an old owner's enforced view from becoming another owner's base.
  A changed owner or policy returns HTTP 409 `stale_review_policy`, including
  no-op writes. `review_policy_enforced` refuses removal, membership narrowing,
  or relocation of required evidence. Refusals roll back staged changes.
- Every stored minimum, mapped exactly back to its requirement's source
  timeline, covers the immutable source interval. Source-backed bindings retain
  the original source endpoints and source synchronization in the existing
  `review_policy_evidence` columns. They never adopt an editable interval or a
  rounded first-target image. Initial creation, imports, and 0019 backfill use
  that basis; duplicates copy it without arithmetic, and transfers preserve it.
  Required imported snapshots and required retime results use the same exact
  rational affine mapper with tight outward float rounding. Optional imports
  and retimes retain their original nearest-rounded values and output bytes.
  A source-backed requirement's floor is the exact outward image of its immutable
  source interval; it never adopts a previous saved cue. Read and export preparation,
  plain saves, retime, and legacy 0019 backfill reconciliation accept endpoints
  within `16 * machine_epsilon * maximum_absolute_operand` of that fixed floor.
  The operands are the submitted endpoint, floor endpoint, and target synchronization
  endpoints. Preparation snaps the effective view without rewriting the saved base;
  an accepted save stores the floor itself, never an endpoint inside it. This fixed
  anchor prevents the allowance from accumulating across saves. Endpoints further
  inside refuse as before. Legacy near-floor cues export the snapped outward floor.
  The proposed MCF channel must
  contain the required channel. Audio or Video may widen to Both, but cannot
  substitute for each other. Skip covers Mute; other actions must match unless
  an explicit superset relation exists. The same coverage predicate governs
  draft validation and output preparation. Real narrowing, channel weakening,
  action weakening, or moving off the interval is `review_policy_enforced`.
- If enforcement matches only editable saved metadata, its floor is the
  previously stored interval, compared exactly. Synchronization changes map
  that interval with exact rational affine arithmetic and outward float rounding.
  The stored start is at or before the exact mapped start; the stored end is at
  or after the exact mapped end. Therefore its exact inverse image covers the
  previous floor, and any sequence of saves covers the original requirement.
  Round-to-nearest alone is not a coverage-preserving transform, even with normal
  synchronization spans. If outward coverage exceeds the physical runtime, the
  save refuses with HTTP 409 `review_policy_timing_precision` and writes nothing.
  Tiny synchronization windows may widen a floor; no minimum span is introduced.
  With a changed synchronization, endpoints within
  `16 * machine_epsilon * scale` of the server-mapped floor snap to that floor
  before exact comparison and storage.
  `scale` is the maximum absolute endpoint operand across the saved cue, submitted
  cue, mapped floor, and stored/submitted synchronizations. Operand scale accounts
  for cancellation near zero; result ULPs do not. The factor 16 covers roundoff
  from the two affine evaluations being compared. Each validated Adjust snaps
  to the canonical floor, so chained previews do not accumulate client offsets.
  At a twelve-hour scale the bound is approximately `1.53e-10` seconds.
  Validated server retime output uses the same normalization, including a return
  to the stored synchronization. This handles chained unsaved adjustments
  without granting plain same-synchronization edits any allowance. Each save
  covers its predecessor under the exact affine transform after normalization.
  Widening remains allowed. Source-backed projections use this same mapper, but
  use the fixed-floor normalization above even for plain same-sync saves.
  Genuine out-of-runtime imported or retimed cues retain HTTP 422
  `mapped_cue_out_of_bounds` and the message
  `Mapped cue endpoint is outside the media runtime`; retime includes filter IDs.
  An outward floor that cannot fit despite valid nearest endpoints still refuses
  with the precision 409. No additional columns or schema
  revision are introduced; the unshipped 0019 backfill stores source coordinates
  instead of lossy target-space arithmetic.
- EDL/MCF output encloses required intervals on the millisecond grid with exact
  rational floor/ceiling before formatting. Required-review MCF material bounds
  use the same enclosure so a tiny synchronization cannot serialize as an
  invalid zero-length document. This does not rewrite saved content or widen
  optional cues. If covering output cannot fit the physical runtime, publication
  refuses with `409 review_policy_timing_precision` before replacing a sidecar
  or creating an export record. Millisecond output can widen a required interval
  by less than one millisecond at each endpoint, including tiny windows.
- `filter_basis` is `effective` by default. An unchanged effective selection or
  severity preserves its saved base value. `filter_basis: "base"` explicitly
  submits underlying values. Enforcement remains a view, so removing enforcement
  restores the saved content without rewriting review or source rows.
- Creation and manual, EDL, and MCF fill store underlying preferences separately
  from enforcement. Review-context Apply accepts `review_id` and policy tokens
  and reads the owner's saved or draft base. Standalone Apply uses the actor's
  preferences and requires own-filter-edit permission. The pure
  `FilterDefaultsApplyResult` is unchanged. HTTP returns
  `ReviewFilterDefaultsApplyResult` with policy owner and revision.
- Credits acceptance returns `base_filter`; retime and HTTP Apply return
  `base_filters`. Browser drafts retain these alongside the displayed effective
  filters. Save and retime submit underlying values with `filter_basis: "base"`.
  Explicit user edits update the underlying fields, including a deliberate edit
  equal to the displayed overlay. Repeated staging cannot materialize enforcement
  as base content.
- Preview, retiming, credits acceptance, MCF preflight, EDL and MCF export, and
  community review capture consume the same prepared content. Draft results and
  preflight carry policy tokens. Export records identify the policy actually used.
  Publication rechecks owner and policy under the existing writer boundary before
  committing output. A failed sidecar publication retains the preceding file.
- The browser draft retains policy tokens through detail, save, retiming, and
  credits results. Save, duplicate, Apply, retime, credits acceptance, and both
  exports send those tokens. A 409 `stale_review_revision` or
  `stale_review_policy` retains the local draft and reads fresh selected detail
  beside it. Normal mutation remains blocked until the user
  explicitly discards the draft or saves it against that fresh revision and
  policy. Any other 409, such as `review_policy_enforced` or
  `published_review_retention_required`, keeps the draft editable and shows the
  server message. Failed writes are not replayed. Resolving a conflict by saving does
  not require timing permission.
- Saved-review source enums are normalized to wire values before catalog target
  capture. Radarr and Sonarr server keys remain identical to their discovery
  and import keys; normalization does not bypass media authorization.
- Community preparation binds policy, source provenance, and required evidence
  into the private source guard. Queue admission rechecks that guard. Required
  findings cannot disappear through mixed-track exclusion. These private fields
  do not change the public community artifact protocol.

Migration `0019_review_policy_evidence` preserves rows and backfills surviving
source-role bindings from immutable findings and synchronization, not edited
filter intervals. The older schema cannot reconstruct evidence detached before
upgrade. The verified snapshot and journal retain the pre-upgrade database for
explicit recovery. Current memberships and currently required tombstones block
source scan deletion. Optional detached history is removed in the admitted scan
deletion transaction before its findings. Review-owned ORM cascade removes its
ledger children even when SQLite foreign keys are disabled. Frozen backfill
uses the runtime affine operation order, dividing before multiplication.
Immutable findings remain the evidence authority;
missing evidence fails closed rather than fabricating replacements. Schema head
is `0019_review_policy_evidence`, with unchanged route inventory
`editarr-route-actions-8` and 225 method/path pairs. The existing Apply route
decides through the review boundary.

## Filter preferences and enforced policy

MU-14 implements the live-mode decision of 2026-10-21. Import from the defining
modules: `scanner/settings/filter_policy/{values,effective,events,updates}.py`,
`scanner/settings/filter_defaults.py`, and
`scanner/persistence/repository/{filter_preferences,filter_enforcement}.py`.

- Effective defaults. `effective.preferences_view(instance, values)`
  returns `FilterPreferencesView`: the user's mode, `mode_locked`, the edit `revision`, `policy_revision`, the instance revision,
  each rule with its `origin` (`instance`, `personal`, `copy`) and `edited`,
  `disabled`, and `enforced` flags, each subcategory severity with its origin
  (`instance`, `override`, `copy`) and `enforced`, and the enforced rule IDs and
  subcategories. `view.settings()` is the `FilterDefaultsSettings` that fill and
  Apply use. In live mode the instance rules come first in instance order, each
  tracking the owner unless the user switched it off or edited it in place, then
  the user's personal rules in the user's order. In copy mode the user's copy
  replaces the instance values. Deny wins over Allow, so order is display only.
- Enforcement. An enforced rule or subcategory severity always takes its current
  instance value, in both modes, so a mode change cannot escape it. The user's
  values for enforced targets stay stored beneath and return when enforcement is
  lifted. `enforced_policy(instance, values)` and
  `filter_preferences.enforced_filter_policy(db, user_id)` return
  `EnforcedFilterPolicy(policy_revision, rules, severities)`.
- Effective review content.
  `filter_defaults.enforce_filter_policy(filters, policy)` returns
  `EnforcedFilters`, the review view of saved content under the review owner's
  enforced policy. It reads saved content and the policy only, never the owner's
  non-enforced defaults, so it changes only when saved content or
  `policy_revision` changes and MU-34 regeneration stays complete. Each copy takes
  the enforced severity for its subcategory, then the enforcement floor: a finding
  is selected when an enforced Deny matches it with that severity (the saved
  severity when its subcategory is not enforced) and its saved channel. Every
  other finding keeps its saved selection. An enforced Allow never unselects,
  because the saved selection may come from the user's own Deny (decision
  2026-10-22). Saved channels, non-enforced saved severities, and the inputs are
  never modified.
- Apply. `apply_filter_defaults` and `POST /api/settings/filters/apply` read
  saved content (or the editor's draft of it), never the view's output. The
  owner's effective settings include the enforced rules and severities at
  instance values, so Deny wins with the floor included, and the view of an
  Applied result is that result: `view(Apply(saved)) == Apply(saved)`. Apply of
  the view is not promised; the view can select a finding Apply would leave
  alone. MU-24 routes review reads, edits, imports, and exports through the view
  and feeds Apply saved content.
- Modes. Every account has its own mode. Account creation
  (`filter_preferences.start_preferences`) sets it to `filter_settings.default_mode`;
  changing that default affects only accounts created afterward.
- Copies. Entering copy mode (own choice, admin assignment, or starting in copy
  mode) takes a new copy of what the user saw live: inherited rules with the
  user's edits, minus switched-off rules, then personal rules, and severities with
  overrides. A user in copy mode always has a copy, which exists when the user has
  copy severity rows. Live overrides stay stored while a user is in copy mode.
- Instance edits. `repository_settings.replace_filter_defaults` returns the new
  defaults and a list of `FilterPolicyChange`. In the same transaction it refuses
  a new instance rule ID that a personal rule uses, deletes overrides and enforcement of deleted rules (bumping those users' revisions),
  and advances `policy_revision` for each user whose enforced view changed. Its
  keyword `stage_changes` receives that list before the commit.
  `PUT /api/settings/filters` takes `InstanceDefaultsUpdate`, the
  `FilterDefaultsUpdate` fields plus an optional `reauthentication`. When the
  list is not empty, the edit changes enforced policy, so
  `filter_policy_api.admit_instance_policy_changes` authorizes each changed
  account as `filters.policy.enforce` through MU-13's guard: an API key gets
  `interactive_session_required`, a session without the password
  `reauthentication_required`, and a wrong password `reauthentication_failed`.
  A refusal rolls the whole edit back and records one denied or failed
  `filters.policy.change` event (details `cause` and `reason`). An edit that
  changes no enforced policy needs no password, as before. An accepted edit
  stages one `filters.policy.change` event per changed user (target the changed
  user, details `cause: instance_defaults` and `policy_revision`), committed with
  the edit. The response stays `FilterDefaultsSettings`.
- Concurrency. Every per-user writer (values, own or assigned mode, enforcement,
  default mode) takes SQLite's write lock with `BEGIN IMMEDIATE`
  (`filter_preferences.write_lock`) before its first read, then reads, decides,
  and writes in that one transaction. Another request can commit only before
  the lock, so the writer always decides on the latest commit. A value or mode
  write whose `revision` (or an enforcement whose `policy_revision`) is not the
  current one returns 409 `stale_settings_revision` with nothing written; a
  value write that a commit made invalid returns that refusal instead (for
  example `unknown_filter_rule` or `filter_policy_enforced`). A default-mode
  request for the mode already in effect, and a mode write for a user already
  in copy mode, never replace an existing copy. Instance edits claim the
  instance revision before reading per-user rows. A refused writer rolls back
  and releases the lock at once.
- Consistent reads. `filter_preferences_view`, `effective_filter_settings`, and
  `enforced_filter_policy` return the instance defaults, the user's values, and
  the default mode as of one commit, so `policy_revision` always names the
  returned policy. A read overtaken by a filter write repeats; after five
  overtaken attempts it raises `stale_settings_revision`, which the view routes
  and Apply return as 409.
- Older data. Runtime foreign keys are off, so evaluation tolerates rows a
  previous version could leave: overrides of rules the instance no longer has
  are ignored, and a personal rule that reuses an instance rule ID is left out of
  the effective view. An instance edit that adds a rule ID drops stale overrides
  under that ID.
- Policy-change events. `events.FilterPolicyChange(user_id, policy_revision, cause)`
  with cause `enforcement` or `instance_defaults` names a policy owner.
  `filter_enforcement.outdated_policy_exports(db, policy_owner_id)` returns the
  `default` and `personal` export records made under an older policy revision of
  that policy owner. It never selects by target user or publishing actor. MU-34
  regenerates only the personal records; defaults remain outside that guarantee.
- Writes. `save_preferences` replaces the values of the user's current mode,
  `set_mode` sets or clears the user's mode, `set_default_mode` changes the
  instance default, and `filter_enforcement.set_enforcement` replaces the enforced
  set. Each guards its revision with a conditional update. A body must omit
  enforced targets; personal rule IDs never equal instance rule IDs.

Routes (`scanner/http/filter_policy_api.py`, each deciding in its handler, plus the
existing `GET|PUT /api/settings/filters` and `POST /api/settings/filters/apply`):

| Route | Capability | Body |
| --- | --- | --- |
| `GET /api/settings/filters/personal` | any principal | |
| `PUT /api/settings/filters/personal` | `filters.preferences.edit.own` | live or copy values with `revision` |
| `PUT /api/settings/filters/personal/mode` | `filters.mode.choose.own`; refused while enforced | `{revision, mode}`, `live` or `copy` |
| `GET /api/settings/filters/users/{user_id}` | any of the values, mode, or enforce capabilities for that account | |
| `PUT /api/settings/filters/users/{user_id}` | `filters.preferences.edit.own`/`.other` | as personal |
| `PUT /api/settings/filters/users/{user_id}/mode` | `filters.mode.choose.own`/`filters.mode.assign.other` | as personal mode |
| `PUT /api/settings/filters/users/{user_id}/policy` | `filters.policy.enforce`, fresh password; never the owner | `{policy_revision, rules, severities, reauthentication}` |
| `GET /api/settings/filters/default-mode` | any principal | |
| `PUT /api/settings/filters/default-mode` | `filters.instance_mode.edit` | `{mode}` |

`PUT /api/settings/filters` now requires `filters.instance_defaults.edit` in its
handler. `POST /api/settings/filters/apply` applies the caller's effective
defaults to the posted saved content. Refusals use `{"code", "detail"}`: 409 `stale_settings_revision`,
`filter_mode_changed`, `filter_policy_enforced`, `unknown_filter_rule`,
`filter_rule_id_conflict`, `owner_filter_policy`, and 422
`filter_severities_incomplete`. The instance route keeps its review error
envelope and adds `filter_rule_id_conflict`. Enforcement outcomes are audited as
`filters.policy.change`.

## Community identity and audit storage

Revision `0013_community_identities` makes community identities personal and adds
stored audit events.

| Table or column | Holds |
| --- | --- |
| `community_identities` | A random 32-hex `credential_ref` naming one private directory, and at most one user (`user_id` is unique; null awaits owner setup) |
| `community_connections` | The existing connection columns, keyed by `credential_ref` and `service_url` |
| `community_operations.owner_id` | The account whose contributor history the receipt, vote, or publication is |
| `community_operations.actor_id` | Who started it; null when nobody recorded that |
| `audit_events` | MU-01's `AuditEvent` plus `actor_kind` (`user`, `system`, `anonymous`, or `unattributed`), the request ID, and `details_json`; empty until MU-10 writes it |

The upgrade creates one identity for the owner, or awaiting owner setup, and moves
every connection to it. Every operation becomes the owner's history, and its
`actor_id` stays null. Before 0013 nobody recorded who acted, so no actor is
invented. Fresh databases get one identity awaiting the owner. Owner setup and
restore composition assign it.

Private signing files live in `secrets/community-identities/<credential_ref>/`.
Upgrades and restores leave them in `secrets/community/`, the incoming directory,
which keeps the backup archive format and restore journal unchanged.
`repository_community.owner_identity_home` moves them into the owner's home with
one directory rename before any other identity work. A crash leaves the files in
exactly one place, and the next resolution finishes the move. Creating an identity
is refused while incoming files wait, and a move never overwrites a home that
holds identity files. One lock file in `secrets/community-identities/` serializes
every identity change. Backup export refuses an owner whose files sit in both
places.

Backups carry the owner's identity under the incoming names and every other
account's home under its own reference (MU-40). Every backup carries
`community_identities`, `community_connections`, and `audit_events`.
The authentication JSONL audit log keeps its format; MU-10 copies none of its
lines into `audit_events` (see "Audit events").

## Community identities per account

MU-23 binds every community workflow to one account's identity.

- `repository_community.user_identity_home(db, data_dir, user_id, *, create=False)`
  returns that account's `IdentityHome`, or None before the account enrolls.
  `create=True` reserves a `community_identities` row with a random reference
  (`ensure_identity_ref`; a concurrent reservation reads the winner's row). Every
  resolution first moves incoming files into the owner's home only
  (`adopt_owner_identity`), because upgrades and restores carry only the owner's
  identity. A session that held no transaction is left without one.
- `CommunityClient(db, home, transport)` acts on that one home. Its enrollment
  secret, rename intent, rotation candidate, and recovery journal live in that
  home's `identity.json`, and `client.connection()` reads the row keyed by
  `(home.credential_ref, service_url)`. Each home has its own `rotation.lock`;
  one `identity.lock` still serializes file changes across homes.
- `enqueue_operation`, `pending_after_restore`, `queue_import_receipt`,
  `import_verified_download`, and `download_and_import` take `credential_ref`,
  the identity that queued the work. An operation's `owner_id` is that identity's
  account. MU-29 records the initiating account in `actor_id`; an explicit admin
  management action does not change an existing operation's original actor.
- New community activities use the caller's identity. Publication management and
  explicitly targeted receipt management use the original contributor's identity
  after current account, source, and media authorization. Local download status
  does not need a connected community identity.
- `community_identity_api` serves each identity operation twice: on the caller
  at `/api/community/...` and on a named account at
  `/api/users/{user_id}/community/...` (connection, connection/status, profile,
  enrollment start/complete/cancel, disconnect, rename and its cancel, rotate,
  rotation-status, rotation-retry, recovery status/start/complete/check/retry).
  Each decides an own/other pair (`community.identity.view`, `.enroll`,
  `.profile`, `.keys`, `.disconnect`), so a user acts only on itself, an admin
  on itself and ordinary users (`protected_account` for the owner and other
  admins), and the owner on anyone. A user naming another account gets
  `not_found`.
- A read for an account without an identity answers without creating one:
  status has `identity: null` and `connected: false`; recovery status has
  `identity: null`. Only enrollment start reserves an identity; other mutations
  answer 409 `not_connected`, except idempotent disconnect, which returns
  disconnected status without creating an identity.
- Mutations record `community.identity.change` (category `community`, target
  account required, no media or resource) with `change` one of
  `enrollment_start`, `enrollment_complete`, `enrollment_cancel`, `disconnect`,
  `profile_rename`, `profile_rename_cancel`, `key_rotation`,
  `key_rotation_retry`, `key_recovery_start`, `key_recovery_complete`,
  `key_recovery_retry`. An access refusal is `denied` with its access code; a
  service or identity failure is `failed` with `service_rejected`,
  `service_unavailable`, or `identity_unavailable`. Reads, status probes, and an
  unconfirmed request record nothing. No display name, secret, or key reaches a
  row or a response.
- `DELETE /api/community/connection` disconnects the caller. `DELETE
  /api/users/{user_id}/community/connection` disconnects a named account through
  the own/other disconnect pair and normal account protection. Sessions and API
  keys are allowed by the current matrix; neither action requires fresh auth.
  Hidden and absent targets have identical responses. Success records the actor,
  target, and `change: disconnect` without private material.
- Disconnect is local and idempotent. It clears the connection row's
  `contributor_id` while retaining `installation_id`, `key_id`, consent, and all
  queued operations. A row with no contributor and a retained key is detached.
  The private identity keeps its contributor, keys, and pending proofs unchanged.
  Identity projection and status reads never reconnect a detached row. The client
  and recovery guards refuse network workflows with `not_connected`; outboxes
  retain their pending intents. Nothing contacts the service to disconnect.
  Every network attempt, including transport retries, rereads detachment outside
  the workflow's transaction. Disconnect cannot undo a request already sent,
  but stops subsequent sends. Publication retries also recheck before changing
  an unsent operation to submitted.
- `POST /api/community/enrollment/start`, or its named equivalent, reconnects a
  detached enrolled identity locally with its retained contributor and consent.
  It returns connected status without an authorization URL or reenrollment.
  Pending outboxes can then retry their original operation IDs and saved bytes.
  A detached pending enrollment resumes its original confirmation intent instead.
  Backups retain every account's identity files and detached connection state.

## Community activities

MU-29 evaluates all 16 existing activity method/path pairs through
`community_activity_access`. No route inventory version, artifact
protocol, migration, or database column changes in MU-29.

- `ActivityAccess` binds an action to `SourceRef`, `ScanMedia` or a verified
  `ImportTarget(import_id, media_item_id, media_source_id)`. It reuses
  `ReviewAccess.writing` and `fresh_authority_write` to gather provider evidence
  outside the writer lock and recheck current authority inside it. It checks
  source ownership, review visibility and repairs, completed scan evidence,
  media scope, active contributor account and the exact connection tuple.
  Browser sessions must still belong to the caller, be unrevoked and unexpired.
  Transport attempts, including automatic retries, recheck those conditions.
- New review publications use the acting contributor, including an admin
  publishing another account's review. MU-24 effective review preparation,
  policy guards, source bytes and consent remain authoritative. Approval only
  queues the exact previewed bytes. Existing status, check, retry and local cancel
  use the persisted operation owner's contributor and original payload.
- Sharing context requires a source-kind publication grant or a publication-check
  grant before source lookup. Source and media visibility remain independently
  required. Authorized saved jobs are projected locally without requiring the
  viewer's enrollment. A disconnected contributor's unsent jobs appear paused
  with `not_connected`; their persisted intent and original contributor remain
  unchanged. This code represents live connection state, never a persisted
  publication outcome. Historical stored `not_connected` values are ignored;
  reconnect projects otherwise sendable jobs as pending without rewriting rows.
  Direct outbox status uses the same live contributor check. The connected
  owner's dialog does not treat a historical connection error as a hold.
  Authorized local cancellation does not bind a signing connection or contact
  the service. An unsent job becomes cancelled; submitted work retains its
  uncertain cancel-pending state, and published outcomes still win.
  Other authorized jobs remain manageable. A viewer's identity-file failure
  appears as `accessError: identity_<status>`, without discarding authorized
  source context or other contributors' jobs. Preview preparation, approval and
  sends still require the applicable contributor's live connection.
- Community settings share refusal handling across initial load, refresh and
  mutation reconciliation. A 403 or 404 clears the connection and dependent
  credential panels and refreshes authority. Transient failures may retain the
  local cached connection, explicitly unverified.
- Preview tokens bind the browser session, identity home, service and source.
  Hidden and absent tokens return identical 404 `not_found` responses. A visible
  changed source returns 409 `preview_stale`. A caller without the route capability
  receives 403 `capability_denied` before resource resolution. Hidden and absent
  publications, imports and vote intents also return identical 404 responses.
  Publication management checks own/other authority before source-level findings
  access. Missing local media targets, confirmed provider absence and hidden
  targets return identical 404 responses for nonowners on lookup,
  `POST /api/community/downloads`, and download status. Lookup authorizes media
  before returning version or metadata conflicts.
  Genuine provider outages remain retryable 503. Malformed target conflicts retain
  their existing errors; the owner's missing-target diagnostic remains 409.
- `POST /api/community/import-receipts/recover` and
  `POST /api/community/import-receipts/{import_id}/retry` accept optional positive
  `user_id` query parameters. Omission means self. Another account requires the
  other-receipt capability and current account/media authority. A receipt ID alone
  never grants access. Recovery stages only the target contributor's original
  receipt and does not send it. A hidden import is skipped with its transaction
  rolled back; recovery pages past skipped candidates and applies the batch limit
  to authorized imports, returning 200 with only staged in-scope import IDs.
  An explicit retry of a hidden import still returns 404.
- Shared imported artifact evidence still deduplicates by service and revision.
  Each additional contributor's exact signed receipt is retained in a separate
  existing `CommunityOperationRow.receipt`. The shared import's original receipt
  is never overwritten. Votes require that contributor's acknowledged receipt.
  Receipt and vote media authorization uses any accessible verified import target,
  not necessarily the first importer's canonical media. The verified association,
  completed source, media scope and findings capability are rechecked under the
  write lock. A generic scan/media association alone does not grant this authority.
  Foreign pending vote intents are not returned or retried. Local download status
  reports only the caller's receipt state and whether their own review uses it.
  Receipt and vote acknowledgments record validated remote success even if local
  authority changes during the send. Authorization gates staging, submission and
  each transport attempt, not recording an already committed remote result.
- Publication approval, retry submission and local cancel stage secret-free
  `community.publication.publish`, `.retry` and `.retract` events with media,
  resource, actor and target. Review publication target is the review owner;
  management target is the original contributor's account. `details.kind` is
  `own_review`, `other_review`, or `scan_evidence`. Historical review kinds select
  the audit viewer's current own/other publication capability by target account.
  Local cancellation cannot withdraw an already published artifact and emits no
  successful retraction event for an acknowledged operation. Unsent local cancels
  remain audited. Local account deletion must not contact the service or retract
  remote publications.

Deterministic evidence is in `tests/test_community_activity_access.py`,
`tests/test_community_activity_http.py`, `tests/test_community_activity_feedback.py`
and `tests/test_community_activity_targets.py`, plus the receipt, vote, lookup,
import and publication regression suites. The
real-app recipe is [community activities](../.github/skills/verify-editarr/features/community-activities.md).

## Audit events

MU-10 writes `audit_events` through `scanner/security/audit/`. Import from the
defining module.

- `actions.AuditAction` names every stored action. `actions.ACTION_SPECS` maps
  each one to its category, the actor kinds each outcome allows, whether a
  target user, media key, and resource are required, allowed, or absent, and
  the detail keys it may carry. A string detail must be one of the listed
  codes, so no free text, typed name, or secret reaches a row.
  `actions.check_event` applies the registry and raises `AuditEventRejected`.
- `store.stage_event(db, event, *, actor_kind, request_id, event_id)` checks an
  `AuditEvent` against the registry and checks that its actor, target, and media
  key exist in `users` and `media_items`, because runtime SQLite does not
  enforce foreign keys. It adds the row to the caller's transaction, so a change
  and its event commit together or not at all. Reusing an `event_id` inserts
  nothing, so a retried job records once.
- `store.record_event` stores a refusal or failure in its own transaction. A
  storage failure is logged without payload and returns false; the refusal the
  caller returns does not change.
- An event at or before `now - AUDIT_RETENTION` has expired. Each write deletes
  expired rows, measured from its own event time, and startup runs
  `store.run_retention`. `store.retention_cutoff(now)` gives MU-28 the same
  boundary for reads.
- `authentication` writes the security actions: `auth.setup`, `auth.sign_in`,
  `account.credentials.change`, `account.credentials.recover`,
  `sessions.revoke`, `api_key.issue`, `api_key.revoke`, and `api_key.reveal`
  (MU-13). JF-06 stops writing `account.credentials.change` and
  `account.credentials.recover`. Their stored rows still read. Key events name the acting account and the holder as target. A
  reveal is staged and committed before the key is returned. A refused key
  action stores its access code; a wrong confirmation password is `failed`. A refused sign-in
  has an `anonymous` actor and names the matched account as its target, never
  the typed name. The database stores the first refused attempt of each
  lockout, not every 429, so unauthenticated requests cannot grow the table
  without bound. A Jellyfin sign-in also records its `method`, `password` or
  `quick_connect`; a refused Quick Connect start is a `throttled` sign-in with
  `method: quick_connect`.
- Startup revokes sessions bound to a replaced CSRF key and stages their system
  `sessions.revoke` event in one transaction. If that commit fails, startup
  fails and the sessions stay unrevoked; the next startup retries both.
- `filters.policy.change` (MU-14) records each enforcement change with the owner as
  actor and the account as target, its cause, the new policy revision, and the
  enforced rule and severity counts. A refusal stores its access code; a wrong
  password is `failed`. An instance edit that moves a user's policy revision
  records one event per such user with cause `instance_defaults` and the new
  policy revision, without counts.
- `export.publish`, `export.regenerate`, `export.delete`, `export.retain` (MU-36),
  `community.publication.publish`, `community.publication.retry`, and
  `community.publication.retract` are registered for MU-31 and its successors.
  Each requires a media key and a resource. A personal export names its account
  as target; the item default names none.
- MU-17 adds category `settings`: `settings.connection.change` (`service`,
  `change` saved or disconnected), `settings.path_mapping.change` (`change`,
  optional mapping ID as resource), `settings.deployment.change` (which fields
  were submitted), `settings.logging.change` (`debug_enabled`),
  `system.restart`, `backup.create` (`kind`, `encrypted`), `backup.download`
  (`kind`), `backup.restore` (`stage` start, upload, preflight, or activate),
  `backup.delete`, and `backup.schedule.change` (`change`). Backup events name
  the job ID as resource. Each refusal stores its access code as `reason`.
  Connection and mapping changes stage their event in the change's
  transaction; file, schedule, restart, and backup job changes record it after
  the change. A successful restore activation stores no event, because the
  restored database replaces the one that would hold it (MU-40).
- MU-15 adds `account.create` (`method` managed or registration; a
  registration has an `anonymous` actor and names the new account; JF-06 stops
  writing both methods),
  `account.update` (`change` profile, username, profile_and_username,
  avatar_set, or avatar_removed; `sessions_revoked`; JF-06 stops writing the
  username changes, which stay readable), `account.status`,
  `account.password.reset`, `permissions.change` (`change` role, overrides, or
  role_defaults), `media_access.change` (`change` default_scope or user_scope;
  history from before JF-02 may also hold exception_granted or
  exception_revoked; no media key), and `settings.registration.change` (`mode`).
  JF-06 stops writing `account.password.reset` and `settings.registration.change`.
  Their stored rows still read. Users may now
  be the actor of `sessions.revoke`. Each change stages its event in its own
  transaction; each refusal of a mutation stores its access code.
- MU-18 adds `account.ownership.transfer` (category `security`, user actor,
  target the successor, `successor_role` admin or user). A refusal stores its
  access code, `successor_disabled`, or `successor_credentials_required` as
  `reason`; a wrong password is `failed`.

- MU-38 adds `export.write_committed` (category `export`, the owner as user actor,
  media key and `export:<id>` resource, `format`, `target_kind`, and a `failed`
  `reason` of `reauthentication_failed`, `capability_denied`, `write_failed`, or
  one of `audit.actions.EXPORT_WRITE_REFUSALS`), visible as `export.publish` is.
  A refusal for an ID with no publication (`not_found`, or
  `reauthentication_failed` for a wrong password) carries only the
  `export:<id>` resource and `reason`: no media key, target, format, or path, so
  only the owner sees it.
- MU-38 adds `account.delete` (category `security`, user actor, target the
  deleted account) with `mode` (`retain`, `transfer`, `delete`),
  `destination_id`, `reviews`, `files_retained`, `files_removed`, `files_moved`,
  `policy_files`, `scans_deleted`, `scans_kept`, `sessions_revoked`,
  `key_revoked`, and `identity_released`. A refusal stores its access code or one
  of `audit.actions.ACCOUNT_DELETE_REASONS`; a wrong password is `failed`.
  Visibility is `accounts.delete.other`.

`audit_events.actor_id` and `target_user_id` reference `users` without cascade.
MU-15 and MU-38 must not delete a `users` row that an unexpired event names;
account deletion keeps the row as a tombstone.

The authentication log `auth-audit.jsonl` stays the request-level record in the
log directory, schema version 1. HTTP setup, sign-in, and sign-out write one
record per outcome with the request ID, client address, method, route, and
status. Since JF-06 every sign-in is a Jellyfin sign-in: a Jellyfin password
sign-in (`/api/auth/jellyfin/login`) and a Quick Connect sign-in
(`/api/auth/jellyfin/quick-connect/complete`) write `auth.login` with outcome
`success` (actor `administrator` or `user`, the account ID, and the session ID),
`failure` (`invalid_credentials`), or `denied` (`account_disabled`,
`jellyfin_user_blocked`, or `jellyfin_unavailable`, with the status the
response carries). Each throttled password sign-in or Quick Connect start writes
`auth.login_throttled`. The record never names the Jellyfin user. MU-10 writes
no `unattributed` row.

## Technical decisions

| Decision | Rationale |
| --- | --- |
| Python tables are the source of truth for capabilities; a JSON manifest for the export layout | The evaluator imports typed names. The plugin cannot import Python, so it reads JSON. |
| Defaults are checked against the Markdown matrix, not restated | A test that restates constants cannot catch drift from the approved matrix. |
| `UserId` is an integer that is never reused | It keeps the migrated administrator's ID. Retained files of a deleted user must never match a new account. |
| Fresh authentication is a password proof in the same request | It avoids inventing a time window. Since JF-06 the password is the account's Jellyfin password. |
| Creating an API key counts as rotation | Creation reveals the key, and reveal and rotation require fresh authentication. |
| Connection testing is part of the owner-only connection power | The matrix groups test with edit and disconnect, and a test submits connection settings. |
| `PUT /api/auth/security` maps to `deployment.edit` | It set the local-address bypass. MU-07 removed the bypass and the route. |
| Symbolic links at an export path are invalid, not followed | The plan forbids unexpected filesystem redirection. |
| Reauthentication failures return 403 | A 401 would sign the client out. |
| Media-access edits need fresh interactive authentication | They change what an account may reach, so they are the matrix's "permission changes". |
| Enforcing filter policy needs fresh interactive authentication | MU-14 names it a permission-changing mutation behind the same guard. |
| `api_keys.issue.other` is owner-only under the reveal power | Creating or rotating another user's key reveals it, and revealing others' keys is an owner power. |
| Key status reads use `profile.view.own` | Status shows no secret, so it should not need the fresh reveal capability. |
| Stored keys are wrapped with a dedicated key file, not the CSRF key | Operators may replace the CSRF key, which revokes sessions; that must not make every API key unrevealable. |
| Legacy plaintext is sealed by the application, not the migration | A migration sees only its connection, and a restore migrates a staged copy whose data directory is not the live one. |
| API keys are refused on interactive-only routes by the middleware | A route that only fresh actions can authorize must refuse keys before any handler, including handlers that later tasks migrate. |
| `filters.preferences.edit.other` is owner-default and delegable | No matrix row or owner power covers it, so the general rule that other powers are delegable applies. |
| A user chooses their own filter mode only while nothing is enforced on them | The matrix row reads "when no policy is enforced"; an admin may still assign the mode, and enforcement applies in both modes. |
| A new copy is taken whenever a user enters copy mode | The copy starts from what the user saw live, so personal edits carry over; an earlier copy from a previous copy period is replaced. |
| Deleting an instance rule deletes the live overrides and enforcement that name it | An override edits or switches off an inherited rule; with the rule gone there is nothing left to inherit. |
| Policy revisions count enforced-view changes only | Exports and reviews depend on the enforced view; personal defaults only affect new fills and Apply. |
| The review view never reads non-enforced personal defaults | Coordinator ruling for MU-14 fix round 3: the view must change only with saved content or `policy_revision`, as export never applies defaults; Apply reads saved content instead of the view. |
| Personal rule IDs never equal instance rule IDs | Both join one effective list, which requires unique IDs; both writers refuse a clash. |
| Plugin EDL rules decide selection validity | The plugin decides fallback at playback, and Editarr's import parser accepts files the plugin rejects. |

## Confirmed product decisions

The user settled these during MU-01. The decision log and matrix hold the
authoritative wording.

1. Admins may link or relink their own account and ordinary users' accounts,
   with ownership proof and fresh authentication. `accounts.link.own` defaults
   to admins; `accounts.link.other` stays limited to ordinary targets by
   target-account protection.
2. Changing registration mode was a nondelegable owner power until JF-06
   removed registration. JF-02 removed granting unmatched-media exceptions
   with Other Files, and JF-05 removed changing Jellyfin first-login admission
   mode.
3. Item-default EDL and MCF publication applies the current review owner's
   enforced policy. Shared fallback stays outside the personal-export
   regeneration guarantee.
