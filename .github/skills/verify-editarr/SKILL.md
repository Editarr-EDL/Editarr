---
name: verify-editarr
description: 'Verify Editarr through its real authenticated web UI and local API. Use after changes to setup, accounts, multi-user access, audit history, settings, imports, saved reviews, exports, scans, or backups; launch isolated source instances and retain browser evidence.'
---

# Verify Editarr

Read [the feature map](./features/README.md) before choosing a journey. This is
the Copilot-compatible project skill, not a Cursor plugin or a mocked UI suite.
Follow root `AGENTS.md`, including backup-first migration requirements. Never
drive a pre-existing Editarr instance or point this harness at existing data.

The primary surface is Editarr's authenticated React web UI and FastAPI service.
The CLI, installed release, native shell, public website, and separate community
Docker service are additional surfaces; this source-development proof does not
certify them. Use their existing test harnesses for changes to those surfaces.

## Launch

Run on macOS or Linux. The helper finds the checkout from its own path, so the
working directory does not matter; the commands below assume the repository root.
Prerequisites are the existing `.venv/bin/python` environment, Docker with the
digest-pinned Jellyfin 12.0.0 image for `auth`, `pnpm` on PATH,
installed `web/node_modules`, Playwright's Chromium binary, `lsof`, and `ps`. Do not install or upgrade anything
silently. Documented initial provisioning is `uv sync --extra dev` and
`pnpm --dir web install`; provision Chromium with
`pnpm --dir web exec playwright install chromium` when needed and authorized.

The helper checks each mode's prerequisites before it creates scratch data. A gap
exits 2 and writes `blocked.json` under `web/test-results/verify-editarr/blocked-*`
with the requirement, the observed detail, and the remedy it did not run. A blocked
run launches nothing and contacts no instance. Invalid arguments exit 64.

Every Python and pnpm child gets `PYTHONPATH` set to this checkout and
`pnpm_config_verify_deps_before_run=false`. A shared virtual environment can hold
an editable install of another checkout, and pnpm 11 otherwise installs before
running Vite. A worktree may reuse another checkout's dependencies read-only
through ignored directories of symlinks (`.venv/{bin,lib,...}` and
`web/node_modules/*` without `.vite`); `checkoutImports` fails if `scanner` would
still load from outside the checkout.

