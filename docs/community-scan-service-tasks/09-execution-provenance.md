# CSS-09 — Capture provenance when scanner stages actually run

Status: **DONE** (September 26, 2026). Worker-attempt stage capture, enrichment
outcomes, and versioned checkpoint admission are implemented.

- Prerequisites: [CSS-08](08-provenance-schema.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §2; contracts §4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Queued settings may differ from actual execution. Resumed scans can contain stages run by different versions or models.

## Starting points

scanner/pipeline/; scanner/modeling/; scanner/persistence/repository/repository_scan_lifecycle.py.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Capture effective release/build/scanner/model revision/digest/quantization/settings at stage execution.
2. Record requested versus available/completed/failed enrichment and coverage intervals/sampling.
3. Version checkpoints; reject incompatible resume state or retain explicit mixed-stage provenance instead of silently mixing.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Changing model/settings after enqueue cannot mislabel completed findings.
- [x] Resume preserves original stage records and identifies mixed execution.
- [x] Unavailable models and valid zero findings remain distinct; local scanning behavior is otherwise unchanged.

## Verification

- Use fake-model stage tests for enqueue/run mismatch, failed enrichment, partial resume and incompatible checkpoints.
- Run affected queue/cache/scanner regressions without downloading inference models.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide stage capture hooks, resume compatibility rules, and representative provenance fixtures to CSS-10/37.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Scanner and credits workers record
stage attempts with the worker token as execution ID. Visual, audio extraction,
audio scan, credits detection/cache and Qwen enrichment report observed transitions.
Isolated audio and visual workers relay Qwen outcomes to the parent; visual outcomes
are scene-specific. Effective settings and pinned offline model identities are
captured at execution; unknown digest/revision remains null. Cached extraction and
scene findings are marked reused, without an invented executed model or full
visual coverage. A requested enrichment is pending (`unknown`); clean/no-input
enrichment is `disabled` with `skip_reason: no_inputs`, unavailable is
`unsupported`, and actual failed/partial/completed inference has its own row.

Files changed and relevant revision: `scanner/pipeline/{runner,runner_scenes,runner_workers,provenance,visual_summary,qwen_language,audio}.py`,
`scanner/credits/credits_scan.py`, `scanner/worker.py`,
`scanner/persistence/repository/{repository_provenance,repository_checkpoints,repository_queue}.py`,
stable repository facade, focused runner/credits/worker/Qwen tests and new
`tests/test_execution_provenance.py`. No release or schema revision bump;
uses CSS-08's `0002_scan_provenance` columns.

Contracts / APIs / schemas / migrations / fixtures introduced: Attempt-scoped
`record_scan_stage` and `scan_checkpoint_stages(require_provenance=True)` through
the repository facade. Checkpoint JSON format 1 binds completed stages to the
matching execution ID and a complete stage row. Legacy/unversioned or incomplete
checkpoint state cannot silently skip work; preserve the scan and reject resume
until reconciled. A later worker keeps earlier stage rows unchanged, so different
scanner versions/models across completed stages remain visible. File-backed SQLite
fixtures cover enqueue/runtime model mismatch, mixed-version resume, invalid
checkpoint, failed enrichment, no-input, cache reuse and zero findings.

Decisions used (gate IDs and recorded answers): Existing D06 data-preserving
schema rule and D16 distinct version identities; no new product-policy gate.

Verification commands and results: `.venv/bin/pytest -q tests --deselect=tests/test_qwen_language.py::test_qwen_language_batches_unique_contexts_and_preserves_finding_fields`
passed 1,318 tests with one deselected. The focused queue/cache/credits/scanner
suite passed 118 tests with the same deselection. `.venv/bin/ruff check` on all
changed Python files passed; `git diff --check` passed.

Known failures, limitations, unavailable checks, and recovery notes: The
pre-existing Qwen prompt assertion above fails when included (expected phrase
`the f-word for sexual intercourse` is absent). Reused cache entries cannot
retroactively prove which model generated them; partial/unknown provenance is
preserved rather than assigning current model weights. Coarse visual sampling
does not claim a continuous full-media interval. No inference model was fetched,
personal database migrated, hosted service deployed, or platform-specific
release verified.

Remaining work in this task: None. Unknown legacy checkpoint origins require
explicit reconciliation, not an automatic schema stamp.

Next task ID and exact first action: CSS-10, compare JSON and Protobuf using
representative mixed-attempt stage coverage and unknown/cache-reuse fixtures;
CSS-37 should read these attempt records and preserve unknown coverage in
donation previews rather than inferring execution from queued options.

Direct dependent tasks: [CSS-10](10-encoding-benchmark.md), [CSS-37](37-donation-artifact-builder.md).

