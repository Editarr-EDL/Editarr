# CSS-66 integration evidence

October 7 successor update: CSS-68 was canceled by the user for local Docker on
Mac mini. Its references below are historical follow-up assignments, not active
work. [CSS-69](../69-pilot-and-launch-scope.md) records the selected disabled
local-only pilot and explicit unverified-risk dispositions for future public scope.

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

Completed locally October 7, 2026. All identities, secrets, media paths and databases
are disposable synthetic fixtures. No existing service was enabled or migrated.
This records integration evidence, not production activation or capacity approval.

## October 8 cross-application follow-up

`multi-instance.fixture.mjs` now drives real independent donor, recipient and
same-contributor sibling Editarr HTTP processes against the source community
HTTP/SQLite server. `tests/test_community_end_to_end.py` exposes the same journey
to pytest. This closes the missing application-to-application path; it is not a
rerun or replacement of the historical counts below.

The focused journey passed locally with exact publication/download bytes,
response-loss/restart reconciliation, selected audio, derived review lineage,
saved review/EDL/MCF export, receipts/votes, private reporting/moderation,
membership changes, key lifecycle and four archive export/restore modes followed
by signed remote access. Its real restore flows exposed and fixed maintenance
HTTP refusal and application-startup reconciliation regressions, with dedicated
unit regressions preserving worker admission refusal.

Final follow-up validation: **184 Python tests** (including the E2E entry point
and nearby auth, maintenance, migration and restore suites) and **38 source-runtime
tests** passed. Scoped Python lint and new-harness formatting checks passed.

Reproduce with `pnpm --dir community-service test:e2e`; prerequisites and exact
mock boundaries are in the service README. External Discord/Access and model
inference remain synthetic. This is source HTTP evidence, not browser, TLS,
built-image, native or live-provider certification. Existing S04 host-import
evidence and October 7 test totals remain historical.

## Execution layers

- **Shared**: 599 existing Node Vitest/SQLite tests. They retain the original request,
  artifact, identity, membership, catalog and moderation contracts.
- **Node source**: real loopback HTTP, file-backed SQLite with exclusive WAL writer,
  production dispatch/configuration validation and actual crypto. Only Discord and
  Access provider answers are replaced. Four new journeys plus adapter/lifecycle/
  schema regressions: 34 runtime tests passed.
- **Built image**: the journeys call `/app/dist/runtime/service.mjs`, HTTP adapter,
  environment reader and cleanup runner from the actual image. They do not replace
  these with the fixture's source imports. Containers run without external network,
  with read-only root, dropped capabilities and disposable `/tmp` storage.
  Five journeys plus existing publication verification passed on Node 24.
- **Local application**: real Python verification/import/repository/backup/auth logic
  on disposable SQLite; AI/provider/native media work is substituted by existing
  fixtures. Browser journeys use mocked APIs and real Chromium interaction/layout.
- **Live ingress**: not run. OPS-01 owns Discord and OPS-02 owns Tunnel/Access.

Image: `editarr-community:local`, `linux/arm64`, manifest identity
`sha256:279f6a2e1938f2c67ec8564bc09c1b2d8f5920c7b6c70788210bd3aca54d5acc`.
Host source tests used Node v26.7.0; the image is the Node 24 compatibility evidence.

## Workflow matrix

Fixture paths below are relative to `community-service/test/runtime/` unless stated.
“Image” means the same fixture against the built modules, with synthetic providers.