The shortest complete launch/doctor/drive/cleanup proof is:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs auth
```

Append `--reviews` to run the Jellyfin item import and saved-review recipe
after authentication. It generates synthetic media through the app's FFmpeg
boundary, serves it as one movie from the labelled synthetic Jellyfin transport
(`scripts/lib/synthetic-jellyfin.mjs`), imports the first EDL for that item
through the production upload API, then drives MCF upload, rename, duplicate, and
EDL/MCF export from the item's page in the browser. It checks real sidecar bytes, unchanged media,
published-review deletion refusal, and a fresh 320px reopen. `reviews-result.json`,
`reviews-http-statuses.json`, masked screenshots, ARIA snapshots, and copied
synthetic sidecars survive the normal cleanup. No models or live providers are
used; live Jellyfin needs the separate checks below. The run ends with the
[review deletion recipe](./features/reviews-exports.md#review-deletion-with-file-choices-mu-36)
and then the
[export choices and deletion UI recipe](./features/reviews-exports.md#export-choices-and-deletion-ui-mu-37).

Append `--quick-connect` instead to drive Jellyfin Quick Connect sign-in on
the run's Jellyfin: the ordinary member approves the code through Jellyfin's
API and is signed in and admitted, a replayed attempt and a cancelled code sign
nobody in, a disabled Editarr account is refused with the Jellyfin password
sign-in message, Quick Connect off in Jellyfin hides the button, and a pending code
expires when Jellyfin expires it. The expiry wait makes the run about eleven
minutes; see
[Quick Connect](features/authentication.md#quick-connect-jf-04).

Append `--jellyfin-accounts` instead to prove JF-05 Jellyfin-owned accounts on
the run's Jellyfin: first sign-in creates `user` or, for a Jellyfin
administrator, `admin` accounts; the owner's role change outlasts sign-in and
sync; Jellyfin renames, avatars, and disablement reach Editarr; linked profile
edits are refused; import works; and the removed admission and role-sync routes
answer 404. See
[Account admission and sync](features/jellyfin-users.md#account-admission-and-sync-mu-16-jf-05).

With `--reviews`, append `--account-link` to prove after the review-policy journey
that a linked Jellyfin member, who holds personal EDL and MCF publications, gets
no link action and that blocked link proofs move nothing, and then to link
`legacy-member`, a local password account that startup's 0030 disabled, from the
Users dialog; see [account linking](features/account-linking.md).
Append `--account-deletion` to retain, transfer, permanently delete, and bulk-delete
synthetic members through the API and then the Users UI after the review-deletion
journey. Add `--community` (and its `EDITARR_VERIFY_PNPM_CJS` prerequisite) so the UI
bulk step has a member with community history; see
[account deletion](features/account-deletion.md).

Either flag seeds the run's database before launch, because only migration 0030
makes an unlinked account. `scripts/lib/legacy-members.mjs` starts the run's
Jellyfin and synthetic catalog first, then `scripts/legacy_members_fixture.py`
migrates the upgrade fixture's 0009 database offline with the real migrations to
`0029_jellyfin_user_blocks`, links user 1 to the run's Jellyfin administrator as the
`repairs` fixture does, and adds `legacy-member` and `legacy-linker`. Both are
local password accounts with a literal Argon2 string. `legacy-member` owns the
fixture's reviews, rebound to the catalog's `Legacy member film`, and a personal EDL
and MCF that the app's publication writer puts under its local export ID. Real
startup applies only `0030_remove_passwords` and disables both members with cause
`unlinked`. The owner then signs in with Jellyfin instead of driving the setup
screen, and `auth` records `auth-seeded-sign-in-1440` in place of `auth-setup-1440`.

For live interoperability with the public Jellyfin plugin, run:

```bash
VERIFY_PLUGIN_CHECKOUT=../jellyfin-edl-public VERIFY_PLUGIN_PORT=9239 node .github/skills/verify-editarr/scripts/verify.mjs auth --plugin-interop --ports=9220-9229
```

`--ports` bounds only Editarr. Set `VERIFY_PLUGIN_PORT` for a bounded run, or
Docker publishes Jellyfin on a random loopback port.

It launches the plugin's disposable Jellyfin 12.0.0 instance over this run's
synthetic media, runs Editarr's setup against it, signs every account in with Jellyfin, publishes, regenerates, copies, transfers, and deletes
through Editarr's API, and compares each user's served EDL and MCF bytes with the
published files through the plugin's `verify.py matrix`. Missing Docker, images,
or the plugin helper exits 2 BLOCKED. See
[plugin interoperability](./features/reviews-exports.md#plugin-interoperability-mu-44).

For permission-scoped audit history, run:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs auth --audit
```

The [audit recipe](./features/audit-history.md) drives owner, admin, and ordinary
accounts through real APIs and the web UI at 1440 and 320. It proves scoped
counts and details, hidden/guessed UUID refusal, capability removal, secret-free
key history, and account isolation. It calls audit retention's existing `now=`
service boundary in the disposable run only, then refreshes two open views.
It does not advance authentication time or claim live provider coverage.
Replace `--audit` with `--audit-inject-failure` to rehearse failure cleanup.

For personal and authorized named-account community controls, use
`auth --community --ports=9100-9119`. Read the
[community recipe](./features/community-activities.md) for the provisioned pnpm
and browser paths. The double bundles the community service's own validators with
the esbuild and zod in `community-service/node_modules`; without them the run
exits 2 BLOCKED (`communityContract`) before launch. The real authenticated UI and API use the existing
signature-verifying hosted-service double at the outbound transport boundary.
Evidence stays under `web/test-results/mu-32/`; no real contributor is used.

For ordinary-user catalog scans and owner controls, run the executable helper:

```bash
node .github/skills/verify-editarr/scripts/scan-ui.mjs
```

It checks Jellyfin at 1440px and 320px with real authenticated
UI and persisted jobs. A synthetic Jellyfin transport and the existing fake-model
adapter replace only those boundaries. It does not certify inference, downloads,
or live providers. Read [the scan recipe](./features/scans.md) for its assertions,
port range, optional evidence root, and retained failure and cleanup records.

