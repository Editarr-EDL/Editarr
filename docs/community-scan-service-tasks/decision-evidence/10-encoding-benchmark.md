# CSS-10 encoding experiment (local, not a format decision)

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

> Storage scope superseded October 3, 2026: [D1-only storage](../d1-storage-scope.md).
> R2 references below record historical implementation/evidence, not current requirements.


September 26, 2026. Synthetic data only. The experiment is implemented in
`scripts/benchmark_community_encoding.py`, with parity/evolution tests in
`tests/test_community_encoding_benchmark.py`. It uses a typed, dynamically built
proto3 descriptor solely for measurement; **neither encoding is a production
schema**. CSS-22 supplies a local-only workerd/D1/R2 test harness. Python and
TypeScript Protobuf wire hashes now match for every fixture/surface pair. Hosted
CPU and real D1/R2 billing remain unmeasured.

## Reproduction

From the repository root, with locked project dependencies installed:

```sh
.venv/bin/pytest -q tests/test_community_encoding_benchmark.py
.venv/bin/python scripts/benchmark_community_encoding.py --iterations 20
.venv/bin/python scripts/benchmark_community_encoding.py --iterations 20
.venv/bin/python scripts/benchmark_community_encoding.py --export-vectors community-service/test/fixtures/encoding-vectors.json
pnpm --dir community-service install --frozen-lockfile
pnpm --dir community-service run typecheck
pnpm --dir community-service exec vitest run test/encoding-experiment.test.ts --reporter=verbose --silent=false
pnpm --dir community-service exec vitest run test/encoding-experiment.test.ts --reporter=verbose --silent=false
```

Both invocations print JSON results. All 12 fixture/surface byte-count rows and
SQLite page counts matched exactly across both runs; CPU samples vary. Host:
macOS 27.0 arm64, Python 3.11.16, `protobuf` 7.35.1 (locked transitively in
`uv.lock`), SQLite 3.53.1; Node 26.7.0, pnpm 11.22.0, Wrangler 4.141.0,
workerd test plugin 1.2.8, `@bufbuild/protobuf` 2.11.0. No `protoc` is
required for the runtime tests: the Python benchmark exports the experimental
file descriptor and SHA-256 vectors. Each Python operation is
timed over 20 runs using `perf_counter_ns`; report median and nearest-rank
sample p95. `tracemalloc` separately measures one operation's Python allocation
peak; it does not measure total process RSS or Worker memory. Compression is
deterministic gzip with `mtime=0`. JSON uses sorted compact UTF-8; typed
Protobuf uses deterministic serialization, explicit nullable identity fields,
and JSON-to-Protobuf conversion on encode and back on decode.

## Fixtures and bytes

The generated fixtures are `small` (12 events), `large` (2,500 events), `zero`
(zero findings with completed visual coverage), `partial` (25 events with two
attempts, partial Qwen and cached-scene unknown model), `manual` (5 authored
events and no executed stages) and `derived` (20 events with parent digest).
All media IDs and notes are invented; no personal database, media or scan cache
is read. The large fixture repeats 17 short notes, making gzip unusually
effective; real public notes and multi-model scans may compress less well.
Unknown build/model digest/revision, quantization and sampling are null, not
invented values. Synthetic settings are encoded as compact JSON strings in
both codecs. No real authorization, signing manifest, fingerprint or full
taxonomy is included, so these sizes cannot set production limits.

| Fixture | R2 JSON raw/gzip | R2 Proto raw/gzip | D1 JSON raw/gzip | D1 Proto raw/gzip | Indexed SQLite 100 rows JSON/Proto |
| --- | ---: | ---: | ---: | ---: | ---: |
| small | 2,899 / 570 | 1,397 / 387 | 603 / 326 | 194 / 179 | 86,016 / 40,960 |
| large | 493,449 / 28,747 | 253,712 / 22,078 | 605 / 327 | 195 / 182 | 86,016 / 40,960 |
| zero | 598 / 323 | 192 / 176 | 602 / 325 | 192 / 176 | 86,016 / 40,960 |
| partial | 6,479 / 851 | 3,029 / 658 | 1,661 / 447 | 508 / 298 | 221,184 / 77,824 |
| manual | 1,133 / 301 | 544 / 195 | 212 / 162 | 80 / 92 | 45,056 / 28,672 |
| derived | 4,164 / 508 | 2,203 / 409 | 276 / 166 | 147 / 96 | 53,248 / 36,864 |

Bytes are per body/summary, except SQLite's **total allocated pages for 100
rows**, including an identical `(work_id, kind)` index and submission primary
key. Searchable identity columns remain separate in both cases. SQLite in
memory is a directional D1 storage proxy, not a D1 billing or query-performance
measurement; rows repeat the same synthetic summary. Gzipped D1 values are
comparisons only; the SQLite measurement stores plain JSON text or Proto BLOB.
Large R2 Proto saves 48.6% raw bytes and 23.2% gzip bytes against JSON; its
D1 summary saves 67.8% raw bytes and 44.3% gzip bytes. For small summaries,
fixed row/index overhead may outweigh payload savings.

