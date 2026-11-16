# MTS-00 - Freeze contracts and baseline

Status: **DONE**

Dependencies: none. Deliverable: exact cross-layer contracts, algorithm vectors, and
baseline evidence; no production implementation.

Read the task-pack README, parent plan, repository `AGENTS.md`, the completed MCF task-pack
contract/handoffs, live Python/TypeScript models, OpenAPI, queue contracts, and current tests
before changing files.

## Scope

1. Record the pre-existing worktree and run an appropriate test-owned baseline. Do not
   create tables, wipe a database, acquire models, or touch media/sidecars.
2. Add a `Contract record` to this file freezing exact Python, JSON, database, and
   TypeScript spellings for:
   - review synchronization START, END, and END mode;
   - canonical before-credits MCF END versus physical synchronization END;
   - accepted credits scan/candidate provenance on a review;
   - target timing fields on upload/path MCF import options and recovery retries;
   - pure retiming request/result structures for a complete unsaved review draft;
   - credits scan mode/configuration, candidate/evidence/result, status, cancellation,
     model-consent, acceptance, and clip contracts;
   - scan-history and finding fields needed to display detector method/confidence;
   - frontend discriminated unions and nullability/default rules.
3. Freeze the affine formula, endpoint validation order, precision/rounding policy, outlying
   cue behavior, shared-range ordering, physical-runtime validation, and exact behavior when
   source MCF bounds are invalid or recovered.
4. Freeze the complete revision matrix for synchronization state, cue changes, canonical
   MCF bounds, detection provenance, scan membership, and accepted End Credits snapshots.
5. Freeze exact protected routes/methods, auth/CSRF policy, safe error codes/statuses,
   OpenAPI responses, queue state transitions, clip MIME/size/duration bounds, and target
   trust boundaries.
6. Freeze credits-finding values: `dispensable_scenes/dispensable`, description and MCF
   comment `End Credits`, visual/video, Skip, selected, configured severity default,
   detector confidence, accepted start, and effective file END.
7. Define deterministic detector evaluation fixtures, boundary tolerance, confidence bands,
   evidence ordering, cache-version identity, low-confidence presentation, and the minimum
   evidence required to return a candidate. These definitions must implement the parent
   decisions without inventing an accuracy claim.
8. Define Qwen selection and consent behavior from existing capability/model UI patterns.
   Missing artifacts cannot be acquired before an explicit size/capability disclosure.
9. Add contract-level tests/fixtures that later tasks can satisfy without adding production
   behavior. Include exact affine vectors and representative OpenAPI/frontend fixture shapes.
10. Decide routine representation details only where the parent plan permits. If a choice
    changes product semantics, destructive lifecycle, model trust, or export output, mark
    this task BLOCKED and ask the user.

## Required precision

- Source MCF parsing is independent of target-file duration until mapping is complete.
- Continue Anyway cannot synthesize source bounds for retiming.
- Before credits is the import default and has no inferred initial END.
- File end may start from probed duration but remains user-correctable and runtime-bounded.
- Review retiming includes selected, disabled, source-linked, and staged manual filters.
- Detection completion alone creates no review mutation. Acceptance is a separate explicit
  operation and the normal scan remains in history after draft cancellation/discard.
- MCF export requires canonical before-credits bounds; EDL does not.
- Detection provenance that does not affect output must not make sidecars outdated.
- Existing strict MCF registry and immutable source-evidence rules remain authoritative.

## Out of scope

- Production models, database rows, routes, queue behavior, detector code, or UI.
- Development database reset; MTS-01 alone owns the approved reset.
- Model downloads or real-media accuracy claims.

## Verification

- Focused schema/OpenAPI/model/frontend contract tests added by this task.
- Existing MCF codec/import/review/export and scan queue contract tests.
- Baseline Ruff, focused pytest/Vitest as applicable, and `git diff --check`.

## Acceptance criteria

- [x] Every cross-layer field, enum, route, state, revision effect, error, and trust boundary
      needed by MTS-01 through MTS-07 is exact.
