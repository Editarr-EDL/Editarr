# CSS-35C - Select the audio track for an AI scan in the UI

Status: **DONE** locally, October 2, 2026. Shared catalog/Arr/Jellyfin and Other
Files hook/control, explicit payloads, file-bound memory/races and actual/unknown
metadata verified with unit/application and 320/1440px accessible browser checks.

- Prerequisites: [CSS-35A](35a-audio-track-provenance.md).
- Decision gates: [G-MATCHING](decisions.md#g-matching); follow confirmed default and file-bound memory rules.
- Parent sources: [main plan sections 6-7](../community-scan-service-plan.md), [contracts section 3](../community-scan-service-contracts.md).
- Read first: [execution guide](README.md), [shared context](context.md), decision gate and predecessor handoff.

## Context and starting points

Choose one track for audio/audio+video AI scans. Show its language and
description/title with observed codec/channel/role hints to distinguish duplicate
languages. Scan details display what actually ran, not today's playback/default
track. Inspect catalog New Scan, Other Files, shared scan/queue controls and API
wrappers in their matching `web/src` feature folders; reuse one hook/control.

## Work within this task

1. Use a labeled select/menu for file-bound inventory across catalog/Arr/Jellyfin
   and Other Files initiation. Preselect the only track or an unambiguous flagged
   default; otherwise require selection. Always display the chosen track.
2. Remember only for that file/version through CSS-35A's selection contract.
   Revalidate file/stream state; another file never inherits a numeric index.
   Late inventory responses must not populate another file's selection.
3. Submit the explicit selector. Handle loading/error/retry, no audio, missing
   language/description, duplicate labels, cancel and stale selection. Never
   substitute another track silently. Video-only/credits need no audio choice.
4. Display persisted actual analyzed language/description in scan details and
   appropriate queue states; label historical unknown identity honestly. Selecting
   another track cannot alter completed findings or their recorded source.
5. Hand off reusable metadata display to sharing previews and Find Filters;
   local scanning stays independent of community enrollment. No publication here.

## Acceptance criteria

- [x] All audio-capable scan initiation surfaces reuse selection behavior and send the exact chosen track.
- [x] Same-language tracks are distinguishable by description/title and observed hints; missing labels are not fabricated.
- [x] File changes, stale responses, cancellation/retry and disappeared tracks cannot silently scan another stream.
- [x] File/version memory and default policy match CSS-35A; video/credits and historical workflows remain usable.
- [x] Keyboard/focus, 320/1440px layouts and actual analyzed-track displays pass accessible interaction checks (native OS-menu keyboard commitment is not automated evidence).

## Verification

Feature unit tests for selection races/request payloads; application integration
for catalog and Other Files; browser journeys for duplicate-language tracks,
no audio, missing tags, error/retry, keyboard/focus, overflow and axe. Use fake
models/synthetic inventories and [shared verification rules](context.md#verification).

## Definition of done

- [x] Acceptance and focused unit/build/browser verification passed.
- [x] Shared [completion requirements](context.md#definition-of-done) met.
- [x] Status/index updated only after writing the handoff.
- [x] Handoff gives reused hooks/controls, API selection fields, displayed metadata, commands/results, screenshots/limits and exact next action.

## Handoff summary

Completed locally October 2, 2026, GitHub Copilot. Read the
[shared UI/verification contract](decision-evidence/35-track-contract.md#ui-and-verification).
181 serial web unit/application tests, production build/types and two mocked
Chromium journeys passed. Screenshots cover catalog/Other Files at 320/1440px;
axe/root overflow, keyboard Tab/focus, exact selection, memory, duplicate language,
no-audio/video/credits, unknown/error/retry and actual-versus-unknown displays pass.
Full chosen metadata wraps below the native select on narrow screens. This host's
headless native-menu key events did not commit selection; native `selectOption`
and focus traversal were verified separately. No real AI or backend migration run.
Next exact action: CSS-46/49 reuse the API types, hook/control and metadata labels
without replacing actual scan provenance with a current/default track.

Direct dependents: [CSS-46](46-manual-sharing-ui.md), [CSS-49](49-find-filters-results.md), [CSS-66](66-integration-and-abuse.md).