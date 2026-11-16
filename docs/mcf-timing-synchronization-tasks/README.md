# MCF timing synchronization coding tasks

Parent: [MCF timing synchronization and credits detection plan](../mcf-timing-synchronization-plan.md)

Status: **MTS-00 through MTS-07 DONE**

This pack turns the approved parent design into dependency-ordered implementation
assignments. Before changing code, read this README, the parent plan, repository
`AGENTS.md`, the assigned task, and every prerequisite handoff.

## Assignment prompt

Assign exactly one task with this prompt:

> Implement MTS-XX from `docs/mcf-timing-synchronization-tasks/XX-NAME.md`. Read that
> task, the task-pack README, parent plan, repository AGENTS.md, and all prerequisite
> handoffs. Work only within the assigned scope, preserve unrelated changes, meet every
> acceptance criterion and definition-of-done item, update the task handoff and README
> checklist, and return a concise checkpoint summary. Do not begin a dependent task, use
> or wipe an installed/user database, start or stop a user's Editarr instance, touch real
> sidecars/media, acquire a model without the approved consent flow, or make a new product
> decision silently.

Allowed statuses are `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, and `DONE`. A dependency is
complete only when it is `DONE` with reproducible passing evidence.

## Frozen product direction

The parent plan is authoritative. In particular:

1. Retiming is the upstream affine endpoint mapping. It changes cue times only and never
   clamps, merges, splits, reorders, classifies, selects, or changes actions.
2. Imported scan bounds and cue times remain immutable source evidence. Only initial review
   snapshots are mapped to the selected target interval.
3. Review retiming maps all current draft filters, including disabled and staged manual
   filters, from the review's current synchronization interval.
4. Target END mode is `before_credits` or `file_end`; Before credits is the import default
   and starts blank, while File end prefills trusted duration and remains editable.
5. Review synchronization bounds are distinct from canonical MCF material bounds. MCF
   export always requires a before-credits END.
6. Credits detection is a cancellable normal video scan with best-candidate output,
   confidence, a short boundary clip, explicit acceptance, and persisted provenance.
7. Detection uses chapters and tail visual/text analysis everywhere, with optional Qwen.
   Missing Qwen acquisition requires an explicit disclosed confirmation.
8. An accepted result creates a selected video Skip from credits start to file end using
   `dispensable_scenes/dispensable`, description/comment `End Credits`, detector
   confidence, video channel, and the configured `dispensable` severity default.
9. Acceptance automatically adds the credits scan and finding snapshot to the invoking
   import/review draft. Canceling or discarding the draft does not delete normal scan
   history.
10. The user approved a fresh checkout development database for the schema change, without
    a preservation migration. Installed/user data and databases outside the checkout are
    excluded.

## Code map

- `scanner/mcf.py`: strict codec plus pure affine timing helpers.
- `scanner/db.py`: review timing/provenance and credits scan persistence.
- `scanner/models.py`: Python/API contracts and validation.
- `scanner/repository.py`: import/review transactions, snapshots, revisions, scan results.
- `scanner/api.py`: trusted target resolution, protected import/retime/detection/clip routes.
- `scanner/worker.py`, `scanner/process_control.py`: queued execution and cancellation.
- `scanner/pipeline/frames.py`, `scenes.py`, `cache.py`: runtime, frame, scene, and cache
  primitives reused by the detector.
- `scanner/pipeline/qwen_summary.py`: optional Qwen capability/acquisition patterns; do not
  route credits only through safety-flagged segments.
- `web/src/api.ts`: frontend timing, detection, scan, and review contracts.
- `web/src/review-draft.ts`: draft timing, retiming application, dirty state.
- `web/src/review-mcf.tsx`: MCF bounds, provenance, and Detect Credits entry point.
- `web/src/App.tsx`: import, review adjustment, scan progress, consent, and acceptance UI.
- `tests/`, `web/src/*.test.ts*`, `web/e2e/`: focused and end-to-end authorities.

## Master checklist

| Done | ID | Task | Prerequisites | Primary ownership |
| --- | --- | --- | --- | --- |
| [x] | MTS-00 | [Freeze contracts and baseline](00-contract-baseline.md) | None | Contracts/tests only |
| [x] | MTS-01 | [Build timing persistence and affine core](01-timing-foundation.md) | MTS-00 | Fresh schema/models/pure math |
| [x] | MTS-02 | [Synchronize MCF imports](02-import-synchronization.md) | MTS-01 | Import service/API |
| [x] | MTS-03 | [Add saved-review retiming](03-review-retiming.md) | MTS-01 | Review draft operation/revisions/export |
| [x] | MTS-04 | [Evaluate and build credits detection](04-credits-detector.md) | MTS-00 | Detector/evaluation/cache |
| [x] | MTS-04A | [Evaluate third-party credits detectors](04a-third-party-detector-evaluation.md) | MTS-04 | Library/model research, benchmark, selection |
| [x] | MTS-05 | [Integrate credits scans and previews](05-credits-scan-integration.md) | MTS-01, MTS-04A | Queue/API/findings/review integration |
| [x] | MTS-06 | [Build timing and credits UI](06-frontend-workflows.md) | MTS-02, MTS-03, MTS-05 | Import/review/detection UX |
| [x] | MTS-07 | [Finish integration and release evidence](07-final-integration.md) | MTS-06 | Full regression/docs/packaging |

Default to sequential implementation. MTS-04 may proceed in parallel with MTS-01 through
MTS-03 after MTS-00 freezes contracts, provided files and tests do not overlap. Do not begin
frontend production work until backend response shapes are implemented. Do not fold credits
detection into the existing safety-detector/Qwen enrichment path merely to reduce task count.

## Checkpoints

| Checkpoint | Tasks | Safe exit state |
| --- | --- | --- |
| 0 - contract | MTS-00 | Exact fields, routes, revisions, errors, evidence, and baseline frozen |
| 1 - timing backend | MTS-01-MTS-03 | Fresh schema, shared affine core, import, review, and export behavior pass |
| 2 - detector backend | MTS-04, MTS-04A, MTS-05 | Selected evaluated detector runs as a normal cancellable scan with safe preview/integration |
| 3 - frontend | MTS-06 | Catalog and Other Files workflows are complete and accessible |
| 4 - release ready | MTS-07 | Full regression, packaging, docs, and real-model evidence are recorded |

## Database and filesystem safety

MTS-01 alone owns the approved development reset. It must resolve and print the exact
database target, verify it is checkout-owned disposable development data, verify no process
is using it, and record the approval from the parent plan before recreation. Never delete an
installed/user database, a path outside the checkout, an unexpected environment override,
media, sidecars, model artifacts, or cached user data.

All automated tests use temporary databases and generated or redistributable fixture media.
No task may point tests at a running user's Editarr instance. Credits clips and detector
workspaces are disposable and ownership-scoped.

## Shared verification

- Python tasks run Ruff on changed files and focused pytest with temporary data.
- TypeScript production changes run focused Vitest and `pnpm build` in `web/`.
- API changes assert live OpenAPI plus authentication, CSRF, and safe-error coverage.
- Queue/model tasks cover cancellation, restart, process cleanup, capability truthfulness,
  cache invalidation, declined acquisition, and failed acquisition.
- UI tasks cover keyboard use, focus restore, 320px responsive behavior, non-color-only
  confidence/warnings, and bounded playable clips.
- Integration tasks preserve MCF/EDL import/export, review revisions, defaults, scan history,
  installed-release verification, and native shared-contract coverage.
- Every task runs `git diff --check` and records exact commands/counts.
- Unrelated failures are reported, not fixed or hidden.

## Definition of done

- Every acceptance criterion is checked with reproducible evidence.
- No test is weakened to permit a regression.
- Imported source evidence remains immutable and every retiming path uses the same formula.
- Credits detection never changes a draft without explicit acceptance.
- Qwen remains optional and cannot download without informed confirmation.
- Existing EDL/MCF, review, scan, auth, runtime, and release behavior remains passing.
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