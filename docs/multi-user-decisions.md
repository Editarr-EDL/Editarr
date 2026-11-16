# Multi-user decision log

Confirmed interview answers for the [implementation plan](multi-user-plan.md).
Updated 2026-10-18. The product interview is complete; the plan and permission
matrix consolidate these confirmed decisions for implementation.

## Confirmed requirements and decisions

- Support concurrent users with independent authenticated sessions.
- Keep ordinary session limits at 30 minutes idle and 8 hours absolute, and
  remember-me limits at 7 days idle and 30 days absolute. Allow simultaneous
  logins on multiple devices. A local password change or reset revokes all of
  that user's existing sessions but does not revoke their API key.
- Support local accounts and accounts linked to the configured Jellyfin server.
  Amended 2026-11-03 (JF-06): every account comes from Jellyfin. Local accounts
  and local passwords are removed.
- Local accounts sign in with username and password; email is optional.
  Amended 2026-11-03 (JF-06): sign-in takes a Jellyfin password or Quick Connect
  only. Email stays optional.
- Make local registration configurable, defaulting to admin/owner-created
  accounts only. Support open self-registration as the optional mode; only the
  owner may change registration settings. An approval-based registration mode
  is not included in the selected scope. Amended 2026-11-03 (JF-06): registration
  and its setting are removed.
- Admins/owner set temporary passwords for managed local-account creation and
  recovery, requiring a password change at the next login. No setup/reset email
  delivery. Admin management remains limited to ordinary users by the previously
  selected defaults; owner recovery uses the offline approach recorded below.
  While a password change is required, restrict browser sign-in to changing the
  password, but keep an existing API key usable with its normal permissions.
  Amended 2026-11-03 (JF-06): local account creation, temporary passwords, and
  password resets are removed.
- Support linking an existing local account to Jellyfin and a local password on
  a linked account. Only admins/owner may initiate linking, with proof of account
  ownership. Admins may link or relink their own accounts and ordinary users'
  accounts, but not another admin or the owner. Fresh authentication and
  ownership proof apply to self-linking too. Amended 2026-11-03 (JF-06): no
  account has a local password. The upgrade disables every account without a
  Jellyfin link (cause `unlinked`) and keeps its data. Linking such an account
  to a Jellyfin user enables it again. The caller's own account is proven by
  its fresh Jellyfin password. Another account is linked on the admin's
  attestation and the Jellyfin user's sign-in.
- If separate local and imported Jellyfin accounts already exist for one person,
  block linking until one account is explicitly removed or its data transferred.
  No automatic account merge feature; matching names do not prove ownership.
- Do not support unlinking a Jellyfin account into a local-only account. Delete
  the linked account and create a new account if that conversion is needed, using
  the reviewed retention/transfer/deletion workflow.
- On Jellyfin disconnection or replacement, keep linked identities/data attached
  to the old server, suspend their media access until explicitly relinked, and
  retain local profile access. The owner retains its separately confirmed full
  access. Relinking to a replacement server is distinct from unlink-to-local
  conversion and must not automatically reassign identity by matching names.
- On linking/relinking that changes the export ID, show an impact preview and
  regenerate personal exports under the new ID. Remove old active files only
  after successful replacement; failures must not discard the old exports.
- First-release profile fields/features include username, display name, email,
  custom avatar, timezone, display-language UI/infrastructure, local password
  change, and account creation/last-login timestamps. JF-06 removes the local
  password change. The user did not include an
  own active-session list/revocation interface in the selected first-release set.
  Local users set custom avatars by uploading image files; image-URL input is not
  included. Linked username, name, and avatar are always synced and read-only
  (the JF-05 amendment of 2026-11-03 adds the username). Amended 2026-11-04
  (JF-06): nobody edits a local username, and migration 0030 moves an unlinked
  account's username to its display name. After reviewing
  Jellyfin API limitations, the user chose independent,
  editable Editarr email/timezone preferences for linked accounts. This supersedes
  the earlier request to sync those unavailable fields from Jellyfin.
