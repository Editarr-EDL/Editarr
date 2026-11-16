# CSS-08 — Add migration-backed scan provenance and coverage fields

Status: **DONE** (September 26, 2026). Migration, nullable provenance and confidence,
stage coverage contracts, and legacy upgrade tests are implemented.

- Prerequisites: [CSS-06](06-migration-startup-cli.md), [CSS-07](07-version-authority.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §2; contracts §4–5.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Existing confidence is non-nullable and provenance incomplete. Manual/imported evidence must support unknown values without pretending current models generated it.

## Starting points

scanner/persistence/rows/db_scans.py; focused modeling modules; migration revisions; scan/review API serializers.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add explicit version/model/stage/coverage provenance and nullable-or-explicit-unknown confidence representation appropriate to current schema.
2. Preserve historical values and label missing old provenance unknown; update affected API/types/renderers only enough to support representation.
3. Add schema constraints for valid coverage/source states and backward-compatible readers.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Known old data survives migration and keeps UUIDs/values; unknown data is not fabricated.
- [x] Manual findings, zero-finding scans, partial/failed/disabled stages, and legacy imports are representable.
- [x] Existing review/export workflows tolerate unknown confidence without exceptions.

## Verification

- Test upgrade/fresh parity plus existing scan/review/MCF contracts.
- Test API serialization and one frontend unknown-provenance rendering case.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide migration ID, provenance/coverage DTOs, unknown semantics, and downstream serializer changes to CSS-09/37/44.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Added Alembic
`0002_scan_provenance` after the immutable CSS-01 baseline. Existing scan/review
confidence and UUIDs survive upgrade unchanged. Fresh and upgraded schemas match;
the legacy synthetic fixture now uses the frozen baseline recipe rather than live
ORM metadata. Manual, EDL and MCF evidence lacking a model score uses null rather
than synthetic 1.0. Review create/save, scan serialization, frontend types and
options display preserve unknown confidence/coverage.

Files changed and relevant revision: `scanner/persistence/migrations/versions/0002_scan_provenance.py`,
`scanner/persistence/migrations/{adoption,preflight,schema}.py`,
`scanner/persistence/{rows,repository}/`, `scanner/modeling/`, `scanner/models.py`,
`scanner/formats/edl.py`, `scanner/settings/version_info.py`, migration/review
tests and fixture in `tests/`, API and scan/review files in `web/src/`,
`docs/api-contract.md`, this brief and the index. No release version bump;
schema head changed from `0001_current_baseline` to `0002_scan_provenance`.

Contracts / APIs / schemas / migrations / fixtures introduced: `Scan` exposes
nullable release, scanner behavior, build and audio/video model revisions, plus
ordered `stage_coverage`; no old scan receives current binary metadata. Coverage
rows have per-scan `stage` and `execution_id` keys, constrained status and source,
nullable effective version/model ID/revision/digest/quantization/settings, bounded
JSON intervals/settings and optional positive sampling. Separate attempts avoid
overwriting mixed-version execution history. Absence of a coverage row means
unknown, including legacy zero-finding scans; `scan_audio` and `scan_video` are
requested options, never proof of execution. `ScanFinding`/`ReviewFilter` confidence
may be null; credits candidate confidence remains numeric. No stage data is
backfilled; CSS-09 owns capture at execution. Public `GET /api/system/version`
reports schema `0002_scan_provenance`.

Decisions used (gate IDs and recorded answers): Confirmed D06 data-preserving
migrations and D16 independent version identities from the shared context; no
new product decision or destructive reset.

Verification commands and results: `.venv/bin/python -m pytest -q tests`
passed 1,308 tests with the pre-existing Qwen prompt assertion failure described
below. Focused `.venv/bin/python -m pytest -q tests/test_scan_provenance.py`
passed 3 after that run; migration/packaging/review contract subsets passed 48,
schema parity passed, and prior migration/import subset passed 35. `pnpm --dir
web test` passed 140 tests; `pnpm --dir web build` passed. `.venv/bin/ruff
check` on changed Python files passed; `git diff --check` passed.

Known failures, limitations, unavailable checks, and recovery notes: The unchanged
`tests/test_qwen_language.py::test_qwen_language_batches_unique_contexts_and_preserves_finding_fields`
still expects an absent prompt phrase, as recorded in CSS-06/07. No personal DB,
service, model download or deployment was touched. Version-one data upgrades only
under coordinated maintenance with a verified pre-write snapshot and recovery
journal; unknown/future schema admission remains rejected. New stage rows are a
storage/DTO contract, not evidence that an existing scanner ran those stages.

Remaining work in this task: None. Stage execution capture is CSS-09.

Next task ID and exact first action: CSS-09; inspect `scanner/pipeline/runner.py`
and `scanner/persistence/repository/repository_scan_lifecycle.py` at the actual
stage execution path, then persist attempt-scoped effective version/model/settings
and coverage transitions without labeling queued options as completed work.

Direct dependent tasks: [CSS-09](09-execution-provenance.md), [CSS-16](16-encrypted-backup-export.md), [CSS-21](21-local-community-state.md), [CSS-34](34-media-identity.md).

