# CSS-01 — Freeze the supported unversioned schema fixture

Status: **DONE** (September 26, 2026). The supported unversioned specimen is pinned
in [unversioned-v1.json](../../tests/fixtures/community/unversioned-v1.json).

- Prerequisites: [CSS-00](00-repository-preflight.md)
- Decision gates: None beyond the shared rules.
- Parent sections: implementation §1.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

D14 supports only the verified current unversioned schema. Schema detection must compare more than column names and must never infer history from a version string.

## Starting points

scanner/db.py; scanner/persistence/rows/; tests/review_schema_support.py; new migration fixtures.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Create a sanitized representative database using current models and explicit fixture data: scans, reviews, findings, selections, retiming, settings, admin records.
2. Record normalized tables, types, constraints, indexes, defaults, foreign keys, and required seeds in a versioned baseline specification.
3. Create unknown/older/future/corrupt fixture variants; use generated or copied test data only.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Fixture captures real model shape and representative relational data without production secrets.
- [x] Changing an index, constraint, table, or required column is detected; fixture reconstruction is reproducible.
- [x] Legacy scans retain unknown provenance and stable identifiers in the fixture.

## Verification

- Add fixture-integrity and schema-comparison tests using file-backed SQLite.
- Run existing review/auth schema tests; record schema fingerprint and fixture creation command.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide baseline fingerprint, seed inventory, fixture paths, and unsupported-shape examples to CSS-04/05.

## Handoff summary for the next task

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: Built a synthetic file-backed SQLite
database with the current `Base.metadata.create_all` path and its required seeds.
Pinned structured reflection of all 24 tables, columns/types/nullability/defaults,
primary/unique/check/foreign-key constraints and indexes, plus seed inventory, in
[unversioned-v1.json](../../tests/fixtures/community/unversioned-v1.json). File-backed
tests compare the specimen to that frozen baseline, verify relational integrity and
reject index, constraint, column, table, seed and corrupt-file variants.

Files changed and relevant revision: `tests/community_schema_support.py`,
`tests/community_schema_fixture.py`, `tests/test_community_schema_baseline.py`,
`tests/fixtures/community/unversioned-v1.json`, this brief and the task index;
base revision `a84aae65dd27fb325ca26cadd84bdf0a83a34324` (CSS-00 handoff).

Contracts / APIs / schemas / migrations / fixtures introduced: Test-only
`unversioned-v1` schema and seed specification. Its SHA-256 over sorted compact JSON
is `b8c923bc2fcf94e19e484581e67b10aa24108823c0736268e81535bf1bc00e3d`.
Generate a disposable specimen and comparison specification from the checkout root:

```bash
fixture_dir=$(mktemp -d)
.venv/bin/python -m tests.community_schema_fixture "$fixture_dir/unversioned.db" --spec "$fixture_dir/unversioned-v1.json"
cmp tests/fixtures/community/unversioned-v1.json "$fixture_dir/unversioned-v1.json"
```

The only required seeded rows are `filter_settings` singleton `(1, 0)` and 97
ordered `mcf_severity_defaults` rows at `medium`. Representative fixture data has
synthetic admin/auth settings/session, connection settings without API keys, one
selection rule, media item, stable `legacy-scan-001` (number 7), unclassified finding,
saved review `review-legacy-001`, linked membership, unselected review filter and
file-end synchronization. No provenance field is invented for the legacy scan.
No application schema, migration, public API, real DB or credential changed.

Decisions used (gate IDs and recorded answers): D14 accepts only the verified current
unversioned shape and rejects unknown/older ones; no additional CSS-01 gate.

Verification commands and results: `.venv/bin/python -m pytest -q
tests/test_community_schema_baseline.py tests/test_auth_schema.py
tests/test_review_schema_database.py` passed 48 tests; the standalone baseline tests
passed 10 after adding the missing-column case. `.venv/bin/ruff check
tests/community_schema_support.py tests/community_schema_fixture.py
tests/test_community_schema_baseline.py` passed. Fixture generation and JSON parsing
passed on a new temporary SQLite file; a fresh generation compared byte-for-byte
equal to the pinned JSON. `git diff --check` passed.

Known failures, limitations, unavailable checks, and recovery notes: An initial
assertion expected `Medium`, but SQLite stores `medium`; corrected and rerun. No
remaining focused failures or unavailable tools. Full suites and platform checks were
not run. Variant tests create a missing index/constraint/required column (older or
unknown), an extra column/table (future or unknown), missing seed and invalid SQLite
bytes (corrupt). These are generated only under test temporary paths. CSS-05 must
validate integrity and foreign keys independently of the schema fingerprint before
adopting an unversioned file; this task adds no production adoption classifier.

Remaining work in this task: None.

Next task ID and exact first action: CSS-04 can read
[04-alembic-fresh-database.md](04-alembic-fresh-database.md) and build its fresh
migration chain against this pinned final schema and seed inventory. CSS-03 can read
[03-safety-snapshots.md](03-safety-snapshots.md) and this handoff before implementing
recoverable snapshots. CSS-05 must use both handoffs before accepting any unversioned
database; never stamp an unknown shape.

Direct dependent tasks: [CSS-03](03-safety-snapshots.md), [CSS-04](04-alembic-fresh-database.md).

