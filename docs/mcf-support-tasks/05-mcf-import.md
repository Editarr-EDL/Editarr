# MCF-05 - Import MCF into saved reviews

Status: **DONE**

Dependencies: MCF-02 and MCF-04. Deliverable: protected upload/path MCF import that creates
one imported scan and one saved review atomically for every supported source.

## Scope

1. Add the exact MCF-00 upload and scanner-path endpoints. Reuse source-aware target
   resolution, same-origin/CSRF policy, path mappings, physical version checks, safe
   basename handling, media readability, and trusted duration probing from EDL import.
2. Require `.mcf` for host paths/uploads. Treat MIME as a hint only and feed both
   transports into the same bounded pure parser and service operation.
3. Accept only parsed 1.0.0/1.1.0. Validate all content before opening the creation
   transaction. Map parser failures to safe structured line-aware errors.
4. In one transaction create:
   - one complete, non-queued `origin=imported` scan with safe source basename/time and
     imported MCF version;
   - one immutable scan-source metadata record;
    - one source finding per payload line, storing the exact token as `subcategory`, its
       registry family as `category`, and the approved severity/channel/action/text fields;
   - one named saved review, one role-aware membership, editable metadata copy, and one
     isolated snapshot per finding; and
   - initial severity/channel defaults plus unified selection evaluation.
5. Derive scan audio/video flags and membership role from all channels. Omitted channel
   has MCF `both` semantics. Multiple payload lines sharing a cue remain separate findings
   with equal ranges and stable order.
6. Compare imported work metadata with trusted target metadata without changing target
   physical-version identity. Preserve imported values and return safe field-level notices
   for comparable mismatches; 1.0 missing metadata is seeded from target data.
7. Import changes neither active EDL nor MCF export state. Any parse, target, validation,
   evaluator, or commit failure leaves no scan, metadata, finding, review, or membership.
8. Add parser-to-service mapping, repository, API/OpenAPI, auth/CSRF, source adapter,
   transaction rollback, mismatch, all-channel, 1.0/1.1, and safe-error tests for Jellyfin,
   Radarr, Sonarr, and Other Files.

## Out of scope

- Import dialog/UI (MCF-10).
- Sidecar export or badges.
- Creating a new physical media version from MCF metadata.

## Verification

- Ruff changed Python files.
- Focused MCF codec/import/review/default/API/auth/OpenAPI tests.
- Existing EDL import tests for shared target/safety regression.
- Temporary media/database rollback tests; no live sources.
- `git diff --check`.

## Acceptance criteria

- [x] Both transports and every source create the same authoritative aggregate.
- [x] One file always creates one correctly typed imported scan, not per-channel scans.
- [x] Exact MCF and immutable source metadata survive; review copies remain editable.
- [x] Mismatch notices do not reject or retarget a valid selected physical version.
- [x] Unsupported/invalid/oversized documents create nothing and leak no unsafe data.
- [x] Import leaves both active sidecar formats unchanged.

## Handoff

Status: DONE
Last completed substep: Added strict basename/shared-range checks, audited the complete
import boundary, and ran the full backend suite.
Files changed: `scanner/models.py`, `scanner/repository.py`, `scanner/api.py`,
`scanner/auth_policy.py`, `tests/test_mcf_import.py`, `tests/test_scan_naming.py`,
`tests/test_saved_review_schema.py`, this task record, and the task-pack README.
Schema/API/contract changes: Added `McfImportOptions`, `McfPathImportRequest`,
`McfMetadataNotice`, and `McfImportResult`; added protected `POST
/api/mcf-imports/path` and `POST /api/mcf-imports/upload`; and added one atomic repository
operation that maps a parsed MCF document to immutable scan evidence plus an isolated saved
review. No schema migration or export-state change was added.
Verification commands and results: Dedicated repository mapping/default/rollback suite
passed 10 tests. Focused codec/import/review/EDL/API/auth regression suite passed 430 tests
without warnings. Final `uv run ruff check scanner tests scripts native_build` passed;
final `uv run pytest -q` passed 1,034 tests in 29.28 seconds without warnings. VS Code
reported no diagnostics in changed source/test files.
Manual or artifact evidence: Checked-in 1.0.0/1.1.0 fixtures drive exact source metadata,
payload order, shared-range, and version tests. Authenticated path/upload tests cover manual,
Jellyfin, Radarr, and Sonarr targets, selected physical versions, MIME-hint tolerance,
safe malformed/version/size failures, and field-level metadata mismatch notices. Injected
evaluator and commit failures leave no owned import rows.
Known failures or risks: None recorded.
Acceptance criteria still unmet: None.
Next exact action: Implement MCF-06 generic export state and independent format lifecycle.
Next eligible task: MCF-08 after MCF-06/07 are also DONE.
Decisions or deviations from the parent plan: A valid zero-cue MCF document creates one
empty imported review with `both` scan capabilities/membership, using MCF's default both
semantics because no narrower channel evidence exists. MIME remains non-authoritative.
User approval required: None.