- Add the UI and plumbing for additional display languages but do not perform
  translations/localization yet. This narrows the earlier localization scope;
  the interface must not imply untranslated languages are already supported.
- No notifications in this release. This supersedes the earlier inclusion of
  personal notification preferences and delivery setup. Authentication-email
  setup/reset delivery is also excluded under the subsequent password decision.
- Exactly one designated `owner` has full access. `admin` and `user` have
  progressively fewer default permissions; the defaults below and in the
  [permission matrix](multi-user-permissions.md) are confirmed.
- Role defaults are configurable, with individual allow/deny overrides. Existing
  users inherit changes to role defaults wherever they have no override.
- Permanently reserve all eight interviewed powers for the owner, regardless of
  role defaults or overrides: ownership transfer; managing the owner account;
  editing role defaults; granting/revoking admin roles or individual permission
  overrides; revealing others' API keys; connection/deployment settings changes;
  backup download/restore; and enforced filter-policy management. Preserve the
  separately approved admin read-only connection-status permission. No override
  may confer these protected owner powers on an admin or ordinary user.
- Clarified during MU-01 implementation on 2026-10-18. Registration mode,
  Jellyfin first-login admission mode, and unmatched-media exceptions are also
  strictly owner-only. These powers cannot be delegated through role defaults
  or individual overrides. The amendment of 2026-11-03 removes unmatched-media
  exceptions, JF-05 removes the admission mode, and JF-06 removes the
  registration mode. Registration and admission history stays readable through
  `connections.edit`, so only the owner reads it.
- Hide unauthorized functionality, including navigation and controls. Backend
  authorization must also reject unauthorized direct requests and object access.
- Add Settings → Users, listing users and supporting user editing.
- Include all interviewed Users-list columns: avatar/name, username, local versus
  Jellyfin source, role, active/disabled status, last login, creation date, and
  permission/media/filter-policy override indicators.
- Include bulk import of selected/all Jellyfin users, enable/disable, deletion
  with impact review, role changes, media-access assignment, and filter-mode
  changes. Each operation retains its individual permission restrictions and
  target-account protections; bulk role changes remain owner-only.
- Admins may list users, create local users, import Jellyfin users, edit ordinary
  users' profiles, reset their passwords, revoke their sessions/API keys, and
  disable/delete ordinary users by default. Admins may not change ordinary
  users' permission overrides, promote users to admin, manage other admins, or
  edit role defaults by default. JF-06 removes creating local users and
  resetting passwords.
- Ordinary users see only their own profile, not the user directory.
- On user deletion, let the authorized deleting admin/owner choose retention,
  transfer, or permanent deletion after reviewing the impact on reviews, exports,
  scans, and community history. This is distinct from disabling an account,
  which retains its data. The impact workflow must validate transfer destinations
  and preserve shared dependencies required by surviving reviews.
  A transfer destination is any active, undeleted account other than the target
  that the deleting admin/owner can see. If the destination cannot access the
  media of any transferred review, the transfer is blocked with a count; there is
  no partial transfer. Transferred personal EDL/MCF files move to the destination
  account's export ID, and the destination becomes their policy owner
  (2026-10-28, wave13-mu38-transfer).
- When a deleted user's data is retained, retain their personal exports as-is,
  including availability to the Jellyfin plugin. Deleting an Editarr user leaves
  their remote community publications in place; local deletion does not imply
  remote retraction.
- Local accounts default to `user`; Jellyfin administrators start as Editarr
  `admin` when their account is created, at first sign-in or import. Amended
  2026-11-03 (JF-05): Jellyfin administrator status sets only that starting
  role. The owner can change it, and no later sign-in or sync overrides it.
- Refresh linked users' Jellyfin status, name, avatar, and media restrictions at sign-in,
  every five minutes in the background, and on manual sync. Detected changes
  affect existing sessions and API access as well as new logins.
  Admins/owner may manually refresh sync. New Jellyfin users always sign in
  automatically (JF-05).
