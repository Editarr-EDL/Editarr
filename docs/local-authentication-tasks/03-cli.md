# Checkpoint 3: headless setup and recovery

Parent design section: 8.

## AUTH-11: Add administrator CLI commands

- [x] Task complete
- Depends on: AUTH-06 and AUTH-10
- Primary files: `scanner/cli.py`, optional focused CLI module, tests, `pyproject.toml`

### Work

1. Preserve the existing default server command while adding an argparse subcommand
   structure that does not break `editarr --host ... --port ...`.
2. Add `admin status`, `admin create`, `admin reset-password`, and `admin rename`.
3. Use `getpass` by default and `--password-stdin` for automation; reject empty stdin and
   confirmation mismatches.
4. Initialize the schema for status/create as needed and reuse the authentication
   service rather than duplicating validation or hashing.
5. Print concise results and stable nonzero exit codes without exposing secrets.
6. Warn that reset/rename should be run while the API is stopped and revoke sessions.

### Definition of done

- Existing server invocation still launches with the same defaults.
- CLI tests cover prompt, stdin, duplicate create, missing admin, reset, rename,
  validation failure, output redaction, and exit codes.
- No plaintext password command-line option exists.
- A CLI-created administrator makes the next API startup configured.
- Focused tests and Ruff pass.

### Handoff record

```text
Status: DONE
Last completed substep: Added the `admin status`, `admin create`,
`admin reset-password`, and `admin rename` commands. Creation and recovery use the
authentication service, never issue a browser session from the CLI, and emit the
secret-free CLI audit records. Reset and rename revoke every session.
Files changed: scanner/auth.py; scanner/cli.py; tests/test_cli.py;
docs/local-authentication-tasks/03-cli.md; docs/local-authentication-tasks/README.md.
Verification run and result: `uv run ruff check scanner tests` passed. Focused
`uv run pytest -q tests/test_cli.py tests/test_auth_services.py` passed (17 passed).
Full `uv run pytest -q` passed (322 passed, 5 existing deprecation warnings).
`git diff --check` passed.
CLI compatibility notes: The no-subcommand server invocation retains the existing
`editarr --host ... --port ...` behavior and disables Uvicorn proxy header rewriting.
Passwords use confirmation-hidden `getpass` or a non-TTY `--password-stdin`; no
`--password` option exists and accidental use is redacted from terminal output.
Known failures or risks: None. The first full-suite attempt exposed the pre-existing
concurrent CSRF-key-loader race test once; its focused rerun and the subsequent full
suite both passed.
Decisions or deviations from the parent plan: None. No schema change, migration, or
database reset was made.
Next exact action: Hand off to a fresh session for AUTH-12 only.
```

## AUTH-12: Add authentication-mode CLI commands

- [x] Task complete
- Depends on: AUTH-11
- Primary files: `scanner/cli.py`, `scanner/config.py` or focused persisted settings, tests

### Work

1. Add `auth show`, `auth set-method`, and `auth set-required` commands.
2. Implement the parent plan's authoritative precedence model: environment override,
   persisted `auth_settings` row, secure default. Refuse mutation while the corresponding
   environment override is active and explain which variable controls it.
3. Require explicit values and print a security warning for `external` and
   `disabled_for_local_addresses`.
4. Enforce restart-required behavior for method changes.
5. Do not let either command mark first-run setup complete.

### Definition of done

- Tests cover valid values, invalid values, precedence, persistence/restart, and warnings.
- Setting external mode without an administrator still leaves the API setup-gated.
- `auth show` emits no credentials, hashes, tokens, or integration API keys.
- Forms + enabled remains the default.
- Focused tests and Ruff pass.

### Handoff record

```text
Status: DONE
Last completed substep: Added `auth show`, `auth set-method`, and `auth set-required`.
The CLI resolves effective values with environment override, persisted row, then secure
defaults; refuses mutations controlled by either environment variable; warns for
`external` and `disabled_for_local_addresses`; and reports the restart requirement for
method changes. These commands initialize only the schema and never create an
administrator, so configuring external mode cannot clear first-run setup.
Files changed: scanner/auth_settings.py; scanner/cli.py; tests/test_cli.py;
docs/local-authentication-tasks/03-cli.md; docs/local-authentication-tasks/README.md.
Verification run and result: `uv run ruff check scanner tests` passed. Focused
`uv run pytest -q tests/test_cli.py tests/test_auth_config.py tests/test_auth_schema.py`
passed (47 passed). `git diff --check` passed. Full `uv run pytest -q` was run twice
and each time had 332 passed, 1 failed: the pre-existing concurrent CSRF-key loader
race in `tests/test_auth_crypto.py::test_key_loader_creates_one_private_file_concurrently_and_rejects_unsafe_files`.
Known failures or risks: The full-suite CSRF key-loader race is out of AUTH-12 scope and
remains reproducible; no AUTH-12 code touches that loader. All AUTH-12 definition-of-done
tests and lint pass.
Decisions or deviations from the parent plan: None. No schema change, migration, or
database reset was made.
Next exact action: Checkpoint 3 is complete; hand off to a fresh session for AUTH-13 only.
```
