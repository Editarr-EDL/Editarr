# Jellyfin-only Editarr plan

Editarr becomes a Jellyfin-only app, modeled on Seerr. A fresh install asks the owner for a Jellyfin server URL and a Jellyfin administrator sign-in, and Editarr mints its own Jellyfin API key. Every account comes from a Jellyfin sign-in, a Quick Connect sign-in, or a Jellyfin import. Local passwords, Radarr, Sonarr, and Other Files go away.

The rule the program enforces is that every person and every media item traces to the configured Jellyfin server. The app is pre-release with one local user, so the plan has no upgrade path for other installs. Migrations still preserve data, as `AGENTS.md` requires. Radarr, Sonarr, and Other Files records move to a read-only archive. After the stack lands, the operator's local Editarr database is wiped at the operator's request.

The stack, bottom to top, is JF-01, JF-02, JF-03, JF-04, JF-05, JF-06, and JF-07.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

The program runs `.github/skills/poteto-mode/playbooks/autopilot-stack.md`. The operator lands every PR. Each PR stops at stack-ready, and the operator lands the stack bottom-up. JF-01 through JF-06 are review-gated.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

The operator decisions behind this plan are recorded in Appendix B. Do not change them without the operator.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on the operator's explicit go.
- [ ] Read these from trunk at program start. Re-read them at every tick.
  - [ ] `git show origin/main:.github/skills/poteto-mode/playbooks/autopilot-stack.md`
  - [ ] `git show origin/main:.github/skills/poteto-mode/playbooks/autopilot-full.md`
  - [ ] `git show origin/main:.github/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:.github/skills/verify-editarr/SKILL.md`
  - [ ] `git show origin/main:.github/skills/poteto-mode/playbooks/opening-a-pr.md`
  - [ ] `git show origin/main:.github/skills/poteto-mode/playbooks/shipping.md`
  - [ ] `git show origin/main:.github/skills/principle-subtract-before-you-add/SKILL.md`
  - [ ] `git show origin/main:.github/skills/principle-sequence-verifiable-units/SKILL.md`
  - [ ] `git show origin/main:AGENTS.md`
- [ ] On the operator's go, arm the audit tick as `/loop 1h` with the tick prompt below. Copilot has no `/loop`, so run the tick at the start of every coordinator turn and log it. Never leave the cadence to memory.
- [ ] Use this tick prompt, verbatim. "Re-read the execution playbook from trunk. Audit the operation against it and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then post a short status message to the operator in chat only when the audit found a tracked change that no earlier status message reported, such as a PR opened, a code-ready head, a round launched or closed, a verdict, a merge, a stuck agent and the action taken, a blocker added or cleared, or a decision only the operator can make. Name every such change and nothing else. Do not repeat a table, the merged list, or an unchanged blocker. If the audit found none, end the turn with no reply text. Either way, log this tick's row in your decision trail. The row names the items reported, or none."
- [ ] On the operator's hold or stand-down, send every owner a zero-writes order at once.

### Spawn owners

- [ ] Spawn one `poteto-agent` owner per PR with the full lifecycle that `autopilot-stack.md` names. Owners report STACK-READY and never merge.
- [ ] Follow this dependency graph. Each owner branches from its parent's branch.
  - [ ] JF-01 is first. It branches from `origin/main` at or after `3f8a08cc`.
  - [ ] JF-02 after JF-01.
  - [ ] JF-03 after JF-02.
  - [ ] JF-04 after JF-03.
  - [ ] JF-05 after JF-04.
  - [ ] JF-06 after JF-05.
  - [ ] JF-07 after JF-06.
- [ ] Hold the file boundaries.
  - [ ] JF-01 touches only Radarr and Sonarr code, its archive migration, the archive view, and their tests and docs.
  - [ ] JF-02 touches only Other Files code, its archive migration, and their tests and docs.
  - [ ] JF-03 and JF-04 touch only setup, sign-in, the Jellyfin client, and their tests, docs, and the `verify-editarr` skill.
  - [ ] JF-05 touches only admission, role defaults, profile editing, and their tests and docs.
  - [ ] JF-06 touches only password code, its migration, and their tests and docs.
  - [ ] JF-07 touches only `community-service/` and the Editarr code that reads community provenance.
- [ ] Hold the review gate. JF-01 through JF-06 change an interaction. They wait for the operator's review in chat with screenshots and a video before the operator lands them.

### PR mechanics, for every PR

- [ ] Resolve the forge once. Default to `gh`; if `command -v origin` succeeds and Origin can resolve the repository, use `origin pr` for every PR operation. Record any fallback to `gh`. Never require `gt`.
- [ ] Open the PR ready, never draft, per **Opening a PR**. Use `gh pr create --base <parent-branch>`. JF-01 targets `main`. Each later PR targets its parent's branch.
- [ ] Run `.venv/bin/ruff check scanner tests` and `pnpm --dir web run build` once before the PR-facing push. Run `pnpm --dir community-service run typecheck` for JF-07. Push with hooks on.
- [ ] Run `/deslop` before each commit and `/no-comments` before review. Neither skill is bundled here, so state which installed equivalent ran or that none ran.
- [ ] Triage every Bugbot and security-reviewer comment per `.github/skills/poteto-mode/references/bugbot-triage.md`.
- [ ] Rebase onto current trunk before the code-ready report and babysit. Keep that merge base in fix rounds. Rebase again only at merge prep, on a `git merge-tree` conflict with trunk, or on a CI failure that comes from a change on trunk.
- [ ] Never run `uv sync`, `pip install -e`, or `uv pip install` in a worktree that symlinks the primary `.venv`.
- [ ] Never run a migration against the operator's database. Migration evidence comes only from scratch databases and fixtures. The only operation on the operator's database is the wipe in **Close the program**.

### Verdict and merge, for every PR

