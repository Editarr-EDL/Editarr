# Account linking

Feature `account-linking` (MU-35 API, MU-39 UI). An admin or the owner links an
unlinked account to a Jellyfin identity, or relinks a linked account after a server
replacement. Only migration `0030_remove_passwords` makes unlinked accounts: it
disables every active account without a Jellyfin link with disable cause
`unlinked`. Linking such an account enables it; enabling it without a link is
refused 409 `account_link_required`. The link moves every personal EDL and MCF
publication to the new export ID. Accounts are linked from the Users dialog's
Jellyfin link section.

## UI

`web/src/settings/users/account-link-section.tsx` shows the effective link state
(unlinked, verified, outage grace with its expiry, profile-only after 24 hours, or
server replaced) and offers `Link to Jellyfin` (unlinked) or
`Relink to current server` (server replaced) only when the actor may link the
target (`accounts.link.own`, or `accounts.link.other` without another admin or
the owner unless the actor is the owner). A verified account gets no action.
`account-link-dialog.tsx` runs prove (the target's Jellyfin username and password,
then the actor's fresh Jellyfin password), review (`account-link-preview.tsx`, with
an explicit `admin_attested` basis, the status change, and blocker explanations),
and confirm (fresh Jellyfin password). `account-link-flow.ts` maps
every refusal code to reprove, review again, or a stop that says nothing changed.
A `removing` result says old files are still being removed; a completed one says
only files that still held what Editarr wrote were removed. The section's cleanup
line comes from the account read's `link_cleanup` (pending, removed, and kept old
files), so a pending removal survives a reload until the read says it finished.
Passwords are cleared when each proof request ends; the result survives the
session remount as an operation receipt. Nested password prompts keep Tab,
Shift+Tab, and Escape in the topmost dialog (`web/src/ui/ModalDialog.tsx`). There is no unlink or merge action; a duplicate identity explains
that explicit deletion or transfer is required (MU-41 adds the action).

## Routes

- `POST /api/users/{user_id}/link/proof` checks the caller's Jellyfin password, a
  Jellyfin sign-in, and the target on the preview's `proof_basis`:
  `actor_authentication` (the caller's own account) or `admin_attested` (another
  account). An `account_password` field answers 422. It returns a single-use proof
  bound to the caller, its session, the target, its permission revision, and both
  identities, valid for ten minutes, plus the impact preview and its
  `confirmation` digest. The preview's `status` is `{current, after, cause}`, for
  example `{disabled, active, unlinked}` for an unlinked account. A proof for an
  already linked account is blocked `already_linked`.
- `POST /api/users/{user_id}/link` spends the proof when the current preview
  matches `confirmation` and has no blockers. A changed preview answers 409
  `link_preview_stale` with the new preview; blockers answer 409 `link_blocked`.
  It first recovers the account's open transition, as the startup and hourly
  sweep does.
- `GET /api/users/{user_id}` carries `link_cleanup`: null, or the latest
  `removing` or `completed` transition's state with pending, removed, absent, and
  kept old-file counts. No paths.

Both routes need `accounts.link.own` or `accounts.link.other` with fresh
interactive authentication. API keys get `interactive_session_required`.

## Automated journey

```bash
VERIFY_EVIDENCE_ROOT=web/test-results/mu-39/verify node .github/skills/verify-editarr/scripts/verify.mjs auth --reviews --account-link
```

Before launch, `scripts/lib/legacy-members.mjs` seeds a 0029 database (see the
[skill](../SKILL.md)): the owner linked to the run's Jellyfin 12 administrator and
`legacy-member`, a local password account with a literal Argon2 string, which owns
the upgrade fixture's two reviews on the synthetic `Legacy member film` and a
personal EDL and MCF of `review-legacy-001` under its local export ID. Startup's
`0030_remove_passwords` disables it with cause `unlinked`. The owner signs in with
Jellyfin.