The helper creates an empty OS temporary directory, removes inherited
`EDITARR_*` overrides from its child environment, sets `EDITARR_DATA_DIR` to
the new scratch data directory and `EDITARR_LOG_DIR` to `logs` beside it, and
selects two different unused loopback ports. Without `EDITARR_LOG_DIR`, source
runs write the host's default log directory (`~/Library/Logs/Editarr` on macOS).
It runs the repository's development launcher using its already installed Python:
`.venv/bin/python scripts/run_development.py dev --web-host 127.0.0.1
--web-port WEB_PORT --api-host 127.0.0.1 --api-port API_PORT`. The helper supplies
both numeric ports; do not substitute the usual shared 5173/3348 listeners.
This uses the same launcher as `scripts/dev.sh dev`, without an implicit `uv`
dependency sync. The launch command and paths are recorded in `instance.json`.

Startup performs the real fresh-database initialization. Ready requires the
browser root to serve Vite, `/api/auth/status` to return `setup_required`, forms
authentication to be enabled, and both listeners to belong to the run's process
group. Then the browser must display the Jellyfin setup screen,
`CONNECT YOUR JELLYFIN SERVER`; `serve` prints a `Ready: the Jellyfin setup
screen` line. An HTTP 200 or a database file alone is not readiness. The owner is
created through that screen by a Jellyfin 12 administrator from the run's own
container (see [Authentication](features/authentication.md)), not a database seed
or a bypass. No other provider credentials, media, models, or pre-populated scans
are seeded. The exceptions are `--account-link` and `--account-deletion`, which
seed a 0029 database whose owner is already linked (see below), so their gate is
`unauthenticated` and the owner signs in with Jellyfin.

For a launch-only check, run:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs probe
```

For the Users directory and personal profile journey, run:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs auth --users --ports=8960-8979
```

