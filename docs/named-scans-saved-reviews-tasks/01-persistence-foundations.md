# NSR-01 — Create persistence foundations

Status: **DONE**

Dependencies: NSR-00. Deliverable: fresh-schema SQLAlchemy rows and domain DTOs for named
scans, saved reviews, review snapshots, and EDL review provenance.

Read the [task-pack README](README.md), parent plan, repository `AGENTS.md`, NSR-00, and
the completed NSR-00 handoff/Contract record first.

## Why this task exists

Every subsequent backend workflow depends on one stable schema. This task adds storage and
serialization foundations without implementing review CRUD, import, export, or frontend
behavior. It also owns the one approved development-schema reset so later tasks do not
perform ad hoc destructive operations.

## Scope

1. In `scanner/db.py`, add the exact NSR-00 fields/tables and relationships:
   - optional scan display name and explicit scan origin/import metadata;
   - saved review identity, media item/version key, name, resource/content revisions, and
     timestamps;
   - review-to-scan role membership;
   - review-owned filter snapshots with source scan/finding provenance and copied editable
     fields;
   - nullable active-EDL review provenance and exported content revision.
2. Add database constraints for valid origin/role values, required review name, unique
   membership, valid revision values, and appropriate uniqueness of one source finding
   snapshot per review. Do not make scan or review names unique.
3. Configure cascades deliberately:
   - deleting a review deletes only its memberships/snapshots;
   - deleting a review must not delete scans or scan findings;
   - scan deletion remains explicit repository behavior and must not cascade through saved
     reviews;
   - active EDL review provenance is nullable and is cleared by repository logic before
     review deletion rather than deleting the EDL.
4. In `scanner/models.py`, add the frozen DTOs/enums from NSR-00. Keep conversion-free
   response types separate from write payloads where validation/revision fields differ.
5. Add schema/model tests for columns, constraints, defaults, relationships, enum values,
   serialization, validation, and duplicate-name acceptance. Use in-memory/temp databases.
6. Update schema documentation owned by this task if NSR-00 identifies a contract table in
   `docs/feature-design-spec.md` that must stay synchronized.
7. Recreate only the checkout development database under the approved policy:
   - first inspect environment/config and print the exact resolved target;
   - stop if it is outside this checkout, comes from an unexpected environment override,
     or appears to be an installed/user runtime database;
   - confirm no Editarr process is using it;
   - recreate using the project's normal initialization path and verify the new tables;
   - record the exact target and result without copying user content.
   Tests do not depend on this database reset.

## Out of scope

- Repository CRUD or business rules beyond relationships/row converters needed by tests.
- Public API routes.
- EDL parsing/export changes.
- Web changes.
- A data-preserving migration or compatibility shim for the old v1 schema.

## Required invariants

- Media-version scoping uses one normalized internal value for versions without an ID.
- Resource and content revisions start at the exact values frozen by NSR-00.
- A review filter always belongs to its review and source scan; a supplied source finding
  must belong to that scan when repository logic later creates it.
- Review filter edits cannot mutate `ScanFindingRow` through ORM cascades or shared row
  identity.
- Timestamps are timezone-aware at domain boundaries, consistent with existing models.
- Existing auth/settings/queue/media tables remain intact in the new schema definition.

## Verification

Run at minimum:

- `uv run ruff check scanner/db.py scanner/models.py tests/test_auth_schema.py` plus any
  new focused test file
- `uv run pytest -q tests/test_auth_schema.py` plus new schema/model tests
- a temporary-database `Base.metadata.create_all`/introspection check
- `git diff --check`

Record the development reset separately from test evidence. If the safe target cannot be
proved, leave it untouched and mark only that acceptance item `BLOCKED`.

## Acceptance criteria

- [x] Fresh schema contains every frozen field/table/constraint and creates cleanly.
- [x] Duplicate scan/review names are accepted; invalid names/revisions/roles are rejected.
- [x] Review deletion cascades only to review-owned rows.
- [x] Active EDL review association can become null without removing scan provenance.
- [x] Domain/write DTOs serialize and validate according to NSR-00.
- [x] No migration was added.
- [x] The exact checkout development database was safely recreated, or the task is
      explicitly BLOCKED with the unresolved safety fact.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Create a fresh temporary database independently, inspect its constraints, insert two
