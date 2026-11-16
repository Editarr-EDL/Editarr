# Multi-User Permissions

Feature `mu-permissions` (MU-04). One evaluator decides every action for HTTP
handlers, services, workers, and serializers. It is proved by deterministic
checks and a TEST FIXTURE HTTP boundary here. Product routes call it through
`load_access` and `may_attempt` (for example `scanner/http/permissions_api.py`),
and the browser journeys in [Multi-user journeys](./multi-user.md),
[Settings access](./settings-access.md), and
[Identity isolation](./identity-isolation.md) exercise it end to end.

## What exists

- `scanner/security/access/evaluator.decide` takes a live `PrincipalAccess`,
  a capability or `ActionPair`, a resolved target (`INSTANCE`, `AccountRef`,
  `ResourceRef`, `MediaRef`), and an optional `FreshAuthentication`.
- `authorization.require`, `may_attempt`, `authorize_job`, and
  `capability_payload` are the shared entry points.
- `request_access.request_access` is the FastAPI dependency. It reads the
  credential the access middleware recorded and re-reads the account, role
  defaults, and overrides on every request in one SQL statement, so a
  concurrent commit cannot mix two stored states into one principal.
- `fresh.verify_reauthentication` is the only way to mint a fresh proof. The
  proof binds to the exact `PrincipalAccess` it verified, so it works only in
  the request that checked the password.

## Route coverage

Every launch admits every active account. The middleware authenticates and
checks freshness; each gated handler resolves its principal through
`request_access` and calls `require` on the targets it touches.
`tests/test_multi_user_route_actions.py::test_every_gated_handler_resolves_its_principal`
walks each gated route's dependency tree and fails for a handler that never
resolves a principal, so a new gated route cannot ship unchecked.

## Deterministic checks

Run from the task checkout with scratch data and logs. Importing the app
writes into `EDITARR_DATA_DIR`, and logging writes into `EDITARR_LOG_DIR`.

```bash
T=$(mktemp -d) && mkdir "$T/data" "$T/logs"
PYTHONPATH="$PWD" EDITARR_DATA_DIR="$T/data" EDITARR_LOG_DIR="$T/logs" \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_access_evaluator.py tests/test_access_live_http.py
rm -rf "$T"
```

- `test_access_evaluator.py` covers the pure tables. It checks every capability
  by role, credential, and account state, and checks default grants against the
  Markdown matrix. It also covers overrides, corrupt grants, target-account
  protection, own and other resources, media, fresh proofs, temporary
  passwords, and payloads.
- `test_access_live_http.py` sends requests through the real access middleware
  and auth router to the TEST FIXTURE routes in `tests/multi_user_access_http.py`.
  The same cookie and API key see role-default, override, status, and role
  changes between requests. It also covers corrupt rows, enumeration, fresh
  passwords, and a worker session that stays open. It commits a concurrent
  role and default change before each statement of a load and of a request,
  and checks that every outcome matches one committed state. It also checks
  that a proof verified in one request is refused in the next.

## Builders for later tasks

`tests/multi_user_access_support.py` is fixture version `mu04-access-builders-2`.

- `build_access(role, credential=..., status=..., local_credential=...,
  role_defaults=..., overrides=..., unreadable=...)` returns a principal whose
  capabilities come from the real evaluator.
- `valid_target`, `resource_of`, `account_ref`, `self_ref`, `ALLOWED_MEDIA`,
  `DENIED_MEDIA`, and `minted_proof` are for pure tables only. `minted_proof`
  binds to the principal object it is given. HTTP tests mint
  proofs through `verify_reauthentication`.
- `live_accounts(tmp_path)` creates owner 1, admins 2 and 3, and users 4 and 5,
  with sessions and keys. Its `set_role_default`, `set_override`, `set_status`,
  and `set_role` methods bump `permission_revision`.
- `tests/multi_user_access_http.serve_access_fixture(monkeypatch, accounts,
  mode)` serves them.

## Real-app regression

The middleware records the authenticated credential, so rerun `verify.mjs auth`
after changing it. That run creates and signs in real nonowner accounts.
