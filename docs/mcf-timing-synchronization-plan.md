# MCF timing synchronization and credits detection plan

Status: **IMPLEMENTED**

Plan date: **September 20, 2026**

Implementation completed: **September 20, 2026**

Task tracker: [MCF timing synchronization coding tasks](mcf-timing-synchronization-tasks/README.md)

This plan extends the implemented [Movie Content Filter support plan](mcf-support-plan.md),
the [Feature design specification](feature-design-spec.md), and the current saved-review
workflow. It adds affine MCF cue synchronization, a repeatable saved-review retiming action,
and assisted closing-credits detection without weakening immutable import provenance or the
strict MCF taxonomy.

This document records approved product behavior. It does not itself change code, schemas,
databases, media, sidecars, model artifacts, or running services.

## 1. Upstream behavior and local interpretation

The canonical MovieContentFilter website stores annotations as positions normalized to the
source material's `START` and `END`. When producing a playback filter for another physical
copy, it maps every cue endpoint from the source interval into the requested target
interval. For source bounds `S_start`, `S_end`, target bounds `T_start`, `T_end`, and cue
endpoint `t`, the mapping is:

```text
T_start + ((t - S_start) / (S_end - S_start)) * (T_end - T_start)
```

The mapping changes timing only. It does not select findings, change categories, merge cue
ranges, infer actions, or clamp cue endpoints to the material bounds. The canonical site
uses target synchronization values for playback formats and keeps MCF as a portable
description whose `START` and `END` identify the actual film material.

Editarr currently imports absolute MCF cue seconds directly into immutable scan findings and
editable review snapshots. This extension applies the same affine behavior while preserving
that ownership split:

- the imported scan retains the source MCF bounds and cue times exactly;
- the initial saved review receives cue times mapped to the selected target interval;
- later retiming changes only the saved-review draft and never rewrites source findings;
- accepted credits detection creates its own normal scan and immutable finding.

## 2. Required outcomes

Implementation must deliver all of the following:

1. MCF import accepts editable target `START`, target `END`, and an END interpretation of
   `before_credits` or `file_end` for every supported source and transport.
2. Import maps every valid cue endpoint with the affine formula before creating the initial
   review snapshots while retaining untouched source bounds and cues on the imported scan.
3. Every saved review offers an Adjust Cue Times action that maps imported or unverified
   cues, including disabled ones, from the current synchronization interval to a new interval.
   Verified local scan cues and manual cues keep their times.
4. Repeated adjustment uses the current review interval and current draft cue times. It does
   not regenerate from the imported scan or discard intervening manual edits.
   A verified local credits boundary and its snapshot keep their times; the detector's
   original detected boundary remains immutable.
5. Synchronization END and canonical MCF material END are independent when File end mode is
   used. MCF export still requires a before-credits material END.
6. Every saved review can request assisted credits detection from the import dialog, Adjust
   Cue Times dialog, or MCF metadata editor.
7. Credits detection runs as a cancellable background scan with visible progress and creates
   a normal scan-history entry.
8. A completed detection returns the best candidate, confidence, supporting method data, and
   a short video clip around the proposed boundary. Low confidence is a warning, not a hidden
   result.
9. The user must explicitly accept or edit a detected timestamp before any review draft or
   import option changes.
10. An accepted result creates a selected video Skip finding from the credits boundary to the
    effective physical file end, snapshots it into the invoking review, and retains accepted
    detection provenance on that review.
11. Credits findings use the canonical `dispensable_scenes/dispensable` pair, description and
    MCF comment `End Credits`, video channel, detector confidence, and the configured
    `dispensable` severity default.
12. Authentication, CSRF, trusted target resolution, physical-version isolation, dirty-draft
    guards, optimistic review revisions, independent EDL/MCF staleness, accessibility,
    responsive behavior, cancellation, and model-acquisition safety remain intact.

## 3. Confirmed product decisions

These decisions were confirmed on September 20, 2026 and must not be silently reopened.

### D1. Target bounds on MCF import

Target bounds are visible and editable in the MCF import flow. Target START defaults to
`00:00:00.000`. The default END interpretation is Before credits. Its END field starts blank
because physical duration is not evidence of the credits boundary; the user must enter a
value or accept a detector suggestion. File end mode prefills the probed media duration but
allows correction.

