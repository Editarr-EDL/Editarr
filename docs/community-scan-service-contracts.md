# Community scan service contracts

This document is the authoritative hosted protocol and storage contract for
community **master filter sets**. Each work version has one master set, merged
from signed contributor submissions. The decisions behind it are in
[ADR 0002](adr/0002-community-master-filter-sets.md). Browser-facing routes are
in the [local community API](community-local-api.md). For operations, see the
[service README](../community-service/README.md), and for deployment and
recovery, the [Docker guide](community-service-docker.md).

Sections 1 and 2 are unchanged from the revision-based protocol. Sections 3–9
replace its catalog, artifact, download, vote and ranking contracts.

Conventions:

- "Token" means `[A-Za-z0-9_.-]{1,128}`.
- Hex values are lowercase.
- Base64 is standard alphabet and padded.
- All times are integer milliseconds unless a field name ends in `At`, in
  which case they are Unix seconds.
- `framed(domain, fields[, issuedAt])` is the ASCII domain, a zero byte, then
  each UTF-8 field preceded by its byte length as a u32 big-endian, then the
  optional `issuedAt` as a u64 big-endian.
  - It is the same framing as `community-service/src/contributions/receipts.ts`.

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


Every endpoint in §7 uses this profile. Read-only `POST` routes
(`/v1/match`, `/v1/sync/revisions`) still send an `Idempotency-Key`, but the
service does not persist an operation for them.

## 3. Works, versions, aliases and match keys

### 3.1 Model

| Entity | Identity | Notes |
| --- | --- | --- |
| Work | A TMDB or TVDB ID. For TV it is the series ID plus `season` and `episode`, with an optional `lastEpisode` for a multi-episode file. | One work can carry both a TMDB and a TVDB identifier. Titles and years are stored as match keys (§3.3). |
| Version | A service-generated `versionId` under one work | `status` is `confirmed`, `candidate`, or `merged_into`. A version has an optional `aliasId` (canonical alias) or a free-text `labelText`, or neither. It also has a runtime, a trust score, and a monotonic master `revision`. |
| Alias | `aliasId` (ASCII `[a-z0-9_]{1,64}`) plus spellings | Approved spellings resolve to the canonical alias. |
| Match key | `(type, value[, algorithm, sizeBytes])` | Keys are attached to versions with observation counts. One key value can belong to several versions. |

Service IDs (`workId`, `versionId`, `filterId`, `submissionId`, `receiptId`)
are opaque ASCII tokens matching `[A-Za-z0-9_.-]{1,128}`. Clients must not
parse them. For a filter ID derivation, see §6.6.

### 3.2 Match key types

| `type` | Value | Extra fields | Equality | Tier |
| --- | --- | --- | --- | --- |
| `sha256_full` | 64 lowercase hex characters: SHA-256 of all file bytes (`sha256-full-1`) | `sizeBytes` | value and `sizeBytes` | 1 |
| `sha256_sampled` | 64 lowercase hex characters: `editarr-community-file-sha256-sampled-1` | `sizeBytes` | value and `sizeBytes` | 2 |
| `md5_quick` | 32 lowercase hex characters | `sizeBytes` | value and `sizeBytes` | 2 |
| `audio_stream` | 64 lowercase hex characters | `algorithm` is `editarr-community-audio-pcm-s16le-16000-mono-sha256-1` or `editarr-community-audio-sha256-sampled-1` | algorithm and value | 3 |
| `video_stream` | 64 lowercase hex characters | `algorithm` is `editarr-community-video-sha256-sampled-1` | algorithm and value | 3 |
| `tmdb` | Decimal ID string (`[0-9]{1,12}`) | `kind` is `movie` or `episode`. An episode also has `season`, `episode`, and optional `lastEpisode`; the ID is the series ID. | provider, kind, ID, and numbering | 4 |
| `tvdb` | Same as `tmdb` | Same as `tmdb` | Same as `tmdb` | 4 |
| `title_year` | `{ "title": string, "year": int\|null }` | — | `normalize(title)` and `year`. A null `year` never matches. | 5 |

**Hash definitions:**

- `editarr-community-file-sha256-sampled-1` hashes nine evenly spaced 1 MiB
  samples. Overlapping or adjacent ranges are merged. The hash binds the file
  size and the ordered offsets and lengths with big-endian framing.
- `md5_quick` is the MD5 of the whole file when the file is 50 MiB
  (52,428,800 bytes) or smaller. For a larger file, it is the MD5 of the first
  10 MiB followed by the last 10 MiB. This is
  `scanner/runtime/media_fingerprints.py`, and it is not a security digest.
- The audio PCM algorithm hashes its ASCII name, a zero byte, and all of the
  normalized 16 kHz mono signed 16-bit little-endian PCM. Capture is bounded to
  four hours of samples and a three-minute decode.
- The video fingerprint samples three 250 ms windows of H.264-AVCC.
- In v3, stream keys carry no timing fields.

Lossy re-encoding can change a stream fingerprint.

### 3.3 Normalization (titles, alias spellings, Other text)

`normalize(s)` is used for titles, alias spellings and Other text. Implement it
exactly as follows:

1. Apply Unicode NFKD.
2. Remove every code point in general category `Mn`.
3. Apply Unicode default full lowercase mapping. Use Python `str.lower()`, or
   JavaScript `String.prototype.toLowerCase()`. Don't use locale-sensitive
   variants or `casefold`.
4. Delete U+0027 `'`, U+2019 `’`, U+02BC `ʼ` and U+0060 `` ` ``.
5. Replace every code point that is not in a general category `L*` or `N*`
   with U+0020.
6. Collapse runs of U+0020 into one space, then trim. Apply NFC.

The result must contain 1–256 code points for a title. Alias spellings and
Other text may normalize to an empty string, which means "no version" (§3.4).

| Input | `normalize` |
| --- | --- |
| `Director’s Cut` | `directors cut` |
| `  EXTENDED   Edition!` | `extended edition` |
| `Amélie` | `amelie` |
| `Spider-Man: No Way Home` | `spider man no way home` |
| `Ⅲ` | `iii` |

The key stored for `title_year` is `normalize(title) + "\u0000" + year`.

### 3.4 Seeded aliases

| `aliasId` | Display label | Seeded spellings (before normalization) |
| --- | --- | --- |
| `theatrical` | Theatrical | `""` (no version), `Theatrical`, `Theatrical Cut`, `Theatrical Version`, `Theatrical Edition`, `Theatrical Release`, `Original Theatrical`, `Original Theatrical Cut` |
| `directors_cut` | Director's Cut | `Director's Cut`, `Directors Cut`, `Director's Edition`, `Director's Version` |
| `extended` | Extended Edition | `Extended Edition`, `Extended`, `Extended Cut`, `Extended Version` |
| `unrated` | Unrated | `Unrated`, `Unrated Cut`, `Unrated Edition`, `Unrated Version` |

- A normalized spelling is unique across all approved aliases.
- `theatrical` and "no version" are the same everywhere: in queries,
  submissions, label equality and the picker.
- Contributor suggestions (from Other text) are stored as `pending` and
  resolve only after moderator approval (§7.8). An approved suggestion either
  becomes a new spelling of an existing alias or creates a new `aliasId`.
- Clients get the current list from `GET /v1/aliases`. The list is ordered
  with `theatrical` first, then by display label in ASCII order.

### 3.5 Version acceptance (D13)

The service runs these steps in order, inside the submission transaction.

1. **Resolve the work.**
   - Find the work by any supplied provider identifier.
   - If none matches, create the work.
   - If the TMDB and TVDB identifiers resolve to two different works, use the
     TMDB work. Record the TVDB identifier as a `work_identifier_conflicts`
     row for moderation, and do not attach it.
   - Attach new non-conflicting identifiers and title/year keys.
2. **Resolve the label.**
   - **Alias choice:** use that `aliasId`. It must be approved, or the
     submission fails with `422 unknown_alias`.
   - **Other choice:** set `labelText = normalize(text)` (null when blank).
     If the text equals an approved spelling, also set `aliasId`, but the
     choice is still Other. Non-blank text that is not already an approved
     spelling records a pending alias suggestion; text that resolves to an
     approved spelling records none.