- [x] Affine mapping vectors and invalid/outlying cases are fixture-backed.
- [x] Credits detector evidence, confidence, cache, consent, preview, and acceptance contracts
      are testable without real models.
- [x] Existing clients and fresh-schema compatibility behavior are explicit.
- [x] Baseline commands, counts, warnings, and unrelated worktree state are recorded.
- [x] No production behavior, schema, database, media, sidecar, service, or model changed.

## Contract record

This is the normative implementation contract for MTS-01 through MTS-07. It records
planned interfaces, not behavior present at the MTS-00 baseline. JSON uses `snake_case`;
UUIDs are strings; timestamps are timezone-aware UTC ISO 8601 strings; finite seconds are
JSON numbers backed by Python/SQLite binary64 values. New write DTOs extend
`ReviewWriteModel` and reject unknown fields. TypeScript mirrors these names exactly except
inside `ReviewDraft`, where existing camel-case draft members remain the local convention.

### Timing authorities and persistence

`SynchronizationEndMode = "before_credits" | "file_end"`.
`ReviewSynchronization` has required finite `start_seconds >= 0`, required finite
`end_seconds > start_seconds`, and required `end_mode: SynchronizationEndMode`. Every saved
review has exactly one synchronization basis. Ordinary review creation seeds
`{start_seconds: 0, end_seconds: authoritative_runtime, end_mode: "file_end"}`. Creation
fails with `media_runtime_unavailable` when no authoritative positive runtime is available;
runtime is never inferred from findings. MCF import instead requires the submitted target
synchronization contract described below. Existing clients may still omit synchronization
on create and receive the server seed, but `ReviewSave.synchronization` is required; an
older save payload therefore receives the existing path-safe 422 validation response.

`SavedReview` and `ReviewSave` add required `synchronization: ReviewSynchronization` and
nullable `accepted_credits: AcceptedCreditsReference` on writes or
`AcceptedCreditsProvenance` on reads. `ReviewCreate` does not accept an arbitrary timing
basis. Duplicate copies synchronization and accepted provenance, with the same source scan
reference and fresh review/filter IDs. Delete cascades only review-owned rows.

MTS-01 adds these fresh-schema authorities without a preservation migration:

| Table/column | Nullability and ownership |
| --- | --- |
| `review_synchronizations.review_id` | PK/FK to `saved_reviews.id`, cascade with review |
| `review_synchronizations.start_seconds` | non-null, finite, `>= 0` |
| `review_synchronizations.end_seconds` | non-null, finite, `> start_seconds` |
| `review_synchronizations.end_mode` | non-null, `before_credits` or `file_end` |
| `review_accepted_credits.review_id` | PK/FK to review, cascade with review |
| `review_accepted_credits.scan_id` | FK to the immutable credits scan |
| `review_accepted_credits.source_finding_id` | composite FK with `scan_id` to its finding |
| `review_accepted_credits.candidate_id` | non-null stable candidate ID |
| `review_accepted_credits.detected_boundary_seconds` | non-null finite, `>= 0` |
| `review_accepted_credits.accepted_boundary_seconds` | non-null finite, `>= 0` |
| `review_accepted_credits.confidence` | non-null finite, in `[0, 1]` |
| `review_accepted_credits.method` | non-null `CreditsDetectionMethod` |
| `review_accepted_credits.accepted_at` | non-null UTC timestamp |
| `scans.scan_mode` | non-null `content` or `credits_detection`; default `content` |
| `credits_detection_runs.scan_id` | PK/FK to `scans.id`, cascade with scan |
| `credits_detection_runs.configuration_json` | non-null canonical `CreditsDetectionConfiguration` |
| `credits_detection_runs.detector_version` | non-null cache behavior version |
| `credits_detection_runs.cache_key` | nullable until video hashing completes |
| `credits_detection_runs.result_json` | nullable canonical `CreditsDetectionResult` |
| `credits_detection_runs.completed_at` | nullable UTC timestamp |

