# Multi-User Journeys

Multi-user support is implemented,
and every launch admits every active account since MU-42 removed the temporary
barrier. Each sub-feature below names the tasks that built it and its proof. A
sub-feature counts as verified only when its recipe passes on the revision under
test. Mocked web specs, unit tests, and this map do not count as a pass.

## Sub-features

Each ID lists the tasks that implement it. MU-43 to MU-45 run the final journeys.

- `mu-contracts` (MU-01). Verified by deterministic checks, not a journey. The
  capability inventory, route/action inventory, and export layout fixtures are
  described in `docs/multi-user-contracts.md`. Run the
  command under "Contract checks" below. Importing the app writes data files,
  so it always gets a scratch `EDITARR_DATA_DIR`.
- `mu-owner-migration` (MU-02, MU-07, MU-45). The existing administrator becomes
  the sole owner. The multi-user upgrade from a released schema (unversioned
  through `0009_readable_api_key`) and every restore revoke browser sessions and
  keep API keys; later development revisions keep sessions (ruling
  wave16-upgrade-session-revocation).
  MU-02 deterministic proof: `tests/test_account_migration.py` (every supported
  source revision, activity-only setup race, injected and hard-exit failures,
  unknown schema, 0009 backup restore), `tests/test_account_schema_admission.py`
  (a head database whose `users` lost AUTOINCREMENT is refused). Real proof:
  `verify.mjs auth` on fresh data, and `verify.mjs upgrade` for
  the synthetic 0009 database upgraded by real startup with masked captures.
- `mu-activity-ownership` (MU-03). Revision `0011_activity_ownership` assigns
  existing reviews, scans and jobs, imports, and export records to the owner,
  or leaves them awaiting owner setup, and marks existing exports `legacy`.
  Revision `0012_personal_filter_values` adds empty per-user copy rules, copy
  severities, and live severity overrides.
  Deterministic proof: `tests/test_activity_migration.py` (every supported
  source revision including 0010 and 0011, activity-only setup race, injected
  and hard-exit failures in 0011 and 0012, partially applied and modified
  schemas), `tests/test_activity_ownership.py` (scope, target, actor, policy
  owner, and author constraints, filter and media policy storage, and existing
  owner workflows on an upgraded database), and
  `tests/test_personal_filter_values.py` (per-user round trips independent of
  instance changes, refused values, and full and settings restore with and
  without local accounts). Real proof: `verify.mjs upgrade` seeds
  `mu03-activity-fixture-2` at 0009 and checks the database inventory, the
  archived Other Files row, and unchanged sidecar bytes.
- `mu-sign-in` (MU-07, MU-19, JF-06). Each user signs in with Jellyfin to their
  own session. Fresh interactive authentication with the Jellyfin password guards
  sensitive actions.
  MU-07 deterministic proof: `tests/test_auth_sessions_http.py` (two users with
  separate cookies, CSRF, and sign-out; fixed-clock idle and absolute limits for
  ordinary and remembered sessions; disabled accounts and revoked sessions) and
  `tests/test_auth_sessions_server.py` (the production app in its own process
  on an OS-assigned loopback port, three users signing in, reading status, and
  signing out at once for three rounds). No address or mode bypass remains.
  Real proof: `verify.mjs auth` for the owner journey. MU-19 adds `scripts/lib/session-journey.mjs` to
  `auth`. Its members (journey-alpha, journey-beta, journey-gamma) are Jellyfin
  users on the run's Jellyfin, admitted by their first Jellyfin sign-in. In one
  browser it proves a failed sign-in, journey-alpha signing in with its Jellyfin
  password to the requested deep link, a denied deep link, a permission refresh
  that hides controls, a revoked session, an account switch that clears the
  previous user's storage, a Jellyfin user with no account signing in at 320px
  and getting one (`sessions-first-jellyfin-sign-in-320`) with no
  `Create an account` offered, and two concurrent contexts with separate state,
  with axe at 1440 and 320. Evidence lands in `sessions-*.png`,
  `sessions-axe.json`, `sessions-api-requests.json`, and `sessions-result.json`.
  At 1440 and 320 an open tab also follows another tab's identity change,
  permission change, sign-out, and the account's disablement
  (`sessions-tabs-*`). Each viewport then drives
  the real API-key `Jellyfin password` dialog through wrong confirmations to 429, checks
  `reauthentication_throttled` and a positive integer `Retry-After`, and checks
  the displayed wait, cleared password, and disabled submission. Captures use
  `sessions-tabs-*` and `sessions-fresh-*`; `sessions-throttles.json` retains
  only safe statuses, codes, retry seconds, and assertion outcomes. Fixed-clock
  HTTP and Vitest tests prove partial-window expiry and retry re-enablement
  without waiting out the production window in the browser journey.
  Fix round 2 adds deterministic regressions in `auth/auth-gate.test.tsx`
  for clearing private tab titles on sign-out, disablement, and user
  replacement. `auth/auth-login.test.tsx` checks throttling feedback,
  cleared credentials, blocked resubmission, and a sign-in that offers only
  Jellyfin with no registration; the fixed-clock
  `tests/test_users_permissions_http.py` checks rounded `Retry-After` and expiry.
  `review/review-preview.test.ts` checks URL cleanup before a response, after
  a response, and before React commits the preview. Run these with
  `pnpm --dir web exec vitest run src/auth src/review/review-preview.test.ts`
  and the focused HTTP tests before repeating the real auth journey above.
