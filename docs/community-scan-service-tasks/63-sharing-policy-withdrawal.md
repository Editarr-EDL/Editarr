# CSS-63 — Enforce standing reuse and operator moderation

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally/mock, October 4, 2026**. New and pending publications require
signed `community-reuse-1`; referenced originals must retain compatible permission
and accurate author attribution. Operator moderation preserves signed evidence.
No author withdrawal, destructive retention, migration or deployment was added.

- Prerequisites: [CSS-39](39-publication-service.md), [CSS-46](46-manual-sharing-ui.md), [CSS-56](56-moderation-service.md)
- Decision gates: [G-LICENSE](decisions.md#g-license)
- Parent sections: main D13; contracts §4/6/7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

October 3 G-LICENSE selects `community-reuse-1`: retain public notes, preserve
original attribution and change history, require rights-to-share / exact-preview
confirmation, and permit copying/sharing/modification. There is no license picker,
named CC or noncommercial restriction, or author withdrawal. CSS-46 implements the
local preview/approval boundary. This task enforces the rule on the service and
integrates approved operator moderation; dependencies remain required.

## Starting points

artifact terms validation; operator service; CSS-46 preview/approval; documentation.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Enforce the approved standing permission version and rights confirmation without rewriting existing signed author claims or silently relicensing imported evidence.
2. Integrate approved operator discovery/download moderation with original attribution, derivative history and retention intact. Ask before any unspecified destructive retention policy.
3. Omit/disable author withdrawal endpoints and controls. A local publication cancellation stops future attempts or reconciles an uncertain outcome; it is not public permission revocation or remote deletion.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] No permission is silently assigned to third-party/manual/derived contributions; original signed claims remain intact.
- [x] Contributors cannot withdraw public contributions; only authorized operators can apply the approved moderation actions. Existing downloaded evidence is not secretly deleted.
- [x] Cache/D1 lifecycle follows approved visibility moderation while signatures and attribution remain intact; destructive retention remains separately gated in CSS-64.

## Verification

- Test permission versions, original/derived attribution, disabled author withdrawal and operator/nonoperator authority against approved examples.
- Verify cached discovery/downloads reflect approved operator moderation and no retroactive artifact mutation occurs.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met locally/mock, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied for local/mock scope.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide standing-permission/moderation state machine and policy version to CSS-64/67/69.

## Handoff summary for the next task

DONE locally/mock, October 4, 2026, GitHub Copilot. Clean starting worktree; no
commit. No existing database, secret, migration, deployment, real-content publication,
frontend, native application or running server was changed.

### Permission Boundary

`community-service/src/contributions/permission.ts` owns `SHARING_PERMISSION`,
`requireSharingPermission` and `validateParentPermission`. `publishArtifact` checks
the signature first, then requires exact signed `sharing_terms: community-reuse-1`
before new publication or completion of a pending reservation. This is the author's
authenticated permission assertion, not proof of legal ownership. CSS-46 still owns
the local rights-to-share and exact-preview confirmation. No unsigned boolean or
new wire field substitutes for that approval.

`revisionStatements` receives publication-only `permissionBounds` and includes
original parent bytes/author in its existing bounded lineage query. Every referenced
original must exist, match its SHA-256 and immutable revision/author binding, parse
within explicit artifact bounds and declare the same permission. V2 source snapshots
referencing that revision must name its original contributor. Missing/corrupt originals,
incompatible terms and substituted authors fail closed. Parent limits, depth, published
state and statement/query bounds remain enforced. Parent parsing is included in the
publication wall-time guard. Pending reservations undergo the same policy checks;
their original bytes and operation are never rebuilt or overwritten.

Generic artifact parsing/signature verification and internal catalog staging remain
compatible with historical claims. Completed published-operation retries return their
original saved result after exact-byte/signature checks, without assigning new terms.
Historical signed evidence is not silently relicensed, deleted or hidden solely for
having older terms; it cannot be used as a new derivative's compatible original.