## Local CPU and memory

Large R2 body p95 milliseconds (runs 1 / 2), Python traced peak bytes (run 1):

| Operation | JSON p95 / peak | Proto p95 / peak |
| --- | ---: | ---: |
| Encode, including mapping | 3.010 / 3,616,522; 2.265 | 16.577 / 253,921; 16.428 |
| Decode to mapping | 1.230 / 2,219,850; 1.246 | 9.617 / 2,879,425; 8.725 |
| Gzip decompress + SHA-256 | 0.284 / 1,971,809; 0.214 | 0.135 / 676,794; 0.182 |

These are **Python/macOS operations**, not V8, Worker CPU or an end-to-end
request. Encoding conversion accounts for much of Proto's measured cost; a
generated TS decoder could differ significantly. D1 partial-summary decode
p95 was JSON 0.005/0.004 ms versus Proto 0.022/0.023 ms across runs.

Local workerd (20 operations per codec per run; p95 wall time rounded to the
runtime timer's millisecond resolution) reported JSON encode/decode 1/1 ms
and Proto encode/decode 15/8 ms in both repeated runs. The paired local
validation/storage probe reported **Proto 17/16 ms and JSON 7/6 ms** wall
time across two runs; after storing the signed compressed bytes instead of
expanded bytes, a confirmation run gave Proto 18 ms and JSON 5 ms. This probe
stream-bounds gzip expansion, hashes and verifies Ed25519 over exact compressed
bytes, decodes a synthetic schema, checks version and count, writes private R2
and an indexed D1 summary, then reads both back. It is not a production
validation/authentication pipeline or a hosted CPU measurement. Local gzip
for the large body measured Proto 22,770 bytes and JSON 28,953 bytes;
TypeScript JSON was 483,445 expanded bytes versus Python's 493,449 because
JavaScript emits integral floating timestamps as integers, another reason not
to sign reserialized JSON. D1 summary values were Proto 195 bytes and native
JSON 601 bytes (Python canonical JSON: 605). TS peak memory is unmeasured;
Python `tracemalloc` does not represent workerd memory.

The plan lists a 10 ms free-Worker CPU target. A proposed *test-only* bounded
upload of at most 64 KiB compressed, 512 KiB expanded and 2,500 events includes
the repetitive large fixture. Local workerd accepts both candidates, rejects
oversized compressed and expanded payloads, and records above-target *wall*
time for the Proto path. Wall time is **not CPU billing**; the local probe lacks
full schema checks, account policy, request parsing and manifest verification.
With the same 2,500 events but deterministic high-entropy 120-character notes,
gzip JSON exceeds the 64 KiB test bound and is rejected before decompression.
Event count alone cannot guarantee admission; CSS-11 needs bounded text and
expansion rules in the selected contract.
**Neither candidate is proven to fit 10 ms Worker CPU.** The user must decide
whether to authorize an isolated free-plan measurement before setting a
production bound, or to narrow the accepted payload/scope until demonstrated
to fit; chunked bounded uploads would be another explicit scope choice. No
hosted deployment or provider traffic was performed. These candidate limits
are not approved policy.

## Interoperability and signing

Python's Protobuf package supports dynamic typed messages, presence and
unknown-field retention; nine focused parity/evolution tests pass. The
experimental null-to-absent Protobuf mapping is explicit. Proto tag reuse or
wire-type changes would break compatibility; assign permanent field numbers,
reserve retired tags and version the envelope if chosen. JSON is immediately
inspectable, but changing canonical key order or number formatting changes
bytes. Protobuf deterministic serialization is not a cross-language canonical
signature contract either: unknown fields and serializer versions can alter
output. In either case, store and hash **the original received bytes** plus
the exact signed manifest; never verify signatures over a parse-and-reserialize
cycle. Keep indexed D1 lookup fields out of opaque summary blobs.

TypeScript JSON has native parsing. Reflective `protobufjs` failed in workerd
because it generates code from strings; the test-only pinned Buf runtime
instead loads the Python descriptor without `eval`. All 12 R2/D1 fixture
cases match Python Protobuf **byte length and SHA-256**, not just semantics;
Python checks that exported vectors remain current. The human-readable `.proto`
is experimental documentation of the same field numbers, not an adopted
production contract. Local D1/R2 bindings were used, not hosted resources.

## Recommendation for G-ENCODING

**Decision (September 26, 2026):** After reviewing the results, the user
selected JSON. Use uncompressed JSON text for D1 serialized summaries alongside
separately indexed searchable columns, and gzip JSON for immutable private R2
artifact bodies. The Protobuf fixtures and runtime remain experimental; no
production Protobuf codec was approved. The local workerd Protobuf path was
slower despite its smaller compressed body.

**Still open, separately from encoding:** Before approving a 64 KiB compressed
/ 512 KiB expanded / 2,500 event upload bound, authorize an isolated free-plan
CPU measurement, choose a smaller bound, or scope a chunked upload protocol?
The benchmark does not answer that resource/scope question or set a production
limit on the user's behalf.