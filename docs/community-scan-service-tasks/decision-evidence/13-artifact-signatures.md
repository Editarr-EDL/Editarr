# CSS-13 durable artifact signature v1

The detached signature is independent of the expiring CSS-12 HTTP request signature.
The contributor signs validated `community-manifest-1` bytes and the manifest's
digest of the original, gzip-compressed `community-artifact-1` bytes. The
synthetic vectors in `community-service/test/fixtures/artifact-signatures-v1.json`
pin artifact, manifest and signature-base SHA-256, raw Ed25519 public keys and
signatures. The original gzip bytes come from `artifact-v1-gzip.json`; the
derived vector includes the exact Python-compressed gzip bytes. Do not replace
these stored bytes with reserialized JSON or newly compressed equivalents.

## Byte contract

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

Python `scanner.security.community_artifact_signatures.sign_artifact` takes a
local private Ed25519 key, original gzip and manifest `bytes`, and caller-
approved `ResourceBounds`; it validates both via the CSS-11 codec and returns
`SignedArtifact(artifact_bytes, manifest_bytes, envelope_bytes)` without
copying or rewriting the original byte buffers. Worker
`verifyArtifactSignature(artifactBytes, manifestBytes, envelopeBytes, key,
submissionId, bounds)` validates bytes via the same codec profile, verifies
the detached signature, and returns the parsed values **plus the original
buffers**. `key` must be a trusted historical registration containing the
expected contributor ID, key ID, raw 32-byte public key, and status. The
expected submission ID comes from the trusted operation/catalog, not the
manifest alone. Only `active` and `retired` keys can yield trusted historical
attribution. `compromised`/`revoked` records are rejected; a retired key
verifies old artifacts but never gains current API authority from this helper.
CSS-23/28/39 must preserve the historical public key and its contributor
binding across rotation; current request authorization remains CSS-25's job.

For derivatives, the **new author** signs a new artifact and manifest with
a new submission ID and their own key; the original source reference and
parent digest remain in the signed artifact. The source signature and bytes
must be retained and verified separately where provenance is needed. A
signature proves possession of a registered key and integrity of the bytes,
not the accuracy of a scan, the honesty of model claims, a real-world identity,
or the correctness of cited parent data. No private key goes to the service.

Reproduce independent vectors with `.venv/bin/python -m pytest -q
tests/test_community_artifact_signatures.py` and `pnpm --dir community-service
exec vitest run test/artifact-signatures.test.ts`. The synthetic test private
seeds are byte ranges `00..1f` and `20..3f`; never enroll them. There is no
database migration, publication route, key enrollment or deployment in CSS-13.