3. **Labels are equal when both of these hold:**
   - their `aliasId` values match, treating null and `theatrical` as the same;
   - when both have no `aliasId`, their `labelText` values match, and a
     null `labelText` matches only another null.

   A null `aliasId` means Theatrical only when `labelText` is also null. A
   label with a null `aliasId` and a non-null `labelText` (unresolved Other
   text) is **not** Theatrical: it never equals a label that has an
   `aliasId`, and equals another label only when both have a null `aliasId`
   and the same `labelText`.
4. **Find tier 1–3 matches.** Compute the tier 1–3 matches (§4) between the
   submission keys and each non-merged version of the work with an equal
   label.
   - If any version matches, attach to the best one (rule 3). The best match
     is the lowest tier, then `confirmed` before `candidate`, then higher
     trust, then the smaller `versionId` in ASCII order.
   - This step also applies to Other choices and to candidates, so
     candidates can gather evidence toward promotion.
5. Otherwise, if the choice is an alias and no non-merged version of the work
   has an equal label, **create a confirmed version** (rule 1). Its
   reference keys are this submission's tier 1–3 keys.
6. Otherwise, **create a candidate** with that label (rules 2 and 4). Other
   never reaches rule 1.

When a submission attaches, its tier 1–3 keys are added to
`version_match_keys` and their `observations` count is incremented.

**Promotion:**

- A candidate becomes `confirmed` as soon as at least
  `candidatePromotionContributors` (default 3) distinct contributors have a
  counted submission (§6.1) attached at tier 1–3.
- The founding contributor counts, because its keys define the candidate.
  A founder that supplied no tier 1–3 keys does not count.
- Submissions moved in by `merge_version` (§7.8) keep counting toward
  runtime and trust but lose their evidence tier, so they never count toward
  promotion: their keys matched the source version, not the target.
- Promotion doesn't change `versionId`.

**Concurrency:** acceptance plans from reads and commits in the submission
transaction behind an optimistic per-work guard. Any concurrent catalog write
to the same work (another acceptance, confirm, merge, relabel or quarantine)
aborts the transaction, and the service plans it again. The store also refuses
to insert a confirmed version when a non-merged version of the work already
has an equal label, so concurrent alias submissions cannot create two
confirmed versions for one label.

A moderator can also confirm a candidate or merge any version into another
(§7.8). Several versions under a work may share a label. They are ranked by
`trust`.

**Version runtime:** the version's `runtimeMs` is the median (§6.4) of
`media.durationMs` across counted submissions.

**Version trust:**

- Positives: distinct contributors with a counted submission or an upvote on
  any of the version's filters.
- Negatives: distinct contributors who aren't positives and have only
  downvotes or disputes on the version's filters.
- `trust = wilson(positives, negatives)` (§6.8).

## 4. Match ladder and Match %

`POST /v1/match` scores every non-merged, unquarantined version that shares
at least one key with the query. For each version, the tier is the best
(lowest-numbered) tier whose condition holds:

| Tier | Condition | `matchPercent` |
| --- | --- | --- |
| 1 | Any query `sha256_full` equals a key of the version | 100 |
| 2 | Any query `sha256_sampled` or `md5_quick` equals a key of the version | 80 |
| 3 | Any query `audio_stream` or `video_stream` equals a key of the version | 65 |
| 4 | A query `tmdb`/`tvdb` key identifies the version's work, the labels are equal (§3.5), and the runtime is `same_cut` or `possible_cut` | 30 |
| 5 | A query `title_year` equals one of the work's title keys, the labels are equal, and the runtime is `same_cut` or `possible_cut` | 15 |
| `runtime_mismatch` | The tier 4 or 5 identity and label hold, but the runtime is `outside` or `unknown` | 5 |
| `other_version` | The work matches through `tmdb`/`tvdb`/`title_year`, but the labels differ | 0 |

**Runtime:**

- `runtimeAgreement` compares the query `runtimeMs` with the version's
  `runtimeMs`.
- A difference of 2,000 ms or less is `same_cut`.
- More than 2,000 ms, up to 30,000 ms, is `possible_cut`.
- More than 30,000 ms is `outside`.
- If the query has no runtime, it is `unknown`.
- Tiers 1–3 report `not_applicable` and are never penalized.
- These thresholds are protocol constants, not operator configuration.

**Other matching rules:**

- A query label is `{aliasId}`, `{text}` (normalized and resolved through
  approved spellings), or null. Null means Theatrical.
- A key mismatch never lowers a tier. For example, a different
  `sha256_full` doesn't cancel an equal `md5_quick`.
- Multiple keys don't add up. Only the best tier counts.
- `matchedKeys` lists every key type that satisfied the reported tier.
  For `runtime_mismatch` and `other_version`, it lists every identity key
  type that matched the work at tier 4 or 5 (`tmdb`, `tvdb` and
  `title_year`), in that order.

**Result order:**

1. `matchPercent` descending.
2. `confirmed` before `candidate`.
3. `runtimeAgreement` in the order `same_cut`, `possible_cut`,
   `not_applicable`, `unknown`, `outside`.
4. `trust` descending.
5. `versionId` in ASCII order.

## 5. Submission artifact v3

### 5.1 Bytes, manifest and signature

A submission consists of three buffers:

- the **artifact**: gzip of UTF-8 JSON (`community-artifact-3`);
- the **manifest**: `community-manifest-1`;
- the **envelope**: `community-artifact-signature-1`.

These artifact and manifest rules apply:

- The gzip must be a single member with no `FEXTRA`, `FNAME`, `FCOMMENT` or
  `FHCRC`. It must expand to valid UTF-8 with no BOM.
- The JSON must not contain duplicate keys. Objects are strict: unknown fields
  are rejected.
- Integers are JSON integers (no fraction or exponent) within
  `Number.MAX_SAFE_INTEGER`.
- The manifest is UTF-8 JSON with snake_case fields, unchanged except that
  `artifact_schema_version` must be `"community-artifact-3"`:

  ```json
  {"schema_version":"community-manifest-1","artifact_format":"gzip-json","artifact_schema_version":"community-artifact-3","artifact_digest":"<64 hex of SHA-256 over the gzip bytes>","submission_id":"<= artifact submissionId>","contributor_id":"<= author.contributorId>","key_id":"<= author.keyId>","summary":{}}
  ```

- `summary` must be `{}`. The service derives everything else.

The Ed25519 message is unchanged from v2:

1. ASCII `editarr-community-artifact-signature-v1`, then one zero byte.
2. Six length-prefixed UTF-8 strings, in this order:
   - manifest `schema_version`
   - `artifact_schema_version`
   - `artifact_format`
   - `submission_id`
   - `contributor_id`
   - `key_id`

   Each string starts with its byte count as a u32 big-endian.
3. The 32 bytes of SHA-256 over the **original manifest bytes**.
4. The 32 bytes decoded from `artifact_digest`, which the receiver verifies
   against the **original gzip bytes**.

The envelope is exactly
`{"schema_version":"community-artifact-signature-1","signature":"<padded base64 of 64 bytes>"}`.

- The receiver keeps all three original buffers and never re-serializes them.
- The **submission digest** is `artifact_digest`.
- The [signature fixture vectors](../community-service/test/fixtures/artifact-signatures-v1.json)
  remain normative for the framing.

### 5.2 Artifact JSON (`community-artifact-3`)

Artifact fields are camelCase, and all times are integer milliseconds on the
submitter's file playback timeline.

```json
{
  "schemaVersion": "community-artifact-3",
  "submissionId": "sub_01J9Z3K5C6Q",
  "createdAt": 1795553648,
  "author": { "contributorId": "c_7f3a", "keyId": "sha256:<64 hex>" },
  "work": {
    "kind": "movie", "tmdb": "603", "tvdb": null,
    "season": null, "episode": null, "lastEpisode": null,
    "title": "The Matrix", "year": 1999
  },
  "version": { "choice": "alias", "aliasId": "theatrical" },
  "media": {
    "durationMs": 8160512,
    "matchKeys": [
      { "type": "sha256_sampled", "value": "<64 hex>", "sizeBytes": 4823749120 },
      { "type": "md5_quick", "value": "<32 hex>", "sizeBytes": 4823749120 },
      { "type": "audio_stream", "algorithm": "editarr-community-audio-pcm-s16le-16000-mono-sha256-1", "value": "<64 hex>" }
    ]
  },
  "alignmentHint": { "versionId": "v_12ab", "revision": 7, "offsetMs": -1200, "scale": 1.0, "anchors": 6 },
  "coverage": [
    { "category": "violence", "intervals": [ { "startMs": 0, "endMs": 8160512 } ] }
  ],
  "entries": [
    {
      "entryId": "e1", "kind": "support", "proposedFilterId": "f_9c1d…",
      "startMs": 600000, "endMs": 605000,
      "category": "violence", "subcategory": "punching", "severity": "medium", "action": "skip",
      "evidence": { "origin": "downloaded", "manualReview": true, "locallyVerified": true, "modelVersion": null }
    },
    {
      "entryId": "e2", "kind": "dispute", "proposedFilterId": "f_44aa…",
      "startMs": 1200000, "endMs": 1203000, "category": "language",
      "subcategory": null, "severity": null, "action": null,
      "evidence": { "origin": "downloaded", "manualReview": true, "locallyVerified": false, "modelVersion": null }
    }
  ]
}
```

