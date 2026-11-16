# Checkpoint 1: persistence foundation

Parent design sections: 3, 5, and 14.

## AUTH-02: Add authentication dependencies and configuration

- [x] Task complete
- Depends on: AUTH-01
- Primary files: `pyproject.toml`, `uv.lock`, `scanner/config.py`, focused tests

### Work

1. Add the selected maintained Argon2id library and lock it through `uv`.
2. Add typed environment overrides for:
   - authentication method (`EDITARR_AUTHENTICATION_METHOD`);
   - authentication-required mode (`EDITARR_AUTHENTICATION_REQUIRED`);
   - trusted proxy addresses/networks;
   - session idle and absolute lifetimes;
   - remember-me idle and absolute lifetimes;
   - cookie name, SameSite behavior, and explicit secure-cookie deployment behavior;
   - login throttling limits/window.
   Implement the exact names, defaults, validation, and remembered-session semantics in
   parent-plan sections 5.2 and 5.3. In particular, the remembered session has a 7-day
   idle deadline and a 30-day absolute deadline; it does not inherit the ordinary
   30-minute idle deadline.
3. Validate enum values, positive durations, trusted networks, and incompatible cookie
   combinations at startup.
4. Implement and test the fixed precedence: environment override, persisted setting,
   secure default.
5. Keep safe defaults: forms, authentication enabled, no trusted proxies.

### Definition of done

- Fresh settings resolve to forms + enabled with no proxy trust.
- Environment overrides are distinguishable from defaults and cannot be silently
  overwritten by web or CLI changes.
- Invalid auth modes, lifetimes, and proxy networks fail with actionable validation.
- Duration validation requires ordinary idle <= ordinary absolute and remembered idle
  <= remembered absolute. SameSite accepts only `lax` or `strict`.
- Cookies default to `editarr_session`, `SameSite=Lax`, and `Secure`; disabling Secure
  is environment-only and emits a startup warning suitable for local HTTP development.
- Login throttling defaults to independent 5-per-username and 25-per-address failure
  buckets over 15 minutes.
- No setting contains an administrator password or session secret.
- Focused configuration tests and `uv run ruff check scanner tests` pass.

### Handoff record

```text
Status: DONE
Last completed substep: Implemented typed authentication settings, validation, override
precedence resolution, and focused configuration tests.
Files changed: pyproject.toml; uv.lock; scanner/config.py; tests/test_auth_config.py;
docs/local-authentication-tasks/01-persistence.md; docs/local-authentication-tasks/README.md
Dependency and chosen version: argon2-cffi 25.1.0 (with argon2-cffi-bindings 26.1.0).
Verification run and result: `uv run pytest -q tests/test_auth_config.py` (22 passed);
`uv run ruff check scanner tests` passed; `git diff --check` passed.
Known failures or risks: The approved database remains untouched and must be wiped only
immediately before AUTH-03. Authentication settings persistence and mutation enforcement
will be wired to the forthcoming auth-settings repository and CLI/API tasks.
Next exact action: Start AUTH-03, wipe only the explicitly approved development database
immediately before schema creation, and add the three authentication tables without a
migration.
Decisions or deviations from the parent plan: None. Trusted proxy networks are supplied
as the typed JSON list `EDITARR_AUTH_TRUSTED_PROXY_NETWORKS`, consistent with existing
list-valued environment configuration.
```

## AUTH-03: Add administrator and session tables

- [x] Task complete
- Depends on: AUTH-02 and database reset approval
- Primary files: `scanner/db.py`, test fixtures

### Work

1. Add the singleton `AdminUserRow` described by the parent plan.
2. Add `AuthSessionRow` with token/CSRF digests, expirations, and administrator relation.
3. Add the singleton `AuthSettingsRow` for persisted method and required mode; its row
   must not affect first-run detection.
4. Add database constraints and indexes for singleton enforcement, session lookup, and
   expiry cleanup.
5. Update in-memory SQLite fixtures so all tests build the current schema.
6. Wipe only the explicitly approved development database target, then let current
   schema creation recreate it. Do not add a migration.

