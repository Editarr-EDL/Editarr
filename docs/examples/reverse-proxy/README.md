# Prefix-preserving TLS proxies

These complete templates publish one installed Editarr HTTP listener through TLS.
`nginx.conf.template` is a standalone Nginx configuration, and `Caddyfile.template`
is a standalone Caddy configuration. `scripts/proxy_fixture.py` renders and validates
both with temporary paths and tests each at `/`, `/editarr`, and `/media/editarr`.
They contain no global includes, cache modules, service-manager integration, or ACME
issuance. TLS uses an explicitly supplied certificate and private key.

For an operator-managed deployment, copy a template to a separate configuration and
replace every `@@TOKEN@@` below. Use absolute paths without quotes/newlines in token
values; the template already quotes filesystem paths. Create the writable ROOT first.
Do not point the fixture runner at an existing installation's data directory.

| Token | Dedicated hostname | Subpath | Nested subpath |
| --- | --- | --- | --- |
| `HOST` | `editarr.example.com` | `example.com` | `example.com` |
| `PORT` | `443` | `443` | `443` |
| `UPSTREAM` | `3348` | `3348` | `3348` |
| Nginx `MATCH` | `^/` | `^/editarr(?:/|$)` | `^/media/editarr(?:/|$)` |
| Caddy `MATCH` | `/*` | `/editarr /editarr/*` | `/media/editarr /media/editarr/*` |
| Editarr Base URL | `https://editarr.example.com` | `https://example.com/editarr` | `https://example.com/media/editarr` |

`ROOT` is the proxy's private writable runtime directory; `CERT` is your certificate
chain PEM and `KEY` its private key PEM. Restrict key access. For public deployment,
replace the loopback `listen` addresses in Nginx with the intended public interface
(e.g. `listen 443 ssl;` and `listen [::]:443 ssl;`). Replace Caddy's loopback `bind`
with the intended interface or remove it to listen normally. The executable fixture
intentionally binds only IPv4/IPv6 loopback and uses high temporary ports.