- Confirmed upstream disablement/deletion disables the entire linked Editarr
  account and revokes its sessions/API access. Retain its data until an authorized
  admin/owner resolves the account. Do not treat an outage as confirmed deletion.
- Decided 2026-10-22. When Jellyfin re-enables a user it had disabled, the next
  sync or sign-in re-enables the linked Editarr account automatically. This applies
  only to a disable that came from Jellyfin; an account an Editarr admin or the
  owner disabled stays disabled until they re-enable it. A deleted Jellyfin user
  never returns under the same identity. Revoked sessions stay revoked.
- Decided 2026-10-22. Cached Jellyfin evidence for the outage grace lives in
  memory. An Editarr restart during an outage ends media access early (profile
  access remains) until Jellyfin answers again.
- During a Jellyfin outage, permit access using last-verified roles/media
  restrictions for 24 hours from the last successful verification. After expiry,
  allow profile/account access but block media operations. New/unverified
  identities cannot be treated as having previously verified access. The owner
  is exempt under the protection below.
- The sole owner must retain local credentials. Jellyfin cannot demote or disable
  the owner, who retains full Editarr access, including during Jellyfin outages.
  Amended 2026-11-03 (JF-06): the owner signs in with Jellyfin and needs a
  Jellyfin link. The upgrade disables an owner without one, and nobody can sign
  in as that owner. Jellyfin sync still never demotes or disables the owner.
- Support ownership transfer to an existing account, requiring current-owner
  reauthentication and local credentials on the successor. The former owner
  becomes admin. Exactly one owner must remain throughout the transition.
  Amended 2026-11-03 (JF-06): the successor must be active and hold a Jellyfin
  link. The owner reauthenticates with its Jellyfin password.
- Extend the existing offline `editarr admin reset-password` recovery approach
  to the sole owner, requiring local host access and the API to be stopped.
  No additional recovery-code system is selected for this release. Amended
  2026-11-03 (JF-06): `editarr admin reset-password` and `admin rename` are
  removed, and Editarr has no lockout recovery. An owner whom 0030 disabled for
  having no Jellyfin link reinstalls, or recovers the pre-upgrade snapshot with
  `editarr migration recover-export` and links it with the previous release.
- Honor linked users' Jellyfin library and parental restrictions across every
  source, including manual-file access. Block unmatched media
  by default, but permit the owner to grant explicit per-user exceptions for
  media that cannot be reliably matched to an authorized Jellyfin item. This does
  not grant admins exception authority or change the rule for media known to be
  restricted in Jellyfin.
- Decided 2026-10-22. Owner exceptions apply only to files outside every folder a
  Jellyfin library covers (after path mapping). A file inside a Jellyfin library
  folder must match a Jellyfin item the user may see; exceptions never apply
  there. If the linked server's library folders cannot be read, deny. The
  amendment of 2026-11-03 removes owner exceptions and manual-file access.
- Local users inherit a configurable default set of libraries or sources,
  initially all configured sources. Admin authority to manage these defaults
  and individual media access is enabled by default and must be configurable.
  Default changes apply live to existing local users wherever they have no
  individual media-access override, matching role-permission inheritance.
- Support explicit Jellyfin import and account creation at first valid
  Jellyfin login. Amended 2026-11-03 (JF-05): every first valid Jellyfin sign-in,
  by password or Quick Connect, creates the account; import-only admission is
  removed.
- Require an identified individual login. Remove both existing authentication
  bypasses: local-address bypass and external-authentication bypass.
- Migrate the existing account to owner, retaining its current saved reviews and
  API key. Also assign existing scans, queued jobs, imports, export records, and
  the current community identity/history to that owner. Preserve conventional
  shared `Movie.edl`/`Movie.mcf` files until explicitly replaced or removed.
- Revoke all existing browser sessions on the multi-user upgrade and on
  backup restores, requiring sign-in again. Retain API keys from the migrated or
  restored data, subject to the restored users' permissions and account state.
- If an older database contains activity but no administrator, preserve the data,
  require local owner setup before normal access or workers resume, and assign
  existing activity to that owner during setup. Never discard data to bootstrap.
