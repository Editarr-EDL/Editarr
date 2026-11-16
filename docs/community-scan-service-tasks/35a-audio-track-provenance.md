# CSS-35A - Bind AI audio scans to a selected track

Status: **DONE** locally, October 2, 2026. Protected inventory, explicit durable
selection, exact execution, immutable actual provenance, cache isolation and
data-preserving migration/backup coverage verified. Existing databases not upgraded.

- Prerequisites: [CSS-06](06-migration-startup-cli.md), [CSS-09](09-execution-provenance.md), [CSS-34](34-media-identity.md).
- Decision gates: [G-MATCHING](decisions.md#g-matching); selection/submission rules are confirmed.
- Parent sources: [main plan section 6](../community-scan-service-plan.md), [contracts section 3](../community-scan-service-contracts.md).
- Read first: [execution guide](README.md), [shared context](context.md), decision gate and predecessor handoffs.

## Context and starting points

One audio AI scan analyzes one selected physical stream. Language and
description/title are retained metadata identifiers, not content proof. A local
stream index selects input but is not portable identity. Inspect
`scanner/pipeline/audio.py`, scan API/CLI, queue/worker execution, PyAV probing,
audio/transcription caches, provenance persistence, migrations and backup tests.
Keep implementation in existing feature packages and entry points thin.

## Work within this task

1. Provide protected, bounded file-bound track inventory with local selector,
   observed language, description/title, codec/channels, disposition and stream
   timing. Unknown metadata stays unknown; do not infer it from filenames.
2. Resolve initial selection: one available track or one unambiguous flagged
   default is preselected; otherwise require selection. Remember only for the
   same file/version and revalidate state/stream metadata. No global preference,
   cross-file index reuse or silent substitution after a track disappears.
3. Carry explicit selection through API/CLI, durable queue, restart, retry and
   worker. Revalidate before analysis and map FFmpeg to the exact selected stream.
   Video-only and credits workflows must not require audio selection.
4. Bind audio/waveform/transcript cache identity to file state, selected stream
   and extraction behavior; selecting another track cannot reuse stale evidence.
5. Persist immutable actual analyzed-track language, description/title, timing
   and provenance with findings/coverage. Separate requested selection from
   completed execution. Retries cannot relabel existing findings as another track.
6. Use data-preserving migrations and backup/restore inventory coverage for new
   state. Historical scans retain unknown track identity, not today's default.
7. Hand off inventory/selection/provenance to CSS-35B/35C/37/48. Only the actually
   scanned individual audio track can supply identified AI audio submission
   evidence; another scan creates separate evidence, not a whole-file inventory.

Do not implement UI or stream fingerprint format here. Ordinary local scans
remain independent of provider IDs and community enrollment. Follow the
[recorded unknown-track exception](decisions.md#g-matching) for historical evidence.

## Acceptance criteria

- [x] Language/description distinguish same-language main/commentary tracks; absent metadata stays unknown.
- [x] Selected stream survives API/CLI/queue/worker/restart/retry and is the exact AI input; missing or stale selectors never cause silent substitution.
- [x] Another track cannot reuse the selected track's extraction/transcript cache or overwrite completed provenance.
- [x] Selection memory is file/version-bound; no-audio/video-only/credits behavior remains usable.
- [x] Supported upgrades preserve existing data; historical identity stays unknown; backup/restore behavior is verified.

## Verification

Generated multi-track media and fake models: duplicate languages/descriptions,
missing tags, ambiguous default flags, no audio, file replacement/reordering,
queue recovery, cancellation/retry, cache isolation and actual execution evidence.
Use disposable file-backed migration/backup fixtures. An existing database upgrade
requires a verified recoverable backup and authorization. Follow
[shared verification rules](context.md#verification); record actual commands and limits.

## Definition of done

- [x] Acceptance and applicable decisions satisfied; relevant tests passed.
- [x] Shared [completion requirements](context.md#definition-of-done) met.
- [x] Status/index updated only after writing the handoff.
- [x] Handoff gives DTO/API/queue/provenance contracts, selection/cache rules, migrations, backup evidence, verification/limits and exact next action.

## Handoff summary

Completed locally October 2, 2026, GitHub Copilot. Read the
[final contract and verification handoff](decision-evidence/35-track-contract.md#selected-audio-contract).
178 backend regression tests cover exact extraction, API/CSRF, durable reload,
unknown/relabelled worker guards, caches, supported 0001-0004 upgrades, packaged
0005 migration and four backup modes. Actual input is recorded after successful
analysis, not guessed from queue options; historical/fake-model input stays unknown.
The dedicated worker CLI consumes the durable snapshot; no public scan CLI exists.
Next exact action: CSS-37/48 adopt the actual-only accessors and unknown exceptions.
Deployment of 0005 remains separately authorized and requires a verified backup.

Direct dependents: [CSS-35B](35b-stream-fingerprints.md), [CSS-35C](35c-audio-track-selection-ui.md), [CSS-37](37-donation-artifact-builder.md), [CSS-48](48-local-lookup-adapter.md).