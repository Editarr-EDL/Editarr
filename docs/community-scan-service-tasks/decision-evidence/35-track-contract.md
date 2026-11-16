# CSS-35A/B/C selected-track and stream contract

October 2, 2026, GitHub Copilot. This supersedes the
[initial slice](35-track-start.md). User approval in this session covers the
generated-only experiment envelope and the subsequent three-position, 250 ms,
codec-aware compressed-stream v1 proposal. No existing database upgrade, user
media read, model acquisition, community deployment or downstream matcher change
was authorized or performed.

## Selected audio contract

`scanner/pipeline/audio_tracks.py` owns frozen `AudioTrack`, `AudioTrackInventory`
and `AudioTrackSelection`. The strict API counterparts live in
`scanner/modeling/model_audio_tracks.py`. Tracks contain physical `stream_index`,
nullable observed `language`, `title`, `codec`, `channels`, disposition bitmask,
nullable `start_seconds`, `duration_seconds`, and rational-string `time_base`.
Unknown labels and timing remain unknown. Index selects local input, not portable
content identity; no language/role inference is made from paths or descriptions.

The local 64-hex `file_version` hashes resolved path, device/inode/size/mtime/ctime
and ordered track metadata. It is not a content hash. Selection requires the
complete version and track snapshot. Only the sole track or exactly one flagged
default can be preselected. Ambiguous defaults require choice. Remembered choices
are keyed by canonical local path and must match the exact version/metadata; a
stale remembered choice suppresses even a newly available default.

Protected GET `/api/media/audio-tracks?video_path=...` returns
`{file_version, tracks, selection, selection_stale}`. Protected, CSRF-checked POST
`/api/media/audio-tracks/selection` takes `{video_path, selection}` and returns the
revalidated selection. Unavailable/stale inputs return 409; malformed DTOs 422.
All Jellyfin, Radarr, Sonarr and Other Files audio admission requires explicit
`audio_selection: {file_version, track}`. Video-only and credits require none.

The durable queue/Scan DTO carries nullable `requested_audio_selection` and
`analyzed_audio_selection`. The dedicated worker CLI loads the durable snapshot
by scan ID/token; there is no separate public scan CLI to extend. Missing queued
selection, changed file/stream, relabelled actual provenance or an audio checkpoint
without actual provenance stops honestly, never picking a replacement. Ordinary
local scanning does not require provider IDs or community enrollment.

FFmpeg receives `-map 0:<physical index>`. Revalidation happens before extraction,
before publishing its atomic WAV replacement, before inference and after success.
WAV and transcript namespaces bind full selection plus versioned mono 16 kHz
PCM-S16LE behavior. Transcripts additionally bind the existing model cache key.
Effective audio extraction/analysis/Qwen coverage includes the same snapshot.
Actual provenance is recorded only after successful analysis, before findings and
checkpoint writes. The guarded record must equal the requested snapshot and may
only transition null to that value; same-track retries are idempotent. A later
failure can retain actual analysis provenance, but does not imply complete findings.
The installed fake-model lifecycle adapter does not fabricate analyzed audio.
Low-level no-selector APIs remain available for historical/import/manual fixtures;
the public HTTP and normal worker execution paths enforce explicit inputs.

Migration `0005_audio_tracks`, based on `0004_media_identity`, adds nullable
requested/actual JSON columns and the `audio_track_selections` table. There is no
backfill: historical actual input remains unknown. Unknown schemas still fail
admission and downgrade refuses provenance deletion. Disposable supported upgrades
from unversioned/v1 and revisions 0001 through 0004 preserve data and backup recovery;
fresh ORM shape and isolated wheel/sdist migration resources are verified. Full
backup modes retain both snapshots and file memory; settings-only modes exclude
these local-media rows. Existing-database application still requires explicit
authorization and an independently verified recoverable backup.

## Compressed-stream v1

Pure framing lives in `scanner/formats/community/stream_fingerprints.py`. Local
sampling/cache access is in `scanner/integrations/community/stream_fingerprints.py`
and `stream_experiments.py`. Distinct domains are:

- `editarr-community-audio-sha256-sampled-1`
- `editarr-community-video-sha256-sampled-1`

