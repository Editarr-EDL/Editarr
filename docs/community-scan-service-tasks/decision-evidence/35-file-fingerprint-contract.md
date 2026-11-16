# CSS-35 file fingerprint contract

Implemented October 2, 2026. G-MATCHING's fingerprint choice is resolved;
CSS-36's compatibility/timing policy remains unselected. No lookup, sharing,
publication or automatic full-file read is wired by this task.

## Approval and measurements

In this task's conversation, the user approved benchmarking two 4 MiB edge
samples, five evenly spaced 2 MiB samples and nine evenly spaced 1 MiB samples.
The user authorized **synthetic temporary files only**, then explicitly chose
**nine 1 MiB positions**, acknowledging representative storage/media measurements
remain unavailable. This is not an inferred approval from the timing recommendation.

Command: `.venv/bin/python scripts/benchmark_community_fingerprints.py`.
Darwin arm64, Python 3.11.16, seven repeats per case. Inputs were allocated
temporary files with byte at offset `i` equal to `i % 256`; files were removed
automatically. No user media, database, credentials or remote storage was read.
The first read followed a write: **neither first nor repeated timings establish
cold-cache performance**. CPU is process time; wall is elapsed time, milliseconds.
Repeat medians exclude the first observation. Candidate framing is experimental,
not the approved production algorithm ID or a reusable protocol vector.

| Input | Candidate | Bytes Read | First Wall ms | Repeat Wall ms | Repeat CPU ms |
| --- | --- | ---: | ---: | ---: | ---: |
| 32 KiB | edges 2x4 MiB | 32768 | 0.064 | 0.029 | 0.029 |
| 32 KiB | spread 5x2 MiB | 32768 | 0.027 | 0.025 | 0.026 |
| 32 KiB | spread 9x1 MiB | 32768 | 0.026 | 0.026 | 0.027 |
| 32 KiB | full SHA-256 | 32768 | 0.025 | 0.024 | 0.025 |
| 10 MiB | edges 2x4 MiB | 8388608 | 3.200 | 2.944 | 2.945 |
| 10 MiB | spread 5x2 MiB | 10485760 | 3.448 | 3.792 | 3.784 |
| 10 MiB | spread 9x1 MiB | 9437184 | 3.717 | 3.395 | 3.376 |
| 10 MiB | full SHA-256 | 10485760 | 3.824 | 3.707 | 3.700 |
| 256 MiB | edges 2x4 MiB | 8388608 | 3.162 | 2.988 | 2.979 |
| 256 MiB | spread 5x2 MiB | 10485760 | 3.699 | 3.716 | 3.698 |
| 256 MiB | spread 9x1 MiB | 9437184 | 3.315 | 3.332 | 3.314 |
| 256 MiB | full SHA-256 | 268435456 | 97.610 | 97.782 | 97.495 |

These measurements support bounded local I/O/CPU, not an accuracy rate, real-media
collision study, HDD/NAS seek budget or cross-platform speed claim.

## Frozen byte format

[The pure format module](../../../scanner/formats/community/fingerprints.py)
defines `editarr-community-file-sha256-sampled-1`. Size `S` must fit unsigned
64-bit (`0 <= S <= 2^64 - 1`). Positions use integer arithmetic only:

1. Empty files have no sample ranges.
2. Otherwise `W = min(S, 1048576)` and for `i = 0..8` the initial range is
   `(floor((S - W) * i / 8), W)`. A tuple denotes byte offset and byte length.
3. Merge overlapping **and adjacent** ranges, in ascending order, taking their
   union. Each byte is read once. Inputs up to 9 MiB are fully covered, but their
   algorithm identity remains sampled, never automatically promoted to full.
4. Hash ASCII `editarr-community-file-sha256-sampled-1` followed by one NUL byte,
   unsigned 64-bit big-endian `S`, then unsigned 32-bit big-endian merged count.
5. For each merged range, hash unsigned 64-bit big-endian offset, unsigned 64-bit
   big-endian length, then exactly those file bytes. Framing and bytes are
   interleaved per range, not separate arrays. Output is lowercase hex SHA-256.

Maximum file bytes read: 9437184. Stream reads use at most 1048576 bytes each.
At size 9437185, ranges are `(0, 8388608)` and `(8388609, 1048576)`:
integer rounding plus adjacency merging leaves one unsampled byte.

