# CSS-37 — Build immutable donation payloads and privacy previews

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally, October 3, 2026**. Immutable preparation, privacy preview and
explicit signing are implemented; no publication route or UI is enabled.

- Prerequisites: [CSS-09](09-execution-provenance.md), [CSS-11](11-artifact-contract.md), [CSS-13](13-artifact-signatures.md), [CSS-21](21-local-community-state.md), [CSS-34](34-media-identity.md), [CSS-35](35-file-fingerprints.md), [CSS-35A](35a-audio-track-provenance.md), [CSS-35B](35b-stream-fingerprints.md)
- Decision gates: [G-CONTENT](decisions.md#g-content)
- Parent sections: main §7; contracts §4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

AI evidence, manual filters and derived reviews differ. Source timeline, original author signatures and edits must remain identifiable.

## Starting points

scanner/formats/community/; scanner/community/ publication preparation; review/scan repositories.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Build artifacts from a stable completed scan or explicit authored review snapshot, with effective provenance/coverage and bounded source lineage.
2. Generate the exact outgoing preview/inventory from the same bytes that will be signed, including any allowed bounded event notes/comments; sanitize excluded local/provider data.
3. Compute event/category/channel counts and explicit revisions; record retiming transforms once in the declared target timeline.
4. For Other Files uploads, enforce the [October 2 TMDB/TVDB association decision](decisions.md#g-matching): require a typed TMDB/TVDB association, not IMDb-only or filename-only metadata. Explicit user association is sufficient; do not require proof that the file belongs to that work. Keep association honestly user-asserted and preserve ordinary artifact validation.
5. AI audio contributions identify only the individual track actually scanned, from immutable CSS-35A language/description/title/provenance and CSS-35B stream/timing evidence. Never submit alternate-track inventory or relabel old findings using the current default/selected track. Track descriptions are bounded sanitized untrusted text, previewed in the exact outgoing bytes.
6. For a derived review with multiple audio source tracks, require choosing one for this contribution; exclude other-track audio findings with explicit preview exclusions. Preserve video findings and source lineage; never mutate the saved review or automatically split contributions. Legacy/imported unknown-track audio may still be shared with explicit unknown labels and required review, without a guessed fingerprint or strong audio claim. This unknown exception does not authorize including a second identified audio track.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] No credentials, absolute paths, raw media, full transcript or internal model debug output leaks from captured private fields; explicit public text is conservatively sanitized and previewed.
- [x] Manual/imported/derived evidence is honestly labeled; missing provenance stays unknown; zero findings is valid.
- [x] Preview consent binds exact bytes/revision, and source edits after preview require a new preview.
- [x] Other Files uploads require TMDB/TVDB association without additional proof of work identity; ordinary local scans remain available without it.
- [x] Identified AI audio submissions bind only the actually scanned track and preserve language/description; derived preview exclusions leave the saved review unchanged and unknown-track legacy evidence stays honestly labeled.

## Verification

- Golden fixtures for AI/manual/derived/retimed/partial/legacy scans and privacy canary strings.
- Include multi-track inventory canaries, mixed-track derived exclusions, changed current selection and historical unknown-track audio; verify no second identified track reaches signed bytes.
- Verify durable signatures and lineage survive a round trip.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide builder/preview interfaces, privacy allowlist and source snapshot rules to CSS-39/45/46.

## Handoff summary for the next task

The [completed contract and verification evidence](decision-evidence/37-donation-artifact-contract.md)
specify public APIs, source capture, privacy allowlist, versions and downstream obligations.

```text
Status / date / implementer: DONE locally / 2026-10-03 / GitHub Copilot.
Implemented outcome: Stable read-only bounded source capture, allowlisted exact-byte
preview, explicit consent/source/file revalidation and detached signing. Actual-only
audio, canonical MCF-channel exclusions, source provenance/status, portable identity,
artifact-local lineage and already-applied retiming; no saved review mutation.
Files/revision: Uncommitted working-tree changes; module map in the contract evidence.
Contracts/APIs: SourceRef, CapturedSource, DonationOptions, PreparedDonation;
capture_source, prepare_donation, approve_donation, identity_from_reference.
Schemas: artifact/summary v2; manifest/signature v1 retained; strict v1 supported.
Migrations/state: None. Eight frozen builder-generated cross-language signed cases.
Decisions: G-CONTENT partial/legacy/notes; Oct3 user Approve Artifact V2;
G-MATCHING actual-only audio, exclusions and Other Files TMDB/TVDB association.
Verification: 220 scoped Python / 92 Worker tests; Worker tsc; changed-Python Ruff
and formatting; editor diagnostics. Exact commands/evidence in linked contract.
Limitations: No UI, route, outbox, network, live/user DB/media/model or deployment.
No production resource/CPU/license policy. Source references are not remote signature
verification; strings require untrusted rendering. Preparation objects are private.
Filesystem generations are metadata-dependent; legacy video MD5 reads the full file.
Windows/Linux and representative real-media accuracy remain unverified.
Remaining CSS-37 work: None within the approved local-mechanism scope.
Next: CSS-38. Read its brief and CSS-11/23/36 handoffs plus v2 summary evidence,
then inspect existing D1 migrations/catalog code for an additive preserving schema.
```

Direct dependent tasks: [CSS-45](45-local-publication-outbox.md), [CSS-46](46-manual-sharing-ui.md), [CSS-62](62-automatic-donation.md).