SHA-256 input is ASCII algorithm plus NUL, then big-endian unsigned 32-bit framed
ASCII codec and canonical codec configuration, then sample count 3. Each ordered
sample has a big-endian packet count and each packet a big-endian byte length plus
payload. Container size, local path/index, language/title and timestamps are not
hashed. Timing is returned separately as measured start, duration and timebase.

The approved windows start at `start + (duration - 0.25) * i / 2` for `i=0,1,2`.
Backward stream-relative seeks collect packet PTS in the half-open 250 ms interval,
in demux order. Packets traversed before a window still count toward the work cap.
Missing start/duration/timebase or packet timestamps, empty windows, duration at
most 250 ms, unsupported configuration and budget exhaustion fail explicitly.
There is no sequential full-stream fallback or decoded/perceptual fingerprint.

Supported audio is configured AAC-LC: nonempty AudioSpecificConfig of 2 through
65536 bytes, with ordinary first-five-bit object type 2, retained exactly. ADTS/TS,
extended object types and PCM are not normalized. Supported video is H.264 AVCC v1
with nonempty ordered SPS/PPS and 1/2/4-byte NAL lengths (3 is rejected). Canonical
configuration retains profile/compatibility/level, ordered 32-bit-framed SPS/PPS,
and 32-bit-framed trailing configuration. The NAL prefix width is omitted; each
packet's NAL payloads receive fixed 32-bit length framing. Packet boundaries and
all NAL bytes remain significant. Annex-B and arbitrary repacketization are not
supported. MP4/MOV remux, timebase changes and reordered audio are regression-tested,
not a blanket guarantee for every remux/container or encoder.

Each fingerprint uses a single `BoundedInput` open for probing, playable video
selection (excluding cover art), actual-audio snapshot validation and sampling.
Inventory probes use the same reader independently. Aggregate actual file reads
are capped at 16 MiB and seeks at 128. At most 128 streams, 20000 visited packets,
64 KiB configuration and 3 MiB sampled raw/normalized payload are accepted.
Cancellation and elapsed-time checks run at I/O and packet boundaries and completion.
The 10-second elapsed budget is cooperative: it cannot preempt a blocking OS read
or a native demux call between checks, and is not a hard CPU/process timeout.
No hard CPU-time limit or real-storage latency guarantee is claimed.

The process-local locked LRU has at most 128 entries, keyed by resolved path,
device/inode/size/mtime/ctime, local selector, normalization behavior and (for AI
audio) full actual snapshot. Cache hits recheck file state and cancellation,
return `cache_hit=true` and zero current sampling I/O/packet/payload counters.
Inode zero bypasses caching. Before/after stat checks reject ordinary mutation;
they are not protection against adversarial metadata-preserving writes.

`fingerprint_analyzed_audio(db, scan_id)` only accepts generated audio scans with
immutable actual input, validates it inside the counted probe, and hashes only its
physical stream. A requested choice, today's default, alternate inventory or
legacy/imported unknown provenance cannot supply identified audio evidence.
`fingerprint_video(path)` uses the first non-cover-art video inside that same probe.
Independent byte framing vectors are in
`tests/fixtures/community/stream-fingerprints-v1.json`, verified independently by
Python hashlib and Worker WebCrypto (`test/stream-fingerprint-vectors.test.ts`).

Equal samples do not prove whole-stream equality, timestamp alignment or filter
compatibility. Delayed/re-encoded cases did not match; offset/perceptual support is
not claimed. The separate approved 2-second file-duration tolerance is not stream
alignment permission. Existing container SHA-256/full SHA-256 and MD5 are unchanged.

## Experiment evidence

`PYTHONPATH=. .venv/bin/python scripts/benchmark_community_streams.py` generates
eleven temporary 12-second/320x180 variants using existing FFmpeg 7.1/PyAV 18.1.0.
Each asset is limited to 32 MiB, all retained assets to 256 MiB; generators have
30-second timeouts. No user media or new dependency is used. Synthetic macOS,
cache-resident results are not real-film accuracy, cold-storage or platform evidence.

| Candidate | Supported cases | Max reads | Max payload | Max wall ms | Max CPU ms | Max seeks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 x 250 ms (selected) | 24 | 212668 | 23581 | 1.4097 | 1.407 | 14 |
| 5 x 250 ms | 24 | 376508 | 43788 | 1.2918 | 1.292 | 16 |
| 3 x 500 ms | 24 | 245436 | 48316 | 1.1365 | 1.124 | 14 |

