# CSS-35B - Fingerprint selected audio and video streams separately

Status: **DONE** locally, October 2, 2026. User-approved generated experiments and
three 250 ms AAC-LC/H.264-AVCC v1 format implemented with counted bounded sampling,
actual-track-only access, independent channels and shared Python/Worker vectors.

- Prerequisites: [CSS-34](34-media-identity.md), [CSS-35](35-file-fingerprints.md), [CSS-35A](35a-audio-track-provenance.md).
- Decision gates: [G-MATCHING](decisions.md#g-matching); benchmark-backed stream sampling/normalization/timing format needs approval.
- Parent sources: [main plan section 6](../community-scan-service-plan.md), [contracts section 3](../community-scan-service-contracts.md).
- Read first: [execution guide](README.md), [shared context](context.md), decision gate and predecessor handoffs.

## Context and starting points

Same video/different audio and same audio/different video require independent
channel evidence. Initial scope is sampled demuxed compressed streams, not
decoded/perceptual re-encode matching. Keep existing container sampled/full
SHA-256 and legacy MD5 unchanged. Inspect `scanner/formats/community`,
`scanner/integrations/community`, existing PyAV/FFmpeg playable-video selection,
CSS-35A actual analyzed-track provenance and shared community fixtures.

## Work within this task

1. Propose bounded candidate experiments with generated multi-track media using
   existing tools. Obtain sampling/experiment approval; user-media reads need
   separate authorization. Measure seeks, demux bytes actually read and CPU/wall
   time; hashed payload size is not the same as total I/O.
2. Compare timeline sampling, seek/keyframe/packet boundaries, codec configuration
   and packet framing normalization. Test remux/repacketization/timebase changes
   before claiming remux-stable identity. Specify supported codecs, behavior for
   missing indexes, cancellation and bounded failure; no hidden full-stream read.
3. Obtain explicit format approval, then implement distinct versioned video/audio
   digest algorithms and shared vectors. Do not bind container total size or
   physical stream index as portable content identity; local selectors stay local.
4. Keep content digest separate from measured stream start/duration/timebase.
   Equal samples do not establish complete equality or filter timing alignment.
   Duration tolerance alone never authorizes automatic retiming or offset matching.
5. Cache by file state, selected stream and algorithm/normalization behavior;
   detect mutations. AI audio fingerprints bind the track actually scanned in
   CSS-35A, not another currently selected/default track or all file audio tracks.
6. Hand off DTOs/vectors and channel/timing limits to CSS-36/37/48. Preserve language
   and description/title as supporting metadata; description differences alone do
   not conflict. Re-encode-tolerant perceptual matching is explicitly deferred.

Experiment preparation may precede CSS-35A completion; final integration cannot
be DONE without its analyzed-track handoff. No UI/publication/downstream matcher
or automatic full-stream hashes are implemented here.

## Acceptance criteria

- [x] Supported normalized streams have deterministic independent audio/video digests and cross-language vectors; unsupported cases stay explicit.
- [x] Same-video/different-audio and the reverse produce independent channel evidence; unrelated-track changes do not invalidate portable channel identity.
- [x] Reordered stream indexes remain local selectors, not portable IDs; AI submission fingerprints only the actual analyzed audio track.
- [x] Sample/packet/codec rules, real I/O/CPU bounds, mutation behavior and timing limits are specified and verified (elapsed checks are cooperative, not hard CPU/native-call preemption).
- [x] Samples never claim whole-stream equality or bypass timing/work/episode conflicts; existing container/MD5 behavior remains compatible.

## Verification

Generated media: unchanged streams, remux/repacketization, reordered audio,
alternate dub/commentary, duplicate language tags, delayed audio, changed video
only, changed audio only, re-encode, missing index, unsupported codec and mutation.
Share Python/Worker vectors; record tool versions and platform/storage limits.
Follow [shared verification rules](context.md#verification); no user-media mutation or model acquisition.

## Definition of done

- [x] Acceptance criteria and format gates resolved; relevant tests/benchmarks pass.
- [x] Shared [completion requirements](context.md#definition-of-done) met.
- [x] Status/index updated only after writing the handoff.
- [x] Handoff includes approved protocol/DTO/vectors, supported normalization, cache/timing rules, measured bounds, verification/limits and exact next action.

## Handoff summary

Completed locally October 2, 2026, GitHub Copilot. Read the
[approved stream contract](decision-evidence/35-track-contract.md#compressed-stream-v1)
and [synthetic candidate measurements](decision-evidence/35-track-contract.md#experiment-evidence).
178 backend slice tests and 11 independent Worker file/stream vectors passed;
types, Ruff and diagnostics passed. Each fingerprint uses one counted open,
including actual-audio validation/playable-video selection; cache hits report zero
current reads. Configured AAC-LC and AVCC H.264 only; missing indexes, Annex-B/TS,
PCM, empty windows and budget failures remain unsupported. Synthetic cache-resident
measurements do not claim real-media/storage accuracy, offset or perceptual support.
Next exact action: CSS-36 read the final DTO/framing/timing limits and explicitly
propose its stream-aware compatibility rules; CSS-37/48 reuse actual-only evidence.

Direct dependents: [CSS-36](36-compatibility-policy.md), [CSS-37](37-donation-artifact-builder.md), [CSS-48](48-local-lookup-adapter.md).