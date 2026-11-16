# Checkpoint 4: web authentication boundary

Parent design section: 9.

## AUTH-13: Centralize authenticated web API requests

- [x] Task complete
- Depends on: AUTH-08 and AUTH-09
- Primary files: `web/src/api.ts`, new focused auth/API helpers, tests

### Work

1. Add typed auth status, setup, login, logout, account, and security API functions.
2. Route existing JSON/blob requests through one request helper without changing their
   public TypeScript function signatures unnecessarily.
3. Add the in-memory CSRF header to unsafe requests and never persist it.
4. Convert structured `401` and `423` responses into one authentication-state event that
   the top-level gate can consume.
5. Preserve safe human-readable errors and existing image URL behavior.

### Definition of done

- Unit tests cover credentials/cookie behavior, CSRF headers, auth-state errors, blobs,
  JSON, empty responses, and existing error detail.
- No auth value is placed in local/session storage or a URL.
- Existing API helper callers compile without broad unrelated rewrites.
- `pnpm test` and `pnpm build` pass.

### Handoff record

```text
Status: COMPLETE
Last completed substep: Added the shared browser API request boundary, typed
authentication endpoint helpers, in-memory CSRF state, and authentication-state
subscription/errors. Routed all existing JSON and blob API helpers through it while
preserving image URL helpers.
Files changed: web/src/api.ts; web/src/api.test.ts;
docs/local-authentication-tasks/04-web.md;
docs/local-authentication-tasks/README.md.
Verification: `cd web && pnpm test` passed (2 files, 52 tests); `cd web && pnpm build`
passed; `git diff --check` passed. There is no configured `lint` script in
web/package.json.
API helper migration remaining: None. All JSON/blob helpers in web/src/api.ts use
apiRequest. Image helpers continue returning same-origin cookie-authenticated URLs and
never add a token to a URL.
Security/contract notes: apiRequest always uses `credentials: "same-origin"`. The CSRF
token is module-private, is set only from authenticated status/setup/login responses,
is cleared on authentication-state rejection, and is attached only to unsafe same-origin
`/api` requests (not setup/login bootstrap requests, safe methods, non-API paths, or
cross-origin URLs). A 401 or 423 emits the centralized state event before a helper
raises its typed error. Security updates do not issue a replacement token server-side,
so an authenticated response without one retains the valid in-memory token for that
unchanged session.
Decisions or deviations from the parent plan: None.
Next exact action: Start AUTH-14.
```

## AUTH-14: Add the top-level authentication gate

- [x] Task complete
- Depends on: AUTH-13
- Primary files: `web/src/main.tsx`, new auth gate/context components, tests

### Work

1. Fetch auth status before mounting `App`.
2. Model status loading, retryable error, setup, login, and authenticated states.
3. Ensure `App` and `ScansFloatingPanel` never mount while access is unresolved or
   denied.
4. Subscribe to centralized session-expiry/setup-required events and unmount protected
   UI immediately.
5. Retain and restore a safe internal hash route after authentication.

### Definition of done

- Component tests prove only the status request occurs before authorization.
- Protected headings/sidebar/polling never flash in loading, setup, login, or error
  states.
- A runtime `401` transitions to login and stops queue polling.
- A runtime `423` transitions to setup.
- Route restoration accepts only internal hashes.
- `pnpm test` and `pnpm build` pass.

### Handoff record

