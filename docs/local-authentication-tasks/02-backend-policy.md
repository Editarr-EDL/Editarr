# Checkpoint 2: backend authentication and policy

Parent design sections: 3 through 7.

## AUTH-06: Implement password and authentication services

- [x] Task complete
- Depends on: AUTH-04 and AUTH-05
- Primary files: new `scanner/auth.py` or focused auth package, tests

### Work

1. Implement shared username/password validation and Argon2id hash/verify helpers.
   Use `m=19456 KiB`, `t=2`, `p=1`, a 16-byte salt, a 32-byte hash, and Argon2 version
   19 exactly as specified in parent-plan section 5.1.
2. Keep setup, login, account change, CLI creation, and CLI reset on the same service
   path.
3. Verify a dummy hash when the submitted username does not match so failure timing does
   not trivially enumerate the singleton username.
4. Implement setup, login, logout, current-password reauthentication, rename, and password
   change orchestration.
5. Add structured, secret-free authentication audit logging.
   Implement the fixed JSONL schema, allowlists, protected file destination, UTC daily
   rotation, and 30-day retention from parent-plan section 5.4.

### Definition of done

- Correct credentials authenticate; wrong username and wrong password return the same
  domain error.
- Hashes meet the selected Argon2id parameters and can be upgraded on successful login if
  parameters later change.
- Unknown usernames perform verification against one process-initialized valid dummy
  hash using the same Argon2id profile.
- Setup and account updates use one validation implementation.
- Credential changes revoke all sessions and do not leave a partially changed account.
- Tests assert secrets are absent from exceptions, logs, and serialized output.
- Audit tests cover every event name, required/null fields, stable generic failure
  reasons, file permissions, UTC rotation, 30-day removal, JSONL injection resistance,
  and a rate-limited ordinary-log error when the audit destination is unavailable.

### Handoff record

```text
Status: COMPLETE
Last completed substep: Added the shared Argon2id password/authentication service,
transactional setup and credential-update flows, and protected JSONL audit logging.
Files changed: scanner/auth.py; scanner/auth_audit.py; scanner/auth_repository.py;
tests/test_auth_services.py; docs/local-authentication-tasks/02-backend-policy.md;
docs/local-authentication-tasks/README.md
Verification: `uv run ruff check scanner tests` passed; `uv run pytest -q` passed
(226 passed, 5 pre-existing deprecation warnings); `git diff --check` passed.
Argon2id parameters: Argon2id v19, m=19456 KiB, t=2, p=1, salt_len=16,
hash_len=32; unknown usernames verify against one process-initialized valid dummy hash.
Audit policy: Typed schema-version-1 JSONL records go only to
<data_dir>/logs/auth-audit.jsonl via a non-propagating dedicated logger; owner-only
permissions, UTC daily rotation, fixed 30-day retention, and strict field/event/reason
allowlists apply.
Known failures or risks: Audit initialization is not yet wired into startup; AUTH-10 owns
startup sequencing. HTTP audit contexts, throttling, CSRF, and session-rejection call
sites are owned by AUTH-08 and AUTH-09.
Next exact action: Start AUTH-08 once AUTH-07 is complete.
```

## AUTH-07: Implement client-address and trusted-proxy classification

- [x] Task complete
- Depends on: AUTH-02
- Primary files: new focused network/auth policy module, tests

### Work

1. Normalize direct IPv4, IPv6, and IPv4-mapped IPv6 peer addresses.
2. Classify loopback, private, link-local, and unique-local addresses.
3. Implement the exact header-family selection in parent-plan section 4.1. Read raw
   ordered header fields; accept repeated fields of one family, reject simultaneous
   `Forwarded` and `X-Forwarded-For`, and enforce the 8,192-byte/32-hop bounds.
4. Implement the strict, numeric-only `Forwarded` and `X-Forwarded-For` grammars in
   section 4.2, including quoted/bracketed IPv6 and port validation.
5. Walk a fully validated chain right-to-left, skip trusted proxies, select the first
   untrusted address, and reject the all-trusted case as specified in section 4.3.
6. Return the structured result and stable reason codes from section 4.4. Missing or
   invalid forwarding metadata must force nonlocal authentication while retaining a
   valid direct peer only for attribution/throttling.
7. Produce a small pure function/API that middleware can test without opening sockets.

### Definition of done

- Table-driven tests cover local/public IPv4 and IPv6, mapped addresses, missing peers,
  trusted proxy chains, multiple proxies, spoofed headers, and malformed inputs.
- A public peer cannot claim locality by setting a forwarded header.
- Repeated fields of one family preserve wire order; both families together are rejected
  without precedence or merging.
