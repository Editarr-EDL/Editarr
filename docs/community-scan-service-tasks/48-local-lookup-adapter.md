# CSS-48 — Combine local discovery with remote lookup state

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally, October 4, 2026**. Mock transport and generated-media
verification passed. No live community activation, existing-database change or
download UI is implied.

- Prerequisites: [CSS-29](29-local-service-client.md), [CSS-34](34-media-identity.md), [CSS-35](35-file-fingerprints.md), [CSS-35A](35a-audio-track-provenance.md), [CSS-35B](35b-stream-fingerprints.md), [CSS-36](36-compatibility-policy.md), [CSS-41](41-catalog-lookup-api.md), [CSS-47](47-find-filters-extraction.md)
- Decision gates: None beyond the shared rules.
- Parent sections: main §7; contracts §7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Remote lookup must use measured target metadata and selected media, not upload an entire library. Local results should remain available during service failures.

## Starting points

scanner/community/ discovery; scanner/http/catalog_source_api.py adapters; web/src/api/ query state.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Resolve selected target identity/fingerprint and call bounded availability/match endpoints.
2. Normalize remote summaries into the shared result model while preserving local-first discovery and approved uncertainty reasons.
3. Implement cancellation, stale results, refresh, backoff and structured no-ID/offline/unavailable/unsupported errors.
4. Bind target audio comparisons to an explicit file-bound selected track using CSS-35A, with separate audio/video fingerprints and timing from CSS-35B. Preserve language/description metadata; changing the track invalidates comparison caches and late responses. Do not send alternate-track inventory, private stream selectors or whole-library media; unknown/unavailable track identity stays review-required, not a guessed strong match.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] A failed remote query is never displayed as zero matches; local results remain usable.
- [x] No whole-library polling is added and private local paths never reach service requests.
- [x] Cache invalidates on changed target/policy; cancelled requests cannot overwrite a newer selection.
- [x] Track changes invalidate channel evidence; independent video/audio compatibility survives opposite-channel differences and no other-track inventory leaks.

## Verification

- Contract/API tests for missing IDs, changed file, stale results, service failures and racing target selection.
- Run integration adapter tests with mocked community transport.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide unified result/status DTOs and cancellation/cache rules to CSS-49/50.

## Handoff summary for the next task

Status / date / implementer: DONE locally / 2026-10-04 / GitHub Copilot.

Implemented outcome and last completed substep: Explicit one-target lookup through
the existing signed local client, with independent availability/page errors and
an optional remote branch in the extracted Find Filters controller. Local results
and local association remain independent. Remote results retain their separate
audio/video compatibility and are always unselected. Final duration probing uses
the counted metadata reader, never the scan helper's whole-file frame-count fallback.

Files changed and relevant revision: Initially clean base
`69dd61f3119c1700e79ae48db30bac736ddb7bd3`; no commit made.
New [lookup adapter](../../scanner/integrations/community/lookup.py),
[private target capture](../../scanner/integrations/community/lookup_targets.py),
[strict models](../../scanner/modeling/model_community_lookup.py),
[protected route](../../scanner/http/community_lookup_api.py),
[lookup tests](../../tests/test_community_lookup.py),
[capture tests](../../tests/test_community_lookup_targets.py),
[browser API adapter](../../web/src/api/api-community-lookup.ts),
[API tests](../../web/src/api/api-community-lookup.test.ts),
[remote state hook](../../web/src/scans/use-community-lookup.ts) and
[state tests](../../web/src/scans/use-community-lookup.test.tsx).
Updated [transport](../../scanner/integrations/community/transport.py),
[transport tests](../../tests/test_community_transport.py),
[route composition](../../scanner/http/community_api.py),
[auth inventory](../../scanner/security/auth_policy.py),
[local query cancellation](../../web/src/api/api-scans.ts),
[composed controller](../../web/src/scans/use-find-filters.ts) and
[controller tests](../../web/src/scans/use-find-filters.test.tsx), this brief,
task index, shared context, parent plan/implementation and API documentation.

