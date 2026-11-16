# Backups

Owners and admins export full archives from Settings. A backup is restored only
into an empty installation, through first-run setup or `editarr backup restore`
into an empty data directory.

## Sub-features

- `backup-export`: create and download a full plain archive from Settings.
- `backup-encryption`: independently verify encrypted archives when prerequisites exist.
- `backup-restore`: restore an archive into a separate empty instance through
  onboarding, then sign in after a restart.
- `backup-refusal`: a configured or occupied target refuses restore with a named reason.

## How to get to it (user POV)

- Settings > General (`/#/settings/general`), section `Backup and restore`. It
  has export and scheduling only.
- First-run `Restore from backup` on a new, empty instance.
- Scheduled backups are a separate `Scheduled backups` region; do not enable one
  as part of a manual export or restore proof.

## Run the journey

```bash
node .github/skills/verify-editarr/scripts/verify.mjs restore --ports=9200-9219
```

`lib/restore-journey.mjs` drives four disposable instances in sequence and keeps
evidence under `web/test-results/verify-editarr/restore-*`. The Jellyfin started for
the source run stays up for the whole journey and stops at its cleanup.

1. Source. The journey starts a run Jellyfin for the source run and passes
   `{base, admin, members}` JSON on stdin to `restore_journey_fixture.py
   seed-source`, which prints only keys. Through the library it sets the owner up
   with Jellyfin setup from the run Jellyfin's administrator and imports
   `restore-admin` (made admin), `restore-user`, and `restore-linked`. It adds
   `restore-retained`, a disabled local account with a retain disposition, and
   `restore-unlinked`, disabled with cause `unlinked` as migration 0030 leaves a
   former local account. It also seeds keys, two enrolled community identities,
   a pending operation, personal publications with sidecars, and one settled
   transfer move whose publication records an empty path while its only copy
   sits at the old path. The journey signs in as
   the owner, checks that Settings has no restore, inspection, or contents
   control, creates and downloads a full backup through the UI, and gets 409
   `setup_already_complete` from the setup flow. With the source stopped, the CLI
   refuses to restore over it with `restore_target_has_accounts`.
   The source saves deployment port 9443 before it starts (the launcher's
   `--port` still decides where it listens).
2. Target. On a fresh instance that saved its own port 9444, onboarding restores
   the archive: preview with no replacement choices, `Restore complete` with the
   review notice listing the backup's base URL (default) and port 9443, then
   sign-in. The target's `deployment.json` is byte-identical afterwards and
   `restore-report.json` records the backup's binding.
   After a restart every sign-in is Jellyfin: the owner signs in at 320px, the
   admin and user sign in, every key works, and the source's browser cookie gets
   401. The users (columns `id`, `role`, `status`, `username`, `local_export_id`,
   `disable_cause`), provider IDs, dispositions, keys, identities, connections,
   operations, publications, moves, and every identity file hash match the source.
   The restored `restore-unlinked` is disabled with cause `unlinked`. The restored
   database holds one system `backup.restore` event and `restore-report.json`
   lists the two disagreements of the seeded move.
   The owner then opens `restore-unlinked` in Users at 1440px. Its Jellyfin link
   section says "Not linked to Jellyfin. The account cannot sign in until it is
   linked." with no `Enable account`. `Link to Jellyfin` proves with its Jellyfin
   user; the preview's `proof_basis` is `admin_attested` with no blockers and
   says "disabled (no Jellyfin link) becomes active." After confirmation the
   account reads `active` with no disable cause and source `jellyfin`, and its
   Jellyfin sign-in reaches that account. Evidence: `restore-link-*.png`,
   `restore-link.json`, and `restore-link-keyboard.json`.
3. CLI. An injected exit after the journal is written leaves a journal;
   `backup recover` rolls it back to an empty installation that starts at setup;
   `backup restore --confirm` then restores it, prints the backup's binding, and
   leaves the unbound installation without `deployment.json`; the owner signs in
   with Jellyfin.
4. Occupied. An instance with work but no account shows the named refusal in
   onboarding at 320px and answers 409 `restore_target_has_work`; its database is
   unchanged.