- `mu-api-keys` (MU-13). One personal key per account, following its current
  permissions. Reveal and issue need a session and the Jellyfin password in the
  same request; every key is refused on interactive-only routes. Real proof:
  `verify.mjs upgrade` checks that `api_keys` holds only the wrapped migrated
  0009 key with no free pages left and that the disabled owner's key gets 403;
  `auth --users` generates a member's key with its Jellyfin password.
  Deterministic proof: `tests/test_api_key_http.py`
  (roles, targets, rotation race, disabled accounts, overrides, bytes at rest),
  `tests/test_reauthentication_http.py`
  (throttle windows on an injected clock, audit without secrets, busy sign-in),
  and `tests/test_api_key_migration.py` (0015 upgrades, interruption, backups).
- `mu-settings-operations` (MU-17, MU-27). Connections, path mappings,
  deployment, diagnostics, models, restart, About, and backups follow the
  settings rows of the permission matrix for sessions and API keys. No signed-in
  account can restore: restore exists only in first-run setup (ruling
  wave14-fresh-install-restore), and `POST /api/backups/jobs` with
  `action: restore` answers 422. Server paths in log tails, path
  mappings, and schedule status follow the live `media.paths.view` grant.
  Deterministic
  proof: `tests/test_operations_access_http.py` (every route, allowed and
  refused role, both credentials, unchanged state, audit rows),
  `tests/test_operations_access_paths.py` (path grant, refusal before body
  validation, served schedule roots), and
  `tests/test_operations_access_contracts.py` (model inventory hidden from
  users, no signed-in restore, backup bytes for the owner only, connection keys,
  override limits, setup restore closed once an owner exists). Real proof:
  `verify.mjs auth --settings --controller` drives owner, admin, and user
  settings at 1440 and 320 px (see [Settings access](./settings-access.md)), and
  `verify.mjs restore` proves first-run restore (see [Backups](./backups.md)).
- `mu-users-roles` (MU-15, MU-25, MU-30). Users directory, profiles, role defaults
  with explicit overrides, and bulk actions. Owner-only powers stay nondelegable.
  MU-15 real proof: `verify.mjs auth` runs
  `lib/users-journey.mjs` after the owner journey. Over real HTTP it proves the
  removed local routes (`POST /api/users`, `POST /api/auth/login`,
  `POST /api/auth/register`, `PUT /api/auth/account`,
  `GET`/`PUT /api/settings/registration`, `POST /api/users/{id}/password-reset`)
  answer 404 or 405; a new Jellyfin user's first sign-in creates an ordinary
  account; the user is refused the directory and cannot tell which account IDs
  exist; it edits its own timezone, but display name and avatar answer 409
  `linked_profile_field`; a role change without the Jellyfin password answers
  `reauthentication_required` and with it applies on the user's next request;
  a fresh owner context reads both accounts back.
  Evidence: `users-steps.json` and `users-result.json`. Deterministic proof:
  `tests/test_users_directory_http.py`, `test_users_management_http.py`,
  `test_users_permissions_http.py`, `test_users_media_access_http.py`,
  `test_users_avatars.py`, and `test_user_profiles_migration.py` (0017 upgrades,
  interruption, checks, backups). MU-25 adds the Users/profile UI recipe below.
  The MU-30 extension below covers account access controls. Delete/link UI remains
  outside this recipe.