`ReviewMcfDocument.material_start_seconds` and `material_end_seconds` remain the sole
canonical MCF material bounds. They are not duplicated in synchronization storage. A
complete Before-credits state requires document START/END to equal synchronization
START/END. File-end mode requires document START to equal synchronization START while
document END remains a distinct nullable before-credits value strictly after START and no
later than synchronization END. Physical duration is never copied into document END merely
because File-end mode is selected.

`AcceptedCreditsReference` contains `scan_id`, `candidate_id`, and finite
`accepted_boundary_seconds >= 0`. It is intentionally the only browser-writeable acceptance
shape. The server resolves and verifies target, physical version, completed result, source
finding, detected boundary, confidence, method, and effective file end before creating
`AcceptedCreditsProvenance`, which adds `source_finding_id`,
`detected_boundary_seconds`, `confidence`, `method`, and `accepted_at`. A null reference
removes only review-owned accepted provenance; it never deletes or mutates the scan.

### Affine timing contract

The one backend authority lives in `scanner.mcf` and exposes
`map_affine_endpoint(endpoint_seconds, source, target)`,
`map_affine_range(start_seconds, end_seconds, source, target)`, and
`retime_review_draft(request)`. `TimingInterval` has finite `start_seconds` and
`end_seconds`. Mapping is exactly:

```text
target.start_seconds
   + ((endpoint_seconds - source.start_seconds)
       / (source.end_seconds - source.start_seconds))
      * (target.end_seconds - target.start_seconds)
```

Validation order is normative: (1) reject non-number, NaN, and infinity; (2) validate the
source interval as non-negative and strictly ordered; (3) validate the target interval the
same way; (4) validate an available authoritative runtime as finite and positive and require
the target interval within `[0, runtime]`; (5) validate each input cue as non-negative with
an absent END or END strictly after START; (6) map each present endpoint independently;
(7) reject non-finite mapped values; (8) require mapped START `>= 0`, mapped END absent or
strictly after START, and every mapped endpoint `<= runtime` when runtime is known. No step
clamps, merges, splits, reorders, classifies, selects, or changes any non-timing field.

Computation uses Python `float` with no intermediate rounding. Timestamp formatting alone
rounds to the existing millisecond display/export boundary. Equal source ranges produce
bit-equal mapped ranges. Array order, including separate payloads sharing one range, is
preserved. Endpoints outside source bounds are mapped by the formula; only the resulting
range/runtime validation may reject them. Point findings with null END map START only.

The vectors in `tests/fixtures/mts/contract-v1.json` are exact contract examples. Repeated
retiming uses the request's current synchronization and current draft values, including
prior unsaved mappings and manual edits; imported source findings are never reloaded.

MCF parsing validates grammar and each cue's own non-negative, strictly ordered range before
any target-runtime check; it does not require cue endpoints to lie inside source START/END.
Missing, malformed, equal, reversed, or parser-recovered source bounds produce fatal
`invalid_source_mcf_bounds`; `ignore_errors` cannot bypass it. Valid outlying source cues
are retained on the imported scan. Target duration is applied only to mapped review values.

### Import and draft-retiming DTOs

`McfImportOptions` keeps `target`, `name`, and `ignore_errors` and adds required
`synchronization: ReviewSynchronization`, nullable
`mcf_material_end_seconds`, and nullable
`accepted_credits: AcceptedCreditsReference`. Both path and multipart upload transports use
the identical options object, and Continue Anyway resubmits it byte-semantically unchanged
apart from setting `ignore_errors: true`.

Before-credits import requires `mcf_material_end_seconds` to equal synchronization END.
File-end import permits it to be null or a finite value in
`(synchronization.start_seconds, synchronization.end_seconds]`. Import preserves the source
MCF document and source finding seconds exactly, while the initial review document uses
synchronization START and the validated before-credits END. API defaults are intentionally
limited to `ignore_errors=false`; UI-only `McfImportTimingDraft` starts at
`start_seconds=0`, `end_seconds=null`, `end_mode="before_credits"`, and
`mcf_material_end_seconds=null`. Selecting File end may copy the server-reported runtime
into the editable draft END, but the submitted request still carries an explicit value.

