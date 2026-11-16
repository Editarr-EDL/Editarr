# Named scans, saved reviews, and EDL import coding tasks

Parent: [Named scans, saved reviews, and EDL import plan](../named-scans-saved-reviews-edl-import-plan.md)

Status: **NSR-00 through NSR-12 DONE**

This pack turns the approved parent design into dependency-ordered assignments. It is
written for a coding agent receiving one bounded task without conversation history.
Before changing code, read this README, the parent plan, repository `AGENTS.md`, the
assigned task, and every prerequisite task's handoff.

The parent plan and decision record are authoritative. A task may settle routine code
organization, naming, response-envelope, and test-fixture details within that design. If
implementation requires a different persistence owner, destructive lifecycle, supported
EDL syntax, source trust boundary, or user workflow, stop and request user approval.

## Assignment prompt

Assign exactly one task with a prompt of this form:

> Implement NSR-XX from `docs/named-scans-saved-reviews-tasks/XX-NAME.md`.
> Read that task, the task-pack README, parent plan, repository AGENTS.md, and all
> prerequisite handoffs. Work only within the assigned scope, preserve unrelated changes,
> meet every acceptance criterion and definition-of-done item, update the task handoff and
> README checklist, and return a concise checkpoint summary. Do not begin a dependent task,
> use or wipe an installed/user database, start or stop a user's Editarr instance, or make
> a new product decision silently.

This pack defines work; it does not launch agents or create sidebar tasks. A dependency is
complete only when its status is `DONE` and its handoff contains passing evidence. Allowed
statuses are `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, and `DONE`.

## Approved product contract

The following decisions are settled and must not be reopened:

1. Scan names are optional, mutable, non-unique, available on Manual scans, trimmed at the
   edges, case-preserving, control-character-free, and limited to 100 Unicode code points.
   Unnamed scans retain `Scan #N`; named displays retain the scan number too.
2. A saved review is scoped to one local media item and physical media version. Its name is
   required but non-unique. It owns scan-role membership and private filter snapshots,
   including selection, category/word/label, description, timing, and action. Review edits
   never mutate another review.
3. Opening a version selects its most recently saved review. With none, the browser starts
   an empty unsaved review. Save is explicit. Reviews can be renamed, duplicated, and
   deleted; the list can sort by name or modification date.
4. Source scan findings remain reusable evidence. Adding a scan snapshots its applicable
   findings. Removing and re-adding it before save restores that browser-session snapshot;
   saving the removal forgets it, so a later add takes a fresh source snapshot.
5. Add Filter creates a reusable source finding in the saved Manual scan when the review is
   saved, then stores an isolated review snapshot. Discarding a draft creates no source
   finding. Later reviews can snapshot the Manual scan; later review edits stay isolated.
6. One active sidecar exists per media item/version. It retains all contributing scan
   provenance plus the saved review and review-content revision used to export. A later
   content save marks that review `Outdated`; a rename does not. Deleting the EDL clears the
   marker.
7. Deleting the marked review is allowed. The on-disk EDL and scan provenance remain;
   review provenance is cleared. Deleting any review leaves every scan intact. Scan
  deletion is blocked by active EDL provenance or any saved review; its confirmation and
  any conflict report all referencing review names with disambiguating modified dates.
8. EDL import is available for Jellyfin, Radarr, Sonarr, and Other Files through browser
   upload and an absolute scanner-host path. The target item/version is selected first.
9. Import accepts UTF-8, whitespace-separated seconds rows. Action `1` is mute/audio and
   `3` is skip/video. Blank lines and Kodi `##` comments are accepted. Other actions are
   ignored with one warning. Comma rows, frames, and alternate timestamp formats are not
   accepted in this phase. An input with no supported actions fails and creates nothing.
10. One import creates one completed, non-queued imported/manual scan: audio-only,
    video-only, or combined according to supported rows. Its initial name is the review
    name and remains renameable. Its source/version display is the safe EDL basename; its
    creation/import time is a timestamp, never a scan duration. Review deletion retains it.
11. Export keeps `## Editarr Simple EDL`, writes one `## category: label` comment per
    contributing filter before each normalized output row, and then writes the action row.
    Import associates contiguous recognized comments with the following supported row.
    If normalization merges filters, all comments remain but original pre-normalization
    boundaries are intentionally not recoverable. Use `other: Imported mute` and
    `other: Imported skip` for uncommented supported rows.
12. The user approved skipping schema migrations and recreating the checkout's v1
    development database. This approval does not cover an installed/user data directory.

Kodi's comment rule is recorded in the parent plan with the official source. The mapping
of immediately preceding `## category: label` lines to an action is Editarr's own
round-trip convention.

## Target domain and API vocabulary

NSR-00 freezes exact DTO spellings and route shapes before implementation. Unless that
task documents a concrete incompatibility, later tasks should use these concepts:

- `Scan.name: str | None` and an explicit origin distinguishing generated, Manual, and
  imported scans without inferring origin from model names.
- `SavedReview`: ID, media item, normalized media-version key, required name, resource
  revision, content revision, created/updated timestamps, scan-role memberships,
  review-owned filters, and active/outdated export state.
- `ReviewScanMembership`: review ID, scan ID, and role `audio`, `video`, or `both`.
- `ReviewFilter`: stable review-local ID; source scan and optional source finding; copied
  source/category/label/details/group/range/confidence/action fields; selected state.
- Active EDL: existing per-version record and all scan references, plus nullable review ID
  and exported review-content revision. Review deletion explicitly nulls those two fields.
- Scan rename: one protected partial-update endpoint returning the current `Scan`.
- Review collection: source-aware list/create operations that can save an empty review
  before a local media row existed; item/version identity is always re-resolved server-side.
- Review item operations: get, save, rename, duplicate, and delete by saved-review ID.
- Review preview operations: render audio/video preview by review ID and review-filter ID,
  using the review-owned edited range with the source scan's verified media path.
- Review export: export a saved review by ID and expected revision; arbitrary browser scan
  arrays are no longer the authoritative export input.
- Import: separate protected upload and scanner-path endpoints feeding one service/parser
  operation and returning the created review, created scan, and one aggregated warning.

The bounded transition is complete. Saved reviews are the sole writable authority for
selection and edited filter content; NSR-12 removed obsolete scan-finding mutation/export
web helpers and tests, while source findings remain immutable reusable evidence.

## Code map

- `scanner/db.py`: SQLAlchemy tables and fresh-schema initialization.
- `scanner/models.py`: domain/API DTOs and finding types.
- `scanner/repository.py`: media/scan persistence, review aggregate, deletion guards,
  import transaction, EDL file lifecycle, and row conversion.
- `scanner/edl.py`: pure EDL normalization, comment emission, and parsing. It must not
  perform database or filesystem writes.
- `scanner/api.py`: source resolution, scan creation, review/import/export endpoints, and
  safe HTTP conflict/error mapping.
- `scanner/arr.py` and `scanner/jellyfin.py`: upstream identity and metadata; browser input
  never replaces server-side resolution for catalog targets.
- `web/src/api.ts`: TypeScript DTOs and protected API helpers.
- `web/src/App.tsx`: current scan forms, selectors, review drafts, Manual/Other Files
  workspace, exports, dialogs, and global scan tables.
- `web/src/styles.css`: scan/review layout and responsive/accessibility states.
- `tests/test_editarr_integration.py`, `tests/test_edl.py`, `tests/test_auth_schema.py`:
  principal backend coverage.
- `web/src/App.test.tsx` and `web/src/api.test.ts`: principal frontend coverage.
- `docs/feature-design-spec.md`, `docs/api-contract.md`, and `README.md`: final product,
  API, and operator/user documentation.

Resolved integration hazards:

- Legacy selection and content edits mutated `ScanFindingRow`; review snapshots now isolate
  edits and source findings are read-only evidence.
- Source finding IDs remain provenance only. Frontend drafts and mutations use stable
  review-local filter IDs.
- Empty review creation needs source-aware media upsert/resolution because no selected scan
  may exist from which to infer `media_item_id`.
- Manual filters are staged in an unsaved review draft. Only a successful review create/save
  transaction creates or reuses the version's Manual evidence scan; discarded or failed
  drafts cannot leak source findings.
- Browser export now takes a saved-review ID/revision and reads selected review snapshots;
  atomic sidecar replacement remains enforced.
- Review-scoped preview routes use private snapshot ranges and verified source-scan media
  provenance without mutating source findings.
- EDL normalization merges ranges and subtracts skip coverage from mute ranges. Metadata
  comments need contributor tracking through those transformations.
- Duplicate scan/review names are valid. Error messages and destructive UI cannot use a
  name alone as identity.
- Browser upload and host-path import must share limits and parser results. Never trust an
  uploaded filename as a filesystem path or expose a full scanner-host path in responses.
- Review deletion may clear review provenance from active EDL state, but must not remove
  the sidecar or its scan links.

## Master checklist and dependency order