duplicate names, exercise review cascade behavior, and confirm existing tables still
initialize. Review the reset evidence and ensure it names only the checkout database.
Reject any implicit migration, installed-data access, or cascade from review to scan.

## Handoff requirements

List every table/column/constraint/relationship, DTO and enum added, the exact safe reset
target/result, test commands/counts, and row-conversion work deliberately left to NSR-02
or NSR-03. Name NSR-02, NSR-03, and (if NSR-00 is done) NSR-05 as next eligible.

## Handoff

### Implementation inventory

- `scans`: nullable `name`, non-null `origin` (Python/server default `generated`),
  nullable `import_source_name`, nullable timezone-declared `imported_at`. `ck_scans_name`
  enforces a normalized 1–100-code-point non-control value or null; `ck_scans_origin`
  restricts origin to `generated`, `manual`, or `imported`. ORM name assignments use the
  same Unicode normalization/validation as write DTOs. Existing columns remain intact.
- `saved_reviews`: `id` primary key, `media_item_id` FK, normalized non-null
  `media_source_id` (default empty string), required `name`, `revision` and
  `content_revision` (both default zero), `created_at`, `updated_at`.
  `ck_saved_reviews_name`, `ck_saved_reviews_revision`, and
  `ck_saved_reviews_content_revision` enforce names/non-negative revisions;
  `ix_saved_reviews_target` indexes item/version. Names are not unique.
- `review_scan_memberships`: composite primary key `(review_id, scan_id)`, both FKs;
  `role`, `position`; unique `(review_id, position)`; role and non-negative-position
  checks. The review FK cascades on deletion; the scan FK does not.
- `review_filters`: composite primary key `(review_id, id)`; `source_scan_id`, nullable
  `source_finding_id`, `position`, `source`, `category`, `label`, `details`, `group_key`,
  `start_seconds`, nullable `end_seconds`, `confidence`, `edit_action`, `selected`.
  Unique review/source-finding and review/position constraints; composite source-finding/
  source-scan FK prevents attaching evidence from a different scan. This requires the
  explicit redundant `uq_scan_findings_id_scan` source identity constraint.
  Checks cover position, source, action, start/end, and confidence. Only the review FK
  cascades on deletion. Multiple null source-finding IDs are allowed for staged rows.
- `media_item_edls`: nullable `review_id` FK and `exported_content_revision`, constrained
  to be null together or populated together with a non-negative revision. No cascade
  deletes or clears the association; repository deletion must explicitly clear the pair.
- `SavedReviewRow.memberships` and `.filters` are ordered `all, delete-orphan`
  relationships. Review/media and membership/scan references have no delete cascade.
  Filter `source_scan` is an ordinary reference, while `source_finding` is a view-only
  evidence relationship; copying/changing snapshot scalar fields cannot edit evidence.
- `scanner.models` adds `ScanOrigin`, `ReviewSource`, `ReviewScanRole`, shared Unicode
  name validators, `ScanNameUpdate`, all NSR-00 review target/summary/detail/membership/
  filter/write/reference/export/import/error DTOs, source-specific create DTOs, and
  `EdlReviewProvenance`. Existing Scan/queue, EDL scan/status, Arr item, Jellyfin item,
  and Jellyfin version DTOs gain their frozen additive fields. New writes forbid extras;
  filter UUIDs, finite ranges/confidence, name normalization, strict non-negative expected
  revisions, discriminated catalog/manual targets, and explicit Manual identity are
  validated. New review/import timestamp boundaries require timezone-aware datetimes.
- No schema contract table was identified by NSR-00 in `feature-design-spec.md`, so this
  foundations task does not rewrite the broader product specification ahead of workflows.
  NSR-02 owns scan creation/conversion (including explicit Manual origin and derived
  `manual` boolean); NSR-03 owns review converters/CRUD and attaching UTC to SQLite's
  naive stored datetime values; NSR-06 owns computed/embedded export state.

Status: DONE
Last completed substep: Fixed import ordering and long test lines, then a Terra verifier
  reran the required lint, focused tests, and whitespace check successfully.
