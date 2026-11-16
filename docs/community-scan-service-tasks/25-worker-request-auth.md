# CSS-25 — Enforce request signatures and mutation replay protection

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally, October 7, 2026**. The current-route authorization audit,
shared regression suite and real Node HTTP/SQLite image/restart checks pass.
Verified time-window failures now return actionable `401 clock_skew` responses.
No production deployment or live Discord/Access/Tunnel behavior is certified.

- Prerequisites: [CSS-12](12-request-signing-profile.md), [CSS-23](23-d1-identity-schema.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §2.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Keys authorize actions only while active. Replay consumption and mutation state must
commit together; a process-local nonce cache is insufficient. The
[Docker runtime](../community-service-docker.md) must retain shared contracts across
real Node HTTP, SQLite transactions, caches and process restart.

## Starting points

community-service/src/security/; operations/; http/; admin/auth/;
community-service/src/runtime/ and test/runtime/; shared Node/SQLite fixtures.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Wire exact-byte request verification and authoritative key/contributor status into protected routes.
2. Implement transaction/reservation hooks consuming nonce with mutation state; separate idempotent result lookup from fresh-signature retries.
3. Permit only the documented bounded read replay behavior; attach an eligibility hook for CSS-27 before production requests.
4. Inventory every implemented route, including operator dashboard/moderation/CLI,
   against the shared authorization boundary and CSS-66's Node matrix. Test exact
   HTTPS authority/body/header preservation, malformed ingress, stale key/member/cache
   state, cross-contributor operations and interrupted/restarted mutation recovery.
   Mock providers only for local tests; OPS-01/02 own actual Discord/Access/Tunnel proof.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Retired/revoked keys fail across isolates and caches immediately; cached public bytes never imply permission.
- [x] Concurrent duplicate mutation cannot execute twice; same operation ID with changed payload conflicts.
- [x] Authenticated contributor cannot read another operation or reach operator actions; clock skew errors are actionable.
- [x] Current-route Node HTTP/SQLite tests preserve these guarantees after process
  restart; synthetic tunnel/JWT evidence is not recorded as live ingress certification.

## Verification

- Run adversarial real Node HTTP/SQLite replay/idempotency/restart fixtures and retain
  shared Node/SQLite regression tests. Reuse CSS-66's harness rather than duplicate it.
- Test retirement between cached verification and authoritative mutation authorization.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met locally, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide middleware/transaction hook, covered headers, replay contract and explicit eligibility integration requirement to CSS-27/28/31/39.

## Handoff summary for the next task

DONE locally, October 7, 2026, GitHub Copilot. Clean starting worktree; no commit,
migration, dependency, existing-database write, real credential, deployment or
public activation. Synthetic providers are not live ingress certification.

### Authorization And Replay Contract

`src/security/contributor-auth.ts` resolves the registered key's installation and
contributor, verifies exact bytes against the configured HTTPS authority, requires
CSS-27 bot membership, then rechecks active key/contributor state. Cached public
material never authorizes requests. Missing/stale unavailable membership fails
closed; verified evidence lasts 900 seconds. Retirement and revocation have no grace.

`src/security/request-signatures.ts` retains profile v1: `@method`, `@scheme`,
`@authority`, `@path`, `@query`, `content-digest`; mutations additionally cover
`content-type`, `idempotency-key` and `content-encoding` when present. Ordered signed
parameters remain `created`, `expires`, `nonce`, `keyid`, `alg`, `tag`. Lifetime is
at most 120 seconds, with five seconds of skew. Invalid structure/lifetime/digest
or crypto remains a generic failure. Only correctly signed out-of-window requests
raise `RequestSignatureTimeError`, mapped to no-store `401 clock_skew` on registered
and candidate-key routes. The local transport preserves that code without remote
detail or automatic retry; synchronize the system clock and explicitly retry the
saved intent with a fresh signature. No signed bytes or protocol version changed.

`admitSignedMutation` and the owning workflow transactions retain durable nonce,
owned operation, side-effect and final authority fences in one SQLite batch.
Fresh-nonce equal-intent retries recover the original result; changed payloads
conflict. Completed exact retries may return their stored result without repeating
side effects. An old successful rename retry cannot overwrite a newer name.
GET and catalog POST reads deliberately permit bounded replay inside the signature
window, write passive observations, and do not consume nonces or increment imports/votes.

### Current Route Inventory

Every service route is dispatched by `src/index.ts` behind the Node adapter's
configured Host, bounded body and duplicate-header checks. Unknown routes expose
no protected data. Unsupported method/content shapes are rejected before auth
where appropriate; rejection is not a signature bypass.

| Routes | Authority / durable behavior | Evidence |
| --- | --- | --- |
| `GET/HEAD /health`, `/ready`; `GET /v1/capabilities` | Public health/readiness; disabled/unavailable capabilities expose no protected data | Runtime HTTP/environment and shared scaffold tests |
| `/v1/discord/{start,recovery-start,confirm,callback}` | Trusted ingress, bound OAuth state, secure cookie and same-origin confirmation; fresh provider identity | `discord-*`, identity HTTP journey |
| `POST /v1/enrollments/complete` | Grant-bound candidate signature, private confirmation and current membership; atomic grant/key/nonce registration | Enrollment shared tests and identity clock/completion journey |
| `GET/PATCH /v1/identity` | Current active principal/member; PATCH atomically records nonce/owned rename | Identity matrix and interrupted/concurrent restart fixture |
| `POST /v1/keys/{rotate,revoke}` | Active owned installation; dual proofs for replacement; atomic nonce/result/key state | Lifecycle shared tests and identity matrix |
| `POST /v1/keys/rotation-status` | Exact prepared dual proof plus candidate signature; no general inactive-key authority | Lifecycle shared tests and identity reconciliation/clock journey |
| `POST /v1/keys/{recover,recovery-status}` | Fresh bound Discord grant or durable exact candidate recovery; current member and owned installation | Recovery shared tests and identity journey |
| `GET /v1/key-operations/{key_rotation,key_revocation,key_recovery}/{id}` | Active principal/member; contributor-scoped result only | Identity matrix, foreign-operation shared/HTTP tests |
| `POST /v1/contributions`; `GET /v1/publication-operations/{id}` | Active principal/member; atomic nonce/operation/catalog/exact BLOBs; owned reads | Publication shared tests, concurrent/rollback/foreign-result sharing journey |
| `POST /v1/availability/batch`, `/v1/matches` | Active principal/member on every cache hit plus final key/visibility fence; bounded read replay | Catalog shared tests and warm-cache HTTP journey |
| `GET /v1/contributions/{revision}/artifact`, `/v1/receipt-keys` | Active requester/member and current author/moderation fences; immutable cache only, private bound receipts | Download shared tests and warm-cache HTTP journey |
| `POST /v1/import-receipts` | Current principal/member and contributor-bound service receipt; atomic nonce/fact/result/counter | Import shared tests and concurrent sibling HTTP journey |
| `POST /v1/contributions/{revision}/vote`; `GET .../vote`, `.../votes` | Current principal/member; exact import eligibility; private own/public aggregate reads; atomic intent | Vote shared tests and concurrent/conflict/cache HTTP journey |
| `POST /v1/contributions/{revision}/reports` | Current principal/member and exact import; private report, atomic nonce/result | Report shared tests and HTTP journey |
| `/admin`, `/admin/*` assets/session/dashboard/inspection/reports | Independent Access JWT, exact origin/current subject allowlist; contributor headers never elevate | Access/dashboard shared tests and operator/identity HTTP journeys |
| `POST /admin/v1/moderation` and CLI calls to it | Same Access boundary plus exact Origin/operator header and same-origin mutation checks; audited action idempotency | Moderation shared tests and lost-response operator CLI HTTP journey |

### Changed Files And Verification

Changed service code: `src/security/{request-signatures,contributor-auth}.ts`,
`src/http/{enrollment,key-lifecycle,recovery}.ts`,
`src/identity/enrollment-proof.ts`. Local client:
`scanner/integrations/community/transport.py`. Tests:
`test/{request-signatures,enrollment-proof}.test.ts`,
`test/runtime/{journey,identity,sharing,restart}.fixture.mjs`,
`tests/test_community_transport.py`. No schema or persistent-state format changed.

Commands from repository root:

```sh
pnpm --dir community-service exec vitest run test/enrollment-proof.test.ts test/request-signatures.test.ts
pnpm --dir community-service test
pnpm --dir community-service typecheck
docker build -f community-service/Dockerfile -t editarr-community:css25-test .
COMMUNITY_RUNTIME_IMAGE=editarr-community:css25-test node --test community-service/test/runtime/journeys.test.mjs
node --test --test-name-pattern='identity journey' community-service/test/runtime/journeys.test.mjs
.venv/bin/python -m pytest -q tests/test_community_transport.py tests/test_community_request_signatures.py
.venv/bin/ruff check scanner/integrations/community/transport.py tests/test_community_transport.py
.venv/bin/ruff format --check scanner/integrations/community/transport.py tests/test_community_transport.py
git diff --check
```

Results: focused signatures/enrollment **30 passed**; full shared **565 tests / 82
files**, CLI **34**, source runtime **36**, image journeys **5** and Python **35**
passed. TypeScript, image/operator asset build, Ruff and editor diagnostics passed.
Final PATCH timing matrix and all five image journeys were rerun; the final Python
format correction was followed by **19 transport tests**, lint and format checks.
The prior 599-test count predates retired usage-admission tests and is not the
current total. Documentation links/fences/status and diff whitespace are checked
before handoff.

The actual Node 24.21.0 image executes built production service/HTTP/configuration
modules, with no external network and disposable SQLite. Restart tests discard a
committed mutation response, SIGKILL the real server, reconcile exact/fresh-signature
retries, reject changed intent/reused nonce, assert one durable mutation, and
retain retired/revoked/suspended denial across subsequent processes. Warm metadata,
artifact and feedback routes deny stale unavailable membership, known nonmembers,
prepared-before-retirement requests and revoked keys, while active siblings work.

Initial test-only failures were corrected: key content-type shape rejection is
400, expired signed enrollment now raises the typed time error, and the actual
name-event table is `profile_name_changes`. No production safety check was weakened.

### Limits And Next Action

No remaining local implementation work. Live Discord, actual Tunnel Host/IP isolation,
Access session/JWKS lifecycle, AMD64/native/target-Mac cold boot, capacity, disk-full
and independent recovery remain unverified under OPS-01/02 and CSS-67/68/69.
This task creates no certificate of live ingress or launch readiness. CSS-64 remains
explicitly deferred; no retention policy, usage allowance or automatic key rotation.
Backups retain current nonce/operation/key history; stale restore security-history
reconciliation remains required before separately approved activation.

October 7 successor update: the user canceled CSS-68 for local Docker on Mac mini.
Next **CSS-69** records the selected local-only pilot, disabled staging and open
future release gates. Preserve CSS-64 deferral; no replacement benchmark/recovery
drill or public activation is authorized. Synthetic auth evidence is not capacity
or independent recovery certification.

Direct dependent tasks: [CSS-27](27-membership-eligibility.md), [CSS-28](28-enrollment-profile.md), [CSS-29](29-local-service-client.md), [CSS-31](31-key-rotation-service.md), [CSS-39](39-publication-service.md), [CSS-41](41-catalog-lookup-api.md), [CSS-42](42-artifact-download-service.md), [CSS-51](51-import-receipts.md), [CSS-52](52-votes-service.md), [CSS-55](55-operator-access.md).

