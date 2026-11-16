# CSS-00 — Record the baseline and update database guidance

Status: **DONE** (September 26, 2026). Baseline evidence: [baseline.md](baseline.md).

- Prerequisites: None; start here.
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §1; main §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Current AGENTS.md still describes development database wipes. The user explicitly approved data-preserving migrations going forward; old task packs contain obsolete reset instructions.

## Starting points

AGENTS.md; docs/community-scan-service-tasks/; README.md test commands.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Record repository revision, unrelated changes, supported runtimes, and available test commands without reading secrets.
2. Replace only the development-data guidance with the approved migration policy from the implementation plan; preserve organization rules.
3. Record baseline focused test results and existing failures in baseline.md in this task directory; identify large modules that must be split before extension.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] No database, credentials, media, or user data changed; no old reset authorization reused.
- [x] Baseline separates observed failures from unavailable checks; new guidance requires recoverable snapshots and rejects unknown schemas.

## Verification

- Review AGENTS diff against implementation §1; run representative schema/auth/runtime tests on fixtures.
- Record actual commands from README and installed environments; do not install heavyweight models for a documentation baseline.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide exact baseline revision, test commands/results, known failures, and the new migration rule to CSS-01.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Captured the clean pre-task
revision and fixture test baseline in [baseline.md](baseline.md); replaced obsolete
wipe guidance in AGENTS.md and README.md with the approved migration policy.

Files changed and relevant revision: AGENTS.md, README.md, this brief,
the task index, and baseline.md; base revision
`a84aae65dd27fb325ca26cadd84bdf0a83a34324`.

Contracts / APIs / schemas / migrations / fixtures introduced: None. New rule:
schema changes need versioned, data-preserving migrations, supported-version
upgrade tests, and a recoverable backup before an existing database is upgraded;
never wipe or silently stamp an unknown schema. A destructive development reset
requires an explicit request for that specific reset.

Decisions used (gate IDs and recorded answers): No CSS-00 gate; uses the confirmed
data-preservation requirement and D14 supported-known-schema/unknown-schema refusal.

Verification commands and results: `.venv/bin/python -m pytest -q
tests/test_auth_schema.py tests/test_review_schema_database.py
tests/test_runtime_paths.py` passed 50 tests in 1.54s on fixture/temp databases,
then 50 again in 1.26s after all task edits; `git diff --check` passed. See
[baseline.md](baseline.md) for installed runtimes and documented test commands.

Known failures, limitations, unavailable checks, and recovery notes: No focused
failures or missing tools; full suites, browser, deployment, and native checks were
not run. No database was touched and no recovery is needed. The supported schema
fingerprint is not yet established; do not infer one from this baseline.

Remaining work in this task: None.

Next task ID and exact first action: CSS-01; read
[01-schema-fixtures.md](01-schema-fixtures.md) and this handoff, then inspect
`scanner/db.py` and the row/seed definitions using disposable SQLite fixtures to
freeze the supported unversioned schema without opening a development database.

Direct dependent tasks: [CSS-01](01-schema-fixtures.md), [CSS-02](02-maintenance-coordination.md), [CSS-07](07-version-authority.md), [CSS-12](12-request-signing-profile.md), [CSS-22](22-worker-scaffold.md), [CSS-47](47-find-filters-extraction.md), [CSS-70](70-membership-method-comparison.md).