Contracts / APIs / schemas / migrations / fixtures introduced:

- `POST /api/community/lookup` uses the mounted local API, real administrator
  session, same-origin/CSRF protection and no-store responses. Request:
  `{target:{source,item_id,media_source_id?,video_path?},audio_selection?,
  page_size?,cursor?,target_key?,refresh?}`. Sources are Jellyfin/Radarr/Sonarr/manual;
  `video_path` is local-only and accepted only for manual targets. Provider paths,
  typed identity and selected version are resolved server-side without media/review
  upserts. Unknown manual identity remains unknown; browser-supplied evidence,
  hashes and alternate-track inventory are rejected.
- Response: `{version:1,policyVersion,targetKey,targetEvidence,warnings,
  availability,availabilityError,page,pageError}`. `targetKey` is an opaque local
  SHA-256 guard, not a hosted cursor. Errors are nullable `{code,retryable}`;
  failed calls leave their payload null, not an empty success. Missing identity
  keeps null counts and skips matches. Successful empty pages are distinct.
- Signed hosted calls are only `POST /v1/availability/batch` with one `id:"target"`
  item and `POST /v1/matches`. Page size defaults to 16, accepts 1..96 within the
  existing contract ceiling; hosted configured limits still govern admission.
  Each response uses the existing transport's 3,000,000-byte technical ceiling,
  not a newly approved hosted operational limit. Schema, policy, counts and
  per-channel compatibility are validated; compatibility is independently
  recomputed locally. Unknown/private summary fields and coerced selection fail closed.
- `LookupResponse`, `RemoteFilterCandidate` and complete v1/v2 summary metadata
  are exported by the browser adapter. `useCommunityLookup(input|null)` exposes
  `status` (idle/loading/ready/error/disabled/cancelled), `response`, `candidates`,
  `error`, `stale`, `start`, `refresh`, `more`, `cancel`, and display projections.
  No lookup, enrollment, polling or retry starts on mount.
- `useFindFilters` adds optional `communityInput`, `community` and local-first
  `results: FilterResult[]`. Discriminated entries carry shared display `candidate`
  plus raw local `match` or remote `remote`; raw provenance and conflict reasons
  are not discarded. Existing `candidates`/`selectedIds`/`select`/`associate`
  remain local-only and reject remote IDs. `open` also covers active remote state
  when the optional branch is supplied. CSS-49 owns remote selection/rendering;
  CSS-50 owns download orchestration, not the local association API.
- No schema, migration, persistent lookup cache, secret inventory or hosted
  protocol change. Tests reuse disposable SQLite, generated AAC/H.264 media,
  existing compatibility/fingerprint implementations and mocked signed transport.

Cancellation / cache / privacy rules for CSS-49/50:

- A browser generation includes target fields, complete file-bound audio selection
  and compatibility policy. Changes abort requests, immediately hide old remote
  evidence and ignore late replies even if transport ignores abort. Local target
  changes also invalidate discovery/association callbacks. Closing/unmounting
  cancels; remote candidates never enter local defaults.
- Failed refresh retains prior remote candidates only with explicit error/stale
  state. Changed target/file/track or unsupported policy clears prior remote
  evidence. Pagination requires a nonstale prior cursor and sends the local target
  key; pages append in service order and deduplicate revision IDs. Refresh is
  explicit and bypasses the hosted lookup cache, not valid local fingerprint caches.
- Backend capture checks resolved path/stat generation before/after evidence and
  every network response. The local guard includes path/stat, selected snapshot,
  public evidence and policy; changed file/track/policy rejects an old cursor.
  Existing fingerprint caches validate stat/selector and retain their bounded
  process-local policies. No private guard preimage reaches the service.
- Sampled file evidence reads at most 9 MiB, with no full-file fallback. Independent
  streams retain the CSS-35B counted read/seek/packet/time bounds; inventory and
  duration use the existing counted metadata probe. Unsupported channels stay
  unknown; unavailable duration is not replaced by catalog runtime. Track selection
  revalidates the complete CSS-35A snapshot; language/title are preserved when
  public-safe, using existing sharing redaction rules. No guessed main role,
  description equality claim, retiming or other-track projection is added.