This tracked journey needs Docker and the digest-pinned Jellyfin 12 image already
available. It drives the original 22 users/profile checks and the MU-30 account
access extension at 1440 and 320 pixels with axe,
fresh API readbacks, and masked captures. The provider adapter reuses the existing
Jellyfin launch/doctor/cleanup helpers. It removes only its recorded labelled
container and cache volume. App and provider evidence survive cleanup. See
[the exact recipe](features/multi-user.md#users-and-personal-profile-ui-mu-25)
for evidence names, failure proof, and the post-cleanup doctor command.
`--users` selects this scenario instead of the earlier API/session fixtures.
`auth` runs at most one of `--users`, `--audit` (or `--audit-inject-failure`),
`--settings`, `--identity`, `--community`, `--plugin-interop`,
`--quick-connect`, and `--jellyfin-accounts`; naming two exits 64 instead of
silently running one. `--reviews` still runs after any of them except `--identity`.
`--ports=A-B` limits all three listeners to that range. Omitting it preserves
ordinary free-loopback-port selection. `probe` and `serve` also accept the range.

For cross-account media isolation, run:

```bash
VERIFY_DOCKER_RUN_PREFIX=<task>-identity- \
  node .github/skills/verify-editarr/scripts/verify.mjs auth --identity --ports=9200-9219
```

It needs FFmpeg and the same Docker and pinned image as `--users`, and exits 2
before launching when any is missing. It runs alone: `--identity` with another
journey flag exits 64. The owner, an admin, and four users, all Jellyfin users
whose library policies differ, each use their own browser context. It
checks fixture library counts and items, hidden-versus-missing refusals,
artwork caching, preview bytes after an account switch and a permission loss,
API-key limits, in-session scope and sync changes, denied deep links, keyboard
sign-in, account switching, and loading and error states at 1440 and 320. Any
browser page error or unexpected API failure fails it. `VERIFY_DOCKER_RUN_PREFIX` begins the container and volume
run label for this and `--users`. See
[the identity recipe](features/identity-isolation.md).

For other feature recipes, start a manually driven instance:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs serve
```

MU-27 adds the [settings-access recipe](./features/settings-access.md).
`auth --settings --controller --ports=9000-9019`
runs its role journeys with a real API-only restart controller and Vite.
`serve` also accepts `--controller` and `--ports=FIRST-LAST`. The port range is
inclusive and applies to both owned loopback listeners. These options do not
change the existing default launcher. Settings evidence is retained under
`web/test-results/mu-27/`. The controller child also gets a scratch `HOME` and
`XDG_CACHE_HOME`; inherited model-library cache variables are removed. Setting
`EDITARR_DATA_DIR` alone does not isolate the native model cache. The journey
requires a cancellation marker inside scratch before sending model deletion.
It checks the archive download, which offers no restore, and enforced-filter
saves with a wrong then correct Jellyfin password at both widths.
Archive fixtures run before filter-default mutations. Post-edit archive
validation has a separate backend limitation recorded in the recipe.

To prove that runs stay isolated, start two `auth` runs at once:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs pair
```

`pair --ports=A-B` gives each run one half of the range.

Each run stops after its doctor passes. The coordinator inspects both live
instances, then releases them to drive their journeys concurrently. It fails if
the runs share a data directory, port, listener, process group, origin, or
evidence directory, if a browser context starts with cookies, if either run fails
its journey, cleanup, or redaction scan, or if either doctor still passes after
cleanup. A post-cleanup doctor failure counts as `stopped` only when it reports
that the recorded process group is not running; any other doctor error is a
violation.

To prove a supported upgrade through real startup, run:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs upgrade --ports=9240-9259
```

It writes the synthetic 0009 database from `build_activity_database` in
`tests/activity_migration_support.py` (fixture `mu03-activity-fixture-2`) into
scratch before launch, then adds MU-06 community state with `add_community_state`
from `tests/community_identity_migration_support.py` (fixture
`mu06-community-fixture-1`). That database has the MU-02 owner, live session, and
readable key, plus two saved reviews, a consent-waiting queued job that the queue
never starts, an imported scan, and legacy EDL and MCF export records whose
`Movie.edl` and `Movie.mcf` sidecars sit beside a synthetic `Movie.mkv` in the
run's scratch. It also has an enrolled community connection, a receipt, a vote,
and a publish operation, an identity with a pending key recovery in the legacy
`secrets/community` directory, and a legacy authentication audit log. The instance
gets the reserved `https://community.test` service; nothing contacts a community
service. Startup upgrades to `0030_remove_passwords`. The fixture owner has no
Jellyfin link and no Jellyfin is configured, so it comes out disabled with cause
`unlinked` and nobody can sign in (plan Appendix C).

The helper waits for the `unauthenticated` gate and checks the complete upgrade
journal and retained snapshot. The old session cookie must get 401, the owner's
key 403 (the refusal code, `account_disabled`, is recorded), and
`POST /api/auth/login` 404 or 405.
`/api/auth/status` must be `unauthenticated` with `sign_in` `{jellyfin: false}`.
No `$argon2` bytes may remain in the database file. `users` must have no
`password_hash`, `local_credential`, or `password_changed_at` column,
`account_settings` no `registration_mode`, and the only user row must be
`[1, owner, disabled, unlinked]`. A read-only database inventory must show every
review, scan, and export record owned by or credited to user 1, an empty queue,
the Other Files item archived as `manual_files` `Movie.mkv`, and no personal filter
values. It must also show one community identity owned by user 1, the connection
under its credential reference, every operation owned by user 1 with no actor, the
owner's key sealed, no stored audit events, the all-sources local media default,
no archived access rows, and no `media_access_exceptions` table. With no owner
session, the identity files stay unchanged in `secrets/community` and no
`secrets/community-identities/` home is made. At 1440 and 320 pixels the sign-in
screen must say "Sign-in is unavailable because Editarr is not connected to a
Jellyfin server." with no sign-in method radio, no "Create an account", and no
overflow (`upgraded-locked-out-*` screenshots). The legacy audit log must keep its
original bytes, no audit event may be stored, and no run secret may appear in
either. The sidecar and media hashes and modification times must be unchanged at
the end. That first run proves the lockout; the second checks what needed an
owner session or a usable key.

When the first run passes, `scripts/lib/upgrade-linked-journey.mjs` starts a
second run with its own Jellyfin 12 and the same seed. The seed is then migrated
offline with the real migrations to `0029_jellyfin_user_blocks`, and user 1 is
linked to the run's Jellyfin administrator with the run's minted key
(`review_repairs_fixture._link_owner`). Real startup applies only
`0030_remove_passwords` from its snapshot, and the owner stays active
(`[1, owner, active, null]`, one provider identity). The data still travels from
0009 to head through the real migrations. The old cookie must get 401. The
migrated key must get 200 with schema `0030_remove_passwords`, read a sealed and
revealable status that never carries the key, and be refused its own reveal with
`interactive_session_required`. With the key, `GET /api/archive` must list only
the Other Files `Movie.mkv` with both reviews, the three scans (the queued one
`cancelled`), and both legacy exports. A manual-path EDL upload must answer 400
`jellyfin_item_required` and write nothing. The inventory must match the first run
apart from the active owner. A personal filter rule must save, refuse a replayed
revision with `stale_settings_revision`, read back, and land in the database,
while enforced policy by key answers `interactive_session_required`. At 1440 a
wrong Jellyfin password must get 401. Then at both widths the Jellyfin
administrator signs in as user 1 and reveals the migrated key after confirming
its Jellyfin password. The community status (own and named route) must show the
migrated installation and key with a pending recovery, and Archive must show
both reviews with no action and no overflow (`upgraded-linked-owner-*`,
`upgraded-archive-*`). After the first owner session the identity files must sit
byte for byte in the owner's `0700` home, the incoming directory must be empty,
and there must be exactly one home. Stored audit rows must be one failed
anonymous sign-in, then a sign-in and a reveal per width, all attributed to user
1. The legacy file log must keep its bytes and then gain one `auth.login` line per
Jellyfin sign-in: the failure (401 `invalid_credentials`) and two successes for
user 1. No run secret may appear in either, and
the sidecars must be unchanged. `upgrade` exits 0 only when both runs pass, and
each run keeps its own evidence directory.

For deleted-scan migration and review resolution, use the
[orphan recovery recipe](./features/review-repairs.md):

```bash
node .github/skills/verify-editarr/scripts/verify.mjs repairs --ports=9240-9259
```

This seeds the migration suite's exact pre-account community-era shape without a
version table, rebinds its media item to a synthetic Jellyfin movie that the run
serves on loopback (an Other Files item would be archived). Each width starts a
run Jellyfin and mints an Editarr key in it. The fixture upgrades the seed with
the real migrations to `0029_jellyfin_user_blocks`, links user 1 to the Jellyfin
administrator, and stores the server URL and key, so real startup applies only
`0030_remove_passwords` from its snapshot and the owner stays active. Two
fresh runs prove the repair list, dialog, no-identity refusal, manual-cue recovery,
editing, review deletion, and community-scan deletion at 1440 and 320 pixels with
axe. Evidence stays under `web/test-results/mu-b2r-proof/`. No community identity,
external network service, or model download is created; the Jellyfin container
stops with each run.

For backup restore into an empty installation, use the
[backups recipe](./features/backups.md):

```bash
node .github/skills/verify-editarr/scripts/verify.mjs restore --ports=9200-9219
```

It seeds a multi-user source whose accounts are Jellyfin users on a run Jellyfin,
downloads a full backup through Settings, restores it
through onboarding on a second empty instance, signs in with Jellyfin after a
restart, compares inventories and identity hashes, links the restored unlinked
account from Users, rolls back an injected interrupted activation
before a CLI restore, and proves the non-empty refusals. Evidence stays under
`web/test-results/verify-editarr/restore-*`, or under `VERIFY_EVIDENCE_ROOT`.

The same recipe lists the upgrade matrix over every revision below head and the
installed-artifact checks: `scripts/verify_installed_release.py --recovery` against
a disposable wheel or native runtime, and `web/e2e/run-production-e2e.mjs` behind a
real TLS proxy. Set `EDITARR_VERIFY_PORTS` to the run's port range for both; each
issued port keeps its restart successor inside that range.

Use a persistent terminal for `serve`. It prints the exact `Evidence:` directory
and owned browser URL. Open only that URL in a new browser context. Record that
directory as `EVIDENCE` for the commands below; it contains `instance.json` with
the actual `origin` and `data` paths. Do not guess either from another run.
Stop `serve` with Ctrl+C in its owning terminal, or SIGTERM to the helper PID
returned by the process tool. The helper owns teardown on success and failure.

## Doctor

While `serve` is running, this checks application state without mutations or
direct writes to evidence files. Its HTTP GET produces normal server access logs:

```bash
node .github/skills/verify-editarr/scripts/verify.mjs doctor "$EVIDENCE"
```

It checks the recorded process group, ownership of both listening ports, the
loopback origin, and forms authentication. The anonymous status must carry a
boolean `sign_in.jellyfin` and no `sign_in.registration`. It lists every log file the group
holds open (`logFiles`) and fails if one lies outside the run's scratch or
evidence directory. It fails if the checkout `HEAD` differs from the launch
revision and reports uncommitted changes as `dirty` without rejecting them. Its
HTTP client is deliberately anonymous, so `setup_required` or `unauthenticated` is expected,
even when a separate browser is signed in. Re-run when the page, auth, or network
looks wrong. A stopped instance or ownership mismatch prints `FAIL doctor:` and
exits 1; never recover by attaching to another listener. The automated `auth` run
also records `doctor.json` and verifies authenticated `/api/system/version`
against `web/package.json`.
Source builds may report unknown build metadata; `version.json` preserves that
fact rather than inventing an installed-build identity.

## Drive

Use Playwright and the exact roles/labels in the matching feature file. The helper
uses the existing `@playwright/test` dependency and Chromium, without replacing
API responses. `auth` records the Jellyfin setup screen at 1440px, setup through
the run's Jellyfin administrator, sign-out, and persisted Jellyfin sign-in in a
fresh 320px browser context. It asserts the original
`/#/settings/general` deep link, protected API authorization, version, no page
overflow, no uncaught page errors, and an on-disk scratch database.

For manual recipes, use available browser tools against the printed origin or
Playwright from `web/`. In the snippets, `page` is the run-owned Playwright page,
`origin` and `data` are read from that run's `instance.json`, and `evidence` is
its `Evidence:` directory. Use generated or synthetic fixture inputs only. Do not
import `mockApi`, use `page.route(...fulfill...)`, or drive test-only endpoints
for a real-app proof. Existing `web/e2e` specs supply selector patterns but mostly
mock APIs; a passing default browser suite is not an integrated app proof.

Multiple runs can coexist because ports, data, process groups, and browser
contexts are separate; `pair` proves it. Port selection has an unavoidable bind
race: strict Vite binding and doctor ownership checks fail that run instead of
reusing a server. Do not edit the checkout while a proof is running; development
reload can change the code under observation.

## Fixtures

Create fixtures through existing production boundaries, never by seeding the
database or adding an app bypass. The exceptions need an old schema, which cannot
come from the current app, so they reuse the migration tests' fixture: `upgrade`,
`repairs`, and the unlinked accounts `--account-link` and `--account-deletion`
need, which only migration 0030 makes from a 0029 database.

- **Synthetic media.** With `serve` running, `verify.mjs media "$EVIDENCE"`
  writes `synthetic-av-v1.mkv` to the run's scratch `media/` directory and
  `media.json` to its evidence. FFmpeg comes from the app's `resolve_ffmpeg` and
  tracks from its `probe_audio_tracks`: 4 seconds of 320x180 video, then audio
  stream 1 `eng`/`Main` (default) and stream 2 `spa`/`Commentary`. Reruns produce
  identical bytes for one FFmpeg build, so `sha256` is the stable media version;
  the app's `file_version` is stat-bound and changes per file. Cleanup removes it.
- **Browser clock.** `auth --fixed-clock` pins `Date` in the sign-in context to
  `FIXED_TIME` (2026-01-01T12:00:00Z) with `installFixedClock` from
  `scripts/lib/fixtures.mjs`. Timers still run, and server time is unaffected.
- **Multiple users.** Every member is a Jellyfin user created on the run's
  Jellyfin with `addJellyfinUser` in `scripts/lib/run-jellyfin.mjs`. Its first
  Jellyfin sign-in (`admitJellyfinUser`) or an owner import creates the Editarr
  account. `auth` admits, signs in, and refuses real nonowner accounts through
  the API and two browser contexts before any flagged journey runs.
- **Server clock.** The source app uses wall-clock time. Prove expiry, cadence,
  grace, and retention at the existing `now=` keyword boundary of the services in
  `scanner/security/auth.py` and `auth_repository_sessions.py`, using the same
  instant as `_NOW` in `tests/test_auth_services.py`. The real-app journey then
  drives only the present-time outcome.

## Evidence

Artifacts are retained under `web/test-results/verify-editarr/editarr-verify-*`,
outside disposable application data. Every mode, including `repairs`, `restore`,
`pair`, and `scan-ui.mjs`, uses `VERIFY_EVIDENCE_ROOT` (relative to the checkout)
instead when it is set. The exact directory is printed at launch.
The automated proof writes `instance.json`, `server.log`, `doctor.json`,
`actions.json`, `http-statuses.json`, `version.json`, `browser-errors.json`,
`contexts.json`, `result.json`, screenshots and ARIA snapshots for setup,
saved-account state, sign-out, and fresh-context sign-in, followed by
`cleanup.json` and `redaction.json`. Failure writes `failure.json` and, when
available, a screenshot with inputs masked. `pair` writes
`web/test-results/verify-editarr/pair-*/result.json`, one log per run, and the
rendezvous files; each run keeps its own evidence directory.

Record the feature ID and every entry point attempted. Capture before/action/after,
not just a final page. Mutation proof needs an independent observable read: a new
browser context, protected GET, parsed downloaded archive, or actual sidecar bytes
and hash. Assert the outcome and refusal/error paths separately. Do not replace a
missing precondition with a mock or a skipped assertion. In the empty baseline,
Jellyfin libraries return 423 before setup, 401 after sign-out, and the explicit
409 `Configure Jellyfin before browsing libraries` after sign-in. This is not a
successful catalog journey; the protected version endpoint independently proves
authenticated access with HTTP 200.

Never retain passwords, cookies, CSRF tokens, raw auth bodies, API keys, or browser
storage-state files in proof artifacts. Screenshots and snapshots must not expose
credentials; mask or omit credential controls in manual journeys. Archives contain
authentication and signing material: keep them private, do not commit them, and
retain only when the authorized proof needs them. The helper records statuses and
paths, not HTTP bodies/headers or auth traces. Every helper screenshot paints
all inputs `#FF00FF`, which covers password and API key fields, and every ARIA
snapshot replaces run secrets with `[redacted]` before it is written. After
cleanup the helper searches every retained text file for the run's passwords,
session cookies, CSRF tokens, and fixture keys and fails if any appears;
`redaction.json` records the counts. That scan does not read PNG pixels, so it
cannot certify screenshots. Masking is the control, and the helper tests sample
a masked pixel in a real browser. Inspect manual screenshots yourself. Browser
requests outside the owned origin are blocked and fail `auth` and `upgrade`;
backend outbound traffic is **not measured**.

This is not a dry-run: `auth` starts a disposable Jellyfin 12 container, mints a
real `Editarr` key in it, and creates a real owner, sessions, key material, and a
database in scratch. The container stops after the run. It does not run scan inference, acquire model weights,
activate community sharing, or test a live provider. Do not infer safety from a
mode name. For any new dry-run recipe, compare its actual files/network/git refs
before and after. Mocks are allowed only at an existing production boundary and
must be labeled; they cannot certify the mocked system.

The proof uses the source Vite/Uvicorn stack, existing checkout dependencies, and
headless Chromium. An installed/native target will not perform this Vite launch,
use the source virtual environment, or inherit these process controls. Packaged UI,
TLS/proxy/cookie policy, installed migrations, native behavior, non-POSIX cleanup,
inference, and external-service journeys remain unverified by this skill's smoke
run. The installed TLS harness lives at `web/e2e/run-production-e2e.mjs`; it needs
a separately provisioned installed artifact and proxy, not this scratch launch.

## Cleanup

`auth` and `probe` always close their browser, terminate only their recorded
process group, wait for exit, and then remove their own scratch directory.
`serve` does the same on Ctrl+C/SIGTERM. Every journey, `upgrade`, `repairs`,
and `restore` included, also cleans up on Ctrl+C or SIGTERM: `stopOnSignals`
stops each registered run's group and closes its browser, the journey fails,
exits 1, and its normal cleanup runs. A run created after the interrupt refuses
to launch. A failed browser close does not skip the later steps: cleanup still
stops the group, writes `cleanup.json` and `redaction.json`, and then fails with
every collected error. Cleanup escalates to SIGKILL only for the same owned group,
never a process name. An `EPERM` signal probe counts as stopped only when a
successful numeric `ps` listing proves the recorded group absent; live or
unprovable groups still fail closed. If that group survives, scratch is retained
and cleanup fails. Do not delete data while a writer survives. A hard kill or host
crash can bypass cleanup; use `instance.json` and verify ownership before any
manual recovery, never `pkill`, port-based mass killing, or deletion of other runs.

After every run, including a failed attempt, read `cleanup.json` and confirm
`processesStopped`, `scratchRemoved`, and `evidenceRetained` are true and
`errors` is empty. Confirm the screenshots and JSON proof still exist in the
printed directory. `doctor` must fail against a cleaned-up instance. Retain failure
evidence too. Generated fixtures belong in scratch; exported proof copies belong in
the evidence directory.
`auth --inject-failure` stops after the credentials are typed. Use it to rehearse
this check: it exits 1 with `failure.json`, a masked `failure.png`, and complete
cleanup and redaction records.

## Helpers

[scripts/verify.mjs](./scripts/verify.mjs) is executable. Its complete command set
is `auth [--fixed-clock] [--inject-failure]` with the journey flags above, `probe`,
`serve [--controller]`, `upgrade`, `repairs`, `restore`, `pair [--ports=A-B]`,
`doctor EVIDENCE_DIRECTORY`, and `media EVIDENCE_DIRECTORY`, as shown above.
Modules under `scripts/lib/` own prerequisites, the instance lifecycle, masked
capture, the auth and upgrade journeys, the pair coordinator, and fixtures.
Journeys read `scanner.db` only through `query` in `lib/database.mjs`, which opens
it read-only, waits up to 10 s for a writer's lock, returns plain rows, and
closes it. They launch Chromium only through `launchBrowser` in `lib/instance.mjs`,
which leaves interrupts to `stopOnSignals`; `lib/instance-signals.test.mjs`
fails on any other `chromium.launch(`. A `--ports` pool bind-checks each port and
never reissues one within its process, but nothing coordinates processes: concurrent
runs must use disjoint `--ports` and `EDITARR_VERIFY_PORTS` ranges.
`scripts/synthetic_media.py` is the media generator. `scripts/media_access_live.py`
runs the opt-in live Jellyfin media-access check with its own launch, doctor,
evidence, and cleanup that removes only containers and volumes whose run label and
recorded identity match; its recipe is [features/media-access.md](./features/media-access.md).
`scripts/jellyfin_accounts_live.py` reuses that launch, doctor, and cleanup for
the live Jellyfin account admission and sync check in
[features/jellyfin-users.md](./features/jellyfin-users.md).
`scripts/users_ui_provider.py` adapts the same lifecycle for `auth --users`
and, with `--libraries`, for `auth --identity`. It does not install dependencies, start Docker, configure
models, or modify app source. A nonzero exit is failure (1), invalid usage (64),
or BLOCKED (2), never a request to reuse an existing server.

Test the helpers with the command below. They include real launches with injected
failures and interrupts. `node --test` runs files concurrently, so each
server-launching file takes its own slice of `VERIFY_PORTS` from
`lib/test-ports.mjs`; register a new one there.

```bash
cd .github/skills/verify-editarr/scripts
VERIFY_PORTS=9250-9259 node --test verify.test.mjs verify-dispatch.test.mjs lib/*.test.mjs
```

Keep the map current with `/maintain-verification-skill`. Use this `.github/skills/`
location when that upstream workflow refers to `.cursor/skills/`.