### Definition of done

- A clean database creates all three authentication tables and constraints.
- A second administrator row is rejected at the database layer.
- Deleting or changing unrelated domain rows cannot create an administrator.
- Session token and CSRF columns are explicitly digests, not raw bearer values.
- Focused schema tests pass and no migration file exists.

### Handoff record

```text
Status: DONE
Last completed substep: Added the authentication ORM schema, recreated the authorized
development database with the current schema, and verified the focused constraints.
Files changed: scanner/db.py; tests/test_auth_schema.py;
docs/local-authentication-tasks/01-persistence.md;
docs/local-authentication-tasks/README.md
Database target wiped after approval: Removed only
/path/to/Editarr/data/scanner.db on 2026-09-03, then immediately
recreated it with `scanner.db.init_db()`. No caches, EDLs, media, migration files, or
unrelated files were removed.
Schema verification: Fresh database contains admin_users, auth_sessions, and
auth_settings. admin_users and auth_settings enforce id = 1; auth_settings constrains
persisted modes; auth_sessions has a unique token_digest, a foreign key to admin_users,
and absolute/idle expiry indexes. Focused tests also prove the database rejects a second
administrator and that auth_settings or unrelated domain-row changes cannot establish
an administrator.
Verification run and result: `.venv/bin/python -m pytest -q tests/test_auth_schema.py`
(5 passed); `.venv/bin/ruff check scanner/db.py tests/test_auth_schema.py` passed;
`git diff --check` passed. Physical schema inspection on the recreated database reported
all three tables, both expiry indexes, and the explicit digest columns.
Known failures or risks: None for AUTH-03. Existing unrelated worktree changes remain
preserved.
Next exact action: AUTH-04 and AUTH-05 may now proceed independently.
Decisions or deviations from the parent plan: None. The schema uses named singleton and
mode check constraints, explicit digest column names, and separate indexes for each
session expiry deadline.
```

## AUTH-04: Implement administrator repository operations

- [x] Task complete
- Depends on: AUTH-03
- Primary files: new focused auth repository module, `scanner/models.py`, tests

### Work

1. Add safe Pydantic input/output types that never expose password hashes.
   Use the exact shared username/password canonicalization, code-point limits, and
   character-category rules in parent-plan section 5.1.
2. Implement administrator existence/status lookup.
3. Implement atomic singleton creation with normalized username and precomputed password
   hash.
4. Implement credential update with optimistic/transactional consistency.
5. Map duplicate/racing setup to a domain conflict without overwriting the winner.

### Definition of done

- Repository tests cover create, read-safe-state, duplicate, race/conflict, rename, and
  password-hash replacement.
- Output serialization cannot include the password hash by accident.
- Username normalization and validation have explicit edge-case tests.
- Password tests cover the 15/256 code-point boundaries, NFC equivalence, significant
  leading/trailing spaces, accepted Unicode, and every rejected character category.
- Username tests cover the 1/64 code-point boundaries, NFKC/casefold equivalence,
  trimming, significant interior spaces, and every rejected character category.
- Transactions roll back fully on failure.
- Focused tests and Ruff pass.

### Handoff record

```text
Status: DONE
Last completed substep: Implemented shared credential canonicalization/validation,
safe Pydantic administrator models, and atomic singleton create/read/update operations.
Files changed: scanner/models.py; scanner/auth_repository.py;
tests/test_auth_repository.py; docs/local-authentication-tasks/01-persistence.md;
docs/local-authentication-tasks/README.md
Verification run and result: `uv run pytest -q tests/test_auth_config.py
tests/test_auth_schema.py tests/test_auth_repository.py` (53 passed); `uv run ruff
check scanner tests` passed; `git diff --check` passed.
Known failures or risks: None for AUTH-04. The repository accepts only a precomputed,
non-empty password hash and never returns it; Argon2id hashing and credential
verification remain AUTH-06 work.
Next exact action: Complete AUTH-05; then start AUTH-06.
Decisions or deviations from the parent plan: None. Username and password validation
uses the exact parent-plan section 5.1 normalization, code-point, whitespace, and
Unicode-category rules. Administrator creation maps singleton races to a domain conflict,
and updates compare the previously-read `updated_at` timestamp atomically.
```

