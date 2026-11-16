# Multi-user default permission matrix

Companion to the [plan](multi-user-plan.md) and [decision log](multi-user-decisions.md).
Updated 2026-10-18. All defaults below are confirmed interview decisions.

`Yes` includes the caller's authorized media only. `Own` restricts objects to the
caller. `Ordinary users` excludes other admins and the owner. Owner has full
application access. Role defaults and explicit allow/deny overrides are live,
except the nondelegable powers below. Bulk actions use the same checks per target.

## Accounts and identity

| Action | user | admin | owner |
| --- | --- | --- | --- |
| View own profile | Yes | Yes | Yes |
| List/search users and see all approved list columns | No | Yes | Yes |
| Import selected or all Jellyfin users | No | Yes | Yes |
| Edit another account's profile | No | Ordinary users | Yes |
| Disable/re-enable/delete another account | No | Ordinary users | Yes |
| Revoke another account's sessions | No | Ordinary users | Yes |
| Revoke another account's API key | No | Ordinary users | Yes |
| Initiate linking/relinking with ownership proof | No | Own and ordinary users | Yes |
| Manage another admin account | No | No | Yes |
| Manually refresh Jellyfin user sync | No | Yes | Yes |
| Unblock a Jellyfin user blocked when their account was deleted | No | No | Yes |
| Manage local-user media defaults/individual media access | No | Yes, configurable | Yes |
| Own API key creation/rotation/reveal/revocation | Own | Own | Yes |
| Reveal another user's API key | No | No | Yes |
| Own profile editing | Own | Own | Yes |
| Choose own filter mode when no policy is enforced | Own | Own | Yes |
| Personal active-session list/revocation UI | Excluded | Excluded | Excluded |

Linked name/avatar are read-only; local avatars use uploads. Linked email and
timezone are independent preferences. Identity proof and fresh authentication
still apply; permission to initiate linking is not permission to impersonate users.

## Media, scans, and reviews

| Action | user | admin | owner |
| --- | --- | --- | --- |
| Browse authorized Jellyfin media and artwork | Yes | Yes | Yes |
| Read/reuse completed scan findings for authorized media | Yes | Yes | Yes |
| Start audio/video/combined scans, rerun, choose tracks/models/options | Yes | Yes | Yes |
| See underlying filesystem paths | No | Yes | Yes |
| View/edit manual media identity/source associations | Yes | Yes | Yes |
| View own job progress/errors | Own | Yes | Yes |
| View others' queue/progress/errors | No | Yes | Yes |
| Cancel/resume queued/interrupted jobs | Own | Yes | Yes |
| Reorder global queue | No | Yes | Yes |
| Rename/delete completed scans | Own | Yes | Yes |
| Create/save/rename/duplicate/delete reviews | Own | Yes | Yes |
| View/copy/edit/delete/export others' reviews | No | Yes | Yes |
| Attach/remove scan evidence; edit filters/selection/classification/severity | Own | Yes | Yes |
| Edit actions/channels/comments/review and MCF metadata | Own | Yes | Yes |
| Synchronize/retime; detect/accept/edit/remove credits | Own | Yes | Yes |
| Play audio/mute/video previews | Own reviews | Yes | Yes |
| Upload EDL/MCF to reviews | Own | Yes | Yes |
| Share a review with another ordinary user | Excluded | Excluded | Excluded |

Deleting a scan is blocked while any saved review references it; private
references must not be disclosed.

Known limitation (accepted): queue IDs are one global sequence and scan numbers
count per media item, so gaps in the IDs and numbers a user sees may reveal how
many jobs other users created. Hidden jobs' content, owners, titles, paths,
progress, errors, and queue positions stay hidden; `sort_order` counts only
jobs the caller may view. Editing metadata cannot grant additional media
access. Enforced filter policy takes precedence over review-edit permissions.

## Exports and filters

