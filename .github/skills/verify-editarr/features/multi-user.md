# Multi-user journeys

Use [Verify Editarr](../SKILL.md) for the source harness and the
[permission matrix](../../../../docs/multi-user-permissions.md) for role defaults.

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
`CONNECT YOUR JELLYFIN SERVER` (`verify.mjs auth` does this), and a passing
`doctor` check.

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
source at the manifest's pinned commit. Run the Jellyfin plugin tests separately for .NET coverage.

## Users and Personal Profile UI

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
  role API with the owner's fresh proof.
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
  Check `ui-result.json` for the profile, user and account access steps,
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

## Account access controls

The existing users journey continues into `lib/users-access-ui.mjs`. Run it with
the same isolated launch, doctor, provider, capture, and cleanup boundaries.

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
retains each assertion group. The screenshots and matching ARIA snapshots capture
the changed controls, and `ui-axe.json` records accessibility results. The normal
`ui-http-statuses.json` records safe request methods, paths, and statuses. The
existing provider cleanup and both redaction records remain required on success
and failure. Run the stopped doctor command from the profile recipe afterward.
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