- Tests cover right-to-left selection with one and multiple trusted hops, attacker-added
  entries to the left of the selected client, and an all-trusted chain that resolves as
  nonlocal rather than choosing the leftmost address.
- Syntax tests cover accepted IPv4/IPv6/port forms, quoted `Forwarded` values, surrounding
  SP/HTAB, and rejection of invalid brackets, ports, quotes/escapes, empty elements,
  `unknown`, obfuscated IDs, hostnames, zone IDs, controls, and internal whitespace.
- Missing, conflicting, malformed, oversized, overlong, and all-trusted forwarding input
  returns the documented stable reason and cannot enable the local-address bypass.
- A missing/malformed direct peer resolves to no address, is nonlocal, and uses the shared
  `unknown` throttle bucket; forwarding failures with a valid peer retain only that peer
  for attribution and throttling.
- No hostname/DNS lookup is used to decide whether a client is local.
- Focused tests and Ruff pass.

### Handoff record

```text
Status: COMPLETE
Last completed substep: Added a pure, fail-closed client-address resolver that parses
raw ASGI headers and preserves the original scope by disabling Uvicorn proxy-header
rewriting.
Files changed: scanner/auth_network.py; scanner/cli.py; tests/test_auth_network.py;
tests/test_cli.py; docs/local-authentication-tasks/02-backend-policy.md;
docs/local-authentication-tasks/README.md
Verification: `uv run pytest -q tests/test_auth_network.py tests/test_cli.py` passed
(52 passed); `uv run ruff check scanner/auth_network.py scanner/cli.py
tests/test_auth_network.py tests/test_cli.py` passed; `git diff --check` passed.
Proxy-chain policy summary: Normalize mapped IPv6 before trust/locality checks. Trust no
forwarding metadata from an untrusted direct peer. A trusted direct peer requires exactly
one raw header family; same-family fields are joined in wire order, and `Forwarded` plus
`X-Forwarded-For` is rejected. Strict numeric parsing, 8,192-byte/32-entry bounds, and
right-to-left trusted-hop removal apply. A malformed, missing, conflicting, oversized,
overlong, or all-trusted chain retains only the direct peer for attribution/throttling
and is always nonlocal; a missing peer uses the `unknown` bucket.
Known failures or risks: The resolver is deliberately not yet connected to request
middleware; AUTH-08 and AUTH-09 own endpoint integration and global enforcement.
Next exact action: Start AUTH-08.
```

## AUTH-08: Implement authentication endpoints

Task complete — 2026-09-03. The user explicitly approved skipping a migration and wiping only
`data/scanner.db` for the new
`auth_sessions.csrf_token_ciphertext` development-schema column. The approved AES-256-GCM
CSRF persistence design in the parent plan is authoritative.

CSRF policy is resolved: retain the digest for request validation and persist a second,
AES-256-GCM-protected copy for authenticated status recovery. The token remains stable
for the session so reloads and multiple tabs do not invalidate one another.

- [x] Task complete
- Depends on: AUTH-06 and AUTH-07
- Primary files: focused auth router/key manager, `scanner/api.py`, `scanner/config.py`,
  `scanner/db.py`, session repository, request/response models, tests

### Work

1. Add the direct `cryptography` dependency and implement the AES-256-GCM CSRF key and
   envelope contract from parent-plan section 5.2, including environment/file key
   loading, strict permissions, key IDs, and mismatch handling.
2. Add `csrf_token_ciphertext` to the fresh-development schema and update session issue,
   recovery, and revocation operations while retaining digest-based request validation.
   This post-checkpoint schema change requires the explicit development-database reset
   approval described below; do not add a migration or wipe the database without it.
3. Add status, setup, login, logout, account-update, and security-update endpoints from
   the parent plan. Authenticated status decrypts and returns the stable per-session CSRF
   token; it never rotates the token.
4. Set and clear cookies through one reviewed helper.
5. Validate safe return/deep-link values without permitting open redirects.
6. Add generic failure responses and an in-process bounded login limiter keyed by client
   and normalized username.
7. Make setup race-safe and return the documented conflict.
8. Ensure security setting changes have well-defined restart behavior where required.

### Definition of done

- Endpoint tests cover every success and documented error response.
- Setup and login immediately return an authenticated state and usable CSRF token.
- Repeated authenticated status calls and status calls from multiple tabs return the
  same CSRF token and do not invalidate previously rendered forms.
- CSRF envelope tests cover exact encoding, fresh nonces, associated-data binding,
  ciphertext/tag corruption, wrong keys, digest mismatch, and generic session rejection.
- Key-management tests cover strict environment decoding, atomic first-run file creation,
  concurrent creation, permissions/symlink rejection, environment precedence, redaction,
  and revocation after a key-ID change.