## Upgrade and recovery acceptance

- `tests/test_upgrade_matrix.py` builds one populated database per revision below
  head (unversioned through 0023) and upgrades it through snapshot-backed adoption.
  `tests/upgrade_matrix_support.py` lists the values each source holds, literally,
  and seeds the state 0017-0023 introduced (profile, avatar, Jellyfin sync, review
  repair, retained personal export, regeneration job, link transition and export
  move). Each run checks those values before and after, every shared column of every
  table except the permitted transformations, the single owner (disabled with its
  password deleted by 0030 because it has no Jellyfin link), the API key, activity credited to user 1, identity and sidecar bytes, and that the
  retained generation exports the exact source database. Released sources
  (unversioned through 0009) must also revoke every session and refuse the old
  cookie. 0010-0023 are unreleased development revisions and need not revoke
  sessions.
- `tests/test_upgrade_restore_acceptance.py` proves legacy activity without an
  account refuses library and CLI restore, before and after its upgrade, with every
  data-directory byte unchanged.
- `tests/test_restore_hard_exit.py` hard-exits activation at each boundary and
  requires worker admission to stay closed until recovery and reconciliation.

## Installed artifact

Build a disposable wheel outside the shared environment, then run the verifier
against it. Never install into the primary `.venv`.

```bash
git archive HEAD | tar -x -C "$SCRATCH/src"
pnpm --dir web build && cp -R web/dist "$SCRATCH/src/scanner/frontend"
(cd "$SCRATCH/src" && UV_OFFLINE=1 SOURCE_DATE_EPOCH=315532800 uv build --out-dir "$SCRATCH/build")
(cd "$SCRATCH/src" && UV_PROJECT_ENVIRONMENT="$SCRATCH/venv" UV_OFFLINE=1 \
  uv sync --frozen --no-dev --no-install-project --python 3.11)
UV_OFFLINE=1 uv pip install --python "$SCRATCH/venv/bin/python" --no-deps "$SCRATCH"/build/editarr-*.whl
EDITARR_VERIFY_PORTS=9240-9259 PYTHONPATH="$PWD" .venv/bin/python \
  scripts/verify_installed_release.py --editarr "$SCRATCH/venv/bin/editarr" \
  --recovery --json-out OUT.json
```

The locked dependencies install offline from the uv cache into the throwaway
environment, so the wheel runs without borrowed site-packages and the evidence
records `legacy_dependencies: installed-environment`. An unpinned
`uv pip install` of the wheel resolves newer versions the cache may lack.
`EDITARR_VERIFY_PORTS=A-B` issues each port with its restart successor, both
bind-checked and inside the span.

Build with plain `uv build` (sdist, then wheel): a direct `--wheel` build honors
`.gitignore` and omits `scanner/frontend`. `--recovery` sets up an owner and key,
exports a backup with the CLI, refuses a restore over the source by name with its
files unchanged, restores into an empty data directory, refuses the source cookie
while the password and key work, refuses owner recovery while the service runs,
and resets the password offline while the key keeps working.

For the native PyInstaller runtime, build from a scratch clone (the inventory needs
its Git revision) with every environment and store in scratch, then verify it:

```bash
git clone -q --no-hardlinks --branch BRANCH "$PWD" "$SCRATCH/src"
(cd "$SCRATCH/src" && UV_PROJECT_ENVIRONMENT="$SCRATCH/buildenv" UV_OFFLINE=1 \
  uv sync --frozen --extra dev --extra audio_macos --python 3.11)
(cd "$SCRATCH/src" && UV_PROJECT_ENVIRONMENT="$SCRATCH/buildenv" UV_NO_SYNC=1 UV_OFFLINE=1 \
  pnpm_config_store_dir="$SCRATCH/pnpm-store" pnpm_config_cache_dir="$SCRATCH/pnpm-cache" \
  pnpm_config_state_dir="$SCRATCH/pnpm-state" GOCACHE="$SCRATCH/gocache" \
  GOMODCACHE="$SCRATCH/gomod" GOPATH="$SCRATCH/gopath" GOFLAGS=-modcacherw GOTOOLCHAIN=local \
  PYTHONPATH= "$SCRATCH/buildenv/bin/python" scripts/build_release.py --out-dir "$SCRATCH/out" \
  --work-dir "$SCRATCH/work" --target macos-arm64 --ffprobe-helper "$(readlink -f "$(which ffprobe)")")
EDITARR_VERIFY_PORTS=9240-9259 PYTHONPATH="$PWD" .venv/bin/python \
  scripts/verify_installed_release.py --runtime "$SCRATCH/out/Editarr" --target macos-arm64 \
  --recovery --json-out OUT.json
```