| ID | Workflow and failure evidence | Shared / local fixture | Node source | Image | Live / remaining owner |
| --- | --- | --- | --- | --- | --- |
| I01 | OAuth cookies, confirmation page, redirects, provider callback, exact confirmation proof, completion retry | `discord-*`, `enrollment-*` shared tests | `identity.fixture.mjs` | Pass | OPS-01 callback/member/nonmember and real browser cookie boundary |
| I02 | Two Discord accounts; additional same-account installation; private IDs omitted; rename conflict and exact retry | `contributor-identity`, `profile-*` | `identity.fixture.mjs` | Pass | OPS-01 same-account enrollment |
| I03 | Dual-proof rotation; immediate old-copy rejection; replacement reconciliation; foreign operation denial; fresh OAuth lost-key recovery; sibling revocation | `key-lifecycle*`, `recovery*`; Python rotation/recovery/backups | `identity.fixture.mjs` | Pass | OPS-01 real fresh authorization; CSS-67 native key portability |
| I04 | Expired evidence fails closed on outage; cooldown, explicit Unknown Member and verified rejoin | `discord-membership`, `discord-member-provider` | `identity.fixture.mjs` | Pass | OPS-01 live leave/ban/rejoin and bot permissions |
| S01 | Concurrent exact publication retry; signed bytes; permission rejection; rollback after payload insert including storage counters and operation | `publication*`, artifact vectors | `sharing.fixture.mjs`, `service.fixture.mjs` | Pass | CSS-68 real disk-full/capacity; OPS-01 ingress |
| S02 | Availability, keyset pages, distinct roots/revisions, stale cursor refusal after vote, private data omitted, selections initially false | `catalog-*`, ranking/compatibility; Python matching | `sharing.fixture.mjs` | Pass | CSS-68 large-catalog latency |
| S03 | Exact gzip/manifest/envelope; signed receipt keys; verified contributor-bound receipts; cross-reader isolation | `download-*`; Python download verification | `sharing.fixture.mjs` | Pass | OPS-01/02 real trust/configuration setup |
| S04 | Actual HTTP bundle verifies in Python; two separate local stores commit/reuse original bytes and receipt intents; explicit audio review and EDL export | `tests/community_node_import.py`, called before HTTP acknowledgment | `sharing.fixture.mjs` invokes Python subprocess | Service half passes; Python runs on host, not in service image | CSS-67 installed/native version of local application |
| S05 | Import acknowledgments from sibling installations count once; foreign receipt refused; pre-import vote denied; own-vote privacy; concurrent vote retry and changed-intent conflict | `import-receipts`, `votes`; Python outbox/import tests | `sharing.fixture.mjs` | Pass | OPS-01 signed public ingress |
| S06 | Private report, sibling exact retry, unimported reporter refusal; report never enters public search | `reports`, moderation shared tests | `sharing.fixture.mjs` | Pass | OPS-02 real operator session |
| O01 | JWT verification, missing assertion, CSRF denial, guarded real operator HTML/JS, no contributor elevation | `operator-access`, `moderation-http`; 18 operator browser journeys | `sharing.fixture.mjs`, `operator.fixture.mjs` | Pass | OPS-02 expired/revoked live Access sessions and JWKS rotation |
| O02 | Dashboard overview/contributions/contributors/keys/usage/operations/audit/reports; unknown SQLite scanned rows remain unknown | `operator-dashboard` | `sharing.fixture.mjs` | Pass | passive telemetry limitations; CSS-68 disk measurement |
| O03 | All seven moderation commands; optimistic conflicts; exact action retries; cached visibility invalidation; vote exclusion/restoration; immutable bytes retained | `moderation*`; operator browser journeys | `sharing.fixture.mjs` | Pass | OPS-02 real Access/CSRF and external ingress |
| O04 | CLI argument/DTO/transport/checkpoint path; dry-run, POST response loss, exact retry, single audit action, bounded page checkpoint | operator CLI shared/subprocess tests | `operator.fixture.mjs` calls real CLI `run` through HTTP; only human token retrieval/Access assertion exchange substituted | Pass | OPS-02 actual cloudflared login/token lifecycle; no CLI bypass added |
| R01 | Disabled liveness/readiness; wrong/missing Host; duplicate security headers; fixed-length/chunked limits; exact request bodies/multiple cookies | `http.test.mjs`, `environment.test.mjs` | Pass | Disabled container and enabled journeys; generic raw-header/body edge cases run against source adapter | OPS-02 direct-origin isolation, actual Tunnel Host/IP preservation |
| R02 | Missing/malformed trusted-IP refusal, byte tampering, warm-cache authority, signed service-outage handling | request-signature/auth shared tests | `sharing.fixture.mjs`, `identity.fixture.mjs`, `service.fixture.mjs` | Pass | [CSS-25 audit completed locally](../25-worker-request-auth.md); live OPS-01/02 remain open |
| R03 | Actual enabled server executable; SIGKILL then restart; SIGTERM drain; persisted operation/nonce/moderation; storage-failure handling after restart; repeated valid reads remain allowed after restart | Existing schema/adapter tests | Not run on host server executable | `restart.fixture.mjs` | CSS-67 Mac/FileVault/Docker cold boot and AMD64 |
| R04 | Second writer refused; hard container restart; offline verified backup; exact key BLOB preserved | `schema.test.mjs` | Pass | `test/container/runtime.test.mjs` | CSS-68 independent off-device recovery |
| R05 | Hourly timer, disabled state, no early/overlapping runs, shutdown drain, sync/async failure; actual scheduled cleanup bounded to 1000, retry next hour, partial writes accounted, durable operations/active nonces preserved | `maintenance.test.mjs` | `cleanup.fixture.mjs` plus timer unit tests | Actual built cleanup runner passes | CSS-64 retention remains deferred; no new deletion policy |
| R06 | Known versions 1–13 upgrade with verified backup; unknown schema/occupied destination refusal; failed upgrade rollback | `schema.test.mjs` | Pass | Existing container validates current schema/startup | CSS-67 packaged platform and separately authorized legacy import |
| L01 | Full/settings × plain/encrypted export/preflight/activation; identity/recovery retained; failure/crash safety | Python portable/restore/activation/rotation-backup/recovery suites | Local Python, 297-test run | Not part of service image | CSS-67 native/cross-platform release checks |
| L02 | Initial setup restore through real auth; restored sign-in or account creation only when absent; competing setup/restore, stale flow closure, malformed/wrong-password refusal | `tests/test_setup_restore_api.py`; `web/e2e/setup-restore.spec.ts` | Local Python and Chromium | Not part of service image | CSS-67 installed fresh-setup entry point |
| L03 | Actual selected track through fake AI execution and backup; stream changes/default rules; exact preview/signatures; language/description; mixed-review exclusions; independent audio/video compatibility and unknown history | Python audio-track/execution/donation/matching/vector suites; `web/e2e/audio-tracks.spec.ts`, community-results/sharing | Local Python and Chromium; Node frozen artifact/lookup contracts | Service contracts pass; no real AI execution in image | CSS-67 packaged demux/scanner; real-media accuracy not claimed |
| L04 | Local-first discovery, explicit uncertainty, partial download recovery, vote retry, compatibility-first sorting, saved review/EDL/MCF behavior | Python local-download/import/review/MCF/EDL; web unit and browser suites | Pass on isolated fixtures | Service portions above | CSS-67 native packaging and non-Chromium checks |

