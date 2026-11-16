# CSS-37 donation artifact contract

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally, October 3, 2026**. This is a pure preparation/signing
mechanism, not an enabled publication workflow.

## Approval and versions

The user explicitly selected **Approve Artifact V2**, preserving v1 support and
the existing detached-signature format. [G-CONTENT](../decisions.md#g-content)
allows partial/legacy evidence and bounded previewed notes; [G-MATCHING](../decisions.md#g-matching)
supplies actual-only audio, mixed-review exclusions and TMDB/TVDB association.
No license, production limit, launch or migration decision was inferred.

- `community-artifact-1` and `community-summary-1` remain strict and unchanged.
- `community-artifact-2` and `community-summary-2` add name, source revision,
  matching evidence, settled source states, source snapshots, audio association,
  portable analyzed metadata and retiming records.
- `community-manifest-1` accepts either artifact version and rejects a manifest
  whose version differs from its actual payload. Summaries are recomputed.
- `community-artifact-signature-1` and its binary domain/binding are unchanged.
  Both exact gzip and exact manifest bytes remain covered.

Python validation lives in [codec](../../../scanner/formats/community/codec.py)
and [v2 schema](../../../scanner/formats/community/artifact_v2.py); the
[Worker schema](../../../community-service/src/artifact/schema.ts) and
[codec](../../../community-service/src/artifact/codec.ts) independently validate
the same contract. V1 rejects v2-only fields rather than silently dropping them.

## Builder interfaces

- [SourceRef and capture_source](../../../scanner/integrations/community/donation_sources.py):
  identify a persisted scan or review and return immutable private snapshot bytes.
- [DonationOptions and project_source](../../../scanner/integrations/community/donation_projection.py):
  explicit public name/identity, author/key/submission, creation time, target
  duration, optional terms/notes/supersedes and chosen audio source.
- [prepare_donation and approve_donation](../../../scanner/integrations/community/donations.py):
  preparation returns frozen `PreparedDonation`; approval returns the existing
  `SignedArtifact` without regenerating artifact or manifest.
- [identity_from_reference](../../../scanner/modeling/community_work_refs.py):
  positive typed TMDB/TVDB IDs or exact supported public HTTPS URLs, without
  fetching providers. TMDB episode URLs normalize to series ID and aired numbering;
  TVDB needs an ID-bearing URL, not an unresolved slug.

Callers provide `ResourceBounds` and an explicit positive `source_limit`.
There are no production defaults. The generator/tests use synthetic bounds only.
`DonationOptions.media` is an explicit normalized association, not proof of media
ownership. Other Files donation preparation requires TMDB/TVDB identity or a typed
series association plus episode numbering; ordinary local scanning is unchanged.

## Snapshot and consent rules

Capture uses an independent read-only SQLAlchemy session. SQLite explicitly starts
a read transaction, including under its legacy driver; WAL concurrent-edit tests
prove one consistent source generation. Queries have deterministic ordering and
row/source limits; returned private snapshot and preview bytes have configured
ceilings. Only complete/failed/cancelled scans are eligible. Missing/active sources
or unavailable lineage fail. No application row is flushed or committed.

**CapturedSource and PreparedDonation are backend-only private objects. Never
serialize them to clients, logs, hosted requests or public backup metadata.** Their
private snapshot includes source paths/settings/details solely for generation
guards. Only `preview_bytes`, artifact bytes and manifest bytes are public outputs.

The preview contains exact decoded outgoing artifact/manifest, byte hashes,
exclusions and warnings. Consent binds hashes of artifact, manifest and preview
plus the private source-generation hash. Preparation rechecks the source after
fingerprint work; approval recaptures it and checks remembered file generations.
Changed bytes, preview/token, source rows or observed files require a fresh preview.
The source check is the approval snapshot, not a lock preventing later edits.
CSS-45 owns atomic persistent intent/retry integration.

Signing requires explicit non-null sharing terms, but no terms or license are
chosen here. CSS-45/46 must derive author/key from the trusted local identity,
require administrator authorization/CSRF, present consent and obey current hosted
eligibility. A consent token is not authentication or permission to publish.

## Public projection

Allowlisted output contains public work/release claims; bounded name and notes;
artifact-local event/source/attempt/parent labels; taxonomy, timing, channel,
severity/confidence/action; public effective stage versions/models/settings and
coverage; optional imported revision/digest/author references; portable matching
evidence; chosen analyzed-track descriptor; and declared target retiming.

No raw local media/scan/finding/attempt IDs, paths, provider-service URL, alternate
track inventory, requested/default selection, full transcript, detector labels,
details, nested selection/debug settings, credentials or media bytes are copied.
Public strings are NFC-normalized, control-stripped, UTF-8 bounded and conservatively
filtered for URL/path/credential markers. Unsafe optional text is redacted with a
preview warning; unsafe required text fails. This is allowlisting plus conservative
filtering, not a universal detector for arbitrary secrets in user-authored text.
CSS-46 must render all public strings as untrusted text.

V1's effective-settings allowlist stays unchanged. V2 additionally permits the
explicit scalar execution settings declared in `PUBLIC_EXECUTION_SETTINGS`:
scene/coarse parameters, Qwen segment/requested-model parameters, audio filter,
Whisper model and credits detector version. Private/nested settings are excluded.
Recorded `audio*` stages map to audio; `visual*` and `credits*` map to video;
unrecognized capabilities remain both. Coverage is not inferred from scan flags.
Each source retains its own timeline, status, revisions and attempt provenance.

Generated observations include zero findings and unselected observations without
authored actions. Manual/imported snapshots are labeled manual, with unknown
confidence and no invented scanner execution. Reviews include selected authored
filters, distinguish linked derived events from locally added manual filters,
and preserve source attribution. Imported references do not assert verification
of a remote source's detached signature; CSS-39/44 own that trust check.

## Audio and retiming

Only immutable actual generated audio selection supplies identified metadata.
Language/description survive sanitized in exact outgoing bytes; stream index and
file version are excluded. Role comes from explicit disposition, never title/default.
Historical/imported unknown audio remains unknown, without guessed metadata/digest.
An identified descriptor without supported stream proof is not a strong audio match.

Distinct actual selections and independently unknown sources require choosing one.
The saved MCF channel controls audio participation. Other-source audio is visibly
excluded; a `both` event retains its video component. Saved review rows and current
selection remain untouched, and no automatic splitting occurs. Alternate sources
retain bounded provenance/coverage, not a second identified track description.

Actual audio hashing reuses CSS-35B's guarded accessor. Video claims require the
existing scanned MD5 to agree with the current file before independent sampling.
Verified media can add the unchanged CSS-35 sampled container fingerprint. Missing
media and known unsupported formats produce explicit warnings without fabricated
digests; stale input, budget failures and cancellation fail preparation. Observed
file generations are checked again before approval. Filesystem metadata is not
adversarial write immunity; legacy MD5 verification reads the full file, and
cancellation/native I/O checks are cooperative rather than hard CPU deadlines.

Review event times are copied as saved target times, never transformed again.
Every source has one `applied: true` retiming record: original MCF bounds when
available, current target synchronization and affine/unknown mapping. Unknown
source bounds never become a fabricated transform. Source coverage is checked
against source duration, not the derived target. Codec validation rejects missing,
duplicate or reapplied retiming and inconsistent matching/audio/source claims.

## Verification

Final local gates:

```sh
.venv/bin/python -m pytest -q tests/test_community_donations.py tests/test_community_donation_reviews.py tests/test_community_donation_vectors.py tests/test_community_artifact_codec.py tests/test_community_artifact_signatures.py tests/test_execution_provenance.py tests/test_scan_provenance.py tests/test_review_retime_provenance.py tests/test_media_identity.py tests/test_stream_fingerprints.py tests/test_community_fingerprints.py tests/test_community_matching.py
pnpm --dir community-service exec vitest run test/artifact-donations-v2.test.ts test/artifact-codec.test.ts test/artifact-signatures.test.ts test/compatibility.test.ts
pnpm --dir community-service exec tsc --noEmit
```

Results: **220 Python tests, 92 Worker tests; typecheck, changed-Python Ruff and
formatting passed; touched-file editor diagnostics clean.** Tests cover actual
selected audio/video on generated media, changed requested selection, media/source
mutation, WAL consistency, bounds/cancellation, known/unknown mixed tracks,
both-channel preservation, privacy canaries and hostile v2 claims.
Documentation checks passed for 159 local links/anchors, six files' fences and
task/index status consistency; `git diff --check` passed.

[Frozen vectors](../../../tests/fixtures/community/donation-artifacts-v2.json)
contain builder-generated AI/manual/imported/derived/retimed/partial/legacy/zero
gzip, manifest and detached-envelope bytes. Python compares regenerated bytes;
the Worker independently verifies original signatures, recomputed summaries and
lineage. Existing v1 vectors still pass. Explicit synthetic regeneration:

```sh
.venv/bin/python -m tests.test_community_donation_vectors tests/fixtures/community/donation-artifacts-v2.json
```

No existing/user database, personal media, model, database schema/migration, live service,
network publication, frontend, deployment, outbox or backup inventory changed.
Windows/Linux execution, representative media accuracy and production resource
limits/license acceptance were not verified or selected.

## Next action

CSS-39/45/46 consume the versioned codecs, exact signed bytes and consent rules;
do not serialize private preparation objects or regenerate queued bytes on retry.
CSS-38 is next: read its brief and CSS-11/23/36 handoffs plus this v2 summary shape,
then inspect existing D1 migrations/catalog repositories before designing the
additive, data-preserving work/release/contribution schema. Do not migrate an
existing database or enable publication as part of that inspection.