Target bounds are always checked against the authoritative selected physical file. A target
interval must be finite, non-negative, ordered, and contained by the trusted runtime when a
runtime is available.

### D2. Source provenance and invalid source bounds

The imported MCF scan is immutable source evidence. Its document retains the original
`START`/`END`, and its findings retain original cue times. Only the new review snapshots are
retimed.

Usable source `START` and `END` are mandatory for synchronization. Missing, malformed,
recovered, equal, or reversed source bounds cannot be bypassed by Continue Anyway because an
affine mapping would have no trustworthy denominator. Other existing recoverable import
issues keep their current confirmation flow.

### D3. Affine mapping and outlying cues

Every start and end endpoint is mapped independently with full internal floating-point
precision. Formatting rounds only at existing display/export boundaries. Cues outside the
declared source `START`/`END` are mapped without clamping, matching upstream behavior, but
the mapped ranges must remain valid and fit the selected physical file.

Multiple payloads sharing one cue continue to produce separate findings with equal mapped
ranges and stable ordering. Retiming does not merge, split, reorder, classify, select, or
change actions.

### D4. Review-owned synchronization basis

Every saved review persists a synchronization START, synchronization END, and END mode. This
state is distinct from scan provenance and from the review's canonical MCF document fields.

Adjust Cue Times is available on every saved review, not only imported-MCF reviews. It maps
current filters whose source timing provenance is unknown, whether selected or disabled.
Verified local scan filters and staged manual filters retain their times. Applying the action
changes the draft only; the existing Save/Discard workflow remains authoritative. Saving uses
the existing optimistic revision guard.

### D5. Before-credits and file-end semantics

In Before credits mode, synchronization END and canonical MCF material END are the same
accepted value. In File end mode, synchronization END is the physical runtime endpoint while
canonical MCF material END is stored separately and may be absent until entered or detected.

MCF preflight and export require a valid before-credits material END even when the current
synchronization mode is File end. EDL export uses the review cue times and does not require
MCF document completeness. Changing cue times makes both EDL and MCF exports outdated;
changing only MCF document bounds follows the existing MCF-only revision behavior.

### D6. Credits detection interaction

Detection is explicit and on demand. It runs in the background with progress and
cancellation, returns its best candidate even at low confidence, and labels weak or
conflicting evidence clearly. It never changes an import or review merely because a job
completed.

The result includes a bounded short video clip spanning the proposed transition. The user
may scrub the clip, accept the timestamp, edit it, or dismiss it. Acceptance updates the
current import/review draft and records detector method, confidence, detection time, and the
source detection scan.

### D7. Hybrid detector direction

Detection uses the best locally available evidence in stages:

1. inspect container chapter metadata for credible credits markers;
2. analyze the tail of the selected video using scene boundaries and visual/text patterns;
3. optionally use an installed Qwen vision-language model to assess ambiguous candidates;
4. offer Qwen acquisition only after disclosing capability and download requirements and
   receiving explicit confirmation.

The implementation must work without Qwen on unsupported platforms. It must not claim that
black frames, silence, or a final scene alone proves the credits boundary. Exact heuristics,
confidence calibration, tail windows, frame sampling, and Qwen prompts require an evaluated
fixture/corpus baseline before production thresholds are frozen.

### D8. Credits scan and finding

Credits detection creates a normal generated video scan visible in scan history. It does not
extend the MCF registry. Its accepted finding is canonical:

- family `dispensable_scenes`;
- subcategory `dispensable`;
- label/details sufficient to display description `End Credits`;
- MCF comment `End Credits` so the meaning survives MCF export;
- source `visual`, channel `video`, action `skip`, selected `true`;
- start at the accepted credits boundary and end at the effective physical file end;
- confidence from the detector and severity from the configured `dispensable` default.

Scan completion persists the detector's candidate range and confidence as immutable source
evidence. Acceptance from import or an existing review automatically adds the detection scan
with a video-capable membership and snapshots the finding into that review. If the user edits
the proposed timestamp before acceptance, the scan finding remains at the detected boundary
while the review snapshot uses the corrected boundary; accepted provenance retains both
values. Explicit acceptance wins over selection defaults for this finding; defaults supply
severity only. The scan remains in history if the user later cancels an import or discards
review changes.

