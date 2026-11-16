# CSS-35 — Implement the approved versioned file fingerprint

Status: **DONE** (October 2, 2026). User-approved nine-position bounded SHA-256,
optional full digest, mutation-checked local cache and shared byte vectors are
implemented and verified locally. Other platform/storage measurements remain unrun.

- Prerequisites: [CSS-34](34-media-identity.md)
- Decision gates: [G-MATCHING](decisions.md#g-matching)
- Parent sections: contracts §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Legacy cache MD5 uses first/last samples for large files. Community fingerprints are a separate protocol and cannot silently reinterpret that digest.

## Starting points

scanner/community/ fingerprints; existing scanner/pipeline/cache.py boundary; contracts/community/ fixtures.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Benchmark approved candidate sampling layouts, then implement the user-approved bounded SHA-256 algorithm/version with size/offset/length binding.
2. Cache by file identity/size/modification state and algorithm; detect mutation during read and invalidate.
3. Expose optional full-file digest as a distinct stronger band without forcing full reads for Find Filters.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Same bytes produce identical digests across platforms; short/overlapping sample behavior is specified.
- [x] Changing sampled bytes/size or a file during hashing cannot produce a trusted stale result.
- [x] Legacy cache behavior remains unchanged; a sampled hash is not described as proof of whole-file equality.

## Verification

- Cross-platform byte vectors, boundary-size, rename, file-change and cache invalidation tests.
- Record bounded I/O/CPU measurements and the approved sampling decision.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide fingerprint contract/vectors/cache rules to CSS-36/37/48 and compatibility fixtures.

## Handoff summary for the next task

Status / date / implementer: DONE, October 2, 2026, GitHub Copilot. Uncommitted
workspace changes; CSS-34 and unrelated user work preserved.

Outcome: bounded sampled SHA-256 with user-selected nine 1 MiB positions,
size/offset/length framing, explicit short/overlap/adjacency rules and separate
optional standard full SHA-256. Local bounded process cache keys file identity,
size/modification/change metadata and algorithm; descriptor/path checks reject
detected mutation, short reads and stale cache hits. Legacy MD5 is untouched.

Files/contracts/fixtures: [Complete contract and benchmark evidence](decision-evidence/35-file-fingerprint-contract.md)
provides module/API map, algorithm IDs, shared vectors, cache and retry rules,
measurement table and limits for CSS-36/37/48. Pure format lives in
`scanner/formats/community/fingerprints.py`, file/cache adapter in
`scanner/integrations/community/fingerprints.py`; synthetic benchmark in
`scripts/benchmark_community_fingerprints.py`. Shared fixture is
`tests/fixtures/community/file-fingerprints-v1.json`, independently verified in
Python and Worker crypto. No schema, persistent cache, migration or backup change.

Decisions: G-MATCHING sampling branch explicitly approved in this conversation.
User authorized three candidate layouts and synthetic-only input, then selected
nine 1 MiB positions while acknowledging unavailable representative media/storage
measurements. Compatibility/timing tolerances remain CSS-36's separate gate.

Verification:

- `.venv/bin/python scripts/benchmark_community_fingerprints.py`: seven repeats
  for 32 KiB, 10 MiB and 256 MiB allocated temporary inputs, three sampled
  candidates and full hash. Actual wall/CPU/read-volume evidence is linked above.
- `.venv/bin/python -m pytest -q tests/test_community_fingerprints.py tests/test_community_fingerprint_benchmark.py tests/test_cache_hashing.py tests/test_runner_cache.py`: **69 passed**, final post-format run.
- `pnpm --dir community-service exec vitest run test/file-fingerprint-vectors.test.ts`:
  **9 passed**. `pnpm --dir community-service exec tsc --noEmit` passed.
- Changed five-file Python Ruff format/check passed; focused editor diagnostics
  report no errors. Documentation links/fences and whitespace checked at closeout.

Limits/recovery: synthetic page-cache-affected macOS performance, not real-media,
cold HDD/NAS or Windows/Linux runtime evidence. Tests establish Python/Worker
byte agreement on this host, not executed platform certification. Stat checks
depend on reliable filesystem metadata and cannot freeze bytes after return or
detect privileged metadata-preserving writes. Sampled hashes deliberately miss
unsampled changes. No models, media, database or active app were changed; no
deployment or downstream Find Filters/publication integration was performed.
Initial test boundary expectation was corrected before final passing checks.

Remaining work in this task: None within approved local scope.

October 2 follow-up: independent audio/video compressed-stream fingerprints and
selected AI audio-track provenance/UI are now authorized in
[CSS-35A](35a-audio-track-provenance.md), [CSS-35B](35b-stream-fingerprints.md) and
[CSS-35C](35c-audio-track-selection-ui.md). They do not reopen or reinterpret the
completed container protocol. The user approved inclusive 2.0-second measured
duration tolerance and conservative channel bands; stream-format parameters
remain a separate gate in [G-MATCHING](decisions.md#g-matching).

Next task: CSS-35A. Bind AI audio execution to an explicit file-bound selection
and retain language/description provenance. Then CSS-35B obtains stream-format
approval before CSS-36's stream-aware compatibility implementation.

Direct dependent tasks: [CSS-36](36-compatibility-policy.md), [CSS-37](37-donation-artifact-builder.md), [CSS-48](48-local-lookup-adapter.md).

