# CSS-11 — Implement the selected bounded artifact codec

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Current storage: [D1-only storage](d1-storage-scope.md), updated October 3, 2026.
> Dated completion evidence below may describe the earlier storage implementation.

Status: **DONE** (September 26, 2026). The pure Python and Worker codecs,
versioned manifest, recomputed summary, and shared exact-byte vectors are in place.
Production upload/CPU limits and durable signatures remain downstream work.

- Prerequisites: [CSS-10](10-encoding-benchmark.md), [CSS-22](22-worker-scaffold.md)
- Decision gates: [G-ENCODING](decisions.md#g-encoding), [G-CONTENT](decisions.md#g-content)
- Parent sections: contracts §3–4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The artifact carries immutable evidence or authored filters, not a database dump. Formats must preserve unknown provenance, timeline meaning, and lineage.
G-ENCODING selects uncompressed JSON text for serialized D1 summaries with
separate indexed lookup columns, and gzip JSON for immutable D1 artifact BLOBs.
The CSS-10 compressed/expanded/event bounds were test-only; production resource
limits and free-plan CPU feasibility still require a separate decision.

## Starting points

contracts/community/; scanner/formats/community/; scanner/modeling/; community-service codec fixtures.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Under the approved JSON encoding and content rules, define versioned envelope, manifest, media/provenance/coverage/findings/derivation types and compatibility errors.
2. Implement pure codecs/validators and shared fixtures; recompute summary counts including overlapping channels and unclassified taxonomy.
3. Bound text/events/depth/compression expansion and reject nonfinite or out-of-timeline values without fetching embedded URLs. Include allowed public event notes/comments as bounded untrusted text, distinct from excluded transcripts/debug output.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Python and TypeScript agree on valid/invalid fixtures and exact stored bytes; future schema failures are actionable.
- [x] Manual, derived, partial and zero findings are represented honestly under the approved policy.
- [x] Mutable votes/rank/moderation are outside the signed author claims; private path/transcript fields are absent.

## Verification

- Run cross-language round trips and adversarial codec tests for NaN, bounds, compression bombs, unknown confidence and lineage.
- Record golden fixtures/schema identifiers for downstream consumers.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide schema/version, byte encoding, validator/count APIs, and compatibility fixtures to CSS-13/37/38/44.

## Handoff summary for the next task

The artifact/manifest/signature wire contracts are unchanged. Persist exact gzip
artifact bytes and original manifest/signature bytes in D1 through CSS-39; keep
indexed JSON summaries separate from the BLOBs.
See [current scope, schema and verification](d1-storage-scope.md).

## Historical completion evidence

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Bounded strict UTF-8 JSON and
stream-bounded gzip decoders preserve exact stored bytes in both runtimes.
Recomputed summary counts overlap `both` channels and preserve unclassified,
partial and unknown coverage. A versioned manifest binds SHA-256 of the exact
gzip bytes, identity, submission and typed recomputed summary. Manual/derived
lineage, explicit source/target timeline, MCF taxonomy and private-field
exclusion are validated. See the [v1 contract and golden byte vectors](decision-evidence/11-artifact-contract.md).

Files changed and relevant revision: `scanner/formats/community/{__init__,codec}.py`,
`tests/test_community_artifact_codec.py`, `community-service/src/artifact/{codec.ts,taxonomy.json}`,
`community-service/test/{artifact-codec.test.ts,fixtures/artifact-v1.json,fixtures/artifact-v1-gzip.json}`,
`community-service/{package.json,pnpm-lock.yaml}`, this brief, task index,
contract/evidence docs. No migration, release bump or enabled network route.

Contracts / APIs / schemas / migrations / fixtures introduced: Python
`ResourceBounds`, `parse_artifact(raw, bounds=..., compressed=True)` and
`parse_manifest(raw, artifact=..., bounds=...)`; TypeScript `parseArtifact` and
`parseManifest`. `community-artifact-1`, `community-summary-1` and
`community-manifest-1`; summary registry label `mcf-1.1.0`. Exact compact UTF-8
and 492-byte gzip SHA-256 vectors are in the contract evidence. Signed-byte
profile/detached signatures belong to CSS-13. R2/D1 persistence belongs to
CSS-38/39, local donation and import to CSS-37/44.

Decisions used (gate IDs and recorded answers): G-ENCODING JSON text D1 / gzip
JSON R2 and G-CONTENT partial and unknown-provenance scans plus previewed bounded
public notes, both confirmed September 26. G-LIMITS and free-plan CPU remain
open; callers must supply approved limits, and no production upload endpoint
exists. G-LICENSE remains deferred; nullable terms do not authorize sharing.

Verification commands and results: `.venv/bin/ruff format scanner/formats/community
tests/test_community_artifact_codec.py` (unchanged on last run), `.venv/bin/ruff
check scanner/formats/community tests/test_community_artifact_codec.py` passed;
`.venv/bin/python -m pytest -q tests/test_community_artifact_codec.py
tests/test_community_encoding_benchmark.py` passed 23. `pnpm --dir
community-service run typecheck`, `pnpm --dir community-service run test`
(28 tests), and `pnpm --dir community-service run build` all passed; `git diff
--check` passed before handoff. Both languages check shared raw/gzip hashes,
invalid schema, JSON duplicates, timeline, taxonomy, lineage, note/text/depth,
nonfinite scores and tampered manifest counts/digests/identity.

Known failures, limitations, unavailable checks, and recovery notes: Synthetic
bounds in tests are not approved production caps; no hosted free-plan CPU
measurement or live D1/R2 test was performed. Workerd bundle is still the
disabled local scaffold. Arbitrary author notes are untrusted public text;
CSS-37 must preview them and exclude secrets before any donation. Signatures
and admission are not implemented, so no caller may treat a validated artifact
as authenticated. No user database, personal media or remote resource touched.

Remaining work in this task: None. Apply an explicitly approved G-LIMITS
configuration before enabling any production upload.

Next task ID and exact first action: CSS-12; read the signed-request profile
brief and primary RFC 9421/9530 requirements, then freeze exact covered HTTP
components and implement Python signing / Worker verification vectors. CSS-13
can then bind the immutable manifest bytes to a detached contributor signature.

Direct dependent tasks: [CSS-13](13-artifact-signatures.md), [CSS-37](37-donation-artifact-builder.md), [CSS-38](38-d1-catalog-schema.md).