| Field | Type and rule |
| --- | --- |
| `schemaVersion` | Literal `community-artifact-3` |
| `submissionId` | Token of at most 121 characters, so that `submit-<submissionId>` fits the 128-character operation ID (§2). A client-generated unique token, equal to manifest `submission_id` |
| `createdAt` | Integer Unix seconds |
| `author.contributorId`, `author.keyId` | Must equal the manifest and the signing request's key |
| `work.kind` | `movie` or `episode` |
| `work.tmdb`, `work.tvdb` | Decimal string (`[0-9]{1,12}`) or null. **At least one is non-null** (`422 work_identity_required`). For `episode` it is the series ID. |
| `work.season`, `work.episode` | Integers from 0 to 9999. Required (non-null) for `episode`, and null for `movie` |
| `work.lastEpisode` | Null, or an integer from `episode` to `episode + 15` (episode only) |
| `work.title` | String of 1–512 bytes. `normalize(title)` must be non-empty |
| `work.year` | Integer from 1870 to 2200, or null |
| `version` | `{ "choice": "alias", "aliasId": <approved aliasId> }` or `{ "choice": "other", "text": string(0..64 code points) \| null }` |
| `media.durationMs` | Integer from 1 to 86,400,000 |
| `media.matchKeys` | 0–5 key objects (§3.2) for the tier 1–3 types only. Each type appears at most once. All `sizeBytes` values must be equal. |
| `alignmentHint` | Null, or `{versionId, revision (int ≥ 1), offsetMs (int, ±86,400,000), scale (number 0.9–1.1), anchors (int 0–2000)}`. The service stores it for diagnostics only. It is never used in the fit (§6.3). |
| `coverage` | 0–9 objects, each with a unique `category`. Each has 1–512 `intervals`, sorted, non-overlapping, with `0 ≤ startMs < endMs ≤ durationMs` |
| `entries` | 0–2,000 entries, each with a unique `entryId` (token, at most 32 bytes) |
| `entries[].kind` | `support`, `dispute`, or `coverage_negative` |
| `entries[].proposedFilterId` | Token or null. Must be non-null for `dispute` and `coverage_negative`. A non-null value appears at most once per artifact. |
| `entries[].startMs`, `entries[].endMs` | Integers with `0 ≤ startMs < endMs ≤ durationMs` |
| `entries[].category` | A top-level MCF family from `taxonomy.json` |
| `entries[].subcategory`, `entries[].severity`, `entries[].action` | For `support`, all three are required: a token in the category's taxonomy family, `low`/`medium`/`high`, and `skip`/`mute`. For other kinds, all three must be null. |
| `entries[].evidence.origin` | `model` (local scan finding), `manual` (user-created), or `downloaded` (from a community master set) |
| `entries[].evidence.manualReview` | Boolean. True when a person reviewed, edited or approved the cue |
| `entries[].evidence.locallyVerified` | Boolean. True when the user confirmed or adjusted the cue, or it overlaps a local model finding of the same category (D21) |
| `entries[].evidence.modelVersion` | Null or a string of at most 128 bytes. Required to be non-null when `origin` is `model` |

The service checks these resource bounds before it parses the JSON:

| Bound | Value |
| --- | --- |
| Compressed size | 1,000,000 bytes |
| Expanded size | 4,000,000 bytes |
| JSON depth | 8 |
| String length | 512 bytes |
| Manifest size | 4,096 bytes |
| Request body size | 1,500,000 bytes |

Validation failures return `400 invalid_submission`. A schema-valid artifact
that is semantically refused returns `422` with a specific code (§7.2).

## 6. Merge algorithm

The master set of a version is a pure function of the following inputs:

1. the set of counted submissions and their stored fits;
2. the current votes;
3. the moderation state;
4. operator config;
5. the persistent **filter registry**, which provides stable IDs and
   incumbent subcategory and action values;
6. the version's **reference keys**.

Arrival order and processing order must not affect the result. The service
recomputes the whole version on every input change. It may cache, but only
if the result is byte-identical to a full recompute.

### 6.1 Counted inputs

- **Counted submission:** for each contributor and version, only the latest
  accepted submission counts. "Latest" means the highest service
  `acceptedSeq`, ignoring quarantined submissions. Earlier submissions are
  retained but superseded.
- Quarantined attestations, submissions, contributors and versions are
  excluded.
- **Weight-0 support:** a `support` entry with `origin = downloaded` and
  `locallyVerified = false` is stored with `ignoredReason = unverified_reshare`.
  It never creates or moves a filter (D15).
- **Canonical attestation order:**
  1. aligned `startMs`;
  2. aligned `endMs`;
  3. `category`;
  4. submission digest;
  5. `entryId`, all ascending in ASCII order.

### 6.2 Reference timeline

- **Reference keys:** a version's reference keys are the tier 1–3 keys of its
  founding submission. They persist through quarantine and merges, and are
  flagged `is_reference` in `version_match_keys`.
- **Identity-aligned submissions:** a counted submission that shares any
  reference key with the version gets the fit
  `{mode: "identity", offsetMs: 0, scale: 1}`.
- **Fitted submissions:** every other counted submission, for example from a
  merged-in candidate, is fitted against the **core set** (§6.3).
- **Core set:** the clusters produced by running §6.5 over identity-aligned
  support entries only. Core clusters have no IDs.

### 6.3 Alignment fit (offset + linear drift)

`aligned(t) = floor(scale × t + offsetMs + 0.5)` in float64. Negative
results are clamped to 0. Let `S` = `alignmentSearchMs` (default 30,000) and
`N` = `matchToleranceMs` (default 500).

**1. Choose candidate pairs:**

- Pair each counted support entry `e` of the submission with each core
  cluster `c` of the same category.
- Keep the pair when `|c.startMs − e.startMs| ≤ S` and
  `|(c.endMs − c.startMs) − (e.endMs − e.startMs)| ≤ max(N, 0.1 × (c.endMs − c.startMs))`.

**2. Estimate the offset:**

- Each pair has `d = c.startMs − e.startMs`.
- For each pair `i`, count its inliers: the pairs `j` with
  `|d_j − d_i| ≤ N`.
- Pick the `d_i` with the most inliers. Break ties by the smaller `|d_i|`,
  then the smaller `d_i`.
- The offset is the median (§6.4) of that `d_i`'s inlier `d` values.

**3. Build one-to-one anchors:**

- Start from the inlier pairs.
- If an entry or a cluster appears in more than one pair, keep the pair
  with the smallest `|d − offset|`.
- Break ties by the smaller cluster `startMs`, then by `entryId`.

**4. Fit drift.** Drift is fitted only when all of these hold:

- there are at least `alignmentMinAnchors` anchors (default 3);
- the anchor entry starts span at least `alignmentMinDriftSpanMs`
  (default 300,000).

Use Theil–Sen:

- `scale` = the median of `(c_j.start − c_i.start) / (e_j.start − e_i.start)`
  over all anchor pairs `i < j` with distinct `e.start`.
- `offsetMs` = the median of `c.start − scale × e.start`, rounded
  half-up to an integer.

Fall back to offset-only, with `scale = 1`, in any of these cases:

- `|scale − 1| > alignmentMaxScaleDeviation` (default 0.05);
- the drift fit yields fewer inlier anchors than offset-only, where an inlier
  has both aligned bounds within `N` of its cluster;
- there are too few anchors or too short a span.

**5. Handle no anchors:** with zero anchors, or an empty core set, use
`{mode: "none", offsetMs: 0, scale: 1}`.

The fit is stored as
`{mode: identity|offset|drift|none, offsetMs, scale, anchors}`. It is
recomputed whenever the core set changes. Medians of floats are computed in
float64. With an even count, a median is the mean of the two middle values.