`ReviewRetimeRequest` has `revision`, complete ordered `memberships`, `current_synchronization`,
`proposed_synchronization`, `mcf_document`, complete ordered `filters`, and complete ordered
`new_manual_filters`, plus nullable `accepted_credits`. `ReviewRetimeResult` has
`synchronization`, `mcf_document`, `filters`, nullable `accepted_credits`, and
`new_manual_filters`. The protected operation is
`POST /api/reviews/{review_id}/retime`; it performs no write. The route verifies the review
and revision only for physical-target/provenance freshness. It accepts a current basis that
differs from persisted state so repeated unsaved adjustment works. Before-credits output
sets document START/END to the proposed interval. File-end output sets document START to the
proposed START and preserves the submitted valid document END, including null. Every filter
and staged manual filter is mapped in input order and returned with all other fields exact.
If the draft has accepted credits, the operation maps the review-owned accepted boundary and
its End Credits snapshot while retaining the detector's original detected boundary and other
immutable provenance.

### Credits detector DTOs and evaluation contract

`ScanMode = "content" | "credits_detection"`.
`CreditsDetectionMethod = "chapters" | "visual_text" | "hybrid" | "hybrid_qwen"`.
`CreditsEvidenceKind = "chapter_marker" | "scene_boundary" | "text_density" |
"credit_motion" | "black_frame" | "qwen"`. `CreditsConfidenceBand = "low" |
"medium" | "high"`; bands are presentation labels, not accuracy claims: low is `[0, .60)`,
medium `[.60, .80)`, and high `[.80, 1]`. Confidence is a finite binary64 value in `[0, 1]`;
band assignment uses the unrounded value, so exactly `.60` is medium and exactly `.80` is
high. Display rounding never changes the assigned band.

| DTO | Exact fields |
| --- | --- |
| `CreditsDetectionConfiguration` | `detector_version`, `tail_window_seconds`, `frame_sample_interval_seconds`, `qwen_model: str | null` |
| `CreditsEvidence` | `kind`, `timestamp_seconds`, `score` in `[-1, 1]`, `detail_code`, `details: str | null` |
| `CreditsCandidate` | `id`, `boundary_seconds`, `range_start_seconds`, `range_end_seconds`, `confidence`, `confidence_band`, `method`, ordered `evidence` |
| `CreditsDetectionResult` | `outcome: "candidate" | "no_candidate"`, ordered `candidates`, `best_candidate_id: str | null` |
| `CreditsDetection` | `configuration`, `detector_version`, `cache_key: str | null`, `result: CreditsDetectionResult | null`, `completed_at: datetime | null` |
| `CreditsDetectionStartRequest` | `target: ReviewTargetRequest`, `qwen_model: str | null` |
| `CreditsDetectionStartResult` | `scan: Scan`, `queue_id: int`, `model_consent: CreditsModelConsent | null` |
| `CreditsModelConsent` | `backend_id`, `artifact_ids`, `expected_size`, `availability`, `device`, `license_spdx`, `license_status` |
| `CreditsModelConsentRequest` | `decision: "acquire" | "continue_without_qwen"` |
| `CreditsAcceptanceRequest` | `context`, `accepted_boundary_seconds` |
| `CreditsAcceptanceResult` | `accepted_credits`, `membership`, `filter` |

`CreditsAcceptanceContext` is a discriminated union: `{kind: "import", target:
ReviewTargetRequest}` or `{kind: "review", review_id: str, revision: int}`. `Scan` and
`ScanQueueItem` add required `scan_mode` and nullable `credits_detection`; the latter is
non-null exactly when `scan_mode="credits_detection"`. Credits scans are generated video
scans with `scan_audio=false`, `scan_video=true`; existing content scan options do not become
detector configuration.

Candidates are sorted by descending confidence, then ascending boundary, then ID. Evidence
is sorted by descending absolute score, then kind in the enum order above, then timestamp.
`best_candidate_id` names the first candidate and is null exactly for `no_candidate`.
Returning a candidate requires either one positive credible chapter marker or two positive,
independent non-black-frame evidence kinds within eight seconds. Qwen or black frames alone
never satisfy the minimum. Conflicting evidence lowers confidence but does not hide a
candidate that meets the minimum. The first credible closing-credits boundary wins over a
later post-credits boundary. This minimum is deterministic and fixture-backed; MTS-04 must
justify it from the evaluation corpus or mark itself BLOCKED and request a contract revision
before changing the threshold.