- Each user can have an API key and create, save, and export their own reviews.
- Each user has at most one active personal API key. It always follows that
  user's current permissions; multiple named keys and narrower per-key scopes
  are not part of the selected design. Keys never expire unless rotated/revoked,
  remain revealable/copyable after creation, and are not revoked by password
  changes. The key's user and the instance owner may reveal/copy it; admins may
  revoke ordinary users' keys but may not reveal others' keys. JF-06 keeps API
  keys unchanged.
- Security-sensitive actions require an interactive session and fresh
  authentication, even when the caller holds an otherwise authorized API key.
  This covers password resets, key reveal/rotation, permission changes, account
  linking, account deletion in every mode, and backup restoration, as well as
  ownership transfer. API keys cannot
  perform these actions; ordinary API operations still follow current permissions.
  Amended 2026-11-03 (JF-06): fresh authentication is the account's Jellyfin
  password, and password resets no longer exist.
- Admins have full access to other users' saved reviews by default: list, view,
  copy, edit, delete, and export. Review permissions do not remove the confirmed
  media-access restrictions.
- Saved reviews are private to their creator, admins, and owner. No explicit
  sharing between ordinary users in the first release.
- When deleting a saved review, let the user choose retention or removal of
  already-published files, subject to export permissions. Retained files keep
  their provenance; this is separate from the account-deletion workflow.
- Copying a review to another user or transferring it changes its policy owner
  to the new review owner. Apply that new owner's enforced policy; retain original
  authorship in provenance/audit history. An export alone does not change policy
  ownership, even when an admin chooses a different export target.
- Users and admins have all interviewed personal-review actions by default:
  create/save/rename/duplicate/delete; attach/remove scan evidence; add/edit/delete
  filters, selections, classifications, severity, actions, channels, and comments;
  edit review/MCF metadata; configure synchronization and retime; run credits
  detection and accept/edit/remove credits; play audio/mute/video previews; and
  import EDL/MCF by upload. Media-access restrictions still apply.
- Server-path EDL/MCF imports are admin/owner only by default. This supersedes the
  earlier ordinary-user allowance; ordinary users may upload files instead.
- Completed scans and findings are shared evidence, reusable by every user with
  access to the underlying media. Saved-review privacy is independent of this.
- No per-user quotas in the first release for scan submissions, queued jobs,
  model downloads, saved reviews, or exports.
- Users may start audio/video/combined scans, select audio tracks, choose models
  and advanced options, rerun scans, and view/edit manual media identity and
  source associations. Users may not scan manually entered server-file paths or
  see underlying filesystem paths by default. Admins may perform all of these
  actions. Model acquisition defaults are recorded below; per-user quotas are
  excluded from the first release.
- Users may cancel their own queued jobs, resume their own interrupted jobs, and
  rename/delete their own completed scans. Admins may do these for any user,
  view others' queued jobs/progress/errors, and reorder the global queue.
  Ordinary users do not have those cross-user queue/history management powers.
  Visibility remains media-scoped. Block scan deletion until saved-review
  references are removed, and show only references the requester may access.
  Hidden references may block deletion without exposing private review details.
- Every export must identify the user to whom it belongs. Personal EDL exports
  use `edl/<user-id>/Movie.user-<user-id>.edl` beside each media file. Personal
  MCF exports use the parallel `mcf/<user-id>/Movie.user-<user-id>.mcf` layout.
  For `/media/Movies/Movie/Movie.mkv`, the EDL is therefore
  `/media/Movies/Movie/edl/<user-id>/Movie.user-<user-id>.edl`.
- Export IDs are Jellyfin user IDs for linked accounts and distinct Editarr IDs
  for local-only accounts. The plan requires namespaced immutable local IDs,
  canonical cross-repository encoding, and collision rejection; account-link
  transitions use the reviewed regeneration flow recorded above.
- When admins or owner export another user's review, let them choose the review
  creator or themselves as the export target. The selected user's ID determines
  the destination; review creator, target user, and exporting actor must remain
  distinguishable in the design.
