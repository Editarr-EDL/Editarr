# MCF support coding tasks

Parent: [Movie Content Filter support plan](../mcf-support-plan.md)

Status: **MCF-00 through MCF-13 DONE**

This pack turns the approved parent design into dependency-ordered implementation
assignments. Before changing code, read this README, the parent plan, repository
`AGENTS.md`, the assigned task, and every prerequisite handoff.

## Assignment prompt

Assign exactly one task with this prompt:

> Implement MCF-XX from `docs/mcf-support-tasks/XX-NAME.md`. Read that task, the task-pack
> README, parent plan, repository AGENTS.md, and all prerequisite handoffs. Work only
> within the assigned scope, preserve unrelated changes, meet every acceptance criterion
> and definition-of-done item, update the task handoff and README checklist, and return a
> concise checkpoint summary. Do not begin a dependent task, use or wipe an installed/user
> database, start or stop a user's Editarr instance, touch real sidecars/media, or make a
> new product decision silently.

Allowed statuses are `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, and `DONE`. A dependency is
complete only when it is `DONE` with reproducible passing evidence.

## Frozen product direction

The parent plan is authoritative. In particular:

1. MCF family/subcategory is Editarr's primary content taxonomy. `category` stores the
   family and `subcategory` stores the exact cue token; the registry validates the pair.
2. Import accepts strict MCF 1.0.0/1.1.0; export emits strict MCF 1.1.0 only.
3. One MCF import creates one completed combined scan and one saved review atomically.
4. Source findings and imported document metadata are immutable evidence. Review filters
   and review MCF metadata are isolated editable copies.
5. MCF has no playback action. Import maps audio to mute, video to skip, and both/default
   to visual skip while retaining exact MCF channel.
6. Existing `Severity` is canonical MCF severity. Channel/comment remain separate from
   evidence source, action, label/details, and confidence.
7. Current producers map ordinary profanity to `language/swearing`, explicit blasphemy
   producers to `language/blasphemy`, nudity to `nudity/nudity`, and violence to
   `violence/violence`. Generic profanity never guesses blasphemy.
8. Defaults use structured Allow/Deny rules over canonical fields and per-subcategory
   severity defaults. They fill severity/channel but never reclassify category/subcategory.
9. Unclassified legacy EDL findings remain EDL-capable. New/manual/MCF data requires a
   valid pair, and selected incomplete filters block MCF export.
10. `.edl` and `.mcf` coexist with independent provenance, format revisions, outdated
    state, badges, and deletion.
11. MCF output groups only exactly equal ranges. It never uses EDL merge, subtraction, or
    skip-precedence normalization.
12. The user approved a fresh checkout development database for schema implementation,
    without a preservation migration. Installed/user data is outside that approval.
13. After core MCF work is complete, MCF-12 improves AI subcategorization. At minimum it
   evaluates Qwen summaries and retains deterministic general-category fallbacks.
14. Review findings use collapsible family, subcategory, and finding-group tiers. Severity
   and channel are columns; Time, Description, and Severity sort independently in Audio
   and Video without reordering the hierarchy.

## Code map

- `scanner/db.py`: fresh schema, finding/review metadata, settings, generic export state.
- `scanner/models.py`: canonical MCF taxonomy DTOs, defaults, reviews, imports/exports.
- `scanner/mcf.py`: pure strict codec; no database or filesystem writes.
- `scanner/edl.py`: existing Simple EDL behavior, which must remain passing.
- `scanner/repository.py`: review/default/import/export transactions and provenance.
- `scanner/api.py`: source resolution, protected routes, safe errors, status decoration.
- `scanner/jellyfin.py`, `scanner/arr.py`: trusted metadata candidates, never browser
  path authority.
- `scanner/pipeline/`: deterministic core mappings, followed by MCF-12 subcategorization;
   source evidence remains immutable after creation.
- `web/src/api.ts`: TypeScript contracts and protected request helpers.
- `web/src/review-draft.ts`: local draft/revision/default-application state.
- `web/src/App.tsx`: settings, review, import/export, catalog, and Other Files UI.
- `web/src/styles.css`: compact responsive MCF controls, menus, and badges.
- `tests/test_mcf.py`: pure codec fixtures and golden bytes.
- Existing saved-review, EDL, API/auth, frontend, and browser tests remain regression
  authorities.

## Master checklist

| Done | ID | Task | Prerequisites | Primary ownership |
| --- | --- | --- | --- | --- |
| [x] | MCF-00 | [Freeze contracts and baseline](00-contract-baseline.md) | None | Contracts/tests only |
| [x] | MCF-01 | [Create persistence foundations](01-persistence-foundations.md) | MCF-00 | Schema/row converters |
| [x] | MCF-02 | [Build the strict MCF codec](02-mcf-codec.md) | MCF-00 | Pure codec/fixtures |
| [x] | MCF-03 | [Implement structured defaults](03-structured-defaults.md) | MCF-01 | Settings/evaluator API |
| [x] | MCF-04 | [Extend the review domain](04-review-domain.md) | MCF-01, MCF-03 | Review metadata/fields/revisions |
| [x] | MCF-05 | [Import MCF into saved reviews](05-mcf-import.md) | MCF-02, MCF-04 | Import service/API |
| [x] | MCF-06 | [Generalize export state](06-format-export-state.md) | MCF-01, MCF-04 | EDL/generic provenance |
| [x] | MCF-07 | [Export reviews to MCF](07-mcf-export.md) | MCF-02, MCF-04, MCF-06 | MCF export lifecycle |
| [x] | MCF-08 | [Add frontend contracts/settings](08-frontend-foundation.md) | MCF-03–07 | Web foundation |
| [x] | MCF-09 | [Build review and manual-filter UI](09-review-mcf-ui.md) | MCF-08 | Metadata/filter UX |
| [x] | MCF-10 | [Build import, export, and badge UI](10-import-export-ui.md) | MCF-09 | End-to-end format UX |
| [x] | MCF-11 | [Finish core integration](11-final-integration.md) | MCF-05, MCF-07, MCF-10 | Docs/full suite |
| [x] | MCF-12 | [Improve AI subcategorization](12-ai-subcategorization.md) | MCF-11 | Model mapping/evaluation |
| [x] | MCF-13 | [Build tiered Review finding tables](13-tiered-review-tables.md) | MCF-12 | Review result hierarchy/sorting |

Default to sequential implementation. MCF-02 can proceed independently after MCF-00.
MCF-03 may proceed while MCF-02 runs if files and tests do not overlap. Do not begin
frontend contract work until MCF-03 through MCF-07 have frozen implemented response
shapes. Do not remove EDL compatibility fields before MCF-10 proves all UI consumers use
the generic shape.

## Checkpoints

| Checkpoint | Tasks | Safe exit state |
| --- | --- | --- |
| 0 - contract | MCF-00 | Exact fields, routes, revisions, rules, errors, and baseline recorded |
| 1 - foundation | MCF-01–03 | Fresh schema, strict codec, and shared defaults pass focused tests |
| 2 - backend workflows | MCF-04–07 | Review/import/export and independent provenance are complete |
| 3 - frontend | MCF-08–10 | Every source has complete MCF settings/review/import/export UX |
| 4 - core ready | MCF-11 | Adapters removed, docs/OpenAPI current, core verification passes |
| 5 - richer AI classification | MCF-12 | AI findings use specific subcategories where reliable |
| 6 - tiered Review results | MCF-13 | Review hierarchies and independent column sorting pass UI gates |

## Database and filesystem safety

MCF-01 alone owns the approved development reset. It must resolve and print the exact
database target, verify it is inside this checkout's development data and not used by a
running or installed service, then obtain/record the already granted plan approval before
recreation. Never delete an installed database, path outside the checkout, unexpected
environment override, media file, or real sidecar. All tests use temporary databases,
media, and sidecars.

## Shared verification

- Python tasks run Ruff on changed files and focused pytest with temporary data.
- TypeScript production changes run focused Vitest and `pnpm build` in `web/`.
- API changes assert live OpenAPI and authentication/CSRF coverage.
- File lifecycle tests prove atomic replacement and rollback for each format.
- UI tasks cover keyboard, 320px responsive behavior, and non-color-only status.
- Every task runs `git diff --check` and records exact commands/counts.
- Unrelated failures are reported, not fixed or hidden.

## Definition of done

- Every acceptance criterion is checked with evidence.
- No test is weakened to permit a regression.
- Existing EDL import/export and saved-review behavior remains passing.
- No live service, user database, real catalog credential, media, or sidecar is used.
- The assigned task handoff and this checklist are current.
- Any needed product decision marks the task `BLOCKED`; it is not guessed.

## Required handoff

Replace the task's handoff placeholder at every blocked exit or completion:

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
