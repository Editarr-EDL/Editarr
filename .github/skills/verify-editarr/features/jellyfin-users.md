# Jellyfin Users

`scanner/integrations/jellyfin_users.py`
reads verified Jellyfin identity and media access. Jellyfin sign-in
(`POST /api/auth/jellyfin/login`), account import and sync, and media decisions
call it. This page covers the adapter's deterministic checks and the live
`jellyfin_accounts_live.py` helper under "Account admission and sync". The
browser side runs in `verify.mjs auth --users` ([Multi-user journeys](./multi-user.md))
and `verify.mjs auth --identity` ([Identity isolation](./identity-isolation.md)).

## What exists

- `JellyfinUserAdapter(server_url, api_key, *, transport=None, clock=..., limits=...)`
  reuses the catalog client's URL validation and `MediaBrowser` header. It
  builds its own HTTP client, so callers and tests can replace only the
  transport. Every operation rereads the server ID and refuses any server but
  the first one the adapter saw. No request follows a redirect or runs an auth
  flow. Like the catalog client, it honors `HTTP_PROXY`, `HTTPS_PROXY`,
  `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, and `SSL_CERT_DIR`, and never
  reads `.netrc`. `ResponseLimits` bounds wire and decoded
  body bytes and each request's time.
- `authenticate(username, password)` proves credentials with
  `/Users/AuthenticateByName` and returns `CredentialProof(user, session)`.
  The user's identity is the server ID and user GUID. `session` is `ended`, or
  `may_remain` when logout still failed after bounded retries.
- `verify_user(identity)` returns a snapshot, `UserDeleted`, or `Unverified`.
  Deletion needs the configured server's ID, a 404 for the user, a valid user
  list without that ID, and the same server ID read again afterwards.
- `users()` lists snapshots for import. Absence from the list is not deletion.
- `item_access(identity, item_ids)` allows an item only when Jellyfin opens it
  for that user with `/Items/{id}?userId=...`, one request per item. A
  server-key query only labels the rest `restricted` or `not_found`. Jellyfin
  12's batch `/Items?Ids=...&userId=...` skips library access, so the adapter
  never uses it to allow.
- `avatar(identity, tag)` reads `/UserImage` through the server key and reads
  the server ID again before returning.
- `Unverified(reason)` carries no timestamp. Reasons are `timeout`,
  `unreachable`, `service_error`, `invalid_response`, `key_rejected`,
  `credentials_rejected`, `server_mismatch`, `redirected`, and
  `response_too_large`.

## Deterministic checks

The boundary is a local HTTP server on 127.0.0.1, ports 8640 to 8659, serving
fixture `editarr-jellyfin-users-1` from
`tests/fixtures/multi-user/jellyfin-users-v1/manifest.json`. It is a SYNTHETIC
EXTERNAL BOUNDARY shaped after Jellyfin 12.0.0. It does not prove live
Jellyfin behavior.

```bash
T=$(mktemp -d) && mkdir "$T/data" "$T/logs"
PYTHONPATH="$PWD" EDITARR_DATA_DIR="$T/data" EDITARR_LOG_DIR="$T/logs" \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_jellyfin_user_adapter.py tests/test_jellyfin_user_outages.py \
  tests/test_jellyfin_user_transport.py tests/test_jellyfin_user_environment.py