- Keep one current personal export per user/media version/format. Exporting a
  different saved review replaces that user's current export after confirmation
  while preserving all saved reviews. Use destination locking and reject stale
  confirmations as described in the plan.
- Include coordinated work in Editarr and the Jellyfin EDL plugin for per-user
  EDL and MCF serving/selection. MCF consumer support is explicitly in scope.
- New default EDL exports use `edl/Movie.edl` beside the media file (for example,
  `/media/Movies/Movie/edl/Movie.edl`). The plugin checks the authenticated user's
  personal EDL first and serves the default if no personal EDL exists. Owners and
  admins with permission may export defaults. This replaces the old location for
  newly published default EDLs; legacy files remain preserved during migration.
- The existence of the default file is the fallback toggle; do not add a separate
  fallback configuration switch. Authorized admins and owner enable fallback for
  an item by publishing its default, or remove it by deleting that default.
  This supersedes the earlier enabled-by-default configuration-switch decision.
- MCF mirrors EDL selection and publishing: personal
  `mcf/<user-id>/Movie.user-<user-id>.mcf` first, then default `mcf/Movie.mcf`.
  Authorized admins and owner may publish/delete default MCFs as well as EDLs.
- Preserve legacy `Movie.edl`/`Movie.mcf` beside media on disk, but the updated
  plugin must not use them as fallback. Require an explicit move or re-export
  into the new directories before they can be consumed by the updated plugin.
- Shared fallback remains allowed even when a personal EDL was withdrawn
  because enforced policy could not be satisfied. The enforced-policy guarantee
  applies only to personal exports. If a personal file exists but is invalid or
  unreadable, report that error rather than serving the default. Fallback applies
  only when the personal file is absent. Coordinated deployment follows the plan.
- Decided 2026-10-21 for the plugin. Personal and default EDL/MCF are served as
  exact file bytes. A request authenticated only by a server API key may read the
  item's default file but never a personal file. Only the final export file is
  checked for links; linked `edl/` or `edl/<user-id>/` folders are allowed.
  On Windows the plugin keeps check-then-open and documents the link-swap race,
  which needs write access inside the library folder. The coordinator applied the
  same documented limitation to every other platform outside the plugin's
  no-follow table (Linux Arm32, ppc64le, s390x, FreeBSD); the user may revisit it.
- Decided 2026-10-21 for Jellyfin user verification. It honors the same proxy and
  certificate environment settings as the catalog client.
- Users may download their own EDL/MCF in the browser, publish their own exports
  beside media, and delete their own published exports. Admins may additionally
  delete others' published exports and publish/replace/delete shared fallback
  EDL/MCF files in the new layout. All of these are default permissions, subject to the agreed
  role defaults, overrides, media restrictions, and replacement confirmation.
- Filter settings support configurable modes: live inheritance from instance
  defaults with personal overrides (the default), or an independent personal
  copy of instance defaults. Users may also be assigned enforced defaults that
  they cannot override. Users may choose their own mode when no policy is enforced.
- Decided 2026-11-06. Every account has its own mode; the instance mode setting
  only sets the mode new accounts start in. Upgrading gave each account that
  followed the setting its current value.
- Clarified at the development pause. MU-03 stores independent copy rules and
  severities plus live per-user severity overrides.
- Decided 2026-10-21 for live mode. Owner-enforced rules always apply and cannot
  be switched off. For each non-enforced inherited rule, a live-mode user may
  switch it off, edit its fields in place, or reset the override so the rule
  tracks the owner again. An in-place edit stops later owner edits to that rule
  from reaching the user. The user may also add personal rules. New owner rules
  still flow in automatically. Deny still wins over Allow regardless of position,
  so order is display only: inherited rules in instance order, then personal
  rules in the user's order. MU-14 adds the ordered migration for this storage.