## AUTH-05: Implement session repository operations

- [x] Task complete
- Depends on: AUTH-03
- Primary files: focused auth repository/service module, tests

### Work

1. Generate high-entropy opaque session and CSRF values using `secrets`.
   Generate independent 32-byte values, expose only their canonical 43-character
   unpadded-base64url encodings, and use the domain-separated SHA-256 digest contract in
   parent-plan section 5.2.
2. Persist only digests and return raw values only at the issuance boundary.
3. Implement session lookup with constant-time digest comparison where applicable.
4. Implement idle/absolute expiry, bounded last-seen refresh, current-session revocation,
   all-session revocation, and expired-session cleanup.
   Apply the parent-plan section 5.2 activity rules and coalesce writes using a 5-minute
   touch interval, reduced to half the applicable idle lifetime when that lifetime is
   under 10 minutes.
5. Make cleanup bounded so startup cannot be monopolized by an unexpectedly large table.
   Delete at most 500 revoked/expired rows per invocation in ascending ID order; do not
   loop through additional batches during startup.

### Definition of done

- Tests cover issue, validate, idle expiry, absolute expiry, remember-me, refresh,
  current revocation, mass revocation, and cleanup.
- Raw session and CSRF values are absent from persisted rows and logs.
- Tests prove each issued value contains 256 bits of CSPRNG input, uses canonical
  43-character unpadded base64url, and rejects alternate or malformed encodings.
- Persisted token values are lowercase, 64-character, domain-separated SHA-256 hex
  digests; CSRF validation uses constant-time digest comparison.
- An expired or revoked token never authenticates.
- Eligible authenticated activity refreshes `last_used_at` and the idle deadline no more
  than once per effective touch interval, never extends the absolute deadline, and
  cannot race with revocation to restore a session.
- Tests distinguish refresh-eligible safe/unsafe requests from invalid-token, failed
  CSRF/Origin, login/setup, logout, and bypass traffic that must not count as activity.
- Session issuance is transactionally tied to its administrator.
- One cleanup call removes no more than 500 qualifying rows, uses a deterministic order,
  reports its count, and leaves additional rows for a later run.
- Focused tests and Ruff pass.

### Handoff record

```text
Status: DONE
Last completed substep: Implemented token helpers and session repository operations for
issuance, validation, CSRF comparison, expiry, coalesced activity refresh, revocation,
and deterministic bounded cleanup; added focused coverage for every repository path.
Files changed: scanner/auth_repository.py; tests/test_auth_repository.py;
docs/local-authentication-tasks/01-persistence.md;
docs/local-authentication-tasks/README.md
Verification run and result: `.venv/bin/python -m pytest -q tests/test_auth_repository.py
tests/test_auth_schema.py tests/test_auth_config.py` (69 passed); `.venv/bin/ruff check
scanner tests` passed; `git diff --check` passed.
Known failures or risks: None for AUTH-05. HTTP request eligibility (CSRF/Origin,
safe/unsafe methods, login/setup/logout, and bypass traffic) remains AUTH-08/AUTH-09
work; the repository never refreshes a row unless that later layer explicitly calls its
eligible-activity operation.
Next exact action: Start AUTH-06.
Decisions or deviations from the parent plan: Session and CSRF tokens independently use
256 CSPRNG bits encoded as exactly 43 unpadded-base64url characters. SQLite stores only
domain-separated SHA-256 lowercase hex digests. Bulk cleanup removes at most 500 revoked
or expired rows per invocation, in ascending ID order, without an unbounded startup
loop. Eligible authenticated activity refreshes at most every 5 minutes (or half the
idle lifetime when configured below 10 minutes), advances only the idle deadline, and
cannot revive an expired or revoked session. No deviations.
```
