# Checkpoint 6: documentation and release

Parent design sections: 11 through 14.

## AUTH-21: Update product and API documentation

- [x] Task complete
- Depends on: AUTH-20
- Primary files: `README.md`, `docs/api-contract.md`, `docs/feature-design-spec.md`, this task pack

### Work

1. Update the feature specification so single-administrator authentication is an
   implemented baseline and only multi-user/role functionality remains out of scope.
2. Update the API contract with auth schemas, endpoint access, cookies, CSRF, auth modes,
   trusted proxies, and error semantics.
3. Replace the README's no-auth warning with first-run instructions and HTTPS/reverse
   proxy guidance.
4. Document interactive and `--password-stdin` CLI setup and lockout recovery.
5. Document local-address and external bypass risks, forwarded-header trust, restart
   requirements, and configuration precedence.
6. Update every completed task checkbox and handoff record.

### Definition of done

- A new operator can complete web setup and headless setup from documentation alone.
- Examples do not place passwords on command lines or expose hashes/tokens.
- Documentation matches actual option names, defaults, endpoints, and restart behavior.
- Current native-client limitations are explicit.
- Markdown links resolve locally and `git diff --check` passes.

### Handoff record

```text
Status: DONE
Last completed substep: Updated the operator, API, and feature-baseline documents to
match the implemented single-administrator authentication boundary.
Files changed: README.md; docs/api-contract.md; docs/feature-design-spec.md;
docs/local-authentication-plan.md; docs/local-authentication-tasks/06-release.md;
docs/local-authentication-tasks/README.md.
Documentation verification: `uv run pytest -q tests/test_cli.py tests/test_auth_config.py
tests/test_auth_api.py tests/test_auth_policy.py` passed (69 passed; 5 existing FastAPI/
Starlette deprecation warnings). Local Markdown targets and anchors used by the changed
documents were checked; `git diff --check` passed; changed documents have no trailing
whitespace.
Known limitations recorded: The current Samwise native prototype still calls retired
endpoints and has no setup/login or cookie-session flow; it receives normal 401/423
responses. External mode requires a deployment-owned authentication boundary, and
local-address bypass remains unsafe when untrusted clients can reach qualifying addresses.
Next exact action: Start AUTH-22.
Decisions or deviations from the parent plan: None. Documentation records the approved
public-origin, CSRF, trusted-proxy, CLI, and restart contracts without changing them.
```

## AUTH-22: Run the release verification and audit

- [x] Task complete
- Depends on: AUTH-21
- Primary files: only fixes required by verification; task records

### Work

1. Start from a clean database and manually verify first-run setup before any protected
   action can run.
2. Verify forms enabled, disabled-for-local-addresses, and external modes with the
   documented restart boundaries.
3. Exercise CLI create/reset/recovery against a disposable database.
4. Run the full project verification commands from the parent plan.
5. Review `git diff`, `git diff --check`, and `git status --short`; preserve unrelated
   worktree changes.
6. Search changed files for canary passwords, raw session tokens, TODO placeholders, test
   bypasses, and accidental unauthenticated routes.
7. Record the final command results and remaining operational caveats.

### Definition of done

- Every item in the parent plan's definition of done is demonstrated by an automated
  test or recorded manual check.
- Backend lint/tests/coverage pass.
- Web build/unit/coverage/E2E pass.
- No secret, destructive migration, unrelated file change, or auth bypass is present.
- The master checklist and all handoff records accurately reflect completed work.
- The final handoff names any remaining native-client or deployment limitation.

### Handoff record

```text
Status: DONE
Last completed substep: Corrected the external-method restart boundary by snapshotting
the active method at HTTP startup, while leaving the documented immediately-effective
authentication-required setting live. Completed the release suite, clean disposable
instance checks, CLI recovery checks, and source/database/audit-log review.
Files changed: scanner/auth_settings.py; scanner/auth_policy.py; scanner/auth_api.py;
scanner/api.py; tests/test_auth_api.py; tests/test_auth_policy.py;
tests/test_startup_gate.py; docs/local-authentication-tasks/06-release.md;
docs/local-authentication-tasks/README.md.
Full verification results: Focused `ruff` and auth/startup tests passed (33 passed).
`uv run ruff check scanner tests` passed; `uv run pytest -q` passed (336 passed, 5
FastAPI/Starlette deprecation warnings); `uv run pytest -q --cov=scanner` passed (336
passed, total 72.95%, above the configured 60% minimum). `cd web && pnpm build` passed;
`pnpm test` passed (82 passed); `pnpm test:e2e` passed (30 passed) with its local Vite
server permission; `pnpm test:coverage` passed (74.35% statements). `git diff --check`
passed. The focused route-policy inventory assertion passed, and local Markdown target
checks found zero broken links. An initial disposable CLI attempt using `uv run` could
not access the sandboxed uv cache and performed no CLI operation; the identical checks
were rerun successfully through `.venv/bin/editarr`.
Manual checks: On a fresh disposable SQLite database, HTTP startup reported only
`setup_required` and returned `423 setup_required` for `/api/capabilities`; web/API
setup, CSRF-protected logout, forms-enabled post-setup `401` gating, local-address
bypass, external mode, and CLI password-reset recovery all passed. A second disposable
database exercised CLI status, stdin creation, both required modes, both methods,
password reset, rename, and final status; its audit log was owner-only (`0600`). A
two-process disposable-instance check proved that `auth set-method external` leaves the
running process in forms mode and becomes active only after a fresh process starts.
Security audit result: No migrations were added, and unrelated worktree changes remain
untouched. A production/documentation canary scan found none. The clean
database stored an Argon2id password hash and SHA-256 session digest only; the encrypted
CSRF ciphertext column is expected by the approved design. Four audit records had no
sensitive-field names. Diff/stat/status review found only the authentication work and
the preserved unrelated untracked file.
Known failures or risks: Native clients still have no setup/login or cookie-session flow;
external deployments must supply their own complete boundary; local bypass remains unsafe
if untrusted clients can reach qualifying addresses. The `external` method now takes
effect only after restart as documented.
Next exact action: Deliver the completed implementation summary.
Decisions or deviations from the parent plan: None. The approved restart-only contract
was enforced without revising the plan or documentation.
```