- `mu-owner-transfer` (MU-18). Ownership transfer and offline owner recovery.
  Real proof: `verify.mjs auth` continues
  `lib/users-journey.mjs` after the users steps. Over real HTTP an admin, an
  owner without its Jellyfin password, and a wrong Jellyfin password are
  refused; the owner hands ownership to a member; the former owner can no
  longer grant roles (`PUT /api/users/{id}/role` 403) while the new owner can on
  its next request; ownership returns, and a fresh context reads exactly one
  owner. Evidence: `owner-transfer-steps.json` and `owner-transfer-result.json`.
  No CLI command creates or recovers a password owner
  (`tests/test_cli_admin.py`). Deterministic proof:
  `tests/test_ownership_transfer_http.py` (roles, API key, eligibility,
  concurrency, one transaction), `tests/test_owner_protection_http.py` (every
  single and bulk path, role defaults, storage guards, a stale former-owner
  request), `tests/test_owner_authorization_races.py` (requests paused before
  transfer cannot reveal or change the new owner's key, profile, or avatar,
  and the former owner cannot change role defaults),
  `tests/test_owner_filter_policy.py` (transfer clears enforcement with an
  attributed policy event and one revision advance, preserves personal values,
  rolls roles and policy back together on interruption, refuses stale filter
  writes, and refuses legacy owner enforcement without a 500),
  `tests/test_ownership_jellyfin.py`, and `tests/test_cli_admin.py`.
- `mu-jellyfin-sync` (MU-08, MU-16). Upstream disable/delete, the 24-hour outage
  grace, five-minute scheduling, and owner immunity.
- `mu-media-access` (MU-11, MU-20, MU-21, MU-26). Catalog, artwork, previews,
  queue, and history show only permitted Jellyfin media. MU-21 proof: `tests/test_scan_access_http.py`
  and `tests/test_scan_access_credits_http.py` drive two users' jobs over
  FastAPI TestClient (own/other queue, history, reorder, cancel, resume, consent,
  hidden deletion references); `tests/test_scan_job_authority.py` revokes an
  initiator's capability, media, or account before launch, at worker start,
  and mid-run, and checks that the refused job writes no findings and leaves
  the queue.
  Fix-round proof: `tests/test_scan_job_boundaries.py` checks empty results,
  acquisitions, and a revocation committed while a write waits for the lock;
  `tests/test_scan_job_policy_boundaries.py` checks linked restrictions without
  a permission revision at 1, 29, 30, and 31 seconds;
  `tests/test_scan_job_process_boundaries.py` runs actual separate workers;
  `tests/test_scan_response_authorization.py` checks review-target privacy,
  findings-read denial, and path redaction.
  Fix-round-2 proof: `tests/test_scan_job_evidence_boundaries.py` holds a
  synthetic Jellyfin item read for 5.5 seconds while an unrelated writer
  commits. It counts provider calls and writer locks for repeated polls and
  checks generation, clock-fence, policy, and restore-epoch races plus bounded
  retries. `tests/test_scan_job_boundaries.py` includes an unrevoked credits
  acquisition that reaches ready, chunk-progress lock counts, and a writer
  during old-artifact deletion after commit. External inference and model
  transport are synthetic. Run these with the scan-access and authority files
  using pytest and isolated data/log directories. These are not browser scan
  journeys; `verify.mjs auth` covers only the running owner authentication UI.
- `mu-filter-policy` (MU-14). Live inheritance with switched-off, edited, and
  reset inherited rules plus personal rules; independent copies; owner-enforced
  rules and subcategory severities in both modes; policy revisions and outdated
  exports by policy owner. `verify.mjs upgrade` checks that the upgraded owner
  has no personal filter values, and its second run, whose owner is linked to
  Jellyfin before startup, saves a personal rule with the migrated key, refuses a
  replayed revision, reads it back, and is refused policy enforcement by key. Deterministic proof: `tests/test_filter_policy_engine.py`
  (pure evaluator), `tests/test_filter_preferences_http.py` and
  `tests/test_filter_policy_revisions.py` (routes, roles, enforcement, copies,
  revisions, exports, read-only views), and `tests/test_filter_rules_migration.py`
  (0016 upgrades, interruption, checks, backups). MU-33 adds the policy UI.