```text
Status: DONE
Last completed substep: Mounted an authentication gate above App that fetches only
the auth status before choosing a screen, and added the focused state/context boundary
for subsequent setup and login pages.
Files changed: web/src/main.tsx; web/src/auth-gate.tsx;
web/src/auth-gate.test.tsx; docs/local-authentication-tasks/04-web.md;
docs/local-authentication-tasks/README.md.
Verification run and result: `cd web && pnpm test -- auth-gate.test.tsx` passed
(3 files, 60 tests); `cd web && pnpm build` passed; `cd web && pnpm test` passed
(3 files, 60 tests); `git diff --check` passed.
Known failures or risks: None. The temporary setup/login boundary screens intentionally
contain no forms; AUTH-15 and AUTH-16 replace them using the exported gate context.
State-machine notes: Loading and retryable status errors mount no protected UI.
`setup_required` and `unauthenticated` mount only their respective auth boundary
screen. An authenticated status mounts App. Centralized 401/423 events immediately
unmount App and retain only a validated internal hash route in memory. Status includes
the current local/external configuration for subsequent UI work without writing it to
browser storage.
Next exact action: In a fresh session, start AUTH-15 only. Replace the setup-required
boundary screen through AuthenticationGate's `setupRequired` slot; use
`useAuthenticationGate().acceptAuthenticationStatus` after successful setup and retain
the gate's in-memory route restoration.
Decisions or deviations from the parent plan: None.
```

## AUTH-15: Build first-run administrator setup

- [x] Task complete
- Depends on: AUTH-14
- Primary files: focused setup/auth components, `web/src/styles.css`, tests

### Work

1. Build the branded centered setup panel from the parent plan.
2. Add username, new-password, and confirmation inputs with correct autocomplete.
3. Mirror server validation where useful while treating server validation as
   authoritative.
4. Implement pending, success, generic failure, validation, and setup-race conflict
   behavior.
5. On success, pass the issued auth/CSRF state to the gate and mount the requested route.

### Definition of done

- Setup is keyboard-operable with visible focus and correctly associated labels.
- Validation and server errors use accessible live regions and do not expose secrets.
- Password values are cleared after completion and are never persisted.
- Setup conflict refreshes status and lands on login safely.
- Component tests, build, and an axe check pass at desktop and 320px widths.

### Handoff record

```text
Status: COMPLETE
Last completed substep: Replaced the setup-required boundary with a branded, first-run
administrator form. The form mirrors useful canonicalization, length, confirmation, and
disallowed-character checks while leaving server validation authoritative; it clears
password values after every request outcome, keeps all credentials and CSRF state out of
persistent browser storage, gives a short live success announcement, and then hands the
authenticated response to the gate for safe route restoration. A setup race refreshes
status and lands on the login boundary without exposing the winning username.
Files changed: web/src/auth-setup.tsx; web/src/auth-setup.test.tsx; web/src/main.tsx;
web/src/styles.css; web/e2e/app.spec.ts; docs/local-authentication-tasks/04-web.md;
docs/local-authentication-tasks/README.md.
Verification: `cd web && pnpm test -- auth-setup.test.tsx` passed (4 files, 66 tests);
`cd web && pnpm build` passed; `cd web && pnpm test:e2e` passed (21 Chromium tests,
including axe checks for the setup screen at desktop and 320px); `git diff --check`
passed.
Visual/accessibility notes: The setup form uses visible global focus treatment,
associated labels, password-manager autocomplete values, inline live validation/errors,
and a full-width pending-aware action. The centered panel remains usable at 320px.
Decisions or deviations from the parent plan: None.
Next exact action: Start AUTH-16 in a fresh handoff session. Replace only the
unauthenticated gate slot with the forms login experience, reusing the established
setup-panel visual conventions and gate context; do not alter AUTH-15.
```

## AUTH-16: Build the forms login experience

- [x] Task complete
- Depends on: AUTH-15
- Primary files: focused login/auth components, `web/src/styles.css`, tests

### Work

1. Reuse the setup panel visual structure with `SIGN IN TO CONTINUE`.
2. Add username, current-password, remember-me checked by default, and full-width login.
3. Use generic incorrect-credential and throttled feedback.
4. Disable duplicate submission, clear the password at the appropriate boundary, and
   hand authenticated state to the gate.
5. Restore the safe attempted route after successful login.

### Definition of done

- Correct autocomplete, capitalization, input types, labels, and focus behavior are
  asserted.