MTS-04's generated fixture manifest is version `mts-credits-evaluation-v1` and each case has
`id`, generated/redistributable `source`, `duration_seconds`, nullable
`expected_boundary_seconds`, `tolerance_seconds`, `tags`, and ordered chapter fixtures.
Required tags cover scrolling, static cards, moving imagery, post-credits, credible/missing/
misleading chapters, no credits, non-English text, black frames, and ambiguous material.
The report records candidate recall within case tolerance, false positives on null-boundary
cases, absolute boundary error, confidence-band counts, runtime, platform, backend, detector
version, and corpus version. It makes no population accuracy claim. MTS-04 must justify the
frozen bands on this corpus or mark itself BLOCKED before production thresholds ship.

Cache identity is a canonical SHA-256 over video hash, corpus-independent detector version,
all configuration values, evidence/threshold version, prompt version, Qwen model ID and
pinned artifact revision when used, and scene/frame cache versions. Any behavior-affecting
change is a miss.

### Credits routes, queue, consent, and clips

The protected routes are:

| Method and route | Contract |
| --- | --- |
| `POST /api/credits-detections` | start/reconnect by authoritative target; 202 `CreditsDetectionStartResult` |
| `GET /api/credits-detections/{scan_id}` | current `Scan`; 200 |
| `POST /api/credits-detections/{scan_id}/model-consent` | explicit consent decision; 202 start result |
| `POST /api/credits-detections/{scan_id}/accept` | validate and return draft acceptance result; 200, no review write |
| `GET /api/credits-detections/{scan_id}/clip?boundary_seconds=...` | bounded MP4 preview |
| `DELETE /api/scans/queue/{queue_id}` | existing cancellation contract |

Start deduplicates an active credits scan by target physical version and exact configuration;
otherwise it creates one normal history scan. A selected ready Qwen model enters `queued`.
A missing selected artifact enters `awaiting_model_consent` without acquisition. Consent
`acquire` enters `acquiring_model`, using existing manifest verification/progress and then
`queued`; `continue_without_qwen` clears Qwen from persisted configuration and enters
`queued`. Normal transitions are `queued -> scanning -> complete`, cancellation uses
`queued|awaiting_model_consent -> cancelled` or
`acquiring_model|scanning -> cancelling -> cancelled`, worker loss uses existing `orphaned`
and resume behavior, and terminal detector errors use `failed`. Completion may contain a
candidate or explicit no-candidate result. Only completed candidate results are acceptable.

Every start creates the `ScanQueueRow` before returning, including an
`awaiting_model_consent` scan, and `CreditsDetectionStartResult.queue_id` is that row's
integer primary key. The scheduler dispatches only `queued` rows. Existing queue cancellation
therefore works before consent as well as during execution; canceling acquisition also
requests cancellation of the scan-owned model acquisition. A reconnect returns the same scan
and queue ID while the row exists and does not enqueue a duplicate.

The clip request boundary must equal the detected boundary or the request's finite edited
acceptance boundary and remain in the authoritative runtime. The server resolves the media
path. Output is H.264/AAC MP4, at most 12 seconds (up to 6 seconds on each side, clipped at
file bounds), at most 16 MiB, `Content-Type: video/mp4`, `Content-Disposition: inline;
filename="credits-preview.mp4"`, and `Cache-Control: no-store`. Temporary output is
ownership-scoped and removed after response completion, cancellation, or failure. Responses
and errors expose no local path. Clip failure does not invalidate the detector result.

Missing Qwen can be acquired only after `CreditsModelConsent` has disclosed backend,
artifacts, total expected bytes, actual platform/device availability, and license fields and
the user sends `decision="acquire"`. Start, status, or acceptance never implicitly invokes
acquisition. Decline/continue, cancellation, failed verification, and retry retain truthful
existing model storage states.

### Finding and acceptance contract