- `mu-private-reviews` (MU-22, MU-24, MU-33). Private reviews and enforced
  filter policy in every review consumer.
- `mu-exports` (MU-31, MU-34, MU-36, MU-37). Personal and default EDL/MCF
  publication, regeneration after policy changes, and deletion choices.
- `mu-plugin-selection` (MU-05, MU-09, MU-12, MU-44). The Jellyfin plugin serves
  each user's EDL/MCF. This runs from the plugin checkout, not this helper.
- `mu-account-lifecycle` (MU-35, MU-38, MU-39, MU-41). Linking, relinking,
  deletion, and transfer with reviewed data disposition.
- `mu-audit` (MU-06, MU-10, MU-28). Permission-scoped audit history. MU-06 adds
  the empty `audit_events` table; `tests/test_community_identity_homes.py`
  checks its actor constraints. MU-10 writes it: `verify.mjs upgrade` checks
  that the upgrade stores no event, that the legacy audit log keeps its original
  bytes, and that no run secret appears in either. Nobody can sign in after that
  upgrade, so it stores no sign-in rows. `tests/test_audit_events.py`
  and `tests/test_audit_http.py` cover the registry, the 90-day boundary, and
  throttling. MU-28 adds the scoped query journey.
- `mu-community` (MU-06, MU-23, MU-29, MU-32). Per-user community identity and
  actions. Revision `0013_community_identities` gives the existing identity,
  connection, receipts, votes, and publications to the owner, with no actor, and
  the owner's first identity use moves `secrets/community/` into
  `secrets/community-identities/<credential_ref>/` by one rename.
  Deterministic proof: `tests/test_community_identity_migration.py` (every
  supported source revision, awaiting-owner setup, injected and hard-exit
  failures, partial and modified 0013 schemas) and
  `tests/test_community_identity_homes.py` (hard exits before and after the
  rename, refused creation and conflicts, unshared references, backup and restore
  into the restored home). Real proof: `verify.mjs upgrade` (fixture
  `mu06-community-fixture-1` at 0009) checks the inventory and that the identity
  files stay unchanged in `secrets/community` while the unlinked owner cannot sign
  in. Its second run links the owner before startup; the owner's Jellyfin session
  reads the migrated identity on the own and named status routes and moves the
  files byte for byte into its `0700` home. MU-23 per-account identities: `tests/test_community_identity_users.py`
  (two accounts enrolling, rotating, and recovering at once through the
  `tests/community_identity_users_support.MockService` transport; interrupted
  rotation and repeated recovery converge; the migrated owner recovers and
  rotates its own keys without reenrolling) and
  `tests/test_community_identity_http.py` (own and named-account routes through
  the real middleware: absent status, admin over ordinary users only, user over
  itself only, and audit rows). Disconnect proof uses
  `DELETE /api/community/connection` and its named-account equivalent. The HTTP
  tests guard against any outbound request, compare identity bytes, check session
  and API-key permissions, and reconnect through enrollment start without another
  enrollment. `tests/test_community_publications.py` and
  `tests/test_community_votes.py` prove held intents resume with saved bytes;
  the publication tests also disconnect during lookup and a lost-response send,
  proving no subsequent POST or transport retry escapes the detached state.
  `tests/test_backup_activation.py` inspects the detached owner's archive.
  No real community service is contacted.
- `mu-backups` (MU-40, MU-45). Complete multi-user backup and restore.

## How to get to it (user POV)

- Every active account signs in with Jellyfin at the ordinary launch. A
  Jellyfin administrator creates the owner through
  `CONNECT YOUR JELLYFIN SERVER`.
- Surfaces are the Users directory (`#/settings/users`), the personal profile
  (`#/settings/profile`), Settings sections gated by capability, Audit history,
  and the catalog, review, and export views filtered per user. The
  feature recipes linked from [the index](./README.md) give exact routes and
  accessible names.

## Driving it with Playwright

