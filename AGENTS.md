# Development guidance

Database schema changes require versioned, data-preserving migrations. Include
upgrade tests from supported schema versions and verify a recoverable backup before
applying migrations to an existing database. Never wipe a database or silently stamp
an unknown schema. A destructive development reset requires an explicit request for
that specific reset and is not a substitute for a migration.

## Code organization

Keep `scanner/` limited to application and worker entry points and the stable
import surfaces (`api.py`, `db.py`, `repository.py`, `models.py`, `mcf.py`, and
`model_acquisition.py`). Put implementations in the existing feature packages:
`http`, `security`, `settings`, `integrations`, `runtime`, `credits`,
`persistence`, `formats`, `modeling`, `evaluation`, `pipeline`, `commands`, or
`testing`. Add a focused subpackage when none of these fits. Use local imports
between modules in the same package, and update entry points and tests when
moving modules.

Keep frontend code in the matching `web/src/` feature folder: `api`, `auth`,
`catalog`, `review`, `scans`, or `settings`. Put genuinely shared helpers in
`shared`, reusable visual primitives in `ui`, and CSS in `styles`. Keep unit
tests beside the feature, application integration tests in `web/src/test/`, and
browser tests in `web/e2e/`.

## Maintainability

Keep each file focused on one responsibility. Aim for fewer than about 400
lines; when a file approaches 500 lines, split it along a clear feature or
workflow boundary before extending it further. Keep entry points and public
facades thin, and avoid creating a new catch-all module.

Reuse existing services, functions, hooks, and components for the same behavior.
When related views need similar controls, state handling, validation, or API
calls, put the shared behavior in the appropriate feature or shared module and
use it consistently. Preserve the established public behavior while refactoring.

Keep comments and docstrings concise and accurate for the current code. Give
non-obvious functions a short docstring describing their purpose, important
parameters, and usage. Cover changed major behavior with appropriate unit or
integration tests; check responsive layouts and interaction states when changing
the UI.