rm -rf "$T"
```

- `test_jellyfin_user_adapter.py` covers credential proof, a name reused by
  another account, upstream role changes, disablement, deletion, server
  replacement, restricted item access, library and parental policy, the user
  list, avatars, and the absence of email and timezone.
- `test_jellyfin_user_outages.py` runs every recorded fault against every
  operation. It also covers a proxy 404, a 404 for a listed user, a rejected
  API key, an outage after a success, and a secret sweep over outcomes,
  errors, logs, and recorded requests.
- `test_jellyfin_user_transport.py` covers the transport-only injection,
  unread credential challenges, gzip bodies that
  would expand past the budget, late replies, malformed or deeply nested JSON,
  and text that is not valid Unicode.
- `test_jellyfin_user_environment.py` runs the adapter and the catalog client
  through an environment proxy stub, past it with `NO_PROXY`, and against a
  local HTTPS server whose generated CA is trusted only through
  `SSL_CERT_FILE`. A `.netrc` file in `HOME` never changes the adapter's
  `Authorization` header.

## Fixture states

`tests/jellyfin_user_boundary.JellyfinBoundary(api_key, passwords)` serves one
state, optionally under a named fault. Set `state` and `fault` between calls.

- `baseline`. Avery is an administrator. Blake has an avatar. Casey may see
  only the Family library, parental rating 10 or lower, without the
  `late-night` tag, and no unrated movies. Of the four items, Casey may open
  only the family film; the others are blocked by library, tag, and rating in
  turn. Drew and Emery are ordinary users.
- `changed`. Avery is no longer an administrator. Blake is an administrator
  renamed to "Casey". Casey is renamed to "Casey Archive". Drew is disabled.
  Emery is deleted.
- `replaced`. Another server ID answers. It has a user with Blake's GUID and
  another user named "Casey".
- Faults. `timeout`, `refused`, `starting` (503), `server-error` (500),
  `gateway-page` (HTML 200), `truncated-json`, `proxy-not-found` (404 under
  `/Users`), and `stale-user-route` (404 for `/Users/{id}` only).

## Live-server check

A live check needs a disposable Jellyfin container with synthetic users and
media; it never uses a real server or credentials. Use the plugin verifier’s digest-pinned Jellyfin 12 image. Repeat a live check when a Jellyfin
upgrade changes these routes. That check found that Jellyfin 12's batch
`/Items?Ids=...&userId=...` ignores library access, which the fixture now
reproduces through `idsQueryItems`.

## Account admission and sync

`scanner/security/jellyfin_accounts/` turns those reads into linked accounts:
first sign-in creation (`POST /api/auth/jellyfin/login` or Quick Connect),
import (`/api/jellyfin/users`), the five-minute and manual sync
(`/api/jellyfin/sync`), linked-account fresh authentication, and the media
evidence cache. The contract is "Jellyfin accounts and admission" in
docs/multi-user-contracts.md.

Every Jellyfin user's first sign-in creates an account; there is no
admission mode, and `/api/settings/jellyfin-admission` and
`POST /api/users/{id}/role-sync` answer 404. Migration `0028_jellyfin_admission`
drops the stored mode and records one system `settings.jellyfin_admission.change`
audit event (`previous: import_only`) on installs that had import-only admission.
A Jellyfin administrator starts as Editarr `admin` and an ordinary user as
`user`, whether the account comes from first sign-in or import. After that only
the owner changes the role (`PUT /api/users/{id}/role` with fresh proof); sign-in
and sync never do. Jellyfin owns a linked account's username, display name, and
avatar: each sign-in, sync, link, and import refreshes them, `read_only_fields`
lists all three, and edits to them answer 409 `linked_profile_field`. The username
is Jellyfin's `Name`; while another account holds it the account keeps its
username, `account.jellyfin.state` records `username: unavailable` once, and the
next sign-in or sync tries again. The profile shows the
Jellyfin username and "Avatar from Jellyfin" without edit controls; email,
timezone, and language stay editable. Sync still disables accounts that Jellyfin
disabled or deleted and ends their sessions.

Browser journeys: `verify.mjs auth --users` imports Jellyfin users and
signs one in through the Jellyfin form. `verify.mjs auth --jellyfin-accounts`
(`scripts/lib/jellyfin-accounts-journey.mjs`) starts from a database at
`0027_jellyfin_owner` with import-only admission stored (`seedImportOnlyDatabase`),
then creates users through the run’s Jellyfin 12 admin API and checks: (1) an ordinary user's first sign-in creates a `user`; (2) the
upgrade journal names `0027_jellyfin_owner`, that sign-in worked after it, and
the audit history holds one system `settings.jellyfin_admission.change` event
(`previous: import_only`); (3) an
administrator's first sign-in creates an `admin` shown in Users; (4) the owner's
change of that admin to `user` survives the user's next sign-in and a manual
sync; (5) a Jellyfin rename reaches the display name, the username, Users, and
the profile after sync; (6) a new Jellyfin avatar changes the avatar digest and
served image at the next sign-in; (7) linked display name and avatar edits
answer 409, a username edit answers 422 because no route
renames an account, and no profile control offers them; (8) import of a
never-signed-in administrator creates an `admin`; (9) Users and the linked
profile fit 320px with no admission control; (10) a Jellyfin-disabled user is
disabled and signed out at sync. Step 11 is an extra check that the admission
and role-sync routes answer 404. Additional checks cover (12) a
Jellyfin user whose name another linked account still holds (Jellyfin renamed
the holder, but no sync has run) signs in with no username, one
`account.jellyfin.state` event says `username: unavailable`, the syncs that
rename the holder hand the claimant the name, and a Jellyfin rename reaches the
username at the next sign-in; (13) deleting with "Allow them back" lets the next sign-in
create a fresh account; (14) deleting with "Block them" at 320px refuses password
sign-in with the blocked message, Quick Connect with 403 `jellyfin_user_blocked`,
and import with `blocked`, audits both sign-in refusals, and lists the user under
"Blocked Jellyfin users"; (15) the owner's Unblock with a fresh password lets the
next sign-in create a fresh account. It writes `jellyfin-accounts-steps.json`,
`jellyfin-accounts-result.json`, and masked screenshots.

```bash
node .github/skills/verify-editarr/scripts/verify.mjs auth --jellyfin-accounts --ports=9950-9999
```

Deterministic checks, on a fixed clock against the boundary above:

```bash
T=$(mktemp -d) && mkdir "$T/data" "$T/logs"
PYTHONPATH="$PWD" EDITARR_DATA_DIR="$T/data" EDITARR_LOG_DIR="$T/logs" \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_jellyfin_account_states.py tests/test_jellyfin_account_sync.py \
  tests/test_jellyfin_account_evidence.py tests/test_jellyfin_accounts_http.py \
  tests/test_jellyfin_sync_migration.py
