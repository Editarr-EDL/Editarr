# MCF-01 - Create persistence foundations

Status: **DONE**

Dependencies: MCF-00. Deliverable: fresh-schema rows, constraints, and converters for the
canonical MCF taxonomy, metadata, defaults, format revisions, and generic exports.

## Scope

1. Follow the MCF-00 contract exactly in `scanner/db.py`, `scanner/models.py`, and focused
   schema/converter tests.
2. Replace `ContentCategory` with nullable `McfCategoryFamily category` and
   `McfCategory subcategory`, persist existing `Severity`, MCF channel, and cue comment on
   source findings and review snapshots, and enforce paired nullability plus registry-valid
   family/subcategory values at the service boundary and practical database constraints.
3. Add one immutable scan-owned MCF document metadata record and one editable review-owned
   metadata record with the approved ownership/cascade behavior and material-bound
   constraints.
4. Add EDL and MCF format revisions to saved reviews while preserving resource and
   aggregate content revisions.
5. Replace EDL-only sidecar rows with format-keyed export/provenance rows unique by media
   item, normalized media version, and format. Preserve all scan-role references.
6. Add normalized persistence for revisioned selection rules and per-subcategory severity
   defaults. Seed Medium severity through fresh-schema initialization, not a migration.
7. Implement row converters and compatibility adapters needed for existing EDL callers,
   without changing their behavior in this task.
8. Map current producers in this task: profanity to `language/swearing`, explicit
   blasphemy producers to `language/blasphemy`, nudity to `nudity/nudity`, violence to
   `violence/violence`, and test-only producers to a real registry pair. Do not infer
   blasphemy or implement richer AI subcategorization here.
9. Update EDL comments to emit canonical subcategory tokens. Parse exact MCF tokens and
   legacy `profanity`/`nudity`/`violence` comments into the approved pairs. Unknown,
   `other`, and uncommented rows retain null category/subcategory with their label/details.
10. Add exhaustive temporary-database tests for constraints, cascades, independent format
   rows, source/review metadata isolation, defaults ordering, and fresh initialization.

## Database reset boundary

This task alone may recreate the checkout development database under the approval recorded
in the parent plan. Before doing so, resolve and print the exact configured target; prove
it is the checkout development database and not used by a running/installed service. Do
not add a preservation migration solely for v1 development data. Tests use temporary
databases regardless of the reset.

## Out of scope

- MCF parsing/import/export behavior.
- Defaults evaluation or public settings routes.
- Review save/revision behavior beyond converters.
- Frontend changes.

## Verification

- Ruff changed Python files.
- Focused fresh-schema, constraint, converter, and existing saved-review schema tests.
- Temporary file-backed SQLite restart/persistence check.
- Existing EDL status schema tests through the compatibility adapter.
- `git diff --check`.

## Acceptance criteria

- [x] Fresh schema stores every frozen field without an opaque competing authority.
- [x] Source metadata survives review deletion; review metadata copies are isolated.
- [x] EDL and MCF export rows coexist independently for one physical version.
- [x] Defaults/rules have stable IDs/order/revision and deterministic seeds.
- [x] Every current production finding uses the approved canonical pair and existing severity.
- [x] Legacy EDL input is the only path allowed to create an unclassified finding.
- [x] Existing EDL converters remain behaviorally compatible.
- [x] No development reset was performed; the configured target failed the safety boundary.

## Handoff

Status: DONE
Last completed substep: Fixed canonical enum rendering in EDL comments and reran the full
backend suite after focused schema, converter, producer, and compatibility checks.
Files changed: `scanner/models.py`, `scanner/db.py`, `scanner/repository.py`,
`scanner/edl.py`, `scanner/pipeline/audio.py`, `scanner/pipeline/visual.py`,
`scanner/pipeline/runner.py`, `scanner/video_model_corpus.py`,
`scanner/video_model_eval.py`, focused backend tests, and this task record.
Schema/API/contract changes: Replaced `ContentCategory` with the closed 9-family and
97-subcategory MCF taxonomy; added severity/channel/comment persistence, scan/review MCF
metadata rows, EDL/MCF review revisions, format-keyed export/provenance rows, normalized
selection-rule and severity-default rows, generic export DTOs, and temporary EDL/defaults
adapters. Fresh schemas seed one revision-zero settings row and 97 ordered Medium defaults.
Verification commands and results: `uv run ruff check scanner tests scripts native_build`
passed with zero issues; `uv run pytest -q` passed 857 tests in 27.53 seconds with no
warnings; focused contract/schema/repository/EDL selection passed 106 tests; focused
schema suite passed 67 tests; file-backed SQLite restart test passed; `git diff --check`
passed.
Manual or artifact evidence: A read-only safety check resolved the active database to
`~/Library/Application Support/Editarr/scanner.db` (278,528 bytes), outside the
checkout, with a scanner process running. It was classified UNSAFE and was not modified.
All implementation verification used temporary or in-memory databases and test-owned
files.
Known failures or risks: The external configured database remains on the pre-MCF schema by
design. It must not be reset under the checkout-only approval. The frontend still consumes
transitional EDL/defaults DTOs until its assigned tasks.
Acceptance criteria still unmet: None.
Next exact action: Implement MCF-02 strict codec, or MCF-03 structured-default evaluation
and API; both prerequisites are now complete.
Next eligible task: MCF-02 and MCF-03.
Decisions or deviations from the parent plan: No migration was added. The approved reset
was skipped because the effective database target was outside the checkout and a scanner
process was active. Existing EDL APIs project from the new format-keyed authority.
User approval required: None for temporary-test work. Any future operation on the external
configured database requires separate explicit approval after the running service is
stopped and the target is re-evaluated.