Successful candidate completion creates exactly one immutable `ScanFindingRow` for the best
candidate: category `dispensable_scenes`, subcategory `dispensable`, label/details and
`mcf_comment` `End Credits`, source `visual`, channel `video`, action `skip`, selected true,
start at detected boundary, end at authoritative physical file end, detector confidence,
and severity from the current configured `dispensable` default. Acceptance does not rewrite
it. `CreditsAcceptanceResult.membership` is `{scan_id, role: "video"}`; its filter is a
complete selected `ReviewFilterInput` linked to that immutable finding. An edited boundary
changes only the returned review filter START and accepted reference; END remains the
authoritative file end.

Import validates and attaches the reference in its transaction. Existing-review acceptance
returns draft data only; normal Save validates the reference again and persists membership,
snapshot, and provenance atomically. Dismissal, import cancellation, Discard, provenance
removal, or review deletion leaves the normal scan/history entry intact. Normal scan
reference/deletion guards apply after a saved membership exists.

`POST /api/credits-detections/{scan_id}/accept` selects behavior from the request context.
Import context has no review ID and validates only the target physical version. Review
context validates the named persisted review, its revision, and physical version. Neither
context writes a review or provenance row; both return the same server-derived membership,
filter snapshot, and accepted reference for later import or normal Save. Reaccepting a
candidate replaces the draft's prior accepted reference and End Credits snapshot rather
than accumulating duplicate accepted credits findings.

### Revision and export matrix

One successful save increments each affected revision at most once. A normalized no-op
increments none. Existing MCF revision rules remain authoritative, with these additions:

| Persisted change | `revision` | `content_revision` | `edl_content_revision` | `mcf_content_revision` |
| --- | --- | --- | --- | --- |
| Synchronization START, END, or mode only | yes | yes | no | no |
| Cue START or END produced by retiming | yes | yes | yes | yes |
| Canonical MCF START or END only | yes | yes | no | yes |
| Accepted detection provenance only | yes | yes | no | no |
| Detector method/confidence/completion on source scan | no | no | no | no |
| Credits scan membership/order/role | yes | yes | yes | yes |
| Accepted End Credits snapshot creation/removal/order/selection/times/classification | yes | yes | yes | yes |
| Accepted snapshot confidence/details only | yes | yes | no | no |

Synchronization state is review content because it changes future draft transformations,
but is not itself emitted by EDL or MCF. The retime operation changes no revision because it
does not persist. EDL preflight ignores canonical MCF material completeness. MCF preflight
requires valid canonical before-credits START/END in both modes and never substitutes
synchronization END. A save containing both timing and cue/document changes takes the union
of effects in the table.

### Errors, security, and trust boundaries

All routes follow the existing authentication middleware. Every POST/PUT/PATCH/DELETE route
requires the existing same-origin session and CSRF token; GET status/clip routes require the
session and remain non-mutating. OpenAPI declares `ReviewError` for structured failures and
the global path-safe 422 response. Exact new safe codes are:

| Status | Code | Meaning |
| --- | --- | --- |
| 400 | `invalid_credits_target` | malformed or cross-context target selector |
| 404 | `review_not_found`, `scan_not_found`, `credits_candidate_not_found` | resource absent without path disclosure |
| 409 | `stale_review_revision` | existing optimistic guard |
| 409 | `credits_detection_not_ready` | scan not complete or no acceptable candidate |
| 409 | `credits_detection_cancelled` | terminal cancelled scan |
| 409 | `credits_model_consent_required` | disclosed artifact awaits a decision |
| 409 | `credits_target_mismatch` | scan physical version differs from context |
| 409 | `media_runtime_unavailable` | no authoritative finite positive runtime |
| 413 | `credits_clip_too_large` | bounded clip cannot meet size limit |
| 422 | `invalid_source_mcf_bounds` | synchronization denominator is unusable/non-original |
| 422 | `invalid_synchronization_bounds` | source/proposed target timing invalid |
| 422 | `mapped_cue_out_of_bounds` | mapped filter invalid or outside runtime |
| 422 | `invalid_credits_boundary` | edited boundary invalid/outside runtime |
| 500 | `credits_detection_failed`, `credits_clip_failed` | safe detector/process failure |
| 503 | `credits_media_unavailable`, `credits_model_acquisition_failed` | trusted media/model unavailable |

