# CSS-59 — Add operator maintenance CLI using shared admin services

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally/mock, October 4, 2026**. Human Access CLI authentication
was explicitly approved. Live Access/session setup, deployment and hosted
backup/restore jobs remain separate gates.

- Prerequisites: [CSS-39](39-publication-service.md), [CSS-55](55-operator-access.md), [CSS-56](56-moderation-service.md)
- Decision gates: [G-OPERATIONS](decisions.md#g-operations)
- Parent sections: implementation §4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Large exports/restores need resumable CLI tooling. Contributor keys cannot authenticate as operators, and a deployment token must not be printed or embedded in the dashboard.

## Starting points

community-service operator tooling; admin operation clients; documentation.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

CSS-58's [workflow/audit handoff](58-operator-dashboard-actions.md) supplies exact
moderation intents, optimistic tokens, protected transport and historical retry
semantics. G-OPERATIONS selects human cloudflared Access login; do not bypass
the browser CSRF boundary or treat contributor credentials as operator authority.
The user explicitly deferred separate enrollment/publishing pauses during CSS-58;
do not add those commands or change protected runtime configuration through this CLI.

1. Implement approved operator authentication and commands for status, moderation and approved maintenance job progress. Separate enrollment/publishing pauses are deferred. CSS-40 is retired; no publication recovery command or job is required.
2. Reuse audited server actions; accept secrets through protected configuration/input and provide dry-run/confirmation where impactful.
3. Expose reusable paginated export/import adapters for later backup tasks without implementing a second business-logic path.

Backup/restore adapters operate on one D1 snapshot including exact BLOBs and schema
revision. Reuse the CSS-39 storage contract; do not create an object-transfer command
or bucket credentials. Status should distinguish payload counters, measured D1 size
and daily usage; CLI fixtures must preserve binary bytes and redact protected secrets.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] CLI and dashboard apply identical permissions/conflict/audit rules.
- [x] Long operations resume by durable ID/cursor; output redacts authorization material.
- [x] No independent contributor-key rotation command or production reset shortcut exists.

## Verification

- CLI subprocess/fake transport tests for interrupted jobs, denied auth, confirmation and stale actions.
- Compare CLI/dashboard mutation outcomes against the same service fixtures.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide commands, authentication setup and resumable adapter interfaces to CSS-60/61.

## Handoff summary for the next task

Status / date / implementer: DONE locally/mock, October 4, 2026, GitHub Copilot.
Clean starting worktree; no commit. G-OPERATIONS explicitly selects **Human
Cloudflare Access login**. No server-side authorization changes, schema/migration,
new dependency, real secret, existing database, deployment or server restart.
Separate enrollment/publishing pauses remain explicitly deferred.

### Commands And Authentication

`community-service/scripts/operator-cli.mjs` is the thin Node entry point;
`scripts/operator/{transport,commands,state,transfer}.mjs` own bounded transport,
commands, private checkpoints and reusable page transfer. Use the existing package
Node/pnpm toolchain, plus independently installed cloudflared for actual human login.
No cloudflared installation or real login was performed during this task.

Once OPS-02 authorizes the domain/application/human subjects, log in locally:

```sh
cloudflared access login https://operator-host/admin/
pnpm --dir community-service operator -- status --origin https://operator-host
pnpm --dir community-service operator -- list operations --origin https://operator-host
pnpm --dir community-service operator -- inspect revision revision-id --origin https://operator-host
pnpm --dir community-service operator -- moderate quarantine revision-id --expected-revision 2 --reason 'Reviewed evidence' --origin https://operator-host
pnpm --dir community-service operator -- moderate quarantine revision-id --expected-revision 2 --reason 'Reviewed evidence' --confirm --checkpoint "$HOME/css59-action" --origin https://operator-host
pnpm --dir community-service operator -- retry --checkpoint "$HOME/css59-action" --confirm --origin https://operator-host
pnpm --dir community-service operator -- page operations --checkpoint "$HOME/css59-pages" --origin https://operator-host
pnpm --dir community-service operator -- page operations --checkpoint "$HOME/css59-pages" --resume --origin https://operator-host
```

These host/resource/revision values are placeholders, not configured production
defaults. `EDITARR_OPERATOR_ORIGIN` can replace `--origin`; it contains no credential.
`--help` lists commands, collections and vote identity options. Status preserves
distinct payload counters, partial daily observations and explicitly unavailable
measured D1/provider/backup data. List/inspect use only existing safe dashboard and
moderation endpoints. Operations retain contributor/kind/operation identity; they
are not fabricated maintenance-job progress. No approved maintenance jobs exist yet.

The CLI privately captures `cloudflared access token -app=<origin>/admin/`, then sends
`Cf-Access-Token` to Access. The edge must supply `Cf-Access-Jwt-Assertion`; the
Worker verifies its existing issuer/audience/expiry/type/current exact human
allowlist. Sending the CLI header directly to the Worker grants no authority.
Mutations send exact Origin and `X-Community-Operator: 1`; no browser context is
forged and no CSRF exception is added. Redirects/login HTML, foreign origins,
oversized (>131,072 bytes), malformed JSON/UTF-8 and unsafe errors fail closed.
15-second request/token retrieval bounds; no automatic retry. Child stdout/stderr,
headers and token values are never printed or persisted in journals. cloudflared
manages its own local Access token cache; protect that separately, never export it
as part of a service backup. JSON output escapes terminal control sequences.

