# MCF-02 - Build the strict MCF codec

Status: **DONE**

Dependencies: MCF-00. Deliverable: pure bounded MCF 1.0/1.1 parser, deterministic 1.1
exporter, canonical registry, and exhaustive fixtures.

## Scope

1. Add `scanner/mcf.py` (or the exact MCF-00 module) with no database, network, catalog,
   or filesystem side effects.
2. Encode the complete category token/display/family registry once and expose safe lookup
   helpers consumed by validation and later UI-contract generation.
3. Parse bytes as strict UTF-8 without BOM and enforce exact header, section order,
   metadata grammar, material bounds, WebVTT timestamps, cue timing, one-or-more payload
   lines, category/severity/channel tokens, and comment restrictions.
4. Accept exactly source versions 1.0.0 and 1.1.0. Parse 1.0 without metadata and 1.1 with
   optional metadata. Reject generic WebVTT features outside the MCF subset.
5. Enforce MCF-00 size, line, and payload limits before unbounded allocation. Return safe,
   stable, line-aware parse errors.
6. Export deterministic MCF 1.1.0 bytes: UTF-8 without BOM, exact field order and newline
   policy, required material bounds, exact-equal-range cue grouping, deterministic cue and
   payload ordering, and no EDL normalization.
7. Add semantic round-trip tests and exact golden-byte fixtures covering all metadata,
   categories, severities, channels, omitted channel, comments, shared ranges, precision,
   supported versions, and the full invalid matrix in the parent plan.
8. Verify fixture/category lists against the published 1.0.0 and 1.1.0 specifications and
   record the access date in tests/docs without copying unrelated site content.

## Out of scope

- Database rows, import orchestration, review defaults, HTTP routes, or sidecar writes.
- Best-effort repair or sanitization of invalid MCF.
- MCF versions other than 1.0.0/1.1.0 or export other than 1.1.0.

## Verification

- `uv run ruff check scanner/mcf.py tests/test_mcf.py`.
- Focused `tests/test_mcf.py`, including golden bytes and all-token parameterization.
- Existing `tests/test_edl.py` to prove codec separation.
- `git diff --check`.

## Acceptance criteria

- [x] Valid 1.0/1.1 documents parse into one exact, ordered representation.
- [x] Deterministic output is strict 1.1.0 and never applies EDL range rules.
- [x] Every canonical token is covered and invalid case/token syntax fails safely.
- [x] Limits and line errors are deterministic and content/path safe.
- [x] The module has no persistence, network, or filesystem side effects.

## Handoff

Status: DONE
Last completed substep: Added fixture-backed strict parsing/export coverage, audited the
codec against MCF-00, and reran the codec, contract, and EDL separation suites.
Files changed: `scanner/mcf.py`, `tests/test_mcf.py`,
`tests/fixtures/mcf/valid-1.0.mcf`, `tests/fixtures/mcf/valid-1.1.mcf`,
`tests/fixtures/mcf/exported-1.1.mcf`, this task record, and the task-pack README.
Schema/API/contract changes: Added pure `McfParseError`, `McfCueEntry`, and
`McfParseResult` codec types; safe registry lookup helpers; bounded strict 1.0/1.1 byte
parsing; and deterministic canonical 1.1 byte export. No persistence or HTTP contract
changed.
Verification commands and results: `uv run pytest -q tests/test_mcf.py` passed 118 tests;
`uv run ruff check scanner/mcf.py tests/test_mcf.py` passed; combined `uv run pytest -q
tests/test_mcf.py tests/test_mcf_contract.py tests/test_edl.py` passed 129 tests. VS Code
reported no diagnostics in the new source or test module.
Manual or artifact evidence: Source and canonical-export golden fixtures are checked in.
The 1.0.0 and 1.1.0 grammar, examples, and shared 97-token registry were rechecked against
the published specification pages on September 17, 2026.
Known failures or risks: None recorded.
Acceptance criteria still unmet: None.
Next exact action: MCF-05 may consume the parser after MCF-04 is complete; MCF-07 may
consume the exporter after MCF-04 and MCF-06 are complete.
Next eligible task: MCF-05 after MCF-04; MCF-07 after MCF-04 and MCF-06.
Decisions or deviations from the parent plan: The registry remains defined once in
`scanner/models.py` by MCF-01; the codec exposes lookup helpers over that authority rather
than duplicating the 97-token table.
User approval required: None.