Files changed: scanner/db.py, scanner/models.py, tests/test_saved_review_schema.py, this
  task file, and task-pack README.md (in addition to prior NSR-00 documentation changes).
Schema/API/contract changes: Scan name/origin/import metadata; saved_reviews,
  review_scan_memberships, review_filters; nullable active EDL review provenance;
  frozen NSR-00 DTOs and strict write validation. No routes or migrations added.
Verification commands and results:
  - `uv run ruff check scanner/db.py scanner/models.py tests/test_auth_schema.py
    tests/test_saved_review_schema.py`: all checks passed after formatting-only fixes.
  - Terra rerun: `UV_CACHE_DIR=/private/tmp/nsr01-verify.4yIN6B/uv-cache
    EDITARR_DATA_DIR=/private/tmp/nsr01-verify.4yIN6B
    EDITARR_DATABASE_URL=sqlite:////private/tmp/nsr01-verify.4yIN6B/scanner.db
    uv run pytest -q tests/test_auth_schema.py tests/test_saved_review_schema.py`:
    66 passed in 0.98s, with four existing FastAPI `on_event` deprecation warnings.
    An initial invocation could not access the shared uv cache under the sandbox;
    using the private temporary cache resolved that before test collection.
  - Independent Terra temporary SQLite check with foreign keys enabled:
    `Base.metadata.create_all` created all 17 tables; required checks and FKs were
    present; duplicate review names were accepted; review deletion removed only its
    memberships/filters and retained the source scan and second review;
    `PRAGMA integrity_check` returned `ok`.
  - `git diff --check`: passed. No test was weakened and no required check was skipped.
Manual or artifact evidence: September 13, 2026 reset target was exactly
  /path/to/Editarr/data/scanner.db (122880 bytes before reset).
  The user confirmed the development service had been killed and explicitly renewed
  permission to wipe this development database for schema changes. Case-insensitive
  environment inspection found no EDITARR_DATA_DIR or EDITARR_DATABASE_URL override.
  bootstrap_entrypoint_runtime_paths(()) plus Settings resolved checkout/data and this
  exact SQLite URL; the resolved path matched literally, was not a symlink, and had one
  hard link. Only scanner.db existed, with no WAL/SHM/journal companions. An escalated
  `ps -axo pid,ppid,etime,command` filtered for run_development/uvicorn/scanner.worker/
  Editarr/samwise returned no matches; `lsof` on the exact database found no open handles.
  `mv /path/to/Editarr/data/scanner.db
  /private/tmp/nsr01-development-db-backup.YoKSh5/scanner.db` preserved a recoverable copy
  in a private mktemp directory. A fresh .venv/bin/python process repeated the override
  and exact-target assertions, invoked bootstrap_entrypoint_runtime_paths(()) followed
  by scanner.db.init_db(), and inspected only the newly created checkout database.
  All 17 Base.metadata tables exist and are empty, new scan/provenance columns and
  saved-review checks/composite filter provenance FK are present, and
  PRAGMA integrity_check returned `ok`; the new database is 176128 bytes.
  No installed/user runtime database, media, or sidecar was opened or changed, and no
  service was started or stopped. Start normally with `./scripts/dev.sh dev`; this fresh
  development database requires initial admin/setup and source settings again.
  Independent Terra reset review confirmed a regular, non-symlink checkout database
  with one hard link and 176128 bytes, all 17 tables, zero review rows, the required
  constraints and only review-owned cascade FKs. Integrity was `ok` and
  `PRAGMA foreign_key_check` returned no rows.
Known failures or risks: No outstanding verification failures. The temporary backup is
  recoverable until OS cleanup;
  it is old-schema development data and should not replace the new runtime database.
  Row converters, scan creation origins/manual-boolean derivation, repository
  review behavior, and export provenance population remain NSR-02/03/06 responsibilities.
Acceptance criteria still unmet: None.
Next exact action: Assign NSR-02 and first verify this handoff and its focused checks.
  No further database reset is needed.
Next eligible task: NSR-02, NSR-03, and NSR-05. Follow the user's requested task order:
  NSR-02 next.
Decisions or deviations from the parent plan: None.
User approval required: None; the development reset was explicitly authorized and completed.
