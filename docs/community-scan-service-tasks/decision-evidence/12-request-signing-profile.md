# CSS-12 signed-request profile v1

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

This is a constrained RFC 9421 HTTP Message Signatures / RFC 9530 Content-Digest
profile, not general-purpose signature negotiation. The synthetic, deterministic
Ed25519 fixtures are in
`community-service/test/fixtures/request-signatures-v1.json`. Their private test
key is the 32 bytes `00..1f`; never enroll or use it outside tests. The fixture
contains the public raw 32-byte key, exact target URL, exact transmitted body
base64, complete headers and ASCII signature-base string. Regenerate/check the
expected header and base bytes with
`.venv/bin/python -m pytest -q tests/test_community_request_signatures.py`;
verify the independent Python-signed bytes in workerd with
`pnpm --dir community-service exec vitest run test/request-signatures.test.ts`.

## Canonical byte contract

- TLS and canonical absolute ASCII `https://` URI required. Host spelling is
  lowercase; omit default port 443. The verifier takes the trusted expected
  authority separately and rejects a different origin, URL normalization,
  credentials, fragment, dot-segment or encoded path separator. The path is
  the exact path component (or `/`), not a decoded route. `@query` includes
  the initial `?`, preserves percent escapes, order and `+`, and is `?` if
  absent. No Unicode or arbitrary URL normalization is negotiated by v1.
- The sole `Signature-Input` and `Signature` dictionary label is `editarr`.
  The former is an inner list of quoted component identifiers with no
  component parameters. In order, every request covers `@method`, `@scheme`,
  `@authority`, `@path`, `@query`, `content-digest`. Methods are uppercase
  `GET`, `POST`, `PUT`, `PATCH`, `DELETE`. Non-GET requests then cover
  `content-type`, `idempotency-key`, and, if present, `content-encoding`.
  GET has no body, content metadata or operation key. Mutations use
  `application/json` or `application/octet-stream`, optional `gzip`, and an
  ASCII `[A-Za-z0-9._~-]` operation ID of 1..128 characters.
- The single RFC 9530 `Content-Digest` value is exactly
  `sha-256=:` + standard padded base64 of SHA-256 over the *transmitted*
  bytes + `:`. The empty GET body is hashed too. For gzip, hash compressed
  bytes before decompression; never hash reserialized JSON. Verify this digest
  before parsing or trusting application content. A transport must hand the
  verifier those exact bytes; an auto-decompressing body API cannot be used.
- Signature parameters in order: `created` and `expires` integer Unix seconds,
  `nonce` a canonical unpadded base64url encoding of 16 bytes, `keyid` the
  registered key identifier, `alg="ed25519"`, and
  `tag="editarr-community-request-v1"`. Signature-Input is serialized as
  `editarr=("@method" ...);created=...;expires=...;nonce="...";keyid="...";alg="ed25519";tag="editarr-community-request-v1"`.
  Signature is `editarr=:` + padded base64 of 64 raw Ed25519 signature bytes
  + `:`. The signed base is the ordered LF-joined lines `"component": value`,
  followed by `"@signature-params": ` and the inner-list-plus-parameters
  portion of Signature-Input, with no final LF. UTF-8/ASCII encode the base
  and sign it with ordinary Ed25519 (no prehash or NODE-ED25519).
- One canonical spelling per Structured Field is accepted. Unknown, repeated,
  combined, reordered, or malformed dictionary members/parameters, unknown
  covered components, and noncanonical base64 are rejected. Ordinary covered
  header fields disallow whitespace and control characters. The verifier
  requires trusted policy-supplied authority, key ID, raw registered 32-byte
  public key, Unix-second `now`, maximum validity and clock skew; `expires`
  must exceed `created`, their difference must not exceed the supplied maximum,
  and `created - skew <= now <= expires + skew`. The key ID in the signed
  request is not itself proof of key status or enrollment.

Python API: `scanner.security.community_signatures.sign_request` returns
headers plus diagnostic `signature-base` bytes; callers supply exact `bytes`,
an `Ed25519PrivateKey`, canonical URI, fresh nonce, times, and a durable
operation ID for mutations. Only the headers are transmitted. Worker API:
`verifyRequestSignature({ method, url, headers, body }, rawPublicKey, policy)`
returns key ID, nonce, operation ID, time bounds, and digest or throws
`RequestSignatureError`. The caller must bind `policy.expectedAuthority` and
`policy.expectedKeyId` to trusted routing/key lookup, and check current key
status before protected work. Do not derive trust from request-supplied fields.

For retry of one mutation, keep the operation ID and payload digest but create
a fresh nonce, timestamps and signature. This pure verifier does **not** reserve or
persist nonces, enforce operation idempotency, check current key state, rate
limit, or enforce eligibility; CSS-25 owns those atomic server-side gates.
CSS-29 owns the transport that retains the exact transmitted bytes. No network
route, D1 migration or deployment is enabled by this profile.

Primary references reviewed: RFC 9421 sections 2.2, 2.3 and 3.1 (derived
components and signature base); RFC 9530 sections 2 and 4 (Content-Digest and
content encoding); Cloudflare Workers Web Crypto Ed25519 documentation. This
strict v1 profile deliberately rejects alternative RFC-valid whitespace,
labels and coverage for deterministic interoperability; any expansion needs
a new profile tag and vectors. Clock/lifetime policy values and nonce storage
duration remain integration policy rather than protocol constants.