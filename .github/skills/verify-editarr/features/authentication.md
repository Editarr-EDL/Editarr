# Authentication

A new installation shows **Connect your Jellyfin server** instead of protected
work. A Jellyfin administrator's sign-in makes that Jellyfin user the owner,
Editarr mints its own `Editarr` API key in Jellyfin, and the requested page
opens. Signing out restores the gate, and the owner signs in with Jellyfin from
a new browser context.

## Sub-features

- `auth-setup`: connect Jellyfin as the first owner through the browser.
- `auth-setup-refusals`: a non-administrator, a wrong password, a bad URL, and
  an unreachable or non-Jellyfin address each refuse with their own message and
  store nothing; a second setup after an owner exists returns 409.
- `auth-deep-link`: restore General Settings after setup and sign-in.
- `auth-sign-out`: remove protected access when the session ends.
- `auth-persistence`: sign in with Jellyfin as the persisted owner in a fresh context.
- `auth-responsive`: setup/General at desktop and sign-in/General at mobile widths.
- `auth-isolation`: two simultaneous instances keep separate data, ports,
  process groups, and browser contexts; automated with `verify.mjs pair`.
- `auth-failure-cleanup`: a run that fails after credential entry still stops
  its group and its Jellyfin, removes scratch, and keeps redacted evidence;
  `auth --inject-failure`.
- `auth-fixed-clock`: sign-in works with browser `Date` pinned to `FIXED_TIME`;
  `auth --fixed-clock`.
- `auth-quick-connect`: Jellyfin Quick Connect sign-in, replay, cancel,
  disabled account, hidden when off, and expiry; `auth --quick-connect`.

## How to get to it (user POV)

- Open the new installation's root to reach setup.
- Open `/#/settings/general` while gated; setup/sign-in preserves the destination.
- Open the account menu (the avatar button named `Account menu for <name>`)
  and choose menu item `Sign Out` to return to sign-in.
- First-run `Restore from backup` is a separate path in [Backups](./backups.md).

## Driving it with Playwright

Preconditions: fresh data, enabled forms authentication, doctor passing on the
run-owned instance, Docker, and the digest-pinned Jellyfin 12.0.0 image that
`media_access_live_docker.IMAGE` names. A missing image or Docker exits 2 with
`jellyfinImage` in `blocked.json`.

Run the complete automated recipe from the repository root:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs auth
```

- **Jellyfin.** `lib/run-jellyfin.mjs` starts the run's Jellyfin through
  `users_ui_provider.py launch`, finishes its startup wizard with the
  administrator `jf-owner` and the ordinary user `jf-member`, and requires
  `/System/Info/Public` to report `StartupWizardCompleted`. `--identity` starts
  it with the synthetic libraries instead, where `root` is the administrator and
  `casey` the ordinary user. The container stops after Editarr's cleanup.
  The same module exports the members' helpers: `addJellyfinUser(run, name,
  {administrator})` creates a user on the run's Jellyfin and returns `{name,
  username, password, jellyfinId}`; `admitJellyfinUser(run, name)` also signs it
  in once over the API, so the account exists, and adds its `id`;
  `renameJellyfinUser(run, jellyfinId, name)` renames it in Jellyfin. Every
  journey's members are Jellyfin users on the run's Jellyfin.
- **Open.** Navigate to `origin + '/#/settings/general'`; expect heading
  `CONNECT YOUR JELLYFIN SERVER` and button `Restore from backup`. Capture the
  empty setup state. Protected libraries must return 423.
- **Connect.** Fill `Jellyfin server URL`, `Jellyfin username`, and
  `Jellyfin password` with the run's administrator, then click button
  `Connect and sign in`. Expect heading `General` and the account menu button.
- **Read.** Require authenticated `/api/auth/status` with role `owner` and
  `jellyfin_user_id` equal to the administrator's Jellyfin ID, exactly one
  `Editarr` key in Jellyfin's `GET /Auth/Keys`, and HTTP 200 from
  `/api/system/version`; its `release` must match `web/package.json`.
- **Sign out.** Click the account menu button, then menu item `Sign Out`
  (`scripts/lib/account-menu.mjs` `signOut`).
  Expect `SIGN IN TO CONTINUE`; protected libraries must return 401.
- **Reopen.** Use a new 320px browser context, open the same deep link, fill
  exact labels `Jellyfin username` and `Jellyfin password` (`fillSignIn` in
  `lib/run-jellyfin.mjs`; there is no sign-in method choice), then click exact
  button `Sign in`. Expect `General`, the unchanged deep link, an
  authenticated protected version, and a 200 catalog read through the minted key.
- **Proof.** Inspect `actions.json`, `result.json`, `version.json`,
  `setup-jellyfin.json`, `setup-jellyfin-cleanup.json`, the screenshot/ARIA
  pairs, `contexts.json`, `cleanup.json`, and `redaction.json` in the emitted
  evidence directory. Each context record must show zero initial cookies; the
  signed-in context lists cookie names, never values.
- **Isolation.** Run `verify.mjs pair` and read `pair-*/result.json`. Require
  `passed: true`, empty `violations`, two `concurrentDoctor` reports taken while
  both groups were alive, and `afterCleanup` of `stopped` for both runs.
- **Failure drill.** Run `verify.mjs auth --inject-failure`. Require exit 1,
  `failure.json` naming the injected failure, masked inputs in `failure.png`,
  all-true `cleanup.json` flags with empty `errors`, a clean
  `setup-jellyfin-cleanup.json`, empty `redaction.json` findings, and a failing doctor.

### Setup refusals and races

Drive these by hand with `verify.mjs serve` and a Jellyfin from
`users_ui_provider.py launch`; `tests/test_jellyfin_setup_api.py` is the unit
proof. Each refusal leaves `/api/auth/status` at `setup_required`, no `Editarr`
key in Jellyfin, and no stored connection.

| Input | Code | On screen |
|---|---|---|
| Ordinary Jellyfin user | `jellyfin_not_administrator` (403) | "That Jellyfin user is not an administrator…" |
| Wrong password | `invalid_credentials` (401) | "Jellyfin did not accept that username and password." |
| Closed port | `jellyfin_unreachable` (502) | "Editarr could not reach a Jellyfin server at that address…" |
| Non-Jellyfin HTTP server | `jellyfin_not_jellyfin` (502) | "That address did not answer as a Jellyfin server…" |
| `ftp://` or a bare host | `jellyfin_bad_url` (400) | Field error on the server URL |
| Any setup after the owner exists | `setup_already_complete` (409) | Returns to sign-in |