## Commands and results

From repository root:

```sh
pnpm --dir community-service test
pnpm --dir community-service test:runtime
docker compose -f community-service/compose.yaml build
pnpm --dir community-service test:container
env COMMUNITY_RUNTIME_IMAGE=editarr-community:local node --test community-service/test/runtime/journeys.test.mjs community-service/test/runtime/service.test.mjs
pnpm --dir community-service exec tsc --noEmit
pnpm --dir web exec vitest run --maxWorkers=1
pnpm --dir web build
pnpm --dir web exec playwright test --workers=2
pnpm --dir web exec playwright test e2e/app-auth.spec.ts e2e/app-layout.spec.ts e2e/backup-settings.spec.ts --workers=2
pnpm --dir web exec playwright test --config playwright.operator.config.ts --workers=2
```

Results: shared **599**, source runtime **34**, container lifecycle **1**, image
journeys/publication **6**, frontend unit **297** all passed. Docker build,
TypeScript and frontend build passed. Existing >500 kB JS advisory remains.
The initial complete browser run was **104 passed / 2 failed**; fixes below and
**51 passed** across the three affected files close both failures. All **18**
operator journeys passed separately. Do not present that as one clean full-browser run.
The final image rerun includes all seven moderation commands and trusted-IP refusal.

Backend commands (the first requires an isolated application data directory):