- CSRF-bearing responses are non-cacheable and tokens do not enter logs, URLs, errors,
  analytics, or frontend persistent storage.
- Cookie tests assert HttpOnly, SameSite, Path, persistence, clearing, and configured
  Secure behavior.
- Login throttle has deterministic tests without real sleeps.
- Response text and captured logs contain none of the submitted secrets or tokens.

### Handoff record

```text
Status: DONE
Last completed substep: Added and integrated the AES-256-GCM CSRF envelope/key manager,
fresh schema column, session recovery/key-ID revocation, and the complete AUTH-08 router.
Files changed: pyproject.toml; uv.lock; scanner/auth_crypto.py;
scanner/auth_repository.py; scanner/db.py; scanner/auth_api.py;
scanner/auth_settings.py; scanner/api.py; tests/test_auth_crypto.py;
tests/test_auth_api.py; tests/test_auth_repository.py; tests/test_auth_schema.py;
docs/local-authentication-plan.md; docs/local-authentication-tasks/README.md;
docs/local-authentication-tasks/02-backend-policy.md.
Verification run and result: `uv run ruff check scanner tests` passed; `uv run pytest -q`
passed (287 passed; 5 existing FastAPI/TestClient deprecation warnings); `git diff --check`
passed. Focused crypto tests cover exact envelope encoding, fresh nonces, associated-data
binding, corruption, wrong keys, digest mismatch/session revocation, strict environment
decoding, atomic concurrent creation, permissions, symlink rejection, precedence, and
key-ID replacement. Endpoint tests cover setup/login/status/logout/account/security,
error outcomes, generic failures, bounded throttling, safe returns, cookies, no-cache
headers, and stable multiple status recoveries.
Known failures or risks: AUTH-09 remains responsible for the global route policy and
unsafe-request CSRF/Origin enforcement. AUTH-10 remains responsible for startup work
gating and audit initialization. No new blocker or policy decision emerged.
Next exact action: Start AUTH-09 in a new handoff session.
Decisions or deviations from the parent plan: None. The approved stable-token,
AES-256-GCM recovery design was implemented as specified. The user explicitly approved
skipping a migration and wiped only `/path/to/Editarr/data/scanner.db`.
```

## AUTH-09: Enforce the global route gate and CSRF policy

- [x] Task complete
- Depends on: AUTH-08
- Primary files: auth middleware/policy module, `scanner/api.py`, `scanner/config.py`,
  deployment/dev-server configuration and documentation, tests

### Work

1. Install a global request policy before protected route execution.
2. Implement the full setup/forms/local/external access matrix.
3. Explicitly classify public authentication endpoints and required `OPTIONS` requests.
4. Gate `/openapi.json`, `/docs`, `/redoc`, unknown application routes, proxied images,
   previews, and every existing `/api` route.
5. Validate exactly one canonical `X-CSRF-Token` header for unsafe cookie-authenticated
   requests before their endpoint runs, following parent-plan section 5.3. Do not accept
   alternative token transports or downgrade a cookie-authenticated request to bypass.
6. Emit structured `423`, `401`, and `403` responses without redirect loops for API
   clients.
7. Add a route-inventory test that fails when a new FastAPI route lacks an explicit
   policy classification.
8. Add and validate environment-only `EDITARR_AUTH_PUBLIC_ORIGIN`. Require it for HTTP
   startup (including first-run setup), without requiring it for offline CLI commands.
   Use only this value for target-origin comparison, never Host or proxy headers.
9. Implement the exact Origin/Referer/Fetch Metadata decision rules in section 5.3,
   including the headless exception, authenticated status-token delivery, rejection
   ordering, and `403 origin_failed`. Keep credentialed CORS within the same trust bound.
10. Document the required public-origin setting for direct and TLS-proxy deployments,
    and same-origin `/api` proxying plus explicit insecure-cookie opt-in for local HTTP
    development. Update relevant HTTP test fixtures to supply explicit origins.

### Definition of done

- No protected endpoint function is entered before setup or authentication.
- Local bypass and external mode work only after the administrator exists.
- Spoofed proxy headers do not bypass forms authentication.
- Safe GET image/preview delivery works with an authenticated cookie.
- Every unsafe cookie-authenticated request requires the matching session CSRF binding;
  sessionless setup/login and bypass use the separately documented origin rules.
- Tests reject missing, repeated, comma-joined, malformed, and wrong CSRF headers and
  prove cookies, URL/query parameters, forms, and JSON cannot substitute for the header.
- HTTP startup rejects absent/invalid public origins, while offline CLI remains usable.
- Origin tests cover HTTPS-over-HTTP proxying, exact scheme/host/port matching, default
  ports, IPv6, forged Host/forwarding headers, null/list/repeated/malformed origins,
  Referer fallback, Fetch Metadata, missing headers, and each headless exception.