- HTTP disconnect/task cancellation sets a cooperative signal and cancels lookup;
  capture cleanup is drained before releasing the Session. Native work is not
  forcibly preempted. Transport retains at most three attempts, identical bytes/
  operation ID per logical query, fresh signing nonces, bounded Retry-After or
  0.25/0.5-second backoff and its existing per-call deadline. No automatic frontend
  retry loop is added. Enrollment/rotation/access errors retain safe machine codes.

Decisions used (gate IDs and recorded answers): No new product gate. Reused
approved CSS-35 sampled SHA-256, CSS-35A explicit selected-track provenance,
CSS-35B compressed streams, CSS-36 exact playback timing/per-channel policy,
CSS-41 bounded signed read contract and CSS-29 auth/transport. D1-only storage and
default-disabled live activation remain unchanged.

Verification commands and results:

- `.venv/bin/python -m pytest -q tests/test_community_lookup.py
  tests/test_community_lookup_targets.py`: final 38 passed. Covers safe signed
  bodies, structured failures/missing identity, malformed metadata, cancellation/
  disconnect, changed files/cursors, exact provider versions, read-only manual
  targets, actual generated independent tracks and bounded duration fallback.
- Final scoped regression command: `.venv/bin/python -m pytest -q
  tests/test_community_lookup.py tests/test_community_lookup_targets.py
  tests/test_community_transport.py tests/test_community_client.py
  tests/test_community_api.py tests/test_auth_policy.py
  tests/test_community_matching.py tests/test_community_fingerprints.py
  tests/test_stream_fingerprints.py tests/test_audio_tracks.py
  tests/test_community_donations.py`: 261 passed.
- `pnpm --dir web exec vitest run src/api/api-community-lookup.test.ts
  src/scans/use-community-lookup.test.tsx src/scans/use-find-filters.test.tsx`:
  23 passed. Mounted CSRF transport, explicit lookup, service/offline/stale/empty
  distinctions, late responses, target/track changes, pagination, cancellation,
  local-first usable association and remote-ID rejection.
- `pnpm --dir web test`: 221 passed in 32 files. `pnpm --dir web build`:
  TypeScript/Vite passed. `pnpm --dir web exec playwright test
  e2e/find-filters.spec.ts`: 4 Chromium journeys passed at 320x900/1440x900,
  including axe/focus/overflow/retry/association/review reuse and no remote calls
  from the existing local-only catalog dialog.
- Ruff check and format check passed for all ten touched Python files. Editor
  diagnostics passed for all seventeen changed source/test files. Documentation
  links/fences and `git diff --check` validated before final handoff.

Known failures, limitations, unavailable checks, and recovery notes: No remaining
test failures. Existing Vite chunk warning remains (505.09 kB main JS). Catalog
does not yet supply `communityInput` or display remote rows; that wiring and rich
error/stale/track UI are CSS-49, and downloads CSS-50. No Worker changes/tests,
live service/provider/media/model use, user database, migration or deployment.
Other browser engines, native packaging and real-storage performance unverified.
Cancellation is cooperative, not hard OS/native preemption; stat caching is
metadata-dependent, not adversarial-write immunity or future immutability.
No recovery action required.

Remaining work in this task: None.

Next task ID and exact first action: CSS-49. Read
[49-find-filters-results.md](49-find-filters-results.md), this DTO/race/privacy
handoff and [CSS-35C track UI](35c-audio-track-selection-ui.md). Start in
[catalog detail](../../web/src/catalog/media-detail.tsx) at its selected-version
`useFindFilters` input. Supply the exact provider target plus the existing
file-bound audio selector, then render `results` and `community` status in
[the shared dialog](../../web/src/scans/find-filters-dialog.tsx). Keep uncertainty
and opposite-channel conflicts visible and remote selection explicit; do not
route remote IDs through local association or implement CSS-50 downloads early.

Direct dependent tasks: [CSS-49](49-find-filters-results.md).