After the review-policy journey, `scripts/lib/account-link-journey.mjs` drives the
browser at 1440 px (some states also at 320 px). The owner confirms every step
with its Jellyfin password. The review member is a Jellyfin user, linked by its
first sign-in. The journey creates Casey and Emery on the run's Jellyfin; Editarr
reaches them through the run's synthetic catalog, which relays user routes to the
run's Jellyfin. Helpers live in `account-link-ui.mjs` (contexts, sign-in,
same-session API, prove and confirm through the dialog, masked screenshots, axe,
focus trap) and `account-link-support.mjs` (run Jellyfin users, SQLite and disk
assertions). The linked member's steps live in `account-link-member-journey.mjs`
and the legacy member's in `account-link-legacy-journey.mjs`.

1. The review member publishes personal EDL and MCF exports; its identity is
   `verified`.
2. The member's profile says `Linked to Jellyfin.` with no button. Its proof for
   itself answers 403 and for the owner 404.
3. The owner imports Emery. The member's Users dialog says "Linked to Jellyfin.
   Name and avatar sync from Jellyfin. A linked account cannot be unlinked." and
   offers no link, relink, unlink, or merge action (1440 and 320 screenshots).
4. A proof with `account_password` answers 422. A proof to Emery is blocked
   `already_linked` and `jellyfin_identity_in_use`. A proof to Casey is
   `admin_attested`, blocked `[already_linked]`, with status
   `{active, active, null}`.
5. That Casey proof presented for the owner's ID is refused 409
   `link_proof_invalid`, and confirming a blocked proof answers 409.
6. Neither identity changes and no export moves.
7. `legacy-member` is `disabled`, cause `unlinked`, permission revision 2, with no
   identity, and its EDL and MCF rows and files sit under its `local-...` export
   ID with their recorded bytes.
8. Users shows its row as "Disabled: no Jellyfin link" with
   `Link legacy-member to Jellyfin` and no enable control
   (`link-ui-legacy-member-row-1440` and `-320`). Its details say "Not linked to
   Jellyfin. The account cannot sign in until it is linked." The row action opens
   the dialog, which asks for no account password.
9. A proof to Emery is blocked `[jellyfin_identity_in_use]` with the "Editarr
   never merges accounts" explanation and no confirm (1440 and 320). Nothing moves.
10. Start again, prove Casey: `admin_attested`, no blockers, status
    `{disabled, active, unlinked}`, and the impact reads "disabled (no Jellyfin
    link) becomes active." and `local-... becomes CASEY_ID`. The proof presented
    for the owner's ID is refused 409 `link_proof_invalid`, and confirming the spent
    proof shows "That proof is no longer valid. Prove both identities again."
11. Prove Casey again with the new export-ID directory made read-only. Confirm
    answers 409 `write_failed`; "Link stopped" says "Nothing changed. The account
    keeps its identity and its existing exports." The account stays disabled with
    no identity, the old files keep their bytes, and the transition is
    `rolled_back` with `write_failed`.
12. Start again, prove, and confirm: `completed`, "Linked to Jellyfin user
    Casey." Both formats move under Casey's export ID with identical bytes and a
    bumped publication revision, the old files and directories are gone, the
    identity is `verified`, and the account is `active` with no cause and a higher
    permission revision.
13. A fresh context signs in with Jellyfin as Casey: the profile says
    `Linked to Jellyfin.`, the status names the former local account's ID,
    `GET /api/library-items/{film}/reviews` lists both pre-0030 reviews, and the
    film page shows `Open review Synthetic review` and `Open review Imported review`
    (`link-ui-legacy-member-reviews-1440` and `-320`).

`verify.mjs restore` also links a restored unlinked account through the UI (see
[Backups](./backups.md)), and its prove and confirm prompts are checked in
Chromium: Tab and Shift+Tab each cycle twice through their controls in order
without leaving them (`restore-link-keyboard.json`).

The owner self-link, the server replacement, and the admin-attested relink no
longer run here. Setup links the owner, and after a server replacement a Jellyfin
owner cannot confirm with its Jellyfin password, so `tests/test_account_relink_http.py`
covers the relink.