Preconditions: one run-owned `serve` instance, the owner set up through
`CONNECT YOUR JELLYFIN SERVER` (`verify.mjs auth` does this), and doctor passing. Read the owning task's handoff for
its selectors and fixture versions before driving.

- **Actors.** Create users on the run's Jellyfin and admit them by their first
  Jellyfin sign-in or `Import Jellyfin users`. Never seed users in the database,
  except the 0030-disabled local accounts that only a 0029 seed can make
  (`scripts/lib/legacy-members.mjs`). Give each actor its own browser
  context and record its `initialCookies` as `auth` does. Never save storage
  state files.
- **Denial.** Check hidden controls and direct API or deep-link denial
  separately, in the same session and after a permission change.
- **Media.** Run `verify.mjs media "$EVIDENCE"` and serve the printed scratch
  path as a Jellyfin item. Record `media.json` `sha256` as the media version.
  Jellyfin needs labeled external-boundary fixtures: the pinned Jellyfin provider used by
  `auth --users` and `auth --identity`, and the synthetic transport in
  `scan-ui.mjs`. Without them those entry points are
  BLOCKED.
- **Time.** Prove 24-hour grace, five-minute cadence, session expiry, and
  retention at the service `now=` boundary with `FIXED_TIME`. Session expiry
  through HTTP replaces `auth_policy.session_now` in tests. Use
  `installFixedClock` only for browser-side time. Do not add a server clock
  override to the app.
- **Isolation.** Use `verify.mjs pair` as the template for cross-instance proof.
  Within one instance, compare each actor's counts, errors, caches, images,
  preview bytes, queues, and local storage against the permission matrix.
- **Evidence.** Each run writes `web/test-results/verify-editarr/editarr-verify-*`.
  Capture before, action, and after per actor at 1440px and 320px. Prove each
  mutation with a fresh context, protected GET, downloaded artifact, or sidecar
  bytes and hash.

## Contract checks

Run from the checkout under test with its own scratch data directory:

```bash
scratch=$(mktemp -d) && EDITARR_DATA_DIR="$scratch" PYTHONPATH="$PWD" \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_multi_user_capabilities.py tests/test_multi_user_route_actions.py \
  tests/test_multi_user_export_layout.py tests/test_multi_user_contracts.py; \
  rm -rf "$scratch"
```

A new endpoint fails `test_every_registered_method_and_route_is_inventoried`
until `scanner/security/access/route_table.py` gains its rule. A change to a
capability row or the export manifest fails its pinned digest until the
contract version and pin are updated together.

Ignored or untracked working files can hide a missing fixture. After
committing a fixture change, rerun the same tests from the committed tree:

```bash
py="$PWD/.venv/bin/python" tree=$(mktemp -d) scratch=$(mktemp -d) && \
  git archive HEAD | tar -x -C "$tree" && cd "$tree" && \
  EDITARR_DATA_DIR="$scratch" PYTHONPATH="$tree" "$py" -m pytest -q \
  -p no:cacheprovider tests/test_multi_user_capabilities.py \
  tests/test_multi_user_route_actions.py tests/test_multi_user_export_layout.py \
  tests/test_multi_user_contracts.py; cd - && rm -rf "$tree" "$scratch"
```

The EDL grammar vectors and the Python plugin reference come from plugin
source at the manifest's pinned commit. They are not .NET evidence. MU-05
runs the vectors through the plugin's own parser.

## Users and Personal Profile UI (MU-25)

Use a fresh `serve` instance and its doctor. Create the
owner through the setup UI. Use separate empty browser contexts for the owner,
an administrator, an ordinary user, and a user from the provider Jellyfin. Every
member is a Jellyfin user; Jellyfin passwords are generated in memory. All
fixture accounts come from production routes, not database seeds. Reserve
distinct app/provider ports within the assigned range.

- The run's Jellyfin gets users `UI Member`, `UI Admin`, and `UI Other`. The
  owner opens `/#/settings/users`, checks them in `Import Jellyfin users`, and
  clicks `Import selected`; each result reads `imported`, and an independent GET
  shows source `jellyfin`, role `user`, status `active`. There is no
  `Create local account` button. Promote `UI Admin` through the existing guarded
  role API with the owner's fresh proof; MU-25 intentionally provides no role
  editor. Wait for a modal's initial focus before filling multiple fields; its
  animation-frame focus must not race automated typing.