Validation errors identify only safe `document_fields` and `filter_ids`; detector errors may
add `scan_id` and `candidate_id` to `ReviewErrorDetail`, both nullable. No browser value is
authoritative for local path, runtime, video hash, physical version, source finding,
detected timestamp, confidence, method, model artifact, or detector result. Catalog targets
resolve through configured Jellyfin/Radarr/Sonarr mappings; manual item IDs resolve through
the repository, while a manual `video_path` is accepted only by the existing scanner-path
allowlist/resolver. Every status, accept, and clip operation rechecks the resolved physical
version against the scan.

### Frontend unions and defaults

`web/src/api.ts` mirrors every enum and DTO above. `CreditsDetectionResult` is a TypeScript
discriminated union: `{outcome: "candidate"; candidates: [CreditsCandidate,
...CreditsCandidate[]]; best_candidate_id: string}` or `{outcome: "no_candidate";
candidates: []; best_candidate_id: null}`. `CreditsAcceptanceContext` is the exact `kind`
union above. `Scan.credits_detection` is narrowed by `scan_mode` in helper code even though
the wire interface retains nullable compatibility.

`ReviewDraft` adds `synchronization`, `acceptedCredits`, and the same fields in
`baselineState`; dirty/discard/save replacement includes both. Import-only form state uses
the nullable `McfImportTimingDraft`; API calls construct strict `McfImportOptions` only after
validation. Before credits is the UI default and never infers END. File end may prefill a
trusted API duration but remains editable. Low confidence is always rendered as text, and
completion never invokes acceptance.

### Baseline evidence

Baseline was recorded on September 20, 2026 with a clean worktree. No database, service,
media, sidecar, cache, or model was accessed or changed.

- `uv run ruff check scanner/models.py scanner/db.py scanner/mcf.py scanner/api.py
   scanner/repository.py scanner/worker.py scanner/process_control.py`: passed.
- Focused existing MCF codec/import/recovery, saved-review, queue, and model-acquisition
   pytest files discovered under `tests/`: 177 passed in 1.82 seconds.
- `cd web && pnpm test -- --run src/api.test.ts src/review-draft.test.ts`: 2 files and 25
   tests passed.
- `git diff --check`: passed before the first edit.
- Baseline schema has no review synchronization, accepted-credits, credits-run, or scan-mode
   authority; OpenAPI and TypeScript have no timing/detector contracts.

## Handoff

Status: DONE
Last completed substep: Added fixture-backed affine and cross-layer contract tests and ran
the final focused regression set.
Files changed: `docs/mcf-timing-synchronization-tasks/00-contract-baseline.md`,
`tests/fixtures/mts/contract-v1.json`, `tests/test_mts_contract.py`.
Schema/API/contract changes: Contract only; exact timing, detector, persistence, route,
revision, error, security, and frontend shapes are recorded above.
Verification commands and results: Ruff passed; focused pytest 310 passed in 4.30 seconds;
direct focused Vitest 2 files/25 tests passed; `git diff --check` passed. The package-level
`pnpm test -- --run ...` ignored its file arguments, ran 117 tests, and hit one unrelated
intermittent `App.test.tsx` failure; the requested files passed directly.
Manual or artifact evidence: Versioned `mts-contract-v1` JSON fixture covers affine vectors,
wire shapes, routes, queue transitions, confidence bands, and clip bounds.
Known failures or risks: MTS-04 must justify the frozen detector evidence/confidence policy
on its legal corpus or stop BLOCKED; no accuracy claim exists yet.
Acceptance criteria still unmet: None.
Next exact action: Implement MTS-01 timing persistence/models and pure affine authority, or
begin the independent MTS-04 legal evaluation corpus.
Next eligible task: MTS-01 and MTS-04.
Decisions or deviations from the parent plan: None.
User approval required: None unless a new product decision is discovered.
