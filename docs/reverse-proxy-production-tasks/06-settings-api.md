# RP-06 — Expose protected deployment settings APIs

Dependencies: RP-03. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

General Settings needs editable Base URL/Port and display-only inferred path/origin.
Saving must persist overrides without changing running routing/auth or listener.
The frontend must distinguish saved desired values from what is currently active.

## Read and expected touch points

Read RP-01 store/resolver, RP-02 snapshots, RP-03 route policy, scanner/models.py,
auth_api.py, auth_policy.py, api.py, and docs/api-contract.md. Implement the RP-00
settings endpoint contract, preferably in a focused deployment router module.

## Implementation steps

1. Add protected read/update operations with the frozen DTO/error shapes. Return
   active, saved/next-start values, source metadata, restart-needed state, and the
   explicit CLI port override effect. Do not expose unrelated settings or secrets.
2. Treat updates as field-selective: absence means leave untouched. A UI save must
   be able to record Base URL deliberately without forcing unrelated fields.
   Invalid combined updates must not partially persist.
3. Delegate validation/writes to RP-01 and active reads to RP-02; avoid a second parser
   or precedence implementation. Do not accept a separately editable origin/prefix.
4. Preserve existing policy in forms, external and verified-local access modes.
   Use standard source/CSRF checks for cookie-authenticated writes and update the
   explicit route inventory. Do not invent a weaker admin/settings access path.
5. Re-read or resolve pending values after save; report restart necessity based on
   effective runtime differences, including a CLI override that masks saved Port.
6. Report file I/O failures and validation errors without exposing private paths/
   content unnecessarily. Concurrent writes must use the store's protection.
7. Document exact examples/status codes in the API contract and handoff.

## Localhost requirement update

Keep absent configured Base URL distinct from effective local URL. Report
local_default source and resolve next-start local origin from the selected Port.
A Port-only save must not persist the derived URL or require a Base URL input.

## Acceptance criteria

- [x] Read response correctly distinguishes active, saved, and next-start configuration.
- [x] Port-only writes leave Base URL in seed mode until an explicit Base URL save.
- [x] Saving root Base URL overrides an environment prefix and survives a new resolver.
- [x] Save does not change auth origin, API mount, cookie path, or bound port immediately.
- [x] Invalid or failed updates preserve all previously saved values.
- [x] Unauthorized/source/CSRF failures do not mutate the file.
- [x] Response exposes the effect of --port and derived fields accurately.
- [x] New routes are included in policy inventory and contain no sensitive configuration.

## Verification

Add API tests with isolated files/database and active snapshots, exercising root and
prefixed access, auth modes, missing CSRF, wrong Origin, partial updates, explicit root,
I/O failures, and active-versus-pending state. Assert disk and runtime after each case.
Run relevant authentication policy tests.

## Definition of done

Shared DoD applies. Documented DTOs/errors are implemented and tested; RP-07 can
build against real responses. No hot apply or database persistence was introduced.

## Handoff

Status: DONE
Last completed substep: Added the protected staged deployment router, its response
  DTOs, pre-persistence complete-state validation, policy classification, contract
  documentation, and isolated routing/auth/file-failure coverage.
Files changed: scanner/deployment_api.py; scanner/deployment_config.py;
  scanner/models.py; scanner/api.py; scanner/auth_policy.py;
  tests/test_deployment_settings_api.py; docs/api-contract.md;
  docs/reverse-proxy-production-tasks/06-settings-api.md;
  docs/reverse-proxy-production-tasks/README.md (RP-06 checklist only).
Contract/API changes: `GET` and `PUT /api/settings/deployment` are protected,
  application-relative routes. PUT accepts only field-selective
  `{base_url?: string | null, port?: integer | null}`; omission retains a field and
  null clears its explicit override. Both return `DeploymentConfiguration` with
  immutable active and independently resolved next-start snapshot DTOs, nullable
  explicit saved values, runtime-based `restart_required`, and `RestartCapability`.
  Validation returns `422 deployment_configuration_invalid`; unreadable/corrupt/write
  errors return `503 deployment_configuration_unavailable` with no storage path.
  `DeploymentStore.preview_update()` lets the router validate the merged state before
  any write, and `resolve_deployment_snapshot(..., overrides=...)` resolves that state
  through the single RP-01 parser/precedence implementation. An explicit Base URL
  scope change stages only `cookie_scope.pending_previous_path=active.prefix`; no
  cutoff or legacy path is activated or altered here.
Verification commands and results: `uv run ruff check scanner/deployment_api.py
  scanner/deployment_config.py scanner/models.py scanner/api.py scanner/auth_policy.py
  tests/test_deployment_settings_api.py` -> passed; `uv run pytest -q
  tests/test_deployment_settings_api.py tests/test_deployment_config.py
  tests/test_auth_policy.py tests/test_backend_routing.py tests/test_auth_api.py` ->
  71 passed (5 existing FastAPI/Starlette deprecation warnings); `git diff --check`
  -> passed.
Acceptance criteria still unmet: None.
Approvals received or outstanding: None. The existing RP-00 decisions on persistent
  local default, explicit Base URL precedence, controller-only restart, and delayed
  session cutoff were applied without a new architecture decision.
Known failures/limitations: Restart remains truthfully unsupported with
  `controller_unavailable` until RP-08 installs the parent-controller capability and
  system endpoints. The router intentionally does not activate a forms cutoff, append
  legacy paths, clear cookies, bind a port, or reload/mutate the active snapshot.
Next exact action: RP-07 should consume `DeploymentConfiguration` to render active,
  next-start, saved/source metadata and submit field-selective PUT payloads. Do not
  derive URL/path/origin client-side. RP-08 must replace the restart capability with
  controller state and, only after a successful child startup, atomically advance the
  persistent monotonic cutoff, append/de-duplicate the staged path in legacy paths,
  and clear `pending_previous_path`; failed/recovered starts leave saved changes
  pending and never invalidate forms sessions.
