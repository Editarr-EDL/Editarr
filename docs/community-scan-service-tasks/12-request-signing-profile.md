# CSS-12 — Freeze and implement the signed-request profile

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE**, September 26, 2026. Evidence and handoff follow.

- Prerequisites: [CSS-00](00-repository-preflight.md), [CSS-22](22-worker-scaffold.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §2.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Ed25519 is confirmed. HTTP signing requires unambiguous covered components and exact body bytes; signing reserialized JSON is incorrect.

## Starting points

contracts/community/http-signatures/; scanner/security/; focused TypeScript signing/verification utilities.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Specify a narrow versioned RFC 9421/9530 profile covering method, authority, path/query, type/encoding, body digest, times, nonce, key ID and mutation operation ID.
2. Implement pure Python signing and TypeScript verification helpers using supported standard Ed25519 interfaces.
3. Create normative byte fixtures for empty bodies, encoded queries, compression, clock skew and malformed headers; separate protocol constants from tunable policy.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Tampered method/path/body/encoding/audience fails; duplicate/ambiguous signature fields fail closed.
- [x] Both languages validate the same independent vectors; no legacy NODE-ED25519 or invented cryptography.
- [x] No network routes or enrollment bypass are enabled here.

## Verification

- Run positive/negative cross-language fixtures and existing crypto regression tests.
- Review the profile against linked primary RFCs before freezing it; document any unresolved interoperability issue.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide exact canonical byte contract, fixture commands and verifier API to CSS-25/29; request replay persistence is not completed here.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: RFC 9421 Ed25519 signing in
Python and strict RFC 9421/9530 verification in workerd, with a versioned fixed
covered-component list, SHA-256 of exact transmitted bytes, canonical
Structured Fields and URL validation. Three deterministic independent
Python-signed GET, JSON mutation and gzip mutation vectors pass in both
languages; malformed/duplicate inputs, clock skew, wrong authority/key and
tampering fail closed. See [exact canonical byte contract and fixture commands](decision-evidence/12-request-signing-profile.md).

Files changed and relevant revision: `scanner/security/community_signatures.py`,
`tests/test_community_request_signatures.py`, `community-service/src/security/request-signatures.ts`,
`community-service/test/request-signatures.test.ts`,
`community-service/test/fixtures/request-signatures-v1.json`,
`community-service/{package.json,pnpm-lock.yaml}`, contract/evidence docs and
task index. No commit, migration, new route or release build.

Contracts / APIs / schemas / migrations / fixtures introduced: Python
`sign_request` returns wire headers and diagnostic `signature-base` bytes;
Worker `verifyRequestSignature({method,url,headers,body}, rawPublicKey, policy)`
returns parsed nonce/key/operation ID and time/digest only after verifying
the digest and Ed25519 signature. `policy` supplies trusted authority/key ID,
Unix-second time, maximum lifetime and allowed skew. The signed fixture
includes exact base bytes, public key, transmitted body and wire headers.
`structured-headers@2.1.0` parses Worker Structured Fields. No persisted state.

Decisions used (gate IDs and recorded answers): D20 retained Ed25519 in the
parent plan. No CSS-12 gate. Tunable time/skew and deployment limits remain
caller policy, not invented product defaults.

Verification commands and results: `.venv/bin/python -m pytest -q
tests/test_community_request_signatures.py tests/test_auth_crypto.py` (24 passed),
`.venv/bin/ruff check scanner/security/community_signatures.py
tests/test_community_request_signatures.py` (passed), `pnpm --dir
community-service exec vitest run test/request-signatures.test.ts` (5 passed),
`pnpm --dir community-service run test` (33 passed), `pnpm --dir
community-service run typecheck` and `pnpm --dir community-service run build`
(both passed, dry-run only). `git diff --check` passed.

Known failures, limitations, unavailable checks, and recovery notes: No
live hosted request, deployment, replay DB, enrollment, current-key status
lookup, idempotency commit or user-generated signature round-trip was tested.
The build bundles only the existing scaffold route; CSS-25 must wire the
verifier before it protects any traffic. Caller must provide original HTTP
content bytes, not decompressed or reserialized content, and must use trusted
authority/key lookup. The strict profile deliberately does not accept all
RFC-permitted alternative encodings. Key freshness and nonce uniqueness are
not provided by cryptographic signature verification alone.

Remaining work in this task: None.

Next task ID and exact first action: CSS-13; read this byte profile and the
artifact codec handoff, then bind immutable manifest bytes to a detached
contributor signature. For CSS-25, start with trusted key-status lookup and
durable atomic `(key_id, nonce)` reservation plus mutation idempotency before
wiring `verifyRequestSignature` into routes. CSS-29 must transmit the exact
bytes signed by `sign_request`.

Direct dependent tasks: [CSS-13](13-artifact-signatures.md), [CSS-14](14-local-key-storage.md), [CSS-25](25-worker-request-auth.md).

