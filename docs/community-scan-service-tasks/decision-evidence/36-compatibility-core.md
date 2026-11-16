# CSS-36 compatibility core handoff

> Current local policy (October 7, 2026): usage-admission requirements in this dated
> evidence are superseded. The Docker service has no daily/hourly user allowances,
> request buckets, aggregate payload caps or usage-pressure pauses. Historical
> measurements are not new execution results. Use the [Docker contract](../../community-service-docker.md).

**Completion update, October 2, 2026:** CSS-36 is now DONE locally. The historical
core record below describes the earlier partial implementation; its pending-work
statements are superseded by the [completed stream policy](#completed-stream-policy).

Implemented October 2, 2026 as independent work after the user requested resuming
the interrupted CSS-36 implementation. **CSS-36 remains IN PROGRESS**:
CSS-35A/B selected-track provenance and stream fingerprints are not implemented.
This core is not a final stream-aware matching contract or deployed lookup route.

## Approved behavior

The user selected inclusive fixed **2.0-second** file-probe duration tolerance,
not the proposed 1.0 second, and the conservative band ladder/conflict rules in
[G-MATCHING](../decisions.md#g-matching). Rounded catalog runtime and Jellyfin
ticks do not establish measured file-probe timing. A reported probe precision
coarser than the tolerance cannot establish a strong timing comparison. Known
file-probe duration differences over two seconds are conflicts even when a name
or digest agrees. No percentage tolerance or automatic synchronization is added.

Known typed work, series, episode numbering, ordered file-span, edition and timing
conflicts override fingerprints. Same provider/entity namespace conflicts remain
conflicts even if another supplied provider matches. No cross-provider linkage,
alias merge or alternative-numbering conversion is invented. Missing identifiers
or unlinked namespaces produce `title_only` with explicit missing fields;
series identity alone is not a physical movie/episode timeline.

Band order: `exact_file`, `strong_fingerprint`, `release_match`, `possible_cut`,
`title_only`, `incompatible`. All outputs start `selected: false`.

- Equal supported full-file digest/size plus measured timing and no identity
  conflict can establish `exact_file` video evidence.
- Equal supported sampled-file digest/size under the same constraints can
  establish `strong_fingerprint` video evidence, not whole-file equality.
- Same work plus exact nonempty scene/group labels and measured timing, without
  stronger matching file evidence, is `release_match`; otherwise `possible_cut`.
- Differing full-file hashes prevent sampled-match promotion; equal full with
  contradictory sampled evidence is an explicit evidence conflict.
- Unsupported algorithms, including legacy MD5, are ignored for matching and
  reported, never compared as SHA-256. Different container hashes alone are not
  a per-stream conflict; remux/re-encode can remain release/possible candidates.
- Supplied selected audio language/role conflicts affect audio, not otherwise
  compatible video. Labels or equal container bytes **cannot identify which
  audio track was analyzed**. This core always reports `analyzed_audio_stream`
  missing; nonconflicting audio stays `possible_cut` or `title_only`, never strong.

Description/title comparison, independently strong audio or video-stream identity,
offset/timebase rules and actual scan-to-track integration await CSS-35A/B. A
container-level video band is not an implemented independent video-stream match.
Opposite-channel stream identity is therefore still an unchecked CSS-36 acceptance
item. No caller should advertise this core as the final completed matching policy.

## Modules and contracts

[Python core](../../../scanner/modeling/community_matching.py) reuses CSS-34's
`MediaIdentity`, `ReleaseEvidence`, duration/audio evidence and conflict helper.
It exposes frozen `FingerprintEvidence(algorithm, digest, size_bytes)`,
`MatchEvidence(media, fingerprints)`, `classify(target, candidate)`,
`classify_candidates(target, candidates, limit=...)` and
`compatibility_cache_key(target, candidate, policy_version=...)`.

[Worker input schema](../../../community-service/src/catalog/compatibility-inputs.ts)
uses existing Zod to validate the typed comparison DTO;
[Worker core](../../../community-service/src/catalog/compatibility.ts) exposes
`classify`, `classifyCandidates` and `compatibilityCacheKey`. No service routes,
database state, subscriptions or public API endpoints are introduced. Python
callers construct typed dataclasses; the fixture decoder is test-only, not a
new production HTTP parser.

Output `Compatibility` contains `policy_version: community-compatibility-core-1`,
`selected: false` and independent `video`/`audio` objects with `band`, sorted
unique `reasons`, `conflicts`, `missing` and `strong_eligible`. The latter is only
an eligibility signal for a future explicit action, not selection/download/apply.
This core has no audio strong eligibility while analyzed-track identity is absent.

Reasons include `work_match`, `measured_duration_match`, `release_name_match`,
`full_digest_match`, `sampled_digest_match`, `full_digest_differs` and
`unsupported_fingerprint_ignored`. Conflicts include `entity_type`,
`work_identity`, `series_identity`, `episode_numbering`, `episode_span`, `edition`,
`measured_duration`, `fingerprint_size`, `fingerprint_evidence`, `audio_language`
and `audio_role`. Missing fields include `work_identity`, `work_link`,
`episode_numbering`, `episode_span`, `physical_episode_or_movie`,
`measured_duration`, `matching_fingerprint`, `release_name`,
`analyzed_audio_stream` and `audio_track_metadata`. File/work context reasons can
appear in audio output without establishing analyzed audio content identity.

Input bounds reuse 16 IDs per namespace group, 32 numbers per numbering/span
list and add eight fingerprint slots; namespaces/algorithms must be unique.
Recognized fingerprints and release size must agree within one input. Comparison
sizes use safe JSON integers through `2^53 - 1`; this does not change CSS-35's
unsigned-64-bit hash framing. Candidate IDs are unique bounded ASCII public tokens.
Candidate count needs an explicit positive caller-supplied limit; overflow raises
instead of silently truncating. No user admission policy is selected here.

Core candidate sorting is deterministic video band, then audio band, then ASCII
public ID; input order cannot alter a tie. There are no vote/popularity hooks or
quality boosts. CSS-48/49 must integrate requested-channel semantics with the
final stream-aware contract, not infer audio usability from a video-first order.

Cache helpers bind the core policy version and all comparison evidence. Python
returns an immutable process-local tuple; Worker returns canonical sorted-key
JSON. These keys are local implementation details, not interchangeable wire IDs.
No actual persistent comparison cache is added. Stream policy must use a new
version and add its actual track/timing evidence, never reinterpret this core ID.

## Verification and limits

[Shared fixture](../../../tests/fixtures/community/compatibility-core-v1.json)
has 20 synthetic cases with separately specified bands/conflicts, plus SHA-256
checksums of complete canonical output DTOs, including every reason/missing field.
Checksums were calculated from the validated Python outputs and independently
checked with Worker Web Crypto. They prove implementation parity, not independent
media accuracy. Test patches are test-only structured merges, not public input
normalization. Cases include renamed files, two-second boundary, rounded/coarse
runtime, wrong work/cut, dub/commentary, unknown track, unlinked providers,
remux/re-encode without stream evidence, MD5, size contradiction, full-file digest,
specials and double episodes. Additional tests cover alternative numbering, wrong
episode/series, full/sample contradictions, bounds, ordering and cache invalidation.

- `.venv/bin/python -m pytest -q tests/test_community_matching.py tests/test_media_identity.py tests/test_community_fingerprints.py tests/test_cache_hashing.py`: **87 passed**, including the final post-format run.
- `pnpm --dir community-service exec vitest run test/compatibility.test.ts test/file-fingerprint-vectors.test.ts`: **36 passed**, two files.
- `pnpm --dir community-service exec tsc --noEmit`: passed.
- `.venv/bin/python -m pytest -q tests/test_community_matching.py` after the
  tolerance constant change: **35 passed**. Two-file Ruff format/check passed.
- Focused editor diagnostics: no errors. `git diff --check` passed; Node
  link/fence/whitespace validation passed **123 local links** in four changed
  documents, verified all 20 complete-output checksum entries and confirmed
  CSS-36 remains IN PROGRESS/unchecked in the index.

Initial Worker typecheck exposed an optional test fixture override; repaired and
rerun successfully. Initial Ruff found test import order; fixed and rerun. No
known failing core checks remain. No live media, models, user database, migrations,
frontend workflow, Windows/Linux certification or deployment was exercised.
Existing identity normalization, file fingerprint and MD5 implementations are
unchanged. The shared cases are not representative remux/codec accuracy evidence.

## Remaining work

CSS-35A must provide immutable actual analyzed-track metadata, including language
and description/title. CSS-35B must obtain sampling/normalization/timing approval
and supply independent stream DTOs/vectors before stream matching is implemented.
Then extend both classifiers under a new final policy version, verify strong
audio-only/video-only matches, descriptions as supporting metadata and stream
timing/unknown-track boundaries. Update public downstream DTOs and do not mark
CSS-36 DONE until those acceptance checks pass.

Next exact action: CSS-35A's file-bound inventory-to-worker selection contract.
CSS-36's ready core is preserved; it is not a replacement for the new prerequisites.

## Completed Stream Policy

October 2, 2026, GitHub Copilot. The user requested the next CSS coding task,
then explicitly selected **Approve Exact Timing**. CSS-35A/B/C were DONE at
resumption. Policy version is now `community-compatibility-stream-1`; do not
reuse `community-compatibility-core-1` cache entries or advertise their audio
results as independently stream-aware. No database migration, persisted state,
public route, frontend, deployment or model change was needed.

### Inputs And Timing

The existing `MatchEvidence(media, fingerprints)` gains optional `video_stream`
and `audio_stream`; old construction and file-only outputs remain compatible,
except for the deliberately changed policy version. Frozen
[StreamEvidence](../../../scanner/modeling/community_stream_matching.py) fields
are `algorithm`, SHA-256 `digest`, nullable `playback_start` and `duration`,
`provenance` (`unknown`, `selected`, `analyzed`), and nullable `description`.
The [Worker stream module](../../../community-service/src/catalog/stream-compatibility.ts)
contains the matching strict Zod schema and rational comparison. Recognized
audio/video algorithms cannot be supplied in the opposite channel. Unsupported
future algorithms are explained and ignored for equality.

Timing is exact rational seconds, signed decimal numerator and optional positive
denominator, at most 19 digits per component with magnitude through `2^63 - 1`.
Duration must be positive. Python Fraction and Worker BigInt cross-products accept
equivalent timebases without floating-point rounding; negative starts are permitted
for measured priming. Descriptions are bounded to 512 Unicode code points and
remain untrusted supporting text; publication sanitization/preview belongs to CSS-37.
Description differences never prove equality or create conflicts.

The existing counted sampler supplies `playback_start = stream.start_time *
stream.time_base - container.start_time / av.time_base` and `duration =
stream.duration * stream.time_base`, without another open. Unknown container
origin yields null playback start. Existing absolute float seconds/timebase,
bounded sampling, digest framing, and actual-only
`fingerprint_analyzed_audio(db, scan_id)` guards are unchanged. Process fingerprint
cache behavior now includes `playback-rational-1`; comparison cache keys bind all
stream/provenance/description fields and the new policy. No persistent cache exists.

### Eligibility And Explanations

Work/edition/episode/span and measured file-duration rules remain the approved
core rules, including inclusive two-second file-probe tolerance. Streams cannot
repair missing identity or rounded runtime, and all results remain unselected.
Supplied channel evidence requires equal supported samples and exactly equal known
playback-relative start/duration for `strong_fingerprint`. An equal supported
full-file digest can label that ready channel `exact_file`; differing container
digests do not block independently equal streams. Supplied stream mismatches or
missing evidence do not fall back to strong container identity. With neither
video stream supplied, the approved file-only video fallback remains available
and is not labeled independent stream proof.

Audio additionally requires `analyzed` candidate provenance and `selected` or
`analyzed` target provenance, plus known language/role for those individual tracks.
The classifier consumes reported evidence; callers must build candidate audio
from immutable actual input through CSS-35B's guarded accessor, never from today's
default/request/inventory. It does not turn contributor assertions into attestation.
Unknown/imported/manual evidence can remain discoverable but cannot be promoted
through equal container/video hashes. Role is not guessed from titles/default flags.

Known stream start/duration differences yield `incompatible` for that channel only;
missing timing, unsupported algorithms or unequal samples remain review-required.
Each known timing field is compared independently: missing duration cannot hide
a known start conflict, and missing start cannot hide a known duration conflict.
Different sampled hashes do not prove incompatible cuts. Identical files may
contain different chosen audio tracks, so file equality cannot erase that uncertainty.
Known language/role conflicts affect audio only. No alignment tolerance, perceptual
re-encode matching, packet-wide timestamp equality or automatic retiming is claimed.

Outputs retain sorted unique reasons/conflicts/missing fields and `strong_eligible`.
Added reasons: `audio_stream_digest_match`, `video_stream_digest_match`, their
`*_digest_differs` counterparts, `*_stream_timing_match`, and
`unsupported_stream_fingerprint_ignored`. Added conflicts are channel-prefixed
`*_stream_playback_start` and `*_stream_duration`. Added missing fields are
`*_stream_fingerprint` and `*_stream_timing`; `analyzed_audio_stream` and
`audio_track_metadata` retain their core meanings. Deterministic ordering remains
video band, audio band, then ASCII public ID, independent of input order. Popularity
and requested-channel UI ordering remain separate downstream concerns.

### Verification And Next Action

[Shared stream fixture](../../../tests/fixtures/community/compatibility-stream-v1.json)
contains 28 independently specified acceptance cases and complete canonical DTO
checksums generated from Python and independently checked with Worker WebCrypto.
The original 20 core case outputs/checksums remain unchanged when substituting their
historical policy version for comparison. This establishes parity, not media accuracy.
Cases cover opposite-channel changes, remux/re-encode, one-microsecond starts,
exact duration, unknown timing/provenance/language/role, dub/commentary, descriptions,
equivalent rationals, negative priming, differing/equal container bytes, edition,
rounded runtime and unlinked identity. Bounds, policy/evidence cache invalidation
and ordering are tested separately.

Final commands:

- `.venv/bin/python -m pytest -q tests/test_community_matching.py tests/test_stream_fingerprints.py tests/test_media_identity.py tests/test_community_fingerprints.py tests/test_cache_hashing.py tests/test_audio_tracks.py tests/test_audio_track_execution.py`: **162 passed**.
- `pnpm --dir community-service exec vitest run test/compatibility.test.ts test/stream-fingerprint-vectors.test.ts test/file-fingerprint-vectors.test.ts`: **69 passed**, three files.
- `pnpm --dir community-service exec tsc --noEmit`: passed.
- `.venv/bin/ruff format` and `ruff check` on both matching modules, both stream
  integration modules and both touched Python test files: passed after import-order
  repair. An initial Worker typecheck caught an undefined cache-input type alias;
  it was repaired using the real Zod input type and the same check passed.
- Local Node link/fence/status validation passed for eight documents and **155
  local links**; `git diff --check` and focused editor diagnostics passed.

Generated temporary FFmpeg/PyAV media exercises the real sampler through matching:
unchanged MP4/MOV streams are strong, changing one channel preserves its sibling,
and exact timing is verified against both zero and nonzero container origins.
Inference is not run; language/role in classifier tests is explicitly synthetic.
Representative user media/storage, Windows/Linux, real codec accuracy, live lookup,
deployment and frontend workflows were not exercised. No user data was read or
migrated, and no service was enabled. Existing file/stream hash vectors pass unchanged.

Remaining CSS-36 work: none in its local pure-policy scope. Direct consumers
CSS-38/41/48/49/53 must use the new DTO/version and preserve actual/unknown evidence,
bounded candidate admission and explicit selection. **Next task CSS-37:** read its
brief and the CSS-09/11/13/21/34/35A/B handoffs, then implement one stable source
snapshot and exact-byte privacy preview using the actual-only audio accessor. Do
not upload alternate-track inventories or add publication routes in that task.