All candidates matched unchanged/remux/timebase/reorder fixtures. Changing video
preserved audio; changing audio preserved video. The benchmark reencodes sibling
audio too; the production regression separately copies an unchanged sibling and
proves its fingerprint survives. TS fails unsupported AVCC/AAC representation,
Matroska fixture fails missing per-stream timing/index, PCM fails codec support.
Final framing/config normalization is covered by generated production regressions;
the table records candidate measurement, not guaranteed final end-to-end latency.

## UI and verification

`web/src/api/api-audio-tracks.ts`, `use-audio-tracks.ts`, and `AudioTrackField` share
catalog/Arr/Jellyfin and Other Files behavior. A 250 ms debounce, abort signal,
generation guard and source guard discard late loads/confirmations. Server memory
and defaults are authoritative. Confirmation and prepare revalidate before queueing;
failure clears selection rather than substituting it. Loading/error/retry, no-audio,
unknown labels and stale remembered selections are explicit. The native select has
an explicit accessible name and the chosen full metadata wraps below it on mobile.
Scan summaries show immutable analyzed metadata, requested input for pending jobs,
and honest historical unknown input. These helpers are ready for sharing previews;
publication/Find Filters integration is not part of these tasks.

Final backend command `.venv/bin/python -m pytest -q tests/test_audio_tracks.py
tests/test_audio_tracks_api.py tests/test_audio_track_execution.py
tests/test_stream_fingerprints.py tests/test_audio_progress.py
tests/test_runner_cache.py tests/test_dedicated_worker.py tests/test_editarr_scans.py
tests/test_existing_migrations.py tests/test_fresh_migrations.py
tests/test_migration_packaging.py tests/test_version_info.py
tests/test_backup_restore.py tests/test_backup_activation.py` passed 178 tests.
Changed-file Ruff format/check passed after the final refactor; focused editor
diagnostics found no errors. The orchestrator is 465 lines and its audio workflow
is in the focused 99-line `runner_audio.py`, preserving existing adapter patch points.
`pnpm --dir web exec vitest run --maxWorkers=1` passed 181;
`pnpm --dir web build` and `pnpm --dir web exec tsc --noEmit` passed.
`pnpm --dir community-service exec vitest run test/stream-fingerprint-vectors.test.ts
test/file-fingerprint-vectors.test.ts` passed 11; Worker `tsc --noEmit` passed.
`pnpm --dir web exec playwright test e2e/audio-tracks.spec.ts` passed 2 journeys
at 320/1440 with exact payloads, file-bound memory, duplicate language labels,
no-audio/video/credits, unknown tags, error/retry, actual/unknown displays, focus,
axe and root-overflow checks. Screenshots are `web/test-results/css35c-{catalog,manual}-{320,1440}.png`.
Direct headless macOS Chromium key events did not commit native menu selection;
browser tests verify Tab/focus traversal and Playwright native `selectOption`
separately. Actual OS-menu keyboard selection is not claimed as automated evidence.
No real AI inference, Windows/Linux runtime or user-database upgrade is claimed.

## Next action

CSS-36 follow-up completed October 2: the user approved exact playback-relative
timing. `StreamFingerprint` now also carries nullable rational-string
`playback_start` and `duration`. The counted sampler computes start as stream
start times its timebase minus container start divided by `av.time_base`;
unknown container origin remains null. Duration is stream duration times timebase.
Absolute seconds/timebase remain available, and digest bytes/windows are unchanged.
The process cache behavior key now includes `playback-rational-1`; cache hits retain
the timing and still report zero current sampling I/O. No additional probe is used.
This is stream-index timing evidence, not proof of every packet/decoded-frame offset.
The [final compatibility policy](36-compatibility-core.md#completed-stream-policy)
compares rational seconds without automatic retiming.

CSS-37/48: adopt the same actual-only
accessors and unknown exceptions without uploading alternate inventories. CSS-46/49:
reuse the API types/metadata display; mixed-track outgoing review exclusion remains
its own explicit preview workflow and must not mutate saved reviews.