- Origin/CSRF rejection precedes endpoint execution and session touch; tests verify
  neither side effects nor secret/header leaks occur. Status never discloses its token
  to a rejected origin or a credentialed CORS origin outside the public origin.
- The complete route inventory test passes.

### Handoff record

```text
Status: DONE
Last completed substep: Added the global fail-closed request policy, the strict
configured-origin parser, forms/local/external access matrix, exact unsafe-session CSRF
transport validation, source-origin and Fetch Metadata checks, protected route inventory,
credentialed-CORS restriction, and development/deployment documentation.
Files changed: scanner/auth_origin.py; scanner/auth_policy.py; scanner/config.py;
scanner/api.py; scripts/dev.sh; README.md; tests/test_auth_policy.py;
tests/test_auth_config.py; tests/test_editarr_integration.py;
docs/local-authentication-tasks/README.md; docs/local-authentication-tasks/02-backend-policy.md.
Verification run and result: `uv run ruff check scanner tests` passed; focused
`uv run pytest -q tests/test_auth_config.py tests/test_auth_policy.py tests/test_editarr_integration.py`
passed (96 passed); full `uv run pytest -q` passed (308 passed; 5 existing FastAPI/TestClient
deprecation warnings); `git diff --check` passed.
Approved contracts: Exactly one canonical X-CSRF-Token header on unsafe session-cookie
requests. Required environment-only EDITARR_AUTH_PUBLIC_ORIGIN is the sole target-origin
authority, with no Host/forwarding fallback. Section 5.3 specifies source-header parsing,
headless exceptions, status-token delivery, CORS limits, and fail-closed behavior.
Known failures or risks: HTTP deployments and test fixtures must now explicitly supply
the public origin. Development browser traffic must use the Vite same-origin `/api` proxy.
The task made no schema change, migration, or database reset.
Next exact action: Start AUTH-10 in a new handoff session; gate startup recovery and the
scan scheduler on administrator setup without changing the completed AUTH-09 policy.
Decisions or deviations from the parent plan: None.
```

## AUTH-10: Gate startup recovery and scheduling

- [x] Task complete
- Depends on: AUTH-09
- Primary files: `scanner/api.py`, queue lifecycle tests

### Work

1. Reorder startup so schema/session cleanup and administrator detection precede queue
   recovery.
2. Skip queue recovery and scheduler start while setup is required.
3. Start normal recovery exactly once after successful API setup.
4. Preserve current reload/shutdown guarantees for dedicated scan workers after setup.
5. Keep failed setup from starting background work.

### Definition of done

- Startup tests prove no worker/recovery function runs on an unconfigured database.
- CLI-preconfigured startup follows the current recovery path.
- API setup starts scheduling exactly once, including repeated status/setup-conflict
  calls.
- Existing queue lifecycle tests remain green.
- Ruff and focused pytest pass.

### Handoff record

```text
Status: COMPLETE
Last completed substep: Reordered HTTP startup to secure/load the CSRF key, initialize
the authentication audit stream, initialize schema, revoke mismatched-key sessions, run
one bounded expired-session cleanup, and detect the administrator before considering scan
work. Setup-required instances perform none of the recovery or scheduler actions.
Files changed: scanner/api.py; scanner/auth_api.py; tests/test_startup_gate.py;
tests/test_editarr_integration.py; docs/local-authentication-tasks/02-backend-policy.md;
docs/local-authentication-tasks/README.md.
Verification: `uv run ruff check scanner tests` passed; focused
`uv run pytest -q tests/test_startup_gate.py tests/test_auth_api.py
tests/test_queue_lifecycle.py tests/test_editarr_integration.py` passed (70 passed);
full `uv run pytest -q` passed (315 passed, 5 existing FastAPI/TestClient deprecation
warnings); `git diff --check` passed.
Concurrency notes: The activation lock protects recovery plus scheduler startup. A
successful API setup invokes the app-owned activation callback only after its transaction
commits; repeated status and setup-conflict calls do not invoke it. Shutdown resets the
per-lifecycle activation state after preserving the existing scheduler stop/join behavior,
so a later HTTP lifecycle may recover normally while dedicated workers remain untouched.
Known failures or risks: HTTP startup requires the completed AUTH-09 public-origin
configuration. The authentication audit sink remains intentionally nonfatal when its
protected destination cannot be written, as specified by AUTH-06.
Decisions or deviations from the parent plan: None. No schema change, migration, or
database reset was made.
Next exact action: Checkpoint 2 is complete; hand off to a fresh session for AUTH-11 only.
```