| Action | user | admin | owner |
| --- | --- | --- | --- |
| Download/publish/delete own personal EDL/MCF | Own | Yes | Yes |
| Delete another user's personal exports | No | Yes | Yes |
| Publish/replace/delete item-default EDL/MCF | No | Yes | Yes |
| Export another user's review to self or review owner | No | Yes | Yes |
| Edit own non-enforced filter preferences | Own | Own | Yes |
| Edit instance selection rules/severity defaults | No | No | Yes |
| Change instance default filter mode | No | Yes | Yes |
| Assign an individual user's filter mode | No | Ordinary users | Yes |
| Enforce individual rules/subcategory severities | No | No | Yes |

Replacement by a different review requires confirmation. Export target does not
change policy ownership. Copying/transferring a review does change policy owner.
Publishing either a personal or item-default export applies the current review
owner's enforced policy. Shared fallback remains outside the personal-export
regeneration guarantee.
Adjacent pre-migration `Movie.edl`/`Movie.mcf` sidecars are shared item files.
Deleting one from Manage exports follows the item-default row.
When deleting a review, offer retention or removal of its published files,
subject to the caller's export permissions; preserve provenance for retained files.

## Community

| Action | user | admin | owner |
| --- | --- | --- | --- |
| Search/find filters and download/import scans | Yes | Yes | Yes |
| Vote/submit feedback | Own identity | Own identity | Own identity |
| Publish own review or shared scan evidence | Yes | Yes | Yes |
| Publish another user's review | No | Yes | Yes |
| Check/retry/retract publications | Own | Yes | Yes |
| Manage connection/profile/enrollment/disconnection/key recovery | Own | Own and ordinary users | Yes |
| Recover/retry own import receipts | Own | Own | Yes |
| Recover/retry another user's import receipts | No | Account/media-scoped | Yes |
| Separate operator/moderation dashboard | Outside scope | Outside scope | Outside scope |

Cross-user new publishing uses the acting admin's contributor identity. Existing
publications retain the original contributor. Deleting a local user does not
retract remote publications.

## Instance settings and operations

| Action | user | admin | owner |
| --- | --- | --- | --- |
| View connection addresses/status | No | Yes | Yes |
| Test/edit/disconnect Jellyfin | No | No | Yes |
| View installed models/global download status | No | Yes | Yes |
| Download models, including scan/credits-triggered acquisition | Yes | Yes | Yes |
| Own-job model-acquisition progress | Own | Yes | Yes |
| Cancel model downloads/delete models | No | Yes | Yes |
| View/test path mappings | No | Yes | Yes |
| Create/edit/delete/reorder path mappings | No | No | Yes |
| View/change deployment/network settings | No | No | Yes |
| View logs/change logging settings | No | Yes | Yes |
| Restart Editarr | No | Yes | Yes |
| View About/version | Yes | Yes | Yes |
| Create backup/configure schedules/view backup job status | No | Yes | Yes |
| Download backup | No | No | Yes |
| Delete backup jobs/retained archives | No | No | Yes |
| Read audit history | No | Permission-scoped | Yes |
| View archived scans, reviews, and exports of removed sources | No | Yes | Yes |

## Backup restoration

Restore happens only on an empty installation, with no accounts and no work,
from the onboarding screen or the offline CLI into an empty data directory. No
role restores a backup into a configured installation (amendment 2026-10-30 in
the [decision log](multi-user-decisions.md)).

## Nondelegable and authentication restrictions

Only the owner may transfer ownership, manage the owner account, edit role
defaults, grant/revoke admin roles or individual permission overrides, reveal
others' API keys, change connections/deployment, download backups, or
manage enforced filter policy. No role template or override can grant these
powers to another role. The owner retains full access even if Jellyfin changes.

JF-05 removed the Jellyfin first-login admission mode, so every first Jellyfin
sign-in creates an account. JF-06 removed local passwords, password resets, and
the registration mode. Every account comes from Jellyfin.

Viewing the archive of removed sources belongs to the admin role and the owner.
It is not delegable either: no role default or individual override grants it to
an ordinary user or removes it from an admin, because the archive lists every
archived review and owner without media or account checks.

Sensitive actions additionally require an interactive session and fresh
authentication with the account's Jellyfin password: API-key reveal/rotation, permission changes,
account linking, account deletion (retain, transfer, or permanent), Jellyfin
unblocking, and ownership transfer. An otherwise
authorized API key cannot substitute for this authentication requirement.
