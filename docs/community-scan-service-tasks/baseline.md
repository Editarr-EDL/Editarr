# CSS-00 repository baseline

Captured September 26, 2026 at revision `a84aae65dd27fb325ca26cadd84bdf0a83a34324`.
The worktree was clean before CSS-00; no unrelated changes were reported. CSS-00
changed only repository guidance and task documentation. No database, credentials,
media, user data, external service, or schema was changed; no earlier development
reset approval was reused.

## Runtimes and verification commands

The development requirements in [README.md](../../README.md) are Python 3.11+,
Node.js 20+, uv, and Corepack. This macOS checkout has a configured local Python
3.11 virtual environment with pytest 9.1.1, Node.js v26.7.0, pnpm 11.22.0, and
uv 0.12.5. No dependencies were installed for this baseline.

The README documents `uv run ruff check scanner tests`, `uv run pytest -q`,
`uv run pytest -q --cov=scanner`, and, from `web/`, `pnpm build`, `pnpm test`,
`pnpm test:e2e`, and `pnpm test:coverage`. These full checks were not run for this
documentation-only task.

Focused fixture check (temporary/in-memory databases; no development database):

```text
.venv/bin/python -m pytest -q tests/test_auth_schema.py tests/test_review_schema_database.py tests/test_runtime_paths.py
50 passed in 1.54s
```

After the task edits, the same command passed 50 tests in 1.26s; `git diff
--check` passed, and all six new relative documentation links resolved.

No failures were observed in the focused run. Full backend/frontend/browser suites,
native platform checks, and deployment checks were not run; this is not evidence
that those checks pass. No tool needed for the focused run was unavailable.

## Module boundaries for later tasks

- `scanner/persistence/repository/repository_imports.py`: 577 lines; split along
  import workflow boundaries before extending it for community imports.
- `scanner/persistence/repository/repository_catalog.py`: 374 lines; near the
  400-line target, so avoid growing it into a combined local/remote catalog.
- `web/src/catalog/media-detail.tsx`: 371 lines; extract the Find Filters workflow
  into the appropriate feature folder before extending the view (CSS-47).

The baseline is descriptive, not a supported-schema fingerprint. CSS-01 must
derive that fingerprint from disposable fixtures, without touching a live database.