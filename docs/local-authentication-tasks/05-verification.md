# Checkpoint 5: adversarial and browser verification

Parent design sections: 12 and 13.

These tasks close coverage gaps after feature tasks. They do not replace the focused
tests required by each earlier task.

## AUTH-18: Complete backend security and route-matrix tests

- [x] Task complete
- Depends on: AUTH-02 through AUTH-12
- Primary files: focused authentication tests and existing API/queue test fixtures

### Work

1. Audit the parent plan's backend verification matrix against implemented tests.
2. Add missing adversarial cases for setup races, session fixation/replay, expiry,
   revocation, CSRF, throttling, proxy spoofing, and auth modes.
3. Iterate every FastAPI route and method to assert its public/protected classification.
4. Update existing API tests to authenticate explicitly rather than adding a global test
   bypass that could mask leaks.
5. Assert setup-gated startup and post-setup worker behavior with deterministic fakes.
6. Search test output, captured logs, database rows, and responses for known canary
   passwords/tokens.

### Definition of done

- Every backend verification bullet in the parent plan has a named automated test.
- Existing endpoint tests fail if authentication middleware is removed.
- No broad test-only production bypass exists.
- `uv run ruff check scanner tests`, `uv run pytest -q`, and coverage pass.
- Any intentional coverage exclusion is documented with rationale.

### Handoff record

```text
Status: DONE
Last completed substep: Audited the parent backend verification matrix, added an
end-to-end access-matrix/replayed-session test, and made the application route inventory
walk included routers plus generated documentation methods. Repaired the concurrent CSRF
key-file publication race with a write-and-fsync temporary file followed by atomic
create-if-absent link publication.
Files changed: scanner/auth_crypto.py; tests/test_auth_policy.py;
docs/local-authentication-tasks/05-verification.md;
docs/local-authentication-tasks/README.md.
Verification run and result: `uv run ruff check scanner tests` passed; focused
`uv run pytest -q tests/test_auth_policy.py tests/test_auth_crypto.py
tests/test_startup_gate.py tests/test_auth_api.py` passed (37 passed); full
`uv run pytest -q` passed (335 passed, 5 existing FastAPI/TestClient deprecation
warnings); `uv run pytest -q --cov=scanner` passed (335 passed); `git diff --check`
passed. A repeated 12-run execution of the former flaky concurrent key-loader test
also passed.
Coverage result: 72.90% total, above the configured 60.0% threshold.
Known failures or risks: None. The only suite output is the pre-existing FastAPI/
TestClient deprecation warnings. The canary search found no test canaries in production
code, documentation, README, or scripts. Existing unrelated changes were preserved.
Next exact action: In a fresh handoff session, start AUTH-19 only.
Decisions or deviations from the parent plan: None. The key-loader fix implements the
approved atomic concurrent-creation requirement; it does not change key format,
permissions, or key-management behavior.
```

## AUTH-19: Complete React authentication tests

- [x] Task complete
- Depends on: AUTH-13 through AUTH-17 and AUTH-18
- Primary files: `web/src/**/*.test.tsx`, web test helpers

### Work

1. Audit setup/login/gate/settings component states against the parent plan.
2. Update existing API mocks to begin in an explicit authenticated state where the test
   is unrelated to auth.
3. Add focused helpers for setup-required, forms unauthenticated, authenticated, local
   bypass, and external states.
4. Test protected poller teardown and stale request handling across logout/session expiry.
5. Assert that auth secrets do not reach storage, URLs, document text, or console output.

### Definition of done

- Every web verification bullet in the parent plan has component coverage or is clearly
  assigned to Playwright.
- Existing feature tests still exercise their original behavior behind realistic auth.
- No mock wildcard accidentally returns success for unknown auth requests.
- `cd web && pnpm test && pnpm test:coverage && pnpm build` passes.

### Handoff record