## 4. Domain and persistence direction

The contract task must freeze exact Python, JSON, database, and TypeScript names before
schema implementation. The model must nevertheless preserve these distinct authorities:

- immutable imported source bounds and cues on `ScanMcfDocumentRow`/`ScanFindingRow`;
- editable canonical MCF metadata on `ReviewMcfDocumentRow`;
- editable review synchronization interval and END mode owned by `SavedReviewRow` or a
  one-to-one review timing row;
- accepted detector provenance linked to the review and source detection scan;
- detector configuration/result provenance on the normal credits scan;
- ordinary review filter snapshots linked to immutable findings.

The complete revision matrix must distinguish output-affecting cue/document changes from
diagnostic provenance. Detection method/confidence alone must not rewrite sidecars, while an
accepted boundary or credits finding must affect the relevant revisions.

This is a v1 fresh-schema change. The user explicitly approved skipping preservation
migrations and recreating a checkout-owned development database. Only the persistence task
may perform that reset, after resolving and printing the exact database target, proving it
is disposable development data, and proving no process is using it. Installed/user data,
paths outside the checkout, media, and sidecars are never covered by this approval.

## 5. Backend behavior

### 5.1 Pure retiming

Add one pure backend authority for interval validation and affine mapping. Import and review
retiming must call the same implementation. Tests cover offset-only mapping, stretching,
compression, repeated mapping, shared ranges, outlying cues, floating-point boundaries, and
invalid/overflow results.

The parser must validate source MCF syntax independently from target duration. Existing
target-duration rejection cannot run against unadjusted source times for a different cut.
After parsing, map into the target interval and validate mapped review ranges against the
selected physical runtime. Raw scan evidence remains valid relative to its own source MCF
bounds and is not rejected merely because that source cut is longer than the target file.

### 5.2 Import service

Upload and scanner-path imports receive the same target timing options and feed one service.
The service resolves the physical target and runtime, parses the source, validates a usable
source interval, computes mapped review entries, then atomically creates the imported scan,
source findings, review, memberships, review timing state, canonical document copy, and
review snapshots. If an accepted credits scan is supplied, its membership and snapshot join
the same review transaction without rewriting that scan.

Continue Anyway must replay the identical source, target timing, END mode, and accepted
credits selection. A failure leaves no partial import-owned aggregate.

### 5.3 Review retiming

Follow the existing Apply Filter Defaults pattern: a protected, non-persistent operation
accepts the complete current draft timing state and filters, returns mapped filters and new
timing/document state, and lets the normal review save persist them. This keeps server-side
math authoritative while preserving unsaved manual filters.

The operation rejects stale/missing timing bases, invalid new intervals, invalid resulting
ranges, physical-runtime overflow, and malformed draft filters with safe field-level errors.
It preserves IDs, provenance, ordering, classification, severity, channel, comments,
descriptions, confidence, action, and selection.

### 5.4 Credits detection lifecycle

Use the existing scan queue, process ownership, cancellation, progress, model capability,
artifact acquisition, video hashing, scene cache, and runtime workspace patterns. Credits
detection is a dedicated scan mode, not a safety-model alias. It may reuse cached scene data
and detector-versioned results for the same video hash, but cache identity must include all
algorithm/model versions that affect the candidate.

The detector emits structured candidate/evidence data before finding creation. Acceptance,
not mere completion, determines the exact finding boundary used by a review. Clip generation
uses the trusted resolved media path, bounded duration/size, and a non-path-bearing protected
response.

## 6. Frontend behavior

### 6.1 Import

The MCF branch of the shared Import dialog adds timestamp controls and an END-mode segmented
control. Before credits is initially selected and blank; File end shows the probed duration
as an editable value. Validation uses `HH:MM:SS.MMM`, describes the selected meaning, and
does not expose scanner paths.

Detect Credits starts or reconnects to a credits scan for the selected physical file, shows
progress/cancel state, handles optional Qwen acquisition consent, and presents the candidate
clip. Acceptance fills Before-credits END and carries the scan reference into import. The
existing recoverable-issues dialog retains every timing/detection option on retry.

### 6.2 Saved reviews

