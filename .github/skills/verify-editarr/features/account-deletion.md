# Account deletion

Feature `account-deletion` (MU-38 API, MU-41 UI). An admin or the owner deletes
another account after previewing what retention, transfer, or permanent deletion
does to it. The journey first drives the real API, then the browser UI.

## Routes

- `GET /api/users/{user_id}/deletion?mode=retain|transfer|delete` (with
  `&destination=ID` for a transfer) returns the consent: `reviews {count, hidden,
  ids}`, the files the caller may see with their `retain`/`remove` choices,
  `scans {hidden, items}`, `jobs {waiting, running, hidden}`, `transfer
  {files_moved, policy_files}`, credentials, community counts, `blockers`, and a
  `confirmation` digest. Only what the caller may see is named.
- `POST /api/users/{user_id}/deletion` applies `{mode, confirmation,
  destination_id, files, reauthentication: {password}}`. A changed consent
  answers 409 `stale_account_deletion`, blockers 409 `account_deletion_blocked`,
  a missing or extra file choice 422 `account_deletion_choice_required`, an
  unusable destination 409 `transfer_destination_unavailable`. A retry by the
  same actor with the stored request returns the stored result with
  `replayed: true`.
- `POST /api/users/bulk-deletion` takes `targets` with one confirmation each and
  one `reauthentication`, and reports `applied` or `refused` with a code per
  target, including an operational failure of one target.
- Both POSTs take `jellyfin_user`: `allow` (the default) lets a linked account's
  Jellyfin user sign in again to a fresh account; `block` stores the Jellyfin
  server and user ID in `jellyfin_user_blocks`, so password sign-in, Quick
  Connect, import, and linking refuse that user until
  `POST /api/jellyfin/blocks/unblock` (`accounts.jellyfin.unblock`, owner by
  default, fresh password). Local accounts ignore it. Results and the
  `account.delete` event carry `jellyfin_blocked`; a replay needs the same choice.

All three need `accounts.delete.other` for each target and an interactive
session: an API key answers 403 `interactive_session_required`, and a POST
without the password 403 `reauthentication_required`. The owner and, for an
admin, other admins answer 403 `protected_account`; the caller's own account
answers 409 `own_account`. A deleted account answers 404 everywhere else and is
missing from `GET /api/users`.

## UI (MU-41)