| Done | ID | Task | Prerequisites | Primary ownership |
| --- | --- | --- | --- | --- |
| [x] | NSR-00 | [Freeze interfaces and baseline](00-contract-baseline.md) | None | Contracts/tests only |
| [x] | NSR-01 | [Create persistence foundations](01-persistence-foundations.md) | NSR-00 | `db.py`, schema DTOs |
| [x] | NSR-02 | [Implement scan naming backend](02-scan-naming-backend.md) | NSR-01 | Scan repository/API |
| [x] | NSR-03 | [Implement saved-review repository](03-saved-review-repository.md) | NSR-01 | Review persistence |
| [x] | NSR-04 | [Expose saved-review APIs and Manual saves](04-saved-review-api.md) | NSR-02, NSR-03 | Review API/service |
| [x] | NSR-05 | [Build the EDL comment codec and parser](05-edl-codec.md) | NSR-00 | `edl.py` |
| [x] | NSR-06 | [Export saved reviews and track active state](06-review-export.md) | NSR-04, NSR-05 | Export/provenance |
| [x] | NSR-07 | [Import EDLs into saved reviews](07-edl-import-backend.md) | NSR-02, NSR-04, NSR-05 | Import/API |
| [x] | NSR-08 | [Add frontend contracts and draft state](08-frontend-data-layer.md) | NSR-06, NSR-07 | `api.ts`, state helpers |
| [x] | NSR-09 | [Add scan naming UI](09-scan-naming-ui.md) | NSR-08 | Scan forms/lists |
| [x] | NSR-10 | [Build saved-review UI](10-saved-review-ui.md) | NSR-08, NSR-09 | Catalog/Arr review UX |
| [x] | NSR-11 | [Add import UI and Other Files parity](11-import-and-other-files-ui.md) | NSR-10 | Import/Other Files UX |
| [x] | NSR-12 | [Remove legacy paths and run final verification](12-final-integration.md) | NSR-06, NSR-07, NSR-11 | Cleanup/docs/full suite |

Default to sequential work. After NSR-00, NSR-05 can run independently of NSR-01–03 if
agents do not both edit shared tests. After NSR-01, NSR-02 and NSR-03 can run in parallel
only with explicit ownership of `scanner/models.py`, `scanner/repository.py`, and
`tests/test_editarr_integration.py`; sequential execution is safer. Do not begin frontend
contract work until NSR-06 and NSR-07 have frozen real response shapes.

## Checkpoints

| Checkpoint | Tasks | Safe exit state |
| --- | --- | --- |
| 0 — contract | NSR-00 | Baseline and exact shared interfaces are recorded; no behavior changed. |
| 1 — persistence | NSR-01–03 | Fresh schema stores named scans and isolated review aggregates. |
| 2 — backend workflows | NSR-04–07 | Review save/manual/import/export and provenance pass API tests. |
| 3 — frontend foundation | NSR-08–09 | Web types/drafts and scan naming use the new contract. |
| 4 — product UX | NSR-10–11 | All sources can manage reviews and import/export EDLs. |
| 5 — release-ready | NSR-12 | Legacy authorities are gone; docs and full verification pass. |

Do not proceed past a checkpoint with a `BLOCKED` prerequisite.

## Safety and database reset

Read repository `AGENTS.md`. The user explicitly approved a v1 schema reset for this
feature. NSR-01 alone owns that reset and must first resolve and print the exact target.
It may recreate only the checkout's development database after confirming that no user or
installed service is using it. Never delete an installed runtime database, a path outside
the checkout, or a database selected through an unexpected environment override. Tests
must continue using in-memory or temporary databases.

Do not create migrations solely to preserve development data. Preserve unrelated worktree
changes. Do not start/stop the user's real service, use real catalog credentials, rewrite
media files, or import a real EDL into user data. Filesystem tests use temporary media and
sidecars.

## Shared verification rules

- Run focused checks for every changed layer and record exact commands/results.
- Backend changes run `uv run ruff check` on changed Python files and focused pytest.
- Frontend changes run focused Vitest; any task changing TypeScript production code also
  runs `pnpm build` in `web/`.
- API/schema changes assert OpenAPI and authentication/CSRF coverage where applicable.
- Filesystem lifecycle tests prove failure atomicity and use temporary directories.
- `git diff --check` passes for every task.
- Unrelated failures are reported, not hidden or fixed outside scope.

## Shared definition of done

Every task must satisfy all of the following:

- All task acceptance criteria are checked with evidence.
- Required focused lint/tests pass and no test was weakened to accommodate a regression.
- Relevant contracts/tests/docs are updated in the same task.
- No user data, installed database, live service, or real media was changed.
- The assigned task file contains a current handoff; this README checklist/status matches.
- The handoff makes the next agent able to verify the result without reconstructing the
  entire implementation.
- `DONE` is forbidden when a required behavior, test, or prerequisite is missing.

## Required handoff record

At every interruption, blocked exit, or completion, replace the assigned task's handoff
placeholder with:

```text
Status: NOT STARTED | IN PROGRESS | BLOCKED | DONE
Last completed substep:
Files changed:
Schema/API/contract changes:
Verification commands and results:
Manual or artifact evidence:
Known failures or risks:
Acceptance criteria still unmet:
Next exact action:
Next eligible task:
Decisions or deviations from the parent plan:
User approval required:
```

The implementing agent must include exact test counts and name any skipped verification.
The verifying/next agent must first inspect the diff and handoff, rerun the named focused
checks, confirm acceptance criteria, and only then begin its own task. If verification
does not reproduce, update the previous handoff to `BLOCKED` or `IN PROGRESS` and return
the failure rather than building on an unverified contract.