`sha256-full-1` is optional standard SHA-256 of all file bytes, with **no**
domain/size/range prefix. Empty full input is SHA-256 of empty bytes. The separate
algorithm identifier is mandatory even for small files. It is stronger byte
evidence, not contributor truth, media identity or timing attestation.
Never compare hashes across algorithm IDs, silently reinterpret MD5, or label a
sampled match proof of whole-file equality. An unsampled change can retain a
sampled digest; the production test explicitly demonstrates this.

## Local API and cache

[The local adapter](../../../scanner/integrations/community/fingerprints.py)
exposes `FingerprintCache(capacity=128).fingerprint(path, full=False)` and frozen
`FileFingerprint(algorithm, digest, size_bytes, ranges, measured_at)`. `ranges`
is the merged sample layout (empty for full); `measured_at` is UTC ISO-8601 at
measurement, not cache access. No path, inode or local database ID enters this DTO
or the digest. A caller must explicitly request `full=True`; CSS-48 must not do
so implicitly for Find Filters. A future caller should reuse one cache instance.

The cache is process-local bounded LRU, not persistent state. Its key includes
algorithm plus device, inode, size, nanosecond mtime, nanosecond ctime and mode.
Changed states for the same file identity evict old entries. Inode-zero files are
never cached. Access is serialized; no background job, disk cache, migration or
backup inventory addition is introduced.

Each call opens the selected file and compares descriptor `fstat` and selected
path `stat` to the initial state, including a second check before returning a
cache hit or newly read result. The descriptor remains open through reading.
Detected append, truncation, replacement, deletion or modification raises
`FileChangedError` without returning the digest and clears the cache. Short reads
also fail closed. Missing/unreadable inputs never fall back to old evidence.
Callers may explicitly retry against stable bytes; no unbounded retry loop is
hidden in this API. Renames preserve the digest; ctime changes can require rehash.

Checks establish stability under the filesystem's reported metadata; they cannot
lock external writers or guarantee future immutability after return. Filesystems
with unreliable/coarse change metadata and adversarial metadata-preserving writes
are not certified. Full hashes also describe bytes at measurement, not future
file state. No claim of protection against a privileged local attacker is made.

## Shared fixtures and verification

[Frozen vectors](../../../tests/fixtures/community/file-fingerprints-v1.json)
contain sizes, merged ranges and sampled/full digest pairs for deterministic
`i % 256` bytes: empty, three bytes, 1 MiB +/- one, 9 MiB +/- one and 17 MiB.
Expected digests were independently calculated using Node `createHash` and
big-endian Buffer writes, then verified by Python and Worker Web Crypto/DataView
with BigInt offset arithmetic. These are compatibility inputs for CSS-36/37/48,
not a downstream match-band policy. A new framing/layout requires a new algorithm
version and vectors, never a changed meaning for this ID.

- `.venv/bin/python -m pytest -q tests/test_community_fingerprints.py tests/test_community_fingerprint_benchmark.py tests/test_cache_hashing.py tests/test_runner_cache.py`: **69 passed**, final post-format run.
- `pnpm --dir community-service exec vitest run test/file-fingerprint-vectors.test.ts`: **9 passed** in local Worker runtime.
- `.venv/bin/ruff format` on the five changed Python files applied formatting;
  `.venv/bin/ruff check` on those paths passed.
- `pnpm --dir community-service exec tsc --noEmit`: passed.
- Focused editor diagnostics: no errors. Documentation links/fences and whitespace
  are checked at closeout.

Initial validation found an incorrect unmerged boundary expectation in the test,
not a production failure; it was corrected with the exact cap-plus-one case and
a separate nine-disjoint-ranges case before the final passing run.

Limits: no Windows/Linux runtime execution, live NAS/media read, application
lookup integration, deployment, scan or model acquisition occurred. Cross-language
byte agreement was exercised on macOS; other hosts remain unverified. Legacy
MD5 and pipeline cache code is unchanged. No database or user data was modified.

Next: CSS-36 reads this contract and CSS-34, then prepares concrete compatibility
and duration/audio conflict examples for approval under remaining G-MATCHING.