# Local administrator authentication plan

> **Superseded.** Editarr no longer has local passwords. Every account comes from
> Jellyfin and signs in with a Jellyfin password or Quick Connect. See
> [Remove local password accounts (JF-06)](jellyfin-only-plan.md#remove-local-password-accounts-jf-06).
> The rest of this page is a historical record.

Status: **Complete — all local-authentication checkpoints and release verification pass**

Multi-user support later removed the local-address and external-authentication
bypasses and the single-administrator model. Every client now signs in with its own
account or API key; see [Run Editarr for several users](multi-user-guide.md). This
plan is kept as a historical record.

Plan date: **September 2, 2026**

Task tracker: [Local authentication implementation tasks](local-authentication-tasks/README.md)

This plan adds mandatory first-run creation of one local Editarr administrator,
forms-based browser authentication, and the authentication configuration patterns
used by current Radarr and Sonarr releases. It intentionally does not add multiple
users, roles, invitations, self-registration, or federated identity management.

The [feature and design specification](feature-design-spec.md) describes the implemented
product baseline. This document records the completed authentication design; verification
results are recorded in the task tracker.

## 1. Outcomes and non-negotiable behavior

- A fresh database has no administrator and is in `setup_required` state.
- Until an administrator exists, no catalog, settings, scan, queue, preview, image,
  EDL, OpenAPI, Swagger, or other application route or view is accessible.
- First-run setup creates exactly one administrator with full Editarr permissions.
- A second user cannot be created. The administrator cannot be deleted.
- Setup is available through the web UI, the one-time setup API, or a local CLI.
- Once setup is complete, Editarr supports the same high-level authentication choices
  as the \*Arr applications:
  - forms authentication for every client;
  - forms authentication disabled for local addresses; and
  - an external-authentication bypass for deployments protected by a reverse proxy or
    external identity provider.
- First-run setup overrides every bypass. Neither a local address nor external-auth
  mode can expose the application before the administrator has been created.
- Browser credentials and session identifiers are never written to `localStorage` or
  `sessionStorage`.

## 2. Reference behavior from Radarr and Sonarr

The design follows the current \*Arr conventions without copying their implementation
verbatim:

- Sonarr v4 and Radarr v5 made authentication mandatory and recommend a forms login.
- Their forms login is a compact, centered branded panel with username, password,
  "Remember Me," generic failure feedback, and a full-width primary action.
- Radarr uses an authenticated cookie and validates redirect targets before returning a
  user to the application.
- \*Arr supports `Enabled` and `DisabledForLocalAddresses` authentication-required
  modes.
- \*Arr supports an `External` method for installations that delegate authentication
  to a reverse proxy. It is an advanced configuration-file option rather than a casual
  UI toggle.
- \*Arr installations can be provisioned non-interactively through configuration and
  APIs. Editarr will provide a dedicated local CLI as its safer headless and recovery
  mechanism, in addition to the one-time setup API.

References:

- [Sonarr forced authentication](https://github.com/Servarr/Wiki/blob/master/sonarr/faq-v4.md#forced-authentication)
- [Radarr security settings](https://wiki.servarr.com/radarr/settings#security)
- [Radarr authentication controller](https://github.com/Radarr/Radarr/blob/develop/src/Radarr.Http/Authentication/AuthenticationController.cs)
- [Radarr login page](https://github.com/Radarr/Radarr/blob/develop/frontend/src/login.html)

## 3. State model and access matrix

### 3.1 Setup and session states

Editarr has three request states:

| State | Meaning |
| --- | --- |
| `setup_required` | The singleton administrator row does not exist. |
| `unauthenticated` | The administrator exists, forms authentication applies to this client, and the request has no valid session. |
| `authenticated` | The request has a valid administrator session or an explicitly configured post-setup bypass applies. |

The absence of the administrator row is the sole source of truth for first-run state.
Do not add a second boolean that could disagree with the database.

### 3.2 Post-setup authentication configuration

Store these values in a singleton `auth_settings` row using stable lowercase
identifiers. `EDITARR_AUTHENTICATION_METHOD` and `EDITARR_AUTHENTICATION_REQUIRED` may
override the persisted values for immutable/container deployments. Precedence is:
explicit environment override, then persisted row, then the secure defaults `forms`
and `enabled`. The API/status response must report when a value is externally overridden,
and web or CLI mutation must fail with an actionable message rather than pretending to
change an overridden value.

| Setting | Values | Behavior |
| --- | --- | --- |
| Authentication method | `forms`, `external` | `forms` uses Editarr sessions. `external` trusts the deployment's external authentication layer after setup. |
| Authentication required | `enabled`, `disabled_for_local_addresses` | `enabled` requires the selected method everywhere. The local-address option bypasses forms login only for locally classified clients. |

`external` is an advanced deploy-time/CLI setting and is not offered as a normal web
control. The web UI may display its active status and a warning. Switching into or out
of it requires a restart so a mistaken browser action cannot silently remove the
application's authentication boundary.

The required-mode setting can be managed in Settings > General > Security after the
administrator signs in. CLI/configuration must also support it.

### 3.3 Request access matrix

| Instance/client state | Allowed anonymous behavior |
| --- | --- |
| Setup required, any method or address | Authentication status and one-time setup only. CORS preflight needed for those endpoints may pass. |
| Configured, forms + enabled | Status and login only; all other routes require a valid session. |
| Configured, forms + disabled for local addresses, local client | Treat the request as the singleton administrator. |
| Configured, forms + disabled for local addresses, non-local client | Status and login only; all other routes require a valid session. |
| Configured, external | Treat requests as the singleton administrator and rely on the external layer. |

Unknown application routes, `/openapi.json`, `/docs`, and `/redoc` follow the same gate.
Static assets required to render setup or login may be served, but the React application
shell and protected views must not mount while access is denied.

## 4. Local-address and reverse-proxy trust

Classify the direct socket peer as local when Python's `ipaddress` handling identifies
it as loopback, private, link-local, or IPv6 unique-local. IPv4-mapped IPv6 addresses
must be normalized before classification.

Forwarded headers are untrusted by default. A client address from `Forwarded` or
`X-Forwarded-For` may be used only when the direct peer matches an explicitly configured
trusted-proxy address or network. The resolver must be a pure function over the direct
peer, the raw ordered header fields, and the configured networks; it must never perform
hostname or reverse-DNS lookup.

### 4.1 Direct peer and header-family selection

Parse the ASGI socket peer as a numeric IP address and normalize IPv4-mapped IPv6 to
IPv4 before matching or classification. Header names are case-insensitive, but values
must be read from the raw ASGI header sequence so repeated fields retain their received
wire order.

Apply these selection rules:

1. If the direct peer is absent or is not a numeric IP, resolution fails closed as
   described in section 4.4.
2. If the direct peer is not in a trusted-proxy network and no forwarding header is
   present, resolve and classify the direct peer.
3. If the direct peer is not trusted but either forwarding family is present, do not
   parse or trust those values. Retain the direct peer for attribution but force the
   request to nonlocal. This prevents a client from supplying forwarding metadata to
   gain the local-address bypass.
4. If the direct peer is trusted, exactly one forwarding family must be present. There
   is deliberately no precedence between `Forwarded` and `X-Forwarded-For`: if both are
   present, the input is ambiguous and fails closed. If neither is present, it also
   fails closed rather than treating the trusted proxy itself as the client.
5. Multiple fields from the one selected family are valid. Combine their field values
   in received order as if joined by a comma, matching HTTP list-field semantics. Do
   not sort, deduplicate, reverse, merge the two families, or accept a framework's
   arbitrary single-value choice.

Limit the selected family's aggregate raw value to 8,192 bytes and its parsed chain to
32 entries. Exceeding either bound is a malformed forwarding result. The byte limit is
measured before decoding or concatenation.

### 4.2 Accepted forwarding syntax

Both parsers accept ASCII only. Optional whitespace means only SP or HTAB and is accepted
at field boundaries and on either side of comma separators. Reject CR, LF, other control
characters, empty list elements, hostnames, IPv6 zone identifiers, non-decimal ports,
and ports outside `1..65535`. Normalize every accepted address through `ipaddress`,
including the IPv4-mapped IPv6 rule above, and discard a validated port before trust and
locality checks.

For `Forwarded`, implement the list and parameter structure from
[RFC 7239](https://www.rfc-editor.org/rfc/rfc7239.html), with this deliberately strict
address subset:

- Every comma-delimited forwarded-element must contain exactly one case-insensitive
  `for` parameter. Duplicate parameter names or an element without `for` invalidate the
  entire family. Other parameters may be ignored only after their token/quoted-string
  syntax and uniqueness have been validated.
- Permit SP or HTAB around commas and semicolons, but not around `=` or within a node
  value.
- Accept an unquoted numeric IPv4 address without a port. A quoted value may contain
  numeric IPv4 with an optional decimal port, or a bracketed numeric IPv6 address with
  an optional decimal port, for example `for="192.0.2.4:443"` and
  `for="[2001:db8::4]:443"`. IPv6 is never accepted unbracketed in this header.
- Reject backslash escapes inside quoted values. They are unnecessary for the accepted
  numeric node syntax and create alternate representations.
- Reject the special `unknown` identifier, obfuscated identifiers beginning with `_`,
  obfuscated ports, comments, and any non-IP node value. One invalid element invalidates
  the whole family; it is never skipped.

For `X-Forwarded-For`, accept only a comma-delimited list of numeric nodes:

- Accept bare IPv4 with an optional decimal port.
- Accept bare IPv6 only without a port. Accept bracketed IPv6 with or without a decimal
  port; brackets are required when an IPv6 port is present.
- Reject quotes, semicolons, parameters, `unknown`, obfuscated identifiers, hostnames,
  zone identifiers, empty elements, and whitespace within a node. One invalid element
  invalidates the whole family; it is never skipped.

### 4.3 Trusted-chain selection

Forwarding entries are ordered left-to-right from the oldest claimed client toward the
newest proxy. After validating the entire selected family, conceptually append the
already-validated direct peer to the right side of the chain. Starting immediately to
the left of the direct peer, walk header entries right-to-left:

1. Skip each address that belongs to a configured trusted-proxy network.
2. The first address not in a trusted-proxy network is the resolved client. Ignore all
   entries to its left because an untrusted client could have supplied them.
3. If every header entry is trusted, do not select the leftmost address. Treat the
   result as `all_hops_trusted` and fail closed. This protects against an accidentally
   broad trusted-proxy network turning a claimed local address into a bypass.

Only a fully valid chain may produce a forwarded resolved client. Classify that resolved
address using the same local-address rules as a direct peer.

### 4.4 Fail-closed result

The resolver must return a structured outcome containing the resolved address when one
is safely available, the locality decision, whether forwarding was valid and used, and
a stable reason code suitable for tests and diagnostics. Use at least these outcomes:
`direct`, `forwarded`, `untrusted_forwarding_header`, `missing_forwarding_header`,
`conflicting_forwarding_headers`, `malformed_forwarding_header`,
`forwarding_chain_too_long`, `all_hops_trusted`, and `missing_direct_peer`.

For forwarding metadata from an untrusted peer, or missing, conflicting, malformed,
oversized, overlong, or all-trusted metadata from a trusted peer, retain the normalized
direct peer as the attribution and login-throttle address but force `is_local = false`.
Do not fall back to classifying that trusted proxy as local. If the direct peer itself is
missing or malformed, return no resolved address, force `is_local = false`, and use one
shared `unknown` client bucket for throttling.

Fail-closed forwarding does not itself return an HTTP `400`: it disables only the
local-address bypass, so forms authentication proceeds normally. Never include rejected
raw header text in responses or logs. Operators must configure only the actual proxy
addresses or narrowly scoped networks and ensure each trusted proxy emits exactly one
supported header family. A local reverse proxy that emits no forwarding header cannot
be distinguished from a direct local client unless it is configured as trusted.

## 5. Persistence and security design

### 5.1 Singleton administrator

Add an `admin_users` table containing:

- fixed primary key `id = 1`;
- display username and normalized username;
- an Argon2id password hash;
- `created_at`, `updated_at`, and `password_changed_at` timestamps.

Enforce the singleton at the database and repository layers. Username comparison is
case-insensitive after Unicode normalization, while the normalized display form retains
the administrator's casing.

Apply one shared validation implementation across web setup, API setup/account changes,
CLI creation/rename/reset, and login. The username contract is:

| Rule | Policy |
| --- | --- |
| Canonical display value | Apply Unicode NFKC normalization, then trim leading and trailing Unicode whitespace. Store this result with its casing preserved. Do not collapse or trim interior spaces. |
| Length | 1 through 64 Unicode code points after NFKC normalization and trimming. Combining marks and each member of an emoji sequence count separately; do not count UTF-8 bytes or grapheme clusters. |
| Lookup value | Apply Unicode NFKC normalization to the display value's full Unicode `casefold()` result. Use this value for uniqueness and login comparison. Apply the same pipeline to submitted login usernames. |
| Disallowed characters | Reject any code point whose Unicode general category begins with `C` (`Cc`, `Cf`, `Cs`, `Co`, or `Cn`) and the line/paragraph separators `Zl` and `Zp`. This rejects NUL, tabs, newlines, bidi/zero-width format controls, surrogates, private-use, and unassigned code points. |
| Interior whitespace | Permit ordinary interior spaces after NFKC normalization; they remain significant. |

Setup and account-management endpoints return actionable validation errors. Login
applies the same canonicalization, but an invalid submitted username produces the same
generic authentication failure as an unknown username.

The password contract is:

| Rule | Policy |
| --- | --- |
| Canonical value | Apply Unicode NFC normalization before length validation, hashing, confirmation comparison, and verification. Otherwise preserve the input exactly: never trim, case-fold, collapse whitespace, or silently truncate it. |
| Length | 15 through 256 Unicode code points after NFC normalization. Combining marks and each member of an emoji sequence count separately; do not count UTF-8 bytes or grapheme clusters. |
| Disallowed characters | Reject Unicode control characters in category `Cc`, surrogate code points in `Cs`, and line/paragraph separators `Zl` and `Zp`. This rejects NUL, tabs, CR/LF, DEL/C1 controls, invalid scalar values, and line-breaking characters that cannot be entered consistently through every supported client. |
| Allowed characters | Permit spaces—including leading, trailing, and repeated spaces—plus printable ASCII, Unicode letters, symbols, emoji, and Unicode format characters. Every allowed character remains significant after NFC normalization. |
| Composition | Do not require uppercase, lowercase, digits, symbols, or periodic password changes. |

The maximum is checked before Argon2id work, and the entire canonical password is
hashed and verified. These rules follow the current
[NIST SP 800-63B password requirements](https://pages.nist.gov/800-63-4/sp800-63b.html#passwordver)
for a single-factor password while placing a bounded denial-of-service limit above its
required 64-character support floor.

Use Argon2id with the fixed OWASP profile `m=19456 KiB` (19 MiB), `t=2`, and `p=1`.
Configure `argon2-cffi` with `time_cost=2`, `memory_cost=19456`, `parallelism=1`, a
16-byte library-generated random salt, a 32-byte hash, and Argon2 version 19. Retain the
parameters in the encoded hash and use `check_needs_rehash` after successful verification
so a future approved profile can upgrade hashes. Validate the 256-code-point password
limit before invoking Argon2id. Generate one valid dummy hash at process initialization
and perform a real verification against it when the submitted username is unknown; never
use a fast-path hash or skip verification for an unknown username. This is the balanced
minimum profile recommended by the current
[OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
Never log request bodies or place plaintext passwords in returned objects.

### 5.2 Server-side sessions

Use these defaults and environment names. Durations are enforced by the server, not by
browser timers:

| Policy | Default | Environment override |
| --- | --- | --- |
| Ordinary-session idle lifetime | 30 minutes | `EDITARR_AUTH_SESSION_IDLE_SECONDS=1800` |
| Ordinary-session absolute lifetime | 8 hours | `EDITARR_AUTH_SESSION_ABSOLUTE_SECONDS=28800` |
| Remembered-session idle lifetime | 7 days | `EDITARR_AUTH_REMEMBER_ME_IDLE_SECONDS=604800` |
| Remembered-session absolute and cookie lifetime | 30 days | `EDITARR_AUTH_REMEMBER_ME_SECONDS=2592000` |

An ordinary session expires at the earlier of its idle or absolute deadline. Selecting
remember-me replaces both ordinary deadlines with the remembered idle and absolute
deadlines; it does not create an unbounded session. Only a remembered cookie receives
`Max-Age`, equal to the remembered absolute lifetime. All expiration checks are
server-side, and every idle lifetime must be positive and no greater than its matching
absolute lifetime.

Add an `auth_sessions` table containing:

- a digest of a cryptographically random opaque session token;
- the administrator foreign key;
- creation, last-use, absolute-expiry, and optional idle-expiry timestamps;
- whether the browser selected remember-me;
- a digest used to validate the session's CSRF token; and
- an authenticated-encrypted copy of that same CSRF token so an authenticated status
  request can recover it after a browser reload.

Only the raw opaque token is sent to the browser. Store its digest in SQLite so a
database disclosure does not immediately disclose active bearer tokens. Rotate the
session on setup and login. Revoke it on logout. Revoke all sessions after username or
password changes. Remove expired sessions during lookup and bounded startup cleanup.

Use this fixed token and digest contract:

| Value | Generation and wire encoding | Persisted representation |
| --- | --- | --- |
| Session token | 32 independent CSPRNG bytes (256 bits), encoded as canonical unpadded base64url: exactly 43 ASCII characters matching `[A-Za-z0-9_-]{43}`. | Lowercase 64-character SHA-256 hex digest of `b"editarr-session-v1\0" + raw_token_bytes`. |
| CSRF token | A separate 32 independent CSPRNG bytes (256 bits), encoded with the same canonical unpadded base64url rules. | The existing lowercase 64-character SHA-256 hex digest of `b"editarr-csrf-v1\0" + raw_csrf_bytes`, plus the versioned AES-256-GCM envelope below, stored on the same session row. |

Generate bytes with Python's `secrets.token_bytes(32)` or an equivalent operating-system
CSPRNG. On input, require the exact canonical 43-character form, decode it to exactly 32
bytes, and reject malformed, padded, truncated, overlong, or non-canonical encodings
before database work. Never accept multiple textual encodings of one token. The domain
prefixes prevent cross-purpose digest reuse. The random 256-bit inputs make an unkeyed
SHA-256 digest resistant to offline recovery. Use `hmac.compare_digest` for the CSRF
digest comparison; session lookup uses the indexed session digest as its database key.

The CSRF token is stable for the lifetime of its session. Setup and login return the new
token, and each authenticated `GET /api/auth/status` decrypts and returns that same token;
status calls never rotate it. This follows the per-session synchronizer-token pattern and
avoids one tab invalidating another tab or a page restored from browser history. The
[OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#synchronizer-token-pattern)
permits per-session tokens and warns that frequently rotated per-request tokens create
usability failures.

Add `cryptography` as a direct dependency and protect the recoverable copy with
`cryptography.hazmat.primitives.ciphers.aead.AESGCM` using this fixed contract:

- Use a 32-byte AES-256 key and a new 12-byte CSPRNG nonce for every session issuance.
- Encrypt the raw 32 CSRF bytes. Bind the envelope to its session using associated data
  `b"editarr-csrf-wrap-v1\0" + bytes.fromhex(session_token_digest)`.
- Store ASCII `v1.<key-id>.<nonce>.<ciphertext-and-tag>` in a new non-null
  `csrf_token_ciphertext` column. `key-id` is the first 16 lowercase hex characters of
  `SHA-256(key)`; `nonce` is canonical unpadded base64url of 12 bytes (16 characters);
  and `ciphertext-and-tag` is canonical unpadded base64url of the 32-byte ciphertext plus
  16-byte GCM tag (64 characters).
- On recovery, strictly validate the envelope and key ID, authenticate/decrypt it with
  the same associated data, re-encode the plaintext canonically, and confirm that its
  existing CSRF digest matches before returning it. Treat any parsing, key-ID, GCM, or
  digest failure as an invalid session: revoke the session, return generic unauthenticated
  state, and never expose which check failed.

Resolve the AES key before accepting HTTP requests. `EDITARR_AUTH_CSRF_ENCRYPTION_KEY`,
when set, has precedence and must be exactly one canonical unpadded base64url encoding of
32 bytes. Otherwise load `<data_dir>/secrets/auth-csrf.key`; when it is absent, create the
directory as `0700` and atomically create a regular, non-symlink key file as `0600`
containing the same 43-character encoding and one trailing newline. Concurrent creators
must use exclusive creation, and the loser must read the completed file. If an existing
database has sessions, the startup key-ID check below revokes them before service begins.
Refuse to start on an unreadable, malformed, non-regular, symlinked, group-readable, or
world-readable existing key file; never overwrite it silently and never log the key.

The key is long-lived and must be backed up with the database if existing browser
sessions are expected to survive a restore. Environment-managed deployments should keep
it in their secret manager rather than the database or image. Replacing or losing the key
does not affect the administrator password, but it invalidates every existing session.
At startup, use the envelope key IDs to revoke all active sessions that do not use the
loaded key before serving requests, and emit the normal secret-free session-revocation
audit event. V1 does not support retaining old decryption keys or online key rotation.

Each expired-session cleanup invocation deletes at most **500 rows** in one transaction.
Candidates include revoked sessions and sessions whose idle or absolute deadline is at
or before the supplied UTC time. Select candidate IDs in ascending primary-key order,
then delete only those IDs. Return the number deleted so a later scheduled or manual run
can continue draining the table, but do not loop through multiple batches during startup.
A request that presents an expired session may delete that one matched row immediately;
that targeted deletion does not consume or expand the 500-row bulk-cleanup limit.

A valid session is eligible for an activity refresh after a request has authenticated
with that session and passed every applicable CSRF and Origin check. Safe authenticated
requests, including `GET /api/auth/status`, count as activity. Malformed, unknown,
expired, or revoked tokens; rejected CSRF/Origin checks; login/setup attempts; logout;
and local/external-bypass requests do not refresh a session.

Do not write on every eligible request. Refresh only when the stored `last_used_at` is at
least the effective touch interval old. The default touch interval is **5 minutes**. If
the applicable configured idle lifetime is shorter than 10 minutes, use one-half of that
idle lifetime instead so sustained legitimate activity cannot expire solely because of
write coalescing. On refresh, atomically set `last_used_at` to the request's captured UTC
time and set `idle_expires_at` to the earlier of that time plus the applicable ordinary
or remembered idle lifetime and `absolute_expires_at`. Never extend or rewrite the
absolute deadline. The conditional update must require the session to remain unrevoked
and unexpired, must not move timestamps backward when requests race, and must not
resurrect a concurrently revoked session. Activity inside the touch interval performs no
session-row write; consequently, the recorded last-use time may trail real activity by
up to—but never more than—the touch interval.

The cookie defaults are:

| Policy | Default | Configuration and constraints |
| --- | --- | --- |
| Name | `editarr_session` | `EDITARR_AUTH_COOKIE_NAME`; validate as an HTTP cookie token. Give co-hosted Editarr instances distinct names because cookies do not distinguish ports. |
| SameSite | `lax` | `EDITARR_AUTH_COOKIE_SAMESITE`; accept only `lax` or `strict`. Do not support `none` in v1. |
| Secure | `true` | `EDITARR_AUTH_COOKIE_SECURE`; environment-only. `false` is the explicit local-HTTP development escape hatch and must emit a prominent startup warning. Never infer it from the request scheme or forwarded headers. |
| HttpOnly | `true` | Mandatory and not configurable. |
| Path / Domain | `Path=/`; no `Domain` | Mandatory and not configurable. |

Production and reverse-proxy deployments must leave `Secure=true` and terminate HTTPS
before the browser reaches Editarr. A plain-HTTP local development instance must
explicitly set `EDITARR_AUTH_COOKIE_SECURE=false`; this exception is not exposed through
the database, API, web UI, or CLI. Support a non-persistent browser session by default
and the bounded persistent cookie above for remember-me. Follow the
[OWASP session management guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

Add an `auth_settings` singleton table for the persisted method and required-mode
values. Its presence never marks setup complete; only `admin_users.id = 1` does that.

### 5.3 CSRF, login abuse, and errors

- Require a per-session CSRF token in `X-CSRF-Token` for unsafe cookie-authenticated
  requests, using the exact transport and origin contracts below.
- Return the CSRF value only to an authenticated same-origin client and retain it only
  in React memory.
- Apply origin checks to setup and login where a browser supplies an origin.
- In local-bypass and external modes, where no Editarr login session exists, reject
  cross-site unsafe browser requests using strict Origin and Fetch Metadata checks.
  Non-browser API clients without browser-origin headers remain governed by the selected
  bypass mode.
- Apply bounded login throttling by both client address and normalized username.
- Keep username/password failure responses generic and approximately timing-consistent.
- Log successful login, logout, throttling, rejected login, setup, and credential change
  events without passwords, cookies, CSRF values, or full request bodies.
- Use machine-readable error codes alongside safe user-facing detail.

#### CSRF request transport

Every request authenticated by an Editarr session cookie whose method is not `GET`,
`HEAD`, or `OPTIONS` must supply exactly one `X-CSRF-Token` header. This includes logout,
account/security changes, and all protected application mutations. Header names are
case-insensitive; values must be the canonical 43-character unpadded base64url token from
section 5.2. Inspect raw header occurrences and reject repeated headers, comma-joined
values, whitespace, missing values, and noncanonical encodings. Do not accept tokens
from cookies, query strings, form fields, or JSON bodies as substitutes. Disable `TRACE`
rather than treating it as an exempt application operation.

Verify the token against the authenticated session's stored CSRF digest with
`hmac.compare_digest`. Failure returns `403` with `code="csrf_failed"` before endpoint
execution or session-activity refresh, without token details. Safe requests require no
CSRF header. Setup/login are bootstrap/credential flows rather than cookie-authenticated
mutations, even if an ambient session cookie exists, and use the origin checks below;
sessionless local/external bypass does not invent a CSRF token. The presence
of a valid session cookie must not silently downgrade a request to bypass mode to evade
CSRF validation.

#### Authoritative public origin

Add environment-only `EDITARR_AUTH_PUBLIC_ORIGIN`, required before starting the HTTP
application in every authentication mode, including first-run setup. It has no inferred
default and is not editable through the database, UI, or API. Missing or invalid values
fail HTTP startup with an actionable configuration error. Offline administrator CLI
commands do not require this setting.

The value is the one browser-facing origin, for example `https://editarr.example.com`
or `http://localhost:5173`. Accept only `http` or `https`, a DNS hostname or numeric IP,
and an optional decimal port in `1..65535`. An optional single trailing `/` is accepted
and removed. Reject userinfo, other paths, queries, fragments, wildcards, lists, opaque
origins such as `null`, whitespace/control characters, backslashes, zone identifiers,
and trailing-dot hostnames. Require ASCII DNS names (IDNs must be supplied as punycode)
and bracketed IPv6. Canonicalize scheme and DNS casing, numeric IP spelling, and default
ports (`http:80` and `https:443`). Compare the resulting `(scheme, host, effective port)`
tuple exactly, never by suffix, substring, DNS lookup, or IP-network equivalence.

HTTPS is required with `EDITARR_AUTH_COOKIE_SECURE=true`. Local HTTP development requires
both an explicit `http://...` public origin and `EDITARR_AUTH_COOKIE_SECURE=false`.
The setting describes the external browser URL even when a TLS proxy talks HTTP to
Uvicorn. Never derive or expand this trust from `Host`, the ASGI scheme, `Forwarded`,
`X-Forwarded-Host`, `X-Forwarded-Proto`, or the trusted client-IP networks. Client-IP trust
and browser-origin trust are separate. V1 supports one public origin, not automatic
hostname/IP aliases. Development should proxy `/api` through the frontend dev server
so the browser sees one origin; a CORS allowlist is not an additional authentication
origin allowlist. Credentialed CORS for authenticated API responses must not authorize
any origin other than this configured public origin.

#### Source-origin checks and failure behavior

On unsafe requests, including setup/login and local/external bypass:

1. Prefer one `Origin` header when present. Require a single serialized HTTP(S) origin
   matching the configured tuple, with no path (not even `/`), query, or fragment.
   Empty, `null`, repeated, list-valued, or malformed
   values fail; never fall back from an invalid `Origin` to `Referer`.
2. Only when `Origin` is absent, use one absolute HTTP(S) `Referer` URL and compare its
   origin tuple. Ignore its path and query for comparison; reject userinfo, fragments,
   malformed URLs, and repeated values. Neither header's raw value is logged.
3. If `Sec-Fetch-Site` is present, require one value of `same-origin` for unsafe browser
   requests. Reject `same-site`, `cross-site`, `none`, duplicate, and unknown values even
   if another header appears to match. Fetch Metadata cannot override an origin mismatch
   and is not a substitute for `Origin`/`Referer` when those are required.
4. With neither `Origin` nor `Referer`, reject unsafe session-cookie requests. For
   setup/login or sessionless local/external requests only, allow the existing headless
   API flow when all `Sec-Fetch-*` headers are also absent; otherwise reject. This is not
   proof that a client is non-browser, and does not relax the setup/authentication gate.

Trim only outer SP/HTAB on source-origin header fields before parsing; reject other
whitespace/controls. If both `Origin` and `Referer` exist, `Origin` is authoritative.
Origin rejection returns generic `403` with `code="origin_failed"` and emits
`auth.origin_rejected` with no raw header data. After setup/authentication access gating,
run origin checks before CSRF checks and before any endpoint or session-activity refresh.

For authenticated `GET /api/auth/status`, validate `Origin` or, when absent, `Referer`
if supplied, and reject non-`same-origin` `Sec-Fetch-Site` if supplied before returning
the token. A same-origin fetch or headless client may omit these headers; do not require
them on this safe request. The browser same-origin policy, restrictive credentialed
CORS, and non-cacheable responses protect token delivery. These rules use OWASP's
[server-configured target-origin approach](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#identifying-the-target-origin).

Use two independent sliding-window failure buckets for login:

| Bucket | Default limit | Window |
| --- | --- | --- |
| Normalized submitted username | 5 failed credential verifications | 15 minutes |
| Resolved client address | 25 failed credential verifications | 15 minutes |

The corresponding overrides are
`EDITARR_AUTH_LOGIN_USERNAME_ATTEMPTS=5`,
`EDITARR_AUTH_LOGIN_ADDRESS_ATTEMPTS=25`, and
`EDITARR_AUTH_LOGIN_WINDOW_SECONDS=900`. A request may proceed only while both buckets
are below their limits. The fifth username failure and twenty-fifth address failure are
processed normally; later attempts within the window return the same generic `429`
response regardless of which bucket fired. Count only failed credential verifications.
A successful login clears that normalized username's bucket, while address failures age
out with the sliding window. Use the trusted-proxy-aware resolved client address from
section 4, do not key on an address/username pair, and do not reveal remaining attempts
or the limiting bucket.

External authentication removes Editarr's login check but does not disable input
validation, request logging, or origin/CSRF defenses that can be applied without an
Editarr session. The external layer is responsible for identity verification and must
not expose Editarr directly.

### 5.4 Authentication audit log

Write authentication security events as UTF-8 JSON Lines through a dedicated
non-propagating `scanner.auth.audit` logger. Each line is one JSON object with exactly
this required envelope; fields that do not apply are present as `null` so consumers can
use a stable schema:

| Field | Format |
| --- | --- |
| `schema_version` | Integer `1`. |
| `occurred_at` | Server-generated UTC RFC 3339 timestamp with milliseconds and `Z`. |
| `event_id` | Server-generated UUIDv4 string, unique to the audit event. |
| `request_id` | Server-generated request/operation UUIDv4, or `null` for startup events. Never trust a client-supplied value for this field. |
| `application` | Literal `editarr`. |
| `event` | Stable allowlisted event name from the list below. |
| `outcome` | `success`, `failure`, or `denied`. |
| `severity` | `info`, `warning`, or `error`. |
| `source` | `http`, `cli`, or `system`. |
| `client_address` | Canonical resolved client IP for HTTP events; `null` only when the socket peer is missing or malformed, or for non-HTTP events. |
| `actor` | `administrator`, `anonymous`, or `system`. |
| `admin_user_id` | Integer `1` only when authenticated/known, otherwise `null`. |
| `session_id` | Internal integer session-row ID when relevant, otherwise `null`; never a token or digest. |
| `http_method` | Uppercase method for HTTP events, otherwise `null`. |
| `route` | Allowlisted route template such as `/api/auth/login`, never a raw URL or query string; otherwise `null`. |
| `http_status` | Integer response status for HTTP events, otherwise `null`. |
| `reason_code` | Stable allowlisted reason, or `null`; login failure uses only `invalid_credentials`, never separate unknown-user and wrong-password reasons. |
| `changed_fields` | Sorted allowlisted field-name array for account/security changes, otherwise an empty array. Values are never included. |

Allowlisted event names are `auth.audit_started`, `auth.setup`, `auth.login`,
`auth.login_throttled`, `auth.logout`, `auth.account_changed`,
`auth.security_changed`, `auth.sessions_revoked`, `auth.session_rejected`,
`auth.csrf_rejected`, and `auth.origin_rejected`. Successful setup/login/logout and
account/security changes are `info`; rejected authentication, throttling, CSRF/Origin
failures, and invalid sessions are `warning`; audit-subsystem failures reported to the
ordinary application logger are `error`.

Construct records from typed/allowlisted values and serialize them with a JSON encoder;
do not build JSON through string interpolation. Never include raw or normalized submitted
usernames, passwords, password hashes, session or CSRF values or digests, cookies,
authorization headers, request/response bodies, query strings, user agents, exception
representations containing request data, or arbitrary user-supplied text. The singleton
`admin_user_id=1` is sufficient to identify the successful actor without duplicating the
display username. Tests must inject distinctive canary secrets and prove they are absent
from serialized records and ordinary log output.

The destination is `<data_dir>/logs/auth-audit.jsonl`, outside every web-served path.
Create the log directory with owner-only permissions (`0700`) and each audit file with
`0600` permissions, without relaxing an already stricter mode. Rotate at UTC midnight.
Retain the current file plus completed daily files whose rotation date is no more than
30 days old; delete older audit segments during logging initialization and after each
rollover. Retention is fixed at 30 days in v1 and is not an API, web, CLI, or database
setting. Operators that ship the JSONL stream to another system are responsible for that
system's access controls and retention.

An audit-file failure must never expose secrets or silently switch to a less protected
destination. Report a rate-limited `error` through the ordinary application logger and
continue the authentication operation so a permissions or disk-space problem cannot
become a permanent authentication lockout. Emit `auth.audit_started` after successful
initialization. Follow the current
[OWASP logging guidance](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
for event coverage, sanitization, protection, and verification.

## 6. API design

Place authentication schemas, persistence operations, and request-policy logic in
focused modules rather than expanding `scanner/api.py` with all implementation detail.

Planned endpoints:

| Method and path | Anonymous availability | Purpose |
| --- | --- | --- |
| `GET /api/auth/status` | Always | Return `setup_required`, `unauthenticated`, or `authenticated`; for an authenticated forms session, decrypt and return its stable per-session CSRF token without rotating it. |
| `POST /api/auth/setup` | Setup required only | Atomically create the singleton administrator and a new session. |
| `POST /api/auth/login` | Configured instance | Validate credentials and create a rotated session. |
| `POST /api/auth/logout` | Authenticated session | Revoke the current session and clear its cookie. |
| `PUT /api/auth/account` | Authenticated administrator | Change username and/or password after current-password verification; revoke sessions. |
| `PUT /api/auth/security` | Authenticated administrator | Change the authentication-required mode. |

Suggested error semantics:

| Status | Code | Meaning |
| --- | --- | --- |
| 401 | `authentication_required` | Forms authentication applies and no valid session exists. |
| 403 | `csrf_failed` | Authentication succeeded but CSRF validation failed. |
| 403 | `origin_failed` | Request-origin validation failed. |
| 409 | `setup_already_complete` | Another request already created the administrator. |
| 423 | `setup_required` | A protected route was requested before setup. |
| 429 | `login_throttled` | The login rate limit was exceeded. |

The setup transaction must be race-safe. A database uniqueness failure from a losing
request becomes `409` and must not overwrite credentials or create an orphan session.
Every response that contains a CSRF token must set `Cache-Control: no-store` and
`Pragma: no-cache`, and the token must never enter URLs, application/audit logs, exception
text, analytics, or persisted frontend storage.

## 7. Startup and worker behavior

The startup sequence becomes:

1. Secure the data directory, then load or atomically create the CSRF encryption key.
2. Create the current schema and revoke active sessions whose CSRF envelopes use a
   different key ID.
3. Clean up bounded expired authentication sessions.
4. Check whether the administrator exists.
5. If setup is required, expose only setup/auth routes and do not recover or schedule
   scan work.
6. If setup is complete, perform current queue recovery and start the scheduler.

Completing setup through the running API must start normal queue processing exactly
once. CLI provisioning before process startup follows the normal configured path.
Shutdown behavior remains unchanged.

## 8. Headless provisioning and recovery CLI

Extend the `editarr` entry point with commands that reuse the same domain service as the
web API:

```text
editarr admin status
editarr admin create --username <name>
editarr admin create --username <name> --password-stdin
editarr admin reset-password
editarr admin reset-password --password-stdin
editarr admin rename --username <name>
editarr auth show
editarr auth set-method forms|external
editarr auth set-required enabled|disabled_for_local_addresses
```

Password input uses a hidden `getpass` prompt by default. `--password-stdin` supports
container secrets and automation. Do not add a plaintext `--password` option because it
would leak through process listings and shell history. Commands must not print password
hashes, session tokens, or CSRF values.

`admin create` initializes the schema if necessary and fails safely if the administrator
already exists. Credential recovery should normally be run while the API is stopped;
document concurrency and file-permission expectations. Password reset and rename revoke
all sessions.

## 9. Web application design

### 9.1 Authentication gate

Add a top-level gate in `web/src/main.tsx` or a focused component mounted from it. The
gate requests only `/api/auth/status` before deciding what to render:

- `setup_required` mounts only the administrator setup page;
- `unauthenticated` mounts only the login page;
- `authenticated` mounts the existing `App` component.

The current sidebar, source navigation, settings requests, queue/history poller, and all
route views must remain unmounted while setup or login is required. There must be no
protected-content flash. Preserve an attempted internal hash route in memory and restore
it after successful login. A later `401` must synchronously leave the protected shell and
show login.

Centralize `fetch` behavior in `web/src/api.ts` so credentials, CSRF headers, structured
errors, and authentication-expiry notifications apply consistently. Blob/image GETs
must continue to authenticate through the cookie without putting a token in their URLs.
Send the in-memory token only as `X-CSRF-Token` on unsafe cookie-authenticated requests,
and use the configured browser-facing origin (including the dev-server `/api` proxy).

### 9.2 First-run setup page

Use an Editarr-branded \*Arr-style centered panel:

- Editarr logo header;
- `CREATE ADMIN ACCOUNT` heading;
- username, password, and confirm-password fields;
- inline validation and safe server error;
- a full-width primary create button;
- disabled/loading state during submission.

Successful setup signs the administrator in and mounts the requested application route.
If another setup request wins the race, refresh status and show login without revealing
the winning username.

### 9.3 Login page

Use the same panel and the heading `SIGN IN TO CONTINUE`, with username, password,
remember-me checked by default, full-width login action, and generic incorrect-credential
feedback. Use correct password-manager autocomplete attributes and never retain the
password after a failed or successful request longer than necessary.

### 9.4 Administrator and security settings

Add Settings > General controls for:

- current username;
- username change;
- password change with current-password verification and confirmation;
- authentication-required mode;
- read-only display of external mode with an explicit deployment warning;
- logout.

Add a persistent logout action in the authenticated application shell when forms login
applies. Local-bypass and external-mode UI must clearly identify why no login was
required.

All new views inherit the accessibility and responsive requirements in the feature
specification, including keyboard-only operation, visible focus, live status/error
announcements, a 320px viewport, and automated axe coverage.

## 10. Native-client boundary

The current Samwise native client still targets retired endpoints and its migration is
already pending in the historical implementation plan. This authentication feature does
not revive those endpoints. Unauthenticated native requests receive the same `401` or
`423` responses as any other client.

When the native-client migration is implemented, it must add explicit setup/login and
cookie-session handling or another separately approved machine-client credential design.
Do not weaken the server gate solely to preserve the current native prototype.

## 11. Documentation changes at implementation completion

- Update `README.md` with the new first-run flow, HTTPS requirement, reverse-proxy
  examples, local bypass, external mode, CLI provisioning, and lockout recovery.
- Update `docs/api-contract.md` with authentication resources, endpoint schemas, cookie
  and CSRF behavior, and new error codes.
- Update `docs/feature-design-spec.md` so single-administrator authentication is part of
  the implemented baseline while multi-user roles remain out of scope.
- Document trusted-proxy configuration and show why forwarded headers are ignored by
  default.
- Remove the README statement that Editarr has no built-in authentication only after the
  full gate is verified.

## 12. Verification strategy

Backend coverage must include:

- singleton creation and concurrent setup;
- password validation, Argon2id hashing, and secret redaction;
- session issue, lookup, expiry, rotation, cleanup, logout, and mass revocation;
- CSRF success/failure;
- login throttling and generic errors;
- local IPv4, loopback, IPv4-mapped IPv6, private IPv6, public IPv6, trusted proxy, and
  spoofed-forwarded-header classification;
- all combinations in the access matrix;
- setup-gated startup and exactly-once scheduler activation;
- CLI prompting, stdin provisioning, conflict, reset, rename, and auth configuration;
- an automated inventory assertion that every FastAPI application route is explicitly
  public or protected.

Web coverage must include:

- no protected requests or view flash before status resolves;
- setup, setup conflict, login, failure, logout, and session-expiry transitions;
- deep-link restoration;
- account and security setting changes;
- forms enabled, local bypass, and external-mode indicators;
- keyboard, accessible names, status announcements, axe, and narrow viewport behavior.

The release checkpoint runs:

```bash
uv run ruff check scanner tests
uv run pytest -q
uv run pytest -q --cov=scanner
cd web && pnpm build && pnpm test && pnpm test:e2e && pnpm test:coverage
git diff --check
```

## 13. Definition of done

Authentication is complete only when all of the following are true:

- A clean database exposes only setup and cannot start scans or render application
  views.
- Exactly one administrator can be created from web, API, or CLI.
- Every existing API and web view is gated according to the access matrix.
- Forms login, remember-me, logout, session expiry, credential changes, local bypass,
  and external mode work and are covered by automated tests.
- Passwords and bearer tokens never appear in the database in recoverable form, logs,
  API responses, URLs, or browser storage.
- Trusted-proxy behavior cannot be enabled accidentally and spoofed forwarded headers
  do not create a local-address bypass.
- Setup and login pages match the established \*Arr visual pattern while using Editarr
  branding and meeting accessibility requirements.
- CLI provisioning and lockout recovery are documented and tested.
- The full verification suite passes and documentation describes actual behavior.

## 14. Required implementation approval

This work adds database tables. The repository's v1 policy forbids adding a
data-preserving migration solely for development data. Before the first schema-changing
coding task starts, obtain explicit user confirmation to skip a migration and wipe the
development database. Do not delete or replace the database as part of planning, and do
not infer approval from approval of this document.