### 6.4 Medians and spread

- **Integer median:** sort the values in ascending order. With an odd count,
  take the middle value. With an even count, take
  `floor((a + b) / 2)` of the two middle values.
- **Spread:** `spread.startMs = max |startMs − median start|` over the counted
  support members, and the same for `endMs`. A single member has a spread of 0.

### 6.5 Same-filter rule and clustering

Sweep the aligned, counted, weight-1 support entries in canonical order. For
each entry `e`:

**1. Find candidate clusters.** A cluster is a candidate when:

- it is in the same top-level category;
- `|cluster.medianStart − e.startMs| ≤ N` and
  `|cluster.medianEnd − e.endMs| ≤ N`, using the current running medians.

Different categories never merge, even at identical times.

**2. Choose a cluster.** Pick the candidate with the smallest
`max(|Δstart|, |Δend|)`. Break ties in this order:

- the smaller `|Δstart| + |Δend|`;
- the cluster whose *prior registry ID* (§6.6) equals
  `e.proposedFilterId`;
- the earlier-created cluster in this sweep.

**3. Handle a repeat contributor.** If the chosen cluster already has an
entry from the same contributor, store `e` as `ignoredReason = duplicate`
(weight 0).

**4. Add or create.** If the contributor is new to the cluster, add `e` and
update the medians. If there is no candidate, create a new cluster.

Members are not re-evaluated after a median moves.

### 6.6 Stable filter IDs

The registry stores the following for every ID ever issued:

- `filterId` and `versionId`;
- `createdSeq`;
- incumbent `subcategory` and `action`;
- `state`: `active`, `retired`, or `quarantined`;
- `mergedInto`.

Each attestation stores the filter ID it was last assigned to (its *prior
ID*).

1. **Order the clusters.** Process them in order of median start, median
   end, then category.
2. **Inherit a prior ID.** A cluster inherits the prior ID shared by most of
   its members, among IDs not already claimed by an earlier cluster. Break
   ties by the smaller `createdSeq`.
3. **Create an ID.** A cluster with no unclaimed prior ID gets a new ID. Let
   `m` be its first member in canonical order:

   ```text
   filterId = "f_" + hex(SHA-256(framed("editarr-community-filter-id-v1", [versionId, m.submissionDigest, m.entryId])))[0:32]
   ```

   `framed(domain, fields)` is the ASCII domain, a zero byte, then each UTF-8
   field preceded by its byte length as a u32 big-endian. This is the same
   framing as receipts.
4. **Retire unclaimed IDs.** A registry ID that no cluster claims becomes
   `retired`. If most of its former members are now in one cluster, set
   `mergedInto` to that cluster's ID. The served filter lists the retired IDs
   that point to it in `mergedIds`.
5. **Store assignments.** Write the new assignments back as prior IDs.

### 6.7 Classification votes

These votes count only weight-1 support members:

- **Subcategory:** the plurality of member subcategories wins. On a tie,
  keep the incumbent if it is among the tied values. Otherwise use the first
  member's value in canonical order if it is tied. Otherwise use the
  ASCII-smallest value.
- **Severity:** each member adds `severityWeights[severity]`. The defaults
  are `low = 1`, `medium = 1`, `high = 3`. The highest total wins, and ties
  go to the more severe value (`high > medium > low`).
- **Action:** uses the same rule as subcategory (plurality, incumbent on a
  tie, then the first member, then ASCII order). This is the suggested
  default; see Open questions.
- **Bounds:** `startMs` and `endMs` are the medians (§6.4) of the member
  bounds.

### 6.8 Signals and confidence

Each contributor contributes **at most one signal per filter**, chosen in this
precedence:

1. support member;
2. dispute;
3. explicit `coverage_negative`;
4. implicit coverage negative;
5. vote.

**Assigning disputes and explicit `coverage_negative` entries:**

1. If `proposedFilterId`, or a registry ID retired into a current filter,
   resolves to a filter with the same category, assign the entry to that
   filter.
2. Otherwise, align the entry's bounds with the submission fit and apply the
   same-filter test (§6.5, step 1) against the final filters. Choose the
   closest match. Break ties by the smaller `filterId`.
3. If neither step assigns the entry, store it unassigned.

**Implicit coverage negative:** for each final filter `F` of category `k`, if
the contributor has no other signal on `F`, and `[F.startMs, F.endMs]` is
inside one aligned `coverage[k]` interval of the contributor's counted
submission, it counts as a coverage negative.

**Votes:** an `up` or `down` vote counts only when the contributor has no
attestation signal on the filter. Votes follow merges through
`mergedIds`/`mergedInto`.

| Quantity | Definition |
| --- | --- |
| `support.attestations` | Count of weight-1 support members, which equals `contributorCount` |
| `support.upvotes` | Count of counted `up` votes |
| `negative.downvotes` | Count of counted `down` votes |
| `negative.disputes` | Count of dispute signals |
| `negative.coverageNegatives` | Count of explicit plus implicit coverage negatives |
| `S` | `attestations + upvotes` |
| `Neg` | `downvotes + disputes + coverageNegativeWeight × coverageNegatives` (default weight 0.25) |
| `confidence` | `wilson(S, Neg)`. With `n = S + Neg + 2`, `p = (S + 1) / n` and `z = 1.96`, the lower bound is `(p + z²/2n − z·sqrt(p(1−p)/n + z²/4n²)) / (1 + z²/n)`, clamped to 0..1 and rounded as `floor(x × 1e9 + 0.5) / 1e9`. This is the same as `community-ranking-wilson-1`. |
| `lowConfidence` | `Neg > S` |
| served | `confidence ≥ confidenceFloor` (default 0.05). The filter must also not be quarantined, and its version must not be quarantined. |

Low Confidence filters are still served. Clients hide them by default.

A filter below the floor stays in the registry and returns if its signals
improve. With the defaults:

- a single-support filter is dropped only at 9 negatives (0.047);
- 8 negatives still give 0.051.

### 6.9 Revision

- After recompute, the service builds the snapshot's served content: every
  snapshot field except `revision`, `generatedAt` and the signature.
- If those canonical bytes differ from the current revision's content, the
  service increments `revision` by 1 and stores an immutable snapshot.
- Confidence-only and count-only changes therefore also advance the
  revision.
- A version starts at revision 1 when it is created.

### 6.10 Worked example

This example uses the defaults: `N = 500` ms and coverage-negative weight
0.25.

- Submissions A, B and C are identity-aligned.
- Submission D comes from a merged candidate, with fit `offset +1,200 ms`.
- E upvotes the filter and F downvotes it.
- G's counted submission covers `violence` from 0 to 3,600,000 with no
  matching cue, which is an implicit coverage negative.

| Sweep | Entry (aligned) | Classification | Action on cluster | Medians after |
| --- | --- | --- | --- | --- |
| 1 | C 599,800–604,800 | kicking/low/skip | create | 599,800 / 604,800 |
| 2 | A 600,000–605,000 | punching/medium/skip | join (Δ 200/200) | 599,900 / 604,900 |
| 3 | D 600,000–605,100 (raw 598,800–603,900) | punching/medium/mute | join (Δ 100/200) | 600,000 / 605,000 |
| 4 | B 600,300–605,400 | punching/high/skip | join (Δ 300/400) | 600,000 / 605,050 |

The result:

| Field | Value |
| --- | --- |
| Bounds | Starts {599,800, 600,000, 600,000, 600,300} → 600,000. Ends {604,800, 605,000, 605,100, 605,400} → 605,050 |
| Spread | start 300, end 350 |
| Subcategory | punching 3, kicking 1 → `punching` |
| Severity | medium 1+1 = 2, high 3, low 1 → `high` |
| Action | skip 3, mute 1 → `skip` |
| Counts | `attestations` 4, `upvotes` 1, `downvotes` 1, `coverageNegatives` 1. `S` = 5 and `Neg` = 1.25 |
| Confidence | `wilson(5, 1.25)` = **0.393857102**. `lowConfidence` is false, and the filter is served |

A second filter, `language/swearing` from A only, is disputed by B and C:
`S` = 1 and `Neg` = 2, so the confidence is 0.117618231. It is served and
flagged Low Confidence.

Reference values (`S`, `Neg` → confidence):

