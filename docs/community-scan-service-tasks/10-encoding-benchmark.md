# CSS-10 — Measure JSON versus Protobuf before choosing storage encoding

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Current storage: [D1-only storage](d1-storage-scope.md), updated October 3, 2026.
> Dated completion evidence below may describe the earlier storage implementation.

Status: **DONE** (September 26, 2026). Synthetic Python and local workerd
benchmarks are recorded; the user subsequently selected JSON for D1 and gzip
JSON for D1 artifact BLOBs. Hosted CPU and accepted upload limits remain open.

- Prerequisites: [CSS-09](09-execution-provenance.md), [CSS-22](22-worker-scaffold.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §4.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

JSON was provisional during the experiment. After reviewing the measured D1/R2
tradeoffs, the user selected uncompressed JSON text for D1 serialized summaries
and gzip JSON for immutable D1 artifact BLOBs. This does not approve a size cap.

## Starting points

contracts/community/ experimental fixtures; new benchmark tooling; docs/community-scan-service-tasks/decision-evidence/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Build sanitized small/large/zero/partial/manual/derived fixtures and equivalent experimental JSON/Protobuf encodings.
2. Measure raw and compressed bytes, encode/decode CPU/memory, Python/TypeScript tooling, evolution and exact-byte signing implications.
3. Measure D1 serialized summary costs separately from compressed D1 artifact BLOBs; retain indexed searchable columns and present a recommendation for G-ENCODING.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Reproducible report includes fixture sizes, commands, runtime versions and tradeoffs, not only one favorable sample.
- [x] No production codec/schema choice is made by the experiment.
- [x] Result identifies whether a bounded upload fits the Worker CPU target or needs a user scope decision.

## Verification

- Run benchmark twice for reproducibility; prove semantic parity across experimental codecs.
- Keep hosted CPU figures marked unverified unless measured on an explicitly authorized free-plan test deployment.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Supply the report, fixtures, candidate limits,
      and the recorded G-ENCODING question and user answer to CSS-11.

## Handoff summary for the next task

JSON remains selected. The current Worker benchmark stores summary and compressed
artifact candidates in D1. Historical vector keys named `r2_body` remain fixture
labels and do not require a bucket.
See [current scope, schema and verification](d1-storage-scope.md).

## Historical completion evidence

Status / date / implementer: DONE, September 26, 2026, GitHub Copilot.

Implemented outcome and last completed substep: [Local benchmark report](decision-evidence/10-encoding-benchmark.md)
records six synthetic fixtures, typed Protobuf and JSON parity, raw/gzip bytes,
local CPU/memory, indexed SQLite summary proxy, reproducible commands, signing
and schema-evolution risks, and a provisional recommendation/question. Two
independent Python benchmark runs produced identical byte counts; two local
workerd test runs agree on all cross-language wire hashes. The user's subsequent
G-ENCODING choice is recorded in [decisions](decisions.md#g-encoding).

Files changed and relevant revision: `scripts/benchmark_community_encoding.py`,
`tests/test_community_encoding_benchmark.py`, `community-service/test/{encoding-experiment.test.ts,encoding-fixtures.ts,fixtures/}`,
`community-service/{package.json,pnpm-lock.yaml}`, this brief, task index and
report linked above; no migration, production schema, protocol or release bump.
Buf Protobuf 2.11.0 is pinned as a test-only dependency.

Contracts / APIs / schemas / migrations / fixtures introduced: None for production.
The Python dynamic proto3 descriptor, exported vector JSON and six matching
TypeScript fixtures are experimental only; they preserve null provenance,
mixed attempts, cache reuse and authored lineage. The local D1/R2 probe
creates a test-only summary table inside its isolated binding.

Decisions used (gate IDs and recorded answers): D16/D23 unknown provenance and
authored scans preserved. G-ENCODING: user selected D1 JSON text with indexed
searchable fields and gzip JSON bodies in private R2 on September 26, 2026.
G-LIMITS and the free-plan CPU/scope choice remain open; the experiment's
candidate bounds are not approved production limits.

Verification commands and results: `.venv/bin/pytest -q tests/test_community_encoding_benchmark.py`
passed 9; `.venv/bin/ruff check scripts/benchmark_community_encoding.py tests/test_community_encoding_benchmark.py`
passed. `.venv/bin/python scripts/benchmark_community_encoding.py --iterations 20`
ran twice; all 12 size and SQLite rows matched. `pnpm --dir community-service run
typecheck` passed; `pnpm --dir community-service exec vitest run
test/encoding-experiment.test.ts --reporter=verbose --silent=false` passed
13 tests on two runs with 12 matching Python Protobuf wire hashes and two
candidate wall-time measurements. `pnpm --dir community-service run test`
passed all 16 Worker tests; `pnpm --dir community-service run build` passed a
local dry-run bundle; `git diff --check` passed. Full numbers and versions are
in the report.

Known failures, limitations, unavailable checks, and recovery notes: CSS-22
is DONE. `protobufjs` dynamic codegen was incompatible with workerd and was
replaced with the pinned Buf test runtime. No hosted D1/R2 billing or
free-plan CPU measurement: local wall time cannot prove the candidate fits
the 10 ms Worker target. Test validation covers synthetic shape, not the
future CSS-11 production artifact contract; TS memory is unmeasured.
Fixture compression is optimistic for real user text; long high-entropy notes
at the same event count exceed the test bound, so CSS-11 needs text caps.
No personal DB, real media, hosted resource or model was touched.

Remaining work in this task: None. The format selection is recorded; a
scope/hosted CPU choice and approved resource limits remain necessary before
setting a production upload cap.

Next task ID and exact first action: CSS-11 can define the versioned JSON
artifact/summary contract and exact gzip/signature byte profile. Before
setting a production upload limit, resolve the separate CPU/scope and
G-LIMITS questions with evidence; do not adopt the test-only benchmark bounds.

Direct dependent tasks: [CSS-11](11-artifact-contract.md).