pnpm 11 reads `pnpm_config_*`, not `npm_config_*`; with the latter it links from the
global store. The pnpm and Go module downloads need network. Check afterward that the
shared environment's `editarr` `direct_url.json` still names its own checkout.

For HTTPS behind a real proxy, run from `web/` with the same artifact:

```bash
EDITARR_VERIFY_PORTS=9240-9259 EDITARR_PRODUCTION_EDITARR="$SCRATCH/venv/bin/editarr" \
  EDITARR_PRODUCTION_PYTHON=PYTHON_WITH_CHECKOUT_PYTHONPATH EDITARR_PRODUCTION_PROXY=nginx \
  EDITARR_PRODUCTION_OUTPUT=EVIDENCE_DIR node e2e/run-production-e2e.mjs
```

`nginx` or `caddy` must be on `PATH`. First-run setup and every sign-in go to a
loopback Jellyfin stand-in (`tests/jellyfin_setup_fake.py`), which is why the
fixture Python needs the checkout on `PYTHONPATH`. Its minted key also reads a
one-movie library that holds the seeded review's media and artwork. The stand-in
records shapes and proves nothing about a live Jellyfin. The fixture's control and
Jellyfin helpers still bind ephemeral loopback ports outside the range. Every run, passed or
failed, keeps `EVIDENCE_DIR/fixture-logs/<UTC start>-{primary,secondary}-{backend,proxy,nginx-error}.log`.
Each backend and proxy output line starts with its UTC arrival time, so a slow
request can be placed against the Playwright trace. The fixture redacts each log
as it writes it there, and the runner scans the whole output again after the
fixture exits. Ctrl+C or SIGTERM to the runner signals Playwright's whole process
group and waits for every process in it to exit. It then waits for the teardown and
the scan, and exits 130 or 143. Repeat signals are ignored, so a second Ctrl+C cannot
skip the scan. When a fixture run fails, it prints only redacted logs.

Playwright traces keep request bodies, cookies, and CSRF headers. The runner rewrites
its output with `scripts/evidence_secrets.py --redact`, archive members included, and
exits 1 if a credential remains. Before keeping any other evidence tree, run
`python scripts/evidence_secrets.py DIR [--secret VALUE]...`; it exits 1 on a finding.

## Driving it with Playwright

- **Export.** `page.locator('.backup-settings').first()`; require heading
  `Backup and restore`, no `Backup contents` or `Backup file` control, click
  `Create backup`, then save the `Download backup` download and verify its bytes.
- **Restore.** On a setup-required instance click `Restore from backup`, set the
  `Backup file`, click `Inspect backup`, require heading `Restore preview`, check
  `Restore this backup into this new installation`, click `Restore backup`, wait
  for `Restore complete`, then `Continue`.
- **Encryption.** Test separately with `Encrypt backup`, `Passphrase`, and
  `Confirm passphrase`, using only ephemeral fixture credentials and the existing
  age prerequisites. Require decryption/validation and wrong-passphrase refusal.

## Gotchas

- Archives include authentication and signing material. Retain them privately,
  never commit them, and never log passphrases. The journey keeps the archive in
  a scratch directory it removes.
- The source development launcher refuses to start over a pending restore journal
  ("Restore recovery is required"); `editarr serve` and API startup recover it.
  The journey uses `backup recover`.
- Restoring publishes the source's absolute sidecar paths. The journey runs both
  instances on one host, so the paths resolve; on another host the report lists
  every move whose files are elsewhere.
- Existing UI browser specs mock backup jobs and archive contents. Their
  successful responses do not establish real archive validity or restore safety.