HTTP 400 codes are `sharing_permission_required`, `source_permission_unknown`,
`source_permission_incompatible` and `source_attribution_mismatch`. The local
`integrations/community/transport.py` allowlist preserves these safe codes;
`publications.py` marks them terminal `rejected`/`failed`, retains exact evidence and
checks owned status without another POST on explicit retry. Existing pause/cancellation
semantics remain unchanged. No API, artifact, signature or schema version changed.

### Moderation And Retention

The existing CSS-55/56 Access/CSRF authority and audited optimistic actions remain
the only public moderation boundary. Revision `published -> quarantined -> published`
changes availability, not permission or evidence. Contributor suspension hides authored
revisions; unsuspension does not unquarantine them or rotate keys. Epoch invalidation,
fresh author checks and final visibility fences protect cached discovery/downloads.
All three signed BLOBs, revision authorship, parent edges, import/vote facts and local
downloaded evidence remain intact. No cascading derivative deletion is introduced.

Contributor withdrawal/deletion routes remain absent (404); local cancellation is
only a send/reconciliation control, not remote deletion or permission revocation.
Legacy `withdrawn` schema values remain inert historical state, not new author authority.
Payload/revision deletion guards and storage counters are unchanged. CSS-64 must obtain
explicit G-RETENTION answers before any deletion, TTL or new cleanup migration. If
future retention removes original bytes, it must separately resolve the fail-closed
parent permission check rather than fabricate terms from metadata.

### Verification

- `pnpm --dir community-service exec vitest run`: **599 passed / 84 files** after
  implementation and fixture corrections. Signed absent/future/other terms, v1/v2,
  exact attribution/derivative history, incompatible historical permission, corruption,
  historical completed retries, absent author withdrawal, operator/nonoperator/CSRF,
  warm-cache/final visibility races and immutable moderation evidence are covered.
- Final pending-reservation bypass regression: `pnpm --dir community-service exec
  vitest run test/publication.test.ts`: **7 passed**. `pnpm --dir community-service
  exec tsc --noEmit`: passed after that final test addition.
- `pnpm --dir community-service exec vitest run test/publication.test.ts
  test/publication-http.test.ts test/publication-legacy.test.ts
  test/publication-safety.test.ts test/publication-failures.test.ts
  test/publication-storage.test.ts test/publication-validation.test.ts
  test/catalog-lineage.test.ts test/catalog-search.test.ts test/catalog-http.test.ts
  test/download-http.test.ts test/moderation.test.ts test/moderation-http.test.ts`:
  **83 passed / 13 files** before the final pending-reservation test extension.
- `.venv/bin/python -m pytest -q tests/test_community_publications.py
  tests/test_community_sharing.py tests/test_community_transport.py
  tests/test_community_verified_import.py tests/test_community_artifact_signatures.py`:
  **92 passed**. Covers consent refusal, terminal remote policy errors/no repeated
  POST, exact retained evidence, local cancellation and previously downloaded imports.
- `.venv/bin/ruff check` and `ruff format --check` on changed Python files: passed.
  Editor diagnostics on all 14 touched code/test files: no errors.
- `node --input-type=module` documentation assertions: **7 documents / 208 local
  links**, balanced fences and consistent task/index status passed.
  `git diff --check`: passed.

Frozen cross-language vectors remain unchanged. Test-only `signedArtifact` reuses
existing signing machinery to create fresh consented donations; legacy reservations
are seeded from those same exact buffers. Initial fixture mismatches and the transport
allowlist omission were caught and repaired by focused checks. One mistyped Python
test filename prevented that batch from running; the corrected command passed above.

No frontend/browser, full Python, packaging, hosted CPU/provider cost or live publication
checks were run; no corresponding readiness is claimed. Parent BLOB parsing adds bounded
local work to the existing lineage read, not another SQL query or new D1 table. Large
parent/capacity and live CPU evidence remain CSS-68. Production remains disabled.

### Next Action

CSS-64: read [its brief](64-retention-cleanup.md), G-RETENTION and this state machine;
prepare the exact retention/deduplication matrix and ask for unresolved policy before
changing deletion guards. CSS-67/69 must document standing reuse and reversible
operator visibility moderation, no author withdrawal, and CSS-62's explicit deferral.

Direct dependent tasks: [CSS-64](64-retention-cleanup.md), [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md), [CSS-69](69-pilot-and-launch-scope.md).

