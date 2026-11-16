# Community artifact codec v1

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Storage scope superseded October 3, 2026: [D1-only storage](../d1-storage-scope.md).
> R2 references below record historical implementation/evidence, not current requirements.


CSS-11 freezes a **pure interchange format**, not an upload allowance or a
signature profile. The JSON schemas are enforced by
`scanner.formats.community.codec` and `community-service/src/artifact/codec.ts`.
Both reject extra fields. The Python classification registry is mirrored in
`community-service/src/artifact/taxonomy.json`; the Python test checks all pairs.

| Identifier | Transport | Meaning |
| --- | --- | --- |
| `community-artifact-1` | UTF-8 JSON, stored as gzip JSON in private R2 | Immutable author claims and timeline evidence |
| `community-summary-1` | Uncompressed JSON text in D1, with separately indexed lookup columns | Recomputed public counts, work IDs, release, version and coverage |
| `community-manifest-1` | Exact UTF-8 JSON bytes retained with the artifact | Artifact SHA-256, author/key/submission and recomputed summary |
| `mcf-1.1.0` | Summary taxonomy registry label | Current canonical category/subcategory pairs and `unclassified` |

The envelope contains submission ID, kind (`scan`, `manual`, `derived`), UTC
creation time, contributor/key, nullable sharing terms and optional public
source-artifact ID/digest references. It deliberately has no local scan ID,
private path, service vote/rank/moderation or transcript field. Media declares
positive source/derived-target duration in seconds, typed work IDs and optional
release/fingerprint/audio descriptions. Provenance records nullable release and
scanner behavior versions and attempt-scoped effective model/settings; unknown
legacy metadata stays null. Coverage declares channel, capability, status and
intervals independently of finding counts. Findings carry stable event ID,
timeline bounds, evidence source, channel, paired MCF classification or two
nulls, severity, nullable model confidence, optional action and bounded public
note. `both` counts once toward total and once per audio/video channel.

Derived artifacts require a target timeline and explicit public parent
ID/digest(s) plus optional retiming/edit description. Non-derived artifacts
cannot claim derived lineage, and manual artifacts cannot claim scanner stages.
An empty valid finding list has total zero regardless of whether coverage is
complete, partial, failed or unknown. No status is inferred from event count.

`parse_artifact(raw, bounds=..., compressed=True)` and
`parseArtifact(raw, bounds, true)` preserve **the exact incoming gzip bytes**.
The manifest validator checks `SHA-256(stored gzip bytes)`, immutable identity,
and structural equality of the supplied summary with recomputed counts. It
preserves exact incoming manifest bytes; CSS-13 will define their durable
signature binding, detached signature and manifest digest. Do not decompress,
re-encode or re-gzip signed bytes before verification. D1 summary JSON text is
serialized from the recomputed summary, not from a client-asserted count.

Each caller **must supply** positive integer byte/event/text/depth bounds;
neither implementation has a production default. G-LIMITS, hosted CPU and
accepted upload volume remain unresolved. The `4096/16384/10/256/12` test
bounds are synthetic. The decoders check input bytes before parsing, bound
gzip output while streaming, reject duplicate JSON fields, extra fields,
nonfinite numbers, unsupported actions, invalid taxonomy, overlong UTF-8
strings, excessive nesting and out-of-timeline intervals. They do not fetch
URLs. Public notes are untrusted and need explicit preview and safe rendering
in CSS-37/46; null sharing terms are a codec value, **not** authorization to
publish before G-LICENSE.

Normative synthetic vectors: `community-service/test/fixtures/artifact-v1.json`
compacts to 1023 UTF-8 bytes with SHA-256
`1b23acc6ba8722de0e3a518437ed3f5ea770045aa40f10b80d44e84e07ad7c21`.
`artifact-v1-gzip.json` contains the exact 492-byte gzip member (mtime zero),
SHA-256 `ea7575b13b112c3d01a2f6ad2e2fcc36842bc3b78407cd24b14393a68318d520`.
Python and workerd decode the same stored member; no cross-runtime gzip encoder
determinism is assumed. Altered body, author, count, schema, path-like extra
fields and transcripts fail validation. No production endpoint or migration
uses this codec yet.