- Decided 2026-10-22. Enforcement sets a floor, not a ceiling. Deny still wins over
  Allow when an enforced rule meets a personal rule, so a user may filter more
  than the owner requires but never less. An enforced Deny always filters; an
  enforced Allow cannot stop a user's own Deny from filtering the same finding.
  Apply and the review view must give the same answer.
- Enforced filter policy operates at individual selection-rule and subcategory
  severity granularity. Enforce it in review editing, imports, and exports, not
  only Settings → Filters. Existing saved reviews are subject to enforced policy.
- Preserve saved review content underneath an enforced effective view; edits and
  exports cannot bypass the active policy. A policy change must not destructively
  rewrite the underlying saved review content.
- Automatically regenerate affected published EDL/MCF exports when enforced
  policy changes, withdrawing exports that cannot be regenerated safely.
  Keep old personal exports available while regeneration runs; withdraw old
  exports only when regeneration fails. Therefore an in-progress export may
  still reflect the previous policy until regeneration completes.
- Apply the current review owner's enforced policy regardless of export target,
  including exports made by admins/owner for themselves. Track this policy source
  separately from the destination user and exporting actor so affected exports
  can be located and regenerated. Shared fallback is expressly outside the
  personal-export policy guarantee; regeneration availability follows the rule
  above. Copy/transfer changes that policy owner as recorded earlier; exporting
  alone does not.
- Clarified during MU-01 implementation on 2026-10-18. Item-default EDL/MCF
  publication also applies the review owner's current enforced policy. Shared
  fallback remains outside the personal-export regeneration guarantee.
- Give each user their own community connection and contributor identity instead
  of using one shared instance contributor. The current identity/history migrates
  to the owner.
- Community defaults allow users to search/find filters, download/import scans,
  vote/submit feedback, publish their own reviews, publish shared scan evidence,
  check/retry/retract their own publications, and recover/retry their own import
  receipts. Admins/owner may recover/retry others' receipts within their existing
  account/media permissions.
  Admins may additionally publish another user's review and check/retry/retract
  others' publications. Media and review restrictions still apply.
- Users manage their own community enrollment, profile, disconnection, and key
  rotation/recovery. Admins may also manage ordinary users' community connections.
  This resolves the earlier instance-connection question in favor of personal
  self-service plus admin management of ordinary users.
- Decided 2026-10-23 for community disconnection. Disconnecting forgets only the
  local connection; it does not contact the community service or revoke anything
  remotely. The account's signing keys are kept (and stay in backups), so a later
  reconnect reuses the same contributor identity and history. Unsent operations
  are held and nothing is sent while disconnected; they resume on reconnect.
  Users disconnect their own connection; admins may disconnect an ordinary user's
  connection under the named-disconnect capability, never the owner's or another
  admin's unless the matrix grants it.
- New community publications made by an admin from another user's review use the
  acting admin's contributor identity. Existing publications remain attributed
  to their original contributor when managed by another authorized user.
- Retain audit history for security/admin changes, exports, and community
  publishing for 90 days. Owner and admins may view it, but admins may see only
  entries within their existing permissions/account/media scope. Do not include
  secrets. Comprehensive auditing of every mutation is not selected.
- Leave the separate community-service operator/moderation dashboard and its
  existing access system outside this multi-user plan.
- Interview the user about every functionality's default permission and all
  major product choices before finalizing implementation work.

### Confirmed settings permission defaults

The minimum role below includes that role and higher roles. These are defaults,
except for the permanently reserved owner powers listed above, which cannot be
delegated. Existing media and filesystem-path restrictions still apply.

| Action | Minimum role |
| --- | --- |
| View connection addresses/status | admin |
| Test/edit/disconnect Jellyfin | owner |
| View installed models and download status | admin |
| Download models, including scan/credits-triggered acquisition | user |
| Cancel model downloads | admin |
| Delete models | admin |
| View/test path mappings | admin |
| Create/edit/delete/reorder path mappings | owner |
| View deployment/network configuration | owner |
| Change deployment/network configuration | owner |
| View diagnostic logs | admin |
| Change logging settings | admin |
| Restart Editarr | admin |
| View About/version information | user |
| Edit instance filter selection rules and severity defaults | owner |
| Change default filter mode (live inheritance or independent copy) | admin |
| Choose an individual user's filter mode | admin |
| Enforce non-overridable filter defaults | owner |
| Create a full backup | admin |
| Download a full backup | owner |
| Configure scheduled backups | admin |
| View backup job/status details | admin |
| Restore a backup | onboarding of an empty installation only |
| Delete backup jobs or retained archives | owner |

