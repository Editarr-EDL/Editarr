# CSS-46 — Build manual donation preview and sharing controls

Status: **DONE locally / mock services** October 3, 2026. G-LICENSE is resolved to standing community
reuse permission with retained notes, no license picker and no author withdrawal
beyond operator moderation. Production activation remains unauthorized.

- Prerequisites: [CSS-30](30-connection-settings.md), [CSS-35C](35c-audio-track-selection-ui.md), [CSS-37](37-donation-artifact-builder.md), [CSS-45](45-local-publication-outbox.md)
- Decision gates: [G-LICENSE](decisions.md#g-license), [G-CONTENT](decisions.md#g-content)
- Parent sections: main §7; D07/D09.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Manual sharing comes first. Both AI scans and authored/derived filters need an accurate outgoing-data preview and applicable sharing consent.

## Starting points

web/src/scans/ sharing components; web/src/api/; local consent endpoints.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add Share entry points for supported scan/review sources with name, provenance, coverage/counts, attribution, lineage and exact metadata preview, including all public free-text event notes/comments.
2. Require the approved sharing terms/ownership consent and bind confirmation to the prepared revision.
3. Display progress/retry/cancel/remote result and policy failures; keep automatic sharing separate and off.
4. For Other Files uploads, accept a TMDB/TVDB URL or typed ID and normalize supported URLs to typed associations. Require explicit user confirmation, not additional proof of media identity. Episodes retain explicit typed series/episode context and numbering. IMDb-only input does not qualify for upload. Apply the [October 2 decision](decisions.md#g-matching); reputation and recipient comparison/confirmation are the trust basis.
5. Reuse CSS-35C metadata display for the actually analyzed audio track's language and description/title; do not offer the file's alternate tracks as interchangeable submission sources. Mixed-track derived reviews require choosing one source track and previewing CSS-37's other-track audio exclusions, keeping video findings and the saved review unchanged. Unknown-track historical/imported evidence stays explicitly unknown and review-required, never relabeled or presented as strongly compatible. A changed outgoing choice invalidates preview consent.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Neither opening a preview nor enabling community access uploads data.
- [x] Source changes invalidate preview consent; imported/derived material cannot lose source attribution.
- [x] Service/offline/membership/partial-policy errors are actionable; narrow-screen and keyboard flows work.
- [x] Other Files sharing requires a TMDB/TVDB URL/ID; supported URLs and direct IDs produce the same typed association without a file-identity proof flow.
- [x] Scanned-track metadata, unknown-track labeling and mixed-review exclusions match signed outgoing bytes; choosing a contribution track never mutates the saved review.

## Verification

- Component and browser tests for AI/manual/derived previews, cancel, revised-source conflict and retry.
- Assert privacy canary strings never appear in outgoing requests.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide reusable donation preview/consent UI and test fixtures to CSS-62/63/67.

## Handoff summary for the next task

```text
Status / date / implementer: DONE locally / mock services, October 3, 2026, GitHub Copilot; uncommitted workspace changes.
Implemented outcome and last completed substep: reusable manual sharing for settled scans and persisted reviews, including Other Files. Exact public preview contains all outgoing notes, metadata, coverage/counts, original attribution/lineage, actual-track metadata and mixed exclusions. Every outgoing choice invalidates consent. Approval checks current access, source/file/key and original permission; durable queue and consent commit atomically before explicit outbox retry. Reopen never auto-sends. Status/check/retry/local cancellation support uncertain outcomes; committed publication wins even over late cancellation responses. Automatic donation remains absent/off.
Files changed and relevant revision: scanner/integrations/community/sharing.py and publications.py; donation_projection.py exposes shared exact audio_source_ids. scanner/http/community_sharing_api.py plus community_api.py composition; settings/config.py and security/auth_policy.py. web/src/api/api-sharing.ts; scans/sharing-dialog.tsx and sharing-preview.tsx, shared ScanSelectionList and review-workspace-editor entry points; styles/community-sharing.css. Development @types/node for existing signed fixture decoding; three window timer handles now explicitly numeric, runtime unchanged. tests/test_community_sharing.py and test_community_api.py; web/src/scans/sharing-dialog.test.tsx, sharing-test-fixtures.ts; web/e2e/community-sharing.spec.ts. API/policy/plan/context/index and CSS-63 brief updated.
Contracts / APIs / schemas / migrations / fixtures introduced: GET source context, POST exact preview, POST strict approval, DELETE private preview under /api/community/sharing; all require real admin/session/CSRF, with nested deployment support. Fully prefixed child routers preserve auth inventory classification. Explicit optional preview bounds/source cap/upload cap, no production defaults. Session/root/service-bound random IDs, 15-minute validity, process cache max16 / 64MiB, at most50 most-recently-updated jobs; private snapshots never serialized or queued. Preparation runs in threadpool. No new schema or secret; CSS-45 local0007 remains unapplied to existing installations. Existing signed donation-artifacts-v2 vectors feed reusable UI fixtures; artifact/manifest/signature formats unchanged.
Decisions used (gate IDs and recorded answers): October3 G-LICENSE user chose retained prose under standing community-reuse-1 permission, attribution/change-history preservation, per-donation rights/preview confirmation, no named CC/noncommercial license picker and no author withdrawal beyond operator moderation. G-CONTENT retains bounded public notes and privacy allowlists. G-MATCHING accepts typed TMDB/TVDB URL/ID with explicit confirmation, no file-identity proof; scanned/unknown/mixed audio rules from CSS-35C/37 remain exact.
Verification commands and results: .venv/bin/python -m pytest -q tests/test_community_sharing.py tests/test_community_api.py tests/test_community_publications.py tests/test_community_donations.py tests/test_community_donation_reviews.py tests/test_community_donation_vectors.py tests/test_community_artifact_codec.py tests/test_community_artifact_signatures.py tests/test_community_client.py tests/test_community_transport.py tests/test_community_repository.py tests/test_auth_policy.py ->199 passed. pnpm --dir web exec vitest run --maxWorkers=1 ->194 passed /28 files. pnpm --dir web build ->passed. pnpm --dir web exec playwright test community-sharing.spec.ts --workers=1 ->6 passed, 320/1440px, focus/Shift-Tab/Escape, no root overflow or axe violations, exact/mixed/Other Files/unknown previews, stale revision, retry/cancel/reopen, privacy canaries and expanded metadata. Component tests additionally cover in-flight cancellation in both response orders. Final .venv/bin/ruff check and ruff format --check across9 touched Python files ->passed; editor diagnostics across21 code/style/test files ->no errors; Node structured Markdown check across9 docs ->202 links and balanced fences; git diff --check ->passed. Frontend-only Vite http://127.0.0.1:5181/ (terminal0b4a583d-664b-4ba0-9a75-afd734a05df1), no backend started.
Known failures, limitations, unavailable checks, and recovery notes: corrected unsupported replaceAll target, jsdom fixture URL, scroll-region focus, themed-link contrast, incomplete Other Files discovery mock and FastAPI nested-router inventory mismatch; no known remaining behavioral failure. Preview cache is not persistent and in-progress preparation is not hard-preempted; restart/expiry requires re-preview, whereas approved jobs retain exact bytes. Only known same community-reuse-1 imported permissions currently qualify; unknown/corrupt/other terms fail closed rather than being relicensed. Existing schema0007 upgrade requires explicit authorization and verified recoverable backup. No real Discord/provider/service/media/model/database/deployment action was performed. Live hosted limits/CPU/activation, CSS-63 service permission enforcement and operator moderation, other browsers/platforms and new release packaging were not exercised or authorized here.
Remaining work in this task: none in authorized local/mock scope. Production activation, backed-up existing schema upgrade and downstream service policy/moderation remain separate.
Next task ID and exact first action: CSS-47; read its CSS-00 prerequisite/current Find Filters handoff and locate the local lookup handler in the shared catalog/Other Files review workflow, then extract without changing behavior or adding remote lookup.
```

CSS-62 must use a distinct explicit automatic-sharing opt-in, never infer it from
the saved manual `sharing_enabled`/consent record. CSS-63 consumes the standing
permission above and original signed attribution; it must not add author withdrawal.
CSS-67 can reuse `ShareAction`, `SharingPreviewDetails`, typed sharing API and the
existing vector-backed UI fixtures. Screenshots are local artifacts under
`web/test-results/css46-{preview,derived,manual}-{320,1440}.png`.

Direct dependent tasks: [CSS-62](62-automatic-donation.md), [CSS-63](63-sharing-policy-withdrawal.md).
