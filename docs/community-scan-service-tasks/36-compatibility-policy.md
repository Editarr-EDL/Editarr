# CSS-36 — Implement deterministic compatibility bands and explanations

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** locally (October 2, 2026). Python and Worker independently classify
audio/video stream evidence under the explicitly approved exact playback-relative
timing policy, with actual-track audio guards and full-output golden parity.

- Prerequisites: [CSS-34](34-media-identity.md), [CSS-35](35-file-fingerprints.md), [CSS-35B](35b-stream-fingerprints.md)
- Decision gates: [G-MATCHING](decisions.md#g-matching)
- Parent sections: main §6–8; contracts §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Compatibility outranks popularity. Uncertain same-work releases must be separate and require explicit review; timing/track conflicts are not repairable by reputation.

## Starting points

shared contracts/community/matching fixtures; scanner/community/; community-service/src/catalog/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Implement bounded candidate classification for full digest, sampled digest, release/runtime and title-only cases.
2. Evaluate audio/video separately using CSS-35B's independent selected-stream fingerprints and stream timing. Same video/different audio and the reverse can have different bands; container digest mismatch alone is not a channel conflict. Language and description/title are metadata identifiers; description differences alone do not block strong audio compatibility.
3. Version policy and use deterministic tie-breaks; keep ranking hooks separate.
4. Apply the approved inclusive fixed 2.0-second file-probe duration tolerance, with no percentage allowance or promotion from rounded catalog runtimes. Stream-start alignment/packet normalization remain separately approved CSS-35B rules; duration approval never authorizes automatic retiming.
5. Use the approved ladder exact_file, strong_fingerprint, release_match, possible_cut, title_only, incompatible. Known work/edition/episode/span/timing conflicts override digest/name evidence. Known selected-track language/role conflicts block audio, not otherwise compatible video; unknown track identity stays review-required. No percentages or guessed aliases. All results start unselected; exact/strong eligibility supports a future explicit selection action, not automatic download/apply.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Wrong cut/dub/commentary/episode cannot rank as a strong match just because a name or scene group matches.
- [x] Missing work identity requires confirmation and uncertainty remains unselected.
- [x] Python and Worker implementations or shared fixtures agree on every supported case.
- [x] Opposite-channel matches stay independent; unknown or unscanned candidate audio cannot become strong merely through matching video or labels.

## Verification

- Table-driven renamed/remux/re-encode/wrong-cut/runtime-rounded/multi-episode/dub fixtures.
- Assert all tie-breaks stable and policy-version changes invalidate affected caches.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide match-band schema/reasons, approved tolerance and golden cases to CSS-41/48/49/53.

## Handoff summary for the next task

DONE locally / October 2, 2026 / GitHub Copilot. Adopted completed CSS-35A/B
evidence after the user explicitly approved exact known playback-relative stream
start/duration, with no alignment tolerance or automatic retiming. Independent
channel conflicts never promote/block the opposite channel; candidate audio needs
actual analyzed provenance and known language/role. Description differences alone
do not affect compatibility. Unknown evidence stays review-required and unselected.

[Completed policy and verification handoff](decision-evidence/36-compatibility-core.md#completed-stream-policy)
maps Python/Worker modules, `community-compatibility-stream-1` inputs/reasons,
bounded exact rational timing, cache invalidation, 28 shared complete-output stream
cases, preserved 20 core outputs and actual generated sampling-to-matching tests.
The sampler now supplies playback-relative rational timing within the same bounded
open; digest bytes and file/stream vectors are unchanged.

Final verification: **162 Python tests**, **69 Worker tests**, Worker typecheck
and changed-file Ruff passed. See the handoff for exact commands and limitations.
No database/migration, public route, UI, real AI inference, user-media access,
live service or deployment change occurred. Cross-platform and representative-media
accuracy remain unverified; this completes local pure policy, not hosted lookup.

Remaining work in this task: none. Next task **CSS-37**: read its brief and
predecessor handoffs, then build stable source snapshots and exact-byte privacy
previews using actual-only audio evidence without alternate-track inventories.

Direct dependent tasks: [CSS-38](38-d1-catalog-schema.md), [CSS-41](41-catalog-lookup-api.md), [CSS-48](48-local-lookup-adapter.md), [CSS-53](53-ranking-policy.md).