Users shows a Delete action on each row the caller may delete and a "Delete
selected" bulk action; Account details has "Delete account"; and
`#/settings/users/{id}/delete` opens the dialog directly. The owner, the caller's
own account, and (for an admin) other admins get no row action, no bulk
selection, and a deep-link message with no preview request. The dialog asks for
Keep data, Transfer data (with a destination search), or Delete permanently;
for a linked account the review also asks "After deletion, their Jellyfin user":
Allow them back (preselected) or Block them, and bulk deletion asks once for every
linked target. Users lists "Blocked Jellyfin users" with an Unblock action for
holders of `accounts.jellyfin.unblock`. The choice's live proof is in
`auth --jellyfin-accounts` steps 13 to 15 ([jellyfin-users](./jellyfin-users.md)).
The dialog asks for
shows every consequence and blocker from the preview, gives each visible file a
Keep/Remove choice (Keep preselected) in delete mode, and confirms with the
password. A 409 `stale_account_deletion` or `account_deletion_blocked` fetches the
preview again and asks for another review. Delete mode calls a scan the preview
does not mark `required` "deleted unless a published file that stays still uses
it", since a kept file keeps its scan. Transfer lists each visible file as moving
(it targets the account) or staying, and as following the destination's policy
(the account's own review) or keeping its review owner's policy (another
account's review). A request whose answer never arrives (no response or a
gateway error) is reported as an unknown outcome, never as "not changed", and
Users is reread. Bulk previews each target, offers confirmation only after every
preview settles, sends exactly the unblocked targets shown when confirmation
opened, and lists each target's outcome; stale targets are previewed again for a
second confirmation. In the link dialog, MU-39's `jellyfin_identity_in_use`
blocker finds the holding account from `GET /api/jellyfin/sync` and, when the caller
may delete it, offers "Review deleting or transferring NAME" with the link target
as the suggested destination when the link preview says the target is active. A
disabled target cannot receive data, so the dialog offers "Review deleting NAME"
and says the target can't receive the data until it is linked; afterwards the link dialog returns to the proof step
for a fresh preview. Nothing merges accounts. Code: `web/src/settings/users/account-deletion-*`,
`web/src/api/api-account-deletion.ts`, `web/src/styles/account-deletion.css`.

## Automated journey

```bash
COREPACK_ENABLE_NETWORK=0 EDITARR_VERIFY_PNPM_CJS=$HOME/.cache/node/corepack/v1/pnpm/11.22.0/bin/pnpm.cjs \
node .github/skills/verify-editarr/scripts/verify.mjs auth --community --reviews --account-deletion --ports=9220-9239
```

`--community` runs the MU-32 community journey first; its `mu32-member` (a
community identity, a queued publication, and its evidence) is the community
history the UI bulk step deletes. `EDITARR_VERIFY_PNPM_CJS` is that journey's
prerequisite (see [community activities](./community-activities.md)). Without
`--community` the API part passes and the UI bulk step stops with a message naming
the missing member.

After the review-deletion journey, seven Jellyfin users on the run's Jellyfin
sign in, which creates their accounts, and the owner promotes one to admin. That admin's preview of the owner answers 403 and a bulk
request that substitutes another target's confirmation for the owner is refused
`protected_account`; the owner previewing itself answers 409.

Retention. A member creates an API key and publishes a personal EDL and MCF. A
review created after the owner's preview makes the confirmation stale (409).
The owner's own API key is refused `interactive_session_required` and a session
request without the password `reauthentication_required`, with the member still
active. The fresh confirmation applies; a replay returns the stored result. The
member's session and key then answer 401. Retention releases the Jellyfin link, so
that Jellyfin user's next sign-in is refused or reaches a new account, never the
retained one. Independent SQLite and file reads show both publications unchanged
byte for byte, the user row disabled with no Jellyfin link, username, or key, and
an `account_dispositions` row whose `result_json.result` equals the response.

Permanent deletion. Another member publishes EDL and MCF and the owner copies the
member's review, so the copy needs the member's scan. The delete preview lists
both files and the scan with `required: true`. A request missing one choice is
refused (422) with nothing changed. Retaining the EDL and removing the MCF
deletes the review and its profile, keeps the EDL bytes detached from the review,
removes the MCF file and row, and keeps the scan the owner's copy uses.

Transfer. A third member publishes EDL and MCF. Naming the member itself as the
destination answers 409 `transfer_destination_unavailable`. A transfer to a
fourth member moves the review (owner changed, author kept) and both
publications: SQLite shows the destination as target with its export ID (a linked
account's Jellyfin user ID), and the
files exist at the destination's export-ID paths with the same SHA-256 while the
old paths are gone. Both `account_transfer_moves` rows settle `removed`.

Bulk. One target with its own confirmation is applied; a second target given the
first target's confirmation is refused as stale; the owner is refused
`own_account`.

The journey asserts `account.delete` success and refusal reasons, no
`community.*` events, an empty `PRAGMA foreign_key_check`, and unchanged
synthetic media. The community service is not configured in this run; the pytest
transport spy in `tests/test_account_deletion_http.py` proves that deletion makes
no remote call. Hard exits before and after the commit are proven by
`tests/test_account_deletion_recovery.py` and, for transfer, by
`tests/test_account_transfer_recovery.py`. An unfinished transfer followed by a
retain or delete, and a rollback that keeps failing, are proven by
`tests/test_account_transfer_leftovers.py`. Backups captured mid-transfer and
restored (full, settings, and over pending cleanup) are proven by
`tests/test_account_transfer_restore.py` and, for MU-35 links,
`tests/test_account_link_restore.py`: every usable copy stays, the restored records
close, and the owner's `editarr admin write-export <export_id>` puts the committed
bytes back at the recorded path. Detecting and blocking a restore whose records
disagree with the disk is MU-40's (ruling wave13-mu38-defer-restore). That command
never replaces an entry. Its cases (an empty path created, the committed bytes
already present left alone, a refused user edit including one saved during the
command, symbolic links including a directory swapped for one after the checks,
no hard links, a platform without no-follow descriptors, a missing media directory,
the owner's password including an empty one, a former owner refused after waiting
for the lock, an unknown publication audited by ID only, a pending recovery journal
refused as `export_recovery_required` including one behind a late directory link,
database failures before placement and at commit, an I/O error or interrupt after
placement removing the file including one as the link or placement returns, a
rerun removing a crashed run's temporary file while a foreign file under that name
and every refusal leave it, a partial file left by a crash without hard links
refused for the operator, a 232-byte export name, the publication lock beside a running app, database
maintenance, and a real process) are proven by `tests/test_cli_write_export.py`.
Recovery never writing export bytes, returned old bytes after a later relink, the
link and transfer interlock, a refused link leaving no block, and a user's own
sidecar edit or deletion never blocking are proven by
`tests/test_account_failsafe_recovery.py`. Stale retain and transfer consent after a re-publication or an equal-count replacement, and
publications the export rules hide, are proven by
`tests/test_account_deletion_stale_consent.py`.

The API journey asserts that deletion adds no `community.*` audit events (a
before/after count, since `--community` records its own).

The UI journey (`scripts/lib/account-deletion-ui-journey.mjs` and
`account-deletion-ui-steps.mjs`, with browser helpers in
`account-deletion-support.mjs`) then runs in the browser at 1440 and 320 px:

- Protected targets. A new admin sees no Delete or Select control for the owner,
  `deleting-admin`, or itself; their deep links show the protection message and send
  no `/deletion` request; a forged bulk request answers `protected_account` for each.
- Retention by keyboard. Tab reaches the row action, Space picks Keep data, Enter
  reviews; Tab and Shift+Tab stay inside each dialog. A review created after the
  preview makes the confirmation 409 `stale_account_deletion`: the dialog shows the
  refreshed preview (2 reviews) and nothing changed in SQLite; the second
  confirmation applies. The impact says the account "can no longer sign in: N
  browser sessions end, its Jellyfin link is released." Both personal files keep
  their rows and bytes, the session answers 401, SQLite shows the row disabled
  with no Jellyfin link or username and a `retain` disposition, and the account
  leaves the list.
- Transfer from the deep link. The destination is searched and chosen; the impact
  names the new owner, the original author, and the destination's export ID, and
  lists each file as moving and following the destination's policy. Both files move to the destination's export ID with identical bytes, the
  old paths are gone, and Done clears the deep link.
- Permanent deletion at 320 px. Both files start at Keep; the MCF is set to
  Remove; the scan the owner's copy needs shows "kept, still needed". Every control
  and consequence lies inside the 320 px viewport. The MCF row and file go, the EDL
  stays detached with its bytes, and the scan and the owner's copy remain.
- Unknown outcome. `ui-unconfirmed` publishes one review; delete mode shows its scan
  as "deleted unless a published file that stays still uses it". The EDL stays and
  the MCF is removed. The browser's POST is passed to the server with
  `route.fetch()` and then aborted, so the deletion commits but no answer arrives.
  The dialog and results say "outcome unknown", never "not changed"; the reread
  Users list omits the account; SQLite keeps the scan (`scans_kept`) for the
  detached EDL.
- Partial bulk. `zbulk-a`, `zbulk-b`, and the community member are retained
  together. The community member is renamed `zbulk-community` in Jellyfin and an
  sync carries the name to Editarr. Reviewing the selection again, the
  browser holds `zbulk-a`'s preview (`page.route`) after the community member's has
  settled: no "Delete N accounts" button shows until it is released. The community impact says the identity
  and records stay and nothing is retracted. `zbulk-b` gains a review after the
  preview, so the first request applies two targets and refuses it as stale; its
  refreshed preview is confirmed alone. The community identity, operations, and
  publications are unchanged in SQLite.
- Duplicate identity. The run seeds `legacy-linker`, a local password account in
  a 0029 database that startup's 0030 disabled with cause `unlinked` (see
  [account linking](./account-linking.md)). Emery, a new user on the run's
  Jellyfin, is imported. The owner opens `Link legacy-linker to Jellyfin` from the
  Users row and proves Emery: blockers `[jellyfin_identity_in_use]`, status
  `{disabled, active, unlinked}`, a "Review deleting NAME" action, the sentence
  "legacy-linker can't receive its data until it is linked.", and no confirm,
  unlink, or merge (`deletion-ui-duplicate-blocker-1440` and `-320`). The action
  opens the holder's deletion dialog with no destination preselected; Transfer
  data lists only active accounts, so `legacy-linker` is not offered
  (`deletion-ui-duplicate-transfer-destinations-1440`). Keep data is reviewed
  (the impact says the Jellyfin link is released) and confirmed:
  `identity_released` is true, and the link dialog says to prove the identity
  again. Nothing is linked until the fresh proof, which has no blockers; confirming
  it links Emery, re-enables `legacy-linker`, and leaves the holder disabled with
  a `retain` disposition, so two rows remain and nothing merges.

The result records a disposition's `confirmationSha256`, never the single-use
token. Every confirmation the journeys receive or read joins the run's secrets,
so `redaction.json` fails the run if one reaches evidence.

Evidence: `account-deletion-result.json`, `account-deletion-http-statuses.json`,
`account-deletion-ui-result.json`, `account-deletion-ui-api-statuses.json`,
`deletion-ui-*.png` and `.aria.txt` (masked), `deletion-ui-axe.json`,
`deletion-ui-keyboard.json`, `deletion-ui-steps.json`,
`deletion-ui-http-statuses.json`, `deletion-ui-browser-errors.json`, and
`account-deletion-ui-provider.json`.

Known gaps. The holder lookup needs `accounts.jellyfin.sync`; without it the
blocker keeps its explanation and offers no action. Destination search lists one
page of 20 matches. Transfer blockers and a moved file of another account's review
are covered by Vitest (`account-deletion-dialog.test.tsx`), not live. The preview
names neither the files that use each scan nor a detached file's effective policy
owner (`intended_policy_owner_id`), so those stay conditional or count-only in the
UI.
