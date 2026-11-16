# CSS-35A/B/C initial implementation handoff

Historical initial slice, superseded by the [completed implementation contract](35-track-contract.md).

October 2, 2026, GitHub Copilot. All three tasks remain IN PROGRESS;
this is an initial slice, not completion or an approved stream protocol.

## CSS-35A internal contract

- `scanner/pipeline/audio_tracks.py` owns frozen `AudioTrack`,
  `AudioTrackInventory`, and `AudioTrackSelection` dataclasses.
- Inventory carries a local `file_version` and ordered tracks. Tracks retain
  physical stream index, nullable language/title/codec/channels, observed
  disposition bitmask, nullable start/duration seconds and rational timebase.
  Tags are observed only; no filename or role inference is performed.
- Preselection is the only track or exactly one default-flagged track. Otherwise
  `select()` requires an index. Missing/invalid indexes and no audio fail explicitly.
- `revalidate()` requires the same file-version token and all observed track
  metadata. Never substitute a disappeared track, including a new default.
- The token hashes canonical local resolved path, device/inode/size/mtime/ctime
  and inventory metadata. It is not portable identity or public content evidence.
  Mutation during probing is rejected. This is metadata-based detection, not
  immunity to adversarial mutation; no content digest is fabricated.
- PyAV opens without decoding, with 4 MiB probe-size and 2-second analyze-duration
  options and 5-second I/O timeout. Maximum inventory is 128 total streams;
  language/title tags are at most 512 characters. These FFmpeg options and I/O
  timeout are not a proven hard total I/O or CPU/wall bound on local files.
- Optional keyword-only `extract_audio(audio_selection=...)` validates before
  extraction, maps exactly `0:<physical stream index>`, validates again before
  replacing the destination, and removes partial output on failure. Existing
  callers retain their existing behavior until explicitly wired to selection.
- `selection.extraction_cache_key()` binds complete selection and versioned
  mono 16 kHz PCM-S16LE extraction behavior. Callers must still adopt that cache
  namespace for WAV/transcript/checkpoint evidence; it is not wired yet.

No public route, request DTO, remembered selection, queue column, actual analyzed
provenance, migration or backup state was introduced. No existing database was
opened or upgraded. Legacy scans are unchanged and retain unknown analyzed tracks.

Next exact action: expose inventory through the existing protected local media
API, add explicit request selection and file-bound remembered selection, then
carry separate requested/actual snapshots through a data-preserving migration,
queue, restart/retry, execution coverage and cache/checkpoint paths. Verify
supported upgrades and backup/restore with disposable file-backed fixtures before
applying any authorized upgrade to an existing database.

## CSS-35C reusable control

`web/src/scans/audio-track-field.tsx` defines matching provisional structural types
and a controlled `AudioTrackField`. Every label includes physical stream index,
language/title (honest unknown labels), codec/channels and observed default hint.
Selections carry both file version and full track metadata. Changed versions,
metadata or disappeared streams clear the displayed choice and require reselection;
the field never calls `onChange` to substitute an input. Loading, error/retry,
empty and disabled states are explicit. Existing `field` styling and native select
are reused; no dependency was added.

This control is not mounted in the application. Parent-owned async loading,
default selection, late-response guards and file-bound memory are not implemented.
No browser/layout/accessibility verification or completed analyzed-track display
is claimed. Next exact action: freeze CSS-35A's protected API DTO, move shared DTO
types into the matching API module, and add one reusable inventory/selection hook
before wiring catalog and Other Files. Follow with 320/1440px browser checks.

## CSS-35B experiment proposal awaiting approval

The user approved the proposed generated-only experiment envelope on October 2,
2026. No production stream digest algorithm or sampling policy is selected.
Do not equate the local selection cache key with a stream fingerprint.

Proposed generated-only experiment envelope (requires explicit approval):

- Use existing FFmpeg/PyAV tools only, no dependency/model acquisition, user
  media, service access or persistent database. Record actual versions/platform.
- Generate at most 12 temporary assets, each at most 30 seconds and 32 MiB,
  with AAC audio and H.264 video; cap total retained fixtures at 256 MiB.
  Generator commands get 30-second subprocess timeouts and bounded file checks.
- Compare three strategies: three timeline positions with 0.25-second windows;
  five positions with 0.25-second windows; three positions with 0.5-second windows.
  Positions span the observed stream timeline, not container-byte offsets.
- Proposed per-run abort envelope: 3 MiB hashed payload, 16 MiB measured source
  reads, 20,000 demuxed packets and 10 seconds wall time. Bound seek count;
  report unsupported rather than sequentially reading a whole stream when indexes
  or budgets fail. Cancellation and mutation checks are mandatory.
- Measure source bytes/read calls via an instrumented seekable input wrapper,
  seeks, packets visited/hashed, payload bytes, process CPU and wall time. Prove
  wrapper coverage; if demux bypasses instrumentation, report I/O as unmeasured
  and do not claim the read cap. Hash payload size alone is insufficient.
- Compare raw packet framing against codec-aware configuration/payload framing,
  including H.264 Annex-B versus length-prefixed representation and AAC framing.
  Do not presume codec normalization is safe or choose a production algorithm.
- Cases: unchanged repeat, lossless remux/timebase changes, reordered audio,
  changed audio only, changed video only, duplicate-language commentary, delayed
  audio, re-encode, missing index, unsupported codec and in-flight mutation.
- Record digest candidate behavior independently of measured stream timing.
  Equal samples never prove whole-stream equality or authorize retiming. No
  stream-start tolerance is selected by this proposal.

Next exact action: obtain experiment-envelope approval, then benchmark candidates
and present measured normalization/bounds results for a separate format approval.
Final integration must use CSS-35A's persisted actual analyzed track, not inventory
defaults or whichever track is selected later. CSS-35's protocol stays unchanged.

## Verification so far

- `.venv/bin/python -m pytest -q tests/test_audio_tracks.py tests/test_audio_progress.py`:
  49 passed, including a temporary generated two-AAC-track Matroska file,
  same-language distinct-title observations and distinct selected WAV outputs.
  Contract tests cover default ambiguity, missing/stale inputs, file mutation,
  bounded metadata/inventory, cache-key separation and atomic output preservation.
- `pnpm --dir web exec vitest run src/scans/audio-track-field.test.tsx --maxWorkers=1`:
  3 passed (duplicate labels/exact payload, changed version/metadata/index and
  loading/error/retry/no-audio/unknown-label states).
- `.venv/bin/ruff format scanner/pipeline/audio_tracks.py tests/test_audio_tracks.py`
  applied formatting; `.venv/bin/ruff check` on those files and
  `scanner/pipeline/audio.py` passed after correcting one test import-order issue.
  The final focused Python rerun passed all 49 tests.
- `pnpm --dir web build`: TypeScript and Vite production build passed.
  Focused editor diagnostics found no errors in the five touched source/test files.
- Node documentation check: 123 local links exist and fences are balanced across
  the three task briefs, task index and this handoff. `git diff --check` passed.

Remaining verification: durable execution, checkpoint isolation, protected API,
restart/retry/cancellation integration, migration/backup, async UI races and browser
journeys. Stream sampling benchmarks and Python/Worker digest vectors await approval.