# NSR-04 — Expose saved-review APIs and Manual saves

Status: **DONE**

Dependencies: NSR-02 and NSR-03. Deliverable: authenticated saved-review collection/item
APIs, source-aware target resolution, atomic Manual-filter saves, and safe conflicts.

Read the task-pack README, parent plan, NSR-00–03, and prerequisite handoffs first.

## Why this task exists

The web and EDL services need one public review authority. This task exposes the completed
repository aggregate while preserving server-owned source paths and the application's
authentication/CSRF policy.

## Scope

1. Implement the exact routes/DTOs from NSR-00 for:
   - source-aware review list and create;
   - review get and full explicit save;
   - rename, duplicate, and delete;
   - audio mute and video skip preview by review ID/filter ID;
   - list sorting by `name` or `updated_at` and validated direction.
2. Resolve target media safely:
   - Jellyfin uses configured credentials and the current item/version returned by the
     server;
   - Radarr/Sonarr use their adapters and current file/episode identity;
   - Other Files accepts only the established manual item identity/path workflow and
     resolves/validates an absolute readable scanner-host media file;
   - no catalog request trusts a browser path, duration, or metadata as authoritative.
3. Permit creation of an empty review even when no scan has created a local `MediaItemRow`;
   source resolution must upsert the local media identity first.
4. Accept full review drafts using review-local IDs/source provenance and expected
   revision. Validate memberships and filters again server-side; never accept a client
   claim that a scan belongs to the target.
5. Save staged new manual filters through NSR-03's atomic operation. Apply existing timing
   syntax/duration rules. New filters enter the reusable version Manual scan and the saved
   review snapshot together; a failed/stale save creates neither.
6. Map repository outcomes to the frozen safe statuses/codes. Scan-deletion conflicts must
   list every referencing review name plus modified date/context, including duplicates.
7. Apply existing setup/authentication, same-origin, and CSRF rules to every unsafe route.
   Add explicit policy/OpenAPI tests so no route becomes anonymous.
8. Keep old scan selection/edit endpoints only as transitional compatibility for the old
   frontend. Add comments/tests identifying NSR-12 as their removal owner; do not let new
   review routes write through them.
9. Review preview routes must resolve the source scan's verified media path but render the
   review snapshot's current edited start/end range and action. Reject wrong-action,
   missing, cross-review, or unusable-source cases safely. Do not mutate either row.

## Out of scope

- EDL export/import routes (NSR-06/07).
- React/API client changes (NSR-08+).
- A generic browser-supplied media path for catalog sources.
- Automatic saves.

## Required behaviors

- Most recently saved means greatest review `updated_at`, with stable ID as deterministic
  tie-breaker; list sort controls do not change this default-open rule.
- Duplicate names are valid in create, rename, and duplicate.
- Name-only rename cannot mark a review outdated.
- Deleting a marked review returns success while preserving the active sidecar and scan
  provenance; the subsequent status has no review marker.
- API responses expose source basenames when allowed, never an unintended full host path.

## Verification

- Ruff changed API/repository/model/test files.
- Focused API tests for all routes, sources, empty create, sorting, validation, revisions,
  duplicate names, Manual saves/rollback, review-edited preview ranges, delete behavior,
  and scan reference conflicts.
- OpenAPI assertions and authentication/CSRF policy tests.
- Existing source/manual/path mapping tests relevant to changed helpers.
- `git diff --check`.

## Acceptance criteria

- [x] One review API authority supports list/create/get/save/rename/duplicate/delete.
- [x] Every source can resolve and save an empty review without trusting browser metadata.
- [x] Full draft and Manual additions save atomically under review revision control.
- [x] Duplicate names, default-most-recent behavior, and sort modes match the plan.
- [x] Marked review deletion preserves EDL/scan provenance and clears review provenance.
- [x] Review-scoped preview uses snapshot timing and verified scan media.
- [x] All unsafe routes are authenticated and CSRF-protected; OpenAPI is current.
- [x] Transitional legacy routes cannot mutate saved-review state.
- [x] Shared definition of done is satisfied.

## Verifier instructions

Review route policy ordering before endpoint side effects. Exercise every source with fake
adapters, including empty create and stale Manual-filter save. Query active EDL rows after
deleting a marked review. Search for any review endpoint accepting a catalog source path.
Rerun focused tests before authorizing NSR-06/07.

## Handoff requirements

Record every endpoint/method, request/response/error shape, source-resolution helper,
legacy transition left in place, test commands/counts, and OpenAPI/policy evidence. Name
NSR-06 once NSR-05 is also DONE and NSR-07 once NSR-05 is DONE.

## Handoff

Status: DONE
Last completed substep: Exposed source-aware review collection/item APIs, trusted manual and
   catalog target resolution, revision-safe mutation errors, and review-scoped previews.
Files changed: scanner/api.py, scanner/repository.py, tests/test_scan_naming.py,
   tests/test_saved_review_schema.py.
Schema/API/contract changes: Added review list/create routes for Jellyfin, Radarr, Sonarr,
   and Other Files; review get/save/rename/duplicate/delete routes; mute/video preview routes;
   structured `ReviewErrorDetail` mapping for stale revisions, invalid membership, missing
   reviews/filters, and unavailable media. Manual and catalog media upserts use `commit=False`
   so initial review creation is one transaction.
Verification commands and results: `uv run pytest -q tests/test_saved_review_schema.py
   tests/test_saved_review_repository.py tests/test_scan_naming.py
   tests/test_editarr_integration.py` passed 179 tests; `uv run ruff check scanner tests`
   passed; `git diff --check` passed. Existing Starlette/httpx and FastAPI `on_event`
   deprecation warnings remain.
Manual or artifact evidence: Authenticated API coverage creates an empty manual review,
   verifies safe stale-revision mapping, and lists the saved review. OpenAPI now asserts the
   review item methods are present. At this task boundary legacy scan-owned routes remained
   for NSR-12; NSR-12 subsequently removed them and their repository mutation helpers.
Known failures or risks: Catalog staged Manual filters require a resolvable local media path;
   normal empty review creation and existing Manual scan reuse are supported.
Acceptance criteria still unmet: None within the NSR-04 scope.
Next exact action: Implement NSR-05, the pure EDL comment codec and parser.
Next eligible task: NSR-05 is eligible; NSR-06 and NSR-07 remain blocked on their prerequisites.
Decisions or deviations from the parent plan: None.
User approval required: None.