Moderation requires the caller's inspected revision and a nonempty bounded reason;
default is fresh-state dry-run, including bounded credential/all-installation scope
for contributors. `--confirm` explicitly approves that command/target/token/reason.
A fresh snapshot mismatch aborts before sending. Before POST, a new 0700 directory
durably stores a 0600 journal containing origin, verified subject, exact UUID/body
and pending state. Exact result validation precedes applied acknowledgment. Network,
auth, storage, query-budget or invalid-success failures retain pending evidence;
explicit retry sends the same UUID/body and never advances the expected revision.
400/404/409 or moderation_ineligible requires fresh review, not an automatic new
action. Applied retry reads current state without resending; a historical result
never overwrites later state. Current-state fetch failure is explicitly unavailable.

Checkpoints contain private reasons/report data, not authorization material. Keep
them outside the checkout, protect them and retain uncertain action evidence until
resolved by exact retry or fresh audit inspection. Symlinked/foreign-owned/group-
accessible checkpoints fail closed. An exclusive lock rejects concurrent writers;
atomic file replacement and directory fsync preserve durable state. SIGINT/hard
termination can leave `lock`; after verifying no CLI process is using that directory,
remove only that lock manually and resume. Do not remove pending state or invent a
new ID. An empty checkpoint from interruption before the first durable state is not
resumable; inspect fresh state/audit before starting a new action directory.

### Adapter Handoff To CSS-60/61

`transferPage(cursor, read, commit)` awaits `read(cursor)` returning
`{cursor,nextCursor,data}`, awaits destination `commit(page)`, then returns
`{nextCursor,complete}`. A failed commit never advances the cursor; retries must use
the same source cursor and an idempotent destination keyed by job/page identity.
`data` is opaque and untouched: synthetic schema metadata and `Uint8Array` BLOBs
are preserved byte-for-byte. Use this same adapter for later export and import
directions; it has no independent database, moderation or backup business logic.

The `page` command demonstrates durable bounded transfer for existing inspection
lists: one page per invocation, origin/subject/endpoint-bound state and private page
files. An interruption after page storage but before cursor advancement resumes
the stored page rather than fetching a changed generation. Inspection pages are
**not a consistent D1 snapshot, backup or restore format**. CSS-60/61 must add their
approved job/snapshot identity, known schema revision, consistency protocol, exact
binary serialization/verification, secret inventory and idempotent destination;
none is silently selected here. No export/import/reset/rotation/pause command or
new server job endpoint exists. CSS-40 remains retired.

### Verification And Limitations

- `pnpm --dir community-service test:operator-cli`: **17 passed**. Native Node
  subprocesses, fake cloudflared/HTTP transport, dry-run/confirmation, auth denial,
  redaction, stale actions, all seven commands, exact interrupted retry, wrong
  subject/origin, unsafe/locked checkpoints, cursor recovery and exact BLOB adapter.
- `pnpm --dir community-service exec vitest run test/operator-cli-service.test.ts
  test/operator-access.test.ts test/moderation-http.test.ts test/moderation.test.ts
  test/operator-dashboard.test.ts`: **59 passed**. Actual shared D1/admin service
  and synthetic RSA JWTs, simulated Access forwarding, golden moderation outcome,
  verified actor audit, conflicts/retries/current state and dashboard unknowns.
- `pnpm --dir community-service exec tsc --noEmit`: passed.
- `pnpm --dir community-service operator -- --help`: executable/package entry passed.
- Scoped editor diagnostics, documentation links/fences/checklists and
  `git diff --check` verified at completion.

Initial integration fixture failures were corrected in tests: workerd Request does
not accept all Node fetch controls, and the golden moderation fixture represents
publication before 0014 version triggers; overview also requires 0011 usage rows.
No server behavior was weakened. Live Access/cloudflared login/cache/session
revocation and binding-cookie compatibility remain OPS-02 verification; do not
silently disable an Access cookie/security policy to make the CLI work. Hostnames,
identities, account CPU/billing, hosted D1, backup/restore, release packaging,
other operating systems and unrelated full suites were not exercised. Worker
build/browser/Python checks were not rerun: no production Worker/frontend/Python
code or dependencies changed. Production remains unconditionally disabled.

Remaining CSS-59 work: none in authorized local/mock scope. Real auth/setup remains
operational acceptance, not claimed complete.

Next task: **CSS-60**. Read its brief, CSS-39/42/56
inventories and this adapter contract. Prepare a concrete independent destination,
secret protection, retention and consistency proposal; ask G-SERVICE-BACKUP before
selecting a backup format/freeze/storage policy. Do not call inspection pages a
backup, apply existing-database migrations or enable production.

Direct dependent tasks: [CSS-60](60-hosted-backup.md), [CSS-67](67-packaging-and-user-docs.md).