The UI must support permitted user-triggered model acquisition without exposing
the admin-only model inventory or general download-status view. Users may view
model-download progress within their own scan/credits jobs.


## Amendment 2026-10-30: restore only into an empty installation

The user simplified backup restoration during wave 14 (ruling
wave14-fresh-install-restore in the task pack's decisions.tsv). These rules
replace earlier restore-into-an-existing-installation behavior.

- Restore is offered only on an empty installation: no accounts and no scans,
  reviews, or other work. An upgraded single-user installation with data but no
  account creates its owner instead.
- Onboarding offers either connecting a Jellyfin server with a Jellyfin
  administrator's sign-in, which makes that Jellyfin user the owner, or restoring
  from a backup. A configured installation has no restore or backup-inspection
  controls.
- The offline `editarr backup restore` command remains, and accepts only an empty
  data directory. To recover a broken installation, stop Editarr and start it with
  a new empty data directory, then restore.
- Only full backups exist. Settings-only backups are removed, and a saved
  settings-only schedule loads as a full schedule with a notice.
- A restore brings back everything, with no replacement choices, except that the
  empty installation keeps its own base URL and listen port. Afterwards Editarr
  shows a notice to review media paths and source connections, listing the
  backup's base URL and port so the user can adopt them deliberately.
- Restored Jellyfin user IDs are trusted as recorded. Normal synchronization rules
  apply afterwards.
- A backup captured during an account transfer or link relies on MU-38's
  fail-safe recovery, which never removes the only copy of a file. The restore
  report lists records that disagree with the disk. No account is blocked.

## Amendment 2026-10-31: upgrade sessions and queue numbering

The user made two rulings during wave 16 (wave16-upgrade-session-revocation and
wave16-sequential-ids in the task pack's decisions.tsv).

- Only the multi-user upgrade revokes browser sessions. That upgrade starts from
  the released schemas: an unversioned database through `0009_readable_api_key`.
  Development revisions `0010` through `0023` were never released. They still
  upgrade with their data preserved and recover from their snapshot, but their
  upgrade keeps existing sessions. Restores still revoke every session.
- Queue IDs are one global sequence and scan numbers count per media item, so
  gaps a user sees may reveal how many jobs other users created. This signal is
  accepted and needs no follow-up. Hidden jobs' content, owners, titles, paths,
  progress, errors, and queue positions stay hidden.

## Amendment 2026-11-03: remove Other Files

JF-02 in the [Jellyfin-only plan](jellyfin-only-plan.md) removes Other Files.
These rules replace the earlier unmatched-media exception decisions.

- Every media item comes from the configured Jellyfin server. Editarr no longer
  scans, reviews, imports into, or exports for a server path entered by hand.
- Unmatched media has no exception. A linked user reaches only Jellyfin items
  their verified scope allows. Granting exceptions is no longer an owner power,
  and the `media_access.exceptions.edit` and `scans.start.server_path`
  capabilities are gone.
- Migration `0026_archive_manual_files` keeps every former Other Files record and
  shows it read-only in Archive with the label Other Files and its original file
  name. Stored exception grants and Other Files scope sources are kept in the
  archive, not applied.
- Operator decision, November 3, 2026: EDL and MCF imports are browser uploads
  only. Path import is removed with its routes, its **Scanner path** control, and
  the `reviews.import.server_path` capability (capability contract 6). Stored
  role defaults and overrides that name it are skipped. An import target must
  name Jellyfin; a target naming another source is 400 `jellyfin_item_required`,
  and a malformed Jellyfin target is an ordinary 422.
