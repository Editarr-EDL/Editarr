# Local authentication implementation tasks

> **Superseded.** JF-06 removed local passwords. See
> [Remove local password accounts (JF-06)](../jellyfin-only-plan.md#remove-local-password-accounts-jf-06).
> These tasks are a historical record.

Parent design: [Local administrator authentication plan](../local-authentication-plan.md)

Status: **Complete — all local-authentication checkpoints and release verification pass**

This task pack breaks the authentication plan into bounded coding sessions suitable for
a Terra High model session or a less capable session. Complete tasks in dependency order
unless a task explicitly says it can run in parallel.

## Progress notation

- `[ ]` not started
- `[x]` definition of done satisfied and verification recorded
- Add `IN PROGRESS — <date/session>` immediately below a task heading while working.
- Add `BLOCKED — <reason>` without checking the task when work cannot continue.

Do not check a task merely because code was written. Its focused verification and every
acceptance item must pass. Preserve unrelated worktree changes.

## Master checklist

### Checkpoint 0 — authorization and baseline

- [x] [AUTH-00: Confirm the v1 database reset](00-preflight.md#auth-00-confirm-the-v1-database-reset)
- [x] [AUTH-01: Record the pre-change baseline](00-preflight.md#auth-01-record-the-pre-change-baseline)

### Checkpoint 1 — persistence foundation

- [x] [AUTH-02: Add authentication dependencies and configuration](01-persistence.md#auth-02-add-authentication-dependencies-and-configuration)
- [x] [AUTH-03: Add administrator and session tables](01-persistence.md#auth-03-add-administrator-and-session-tables)
- [x] [AUTH-04: Implement administrator repository operations](01-persistence.md#auth-04-implement-administrator-repository-operations)
- [x] [AUTH-05: Implement session repository operations](01-persistence.md#auth-05-implement-session-repository-operations)

### Checkpoint 2 — backend authentication and policy

- [x] [AUTH-06: Implement password and authentication services](02-backend-policy.md#auth-06-implement-password-and-authentication-services)
- [x] [AUTH-07: Implement client-address and trusted-proxy classification](02-backend-policy.md#auth-07-implement-client-address-and-trusted-proxy-classification)
- [x] [AUTH-08: Implement authentication endpoints](02-backend-policy.md#auth-08-implement-authentication-endpoints)
- [x] [AUTH-09: Enforce the global route gate and CSRF policy](02-backend-policy.md#auth-09-enforce-the-global-route-gate-and-csrf-policy)
- [x] [AUTH-10: Gate startup recovery and scheduling](02-backend-policy.md#auth-10-gate-startup-recovery-and-scheduling)

### Checkpoint 3 — headless setup and recovery

- [x] [AUTH-11: Add administrator CLI commands](03-cli.md#auth-11-add-administrator-cli-commands)
- [x] [AUTH-12: Add authentication-mode CLI commands](03-cli.md#auth-12-add-authentication-mode-cli-commands)

### Checkpoint 4 — web authentication boundary

- [x] [AUTH-13: Centralize authenticated web API requests](04-web.md#auth-13-centralize-authenticated-web-api-requests)
- [x] [AUTH-14: Add the top-level authentication gate](04-web.md#auth-14-add-the-top-level-authentication-gate)
- [x] [AUTH-15: Build first-run administrator setup](04-web.md#auth-15-build-first-run-administrator-setup)
- [x] [AUTH-16: Build the forms login experience](04-web.md#auth-16-build-the-forms-login-experience)
- [x] [AUTH-17: Add account, security, and logout controls](04-web.md#auth-17-add-account-security-and-logout-controls)

### Checkpoint 5 — adversarial and browser verification

- [x] [AUTH-18: Complete backend security and route-matrix tests](05-verification.md#auth-18-complete-backend-security-and-route-matrix-tests)
- [x] [AUTH-19: Complete React authentication tests](05-verification.md#auth-19-complete-react-authentication-tests)
- [x] [AUTH-20: Complete browser, accessibility, and responsive tests](05-verification.md#auth-20-complete-browser-accessibility-and-responsive-tests)

### Checkpoint 6 — documentation and release

- [x] [AUTH-21: Update product and API documentation](06-release.md#auth-21-update-product-and-api-documentation)
- [x] [AUTH-22: Run the release verification and audit](06-release.md#auth-22-run-the-release-verification-and-audit)

## Dependency map

```text
AUTH-00 -> AUTH-01 -> AUTH-02 -> AUTH-03 -> AUTH-04 --\
                                       -> AUTH-05 ----+-> AUTH-06 --\
AUTH-02 -> AUTH-07 ------------------------------------+-> AUTH-08 -> AUTH-09 -> AUTH-10
AUTH-06 + AUTH-10 -> AUTH-11 -> AUTH-12
AUTH-08 + AUTH-09 -> AUTH-13 -> AUTH-14 -> AUTH-15 -> AUTH-16 -> AUTH-17
Backend + CLI + web tasks -> AUTH-18 -> AUTH-19 -> AUTH-20 -> AUTH-21 -> AUTH-22
```

AUTH-04 and AUTH-05 can be assigned independently after AUTH-03. AUTH-07 can begin
after AUTH-02 and run alongside the persistence tasks when their edits do not overlap.
Avoid parallel edits to `scanner/api.py`, `scanner/cli.py`, `web/src/App.tsx`, or
`web/src/api.ts`.

## Checkpoint exit criteria

| Checkpoint | Safe handoff state |
| --- | --- |
| 0 | Database reset explicitly approved; baseline commands and existing failures recorded. |
| 1 | New tables and repositories exist; focused persistence tests pass; no HTTP behavior changed. |
| 2 | API setup/login/session/modes and startup gate work; backend focused tests pass. |
| 3 | An administrator and auth modes can be managed safely without a browser. |
| 4 | Protected React UI never mounts before authorization; setup/login/settings flows work in component tests. |
| 5 | Adversarial API cases and full browser journeys pass, including axe and narrow viewports. |
| 6 | Contracts and operator docs match implementation; complete release suite passes. |

## Required end-of-session handoff record

At every interruption or completed task, update the relevant task file and leave this
block in the task's **Handoff record** section:

```text
Status: NOT STARTED | IN PROGRESS | BLOCKED | DONE
Last completed substep:
Files changed:
Verification run and result:
Known failures or risks:
Next exact action:
Decisions or deviations from the parent plan:
```

If implementation reveals a product decision not resolved by the parent plan, stop at a
checkpoint, record the question, and ask the user. Do not silently create a fourth auth
mode, a second user type, or an unauthenticated compatibility route.