Two concurrent submissions create one owner and one key. Setup mints only while
it holds SQLite's write lock after checking again that no owner exists, so the
loser refuses with 409 before it mints. A failure after minting revokes the key.

## Quick Connect

```bash
node .github/skills/verify-editarr/scripts/verify.mjs auth --quick-connect --ports=9900-9919
```

`lib/quick-connect-journey.mjs` runs after setup against the run's Jellyfin and
takes about eleven minutes, because a pending code expires only when Jellyfin
forgets it ten minutes after it starts. The setup screen never offers Quick
Connect, and `GET /api/auth/jellyfin/quick-connect` answers 423 before setup.

- **Off.** Set `QuickConnectAvailable` false in Jellyfin's
  `/System/Configuration`. The sign-in page shows no `Use Quick Connect` button
  and the availability route reports `{enabled: false}`. Turn it back on.
- **Approve.** Click `Use Quick Connect`, read the six digits labelled
  `Quick Connect code`, and approve them as `jf-member` with Jellyfin's
  `POST /QuickConnect/Authorize?code=`. Expect the account menu button, a status whose
  `jellyfin_user_id` is the member's, role `user` from first-sign-in admission,
  and no `*_quick_connect` cookie left.
- **Replay.** Send the spent handle cookie to `POST .../complete`; expect 409
  `quick_connect_consumed` and no session.
- **Cancel.** At 320px, show a code, click `Cancel`, then approve that code in
  Jellyfin. Expect no further status polls, no session, and state `cancelled`.
- **Disabled account.** Disable the member in Editarr. Quick Connect shows
  "This account is disabled. Ask an administrator for help.", the same refusal
  Jellyfin password sign-in gives. Re-enable it.
- **Expiry.** A code shown at the start of the journey turns into
  "This code expired." with `Get a new code` when Jellyfin forgets it, and
  polling stops.

Evidence: `quick-connect-steps.json`, `quick-connect-result.json` (including
`approvalToSignedInMs`), and the `qc-*` screenshot and ARIA pairs.
`tests/test_jellyfin_quick_connect.py` and
`tests/test_jellyfin_quick_connect_http.py` are the unit proof, against a
boundary that answers Quick Connect as the pinned Jellyfin 12 does.

Jellyfin 12 accepts an approved secret again until the request expires. Editarr
never sends the secret to the browser: the browser holds only an HttpOnly,
SameSite=Strict handle, and the server spends an approved attempt once. Pending
attempts live in server memory, so a restart shows the browser an expired code.
Starting codes is limited to ten per client address (an IPv6 /64 counts as one)
per ten minutes and 30 per minute in all; a waiting code is never dropped for a
new one.

## Gotchas

- `/api/auth/status` contains a CSRF token when authenticated; do not archive the
  raw body, cookies, Playwright storage state, or authentication trace payloads.
- Setup stores the run's Jellyfin and its minted key, so authenticated libraries
  return 200: an empty list without `--identity`, the five synthetic libraries with it.
- Setup logs out Jellyfin's own sign-in session; only the `Editarr` key stays.
- The automated run covers setup through a deep link, not an independent root-only
  navigation or backup-restore setup path. Do not claim those entry points passed.
- Wrong-password throttling, remember-me expiry, security changes, and API-key
  authentication are additional cases, not certified by this smoke.
- Every run now owns one Jellyfin. Journeys that swap in a synthetic catalog keep
  the owner's link valid because `lib/synthetic-jellyfin.mjs` reports the run's
  server ID and relays its user routes, and they reconnect the run's Jellyfin
  when they finish.
- No account has an Editarr password. `POST /api/auth/login`,
  `POST /api/auth/register`, `GET`/`PUT /api/settings/registration`,
  `PUT /api/auth/account`, `POST /api/users`, and
  `POST /api/users/{id}/password-reset` answer 404 or 405. Sign-in is
  `POST /api/auth/jellyfin/login` (`SIGN_IN_PATH`) or Quick Connect, and the
  sign-in screen offers no `Create an account`. Every fresh confirmation asks for
  the `Jellyfin password`, which Editarr checks against the configured server
  under the linked Jellyfin user ID. The owner-transfer round trip in `auth` and
  `--users` hands ownership to a member and back, each side confirming with its
  own Jellyfin password.
- `--plugin-interop` runs setup against the plugin's own disposable Jellyfin
  instead of the run's Jellyfin, so the owner's proofs verify there.
