# NSR-05 — Build the EDL comment codec and parser

Status: **DONE**

Dependencies: NSR-00. Deliverable: pure contributor-aware EDL normalization/export and a
bounded Simple EDL parser with Editarr comment metadata.

Read the task-pack README, parent plan, NSR-00, and its handoff first.

## Why this task exists

Import and saved-review export must share one deterministic codec. This task is isolated
from database/API work so parsing and normalization can be verified as pure functions.

## Scope

1. Refactor `scanner/edl.py` without changing established playback semantics:
   - clamp negative starts;
   - apply the established one-second fallback for invalid/missing ends where export
     inputs permit it;
   - merge adjacent/overlapping same-action ranges;
   - subtract skip coverage from mute ranges;
   - sort deterministic output; action `3` skip and `1` mute.
2. Preserve contributor metadata through normalization. Each normalized output range
   carries the source review filters that contributed to that final piece. A mute split by
   skip coverage contributes its comments to each surviving mute piece. Do not deduplicate
   two distinct filters merely because their comment text matches.
3. Emit exactly:
   - first line `## Editarr Simple EDL`;
   - immediately before each action row, one `## category: label` line per contributing
     filter in deterministic order;
   - then the established three-decimal action row.
4. Sanitize comment fields so embedded controls/newlines cannot create action rows or extra
   comments. Preserve useful Unicode. Define deterministic fallback category/label:
   `other: Imported mute` and `other: Imported skip` for supported rows without recognized
   metadata.
5. Add a pure UTF-8 parser/result model using NSR-00 limits. It must:
   - accept blank lines, the header, and `##` comments;
   - recognize an Editarr metadata comment by splitting the content after `##` on the first
     colon, trimming category/label, and rejecting an empty side as metadata without making
     the whole EDL invalid;
   - associate contiguous recognized comments with the next supported action row;
   - accept only finite decimal seconds and exactly three whitespace-separated row fields;
   - import action `1`/`3`, count all other integer actions as ignored, and return one
     aggregate warning indicator/count;
   - reject malformed supported rows with line-aware errors;
   - reject content/line/row limits and invalid UTF-8 safely;
   - report the frozen no-supported-actions error after ignored rows are removed.
6. For one supported row with several comments, return one imported filter per comment,
   each using that normalized row's time range. Use one generic filter when no recognized
   comment exists.
7. Keep the module free of database, FastAPI, and filesystem side effects. Reading upload
   bytes/path content belongs to NSR-07.

## Clarifications

- Alternate clock/frame/comma formats are unsupported, even though Kodi may support more.
- Generic `##` comments without `category: label` are allowed and ignored as metadata.
- Unsupported actions do not fail an otherwise valid import. A malformed three-field row
  or non-integer action remains a format error.
- Accepted round trips preserve normalized playback ranges and labels, not original raw
  overlapping boundaries.

## Verification

- Ruff `scanner/edl.py` and `tests/test_edl.py`.
- Expand `tests/test_edl.py` for contributor propagation, stable comment order, duplicate
  comments from distinct filters, mute splitting, comment sanitization, import variants,
  ignored-action warning, limits, UTF-8, line numbers, no-supported-actions, and round trip.
- Rerun existing EDL normalization tests unchanged where possible.
- `git diff --check`.

## Acceptance criteria

- [x] Existing skip/mute normalization results are unchanged.
- [x] Every normalized row emits one safe comment per contributing filter.
- [x] Parser accepts exactly the approved subset and rejects malformed/oversized input.
- [x] Unsupported actions yield one aggregate warning and supported rows still import.
- [x] Commented and uncommented rows create the approved filter metadata.
- [x] Round-trip tests document accepted boundary loss.
- [x] Module remains pure and shared definition of done is satisfied.

## Verifier instructions

Inspect contributor tracking through merge and subtraction, then add/rerun a case where
one mute filter is split by one skip and two skip filters merge with identical comments.
Confirm controls cannot inject lines and that no database/filesystem import appears in
`edl.py`. Rerun all EDL unit tests before NSR-06/07.

## Handoff requirements

Record parser/export function signatures, result/error types, exact limits, contributor
ordering, comment escaping, warning semantics, tests/counts, and any changed legacy helper.
Name NSR-06 and NSR-07 as next eligible when their other prerequisites are DONE.

## Handoff

Status: DONE
Last completed substep: Implemented contributor-aware normalization/export and the bounded
  pure Simple EDL parser.
Files changed: scanner/edl.py, tests/test_edl.py, tests/test_editarr_integration.py.
Schema/API/contract changes: Added `ParsedEdlFilter`, `ParsedEdl`, `EdlParseError`, and
  `parse_edl_content(content: bytes, *, duration_seconds: float | None = None)`. Export now
  emits the Editarr header, safe category/label comments, and deterministic three-decimal
  action rows while preserving contributors through merges and mute subtraction. Parser
  enforces UTF-8/BOM, line/action/content limits, decimal row syntax, metadata boundaries,
  duration checks, ignored-action counts, and the no-supported-actions error.
Verification commands and results: `uv run pytest -q tests/test_edl.py
  tests/test_editarr_integration.py` passed 62 tests; Ruff passed for changed codec/tests;
  `git diff --check` passed. Existing framework deprecation warnings remain.
Manual or artifact evidence: Existing normalization fixtures remain green; integration EDL
  expectations now cover required metadata comments and parser tests cover BOM/CRLF, Unicode,
  fallback metadata, malformed rows, duration and line limits, and ignored actions.
Known failures or risks: Parser result categories use the shared `ContentCategory` enum;
  NSR-07 owns upload/path reading and API integration.
Acceptance criteria still unmet: None within the pure codec scope.
Next exact action: Implement NSR-06 review export/provenance, after its NSR-04 and NSR-05
  prerequisites are complete.
Next eligible task: NSR-06 and NSR-07 are now eligible.
Decisions or deviations from the parent plan: None.
User approval required: None.

