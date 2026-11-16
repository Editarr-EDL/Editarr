# Checkpoint 0: authorization and baseline

## AUTH-00: Confirm the v1 database reset

- [x] Task complete
- Depends on: none
- Expected code changes: none

### Work

1. Obtain explicit user confirmation to skip a data-preserving migration and wipe the
   current development database.
2. Record which database URL/path is in scope.
3. Do not remove the database until immediately before the schema task that needs the
   clean database.
4. Do not remove model caches, exported EDLs, media, or unrelated files.

### Definition of done

- The user's explicit approval is recorded in the task/session history.
- The exact database target is resolved without a broad glob or environment-variable
  assumption.
- No destructive action has occurred merely to complete this preflight task.

### Handoff record

```text
Status: DONE
Last completed substep: Recorded explicit approval to skip a data-preserving migration and wipe the exact development database target, with deletion deferred until immediately before AUTH-03.
Files changed: docs/local-authentication-tasks/00-preflight.md
Verification run and result: Loaded scanner.config.Settings in the repository working directory; database_url=sqlite:///./data/scanner.db and resolved target=/path/to/Editarr/data/scanner.db. The target exists. No deletion, replacement, or schema change was performed.
Known failures or risks: The approved database must remain intact through AUTH-01 and AUTH-02; do not remove it before AUTH-03.
Next exact action: Run AUTH-01 before changing dependencies or schema.
Decisions or deviations from the parent plan: None. The target was read from the loaded Settings configuration rather than assumed from an environment variable or a filesystem glob.
Approval reference: User explicitly approved in this task/session: skip a data-preserving migration and wipe /path/to/Editarr/data/scanner.db, with deletion deferred until immediately before AUTH-03.
```

## AUTH-01: Record the pre-change baseline

- [x] Task complete
- Depends on: AUTH-00
- Expected code changes: none

### Work

1. Inspect `git status --short` and record all pre-existing changes.
2. Run focused fast checks:

   ```bash
   uv run ruff check scanner tests
   uv run pytest -q
   cd web && pnpm build && pnpm test
   ```

3. Record pre-existing failures exactly; do not broaden authentication work to repair
   unrelated failures.
4. Inspect live FastAPI routes and capture their paths/methods for later route-inventory
   assertions.

### Definition of done

- Existing worktree changes and test results are recorded.
- The current route inventory is available to compare with the post-gate inventory.
- Authentication implementation can begin without ambiguity about regressions.

### Handoff record

```text
Status: DONE
Last completed substep: Captured the live FastAPI API-route inventory after recording the worktree and running all focused baseline checks.
Files changed: docs/local-authentication-tasks/00-preflight.md; docs/local-authentication-tasks/README.md
Pre-existing worktree changes: `M docs/local-authentication-tasks/00-preflight.md`; `M docs/local-authentication-tasks/README.md`; and one unrelated untracked scanner file. Preserve them as unrelated to authentication implementation, except for the task-record updates made by AUTH-00 and AUTH-01.
Baseline verification: `uv run ruff check scanner tests` passed (`All checks passed!`); `uv run pytest -q` passed (149 passed, 5 warnings in 3.46s: Starlette TestClient deprecation and four FastAPI `on_event` deprecations); `cd web && pnpm build && pnpm test` passed (TypeScript/Vite production build succeeded; 45 tests passed).
Route inventory location/result: This AUTH-01 record, under `Live FastAPI route inventory (pre-gate)`: 57 `APIRoute` entries from `scanner.api.app`, captured using each route's live registered methods and path.
Known failures or risks: No baseline failures. The approved database target must remain intact through AUTH-02 and be wiped only immediately before AUTH-03.
Next exact action: Start AUTH-02.
Decisions or deviations from the parent plan: None.
```

### Live FastAPI route inventory (pre-gate)

Captured from live `scanner.api.app` `APIRoute` registrations on 2026-09-02:

```text
GET /api/settings/jellyfin
POST /api/settings/jellyfin/test
PUT /api/settings/jellyfin
DELETE /api/settings/jellyfin
GET /api/settings/sources/{service}
POST /api/settings/sources/{service}/test
PUT /api/settings/sources/{service}
DELETE /api/settings/sources/{service}
GET /api/sources/{service}/items
GET /api/sources/{service}/items/{item_id}/image
GET /api/sources/{service}/items/{item_id}
GET /api/sources/sonarr/items/{series_id}/episodes
GET /api/sources/sonarr/episodes/{item_id}
POST /api/filter-candidates
POST /api/filter-candidates/associations
GET /api/sources/{service}/items/{item_id}/scans
DELETE /api/sources/{service}/items/{item_id}/edl
POST /api/sources/radarr/items/{item_id}/scans
POST /api/sources/sonarr/episodes/{item_id}/scans
POST /api/sources/{service}/items/{item_id}/manual-findings
GET /api/settings/filters
PUT /api/settings/filters
GET /api/libraries
GET /api/libraries/{library_id}/items
GET /api/library-items/{item_id}
GET /api/library-items/{item_id}/children
GET /api/library-items/{item_id}/seasons
POST /api/scans
POST /api/library-items/{item_id}/manual-findings
POST /api/manual-scans
POST /api/manual-files/findings
GET /api/manual-scans
GET /api/manual-files/edl
DELETE /api/manual-files/edl
GET /api/scans/queue
POST /api/scans/queue/reorder
DELETE /api/scans/queue/{queue_id}
POST /api/scans/queue/{queue_id}/resume
GET /api/scans/history
DELETE /api/scans/{scan_id}
GET /api/scans/{scan_id}/findings
PUT /api/scans/{scan_id}/findings/{finding_id}
DELETE /api/scans/{scan_id}/findings/{finding_id}
GET /api/scans/{scan_id}/findings/{finding_id}/mute-preview
GET /api/scans/{scan_id}/findings/{finding_id}/video-preview
PUT /api/scans/{scan_id}/findings/selection
POST /api/scans/{scan_id}/edl
GET /api/library-items/{item_id}/scans
DELETE /api/library-items/{item_id}/edl
GET /api/library-items/{item_id}/image
GET /api/settings/path-mappings
POST /api/settings/path-mappings
PATCH /api/settings/path-mappings/{mapping_id}
DELETE /api/settings/path-mappings/{mapping_id}
POST /api/settings/path-mappings/reorder
POST /api/settings/path-mappings/resolve
GET /api/capabilities
```