- [ ] A full swarm runs once, at the code-ready head. Another full swarm runs only for new behavior, a redesign, or a large new diff. A rebase, a merge-conflict resolution, or a minor fix gets a single verifier at the new head. Run each full swarm per `.github/skills/swarm/SKILL.md`. One gates lane. The ten live lanes from the PR's **Verify, live** block. The perf lane from its **Verify, perf** block. Two or more audit lanes, each with its own focus, that read the diff and the receipts and distrust the PR body. The root audits the receipts in the stack-ready report before the verdict.
- [ ] Clean only when every lane is `PASS`. Findings go back to the owner, including a defect that a lane filed as a note. A new head gets a fresh verdict, except for results that stay valid under the patch-id rule in `.github/skills/poteto-mode/playbooks/shipping.md`.
- [ ] The root appends each clean PR to the stack in dependency order, with its base set to its parent's branch. The root is the only writer of the stack topology. Once the operator starts landing, nobody writes to the stack except the post-merge retarget that `shipping.md` allows. A head that keeps the same patch-id after a rebase keeps its verdict.

### Boot recipe, for every live lane

Each live lane runs on its own scratch instance at the PR head. Drive the browser through the `verify-editarr` skill and its Playwright helpers, because `control-ui` is not installed here.

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>` in a fresh worktree.
- [ ] Start Jellyfin 12 with the digest-pinned image that the `verify.mjs auth --users` journey uses. Create one administrator and one ordinary Jellyfin user through the Jellyfin startup API. Wait for `/System/Info/Public` to report `StartupWizardCompleted` true.
- [ ] Start Editarr with `node .github/skills/verify-editarr/scripts/verify.mjs serve --ports=<range>`. Wait for its readiness line. From JF-03 on, readiness is the Jellyfin setup screen, not `CREATE ADMIN ACCOUNT`.
- [ ] Deliver input only through Playwright in the `verify-editarr` helpers. Read state only through `GET` calls to the Editarr API and the Jellyfin API.
- [ ] Before a migration lane, create a pre-change scratch database at the parent SHA and take a backup with the backup API. Confirm that the backup restores into an empty instance before the head migrates the database.
- [ ] Run `python scripts/evidence_secrets.py <evidence dir>` before keeping evidence.
- [ ] Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.

## Remove Radarr and Sonarr (JF-01)

**Depends on.** None. JF-01 branches from `origin/main`.

**Files.**

- [ ] Delete `scanner/integrations/arr.py`.
- [ ] Delete `web/src/catalog/arr-pages.tsx` and `web/src/catalog/arr-pages.test.tsx`.
- [ ] Edit `scanner/modeling/model_core.py` to remove `MediaSourceKind.RADARR` and `MediaSourceKind.SONARR`.
- [ ] Edit `scanner/api.py`, `scanner/repository.py`, `scanner/http/catalog_source_api.py`, `scanner/http/catalog_settings_api.py`, `scanner/http/scan_jobs_api.py`, `scanner/http/review_api.py`, `scanner/modeling/media_identity.py`, `scanner/modeling/model_scans.py`, `scanner/modeling/model_audio_tracks.py`, `scanner/modeling/model_community_imports.py`, `scanner/security/scan_jobs/media.py`, `scanner/integrations/community/local_downloads.py`, and `scanner/integrations/community/donations.py` to remove Radarr and Sonarr branches.
- [ ] Edit `web/src/App.tsx`, `web/src/routes.ts`, `web/src/api/api-service.ts`, `web/src/api/api-media-types.ts`, `web/src/api/api-reviews.ts`, `web/src/api/api-user-access.ts`, and `web/src/settings/settings-view.tsx` to remove Radarr and Sonarr routes, types, and connection settings.
- [ ] Create the next Alembic revision after `0024_account_dispositions` in `scanner/persistence/migrations/versions/`. It moves Radarr and Sonarr scans, reviews, and exports to an archived, read-only state and keeps every row. It keeps `arr_settings` rows without credentials until the operator approves a later drop.
- [ ] Create a read-only **Archive** view in `web/src/catalog/` and its API in `scanner/http/`. It lists archived records with their original source and title.
- [ ] Edit the restore code in `scanner/backup/` so restore accepts only a backup at the current schema head and refuses an older backup with a clear message.
- [ ] Create a migration test beside `tests/test_account_disposition_migration.py` that upgrades from every supported schema version.
- [ ] Edit Radarr and Sonarr cases in `tests/test_editarr_settings.py`, `tests/test_editarr_catalog.py`, `tests/test_editarr_library.py`, `tests/test_editarr_scans.py`, `web/src/api/api.test.ts`, and the `web/e2e/` specs.
- [ ] Edit `README.md`, `docs/api-contract.md`, `docs/multi-user-decisions.md`, `.github/skills/verify-editarr/SKILL.md`, and `.github/skills/verify-editarr/features/scans.md`.
- [ ] Regenerate `website/public/docs/openapi.json` with `scripts/export_site_openapi.py`.

**Build.**

- [ ] Remove every Radarr and Sonarr code path, then add the archive migration and the read-only **Archive** view. No code outside the archive reads an archived row.

**You see.**

- [ ] The sidebar has no Radarr or Sonarr entry. **Settings** has no Radarr or Sonarr connection. An upgraded database shows former Radarr and Sonarr scans under **Archive** with no scan, edit, or export action.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] The new migration test proves that every Radarr and Sonarr row survives as archived and that an upgrade from each supported version succeeds. Run `.venv/bin/python -m pytest -q tests/test_existing_migrations.py tests/test_fresh_migrations.py <new migration test>`.
- [ ] `git grep -n -i -E 'radarr|sonarr' -- scanner web/src` returns only the migration and the archive source labels. Run the command and save its output.
- [ ] The full backend and web suites pass. Run `.venv/bin/python -m pytest -q` and `pnpm --dir web exec vitest run`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run the Jellyfin library scan journey from `scan-ui.mjs` at trunk and head. Save `jf01-jellyfin-scan.png`. Pass when both finish a Jellyfin scan and head shows the same result rows as trunk.
- [ ] Lane 2. Open the app at 1440 pixels as the owner. Save `jf01-nav-1440.png`. Pass when no Radarr or Sonarr entry appears in the sidebar or the route table.
- [ ] Lane 3. Open the app at 320 pixels as the owner. Save `jf01-nav-320.png`. Pass when the mobile menu has no Radarr or Sonarr entry and nothing overflows.
- [ ] Lane 4. Open **Settings** connections. Save `jf01-settings.png`. Pass when only Jellyfin connection settings appear.
- [ ] Lane 5. Upgrade a parent-SHA scratch database that holds Radarr scans, reviews, and exports. Save `jf01-archive-radarr.png`. Pass when **Archive** lists every seeded Radarr record by title.
- [ ] Lane 6. Repeat lane 5 with Sonarr episodes. Save `jf01-archive-sonarr.png`. Pass when every seeded episode appears with its series and episode number.
- [ ] Lane 7. Try to start a scan, edit a review, and export from an archived record through the API. Save `jf01-archive-readonly.png`. Pass when each call returns a refusal and no row changes.
- [ ] Lane 8. Request a former Radarr route such as `/radarr` directly. Save `jf01-old-route.png`. Pass when the app shows its not-found page and no client error.
- [ ] Lane 9. Sign in as an ordinary user with no archive capability. Save `jf01-archive-hidden.png`. Pass when **Archive** is hidden and its API refuses that user.
- [ ] Lane 10. Restore a backup taken at the parent SHA into an empty instance at head. Save `jf01-old-backup.png`. Pass when restore refuses with a message about the backup's schema and the instance stays empty.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Cold start time from launch to `/api/auth/status` ready at trunk and head. Trunk has no archive migration, so also time the head migration on a database seeded with 10,000 Radarr and Sonarr scans.
- [ ] Probe. Run five interleaved cold starts at trunk and at head on the same machine. Time the migration three times on fresh copies of the seeded database.
- [ ] Baseline. Record the trunk median cold start first.
- [ ] Rule. Fail when the head median cold start exceeds the trunk median by more than 10 percent. Fail when the migration median exceeds 30 seconds. Both budgets are proposals and are not yet measured.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 2, lane 5, and lane 7 screenshots into `docs/jellyfin-only-media/jf01-review-<slug>.png`.
- [ ] Record a 30 to 60 second video of the upgraded archive and the sidebar. Save it as `docs/jellyfin-only-media/jf01-review.mp4`.
- [ ] Post the screenshots and the video in chat for the operator. Stop at stack-ready. Wait for the operator's go.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends JF-01 to the stack with base `main`, and the operator lands it.

## Remove Other Files (JF-02)

**Depends on.** JF-01.

**Files.**

- [ ] Delete `web/src/scans/manual-scans.tsx`, `web/src/scans/manual-scan-workspace.tsx`, `web/src/scans/manual-media-identity.tsx`, and `web/src/scans/manual-media-identity.test.tsx`.
- [ ] Delete `scanner/http/media_identity_api.py` and the `/api/manual-files/identity` route, after confirming that no Jellyfin path uses it.
- [ ] Edit `scanner/modeling/model_core.py` to remove `MediaSourceKind.MANUAL_FILES` and `ScanOrigin.MANUAL`.
- [ ] Edit `scanner/http/review_target_routes.py`, `scanner/http/scan_create_api.py`, `scanner/api.py`, and the upload routes for MCF and EDL files to remove Other Files branches. Keep path mappings and sidecar writes for Jellyfin items.
- [ ] Edit `web/src/App.tsx`, `web/src/api/api-media-identity.ts`, `web/src/api/api-reviews.ts`, `web/src/api/api-audio-tracks.ts`, and `web/src/api/api-user-access.ts`.
- [ ] Create the next Alembic revision. It moves Other Files scans, reviews, and exports to the archive from JF-01 and keeps every row.
- [ ] Create its upgrade test from every supported schema version.
- [ ] Edit `tests/test_editarr_scans.py`, `tests/test_editarr_library.py`, `web/e2e/media-identity.spec.ts`, `web/e2e/mcf10.spec.ts`, `web/e2e/audio-tracks.spec.ts`, and `web/e2e/community-sharing.spec.ts`.
- [ ] Edit `README.md`, `.github/skills/verify-editarr/features/imports.md`, `.github/skills/verify-editarr/features/reviews-exports.md`, and `.github/skills/verify-editarr/features/scans.md`.
- [ ] Regenerate `website/public/docs/openapi.json`.

**Build.**

- [ ] Remove the Other Files source and archive its records. A scan path now always comes from a Jellyfin item through a path mapping.

**You see.**

- [ ] The sidebar has no **Other Files** entry. Former Other Files records appear under **Archive** as read-only rows.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] The new migration test proves that every Other Files row survives as archived. Run `.venv/bin/python -m pytest -q <new migration test> tests/test_existing_migrations.py`.
- [ ] `git grep -n -E 'MANUAL_FILES|manual-files|ScanOrigin.MANUAL' -- scanner web/src` returns only the migration and the archive labels.
- [ ] The full backend and web suites pass. Run `.venv/bin/python -m pytest -q` and `pnpm --dir web exec vitest run`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Scan a Jellyfin movie through a path mapping at trunk and head. Save `jf02-jellyfin-scan.png`. Pass when both runs finish and head writes the same sidecar file beside the media.
- [ ] Lane 2. Open the sidebar at 1440 pixels. Save `jf02-nav-1440.png`. Pass when no **Other Files** entry appears.
- [ ] Lane 3. Open the sidebar at 320 pixels. Save `jf02-nav-320.png`. Pass when no **Other Files** entry appears and nothing overflows.
- [ ] Lane 4. Upgrade a parent-SHA scratch database with Other Files scans and reviews. Save `jf02-archive.png`. Pass when **Archive** lists every seeded record with its original file name.
- [ ] Lane 5. Call the former `/api/manual-files/identity` route. Save `jf02-old-api.png`. Pass when it returns 404 and the log has no traceback.
- [ ] Lane 6. Export an EDL for a Jellyfin review. Save `jf02-export.png`. Pass when the sidecar exists at the mapped path and its bytes match trunk for the same review.
- [ ] Lane 7. Import an MCF file for a Jellyfin item. Save `jf02-mcf-import.png`. Pass when the import attaches to that Jellyfin item.
- [ ] Lane 8. Try to import an MCF or EDL file with no Jellyfin item. Save `jf02-orphan-import.png`. Pass when the UI explains that the file must belong to a Jellyfin item and nothing is saved.
- [ ] Lane 9. Open the audio-track picker for a Jellyfin item. Save `jf02-audio.png`. Pass when the tracks match trunk.
- [ ] Lane 10. Take a backup at head after the upgrade and restore it into an empty instance. Save `jf02-restore.png`. Pass when the Other Files records appear in **Archive**.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Duration of a Jellyfin item scan from enqueue to done, with the fake model adapter, at trunk and head. Also time the head migration on a database seeded with 10,000 Other Files records.
- [ ] Probe. Run five interleaved scans at trunk and at head on the same synthetic media. Time the migration three times on fresh copies.
- [ ] Baseline. Record the trunk median scan duration first.
- [ ] Rule. Fail when the head median scan duration exceeds trunk by more than 10 percent. Fail when the migration median exceeds 30 seconds. Both budgets are proposals and are not yet measured.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 2, lane 4, and lane 8 screenshots into `docs/jellyfin-only-media/jf02-review-<slug>.png`.
- [ ] Record a 30 to 60 second video of the archive and the orphan import refusal. Save it as `docs/jellyfin-only-media/jf02-review.mp4`.
- [ ] Post the screenshots and the video in chat for the operator. Stop at stack-ready. Wait for the operator's go.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends JF-02 with base JF-01's branch, and the operator lands it after JF-01.

## Set up a fresh install with a Jellyfin administrator (JF-03)

**Depends on.** JF-02.

**Files.**

- [ ] Edit `scanner/integrations/jellyfin_users.py` to add API-key minting with `POST /Auth/Keys` and the administrator check on `Policy.IsAdministrator`.
- [ ] Edit `scanner/http/auth_api.py` to replace the local owner setup with Jellyfin setup. The request carries the server URL, the Jellyfin username, and the password.
- [ ] Edit `scanner/http/api_lifecycle.py` so that `setup_required` means no owner exists, whether or not a Jellyfin server is stored.
- [ ] Edit `scanner/http/catalog_settings_api.py` so that setup stores the server URL and the minted key through the existing Jellyfin settings code.
- [ ] Edit `web/src/auth/auth-setup.tsx`, `web/src/auth/auth-gate.tsx`, and `web/src/api/api-auth.ts`. Keep the **Restore from backup** path from `scanner/http/setup_restore_api.py`.
- [ ] Edit `tests/test_auth_api.py`, `tests/test_auth_status_http.py`, `tests/test_setup_restore_api.py`, `web/src/auth/` tests, `web/e2e/app-auth.spec.ts`, and `web/e2e/setup-restore.spec.ts`.
- [ ] Edit `.github/skills/verify-editarr/scripts/verify.mjs`, `.github/skills/verify-editarr/SKILL.md`, and `.github/skills/verify-editarr/features/authentication.md` so readiness is the Jellyfin setup screen and every journey sets up through a Jellyfin 12 container.
- [ ] Edit `README.md`, `docs/api-contract.md`, and `docs/multi-user-decisions.md`.

**Build.**

- [ ] Replace local owner setup with Seerr-style setup. Editarr signs in to Jellyfin with the given credentials, refuses a non-administrator, mints an Editarr key, stores the URL and key, creates the owner linked to that Jellyfin user, and starts a session.

**You see.**

- [ ] A fresh install shows **Connect your Jellyfin server** with URL, username, password, and **Restore from backup**. A Jellyfin administrator lands signed in as the owner. Jellyfin's **API Keys** page lists an `Editarr` key.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Backend tests cover an administrator, a non-administrator, wrong credentials, an unreachable server, a bad URL, and a second setup attempt after an owner exists. Run `.venv/bin/python -m pytest -q tests/test_auth_api.py tests/test_auth_status_http.py tests/test_setup_restore_api.py`.
- [ ] `verify.mjs` dispatch tests pass with the new readiness rule. Run `node --test .github/skills/verify-editarr/scripts/verify.test.mjs .github/skills/verify-editarr/scripts/verify-dispatch.test.mjs`.
- [ ] Web setup tests pass. Run `pnpm --dir web exec vitest run src/auth`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run first-run setup at trunk and head. Trunk creates a local owner. Head creates a Jellyfin-linked owner. Save `jf03-setup.png`. Pass when head lands signed in as the owner and `/api/auth/status` reports the linked Jellyfin user ID.
- [ ] Lane 2. Set up with an ordinary Jellyfin user. Save `jf03-not-admin.png`. Pass when setup refuses, explains that an administrator is required, and creates nothing.
- [ ] Lane 3. Set up with a wrong password. Save `jf03-bad-password.png`. Pass when the error names the credentials and no key exists in Jellyfin.
- [ ] Lane 4. Set up with an unreachable URL. Save `jf03-unreachable.png`. Pass when the error says the server cannot be reached and differs from lane 3.
- [ ] Lane 5. After setup, read Jellyfin's key list. Save `jf03-key.png`. Pass when exactly one `Editarr` key exists and Editarr's catalog loads with it.
- [ ] Lane 6. After setup, open the setup URL in a second browser. Save `jf03-setup-closed.png`. Pass when it shows sign-in and the setup API refuses.
- [ ] Lane 7. Restore a backup from the setup screen. Save `jf03-restore.png`. Pass when the restore completes and the restored owner can sign in with Jellyfin.
- [ ] Lane 8. Run setup at 320 pixels. Save `jf03-320.png`. Pass when every field and the restore link fit and work.
- [ ] Lane 9. Start the native shell from the packaged app at head. Save `jf03-native.png`. Pass when the native window shows the same Jellyfin setup screen.
- [ ] Lane 10. Submit setup twice at once from two tabs. Save `jf03-race.png`. Pass when exactly one owner and one key exist.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Time from setup submit to the signed-in home page at trunk and head. The scenarios differ, so also set an absolute budget for the head setup against a loopback Jellyfin 12.
- [ ] Probe. Run five interleaved setups at trunk and head on fresh scratch instances.
- [ ] Baseline. Record the trunk median setup time first.
- [ ] Rule. Fail when the head median exceeds 3 seconds. Report the trunk number beside it without a ratio. The budget is a proposal and is not yet measured.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1, lane 2, and lane 4 screenshots into `docs/jellyfin-only-media/jf03-review-<slug>.png`.
- [ ] Record a 30 to 60 second video of a fresh setup through to the home page. Save it as `docs/jellyfin-only-media/jf03-review.mp4`.
- [ ] Post the screenshots and the video in chat for the operator. Stop at stack-ready. Wait for the operator's go.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends JF-03 with base JF-02's branch, and the operator lands it after JF-02.

## Sign in with Jellyfin Quick Connect (JF-04)

**Depends on.** JF-03.

**Files.**

- [ ] Edit `scanner/integrations/jellyfin_users.py` to add `QuickConnect/Enabled`, `QuickConnect/Initiate`, `QuickConnect/Connect`, and `Users/AuthenticateWithQuickConnect`.
- [ ] Edit `scanner/http/jellyfin_login_api.py` to add initiate, poll, and complete routes that end in the same session and admission path as password sign-in.
- [ ] Edit `web/src/auth/auth-login.tsx` and `web/src/api/api-auth.ts` to show the code and poll.
- [ ] Create backend tests beside `tests/test_jellyfin_user_transport.py` and a web test beside `web/src/auth/`.
- [ ] Edit `.github/skills/verify-editarr/features/authentication.md` and `docs/api-contract.md`.

**Build.**

- [ ] Add Quick Connect sign-in. Hide it when Jellyfin reports it is disabled. Expire a pending code when Jellyfin expires it.

**You see.**

- [ ] The sign-in page has **Use Quick Connect**. It shows a six-digit code. After the user approves it in a Jellyfin client, Editarr signs them in.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Tests cover enabled, disabled, approved, expired, and a secret reused after sign-in. Run `.venv/bin/python -m pytest -q <new tests>` and `pnpm --dir web exec vitest run src/auth`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run Jellyfin password sign-in at trunk and head. Trunk lacks Quick Connect, so record that and gate the head Quick Connect sign-in. Save `jf04-qc.png`. Pass when password sign-in works on both and Quick Connect signs in the ordinary user on head.
- [ ] Lane 2. Disable Quick Connect in Jellyfin. Save `jf04-disabled.png`. Pass when **Use Quick Connect** is hidden.
- [ ] Lane 3. Let a code expire. Save `jf04-expired.png`. Pass when the UI says the code expired and offers a new one.
- [ ] Lane 4. Approve with a different Jellyfin user than expected. Save `jf04-other-user.png`. Pass when Editarr signs in the approving user.
- [ ] Lane 5. Reuse a spent secret through the API. Save `jf04-reuse.png`. Pass when the API refuses.
- [ ] Lane 6. Use Quick Connect for a new Jellyfin user. Save `jf04-new-user.png`. Pass when admission creates the account as in password sign-in.
- [ ] Lane 7. Use Quick Connect for a disabled Editarr account. Save `jf04-disabled-account.png`. Pass when sign-in refuses with the disabled-account message.
- [ ] Lane 8. Stop Jellyfin while polling. Save `jf04-outage.png`. Pass when the UI reports the outage and stops polling.
- [ ] Lane 9. Run Quick Connect at 320 pixels. Save `jf04-320.png`. Pass when the code is readable and the cancel control works.
- [ ] Lane 10. Cancel a pending Quick Connect. Save `jf04-cancel.png`. Pass when polling stops and no session exists.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Password sign-in latency at trunk and head. Trunk lacks Quick Connect, so also time head from approval in Jellyfin to the signed-in page.
- [ ] Probe. Run ten interleaved password sign-ins at trunk and head. Run five Quick Connect approvals at head.
- [ ] Baseline. Record the trunk median password sign-in latency first.
- [ ] Rule. Fail when head password sign-in exceeds trunk by more than 10 percent. Fail when approval to signed-in exceeds 3 seconds. Both budgets are proposals and are not yet measured.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1, lane 3, and lane 9 screenshots into `docs/jellyfin-only-media/jf04-review-<slug>.png`.
- [ ] Record a 30 to 60 second video of a Quick Connect sign-in. Save it as `docs/jellyfin-only-media/jf04-review.mp4`.
- [ ] Post the screenshots and the video in chat for the operator. Stop at stack-ready. Wait for the operator's go.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends JF-04 with base JF-03's branch, and the operator lands it after JF-03.

## Make Jellyfin the source of admission, roles, and profiles (JF-05)

**Depends on.** JF-04.

**Files.**

- [ ] Edit the admission code behind `scanner/http/jellyfin_accounts_api.py` and the `/api/settings/jellyfin-admission` routes to remove import-only mode. Every first Jellyfin sign-in creates an account. Import stays.
- [ ] Edit account creation in `scanner/security/` so a Jellyfin administrator starts as an Editarr admin. The owner can change the role later.
- [ ] Edit the profile routes and `web/src/settings/profile/profile-editor.tsx` so username and avatar are read-only and come from Jellyfin on sign-in and sync.
- [ ] Edit `web/src/settings/users/admission-controls.tsx` to remove the mode choice.
- [ ] Create the next Alembic revision. It sets any stored import-only mode to first-sign-in mode and records the change in the audit history.
- [ ] Create its upgrade test.
- [ ] Edit `tests/test_jellyfin_account_admission.py`, `tests/test_account_schema_admission.py`, `web/src/settings/profile/profile-editor.test.tsx`, and `.github/skills/verify-editarr/features/jellyfin-users.md`.
- [ ] Edit `docs/multi-user-decisions.md` and `docs/multi-user-permissions.md`.

**Build.**

- [ ] Remove import-only admission, add the administrator role default, and make the Jellyfin profile read-only.

**You see.**

- [ ] **Users** settings has no admission mode choice. A new Jellyfin administrator appears as an Editarr admin. The profile page shows the Jellyfin name and avatar with no edit control.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Tests cover first sign-in of an ordinary user, first sign-in of an administrator, a later role change by the owner, a Jellyfin rename picked up on sync, and the admission migration. Run `.venv/bin/python -m pytest -q tests/test_jellyfin_account_admission.py tests/test_account_schema_admission.py <new migration test>`.
- [ ] Profile tests pass. Run `pnpm --dir web exec vitest run src/settings/profile src/settings/users`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Sign in a new ordinary Jellyfin user at trunk and head with first-sign-in mode. Save `jf05-first-signin.png`. Pass when both create the account with the same default capabilities.
- [ ] Lane 2. Upgrade a parent-SHA database set to import-only. Save `jf05-mode-migrated.png`. Pass when a new Jellyfin user can sign in after the upgrade and the audit history records the change.
- [ ] Lane 3. Sign in a new Jellyfin administrator. Save `jf05-admin-default.png`. Pass when **Users** shows the account as an admin.
- [ ] Lane 4. As the owner, change that admin to an ordinary user. Save `jf05-role-change.png`. Pass when the change persists after the user signs in again.
- [ ] Lane 5. Rename a user in Jellyfin and run sync. Save `jf05-rename.png`. Pass when Editarr shows the new name.
- [ ] Lane 6. Change an avatar in Jellyfin and sign in again. Save `jf05-avatar.png`. Pass when Editarr shows the new avatar.
- [ ] Lane 7. Try to edit the username through the profile API. Save `jf05-profile-readonly.png`. Pass when the API refuses.
- [ ] Lane 8. Import a Jellyfin user from **Users**. Save `jf05-import.png`. Pass when the account appears before that user's first sign-in.
- [ ] Lane 9. Open **Users** at 320 pixels. Save `jf05-users-320.png`. Pass when no admission mode control appears and the layout fits.
- [ ] Lane 10. Disable a user in Jellyfin and run sync. Save `jf05-disabled.png`. Pass when Editarr disables the account as on trunk.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. First Jellyfin sign-in latency, which includes account creation, at trunk and head. Also time a sync of 500 Jellyfin users.
- [ ] Probe. Run ten interleaved first sign-ins at trunk and head. Run the sync three times on each side.
- [ ] Baseline. Record the trunk medians first.
- [ ] Rule. Fail when either head median exceeds trunk by more than 10 percent.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 3, lane 7, and lane 9 screenshots into `docs/jellyfin-only-media/jf05-review-<slug>.png`.
- [ ] Record a 30 to 60 second video of an administrator's first sign-in and the read-only profile. Save it as `docs/jellyfin-only-media/jf05-review.mp4`.
- [ ] Post the screenshots and the video in chat for the operator. Stop at stack-ready. Wait for the operator's go.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends JF-05 with base JF-04's branch, and the operator lands it after JF-04.

## Remove local password accounts (JF-06)

**Depends on.** JF-05.

**Files.**

- [ ] Delete `scanner/security/auth_passwords.py`, `web/src/auth/password-change-required.tsx`, and `web/src/settings/users/create-account.tsx`.
- [ ] Edit `scanner/http/auth_api.py` to remove local login, registration, password change, and temporary passwords.
- [ ] Edit `scanner/security/auth_repository_admin.py`, `scanner/security/auth_repository.py`, `scanner/security/auth_policy.py`, and `scanner/persistence/rows/db_auth.py` to remove password state and the registration setting.
- [ ] Edit `web/src/auth/auth-login.tsx`, `web/src/auth/auth-security-controls.tsx`, and the account views in `web/src/settings/users/`.
- [ ] Create the next Alembic revision. It deletes every password hash and temporary password, removes the registration setting, and disables every account without a Jellyfin link. It keeps every account's data.
- [ ] Create its upgrade test from every supported schema version.
- [ ] Edit `pyproject.toml` and `uv.lock` to remove `argon2-cffi` if nothing else uses it.
- [ ] Edit `tests/test_auth_api.py`, `tests/test_auth_crypto.py`, `tests/test_auth_policy.py`, `tests/test_auth_repository.py`, `tests/test_auth_schema.py`, `tests/test_auth_services.py`, `web/src/auth/` tests, and `web/e2e/app-auth.spec.ts`.
- [ ] Edit `README.md`, `docs/local-authentication-plan.md`, `docs/multi-user-decisions.md`, `.github/skills/verify-editarr/features/authentication.md`, `.github/skills/verify-editarr/features/account-linking.md`, and `.github/skills/verify-editarr/features/backups.md`.
- [ ] Regenerate `website/public/docs/openapi.json`.

**Build.**

- [ ] Remove every local password path and add the migration. The owner links a disabled member to a Jellyfin user with the existing account-link dialog, and the link enables the member.

**You see.**

- [ ] The sign-in page offers only Jellyfin sign-in and Quick Connect. After an upgrade, **Users** marks unlinked members as disabled with a **Link to Jellyfin** action.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Migration tests prove that hashes are gone, unlinked accounts are disabled, and every user-keyed row survives. Run `.venv/bin/python -m pytest -q <new migration test> tests/test_existing_migrations.py`.
- [ ] `git grep -n -i -E 'password_hash|argon2|temporary_password' -- scanner web/src` returns only the migration. Run the command and save its output.
- [ ] The full backend and web suites pass. Run `.venv/bin/python -m pytest -q` and `pnpm --dir web exec vitest run`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Sign in a linked Jellyfin user at trunk and head. Save `jf06-jellyfin-signin.png`. Pass when both sign in and head shows no password form.
- [ ] Lane 2. Upgrade a parent-SHA database with a linked owner, two linked members, and two password-only members. Save `jf06-upgrade.png`. Pass when the linked accounts sign in and the password-only members appear disabled with their data intact.
- [ ] Lane 3. Upgrade a parent-SHA database with an unlinked owner. Save `jf06-unlinked-owner.png`. Pass when the migration completes, the owner's rows survive, and sign-in offers only Jellyfin. The owner cannot sign in, and the plan accepts that.
- [ ] Lane 4. Start a fresh install at head and read its schema. Save `jf06-fresh-schema.png`. Pass when no password, temporary password, or registration column exists.
- [ ] Lane 5. As the owner, link a disabled member to a Jellyfin user. Save `jf06-link-member.png`. Pass when the member signs in with Jellyfin and sees their old reviews.
- [ ] Lane 6. Post to the former local login and registration routes. Save `jf06-old-routes.png`. Pass when each returns 404.
- [ ] Lane 7. Use an existing Editarr API key after the upgrade. Save `jf06-api-key.png`. Pass when the key still works for its linked owner.
- [ ] Lane 8. Take a backup at head and restore it into an empty instance. Save `jf06-restore.png`. Pass when the restored owner signs in with Jellyfin.
- [ ] Lane 9. Open sign-in at 320 pixels. Save `jf06-signin-320.png`. Pass when only the Jellyfin options appear and both work.
- [ ] Lane 10. Search the upgraded database file for any former hash prefix. Save `jf06-no-hashes.png`. Pass when no Argon2 hash string remains.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Cold start time to ready at trunk and head. Also time the head migration on a database with 1,000 accounts.
- [ ] Probe. Run five interleaved cold starts at trunk and head. Time the migration three times on fresh copies.
- [ ] Baseline. Record the trunk median cold start first.
- [ ] Rule. Fail when the head median cold start exceeds trunk by more than 10 percent. Fail when the migration median exceeds 10 seconds. The second budget is a proposal and is not yet measured.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 2, lane 3, and lane 5 screenshots into `docs/jellyfin-only-media/jf06-review-<slug>.png`.
- [ ] Record a 30 to 60 second video of an upgrade and a member link. Save it as `docs/jellyfin-only-media/jf06-review.mp4`.
- [ ] Post the screenshots and the video in chat for the operator. Stop at stack-ready. Wait for the operator's go.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends JF-06 with base JF-05's branch, and the operator lands it after JF-05.

## Strip Radarr and Sonarr provenance from the community service (JF-07)

**Depends on.** JF-06.

**Files.**

- [ ] Create `community-service/migrations/0017_<name>.sql`. It rewrites stored Radarr and Sonarr provenance to the generic provenance value the service already validates and keeps every published row.
- [ ] Edit `community-service/src/catalog/compatibility-inputs.ts` and the validators that accept Radarr or Sonarr provenance.
- [ ] Create `community-service/test/catalog-upgrade-v9.test.ts` from the pattern in `catalog-upgrade-v8.test.ts`.
- [ ] Edit `community-service/test/compatibility.test.ts`, `community-service/test/catalog-legacy.test.ts`, and `community-service/test/import-receipts.test.ts`.
- [ ] Edit `docs/community-service-docker.md` to add the upgrade note and the backup step before the image replacement.

**Build.**

- [ ] Remove Radarr and Sonarr provenance from the community service schema and validators. A request that carries that provenance gets the service's normal validation error.

**You see.**

- [ ] The upgraded service database has no Radarr or Sonarr provenance value. Lookups for formerly Radarr-sourced publications return the same publications.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] The upgrade test proves row counts and publication IDs are unchanged. Run `pnpm --dir community-service test`.
- [ ] Typecheck passes. Run `pnpm --dir community-service run typecheck`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run the `verify.mjs auth --community` journey at trunk and head. Save `jf07-community.png`. Pass when both publish, look up, and download a Jellyfin-sourced review.
- [ ] Lane 2. Upgrade a copy of a trunk service database with Radarr provenance rows in the Docker image. Save `jf07-upgrade.png`. Pass when row counts match and no Radarr value remains.
- [ ] Lane 3. Repeat lane 2 with Sonarr rows. Save `jf07-upgrade-sonarr.png`. Pass when row counts match and no Sonarr value remains.
- [ ] Lane 4. Post a publication with Radarr provenance. Save `jf07-reject.png`. Pass when the service returns its validation error and stores nothing.
- [ ] Lane 5. Look up a formerly Radarr-sourced publication. Save `jf07-lookup.png`. Pass when the result matches trunk.
- [ ] Lane 6. Open the operator UI publication list. Save `jf07-operator.png`. Pass when the list renders with no blank provenance column.
- [ ] Lane 7. Start the image with the init refusal check. Save `jf07-init.png`. Pass when init still refuses an existing database.
- [ ] Lane 8. Back up the service database, upgrade, and restore the backup into a second container. Save `jf07-restore.png`. Pass when the restored service starts on the pre-upgrade schema and the image refuses to start it until it is migrated.
- [ ] Lane 9. Download a publication from Editarr at head. Save `jf07-download.png`. Pass when the review imports onto the Jellyfin item.
- [ ] Lane 10. Check `/ready` after the upgrade. Save `jf07-ready.png`. Pass when it returns 200 with the configuration enabled.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Catalog lookup latency at trunk and head. Also time the head migration on a database with 50,000 publications.
- [ ] Probe. Run 100 interleaved lookups at trunk and head. Time the migration three times on fresh copies.
- [ ] Baseline. Record the trunk lookup p50 first.
- [ ] Rule. Fail when the head lookup p50 exceeds trunk by more than 10 percent. Fail when the migration median exceeds 30 seconds. The second budget is a proposal and is not yet measured.

**Review gate.** None. JF-07 is not review-gated.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends JF-07 with base JF-06's branch, and the operator lands it after JF-06.

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Wipe the operator's local Editarr database, at the operator's explicit request, after the operator lands the whole stack. Take no backup.
  - [ ] Resolve the data directory from `EDITARR_DATA_DIR` or the app default. Post the exact path in chat and wait for the operator's go, because deletion is irreversible.
  - [ ] Stop any Editarr process that uses that directory. Delete only the database files in it. Start Editarr and confirm that `/api/auth/status` reports `setup_required` and the browser shows the Jellyfin setup screen.
  - [ ] Leave the community service database alone. It upgrades through its own migration.
- [ ] Reply to the operator with the report that `autopilot-stack.md` names. List the stack in landing order, each PR link, each verdict SHA, and every decision the owners made that the operator did not.

## Appendix A. Prototype evidence

One prototype ran on November 1, 2026, against a throwaway Jellyfin 12.0.0 container. The image was `jellyfin/jellyfin@sha256:baba630419915985442f315f08b0cf46d9f4c8a0cc4bd38e94a6d35751dd5ef5`, the same image that `verify-editarr` pins. No branch or SHA exists, because the prototype changed no code. The container was removed after the run.

- An administrator sign-in through `POST /Users/AuthenticateByName` returned `Policy.IsAdministrator` true and the server ID.
- `POST /Auth/Keys?app=Editarr` with the administrator token returned 204. The minted key was 32 characters, and `GET /Users` with it returned 200.
- The same call with an ordinary user's token returned 403.
- Jellyfin 12 rejected the legacy `X-Emby-Authorization` header for sign-in. The `Authorization: MediaBrowser` header worked. `media_browser_authorization` in `scanner/integrations/jellyfin_users.py` already sends that header.
- Quick Connect was enabled by default. Initiate, authorize by an ordinary user, connect, and `AuthenticateWithQuickConnect` signed in that user.

These questions stay unproven.

- Every perf budget marked as a proposal.
- Whether the native shell needs code changes for the new setup screen. The inventory found no setup code in `native/`.

## Appendix B. Alternatives rejected

The operator made each product decision below in the interview on November 1, 2026.

- Keep local sign-in as a fallback, as Seerr does. Rejected. The operator wants no local accounts.
- Offer setup-screen reclaim when the stored Jellyfin server is offline. Rejected. Without a host-side guard, anyone who can reach the UI during an outage could claim ownership. Seerr has no such path. During an outage, wait for Jellyfin to return. A restored backup stays linked to the old server, so a permanently lost server means a fresh install.
- Offer a host recovery command or an emergency owner password. Rejected. The operator chose no lockout recovery.
- Re-match archived records to Jellyfin items, and refuse an upgrade until the owner links Jellyfin. Both were in the first draft. Dropped on November 2, 2026, because the app is pre-release, the operator is its only user, and the operator's database is wiped after landing.
- Squash the Alembic chain into a new baseline, or add migrations that drop Radarr, Sonarr, and Other Files data. Rejected. The operator kept the `AGENTS.md` rule for data-preserving migrations.
- Keep restoring backups made before the change. Rejected. Restore accepts only backups at the current schema.
- Reset the community service database. Rejected. It gets a normal forward migration.
- Keep import-only admission. Rejected. Every first Jellyfin sign-in creates an account.
- Mirror Jellyfin administrator status into Editarr roles at every sign-in. Rejected. Administrator status sets the starting role only.
- Paste an existing Jellyfin API key at setup. Rejected. Editarr always mints its own key.
- Leave Radarr and Sonarr provenance in the community service. Rejected. The operator chose to strip it.
- Keep EDL and MCF import from a scanner-host path for Jellyfin items. Rejected on November 3, 2026. The operator chose to remove path import entirely in JF-02 and keep browser uploads only. The `/api/edl-imports/path` and `/api/mcf-imports/path` routes, the **Scanner path** import control, and the `reviews.import.server_path` capability are gone; stored grants of that capability are ignored.

The plan made these sequencing choices. The operator can change them.

- Subtraction comes first. JF-01 and JF-02 remove sources and archive their data before JF-03 changes setup, so every later PR builds on fewer source kinds.
- Password removal comes last in Editarr. JF-03, JF-04, and JF-05 make Jellyfin sign-in complete while passwords still exist, so every stack position stays usable during development.

## Appendix C. Risks

- JF-03 changes `verify-editarr` readiness. Every later live lane needs Docker and the pinned Jellyfin 12 image. A lane without them exits BLOCKED, and the owner reports it rather than skipping it.
- JF-06 leaves an unlinked owner unable to sign in. The operator accepted that, because the only database is wiped after landing.
- Restore refuses every backup taken before JF-01. The operator accepted that.
- An old Editarr release that still sends Radarr or Sonarr provenance gets a validation error from the community service after JF-07. The owner records the oldest client that still works in `docs/community-service-docker.md`.
- `control-ui`, `deslop`, and `create-skill` are not installed in this Copilot setup. Lanes drive the browser through the `verify-editarr` Playwright helpers instead. Owners state which equivalent ran.
- Copilot has no `/loop`, and Cursor model names do not apply. The coordinator runs the tick at each turn and uses the models the host exposes.
- Perf budgets are proposals until each PR measures its trunk baseline. A budget that fails on its first measurement goes to the operator rather than being raised quietly.
- Migration evidence must never touch the operator's database. The boot recipe uses only scratch databases. The wipe in **Close the program** is the only operation on that database, and it waits for the operator to confirm the path.

## Appendix D. Links and reading list

Read these before editing.

- `AGENTS.md` for migration and code organization rules.
- `docs/multi-user-decisions.md` and `docs/multi-user-permissions.md` for the account model this plan changes.
- `docs/local-authentication-plan.md` for the password design JF-06 removes.
- `.github/skills/verify-editarr/SKILL.md` and its `features/` recipes.
- `scanner/integrations/jellyfin_users.py` and `scanner/http/jellyfin_login_api.py` for the existing Jellyfin sign-in.
- Seerr's Jellyfin sign-in in `server/routes/auth.ts` and `server/api/jellyfin.ts` at `seerr-team/seerr` commit `b185621e`.

JF-03 and JF-06 get `.github/skills/how/SKILL.md` before building and `.github/skills/interrogate/SKILL.md` before stack-ready, because both change who can own the install. The coordinator keeps a decision trail per `.github/skills/show-me-your-work/SKILL.md` and commits it with the plan.
