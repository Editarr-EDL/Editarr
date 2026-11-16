# Multi-user support implementation plan

Status: planning complete, 2026-10-18. Product decisions and permission defaults
come from the completed interview. No application code or database was changed.

> JF-06 later removed local accounts, local passwords, registration, temporary
> passwords, and offline owner recovery. Every account now comes from Jellyfin.
> See [Remove local password accounts (JF-06)](jellyfin-only-plan.md#remove-local-password-accounts-jf-06).

- [Default permission matrix](multi-user-permissions.md)
- [Confirmed decision log](multi-user-decisions.md)

## 1. Scope and invariants

Deliver concurrent local/Jellyfin accounts, independent sessions, one protected
owner, configurable admin/user defaults with individual overrides, private saved
reviews, personal API keys/community identities, and per-user EDL/MCF exports.
Coordinate changes with the adjacent `jellyfin-edl` repository.

Hide unauthorized navigation, pages, actions, and returned data. Enforce the same
permissions on the server, including direct requests, object IDs, background
work, previews, downloads, and API-key access. Hiding controls alone is insufficient.

Exactly one active owner retains full access and local credentials. Jellyfin
cannot disable/demote that owner. Nondelegable owner powers and action-specific
fresh authentication are defined in the permission matrix. All other effective
permissions combine live role defaults and explicit inherit/allow/deny overrides.

Keep existing public behavior except where this plan explicitly changes it.
There is no application implementation authorization in this planning task.

Out of scope: notifications or email delivery, actual translations, personal
session-management UI, per-user quotas, ordinary-user review sharing, automatic
account merging, unlink-to-local conversion, multiple/scoped API keys, and changes
to the separate community-service operator/moderation dashboard.

## 2. Current code and implementation boundaries

The singleton administrator/API key live in
`scanner/persistence/rows/db_auth.py`; sessions reference that administrator.
`scanner/security/auth_policy.py` authenticates requests without object/role
authorization and contains the two bypass modes that must be removed.
`scanner/persistence/rows/db_reviews.py` has no review owner.
`repository_review_exports.py` currently replaces one sidecar per media/version/
format. Preserve its recoverable publication behavior while changing its scope.

`web/src/settings/settings-view.tsx` has Connections, Filters, Model Storage,
and General. Add Users using focused feature components rather than extending
that large component with all account workflows.

In `jellyfin-edl`, `EdlController.cs` requires authentication, but
`EdlLookupService.cs` and `JellyfinLibraryItemResolver.cs` resolve by item alone.
They need authenticated-user authorization, selected-version resolution, the
new file-selection contract, and MCF support.

Keep backend implementation in `security`, `persistence`, `integrations`,
`http`, `settings`, `runtime`, and focused feature packages where needed.
Keep scanner facades thin. Use `web/src/auth`, `settings`, `review`, `scans`,
`catalog`, and `api`; genuinely shared guards/state belong in `shared`.
Split files before extending them beyond the repository's size guidance.

## 3. Persistence and safe upgrade

Design versioned migrations for these logical records; exact table/column names
are implementation details to settle against the schema at implementation time.

| Record | Required state |
| --- | --- |
| User | Immutable internal ID, role, status, local credentials/change-required state, profile, timestamps |
| Linked identity | Jellyfin server identity + user ID, sync status, last successful verification, explicit role override |
| Role defaults/user overrides | Named capabilities; explicit inherit/allow/deny; revision for stale UI/background work |
| Media access | Local live defaults/overrides, verified Jellyfin scope |
| Sessions/API key | User FK; per-user revocation, one active key, digest plus protected revealable secret |
| Filter preferences/policy | Live/copy mode, personal overrides, enforced rules/severities, policy revisions |
| Reviews/scans/jobs | Review owner/policy owner, scan/job initiator, historical authorship |
| Exports | Media/version/format, personal target or shared default, review/revision, policy owner/revision, actor, path/status |
| Community | Per-user credentials/profile, votes, receipts, publications and acting-user attribution |
| Audit | Actor/target/action/outcome, authorized scope, timestamp, 90-day retention |

Use stable internal user IDs even when exported identifiers use Jellyfin IDs.
Uniquely bind remote identities by server ID plus Jellyfin user ID, never by
username. Distinguish the review owner, original author, export target, and actor.
Enforce singleton ownership transactionally, including concurrent transfers.

Upgrade under existing maintenance coordination. Inspect the current schema,
verify a recoverable backup, migrate supported versions preserving data, and
verify row counts, foreign keys, owner uniqueness, and credential usability.
Never wipe data or stamp an unknown schema. Include every supported upgrade path
and recovery from interruption in tests.

Assign the current account and all existing reviews/scans/jobs/imports/export
records/community identity/history to the initial owner; preserve its API key.
Preserve legacy sidecar files unchanged. Mark legacy records accurately so they
are not shown as active exports in the new plugin layout.

If activity exists without an administrator, preserve it as awaiting owner
assignment. Require local owner setup before normal access or workers resume;
atomically assign existing activity at setup. Remove local-address/external
bypasses and document the new mandatory sign-in behavior.

Revoke browser sessions on the multi-user upgrade from a released schema and
on every restore (decision log amendment 2026-10-31). Retain migrated/restored API
keys, subject to current/restored account state and permissions. Backups must
include ownership, policy, identity, avatar, community and secret-recovery state.
Keep exported-file and database recovery coordinated; a failed migration or
restore must not silently leave a partially switched installation. Restore runs
only on an empty installation, from onboarding or the offline CLI, and restores
everything (decision log amendment 2026-10-30).

## 4. Authentication, roles, and account lifecycle

Retain existing password hashing, CSRF, origin checks, cookie protections, login
rate limits and server-side sessions. Carry a user principal through every
request; do not treat an API-key boolean as sufficient authorization.

Local login uses username/password with optional email. Registration defaults
to admin/owner creation; owner may enable open registration. Changing
registration mode is a nondelegable owner power. Managed creation
and recovery use temporary passwords and force browser password change.
Existing API keys remain usable during that requirement. Changing/resetting a
password revokes all of that user's existing sessions, not their API key.

Keep 30-minute idle/8-hour absolute ordinary sessions and 7-day idle/30-day
absolute remember-me sessions. Allow concurrent devices. Personal API keys have
no expiry, follow current user permissions, and are revealable only by their
holder or owner. Sensitive actions require fresh interactive authentication and
cannot be performed using an API key.

Jellyfin admission supports explicit import and first-login creation, the latter
enabled by default. Ordinary new accounts default to user; upstream admins map
to admin. Keep role mapping live unless the owner explicitly overrides it.
Sync at sign-in, every five minutes, and manually. Re-check effective
authorization at action boundaries so long-lived sessions/keys do not preserve
revoked permissions. Manual refresh is admin/owner; changing first-login admission
is a nondelegable owner power.

Confirm ownership of both identities during admin/owner-initiated linking.
Admins may link or relink their own and ordinary users' accounts, not another
admin or the owner; self-linking also needs fresh authentication and proof.
Reject duplicate-account linking until explicit deletion/data transfer resolves
the conflict. No name-based merging or unlink-to-local workflow.
On server replacement retain old identities/data and profile access; suspend
media access until explicit relinking. Preview export-ID effects, regenerate
under the new ID, and remove old active files only after successful replacement.

Confirmed upstream disablement/deletion disables the entire linked account,
revokes sessions/API access, and retains data pending authorized resolution.
An outage instead permits last-verified access for 24 hours from the last
successful verification, then profile/account access only. Never treat failed
refresh as verified deletion or refresh the grace timestamp on failure.

Owner transfer requires current-owner reauthentication and local credentials
on the successor; former owner becomes admin. Extend offline
`editarr admin reset-password` recovery for the sole owner with API stopped.
Do not allow bulk operations or role overrides to weaken owner protection.

## 5. Resource authorization and Users UI

Build one capability/resource-authorization service used by HTTP handlers,
repositories/service entry points, workers, and UI capability payloads.
Maintain an endpoint/action inventory and fail-closed coverage tests for new
routes. Filter lists/counts/pagination before returning data; also authorize
individual IDs, images, previews, audio-track requests, exports and job errors.

For linked users enforce Jellyfin library/parental restrictions across every
source. Media that matches no authorized Jellyfin item is blocked.
A metadata reassociation is not authorization. Local media defaults start at
all sources and inherit live unless individually overridden; admin management
is enabled by default but configurable. Owner keeps full application access.

Users list includes avatar/name, username, source, role, status, last login,
creation date, and override indicators. Include all agreed bulk operations,
checking permissions and protected targets individually. Editing includes the
approved profile, credentials, role/overrides, media access, filter mode/policy,
API key, and community controls. Ordinary users see only their own profile.

Local avatars use uploads. Linked name/avatar are synced read-only; email and
timezone remain Editarr preferences. Add language preference UI and infrastructure
without translations. Hide unavailable actions rather than disabling them.
Read-only synced values remain visible without an edit control.

User switches/logout must clear private drafts, queries, previews and persisted
user-specific state. Separate ordinary job feedback from admin-only inventory,
global job lists and filesystem paths. Users can see model-download progress in
their own scan/credits jobs. Render fresh permissions without briefly
showing forbidden controls during initial load.

Deletion presents retention/transfer/permanent-deletion effects before execution.
Respect shared dependencies and management scope. Retention keeps personal export
files as-is; remote community publications survive local account deletion.
Block deletion of scans referenced by any review, without disclosing hidden
review details. Preserve historical provenance needed by surviving records.
Deleting a saved review separately offers retaining or removing its published
files within the caller's export permissions. Keep provenance for retained files.

## 6. Reviews, filter policy, and community

Saved reviews are private to their owner, admins with permission, and owner.
Shared completed scan evidence remains reusable for authorized media. Keep
revision-based edit conflicts. Ownership changes do not rewrite shared evidence.

All approved personal review operations remain available subject to policy:
editing, timing, credits, previews, uploads and exports. Server-path scanning
and imports, and filesystem-path visibility, are admin/owner by default.

Filter settings default to live instance inheritance with personal overrides;
support independent-copy mode. Owner edits instance rules/severities and alone
enforces individual rules/subcategory severities. Admins can manage modes; users
can choose their own mode when no policy is enforced.

Preserve underlying review content and derive an enforced effective view.
Apply it to editing/import/export and existing reviews, validating on the
backend rather than trusting client classifications/selections. Enforced rules
must not be bypassed by altering matching metadata or dropping needed evidence;
retain enough provenance to reconstruct the effective result and explain it.

An export uses its review's policy owner regardless of target. Personal and
item-default publication both apply the current review owner's enforced policy. Copy/transfer
changes policy owner to the new review owner while retaining original authorship.
Policy changes regenerate affected personal exports; old files remain available
during regeneration and are withdrawn only if it fails. Track policy revision,
outdated/regenerating/failed status, and retry state without adding notifications.
Shared defaults remain outside the enforced-personal-export guarantee.

Each user has a separate community identity and credentials. Move the old
instance identity/history to owner. New admin publications use the acting admin's
identity; existing publications retain their original contributor.
Scope connection operations, votes, receipts, retry/recovery and publication
management to the permission matrix. Users may recover/retry their own import
receipts; admins/owner may do so for others within account/media permissions.
Do not change the remote operator dashboard.

## 7. Export and Jellyfin plugin contract

For `/media/Movies/Movie/Movie.mkv`:

| Scope | EDL | MCF |
| --- | --- | --- |
| Personal | `edl/<id>/Movie.user-<id>.edl` | `mcf/<id>/Movie.user-<id>.mcf` |
| Item default | `edl/Movie.edl` | `mcf/Movie.mcf` |

Paths are relative to the media's directory. Linked IDs use Jellyfin user IDs;
local-only exports use a distinct namespaced immutable Editarr ID. Define and
test one canonical encoding across repositories; never use usernames as paths.
A local-only identity has no Jellyfin user mapping until linking.

Allow one current personal export per target/media version/format. Admins/owner
exporting others' reviews choose self or review owner. Confirm replacement by a
different review; retain every saved review. Record target, actor, source review,
original authorship, policy owner and revisions. Shared exports record their
publisher/source even though their filenames identify a default rather than user.

Reuse recoverable atomic file publication, adding per-destination concurrency
checks/locking and stale-confirmation rejection. Detect filename collisions
between physical versions rather than overwriting another version's output.
Validate paths against the authorized resolved media path; reject traversal and
unexpected filesystem redirection. Do not leak host paths to ordinary users.

The plugin derives personal identity from the authenticated Jellyfin principal,
never an arbitrary caller-supplied user ID. Authorize the requested item/version,
then resolve that exact media path. Serve the personal file if present; otherwise
serve the matching new-layout default if present; otherwise report no export.
A server credential without an authenticated user must not impersonate a user.

Default-file existence is the only fallback switch. EDL and MCF use identical
selection/publishing rules. Do not read legacy adjacent sidecars. Existing EDL
route compatibility should be retained where possible; add explicit MCF access
without treating MCF as EDL. Scope caches by user/item/version/format so responses
cannot leak across users. If a personal file exists but is invalid/unreadable,
report that error and do not fall back. Default fallback requires personal-file
absence.

Document the coordinated deployment change: old files are preserved but ignored
by the updated plugin until explicitly moved/re-exported. Consumer selection does
not mean editing every playback client to interpret MCF; plugin serving and
selection are the agreed scope.

## 8. Audit and validation

Retain secret-free audit records for security/admin changes, exports and community
publishing for 90 days. Owner sees all; admin queries and detail access are scoped
to their account/media permissions. Do not expose credential material through
audit diffs, logs, backup job summaries or error payloads.

Required validation:

- Upgrade every supported schema with recoverable backup; verify ownership,
  credentials, legacy files, bootstrap-with-data, interrupted upgrade and restore.
- Two local users, two linked users and owner concurrently; local fallback login,
  provider collisions, disabled users, sync changes, 24-hour outage boundary,
  role override, owner immunity, server replacement and relinking.
- Every capability via session and API key; fresh-authentication restrictions,
  own/other/hidden resources, bulk mixed targets, owner nondelegability, path
  redaction, account switches and direct navigation/API attempts.
- Shared scans/private reviews, forbidden reference disclosure, optimistic edit
  conflicts, copy/transfer policy ownership, enforced rules on existing drafts,
  imports and exports, policy regeneration failures and stale worker writes.
- Personal/default EDL and MCF, all target/actor combinations, exact filenames,
  user/media/version isolation, collision handling, atomic rollback, simultaneous
  publishing, no legacy fallback, deleted-user retained files and ID transitions.
  Test explicit review-deletion file choices and invalid/unreadable personal-file
  errors without fallback in both formats.
- Community per-user credentials/votes/publications, authorized cross-user work,
  no accidental remote retraction, audit scope and 90-day expiry.
- Desktop/mobile Users and settings layouts, hidden controls, loading/error states,
  temporary-password-only browser flow, and no user data left after switching.

Use focused unit/integration tests in existing packages, frontend feature tests,
application tests in `web/src/test`, and browser tests in `web/e2e`. Add plugin
tests in its existing test project. Avoid broad unrelated refactors.

## 9. Delivery sequence and acceptance gates

The sequence below summarizes the work and keeps lifecycle changes after their
export dependencies.

1. Freeze permission/action contracts and migration design against this plan.
2. Implement user/identity/permission storage and data-preserving upgrade.
3. Add local auth, per-user keys, owner lifecycle and centralized authorization.
4. Add Jellyfin admission/linking/sync/media scope and outage handling.
5. Add Users/profile/settings UI and enforce capabilities across existing views.
6. Add review ownership, personal filters/enforcement and community identities.
7. Add export persistence/layout/regeneration and coordinated EDL/MCF plugin work.
8. Complete deletion/transfer, backups/restores, audit, documentation and release QA.

Do not ship a partially permissioned multi-user UI: all routes, workers and
consumer file selection must pass isolation tests before multi-user release.
Each phase must leave recoverable data and keep its checks passing.

## 10. Implementation handoff

The product interview and default-permission matrix are complete. Use the
decision log to resolve historical choices: later explicit decisions supersede
earlier options. Ask before changing a confirmed behavior or making a new major
product decision during implementation.

Implementation details such as exact table names,
module boundaries, route names, and canonical local-ID serialization are design
work within the confirmed behavior, not new product defaults.

## References

- [Seerr user creation](https://docs.seerr.dev/using-seerr/users/adding-users/)
- [Seerr user editing](https://docs.seerr.dev/using-seerr/users/editing-users/)
- [Seerr defaults](https://docs.seerr.dev/using-seerr/settings/users/)
- [Seerr owner](https://docs.seerr.dev/using-seerr/users/owner/)
- [Seerr deletion](https://docs.seerr.dev/using-seerr/users/deleting-users/)
- [Jellyfin user API](https://typescript-sdk.jellyfin.org/interfaces/generated-client.UserDto.html)
- [Jellyfin user configuration](https://typescript-sdk.jellyfin.org/interfaces/generated-client.UserConfiguration.html)

References were inspected during the interview. Seerr informed the available
options; the decision log takes precedence over Seerr's behavior. In particular,
Editarr uses live permission inheritance and configurable deletion dispositions.
Jellyfin has no standard separate email/timezone fields, which is why those
remain Editarr preferences.