Every review exposes Adjust Cue Times near the MCF timing metadata. The dialog shows the
current interval and mode, accepts a new interval/mode, explains which cues are eligible,
and applies the returned transformation to the draft.
Save and Discard retain their current meanings.

The MCF metadata editor also offers Detect Credits. Accepting a result updates canonical
material END, adds the detection scan/membership/finding snapshot, and displays accepted
method, confidence, and time. File-end synchronization remains visibly distinct from the
before-credits value required by MCF export.

### 6.3 Accessibility and responsive behavior

All mode controls, progress, warnings, confidence, clip controls, validation, confirmation,
and cancellation must be keyboard accessible and screen-reader named. Low confidence cannot
be conveyed by color alone. Dialogs must fit at 320px without page overflow, trap/restore
focus through nested detection and model-consent states, and prevent incoherent overlapping
controls or text.

## 7. API and error direction

The contract task owns exact route and DTO spellings. The API must include:

- target timing and END mode on both MCF import transports;
- review timing/provenance on review read/write contracts;
- a non-persistent review retiming operation;
- queue/start, status, cancellation, result, and clip access for credits scans;
- an explicit accepted credits scan/candidate reference on import/review operations;
- structured safe errors for invalid source bounds, target bounds, runtime overflow,
  unavailable media, stale review, unavailable candidate, canceled detection, model
  consent/acquisition failure, and clip failure.

All mutating routes remain authenticated and CSRF protected. Browser input never supplies an
authoritative media path, runtime, hash, scan finding, confidence, or model result.

## 8. Verification strategy

Backend tests must cover:

- exact affine vectors and inverse/repeated behavior;
- immutable source scan versus mapped review snapshots;
- upload/path and Jellyfin/Radarr/Sonarr/Other Files parity;
- strict versus recoverable source-bound failures;
- target mode/default/validation and runtime overflow;
- review retiming of imported and unverified filters while preserving verified local and
  manual filters;
- revision/staleness matrices and MCF before-credits preflight;
- credits candidate scoring with deterministic fixtures and low-confidence warnings;
- Qwen available, unavailable, declined, acquired, canceled, and failed paths;
- normal scan history, cancellation, restart safety, cache versioning, accepted provenance,
  canonical finding fields, membership/snapshot integration, and rollback;
- bounded clip generation without path disclosure;
- live OpenAPI and authentication/CSRF policy.

Frontend tests must cover import defaults, both END modes, recovery retry preservation,
retiming only eligible cues, repeated adjustment, dirty/save/discard behavior, detector progress and
cancel, Qwen consent, low-confidence warnings, clip verification, acceptance/edit/dismiss,
metadata display, scan-history visibility, export preflight, keyboard behavior, mobile
layout, and non-color-only status.

Use temporary media and databases for automation. Detector evaluation fixtures must be
redistributable or locally generated and must record expected boundaries and tolerances.
Real-model acceptance is separate from deterministic unit/integration tests.

## 9. Rollout and compatibility

- No migration is added solely to preserve v1 development data.
- Existing saved-review, MCF import/export, EDL import/export, default evaluation, scan
  queue, cancellation, restart, model capability, and native/release contracts remain
  regression surfaces.
- Older clients cannot write the new complete review timing contract; exact compatibility
  behavior is frozen in the contract task rather than guessed during implementation.
- Qwen remains optional. Cross-platform chapter/visual analysis must remain useful without
  it, and capability responses must state the actual platform behavior.
- No task downloads a model, wipes data, starts/stops a user service, or touches real media
  or sidecars without the task's explicit scope and safety checks.

## 10. Definition of done

- Import maps review cues correctly while preserving exact source evidence.
- Every review can repeatedly retime cues with unknown provenance from its current draft basis.
- Before-credits and file-end semantics remain distinct through save, reopen, and export.
- MCF export cannot silently emit physical file duration as canonical before-credits END.
- Credits detection is cancellable, reviewable, confidence-bearing, and useful without
  Qwen; Qwen acquisition always requires consent.
- Accepted detection creates the approved canonical scan finding and review snapshot.
- Detection provenance, revision effects, and export behavior are reproducible.
- Backend, frontend, browser, OpenAPI, security, packaging, and focused real-model evidence
  pass the task pack's gates.
- Documentation and task handoffs match implemented behavior, with no unresolved product
  decision hidden in code.