Evidence: `legacy-members-fixture.json`, `account-link-result.json` (with
`legacyLink`), `link-ui-steps.json`, `link-ui-*.png` with
`.aria.txt` (inputs masked), `link-ui-axe.json`, `link-ui-http-statuses.json`,
`link-ui-browser-errors.json`, and `account-link-http-statuses.json`. Jellyfin
passwords, proofs, cookies, and CSRF tokens join the run's redaction set.

## Deterministic checks

- `tests/test_account_link_http.py`: preview, both formats moved, proof binding,
  replay, expiry, matching names, throttling, an imported
  duplicate, a foreign file at the new path, stale preview, API keys, roles.
- `tests/test_account_link_proof_binding.py`: a status or revision change voids
  an outstanding proof, before or during the file writes;
  a concurrent import is an audited 409; every refusal reason is registered; an
  identity without an avatar removes the local one; GUID encodings.
- `tests/test_account_relink_http.py`: server replacement relink, same-GUID relink,
  no same-server switch, a late sync read of the old identity, owner credentials,
  `admin_attested` relinks (owner relinks admins, admins do not), and an
  unreachable server that leaves the owner in charge until a replacement.
- `tests/test_account_link_recovery.py`: real hard exits at six points through
  `tests/account_link_crash.py`, a failed write, an old file that cannot be removed
  yet, unfinished regeneration, a withdrawn publication, unrecognized old bytes,
  an uncertain switch commit.
- `tests/test_account_link_evidence.py`: returned old bytes are reconciled,
  record-only destinations check collisions and never claim bytes, and a crash
  before the staging rename leaves no temporary file.
- `tests/test_account_link_withdrawn.py`: a publication withdrawn during an
  outage and moved by a link serves again under the new ID once its owned old
  bytes return, before or after the link; foreign bytes stay.
- `tests/test_account_link_returned_crash.py`: real hard exits at five points while
  removing returned old bytes (`tests/account_link_returned_crash.py`), from
  `finish` and from reconciliation, in both formats, and a directory fsync error
  after the unlink; the withdrawn publication still serves again.
- `tests/test_account_link_redirected_paths.py`: link-time cleanup and
  reconciliation never follow a symbolic link at the old file, export-ID directory,
  or format directory, and rollback never follows a linked new export-ID directory
  to its new or staging file; matching bytes behind a link stay.
- `tests/test_account_link_refusal_audit.py`: link refusals name their step and
  established basis; an access refusal under the switch lock rolls back as
  `refused`; a provider refusal after a valid proof is audited once.
- `tests/test_account_link_new_file_ownership.py`: a foreign file at the new path
  survives a collision and every crash point even with matching bytes; recovery
  removes the file Editarr placed, also after a remount renumbers the device; a
  renumbered inode keeps the file, the next link is blocked by
  `export_file_collision`, and it succeeds once the file is removed; links still
  complete without hard links.
- `tests/test_account_link_cleanup_read.py`: `link_cleanup` is null before a link,
  stays `removing` with pending counts for the admin and the member until recovery
  settles it, and reports a hand-edited old file as `kept`; the read names no path.
- `web/src/settings/users/account-link-keyboard.test.tsx`: Tab and Shift+Tab stay
  in the topmost prompt inside an account dialog, stray focus returns to it, and
  Escape in a busy prompt closes nothing behind it.
- `tests/test_account_link_migration.py`: 0023 upgrade, constraints, a modified
  schema, raised and hard-exit partial upgrades with snapshot recovery, backups.

## Not covered

A live Jellyfin server. The `outage_expired` state is covered by Vitest
(`web/src/settings/users/account-link-*.test.tsx`), not the journey. The journey
finishes a pending removal through the pre-link recovery, not by waiting for the
hourly sweep or restarting. The legacy member's personal exports are written by
the current publication code against the 0029 database, standing in for a JF-05
local account's own publication; no JF-05 build runs.

A real remount is not driven; the tests renumber the file's device or inode in
`os.lstat`. Where a share's inode numbers change across mounts, an interrupted
link leaves its new file in place and the account's next link reports
`export_file_collision` until the operator removes that file.
