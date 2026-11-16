# RP-12 — Add executable Nginx/Caddy proxy fixtures

Dependencies: RP-10. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

Documentation must demonstrate real prefix-preserving proxies, not merely ASGI tests
with forwarding headers. Provide Nginx and Caddy examples that work with the packaged
single-listener service and can also support the production test harness.

## Read and expected touch points

Read README.md current proxy guidance, parent plan D2–D4, RP-10's installed launch
procedure, scanner/auth_network.py, and approved runtime configuration. Add examples
and a bounded local fixture/runner under a suitable docs/examples/tests location.

## Implementation steps

1. Create complete configurable Nginx and Caddy examples for dedicated-host root
   and /editarr deployments, including TLS termination and one upstream UI/API port.
   Also demonstrate how nested prefixes generalize.
2. Preserve request paths exactly. Verify proxy directives do not strip or append
   the prefix twice; do not assume a trailing slash in proxy_pass is harmless.
3. Establish one supported forwarding family (normally X-Forwarded-For). Sanitize
   client-supplied competing headers at the edge; document the direct proxy networks
   to trust and avoid broad trust defaults. Preserve Cookie, Origin, and CSRF headers.
4. Keep authenticated API/bootstrap responses from inappropriate proxy caching and
   preserve redirects, query strings, media content types, and needed response headers.
5. Set up temporary local TLS certificates/ports/configuration for tests without
   installing certificates into the user's system trust store or changing their proxy.
   Record how the test browser validates/trusts the test certificate.
6. Run each proxy's syntax validation and actual requests through its process to the
   installed backend. Assert Location, cookie Secure/path, static assets, and mounted API.
7. Document manual changes needed when the upstream Port or public prefix changes.
   Do not automatically rewrite a user's external proxy from Editarr Settings.
8. Reuse existing available tools; if installation/access is needed, request the
   appropriate approval. A missing proxy binary is a reported verification blocker.

## Acceptance criteria

- [x] Nginx and Caddy examples pass syntax checks and actual root/prefix smoke requests.
- [x] Both proxy types preserve /editarr/api/... and nested paths correctly.
- [x] HTTPS browser-facing settings work over HTTP upstream with Secure cookies.
- [x] Forwarded client classification is correct and spoofed edge headers cannot
      obtain verified-local bypass.
- [x] UI assets load and slash redirects stay on the intended HTTPS public address.
- [x] Fixtures isolate ports, keys, configs, and data; no global proxy/trust changes occur.
- [x] Examples state upstream-port/prefix updates and direct-peer trust configuration.

## Verification

Validate with installed proxy tools, launch temporary processes, and send actual HTTPS
requests using the generated CA/cert setup. Test both root and prefix per proxy.
Keep scripts/configs reusable by RP-13. Record tool versions and exact smoke commands;
do not label unrun examples verified.

## Definition of done

Shared DoD applies. Both examples have executable validation evidence and RP-13
receives the local TLS/proxy fixture contract plus cleanup instructions. No deployment
Dockerfile or external deployment was created.

## Handoff

Status: DONE

Last completed substep: Validated both real proxy binaries and ran the complete
six-cell HTTPS matrix against the freshly rebuilt installed artifact, then recorded
the reusable fixture and operator configuration contract.

Files changed: `scripts/proxy_fixture.py`; `docs/examples/reverse-proxy/README.md`;
`docs/examples/reverse-proxy/nginx.conf.template`;
`docs/examples/reverse-proxy/Caddyfile.template`; this task; RP-12 checklist row.
No production application code, real data, external proxy, or system trust changed.

Contract/API changes: Import `ProxyFixture` from the repository scripts directory.
Constructor is `(kind, editarr, dependencies, prefix="/", *, cli_port_override=False,
authentication_required="disabled_for_local_addresses")`. Use a context manager;
`close()` terminates only owned process groups and deletes temporary data/certs/logs.
Default launch seeds a saved isolated port and runs ordinary installed `editarr`, so
RP-13 can change its port through Settings. Use `authentication_required="enabled"`
for forms browser scenarios and `cli_port_override=True` for override scenarios.
`origin`, `prefix` (empty at root), `port`, `upstream_port`, `data`, `root`, `ca`,
`cert`, `backend` are exposed. `request(path, method=..., body=dict, headers=dict,
ipv6=False)` returns status/headers/bytes without redirect following or cookie storage.
`reconfigure(upstream_port=..., prefix=...)` validates a candidate before replacing
only the fixture proxy, keeping public TLS port/cert stable; it does not change app
settings or restart the backend. Call `wait_ready()` after the application transition.
`chromium_certificate_arg()` returns a process-local exception for exactly the
fixture leaf SPKI, for disposable Chromium with `ignore_https_errors=False`.
Python HTTPS separately validates the generated CA and hostname/IP SANs normally.
Detailed contracts, manual template substitutions and limitations are in the examples
README. No external deployment Dockerfile or service-manager setup was added.