| `S`, `Neg` | Confidence |
| --- | --- |
| 1, 0 | 0.207654955 |
| 1, 1 | 0.150035709 |
| 1, 2 | 0.117618231 |
| 1, 8 | 0.051366601 |
| 1, 9 | 0.046964148 (dropped) |
| 3, 0 | 0.375528264 |

### 6.11 Clarifications

The reference engine (`community-service/src/filters`) resolves these details
as follows:

- **Implicit coverage negatives** are suppressed when the contributor has any
  cue on the filter. Cues include duplicate and unverified re-shared support,
  and quarantined attestations. A quarantined attestation never counts as a
  signal.
- **Derived ID collision:** if the derived ID is already claimed by an earlier
  cluster in the same recompute, derive from the next member in canonical
  order. If every member collides, append the decimal counter `"1"`, `"2"`, …
  as an extra framed field of the first member.
- **`mergedInto`** requires a strict majority (more than half) of the retired
  ID's counted former members, including duplicates, to be in one cluster.
  With no counted former members, the previous `mergedInto` is kept. Chains
  are collapsed to point at the current filter.
- **Votes across retired IDs:** when one contributor has votes on several IDs
  that resolve to the same filter, the vote on the current ID counts.
  Otherwise the vote on the ASCII-smallest ID counts.
- **Quarantined version:** the revision and its served content stay unchanged
  while the version is quarantined. A restore advances the revision only if
  the content then differs.

## 7. Service endpoints

General rules:

- Every path is under the service origin. `/v1/*` routes require a signed
  contributor request (§2), and `/admin/v1/*` routes require operator
  authorization.
- JSON bodies are strict objects, and unknown fields return `400`.
- Errors use `{"error":{"code":"<code>"}}`, with extra fields only where
  this document defines them.
- Responses carry `Cache-Control: private, no-store`.
- Every `/v1` route can return these common errors:

  | Status | Code |
  | --- | --- |
  | 401 | `invalid_signature`, `clock_skew`, `key_inactive` |
  | 403 | `membership_ineligible` |
  | 503 | `membership_unavailable`, `service_unavailable` |
  | 413 | `body_too_large` |
  | 429 | `rate_limited`, with a `Retry-After` header in seconds |
  | 409 | `nonce_conflict`, `operation_conflict` |

- Mutations return `{"version":1,"state":"completed","result":{...}}`. A
  retry with the same operation and bytes returns the stored result.

### 7.1 `POST /v1/match` (read)

```json
{ "version": 1,
  "keys": [ { "type": "md5_quick", "value": "<32 hex>", "sizeBytes": 4823749120 },
            { "type": "tmdb", "kind": "movie", "id": "603" },
            { "type": "title_year", "title": "The Matrix", "year": 1999 } ],
  "label": { "aliasId": "theatrical" },
  "runtimeMs": 8160512,
  "limit": 20 }
```

Request rules:

- `keys`: 1–12 entries, with at most 2 per type.
- `label`: `{aliasId}`, `{text}`, or null.
- `runtimeMs`: an integer from 1 to 86,400,000, or null.
- `limit`: 1–50, default 20.

There is no cursor. Results are truncated after ordering (§4).

`200`:

```json
{ "version": 1, "results": [ {
    "versionId": "v_12ab", "status": "confirmed", "aliasId": "theatrical", "label": "Theatrical", "labelText": null,
    "work": { "workId": "w_77", "kind": "movie", "tmdb": "603", "tvdb": null, "season": null, "episode": null, "lastEpisode": null, "title": "The Matrix", "year": 1999 },
    "tier": "2", "matchPercent": 80, "matchedKeys": ["md5_quick"], "runtimeAgreement": "not_applicable",
    "runtimeMs": 8160500, "trust": 0.41, "revision": 12, "filterCount": 34, "contributorCount": 5 } ] }
```

- `tier` is one of `"1"`–`"5"`, `"runtime_mismatch"`, or `"other_version"`.
- `label` is the display label of `aliasId`, or `labelText`, or null for an
  unlabeled candidate.
- `filterCount` counts served filters at the current revision.

Errors: `400 invalid_match`.

### 7.2 `POST /v1/submissions`

Body:

```json
{"version":1,"submissionId":"…","artifact":"<base64 gzip>","manifest":"<base64>","envelope":"<base64>"}
```

The base64 is canonical and padded. `submissionId` must equal the artifact and
manifest. Use `Idempotency-Key: submit-<submissionId>`.

The service runs these steps in one transaction:

1. verify the digest, the signature and the signing key, which must be
   `active` (a `retired` key cannot upload);
2. validate (§5);
3. accept the version (§3.5);
4. store the raw buffers;
5. recompute (§6);
6. advance the revision.

Result:

```json
{ "version": 1, "operationId": "submit-…", "submissionId": "…", "submissionDigest": "<64 hex>",
  "workId": "w_77", "versionId": "v_12ab", "versionStatus": "confirmed",
  "disposition": "attached", "tier": "2", "revision": 13, "supersedes": "sub_prev…",
  "outcome": { "newFilters": 2, "supports": 31, "disputes": 1, "coverageNegatives": 0, "ignored": 3, "unassigned": 0 },
  "aliasSuggestion": null }
```

- `disposition` is `attached`, `created_confirmed`, or `created_candidate`.
- `aliasSuggestion` is `{suggestionId, text, state:"pending"}` or null.

Errors:

| Status | Code |
| --- | --- |
| 400 | `invalid_submission` |
| 403 | `upload_suspended` |
| 409 | `submission_conflict` (same `submissionId`, different bytes) |
| 422 | `work_identity_required`, `unknown_alias`, `invalid_taxonomy`, `invalid_bounds`, `key_mismatch` (author or key doesn't match the signer) |

Each contributor may accept `submissionsPerDay` submissions (default 50).

**Clarifications.** The service resolves these details as follows:

- The `Idempotency-Key` must be exactly `submit-<submissionId>`; any other key is
  `400 invalid_submission`. A `submissionId` longer than 121 characters is
  `400 invalid_submission`. A retry with the same operation and bytes returns the
  stored result. A different upload that reuses a stored `submissionId`, from any
  contributor, is `409 submission_conflict`.
- `tier` is the attach tier, and is null when the submission created the version.
- `supersedes` is the contributor's previous counted submission for the version, or
  null.
- `outcome` counts this submission's rows after the recompute: `newFilters` are
  registry IDs created by it that are now active; `supports` are weight-1 support
  members; `disputes` and `coverageNegatives` are assigned disputes and explicit plus
  implicit coverage negatives; `ignored` are weight-0 support entries
  (`unverified_reshare` or `duplicate`); and `unassigned` are disputes and explicit
  coverage negatives that no filter took.
- `403 upload_suspended` refuses a contributor under moderator quarantine (§7.8).
  A suspended contributor already fails request authentication.
- Past the daily limit the service returns `429 rate_limited` with `Retry-After`. The
  limit counts accepted submissions in the rolling 24 hours before the request, and is
  checked again inside the submission's write transaction, so concurrent uploads
  cannot exceed it.
- A recompute that finds the accepted version merged meanwhile plans the submission
  again.
- A submission that would exceed the stored-byte caps (§8) is refused with
  `503 service_unavailable`, and nothing is stored.

### 7.3 `GET /v1/versions/{versionId}/master[?knownRevision=N]`

This route has no body. It returns `200` in one of two forms:

- **Not newer:** if `knownRevision` is at least the current revision, the
  response is `{"version":1,"changed":false,"versionId":"…","revision":13}`
  and no receipt is issued.
- **Newer:** otherwise:

```json
{ "version": 1, "changed": true,
  "snapshot": "<padded base64 of exact snapshot JSON bytes>",
  "snapshotSignature": { "schemaVersion": "community-master-signature-1", "service": "https://community.example",
    "serviceKeyId": "sha256:<64 hex>", "versionId": "v_12ab", "revision": 13,
    "snapshotDigest": "<64 hex>", "issuedAt": 1795553648, "signature": "<padded base64 of 64 bytes>" },
  "receipt": { "receipt": { "schemaVersion": "community-master-receipt-1", "service": "https://community.example",
    "serviceKeyId": "sha256:<64 hex>", "contributorId": "c_7f3a", "versionId": "v_12ab", "revision": 13,
    "snapshotDigest": "<64 hex>", "receiptId": "<64 hex>", "issuedAt": 1795553648 }, "signature": "<padded base64>" } }
```

Snapshot JSON (UTF-8, compact, key order as shown):

```json
{ "schemaVersion": "community-master-snapshot-1", "service": "https://community.example",
  "versionId": "v_12ab", "revision": 13, "generatedAt": 1795553600,
  "work": { "workId": "w_77", "kind": "movie", "tmdb": "603", "tvdb": null, "season": null, "episode": null, "lastEpisode": null, "title": "The Matrix", "year": 1999 },
  "versionInfo": { "status": "confirmed", "aliasId": "theatrical", "label": "Theatrical", "labelText": null, "runtimeMs": 8160500, "trust": 0.41 },
  "filters": [ { "filterId": "f_9c1d…", "mergedIds": [], "category": "violence", "subcategory": "punching",
    "severity": "high", "action": "skip", "startMs": 600000, "endMs": 605050,
    "spread": { "startMs": 300, "endMs": 350 }, "confidence": 0.393857102, "contributorCount": 4,
    "support": { "attestations": 4, "upvotes": 1 },
    "negative": { "downvotes": 1, "disputes": 0, "coverageNegatives": 1 }, "lowConfidence": false } ] }
```

Snapshot rules:

- `filters` are sorted by `startMs`, `endMs`, `category`, then `filterId`.
- There are at most `maxServedFilters` filters (default 4,000), and the
  snapshot is at most 2,000,000 bytes.
- `generatedAt` is the time the revision was created. Bytes for a revision
  are immutable.

**Snapshot digest:** `snapshotDigest` is the lowercase hex SHA-256 of the
decoded snapshot bytes.

**Signed bytes:**

```text
framed("editarr-community-master-snapshot-v1", [schemaVersion, service, serviceKeyId, versionId, decimal(revision), snapshotDigest]) ‖ u64be(issuedAt)
```

**Receipt identity:**

```text
receiptId = hex(SHA-256(framed("editarr-community-master-receipt-identity-v1", [service, contributorId, versionId, decimal(revision), snapshotDigest])))
```

The receipt identity doesn't include `issuedAt`, so a repeated download
returns the same `receiptId`.

**Receipt signed bytes:**

```text
framed("editarr-community-master-receipt-v1", [schemaVersion, service, serviceKeyId, contributorId, versionId, decimal(revision), snapshotDigest, receiptId]) ‖ u64be(issuedAt)
```

`decimal` is base-10 with no sign or leading zeros. The receipt signer key is
reused for both signatures, and `GET /v1/receipt-keys` returns its key
history.

**Client verification steps:**

1. Fetch and pin the receipt-key history.
2. Base64-decode the snapshot and check `snapshotDigest`.
3. Verify the snapshot signature. The key must be `active` or `retired`;
   `revoked` keys are rejected.
4. Strict-parse the snapshot. Its `service`, `versionId` and `revision` must
   equal the signed values and the request.
5. Verify the receipt binding and signature the same way.

If any step fails, reject the whole response.

Errors:

| Status | Code |
| --- | --- |
| 404 | `version_not_found` |
| 410 | `version_unavailable` (quarantined) |
| 409 | `version_merged`, with `{"error":{"code":"version_merged","mergedInto":"v_…"}}` |
| 400 | `invalid_master_request` |

A contributor gets `versionChecksPerHour` requests (default 60).

**Clarifications.** `knownRevision` is optional and appears at most once as a
canonical non-negative decimal; any other query parameter is
`400 invalid_master_request`. The limit counts every authenticated request to this
route, including unchanged answers, in fixed one-hour windows, and returns
`429 rate_limited` with `Retry-After`. Both signatures use the issuing time of the
response as `issuedAt`; the stored receipt keeps the time of its first issue. Each
issued receipt records the snapshot's filter IDs and their `mergedIds` in
`master_revision_filters` for vote and report eligibility.

### 7.4 `POST /v1/filters/{filterId}/votes`

Body: `{"version":1,"vote":"up"|"down"|"none","receiptId":"<64 hex>"}`. Use
`Idempotency-Key: vote-<client id>`.

The vote is accepted only when all of these hold:

- `receiptId` belongs to the caller;
- the receipt's snapshot contained `filterId`, or a filter whose
  `mergedIds` contains it;
- the caller has no weight-1 support attestation currently assigned to the
  filter (self-vote).

`none` removes the vote. The service recomputes the version.

Result:

```json
{"version":1,"operationId":"vote-…","filterId":"f_…","versionId":"v_…","vote":"up","revision":14}
```

Errors:

| Status | Code |
| --- | --- |
| 403 | `vote_ineligible`, with `reason` `self_vote` or `receipt_required` |
| 404 | `filter_not_found` |
| 400 | `invalid_vote` |

`GET /v1/versions/{versionId}/votes` returns the caller's own votes:

```json
{"version":1,"versionId":"…","votes":[{"filterId":"f_…","vote":"up"}]}
```

It returns at most 4,000 votes.

**Clarifications.** The `Idempotency-Key` must start with `vote-`; any other key
is `400 invalid_vote`. A contributor may cast `votesPerHour` votes (from
`VOTE_LIMITS`, default 120) in fixed one-hour windows; beyond that the route returns
`429 rate_limited` with `Retry-After`. Retries of a completed vote are not counted.
A vote may name a retired filter ID listed in the receipt snapshot's `mergedIds`;
the self-vote check follows the whole `mergedInto` chain to the current filter.
The vote is stored under the current filter ID. Changing or removing a vote
replaces every non-excluded vote of the caller whose filter ID resolves through
`mergedInto` to the same current filter, so a vote left on a since-merged ID
never keeps counting. Changing or removing a vote keeps a moderator's
`exclude_vote`. A vote on a filter
whose version is merged meanwhile is recomputed on the target. The vote list returns
`404 version_not_found`, `409 version_merged` and `410 version_unavailable` like
§7.3.

### 7.5 `POST /v1/sync/revisions` (read)

```json
{ "version": 1, "runId": "<token>", "items": [ { "versionId": "v_12ab", "revision": 12 } ] }
```

- `items` has 1–500 entries, with unique `versionId` values.
- A run is one `runId`, made of at most `syncMaxBatchesPerRun` requests
  (default 20) within `syncRunWindowSeconds` (default 600) of its first
  request.
- Each installation gets `syncRunsPerDay` runs (default 1) per rolling 24
  hours.
- A new `runId` beyond the limit returns `429 sync_rate_limited` with
  `Retry-After`.

`200`:

```json
{ "version": 1, "changed": [
    { "versionId": "v_12ab", "status": "changed", "revision": 13 },
    { "versionId": "v_99", "status": "merged", "revision": null, "mergedInto": "v_12ab" },
    { "versionId": "v_50", "status": "unavailable", "revision": null } ],
  "nextRunAllowedAt": 1795640048 }
```

- Unchanged items are omitted.
- `unavailable` covers quarantined and unknown versions.
- Sync issues no receipts.
- Downloading changed versions uses `GET …/master` and its
  `versionChecksPerHour` limit (§7.3). A client finishes the run's batches
  first and defers downloads past that limit until `Retry-After`, without a
  new run.

**Clarifications.** Runs are scoped to the signing key's installation. A request
that would exceed the run's batches or window, or start a run past the daily limit,
returns `429 sync_rate_limited` with `Retry-After` until `nextRunAllowedAt`.
`nextRunAllowedAt` is the request time when another run may start now, otherwise
the time the oldest counted run leaves the rolling 24 hours. A malformed request
is `400 invalid_sync`. Items keep request order.

### 7.6 `POST /v1/reports`

```json
{"version":1,"target":{"type":"filter","id":"f_…"},"reason":"inaccurate","note":"…"}
```

- `target.type` is `filter` or `version`.
- `reason` is `spam`, `inaccurate`, `privacy`, `rights`, or `other`.
- `note` is at most 1,000 characters.
- The caller must hold a receipt for the version, and for filters a receipt
  whose snapshot contained the filter. Otherwise the report returns
  `403 report_ineligible`.
- A contributor can have one open report per target
  (`409 report_already_open`).

Result:

```json
{"version":1,"operationId":"…","reportId":"…","state":"open"}
```

**Clarifications.** `note` is optional. A report on an unknown target is
`403 report_ineligible`, so the route reveals nothing about targets the caller has
no receipt for. A version report also accepts a receipt for a version merged into
the target. A malformed request is `400 invalid_report`.

### 7.7 `GET /v1/aliases` and `GET /v1/receipt-keys`

`GET /v1/aliases` returns the approved aliases:

```json
{"version":1,"aliases":[{"aliasId":"theatrical","label":"Theatrical","spellings":["theatrical","theatrical cut",…]}]}
```

The list is ordered as in §3.4. `GET /v1/receipt-keys` is unchanged.

### 7.8 Admin (`/admin/v1`, operator authorization, origin and CSRF)

`POST /admin/v1/moderation` keeps the existing envelope:

```json
{"version":1,"actionId":"<uuid>","command":"…","resource":"…","targetId":"…","expectedRevision":0,"reason":"…", …extra}
```

| `command` | `resource` | Extra fields | Effect |
| --- | --- | --- | --- |
| `confirm_version` | `version` | — | Changes a candidate to confirmed |
| `merge_version` | `version` | `intoVersionId` | Sets the source to `merged_into`. Moves its submissions, keys, votes and receipts' eligibility to the target, then recomputes the target. Same-work only, and neither version may already be merged. Moved submissions lose their evidence tier (§3.5). Versions previously merged into the source are repointed to the target. |
| `approve_alias` | `alias_suggestion` | `aliasId` (existing) or `newAliasId` + `label` | Adds the suggestion as a spelling, or creates an alias. Rejected with `409 alias_conflict` if the spelling is taken. Every version whose `aliasId` is null and whose `labelText` equals the approved spelling is relabeled to that alias; each non-merged relabeled version advances its master `revision` (its `versionInfo` changes) and its moderation revision. |
| `reject_alias` | `alias_suggestion` | — | Rejects the suggestion |
| `quarantine` / `restore` | `filter`, `attestation` (`targetId = submissionId:entryId`), `submission`, `contributor`, `version` | — | Changes visibility and recomputes every affected version |
| `suspend` / `unsuspend` | `contributor` | — | Unchanged |
| `exclude_vote` / `restore_vote` | `vote` (`targetId = filterId`) | `contributorId` | Excludes or restores the vote, then recomputes |
| `resolve_report` | `report` | — | Unchanged |

The read routes are:

- `GET /admin/v1/versions?status=candidate&after=`
- `GET /admin/v1/alias-suggestions?state=pending&after=`
- `GET /admin/v1/moderation/{resource}/{id}`
- `GET /admin/v1/reports?after=`
- `GET /admin/v1/dashboard/versions/{versionId}/filters?after=` (registry state,
  quarantine and master filter values with count-only `attestations`, votes,
  disputes and `served`)
- `GET /admin/v1/dashboard/versions/{versionId}/submissions?after=` (submission
  ID, contributor, state, tier, quarantine and creation time; no bytes or digest)

Each returns 20 items per page with `nextCursor`. A version list row also
carries `contributorCount`, `filterCount` and `servedFilterCount`.

`POST /admin/v1/recompute` accepts
`{"version":1,"actionId":"<uuid>","versionIds":["…"]|"all","reason":"…"}`.

- It recomputes deterministically and records an audit row.
- `all` runs in bounded batches and returns `{"version":1,"jobId":"…"}`.
- The operator uses it after config changes.

**Clarifications.** The service resolves these admin details as follows:

- The envelope always carries `resource`. A vote action uses `resource: "vote"`,
  `targetId` = filter ID and `contributorId`; its moderation resource is
  `vote:<filterId>:<contributorId>`. Other resources are `<resource>:<targetId>`.
  `GET /admin/v1/moderation/{resource}/{id}` reads `filter`, `attestation`
  (URL-encoded `submissionId:entryId`), `submission`, `contributor`, `version`,
  `alias_suggestion`, `report` and `vote/{filterId}/{contributorId}`.
- Quarantining something already quarantined, or restoring something that is not,
  is `403 moderation_ineligible`, as are suspend, unsuspend, vote and report actions
  that do not change state. Each refusal is audited.
- Contributor quarantine (`contributors.quarantined`) is separate from suspension.
  It excludes the contributor's submissions and votes from every version they
  touched, and their uploads return `403 upload_suspended`; their requests still
  authenticate.
- Every quarantine, restore and vote action recomputes each affected version in the
  action's batch. A version restore advances the revision only if the served
  content then differs (§6.11).
- `merge_version` recomputes the target with the source's submissions, registry
  and votes. Filter IDs, and therefore votes and the eligibility of receipts for
  source snapshots, follow the filters into the target.
- An approved alias relabel advances `versions.revision` once and stores the
  relabeled snapshot at that revision; the recompute does not advance it again.
- A `versionIds` list holds 1–100 unique non-merged versions, otherwise
  `404 resource_not_found`. It returns
  `{"version":1,"actionId":"…","versions":[{"versionId","revision","changed"}]}`.
  `all` runs synchronously: it recomputes at most 200 non-merged versions in version
  ID order, 20 per batch, and answers
  `{"version":1,"jobId":"<actionId>","complete":bool,"nextAfter":"<versionId>"|null}`.
  To continue, send a new action with `"versionIds":"all","after":"<nextAfter>"`;
  `after` is allowed only with `all`. Reusing an `actionId` with a different body is
  `409 action_conflict`. A retry recomputes again, which changes nothing when no
  input changed. Recompute audit rows use `command: "recompute"` and need no
  `expectedRevision`.

Admin errors:

| Status | Code |
| --- | --- |
| 400 | `invalid_moderation` |
| 403 | `moderation_ineligible` (the action would not change state, for example quarantining a quarantined filter) |
| 404 | `resource_not_found` |
| 409 | `moderation_conflict` (stale `expectedRevision`), `alias_conflict`, `merge_invalid` |
| 429 | `operator_rate_limited` |

## 8. Storage schema (service)

The service migrations are **rebased** to a new baseline,
`0001_master_baseline.sql`, with its own schema lineage identifier
`community-master-1`.

- The owner explicitly authorized wiping the community service database
  because no users exist (ADR 0002, D1). The reset applies only to the
  community service. It is an operator action: stop the service, archive
  the old store, and initialize a fresh store with the offline runner.
- The runner refuses an old-lineage store. It never stamps or upgrades
  one.
- Later migrations return to verified-backup-first upgrades.

**Kept tables (adapted from 0001–0008 and 0014):**

| Area | Tables |
| --- | --- |
| Identity | `contributors`, `installations`, `installation_keys` |
| Enrollment | `enrollment_states`, `discord_oauth_states`, `oauth_usage_counters`, `discord_membership`, `discord_membership_refresh`, `profile_name_changes` |
| Replay and idempotency | `request_nonces`, `operations` |
| Observations | `usage_observations` |
| Moderation | `moderation_versions`, `moderation_epoch`, `operator_request_counts`, `moderation_actions` (with the new commands and resources), `reports` (`target_type`, `target_id` instead of `revision_id`) |

**New tables:**

| Table | Columns (PK **bold**) | Constraints and indexes |
| --- | --- | --- |
| `works` | **work_id**, kind (`movie`\|`episode`), created_at | — |
| `work_identifiers` | **provider**, **kind**, **value**, **season**, **episode**, **last_episode**, work_id | Unique on the PK. NULLs are stored as -1. Index on `work_id` |
| `work_identifier_conflicts` | **conflict_id**, submission_id, provider, value, existing_work_id, created_at | — |
| `work_titles` | **work_id**, **normalized**, **year**, title, observations | Index on `(normalized, year)` |
| `aliases` | **alias_id**, label, created_at | — |
| `alias_spellings` | **normalized**, alias_id, source (`seed`\|`moderator`) | Unique on `normalized` |
| `alias_suggestions` | **suggestion_id**, normalized, text, submission_id, state (`pending`\|`approved`\|`rejected`), alias_id, created_at | Index on `(state, suggestion_id)` |
| `versions` | **version_id**, work_id, alias_id, label_text, status, merged_into, runtime_ms, trust, revision, reference_submission_id, quarantined, created_seq, created_at | Index on `(work_id, status)` and `(work_id, alias_id, label_text)` |
| `version_match_keys` | **version_id**, **type**, **algorithm**, **value**, **size_bytes**, is_reference, observations | Index on `(type, algorithm, value, size_bytes)` |
| `submissions` | **submission_id**, digest (unique), contributor_id, installation_id, key_id, version_id, accepted_seq (unique, monotonic), tier, artifact_bytes, manifest_bytes, envelope_bytes, alignment_hint_json, fit_json, state (`counted`\|`superseded`\|`quarantined`), created_at | Index on `(version_id, contributor_id, accepted_seq)` and `(version_id, submission_id)` |
| `submission_entries` | **submission_id**, **entry_id**, kind, proposed_filter_id, start_ms, end_ms, category, subcategory, severity, action, origin, manual_review, locally_verified, model_version, aligned_start_ms, aligned_end_ms, weight, ignored_reason, quarantined | — |
| `submission_coverage` | **submission_id**, **category**, **ordinal**, start_ms, end_ms | — |
| `filter_registry` | **filter_id**, version_id, created_seq, incumbent_subcategory, incumbent_action, state, merged_into, quarantined | Index on `version_id` and `(version_id, filter_id)` |
| `master_filters` | **filter_id**, version_id, category, subcategory, severity, action, start_ms, end_ms, spread_start_ms, spread_end_ms, confidence, contributor_count, attestations, upvotes, downvotes, disputes, coverage_negatives, low_confidence, served, subcategory_tally_json, severity_tally_json, action_tally_json | Index on `(version_id, served)` |
| `filter_attestations` | **submission_id**, **entry_id**, filter_id, contributor_id, kind (`support`\|`dispute`\|`coverage_negative`), implicit (0/1), counted (0/1), prior_filter_id | Index on `(filter_id, contributor_id)`. Implicit rows have `entry_id = "cov:" + filterId` |
| `filter_votes` | **filter_id**, **contributor_id**, vote (`up`\|`down`), receipt_id, excluded, updated_at | Index on `contributor_id` |
| `master_revisions` | **version_id**, **revision**, snapshot_digest, generated_at, snapshot_bytes (nullable after supersession, if no receipt) | — |
| `master_revision_filters` | **version_id**, **revision**, **filter_id** | Kept for every revision with an issued receipt. Used for vote and report eligibility |
| `download_receipts` | **receipt_id**, contributor_id, version_id, revision, snapshot_digest, service_key_id, issued_at | Index on `(contributor_id, version_id)` |
| `sync_runs` | **run_id**, installation_id, started_at, batches | Index on `(installation_id, started_at)` |

Each raw submission is stored exactly once, as BLOBs, with no expanded copies
except `submission_entries`. Those rows are derived from the artifact and can
be rebuilt from it.

**Baseline details.** The rebased baseline resolves these storage details
conservatively; none changes a wire format:

- **Lineage:** the single-row, immutable `schema_lineage` table holds
  `community-master-1`. The offline runner and the server refuse a store whose
  lineage is missing or different with `unsupported_database_lineage`, before any
  version check, and print that reason.
- **Catalog:** the interim `master_works` (with its `catalog_seq` optimistic write
  guard) and `master_work_identifiers` are renamed `works` and
  `work_identifiers`. `version_acceptances` with the `version_counted_acceptances`
  view, and the `catalog_moderation_actions` audit for the version and alias
  commands, are kept. `submissions.submission_id` references
  `version_acceptances`, and a merge moves both rows.
- **Derived submission columns:** `submissions.match_keys_json` holds the
  artifact's tier 1–3 keys, which the merge needs for identity alignment (§6.2).
  Like `submission_entries` and `submission_coverage`, it is derived from, and can
  be rebuilt from, the stored artifact.
- **Moderation state:** `contributors.quarantined` records contributor quarantine,
  which is separate from `state` (suspension). `moderation_actions` adds
  `error_code` and the `recompute` command; the separate account, report and vote
  triggers of the old schema are replaced by one audit trigger that advances the
  resource's moderation revision.
- **Limits:** `rate_counters (scope, window_start, requests)` holds the fixed
  one-hour windows for `versionChecksPerHour` and `votesPerHour`. `submissionsPerDay`
  counts the contributor's accepted `submissions` in the rolling 24 hours before the
  request; the submission batch re-checks it through a `rate_counters` guard row whose
  `requests > 0` check aborts the batch over the limit.
  `sync_runs` is keyed by `(installation_id, run_id)`, so a run ID is scoped to its
  installation.
- **Stored bytes:** `artifact_storage_policy` and `artifact_storage_usage` are kept.
  Inserting a submission charges its three raw buffers and is refused beyond the
  global (350,000,000 bytes) or per-contributor (7,000,000 bytes) cap.
- **Snapshot bytes:** the latest stored revision of a version always keeps
  `snapshot_bytes`. An older revision keeps them only while a receipt for it was
  first issued within the last 7 days, and at most 8 older revisions per version keep
  them; others keep their digest, receipts and `master_revision_filters` but drop the
  bytes. The check runs when a revision is stored and in the hourly maintenance. Old
  snapshots are never served, so this bounds storage without changing any response.

### 8.1 Operator configuration

`MASTER_POLICY` is a JSON environment value. Every key is required.

| Key | Default | Meaning |
| --- | --- | --- |
| `matchToleranceMs` | 500 | N in §6.5. Never sent to clients |
| `severityWeights` | `{"low":1,"medium":1,"high":3}` | §6.7 |
| `confidenceFloor` | 0.05 | Serving floor (§6.8) |
| `coverageNegativeWeight` | 0.25 | Weight of a coverage negative. Proposed default; see Open questions |
| `candidatePromotionContributors` | 3 | §3.5 |
| `alignmentSearchMs` | 30000 | §6.3 |
| `alignmentMinAnchors` | 3 | §6.3 |
| `alignmentMinDriftSpanMs` | 300000 | §6.3 |
| `alignmentMaxScaleDeviation` | 0.05 | §6.3. Covers 23.976/25 fps speed-up |
| `maxServedFilters` | 4000 | Per snapshot; 1–4,000, the client's snapshot limit |
| `syncRunsPerDay` | 1 | Per installation, rolling 24 h |
| `syncMaxBatchesPerRun` | 20 | — |
| `syncRunWindowSeconds` | 600 | — |
| `versionChecksPerHour` | 60 | `GET …/master` per contributor |
| `submissionsPerDay` | 50 | Per contributor |

`SUBMISSION_LIMITS` holds the request, manifest and artifact bounds (§5.2):
`{"requestBytes","manifestBytes","artifact":{"compressedBytes","expandedBytes","depth","stringBytes"}}`,
each at most its §5.2 value. `VOTE_LIMITS` keeps `requestBytes` and `maxQueries` and
adds an optional `votesPerHour` (default 120) per contributor (§7.4).
`REPORT_LIMITS` and `DOWNLOAD_RECEIPT_KEYS` keep their existing shapes. Changing `MASTER_POLICY` takes effect only after an
`/admin/v1/recompute`.

## 9. Removed features

These are removed with no compatibility path:

- `community-artifact-1` and `community-artifact-2`
- `community-summary-1` and `community-summary-2`
- `community-compatibility-stream-1` bands
- `/v1/contributions` upload
- `/v1/availability/batch` and `/v1/matches` revision search, with
  `community-search-2` cursors
- `/v1/contributions/{revisionId}/artifact` download
- `community-download-receipt-1` and `/v1/import-receipts`
- revision likes, dislikes and votes (`/v1/contributions/{revisionId}/vote(s)`)
- `community-ranking-wilson-1` revision ranking
- revision statistics, publications and lineage
- these tables: `releases*`, `contributions`, `contribution_*`,
  `artifact_payloads`, `import_receipts` and `revision_votes`

The Wilson formula itself is reused (§6.8).

## Open questions

These items are not decided in the approved plan. This contract specifies a
suggested default for each, which the owner should confirm.

1. **Action merge rule.** The suggested default is a plurality vote, where
   ties keep the incumbent, then the first member's action (§6.7).
2. **Coverage-negative weight.** The suggested default is 0.25 (§6.8).
3. **Tier 1–3 match to a version with a different label.** The suggested
   default is that the label wins: the submission doesn't attach, and it is
   handled by rule 1, 2 or 4. The key collision is visible to moderators
   (§3.5).
4. **Founder counting toward promotion, and Other attaching to existing
   candidates.** The suggested default counts the founder, and allows Other
   submissions to attach to an equal-label candidate at tiers 1–3 (§3.5).
5. **Query without a runtime at tiers 4–5.** The suggested default is
   `runtime_mismatch` (5%).
6. **Other versions of the same work.** The suggested default returns them
   as `other_version` at 0%, so users can adapt another version's set
   (D4) (§4).
7. **Version trust formula.** The suggested default is in §3.5.
8. **Latest-submission-wins per contributor and version.** The suggested
   default is in §6.1.