rm -rf "$T"
```

Live check. `scripts/jellyfin_accounts_live.py` reuses the media-access
helper's launch, doctor, and cleanup for the pinned Jellyfin 12.0.0 image,
creates synthetic users `dana` (administrator), `erin`, and `finn`, and drives
the real services on a scratch database. Its owner is set up through Jellyfin
setup (`set_up_owner`) by the container's `root` administrator, which links the
owner and stores the minted key. It covers import, first sign-in, three
overlapping sign-ins (one account; a superseded one may be refused
`jellyfin_unavailable`, listed as `superseded`; the database then holds four
identities, `root`, `dana`, `erin`, and `finn`), upstream disable and delete with session revocation,
upstream re-enable (the disabled user's account returns without its revoked
sessions and signs in again, the deleted one stays disabled), and
an outage (the container stopped) across the 24-hour grace on a fixed clock.

Set `VERIFY_JELLYFIN_PORT` to publish Jellyfin on that loopback port instead of
a random one.

```bash
T=$(mktemp -d) && mkdir "$T/data" "$T/logs"
PYTHONPATH="$PWD" EDITARR_DATA_DIR="$T/data" EDITARR_LOG_DIR="$T/logs" \
  .venv/bin/python .github/skills/verify-editarr/scripts/jellyfin_accounts_live.py run
# Rehearse cleanup:   ... jellyfin_accounts_live.py run --inject-failure
# Repeat cleanup:     ... jellyfin_accounts_live.py cleanup EVIDENCE_DIRECTORY
rm -rf "$T"
```

Evidence (`instance.json`, `doctor.json`, `results.json` or `failure.json`,
`cleanup.json`, `redaction.json`) stays under
`web/test-results/verify-editarr/jellyfin-accounts-<run>/` after the
container, volume, and scratch are removed. Exit 0 pass, 1 failure, 2
blocked, 64 usage.