Verification commands and results:

- Normal approval installed Nginx 1.31.5 and Caddy 2.11.4 via Homebrew. It also installed
  pcre2 10.48 and upgraded OpenSSL to 3.6.4. No service was started and no global trust
  was modified. Both executable versions were queried after installation.
- `UV_OFFLINE=1 UV_CACHE_DIR=/path/to/Editarr/.uv-cache uv run
  python scripts/build_release.py --out-dir '/tmp/Editarr RP12 build'` passed using
  the current audited source/frontend. pnpm's optional update metadata fetch failed
  in the sandbox; frozen install and production build succeeded offline.
- `UV_CACHE_DIR=/path/to/Editarr/.uv-cache uv venv
  '/tmp/Editarr RP12 installed env' --python .venv/bin/python`, then the same cache
  setting with `uv pip install --no-deps --python
  '/tmp/Editarr RP12 installed env/bin/python'
  '/tmp/Editarr RP12 build/editarr-0.1.0-py3-none-any.whl'` passed.
  Wheel SHA-256 is
  `eaf703c7c5b86ecd8e34de28c49e7230a50e5f8015bfc59a7ba2e1d8f09340e9`.
- `.venv/bin/python scripts/proxy_fixture.py --editarr
  '/tmp/Editarr RP12 installed env/bin/editarr' --dependency-site-packages
  '/path/to/Editarr/.venv/lib/python3.11/site-packages'` passed
  after normal loopback-bind approval: Nginx and Caddy each at `/`, `/editarr` and
  `/media/editarr`. Final run also verified the generated Chromium SPKI argument.
  Every cell ran actual `nginx -t` or `caddy validate` before initial launch and
  proxy replacement, then actual CA-validated HTTPS requests to the installed backend.
  Checked six initial assets, JS/CSS content types, hashed cache policy, no-store
  HTML/auth bootstrap, query-preserving origin-relative redirects, scoped Secure
  HttpOnly cookies, authenticated settings GET/PUT, rejected Origin/CSRF, outside
  prefix/API/missing-asset boundaries, and forwarding classification/spoof sanitation.
- Narrow trusted upstream `127.0.0.1/32` plus actual `::1` client gave authenticated
  verified-local bypass after setup, with and without spoofed edge headers. Actual
  IPv4 loopback client gave all-hops-trusted fail-closed 401, including spoof attempts.
  No remote-network identity was fabricated or production bypass added.
- Focused Ruff passed. `UV_CACHE_DIR=/path/to/Editarr/.uv-cache
  uv run pytest -q tests/test_auth_network.py tests/test_backend_routing.py
  tests/test_production_frontend.py` passed: 66 tests, five existing deprecation warnings. `git diff --check`
  and new-file whitespace checks passed.

Acceptance criteria still unmet: None.

Approvals received or outstanding: Existing RP-00 behavior governs all examples.
Tool installation and isolated loopback listening received normal sandbox approval.
No database migration/wipe, architecture change, publication or global trust approval
was needed or requested.

Known failures/limitations: Runtime dependencies are reused from the locked project
site-packages; this is not a clean dependency installation claim. IPv6 loopback is
required for meaningful forwarded-local classification, and all-hops-trusted IPv4
is deliberately not described as remote-client testing. Real public-network traffic
was not used. The SPKI browser helper was generated/checked here; RP-13 owns actual
browser execution, media responses, Settings/restart transitions and broader
regression. Templates default to loopback/high ports; public operator listeners,
certificates, DNS, and external reloads remain manual. Temporary smoke processes/data
were cleaned; release artifact and installed environment remain available under /tmp.

Next exact action: RP-13 should use the fresh RP-12 installed artifact and import
`ProxyFixture`, with forms enabled and no CLI port override for its normal browser
restart flow. Reconfigure only the fixture proxy after a saved upstream/prefix
transition. Keep browser profiles disposable and use the provided leaf pin; never
install this CA into global trust or reuse real user data.
