# Community activities

## Sub-features

- Authorized media search and imported shared evidence.
- Contributor-private receipts and votes, including explicit admin receipt recovery.
- Session-bound exact previews and current-policy review publications.
- Original-contributor management, live revocation, and scoped publication audits.

## How to get to it (user POV)

Community results are in Find Filters. Sharing is available on completed scans
and saved reviews. My Profile contains the account's own community identity and
receipts, independently of provider-settings permission. Connections retains its
existing personal panel. Authorized administrators open Users, View, Community
connection for a named ordinary account. Protected accounts and each own/other
grant are checked before rendering. Endpoint authorization is independent of
whether a control is displayed.

## Driving it with verify.mjs

The browser journey uses the existing `MockService` and
`ActivityService` at `community_identity_api.service_transport`. The production
API, authentication, capabilities, CSRF, preview cache, and database stay real.
The disposable fixture learns public authorization URLs
only after the UI starts enrollment, then verifies the real confirmation secret
and HTTP signatures. No hosted service or Discord account is contacted.

```sh
EDITARR_VERIFY_PNPM_CJS=/absolute/path/to/provisioned/pnpm.cjs \
PLAYWRIGHT_BROWSERS_PATH=/absolute/path/to/existing/playwright-browsers \
node .github/skills/verify-editarr/scripts/verify.mjs auth --community --ports=9100-9119
```

The explicit pnpm path creates a run-owned executable shim; it does not install
dependencies or restore host cache roots. The `--community` journey refuses to
start without it. It also needs `pnpm --dir community-service install
--frozen-lockfile` (esbuild and zod): the double builds the service's validators
offline before launch, and a failed build exits 2 BLOCKED as `communityContract`. Point it at the Corepack-provisioned pnpm matching the
`packageManager` field in `web/package.json` (currently `pnpm@11.22.0`), for
example `$HOME/.cache/node/corepack/v1/pnpm/11.22.0/bin/pnpm.cjs`. Resolve it
from the real HOME before isolating HOME and XDG_CACHE_HOME, and set
`COREPACK_ENABLE_NETWORK=0`. This journey launches an API-only
controller plus Vite, creates the owner through the UI, admits member/admin
accounts as Jellyfin users with one Jellyfin sign-in each (`admitJellyfinUser`),
then syncs so they read the synthetic catalog's libraries, and checks own enrollment and receipts, named
connection actions, concealed/protected account refusals, original-contributor
saved jobs, acting-admin previews, and stale-source refresh. It captures axe,
overflow, screenshots and ARIA snapshots at 1440 and 320 pixels. The API entry
point refuses an existing database and requires the run's verification marker.

The journey then proves each community write against the real app
(`lib/community-write-probes.mjs`, evidence `community-write-probes.json`). The
owner withholds each capability in turn and restores it afterwards. From the
member it withholds `community.search` and the own `identity.view`,
`identity.profile`, `identity.keys`, and `publications.check`, `retry`, and
`retract` capabilities. The member's matching lookup, status read, rename, rename
cancel, key rotation, check, retry, and retract each get 403. From the admin it
withholds the `.other` profile, key, and publication capabilities. The admin's
named rename and rotation status get 403, and its check, retry, and retract of the
member's publication get a concealing 404. Fresh reads and the double's request
log show that no refused request changed state or reached the service.

With the grants restored, the member's lookup gets 200. The admin's check and then
the member's check each add one service request. The member's retry publishes the
first publication, and the admin's retraction sets its `cancelRequested`. The member
queues a second publication. The admin's retry publishes it, and the member's
retraction sets its `cancelRequested`. A fresh read follows each write. Repeating a
retry or retraction on the first publication changes nothing. The member renames
itself, and the admin restores the name through the named route. The member's
rename cancel (`DELETE`) answers 200 with no rename pending and the name unchanged.
The member rotates its key (a new `keyId` reads back), and the admin reads its
rotation status.

The service double is labelled. `lib/community_double.py` judges every request
with `lib/community-contract.mjs`, which bundles the community service's own route
methods and body validators. It answers lookups with `missing_identity`. Contributors
start with distinct names. A rename to a name another contributor holds, in any
case, gets 409 `display_name_taken`, and reusing an operation ID with a different
body gets 409 `operation_conflict`. `tests/test_verify_community_double.py` pins
these rules. Votes stay unproven, because they need imported revisions from a live
service.

The printed evidence directory contains `doctor.json`,
`community-result.json`, `community-steps.json`, `community-http-statuses.json`,
`community-axe.json`, `community-double.json`, masked captures, `cleanup.json`,
and `redaction.json`. After cleanup, run `verify.mjs doctor EVIDENCE`; it must
fail because the recorded process group stopped. Retain failure evidence too.

To exercise the local status routes:

Run from the assigned source checkout with disposable HOME and XDG_CACHE_HOME.
Use a provisioned pnpm executable on PATH without exposing host model caches.
If that HOME hides the installed browser, set PLAYWRIGHT_BROWSERS_PATH to its
existing Playwright browser directory. Do not install another browser or restore
host model-cache variables.

```sh
node .github/skills/verify-editarr/scripts/verify.mjs auth --reviews --ports=9100-9119
```

The existing authentication and review journey launches and doctors the real app,
creates accounts through supported APIs, and imports synthetic media evidence.
The member submits `POST /api/community/downloads/status` for that manual target.
The owner denies `community.download` through the permissions API. The same
member request must return 403 `capability_denied`, with unchanged scan, review,
import and operation counts from an independent read-only SQL connection.
Restoring inheritance permits the request again. `community-activities-result.json`
records 200, 403 and 200. Existing review captures cover 1440 and 320 pixels.

Keep `doctor.json`, the community result, HTTP statuses, screenshots and
`cleanup.json`. Confirm process shutdown, scratch removal and retained evidence.
After cleanup, `verify.mjs doctor EVIDENCE` must fail for the stopped instance.

For signed publication, receipt and vote checks, run the existing transport-boundary
tests with isolated data/log/cache roots and an OS-temp TMPDIR.

```sh
.venv/bin/python -B -m pytest -q -p no:randomly -p no:cacheprovider tests/test_community_activity_access.py tests/test_community_activity_http.py tests/test_community_activity_feedback.py tests/test_community_import_receipts.py tests/test_community_votes.py
```

These tests use the existing signature-verifying community service double. They
record contributor IDs, not credentials. They cover two-contributor shared import,
receipt and vote isolation, admin management, hidden and absent IDs, stale preview,
capability changes at the write lock, disconnect-before-send, and revoked download
after artifact verification with no import writes.

## Gotchas

The earlier real-app status check is local and makes no service request. The
The browser journey covers enrollment, connection and publication UI against the signed
double, not the live community service. Receipt retry, vote transport and queue
lifetime cases also have component/HTTP tests. Neither recipe certifies live
providers, inference, installed runtime, account deletion or the separate
community Docker service.
Never use a real contributor, database, media library or signing identity.
Do not edit Python source while the development verifier is running.
