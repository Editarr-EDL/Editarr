# Community scan service contracts

This document describes the hosted protocol and storage model. Use the
[local API contract](api-contract.md#local-community-api) for browser-facing
routes, the [service README](../community-service/README.md) for operating the
service, and the [Docker guide](community-service-docker.md) for deployment and
recovery.

## 1. Boundaries and identity lifecycle

- A local administrator controls connection, identity changes and sharing in
  Editarr. Local authentication credentials stay on the local instance.
- A contributor is an opaque service identity associated privately with a
  verified Discord user. Display name, contributions, votes and moderation
  state belong to the contributor.
- Each installation registers an independent Ed25519 key. A restored copy
  retains the installation ID and key of its backup. Rotation and recovery
  change the key for one installation. The contributor ID stays the same.
- Operator authorization uses a separate Cloudflare Access identity and
  subject allowlist. The service validates its JWT and enforces origin and
  CSRF requirements on admin mutations.

The local backend stores private identity material in its private data directory.
The key identifier is the SHA-256 of the canonical raw public key, with the
`sha256:` prefix. The `community-identity-1` manifest identifies the private
files included in portable backups. Keep historical public keys for artifact
verification. Retired keys cannot authorize current requests. A revoked or
compromised key cannot establish trusted artifact attribution.

Enrollment binds a hash of a backend-held confirmation secret before OAuth.
After Discord membership verification and local administrator approval,
`POST /v1/enrollments/complete` submits the original secret in a signed request.
The public enrollment reference is safe to display. The private key, Discord
token and local administrator credentials stay out of the completion request.
The service consumes the grant and registers the identity atomically. A retry
with the same installation and active key can reconcile within the original
ten-minute grant. A different key for that installation returns
`recovery_required`. See [Discord enrollment](community-scan-service-discord-enrollment.md)
and the [enrollment profile fixture](../tests/fixtures/community/enrollment-profile-v1.json).

`GET /v1/identity` returns the signed caller's profile. `PATCH /v1/identity`
accepts `{"displayName":"..."}`. Custom names are trimmed and NFC-normalized,
contain 3–40 Unicode code points, and exclude control and format characters.
Current names are unique by NFC, lowercase, then NFC. A profile revision
advances when the display name changes. Signed identity responses return the
current name and revision, including on successful retries.

## 2. Request authentication and replay handling

The `editarr-community-request-v1` Ed25519 profile follows
[RFC 9421](https://www.rfc-editor.org/rfc/rfc9421.html) and
[RFC 9530](https://www.rfc-editor.org/rfc/rfc9530.html). The
[cross-language vectors](../community-service/test/fixtures/request-signatures-v1.json)
include exact URLs, headers, body bytes and signature bases.

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

The service verifies exact body bytes, authoritative key status, contributor
eligibility and current membership before protected work. Positive and negative
membership checks expire after 15 minutes. An expired check must refresh
successfully before the request can proceed.

Mutations bind the nonce to the key and use an idempotency key scoped to the
contributor and operation. Persist the nonce atomically with the operation.
On retry, sign the same body and operation ID with a fresh nonce and validity
window. Reusing an operation ID with different bytes conflicts. A retry of a
completed operation returns its stored result. Read-only requests can repeat
during their short signature lifetime. `401 clock_skew` follows successful
digest and signature verification for an out-of-window request.

## 3. Work, release, and fingerprint contracts

A work or episode is identified by typed provider IDs and, for an episode or
multi-episode release, explicit numbering and episode spans. The supported
providers are TMDB and TVDB. Provider-asserted work IDs, ordered release
associations and possible aliases retain their provenance. Local database IDs,
filenames and paths are private. File cuts and track/stream evidence are stored
separately from the work.

`editarr-community-file-sha256-sampled-1` hashes nine evenly spaced 1 MiB
samples, merging overlapping or adjacent ranges and binding the file size and
ordered offsets and lengths with big-endian framing. It identifies the sampled
file bytes. `sha256-full-1` is independent whole-file evidence.

The video fingerprint samples three 250 ms windows of H.264-AVCC. The audio
algorithm `editarr-community-audio-pcm-s16le-16000-mono-sha256-1` hashes its ASCII
name, a zero byte, and all normalized 16 kHz mono signed 16-bit little-endian PCM.
Sharing uses the recorded analyzed track and reuses its scan WAV when available.
Lookup normalizes the recipient's selected track with the same settings. The
playback start is relative to the container playback origin. Audio duration is
the normalized sample count divided by 16,000. Lossy re-encoding can change
the fingerprint. PCM capture is bounded to four hours of samples and a
three-minute decode.
The audio contribution describes the track actually analyzed. Unknown
historical or imported track information stays unknown. For a derived review
with multiple audio tracks, the outgoing preview includes the chosen track's
findings and identifies the excluded audio findings. The original review stays
intact.

`community-compatibility-stream-1` provides separate audio and video bands.
Strong stream matches require known matching playback-relative start and
duration, compared as exact rational seconds. Strong audio matches require
actual analyzed provenance for the candidate and selected or analyzed
provenance for the target. An exact PCM match can establish track identity
when language or track role is unknown. Known conflicts still require review.
The fixed inclusive 2.0-second file-duration tolerance is a compatibility
check. It does not authorize retiming. Unknown or conflicting stream evidence
can require review or make that channel incompatible.

## 4. Artifact and summary formats

The detached signature is independent of the expiring request signature.
`community-manifest-1` binds the original gzip bytes and the artifact version.
Both `community-artifact-1` and `community-artifact-2` are supported. Version 2
adds public name, source revision, portable matching evidence, bounded source
snapshots and explicit source timeline associations. Its summary format is
`community-summary-2`. The
[fixture vectors](../community-service/test/fixtures/artifact-signatures-v1.json)
pin the signature base and original body and manifest bytes.

The Ed25519 message is the following concatenation, in order:

1. ASCII `editarr-community-artifact-signature-v1` followed by a zero byte.
2. Six length-prefixed UTF-8 strings, in this order: manifest schema version,
   artifact schema version, artifact format, submission ID, contributor ID,
   signing key ID. Each length is an unsigned 32-bit big-endian byte count
   followed immediately by those UTF-8 bytes. The strings are read from the
   validated manifest, which the codec has already matched to the artifact.
3. The 32 raw SHA-256 bytes of the **original manifest bytes**.
4. The 32 raw bytes decoded from the manifest's lowercase hex artifact digest;
   the codec verifies that digest against the **original compressed bytes**.

No newline, JSON canonicalization or terminator follows the final digest.
Sign these bytes with ordinary Ed25519, without prehashing the message or
using `NODE-ED25519`. The detached envelope is ASCII JSON, with the exact key
order and compact form:

```json
{"schema_version":"community-artifact-signature-1","signature":"<padded base64 of 64 Ed25519 bytes>"}
```

The envelope is stored alongside, not inside, the original artifact and
manifest. The receiver rejects unknown versions, duplicate/reordered/extra
envelope fields, noncanonical signature encoding and mismatched body/manifest
claims. It must preserve and later verify all three original buffers, even
when an equivalent parsed JSON object could be serialized differently.

The builder projects allowlisted public fields from the saved source and
generates the preview from the exact outgoing artifact and manifest. Approval
binds the exact bytes and source generation. Preserve author notes and
attribution under the community reuse terms. A derived artifact is signed by its
new author and retains the source attribution and parent digest. Imported
artifacts keep the original evidence independently from local review edits.

Validate sizes and expansion, finite times and scores, duration, event bounds,
taxonomy pairs and bounded notes. The service computes summaries from the
artifact and stores mutable statistics and moderation state separately.
Do not execute artifact content or treat its claims as verified detection
accuracy. All times refer to the declared source playback timeline. An approved
retiming belongs to the derived local review and does not rewrite signed source
bytes.

## 5. Storage model

| Store / records | Purpose and essential uniqueness/indexes |
| --- | --- |
| Service SQLite `contributors`, `installations`, `contributor_keys` | Stable contributor/name/trust; multiple installations with independent current key/history; globally unique public key ID |
| Service SQLite `external_identities`, enrollment/membership state | Unique provider/Discord user ID mapped privately to contributor; bounded OAuth state and eligibility freshness; signing keys registered separately |
| Service SQLite `works`, `work_identifiers` | Typed media identity; indexed unique provider/type/value plus episode identity rules |
| Service SQLite `releases`, `release_work_associations`, `release_episodes`, `release_fingerprints` | Immutable file/cut evidence, release-level asserted IDs and ordered episode spans; work/episode/channel/algorithm/digest indexes; no global filename uniqueness |
| Service SQLite `contributions`, `contribution_revisions` | Immutable family/author/key/release/type/schema, exact digests/lengths/object key, owned durable operation, bounded original summary and lineage |
| Service SQLite `contribution_publications`, `contribution_statistics` | Separate mutable lifecycle/etag and counters; published-only catalog visibility |
| Service SQLite `contribution_parents` | Derivation graph with bounded parents; prevent cycles and preserve lineage |
| Service SQLite `import_receipts`, `revision_votes` | Unique contributor/contribution revision; enforce one counted import and current vote |
| Service SQLite `reports`, `moderation_actions` | Structured reports and reasoned operator actions, retained indefinitely under community-retain-first-1 |
| Service SQLite `request_nonces`, `operations` | Replay and idempotency state, expiry indexes, durable upload/import operation recovery |
| Service SQLite usage observations | Passive daily observations under historical request/write labels; logical writes and unknown reads are not provider billing or admission gates |
| Service SQLite `artifact_storage_usage` | Atomic global/contributor byte observations, without capacity admission; these exclude database/index/WAL overhead |
| Service SQLite `artifact_payloads` | Immutable exact gzip, manifest and signature BLOBs, keyed by the revision’s service-generated storage identifier |
| Local SQLite provenance | Remote service/contribution/revision/digest, original author/type, local scan linkage, receipt/vote state, pending transfers |
| Local private files | Signing keys and versioned identity manifest; excluded from logs and normal settings API responses |

Keep event arrays inside the compressed SQLite artifact BLOB; do not expand each event
into a catalog row. Keep searchable summaries as bounded JSON text and indexed
columns. Store each original signed manifest and signature exactly once alongside
its payload, rather than duplicating them in catalog summaries. Size BLOBs, indexes,
receipts/votes and operational tables together when measuring database headroom.

Publication commits the artifact BLOBs, nonce, revision, lineage, publication
state and idempotent result in one SQLite transaction. Import facts count once
per contributor and revision, including across installations. Votes require an
eligible counted import. Quarantine and operator suspension change visibility.
The retained records and historical keys support audit and verification.

See [authorized downloads and receipts](../community-service/README.md#authorized-downloads),
[revision feedback](api-contract.md#hosted-votes) and the
[SQLite migration guide](../community-service/migrations/README.md) for their
specific limits and procedures.