The application receives the original complete path: `/editarr/api/auth/status`
remains `/editarr/api/auth/status`. Nginx `proxy_pass` has **no URI or trailing slash**;
Caddy uses `handle`, not `handle_path`, and performs no URI rewrite. The final fallback
returns 404 outside the prefix. These choices follow the official
[Nginx proxy_pass documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass)
and [Caddy reverse_proxy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

Set `EDITARR_BASE_URL` to the public HTTPS URL before first launch, or save it in
General Settings. Saved Base URL takes precedence over the environment seed. Keep
Secure cookies enabled even though the upstream uses HTTP. Keep the upstream private;
for this same-machine topology, bind Editarr to its normal `127.0.0.1` listener and set
`EDITARR_AUTH_TRUSTED_PROXY_NETWORKS='["127.0.0.1/32"]'`. For a separate-machine proxy,
use its actual direct source address with a narrow network instead, and explicitly
configure the upstream bind/firewall. Do not trust every private network by default.
Do not enable Uvicorn proxy-header rewriting. Proxy transport alone is not external
authentication; these examples use Editarr's forms authentication.

Each edge replaces X-Forwarded-For with the actual connecting client and removes
Forwarded, X-Real-IP, and client-supplied forwarding host/port/prefix values. The
scheme is replaced with the actual edge scheme. Do not change this to append arbitrary
client XFF. If another trusted proxy/CDN is added in front, design and verify that
chain explicitly; these configurations demonstrate one edge. Cookie, Origin and
X-CSRF-Token pass through normally. No cookie path/domain rewriting is needed.
Nginx proxy caching is explicitly disabled; stock Caddy has no cache configured.
Keep it that way for API/bootstrap/auth responses. Upstream Cache-Control,
Content-Type, Set-Cookie, and origin-relative Location responses pass through.
Hashed assets remain cacheable by browsers. Cookie paths are routing scopes, not a
security boundary between mutually untrusted applications on one origin.

When changing Editarr **Port**, update `UPSTREAM` and syntax-check/reload your external
proxy at the appropriate point after the application restart. When changing the Base
URL prefix, update both MATCH patterns for your chosen proxy and any public routing;
leave the complete upstream request path intact. A host/public-port change also
requires the matching listener, certificate and DNS configuration. Save does not apply
pending application changes; Restart Editarr does. The application never rewrites
these external proxy files. Base URL origin/prefix changes require forms login again.

For a rendered configuration, validate it before starting your proxy:

```sh
nginx -t -p /absolute/runtime -c /absolute/nginx.conf
caddy validate --adapter caddyfile --config /absolute/Caddyfile
```

# Isolated executable verification

Build/install the artifact using `docs/release-build.md`, then run from the checkout:

```sh
.venv/bin/python scripts/proxy_fixture.py \
  --editarr '/tmp/Editarr RP12 installed env/bin/editarr' \
  --dependency-site-packages "$PWD/.venv/lib/python3.11/site-packages"
```

`--proxy nginx` or `--proxy caddy` limits the matrix. Missing tools fail explicitly.
The runner uses installed `scanner` code from a temporary working directory, with
Node/Vite absent from the backend PATH. The optional reused dependency environment
is explicit; this is not a clean dependency-install claim. IPv4 and IPv6 loopback
must both work. A normal sandbox approval may be required to bind these listeners.

Every fixture creates an isolated database, deployment file, keys, CA, leaf
certificate, proxy configuration, logs, Caddy storage and process groups. It stops
only its own processes and removes its temporary tree on exit, including failure.
A failure prints logs before cleanup. No global certificate installation, proxy
configuration change, or service activation occurs. Tool installation is separate
and may install ordinary package dependencies; no `brew services` command is used.

The Python HTTPS client validates the generated CA and localhost/IP SANs with
`ssl.create_default_context(cafile=fixture.ca)`; verification is never disabled.
For RP-13 Chromium, launch a **disposable browser process** with
`args=[fixture.chromium_certificate_arg()]` and leave `ignore_https_errors=False`.
The returned Chromium switch makes a process-local exception for only this fixture
leaf public-key pin; it is not OS trust or proof of browser CA-chain validation.
The Python matrix separately proves chain/hostname validation. Other browser engines
need an equivalent isolated certificate configuration; do not silently disable all
certificate checks or install the CA into the user's trust store.

Import contract (put the repository `scripts` directory on the harness import path):

```python
from proxy_fixture import ProxyFixture

with ProxyFixture("nginx", installed_editarr, dependency_site_packages,
                  "/editarr", authentication_required="enabled") as fixture:
    # origin, prefix, port, upstream_port, data, root, ca, cert, backend are available.
    # Launch a disposable Chromium with fixture.chromium_certificate_arg().
    # Drive real Settings/Restart before following a changed upstream or prefix:
    fixture.reconfigure(upstream_port=new_port, prefix="/media/editarr")
    fixture.wait_ready()
```

Default launch uses a saved temporary initial port and ordinary `editarr` (no CLI
port override), so Settings can change the listener. Use `cli_port_override=True`
only for the explicit override scenario. `request(path, method=..., body=dict,
headers=dict, ipv6=False)` returns `(status, email-message-headers, bytes)` and does
not follow redirects or manage cookies. `reconfigure` syntax-checks the candidate
before stopping this fixture's proxy, then starts it on the existing TLS port. It
never changes app settings or restarts the backend. Call `wait_ready` when the new
app listener is expected to be available. Invalid candidate syntax leaves the old
proxy running; a new process bind failure raises during readiness and is not a
successful transition. Use the context manager or `close()` for guaranteed cleanup.

Sign-in has no address-based bypass. The matrix sends unauthenticated requests
through a trusted upstream peer `127.0.0.1/32` from both a real IPv4 and a real
`::1` client, with and without spoofed forwarding headers, and each one receives
401 without a session cookie. This proves edge sanitation; it does not test a
remote public-IP client.

RP-12 passed all six matrix cells with Nginx 1.31.5, Caddy 2.11.4 and OpenSSL 3.6.4
on macOS arm64. Each cell validates syntax before launch and after reconfiguration,
then checks actual HTTPS HTML/assets/cache/query behavior, mounted API boundaries,
Secure scoped cookies, Origin/CSRF acceptance and rejection, origin-relative slash
redirects, and the two forwarding classifications. RP-13 owns actual browser flows,
media playback and application settings/restart transitions.