- An ordinary user must have no Users navigation. A direct
  `/#/settings/users` visit shows `Page unavailable`, mounts no directory, and
  sends no `/api/users` search. A direct directory GET independently gets 403.
- `/#/settings/profile` for a member shows `Avatar from Jellyfin` and has no
  Display name or Avatar image field. Email, Timezone, and English Display
  language save, survive a reload, and match an independent GET; the display
  name stays the Jellyfin name. `Generate API key` asks for the
  `Jellyfin password`: a wrong one shows an alert and clears the field, the
  right one shows the key. The profile has no `.security-form`, so no username
  or password change, and the key keeps working.
- Compare read-only Created and Last login facts with a fresh account GET for
  owner, member, and provider-user profiles at both widths. An account with no
  prior login displays Never. These dates use the directory's accountDate format.
- In Users, check every list column and override indicator against account GETs.
  Search users, use Previous page/Next page when the fixture exceeds 20 entries,
  and open a named `View ...` button. Ordinary target edits,
  Disable/Enable account, and Revoke sessions must persist in independent reads.
- The admin cannot edit the owner or another admin, manage itself, or reveal
  other keys. Admin key revocation remains available for ordinary targets.
  Owner target-key reveal uses its own fresh Jellyfin password.
  Check Tab/Shift+Tab, Escape, and restored focus for account and key dialogs.
- Select ordinary targets and confirm Enable selected/Disable selected. Keep
  every server per-target result visible, including a partial refusal. Do not
  turn mixed outcomes into a single success claim. Wait for each bulk request's
  own response and dialog close before reading results, because an earlier
  operation's results stay visible until the new one lands.
- Neither the owner nor an admin sees a `Local registration` or
  `Jellyfin admission` control, and `GET /api/settings/registration` and
  `GET /api/settings/jellyfin-admission` answer 404.
- With the approved digest-pinned disposable Jellyfin 12 server, import selected
  and then all accounts through `Import Jellyfin users`. Verify imported and
  existing results. An admin runs `Sync Jellyfin now`; compare per-user outcomes
  and Last successful verification with `/api/jellyfin/sync`. Imported accounts
  must appear in sync status without remounting the page. The imported
  `jf-linked` user signs in; its profile has no Display name or Avatar image
  field while Email/Timezone save and reopen.
- After an admin sync shows active outcomes, disable a fixture user in Jellyfin
  and run sync from the owner context. In the existing admin tab click Refresh
  sync status. Both Last completed run and Sync results must match the latest
  backend run, including the disabled outcome. Repeat at both widths. Restore
  the upstream policy and sync afterward. Use a fresh linked browser context
  for the next viewport because the disable/restore lifecycle changes access.
  The tracked journey runs the original 14 steps plus eight profile/sync checks
  and axe. It requires Docker with the approved pinned Jellyfin image already
  provisioned. From any clean checkout with the skill's dependencies, run:

  ```sh
  node .github/skills/verify-editarr/scripts/verify.mjs auth --users --ports=8960-8979
  ```

  `lib/users-ui-journey.mjs` reuses the normal launch, doctor, capture, redaction,
  and cleanup. The shared port pool chooses two Editarr ports and one Jellyfin
  port within the explicit range. Omit `--ports` to use free loopback ports.
  `users_ui_provider.py` reuses the existing pinned Jellyfin launch, doctor, and
  label/identity-checked cleanup. It creates only disposable synthetic media,
  configuration, and a labelled cache volume. No absolute checkout path or
  ignored driver is required. Sync and bulk results must show loaded names.
  Evidence stays in the printed `web/test-results/verify-editarr/editarr-verify-*`
  directory. Provider evidence stays alongside it with a `-jellyfin` suffix.
  Check `ui-result.json` for the original 22 steps plus the MU-30 extension,
  and `ui-profile-sync-checks.json` for eight
  checks. `ui-axe.json` includes the original 17 reports and the access controls.
  Read `cleanup.json`,
  `ui-jellyfin-cleanup.json`, `redaction.json`, and `ui-jellyfin-redaction.json`
  after success or failure. The two redaction records cover the app evidence
  and the sibling provider evidence separately.
  Run `node .github/skills/verify-editarr/scripts/verify.mjs doctor "$EVIDENCE"`
  afterward. It must refuse the stopped recorded process group, while evidence
  still exists. `auth --users --ports=8960-8979
  --inject-failure` exercises the normal failed-auth capture and cleanup path.