```text
Status: DONE
Last completed substep: Audited the React authentication matrix; put the existing App
feature suite behind AuthenticationGate with an explicit authenticated status; added
coverage for external and local-bypass presentation, credential-change revocation,
logout poller teardown, secret sinks, and stale status handling after session expiry.
Fixed the stale-response race by invalidating pending status requests when the shared
authentication-state event is received.
Files changed: web/src/App.test.tsx; web/src/api.test.ts;
web/src/auth-gate.tsx; web/src/auth-gate.test.tsx;
web/src/auth-settings.test.tsx; docs/local-authentication-tasks/05-verification.md;
docs/local-authentication-tasks/README.md.
Verification run and result: `cd web && pnpm test` passed (6 files, 82 tests);
`cd web && pnpm test:coverage` passed (6 files, 82 tests); `cd web && pnpm build`
passed; `git diff --check` passed.
Coverage result: 74.53% statements, 66.24% branches, 71.63% functions, and 81.89%
lines. The repository has no configured web coverage threshold.
Known failures or risks: None. App test API doubles reject unknown requests rather than
using a successful wildcard. Test canaries prove passwords and CSRF values do not reach
browser storage, URLs, document text, or console calls. Unrelated worktree changes were
preserved.
Playwright-only cases: AUTH-20 owns the complete browser journey, direct browser API
gate assertion, axe checks, keyboard journeys, 320px layout, and authenticated preview
flow. Its existing focused e2e coverage is not substituted for the final AUTH-20 run.
Next exact action: In a fresh handoff session, start AUTH-20 only.
Decisions or deviations from the parent plan: None. Invalidating a stale status response
after an authoritative expiry/setup event enforces the existing gate contract and does
not change any authentication mode or server protocol.
```

## AUTH-20: Complete browser, accessibility, and responsive tests

- [x] Task complete
- Depends on: AUTH-19
- Primary files: `web/e2e/app.spec.ts`, Playwright helpers/config as needed

### Work

1. Add a fresh-database browser journey: setup, protected app, logout, blocked deep link,
   login, restored route.
2. Add local-bypass and external-mode journeys with clear status indicators.
3. Prove a protected API response cannot be reached before setup with direct browser
   requests as well as UI assertions.
4. Run axe on setup, login, account/security settings, and a representative protected
   view.
5. Exercise keyboard-only submission/focus and 320px layout for setup and login.
6. Verify cookie-backed protected images/previews in at least one authenticated flow if
   practical with the test server.

### Definition of done

- The complete cross-screen journey passes without arbitrary sleeps.
- Tests assert absence of protected UI before authorization, not only eventual redirect.
- New views have no automated axe violations.
- Setup/login remain usable at 320px without horizontal page overflow.
- `cd web && pnpm test:e2e` passes reliably on a clean retry.

### Handoff record

```text
Status: DONE
Last completed substep: Expanded the stateful Playwright authentication harness and
completed the browser verification matrix: a fresh-install setup, protected-request
denial, setup conflict, logout/login deep-link restoration, login failure, session
expiry, local-bypass/external indicators, protected image cookie transport, axe,
keyboard, and 320px no-overflow checks.
Files changed: web/e2e/app.spec.ts; docs/local-authentication-tasks/05-verification.md;
docs/local-authentication-tasks/README.md.
Verification run and result: `cd web && pnpm test:e2e` passed twice on clean retries
(30 passed each); `cd web && pnpm test` passed (6 files, 82 tests); `cd web && pnpm
build` passed; `cd web && pnpm test:coverage` passed (74.49% statements, 66.19%
branches, 71.63% functions, 81.89% lines); `git diff --check` passed.
Known failures or risks: None. Playwright uses its existing stateful API route harness
to model a fresh installation and browser-visible HTTP contracts; the backend access
matrix remains covered by AUTH-18's direct server tests.
Next exact action: In a fresh handoff session, start AUTH-21 only.
Decisions or deviations from the parent plan: None. The browser tests exercise the
approved forms, local-bypass, and external-mode contracts without adding a mode,
storage mechanism, or production bypass.
```
