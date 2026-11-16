# CSS-13 — Sign and verify durable contribution artifacts

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Storage scope superseded October 3, 2026: [D1-only storage](d1-storage-scope.md).
> R2 references below record historical implementation/evidence, not current requirements.

Status: **DONE**, September 27, 2026. Evidence and handoff follow.

- Prerequisites: [CSS-11](11-artifact-contract.md), [CSS-12](12-request-signing-profile.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §4; Discord §2.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Request signatures expire; artifacts need a separate durable signature that preserves uploader attribution after download or key retirement.

## Starting points

scanner/formats/community/; scanner/security/; contracts/community/ signing fixtures; TypeScript codec helpers.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Bind manifest digest, artifact digest, contributor ID, schema and submission ID in the defined durable signature.
2. Implement creation/verification that stores exact original manifest/payload bytes and verifies against a trusted contributor/key binding.
3. Preserve historical public keys and model retirement versus compromise status separately; no private key material travels to the service.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] One-byte changes, wrong author/key/submission and mismatched manifest/payload fail.
- [x] A retired public key still verifies historical bytes but confers no current API authority.
- [x] Derivative signatures identify the new author while retaining original source references; no claim that signatures prove scan accuracy.

## Verification

- Use independent Ed25519 vectors and cross-language altered-byte fixtures.
- Verify original bytes survive serialization/storage round trips without resigning.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide detached-signature envelope and historical-key verification requirements to CSS-37/39/42/44.

## Handoff summary for the next task

Status / date / implementer: DONE, September 27, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Python signs the exact validated
gzip artifact and manifest bytes in a detached, versioned Ed25519 envelope;
workerd verifies the same original bytes against trusted historical key and
submission bindings. Independent original and derived Python vectors pass in
workerd. Retired keys can authenticate historical bytes, while compromised
or revoked keys cannot; no signature helper grants current API access. The
derived fixture signs new author/key/submission and retains the original
source reference. See the [byte contract and vector commands](decision-evidence/13-artifact-signatures.md).

Files changed and relevant revision: `scanner/security/community_artifact_signatures.py`,
`tests/test_community_artifact_signatures.py`, `community-service/src/artifact/signatures.ts`,
`community-service/test/artifact-signatures.test.ts`,
`community-service/test/fixtures/artifact-signatures-v1.json`, contract and
evidence docs, this brief, task checklist. No commit, migration or route.

Contracts / APIs / schemas / migrations / fixtures introduced: Python
`sign_artifact(private_key, artifact_bytes, manifest_bytes, bounds)` returns
the untouched original buffers plus canonical envelope bytes. Worker
`verifyArtifactSignature(artifactBytes, manifestBytes, envelopeBytes,
trustedHistoricalKey, expectedSubmissionId, bounds)` returns validated
payload/manifest, original buffers and historical key status after verification.
Binding is six length-prefixed UTF-8 fields plus raw SHA-256 manifest and
artifact digests under an ASCII domain separator; envelope version is
`community-artifact-signature-1`. Pinned vectors cover original/derived
compressed bytes, manifest digest, signature-base hash, public keys, signature.

Decisions used (gate IDs and recorded answers): D20 requires Ed25519, G-ENCODING
selects JSON/gzip; key-retirement rules are confirmed under D18/G-KEYS. No
new product decision or production resource limit was made in CSS-13.

Verification commands and results: `.venv/bin/ruff format --check
scanner/security/community_artifact_signatures.py tests/test_community_artifact_signatures.py`
and `.venv/bin/ruff check` on those paths passed; `.venv/bin/python -m pytest
-q tests/test_community_artifact_signatures.py tests/test_community_artifact_codec.py
tests/test_community_request_signatures.py tests/test_auth_crypto.py` (42 passed);
`pnpm --dir community-service exec vitest run test/artifact-signatures.test.ts`
(4 passed), `pnpm --dir community-service run test` (37 passed),
`pnpm --dir community-service run typecheck` and `pnpm --dir community-service
run build` (both passed, dry-run only). `git diff --check` passed.

Known failures, limitations, unavailable checks, and recovery notes: Python
and workerd gzip encoders do not produce identical bytes for the derived JSON;
the test freezes the actual Python gzip bytes instead of recompressing them
for verification. No live D1/R2 storage, key-registration history, public
route, current-key authorization, deployment, or accuracy/source-truth check
was performed. Production `ResourceBounds` and publication consent remain
unset. The key record and expected submission must come from trusted storage;
an arbitrary caller-supplied record cannot prove enrollment. Keep the original
source signature and bytes separately when storing derivatives.

Remaining work in this task: None.

Next task ID and exact first action: CSS-37; read the [byte contract](decision-evidence/13-artifact-signatures.md)
and the CSS-11 codec handoff, then build the previewed gzip artifact and
manifest using the local signing key without changing their bytes after
signing. CSS-39 must verify trusted authenticated submitter and preserve all
three byte buffers plus the historical key binding; CSS-42 must return those
buffers and public key history; CSS-44 must verify the detached signature and
source lineage before import without resigning original evidence.

Direct dependent tasks: [CSS-37](37-donation-artifact-builder.md), [CSS-39](39-publication-service.md), [CSS-42](42-artifact-download-service.md), [CSS-44](44-verified-local-import.md).