- Repeat directory/profile/dialog journeys at 1440x900 and 320x900. Run axe,
  inspect named keyboard-scrollable table regions, and capture masked before,
  action, and after screenshots plus redacted ARIA snapshots. Never retain raw
  auth bodies, storage-state files, keys, passwords, or CSRF tokens.
- Stop only the recorded app group and labelled container/volume. Verify cleanup,
  secret redaction, and a post-cleanup doctor refusal. Retain exact commands,
  source revision, and failures outside disposable data.

## Account access controls (MU-30)

The existing users journey continues into `lib/users-access-ui.mjs`. Run it with
the same isolated launch, doctor, provider, capture, and cleanup boundaries.
The MU-30 handoff and retained report record the observed acceptance results.

```sh
node .github/skills/verify-editarr/scripts/verify.mjs auth --users --ports=9120-9139
```

At both 1440 and 320 pixels, the owner changes role defaults and saves individual
allow, inherit, and deny overrides. Fresh permissions GETs check the effective
result, and a reload checks the saved deny. Explicit role defaults equal to their
catalog values survive reload and an untouched save. The owner adds a Jellyfin library ID
to the local media default, which matches a fresh GET. A linked account's dialog
has no `Local media access` section and its media access reads
`{"linked": true, "scope": null}`; Jellyfin library policy decides its media.
The **For new accounts** radio cards on the Filters page's Server defaults tab
(`#/settings/filters/server`) save the mode new accounts start in for the owner
and admin. Users has no duplicate instance-mode editor. Account controls save
assigned modes, enforce an instance rule
and subcategory severity, and confirms enforcement survives a mode change.
The linked account moves from an owner-selected admin role back to the stored
verified Jellyfin user role. Its dialog has no unmatched-media exceptions.

A stale mixed bulk selection includes a newly promoted admin. The admin actor
must see one refused target and one applied target after an auth-status refresh
at both widths. Independent GETs prove the protected mode is unchanged and the
eligible mode was applied. Removing the actor's media and mode capabilities
refreshes its principal and removes individual and bulk controls at both widths.
Removing `filters.instance_mode.edit` also hides the For new accounts card, so
that account sees only My filters with no tab bar, and makes a direct
default-mode PUT fail.
The linked account's media access reads `{"linked": true, "scope": null}`, and the
owner's `PUT .../media-access/exceptions` answers 404 and changes nothing
(`users-exceptions-removed.json`).
Direct requests independently prove owner-only and removed-capability refusals.
A disabled successor is refused 409 `successor_disabled`.
Missing or incorrect fresh proof leaves ownership unchanged. A fresh-authenticated
UI transfer changes the sole owner and the fixture returns ownership.

`ui-access-result.json` records the completed access extension. `ui-steps.json`
retains each assertion group. `ui-mu30-*.png` and matching ARIA snapshots capture
the changed controls, and `ui-axe.json` records accessibility results. The normal
`ui-http-statuses.json` records safe request methods, paths, and statuses. The
existing provider cleanup and both redaction records remain required on success
and failure. Run the stopped doctor command from the MU-25 recipe afterward.
This proves the source application and disposable pinned Jellyfin only. It does
not certify installed releases, native builds, or the later lifecycle UI.

Principal capabilities come from `/api/auth/status`, through the existing auth
gate. `/api/capabilities` is the scan-model catalog and is not repurposed.

## Gotchas

- Every launch admits every active account; each gated handler decides per
  target. There is no local registration or local account creation: a valid
  first Jellyfin sign-in or an owner import creates the account.
- Use `VERIFY_PLUGIN_CHECKOUT` to point to a checkout of the public
  [Jellyfin plugin](https://github.com/Editarr-EDL/jellyfin-edl). The live
  interoperability journey needs Docker and runs a disposable Jellyfin server.
- Installed-release proof uses `web/e2e/run-production-e2e.mjs` and
  `scripts/verify_installed_release.py` against a separately provisioned artifact.
  This helper does not certify installed, native, or TLS behavior.