```sh
EDITARR_DATA_DIR=/private/tmp/css66-backend-tests .venv/bin/python -m pytest -q tests/test_setup_restore_api.py tests/test_backup_activation.py tests/test_backup_restore.py tests/test_portable_backup.py tests/test_community_rotation_backup.py tests/test_community_recovery.py tests/test_community_verified_import.py tests/test_community_donation_reviews.py tests/test_community_import_channels.py tests/test_audio_track_execution.py tests/test_audio_tracks.py tests/test_audio_tracks_api.py tests/test_community_votes.py tests/test_community_local_downloads.py tests/test_mcf_final_integration.py tests/test_edl.py tests/test_auth_policy.py
.venv/bin/python -m pytest -q tests/test_community_donations.py tests/test_community_donation_vectors.py tests/test_community_matching.py tests/test_community_publications.py tests/test_community_import_receipts.py tests/test_community_sharing.py tests/test_review_repository_create.py tests/test_review_repository_save.py tests/test_review_repository_export.py tests/test_review_repository_integrity.py
.venv/bin/ruff check tests/community_node_import.py
.venv/bin/ruff format --check tests/community_node_import.py
git diff --check
```

Backend results: **297 + 215 passed** in disjoint file lists. The Node source
sharing journey additionally invokes the Python bridge with actual HTTP responses.
Provision the repository `.venv` and build operator assets before `test:runtime`.
The service image deliberately contains neither Python nor the local application.

## Findings, recovery and limits

- CSS-65's unauthenticated scheduled-backup link inherited the browser's dark blue
  link color (1.77 contrast). A scoped stylesheet rule now uses the established
  readable link color. Both access modes, 320/1440px, keyboard focus and axe pass;
  `web/test-results/css66-backup-signin-{320,1440}.png` were inspected.
- The old scan browser fixture lacked the mandatory audio-track probe/selection
  responses. The fixture now supplies an explicit single/default track and its
  selection response; the UI's safety requirement remains intact.
- Hourly maintenance moved into a focused reusable runtime module. It preserves
  the one-hour interval/transient deletion scope, catches synchronous and rejected
  cleanup failures with redacted reporting, prevents overlap and drains on shutdown.
- Sandbox socket/log refusals were rerun with the required execution permission.
  Initial Python collection tried the installed app's lock location; it was refused,
  then rerun with the isolated data directory. No personal DB was used. The former
  provider type-generation step is retired; current checking is direct `tsc`.
- No schema, protocol, signature, key policy, license or retention policy
  changed. Larger write caps exist only in the disposable workflow fixtures; runtime
  defaults remain unchanged. Existing backup/upgrade checks retain recoverable copies.
- Live Discord/Tunnel/Access, AMD64/native builds, real Mac cold boot, capacity,
  disk-full and independent recovery remain explicitly unverified with the owners
  above. CSS-25's separate October 7 handoff now records the completed local audit;
  its additional checks do not certify any live ingress or release gate.
  CSS-62 automatic donation and CSS-64 retention remain deferred.

## Node-only tooling update

October 7: all 599 shared tests now use Node Vitest and disposable file-backed SQLite
through the production adapter. Locally owned runtime types replace generated provider
declarations; `build`/`build:container` use esbuild, and `dev` launches the Node server.
The default `test` command also runs 34 CLI/offline recovery and 35 source-runtime
checks. Provider export and its mocks are retired; legacy archive recovery remains
offline and disabled. No migration, existing database or service activation is implied.
Build/runtime stages contain no provider deployment or test-pool dependencies.
`cloudflared` for Tunnel/human Access remains a separate selected ingress tool.

The updated Node 24 ARM64 image was rebuilt with frozen dependencies and passed the
isolated crash/restart/verified-snapshot container check plus all six production
HTTP workflow checks. Existing service volumes and live integrations were untouched.
These checks are not AMD64, real Mac cold-boot or off-device recovery certification.

Next: CSS-67. Start with this matrix and the Docker runbook, verify packaged local
assets/migrations and image/platform behavior, then document the user/operator
workflows. Obtain the brief's explicit approval before any existing-data cutover.