- Wrong username and password are indistinguishable in UI copy.
- Remember-me is sent accurately.
- Login works without a full page reload and session expiry returns to this page.
- Component tests, build, and axe checks pass at desktop and 320px widths.

### Handoff record

```text
Status: COMPLETE
Last completed substep: Replaced the unauthenticated gate boundary with the branded
forms login screen. It submits only through the existing browser API boundary, retains
the CSRF token only in the API module after a successful server response, clears the
password after every request result, and lets the gate restore only its in-memory safe
hash route. Session-expiry notifications return directly to this form.
Files changed: web/src/auth-login.tsx; web/src/auth-login.test.tsx; web/src/main.tsx;
web/src/styles.css; web/e2e/app.spec.ts; docs/local-authentication-tasks/04-web.md;
docs/local-authentication-tasks/README.md.
Verification run and result: `cd web && pnpm test -- auth-login.test.tsx` passed
(5 files, 73 tests); `cd web && pnpm build` passed; `cd web && pnpm test` passed
(5 files, 73 tests); `cd web && pnpm test:e2e` passed (22 Chromium tests, including
axe checks for the login screen at desktop and 320px); `git diff --check` passed.
Known failures or risks: None.
Visual/accessibility notes: The login UI uses the setup panel convention, associated
labels, visible focus treatment, username/current-password autocomplete, disabled
pending controls, a checked-by-default remember-me checkbox, and live generic errors.
Wrong and unknown credentials have identical UI copy; throttling has safe generic
feedback. No credential or token reaches browser storage or a URL.
Next exact action: Start AUTH-17.
Decisions or deviations from the parent plan: None.
```

## AUTH-17: Add account, security, and logout controls

- [x] Task complete
- Depends on: AUTH-16 and AUTH-12
- Primary files: `web/src/App.tsx`, `web/src/api.ts`, `web/src/styles.css`, tests

### Work

1. Add the administrator/security section under Settings > General.
2. Add username change and password change with current-password and confirmation fields.
3. Add authentication-required mode control, external-mode read-only indicator, and
   contextual security warnings.
4. Add a persistent logout action when a forms session applies.
5. Identify local-bypass and external-mode sessions without implying a password login
   occurred.
6. Handle credential-change session revocation by returning to login.

### Definition of done

- Account forms never display a hash or existing password.
- Current-password failure is generic and preserves safe editable state.
- Required-mode changes update effective UI after the documented save/restart boundary.
- Logout revokes the server session, clears protected client state, and shows login.
- Local/external warnings are accessible and not color-only.
- Existing Settings tests plus new component tests and build pass.

### Handoff record

```text
Status: DONE
Last completed substep: Added Settings > General administrator/security controls and
the persistent forms-session sign-out action. Corrected the existing status contract so
post-setup external and verified-local bypasses mount the authenticated web gate without
inventing a forms session; the UI identifies those sessionless modes and does not expose
password-session controls there.
Files changed: scanner/auth_api.py; tests/test_auth_api.py; web/src/App.tsx;
web/src/styles.css; web/src/auth-settings.test.tsx; web/e2e/app.spec.ts;
docs/local-authentication-tasks/04-web.md; docs/local-authentication-tasks/README.md.
Verification run and result: `cd web && pnpm test` passed (6 files, 77 tests);
`cd web && pnpm build` passed; `cd web && pnpm exec playwright test --workers=1`
passed (23 Chromium tests, including axe checks for the security screen at desktop and
320px); `uv run pytest -q tests/test_auth_api.py` passed (4 tests); `git diff --check`
passed. The existing parallel Playwright suite had separate timing-only failures in two
unrelated visual/navigation tests on two runs; the required serial suite passed in full.
Known failures or risks: None in AUTH-17. Browser parallelism remains timing-sensitive
outside the AUTH-17 controls.
Next exact action: In a fresh handoff session, start AUTH-18 only.
Decisions or deviations from the parent plan: None. The bypass status correction follows
the parent plan's defined authenticated bypass state and enables the required UI; it
does not add an authentication mode or persist a browser auth value.
```
