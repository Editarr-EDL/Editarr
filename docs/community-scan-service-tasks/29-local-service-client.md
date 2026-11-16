# CSS-29 — Add the signed local client and enrollment transport

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE**, October 1, 2026, local/mock scope. Authority-bound signed
transport, durable administrator-confirmed enrollment/profile operations and
protected local APIs are implemented. CSS-30 owns the settings UI; live provider
setup and deployment remain OPS-01/02.

- Prerequisites: [CSS-14](14-local-key-storage.md), [CSS-21](21-local-community-state.md), [CSS-25](25-worker-request-auth.md), [CSS-28](28-enrollment-profile.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §1–2 and §7; Discord §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The browser uses local Editarr APIs; only the local backend holds signing keys. Interrupted requests need durable operation IDs and fresh nonce/signature retries.

## Starting points

scanner/community/ transport and enrollment modules; scanner/http/ routes; web/src/api/ types.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement configured HTTPS endpoint/audience validation, signing, bounded timeouts/backoff and machine-readable error mapping.
2. Drive hosted enrollment from a privileged local route and redeem handoff with proof of the initiating key.
3. Persist identity and pending operation state atomically; enforce local admin/CSRF and redact payload/query/secret logs.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Replayed handoff, wrong endpoint/audience and unexpected redirects cannot leak authority.
- [x] Restart recovers an operation without duplicate identities; network failure is never reported as no matches.
- [x] Frontend receives safe connection status/URLs only, never private keys or provider tokens.

## Verification

- Use mocked Worker contract tests for request signing, retries, clock skew, cancellation and lost enrollment response.
- Run local auth and reverse-proxy/subpath regressions.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for local/mock scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide client API, typed failure states, endpoint configuration and durable retry hooks to CSS-30/32/45/48.

## Handoff summary for the next task

October 1 completion, GitHub Copilot (uncommitted workspace changes):

`scanner/integrations/community/transport.py` exposes `ServiceEndpoint` and
`CommunityTransport.request`. Configure `EDITARR_COMMUNITY_SERVICE_URL` as a
canonical HTTPS origin without a trailing slash/path/query and
`EDITARR_COMMUNITY_SERVICE_AUDIENCE` as its exact canonical authority. Both are
unset by default; no community network activity starts at application startup.
Endpoint changes cannot send an enrolled key to another service. No redirects,
environment proxies, cookies or provider tokens accompany backend requests.
Exact bytes and operation ID survive three bounded attempts; each attempt has a
fresh 128-bit nonce and 60-second signature. Connect/read timeouts are 5/10 seconds,
the total request deadline is 35 seconds, Retry-After waits are capped at two
seconds, and responses are bounded to 16 KiB with compression rejected.
Cancellation propagates instead of silently retrying. Clock errors fail signature
validation rather than trusting a server clock adjustment.

`scanner/integrations/community/client.py` drives browser start, completion,
current-profile refresh and exact rename intents. Start stores a backend-only
32-byte hex secret before returning `/v1/discord/start` with only the installation,
public key, key ID and domain-separated confirmation hash. This browser request
sets the hosted HttpOnly cookie; never replace it with a backend POST whose cookie
would not reach the browser. CSS-30 must obtain the hosted confirmation URL's
public state ID and ask for explicit local confirmation before completion.
The first approved completion durably binds that state ID; another state conflicts.
Retries retain the original body/state/secret, refresh signatures and reconcile
within the grant window. The local pre-OAuth journal has a conservative 20-minute
deadline (OAuth plus grant); binding the state limits it to another ten minutes
or the earlier deadline. Expired secrets are removed on the next local access.
Success atomically registers the profile and clears the pending secret.

`scanner/security/community_client_state.py` owns the optional version-1
`client_state` extension inside the existing private `community-identity-1`
manifest: pending enrollment, sanitized current profile, and optional exact
rename operation/name. Existing manifests remain readable. Writes use the
existing owner-private, maintenance-aware process lock, compare-and-publish and
atomic replace/fsync; there is no new private file or database schema. Both full
and settings backups retain the journal. Archives explicitly inventory pending
`community-enrollment-confirmation` secrets; old inventories remain accepted.
The manifest limit is now 16 KiB, including restore identity inspection.
SQLite connection metadata is a projection: if the process stops after the
atomic file write but before its database commit, the next access reconstructs
the same connection with sharing still off. Existing enrolled identity conflicts
remain errors, never automatic key or contributor replacement.

Local API in `scanner/http/community_api.py`, registered by `scanner/api.py`:

| Route | Request / behavior |
| --- | --- |
| `GET /api/community/connection` | Safe status; refresh signed identity when enrolled. Unconfigured returns `configured: false`. |
| `POST /api/community/enrollment/start` | Persist/reuse pending transaction and return safe `authorizationUrl`. |
| `POST /api/community/enrollment/complete` | `{ "state_id": "<64 hex>", "confirmed": true }`; signed completion. |
| `DELETE /api/community/enrollment` | Discard local pending enrollment; never replace/revoke a key. |
| `GET /api/community/profile` | Refresh signed current profile. |
| `PATCH /api/community/profile` | `{ "display_name": "...", "operation_id": "..." }`; persist exact intent before sending. |
| `DELETE /api/community/profile` | Discard local rename retry intent, not an already committed hosted rename. Refresh before displaying its outcome. |

These routes require a local administrator session even under local-address or
external-auth bypass. Mutations also require existing same-origin/CSRF protection;
mounted public paths use the existing Base URL prefix. Responses are no-store.
Safe status fields are `configured`, `connected`, `profile`, `pending`
(`stateId`, Unix-second `expiresAt`, `awaitingConfirmation`), optional
`pendingRename` (original operation/name), and start-only `authorizationUrl`.
Error envelopes contain only `code`/`retryable`, with 409 for local/nonretryable
conflicts and 503 for retryable/unconfigured states; auth policy retains 401/403.
Transport errors never become an empty lookup result. `CommunityFailure` retains
safe hosted failure codes, including `recovery_required`, `key_inactive`,
membership/service failures, invalid signatures and rename conflicts. Unknown
payloads map to `remote_error`/`invalid_response`; no remote diagnostic text is
forwarded. Normal enrollment never invokes CSS-33 recovery.

`web/src/api/api-community.ts` provides typed safe status/profile DTOs,
`CommunityApiError`, enrollment start/complete/cancel, connection/profile refresh,
rename and cancellation wrappers. All use the existing local same-origin
credentials/CSRF transport and runtime subpath bootstrap. CSS-30 must render names
as escaped text, replace labels from every successful response, retain/reuse
`pendingRename.operation_id` and its exact name after interruption, and explicitly
discard a failed intent before choosing different bytes. Profile revisions cannot
go backwards; private Discord fields and legacy accounting fields are removed.

Decisions used: CSS-28's approved backend-held confirmation, same active-key retry,
three-word/current-unique names, unrestricted valid name changes and current-name refresh;
CSS-12 Ed25519 byte profile; CSS-14 portable identity; CSS-21 default-off sharing.
No production endpoint, membership policy, new recovery rule or spending default
was selected. CSS-25's implemented shared identity/admission hook is sufficient
for these hosted routes; its unfinished downstream mutations remain separate.

Final verification: 190 scoped backend tests passed (community client/state/API,
identity/repository/signatures, auth, deployment, backend reverse-proxy/subpath
routing, production frontend and portable backup/restore/activation). Frontend
API checks 21 passed and `pnpm --dir web build` passed. Worker checks 42 passed
across local-client contract, request signatures, enrollment proof and enrollment
profiles; Worker typecheck passed. Ruff and editor diagnostics passed; 119 local
documentation links and Markdown fences passed; `git diff --check` passed.
The final inventory formatting correction was followed by Ruff and three focused
contract/backup tests, all passing. Exact frozen synthetic vector is
`tests/fixtures/community/local-enrollment-request-v1.json`, reproduced by Python
and independently verified/consumed by Workerd. No live Discord, deployed service,
user database, model download or key rotation was used. Native Windows filesystem
verification and the CSS-30 browser UI are not claimed.

Exact commands used: `.venv/bin/python -m pytest -q` with
`tests/test_community_{transport,client,client_state,client_contract,api,identity,repository,request_signatures}.py`
and the named auth/deployment/production/backup files above;
`pnpm --dir web exec vitest run src/api/api-community.test.ts src/api/api.test.ts`;
`pnpm --dir community-service exec vitest run test/local-client-contract.test.ts
test/request-signatures.test.ts test/enrollment-proof.test.ts
test/enrollment-profiles.test.ts`; `pnpm --dir community-service run typecheck`
and `pnpm --dir community-service exec tsc --noEmit`; focused `.venv/bin/ruff check`.

Next exact action: CSS-30 consumes these wrappers to render connection/profile
settings, hosted browser authorization, explicit local state confirmation and
pending retry/error states. Preserve backup guidance, escaped labels and no
implicit recovery/rotation. CSS-32 owns replacement-key reconciliation; CSS-45/48
use the transport with their existing durable operations rather than generating
new IDs on retries. Artifact upload-specific metadata and lookup routes belong
to those downstream tasks, not this JSON enrollment/profile client.

Predecessor guidance retained below:

CSS-28 October 1 hosted contract is complete locally. Start by reading
[its handoff](28-enrollment-profile.md#handoff-summary-for-the-next-task) and
[shared client fixture](../../tests/fixtures/community/enrollment-profile-v1.json).
Persist a backend-only random confirmation secret with initiating installation/key
before OAuth; the browser start carries only its domain-separated hash. Local
admin/CSRF approval precedes signed completion containing the secret. Fresh nonce
retries keep state/operation and reconcile within the ten-minute grant expiry.
Retired keys fail; `recovery_required` belongs to CSS-33, not implicit replacement.
Every successful profile response contains current name and monotonic revision;
replace cached labels and refresh signed identity on connection/profile views.
Names are case-insensitive normalized-unique, three generated words, custom 3-40
code points, unrestricted valid name changes. Render only escaped text.
No local confirmation, transport or UI was implemented in CSS-28.

Leave this complete at handoff or interruption; keep status BLOCKED/IN PROGRESS when appropriate.

```text
Status / date / implementer:
Implemented outcome and last completed substep:
Files changed and relevant revision:
Contracts / APIs / schemas / migrations / fixtures introduced:
Decisions used (gate IDs and recorded answers):
Verification commands and results:
Known failures, limitations, unavailable checks, and recovery notes:
Remaining work in this task:
Next task ID and exact first action:
```

Direct dependent tasks: [CSS-30](30-connection-settings.md), [CSS-32](32-manual-key-rotation-ui.md), [CSS-33](33-identity-recovery.md), [CSS-44](44-verified-local-import.md), [CSS-45](45-local-publication-outbox.md), [CSS-48](48-local-lookup-adapter.md).

