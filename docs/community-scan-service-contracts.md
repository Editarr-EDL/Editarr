# Community scan service: proposed contracts

Status: **Technical design supporting the [main plan](community-scan-service-plan.md)**.
The main plan's decision register governs this document. Unapproved recommendations
and example limits are not implementation decisions.
October 3 [G-LICENSE](community-scan-service-tasks/decisions.md#g-license) selects
standing community reuse permission with retained public notes and attribution,
per-donation rights/exact-preview confirmation, no license picker and no author
withdrawal beyond operator moderation. Older withdrawal alternatives below are
superseded by that decision; they do not authorize an owner withdrawal endpoint.
Production activation remains deferred.
October 5 deployment update: the [Docker/SQLite contract](community-service-docker.md)
supersedes Workers/D1 hosting. The Node 24 service reuses the same signed API and
numbered SQL schema; historical D1 references below identify shared storage/transaction
contracts, not instructions to provision D1. Preserve exact BLOBs, security and history
policy. Node HTTP parity is CSS-66 and image/conditional legacy import is CSS-67.
October 7: user canceled CSS-68 for local Docker on Mac mini; CSS-69 records the
local-only pilot scope and unverified hardware/recovery risks. Live
Tunnel/Access/Discord remains OPS-01/02; no public activation is authorized.
Discord membership gates enrollment, and D20 confirms retaining Ed25519 request and
artifact signatures. See [Discord enrollment](community-scan-service-discord-enrollment.md)
for how account eligibility and signing-key registration fit together.

## 1. Boundaries and identity lifecycle

Four identities have different meanings:

- **Local administrator:** existing Editarr authentication; controls local settings,
  sharing, identity export, and restore. Never sent to the community service.
- **Community contributor:** opaque service ID to which reputation, contributions,
  votes, and history attaches; privately linked to one verified Discord user ID.
- **Installation:** one logical installation under a contributor, with independent key
  history; restored copies retain this ID and share its current key.
- **Signing key:** registered public key proving authority for that installation.
  It has an algorithm, key ID, state, creation time, and optional retirement time.

Use a portable random private key in the backend's private data directory, never the
browser, OS hostname, media file path, container image, or disposable cache. Persist
it before enrollment. Public-key encoding must be canonical before computing its
SHA-256 key identifier. Contributor IDs survive key rotation and are not derived anew
from each replacement key. Registration is idempotent for an existing public key.

Use owner-only filesystem permissions and appropriate Windows access controls, reusing
the existing secure-file practices. OS-backed storage is optional only if export and
headless/container operation remain supported. Never encrypt a portable key with a
hardware-derived secret that would make the promised restore impossible.

The implemented private-file inventory, `community-identity-1` manifest,
`sha256:` public-key identifier, pending replacement slot, and locked
load/backup interfaces are specified in [CSS-14 evidence](community-scan-service-tasks/decision-evidence/14-local-key-storage.md).
Local last-known key state does not authorize requests; the hosted service
must verify current status before protected use.

Proposed Discord-bound signing-key enrollment flow:

1. User enables community access; show the outgoing metadata and identity policy.
2. Backend loads or creates its key atomically, under the existing instance lock.
3. Complete the hosted Discord authorization and verify required server membership.
   Bind that result to this installation's short-lived enrollment challenge, public
   key, audience, and expiry. Rate-limit issuance before durable writes where possible.
4. Sign the challenge and register. Atomically consume the authorization/challenge
   and resolve the unique Discord-to-contributor link. A new installation registers
   its own key; existing installation recovery requires fresh Discord authorization
   and explicit UI replacement, never silent replacement of another key.
5. Return contributor ID, generated display name, key state, allowed operations,
   policy versions, and current limits. Save the response with the local key.

Generate names from a curated word list and show a stable short ID for disambiguation.
October 1 profile decision supersedes nonunique display names: generate curated
three-word names and require uniqueness among current names using NFC, Unicode
lowercase, then NFC. Custom names are trimmed/NFC, 3-40 Unicode code points,
without control/format characters. Names are untrusted display text, never
credentials; escape them at rendering boundaries. Valid name changes have no
hourly allowance. Identical-name updates and idempotent retries do not change
the profile revision; released names may be reused.
Return the current name and monotonic `profileRevision` on every successful
enrollment, signed identity read and rename response. Clients replace cached
labels from these responses and refresh signed identity on connection/profile
views so another installation's rename is reflected without reenrollment.

Hosted profile/enrollment contract v1 is captured in
[client fixture](../tests/fixtures/community/enrollment-profile-v1.json).
Before OAuth, the local backend stores a random 32-byte lowercase-hex confirmation
secret and sends only its SHA-256 hash, domain-separated by
`editarr-enrollment-confirmation-v1:`, in `confirmationHash` at browser start.
The hash is bound with installation/key and copied into the verified grant.
Only after authenticated local administrator confirmation may the backend sign
`POST /v1/enrollments/complete` with exact JSON fields `version: 1`, `stateId`,
`installationId`, `confirmationSecret`, and CSS-12 `Idempotency-Key: stateId`.
The Worker requires the signature, matching secret hash and fresh membership.
Public IDs or browser consent alone cannot claim a grant. No private key, local
administrator credential or provider token is transmitted in this payload.

Same-grant signed retries reconcile only the committed active installation/key
before the original ten-minute grant expiry. Fresh authorization for the same
active installation/key also reconnects without another key or contributor identity.
A different key targeting that installation returns `recovery_required`; normal
enrollment never performs lost-key replacement. Retired/revoked keys are rejected.
`PATCH /v1/identity` accepts only `{ "displayName": "..." }`, signed with a
durable operation ID and fresh nonce for retries. Changed bytes for an existing
operation conflict; retries return the latest profile rather than a stale label.
Responses include contributor/short/installation/key IDs, active key state,
display name/revision, policy/protocol versions, implemented allowed operations
and allowed operations, never private Discord linkage. Client-side
confirmation/admin storage is implemented by CSS-29's protected local client;
[CSS-30](community-scan-service-tasks/30-connection-settings.md) renders the explicit
approval UI in Settings > Connections. Hosted success displays the public state
reference for that handoff, never the confirmation secret or provider code/token.
Pasted trusted callback URLs are immediately reduced to their public reference.
Local saved status and current signed membership/profile are separate; an outage
does not make a cached profile eligible. Local approval is not a hosted authentication claim.

The implemented [local client and API handoff](community-scan-service-tasks/29-local-service-client.md)
documents canonical HTTPS origin/audience configuration, no redirects, bounded
fresh-signature retries, safe failure states, current profile projection, and
the private version-1 enrollment/profile journal. Registration and clearing the
pending secret are one atomic manifest publication; an interrupted SQLite
connection projection is repaired on the next access without changing identity.

A shared application header may identify client family but is not a security boundary.
Do not embed a privileged global enrollment secret in the distributed app. Discord-gated
enrollment requires trusted ingress, current membership and explicit confirmation. No anonymous
fallback is allowed when Discord authorization or membership verification fails.

The portable identity model treats copied keys as the same logical installation.
A clean installation signing into the same Discord account registers independently
under the same contributor; its key can rotate/revoke without affecting other installations.
Explicit lost-key recovery targets the existing installation. Never merge by display names, IPs,
filenames, or hardware hints, or allow a new credential to reset contributor identity.

Rotation starts only when the user manually triggers the action in Editarr's UI.
There is no scheduled, startup, upgrade, restore, or transfer-triggered rotation and
no independent CLI/operator action that initiates contributor-key rotation. The UI's
backend request proves possession of the old and new keys. Atomically activate the
replacement for the same installation/contributor IDs and retire that installation's old key immediately, with
no grace period. Subsequent authorization attempts using the retired key fail.

Persist the replacement private key and operation ID locally before submitting the
rotation. If the response is lost, reconcile the same user-triggered operation using
the replacement key if it was activated; do not depend on the retired key remaining
usable. Recovery must not generate another replacement or initiate another rotation.
If the original rotation did not commit, the old key remains active. Surface pending
or failed status in the UI and prompt for a fresh backup after success; encryption is optional.

Keep old public keys to verify historical artifacts; retirement is different from
compromise. Revocation also blocks new actions immediately at the authoritative check.
Display compromise/withdrawal status without silently removing downloaded evidence.

The hosted local/mock [CSS-31 lifecycle contract](community-scan-service-tasks/decision-evidence/31-key-lifecycle-contract.md)
implements old-key signed dual-proof rotation, atomic nonce/operation/key
commit, owned-key revocation and contributor-scoped operation lookup. A prepared
replacement signs status with the exact retained body/operation; a detached old
proof binds that one intent without allowing general retired-key access. Status
distinguishes committed from absent while the relevant key remains active, but
is not cancellation of a concurrent send. Other-account keys/operations are
rejected. Self-revocation needs a still-active owned sibling to reconcile; no
revoked-key exception or implicit replacement is introduced. CSS-32 owns the
local private journal, administrator confirmation and actual UI trigger.

If an installation's key is lost, backup recovery remains supported. Fresh Discord
authorization plus explicit UI recovery may replace it and immediately retire the
lost key; other installations remain authorized. Normal sign-in does not silently
replace a key. Lost Discord access has no automatic identity
merge or operator bypass policy. Operator suspension can block compromised credentials.

The implemented [CSS-33 recovery protocol and runbook](community-scan-service-tasks/decision-evidence/33-key-recovery-contract.md)
defines separate fresh-Discord recovery authorization, candidate-signed atomic
replacement/status and active-candidate operation lookup. Normal enrollment cannot
consume recovery grants. The local administrator-confirmed private journal retains
one candidate/operation through expiry, outage, restore and interrupted publication;
fresh authorization never silently creates another key. Retained installation
metadata or matching backup identifies the target; a clean reinstall registers a
separate installation under the existing contributor. All four backup modes retain
pending/completed evidence, and success prompts for a fresh optional-encryption backup.

## 2. Request authentication and replay handling

Use the accepted Ed25519 approach with Python cryptography and Node Web Crypto.
Use the standard algorithm,
not the legacy `NODE-ED25519` interface.
[Node Web Crypto](https://nodejs.org/docs/latest-v24.x/api/webcrypto.html)

Define a narrow, versioned application profile of HTTP Message Signatures. Specify
the method, service authority, exact target path/query, content type/encoding, body
digest, created/expiry values, key ID, request nonce, and mutation idempotency key as
required covered components. Restrict algorithms/key encodings; reject ambiguity and
missing required fields rather than guessing canonicalization.
[HTTP Message Signatures](https://www.rfc-editor.org/rfc/rfc9421.html)

Hash the exact agreed HTTP message content bytes and verify the signed digest. Do not
parse JSON and reserialize it before verifying. Freeze compression/digest semantics
in Python/TypeScript fixtures, including empty bodies and content encoding.
[Digest Fields](https://www.rfc-editor.org/rfc/rfc9530.html)

The implemented `editarr-community-request-v1` covered-component order, wire
bytes, canonical URI/Structured Fields rules, cross-language vectors and verifier
API are frozen in [CSS-12 evidence](community-scan-service-tasks/decision-evidence/12-request-signing-profile.md).
This pure profile does not implement replay persistence or server authorization;
CSS-25/29 must bind it to trusted key status, exact body transport and durable
nonce/idempotency state before protected routes are enabled.

TLS remains required. Signatures do not conceal media queries or payload contents.
Installation private keys never leave the local application. Public registration metadata does not confer
trust that uploaded findings, fingerprints, or claimed model versions are truthful.

For state changes, require an unpredictable nonce and short validity window; keep a
durable `(key_id, nonce)` uniqueness record until expiry plus allowed clock skew.
Consume it atomically with the operation's SQLite state change, or its durable reservation
for storing another artifact. A timestamp alone is not replay protection. Cleanup is bounded.

Use a separate durable idempotency key scoped to contributor and operation. A retry
uses a fresh nonce/signature and the same operation key. Equal keys with different
payload digests fail with conflict; a completed identical operation returns its stored
result. Unique content/import/vote constraints outlive temporary nonce cleanup.

Read-only searches can allow a valid signed request to repeat during its short expiry
window, avoiding a SQLite nonce write for each search. This is a deliberate bounded replay
tradeoff, not a claim that all requests are single use. Each replay still consumes
passive request/write observations; reads never create likes or download popularity.

Check key status on the authoritative path before protected activity. Cached public
key material must not substitute for current authorization status: neither retirement
nor revocation permits a stale-cache grace period for reads or mutations.

Correctly signed requests outside the allowed time window receive no-store
`401` with `error.code: "clock_skew"`. Synchronize the system clock, then explicitly
retry the saved intent with a fresh signature/nonce and unchanged operation ID/body.
Malformed lifetimes, altered bytes and unverifiable signatures remain generic
failures. This diagnostic is returned only after digest and signature verification;
the local client preserves the code without remote detail or automatic retries.
See the [completed route/auth audit](community-scan-service-tasks/25-worker-request-auth.md).

Service authorization stages: inexpensive shape/size checks and burst controls,
signature/key checks, contributor eligibility including current membership policy,
current authority checks, then application work. Local
Editarr routes retain existing administrator authentication and CSRF protections.
Community identity export/restore and sharing settings are privileged local actions.
Membership is sufficient without screening/role requirements. Recheck on use when
15 minutes have elapsed since successful verification; if an expired check cannot
refresh, pause community reads/writes. Known ineligibility denies access immediately.
Successful rejoin verification restores eligibility unless separately suspended by
Editarr operators. Recheck method selection follows the CSS-70 technical comparison.

## 3. Work, release, and fingerprint contracts

The implemented local normalization, additive discovery DTO, real sanitized
capture evidence and explicit Other Files confirmation are specified in the
[CSS-34 contract](community-scan-service-tasks/decision-evidence/34-media-identity-contract.md).
This supplies matching inputs, not global alias reconciliation or a new tolerance.

Public identity must never equal the local `MediaItemRow` UUID or an Arr numeric row ID.
Represent identifiers as `{provider, entity_type, value}`. Keep aliases with recorded
provenance; do not let an arbitrary contributor merge two global works by assertion.
Cross-provider linkage comes from validated source data or operator reconciliation.

Episode identity supports a provider episode ID where available, plus a typed series
ID, numbering scheme, season/episode, and optional ordered episode span. Recognize
specials (including season zero), absolute numbering, double episodes, and anime/DVD
ordering as distinct cases. A shared file containing two episodes has a file timeline;
do not shift its events onto one episode without an explicit mapping.

For each media file, collect locally:

- Original release/scene title, release group, edition, source and quality tokens.
- Measured duration and its source/precision; approximate catalog minutes are labeled.
- File size, container/codec as descriptive data, and timebase/frame-rate information.
- Actually analyzed audio track's language and description/title as metadata
   identifiers, role (main/commentary/descriptive), codec/channel hints and timing.
   Unknown fields stay unknown. Track index alone is not portable across remuxes;
   description differences alone do not establish a compatibility conflict.
- Fingerprint algorithm ID/version, digest, sampling layout, and measurement time.

The approved [CSS-35 fingerprint contract](community-scan-service-tasks/decision-evidence/35-file-fingerprint-contract.md)
implements `editarr-community-file-sha256-sampled-1`: nine evenly spaced 1 MiB
positions, merged overlap/adjacency, and size plus ordered offsets/lengths bound
with big-endian framing. The user selected it after synthetic-only bounded
measurements, acknowledging unavailable real-media/storage evidence. It is a
cross-install byte fingerprint, not whole-file equality, perceptual similarity or
an attestation. Optional `sha256-full-1` is separate standard full-file SHA-256;
it must not force Find Filters to read an entire movie. Legacy MD5 is unchanged.

October 2 scope extension: [CSS-35A](community-scan-service-tasks/35a-audio-track-provenance.md)
adds explicit per-file audio selection and immutable actual execution metadata;
[CSS-35B](community-scan-service-tasks/35b-stream-fingerprints.md) adds independent
sampled compressed-audio/video stream fingerprints. Its user-approved three
250 ms AAC-LC/H.264-AVCC v1 format and synthetic-only bounds are recorded in the
[selected-track/stream contract](community-scan-service-tasks/decision-evidence/35-track-contract.md).
Do not reinterpret the container
hash as a stream digest or assume its byte layout works for demuxed streams.
Re-encode-tolerant perceptual fingerprints are deferred. Stream content identity
and measured start/duration/timebase are distinct; equal samples do not prove
whole-stream equality or timestamp alignment. [CSS-36](community-scan-service-tasks/36-compatibility-policy.md)
implements different audio/video bands. Its approved inclusive fixed 2.0-second
file-probe duration tolerance does not authorize automatic retiming. The October 2
CSS-36 timing approval requires identical known playback-relative stream start and
duration, compared as bounded exact rational seconds, with no alignment tolerance.
Equivalent timebases may match; unknown container origin cannot establish alignment.
Known stream timing conflicts affect only their channel. Rounded catalog runtime cannot establish strong
timing evidence; work/edition/episode/timing and selected audio language/role
conflicts remain authoritative over superficial labels or popularity.

[CSS-35C](community-scan-service-tasks/35c-audio-track-selection-ui.md) displays the
chosen AI input: preselect a single available track or unambiguous flagged default,
otherwise require selection. Remember only for that file/version and revalidate;
never silently substitute tracks. AI contributions use only the individual audio
track actually scanned, preserving its language and description/title, not the
file's alternate-track inventory. Historical/imported unknown-track evidence may
still be shared with explicit unknown labeling and required review, never a guessed
fingerprint or strong audio claim. Derived mixed-track reviews require choosing
one track and visibly excluding other-track audio findings from outgoing bytes;
video findings and the saved review remain unchanged. Unknown evidence retains
its unknown association rather than being relabeled as the chosen track.

The locally implemented [CSS-36 policy](community-scan-service-tasks/decision-evidence/36-compatibility-core.md)
provides Python/Worker file/identity/duration and independent stream classification
under `community-compatibility-stream-1`, with full-output golden parity. Comparison
inputs add nullable `video_stream` and `audio_stream`, each with algorithm/digest,
nullable rational `playback_start`/`duration`, provenance and bounded description.
Candidate audio requires `analyzed` provenance; the target accepts `selected` or
`analyzed`. These reported fields are evidence, not independent attestations.
Language/role must describe those tracks and remain known for strong audio eligibility.
Descriptions never promote or block matches. Equal file/video hashes cannot identify
the analyzed audio track. Missing or differing supplied stream evidence remains
review-required unless a known conflict makes that channel incompatible; differing
container hashes do not prevent independent stream matches. All candidates start
unselected. No lookup route or publication integration is introduced here.

Cache fingerprints against file identity/size/modification metadata and algorithm
version. Detect a file changing during probing or hashing; invalidate and retry.
Keep canonical matching fields and display strings separate so punctuation handling
does not erase meaningful release/edition distinctions. Bound user-supplied text.

Use indexed work lookup first and calculate release compatibility only for a bounded
candidate set. Report a band plus reasons/missing/conflicting fields, not a fabricated
99% confidence. Match audio and video independently where appropriate. Work conflicts
or known timing conflicts cannot be repaired by a high reputation score.

Filename/title inference without provider IDs requires user confirmation of the work.
The first service need not crawl TMDB/TVDB or retain artwork/descriptions. Other Files
can use manually supplied typed IDs and locally measured file data; missing identity
gets an actionable status, not a guessed global association.

## 4. Artifact and summary formats

Use a versioned, bounded JSON interchange artifact. Following the CSS-10
comparison, the user selected uncompressed JSON text for SQLite serialized
summaries (alongside separate indexed fields) and gzip JSON for immutable
immutable SQLite artifact BLOBs. Resource limits and signed-byte profiles are
not selected by that encoding decision.
The pure `community-artifact-1` / `community-summary-1` /
`community-manifest-1` codec and exact-byte fixtures are documented in
[CSS-11's contract evidence](community-scan-service-tasks/decision-evidence/11-artifact-contract.md).
Production resource limits are still unset; CSS-13 owns signature binding.
It represents scan evidence or an explicit authored filter snapshot. EDL/MCF remain
import/export formats in Editarr; neither alone contains the required community data.

Before fixing the storage encoding, evaluate **JSON versus Protocol Buffers (Protobuf)**
for space savings in SQLite serialized metadata and compressed artifact BLOBs separately. Benchmark
representative small and large scans, comparing both uncompressed and compressed sizes,
encoding/decoding CPU and memory, SQLite queryability, schema evolution, Python/TypeScript
tooling, and exact-byte signature handling. Keep searchable SQLite fields indexed rather
than hiding them in opaque payloads. The CSS-10 report records the comparison
and the subsequent JSON selection; Protobuf remains experimental only.

| Section | Required meaning |
| --- | --- |
| Envelope | Artifact schema version, immutable submission ID, type, creation time, author/key, source references, declared sharing terms |
| Media | Typed work IDs, release identity, measured timing, fingerprint versions, analyzed audio track/language |
| Provenance | Editarr creation version/build, scanner behavior version, model IDs and immutable revisions/digests where available, relevant effective settings |
| Coverage | Per detector/channel/category capability: complete/partial/failed/disabled/unsupported/unknown, intervals, sampling, enrichment status |
| Findings | Stable artifact-local event IDs, start/end, evidence source, category/subcategory, severity, model confidence when meaningful, authored action where applicable |
| Derivation | Parent artifact IDs/digests, copied-event lineage or explicit manual origin, recorded retiming/edit transformations |
| Integrity | Artifact digest, manifest digest, signing key ID, detached signature over their versioned binding |

Store contributor-signed manifest bytes exactly. Bind manifest digest, artifact digest,
contributor ID, artifact format, and submission ID in a versioned durable signature.
The service must verify that signed ownership matches the authenticated submitter.
This signature remains verifiable after the request signature expires. Define the
precise encoding and cross-language test vectors before implementation.

The implemented `community-artifact-signature-1` detached envelope, binary
binding, original/derived byte vectors and trusted historical-key verifier
interface are frozen in [CSS-13 evidence](community-scan-service-tasks/decision-evidence/13-artifact-signatures.md).
This pure verifier does not persist key history, establish current authorization,
or validate a derived contribution's referenced source by itself.

On October 3 the user approved artifact v2 while preserving v1 and that detached
signature profile. The implemented local-only
[CSS-37 builder contract](community-scan-service-tasks/decision-evidence/37-donation-artifact-contract.md)
adds `community-artifact-2` / `community-summary-2`; manifest v1 accepts either
artifact version and rejects version substitution. V2 includes a public name,
source revision, portable CSS-36 matching evidence, settled source states,
bounded source snapshots, actual-only audio association and explicit per-source
already-applied target-timeline retiming. Unknown/manual/imported provenance is
not promoted to AI evidence. Strict v1 schemas, summaries and byte vectors remain
supported unchanged.

The builder captures a read-only persisted generation, projects public allowlisted
fields, and creates the outgoing preview from the exact gzip/manifest bytes.
Approval requires matching consent and unchanged source/file generations; signing
does not regenerate either byte sequence. Mixed-track authored reviews require one
chosen audio source and preview other-track exclusions while retaining video and
the saved review. Independently unknown sources are not silently grouped. Imported
source references retain original attribution without claiming remote signature
verification. License policy, limits, hosted acceptance and privileged UI/outbox
integration remain separate tasks; this builder enables no publication route.

Separate immutable author claims from mutable service facts such as moderation state,
download totals, votes, and calculated rank. Validate and recompute all summary counts
from the actual payload. Reject inconsistencies; do not sign/endorse a client summary
just because its JSON is well formed. New names or content create a revision if part
of the signed artifact; retain explicit supersedes relationships.

Validate finite times and scores, start/end order, duration bounds, supported actions,
taxonomy pairs, maximum findings/depth/text lengths, and compressed/uncompressed
size/expansion limits. Preserve unknown historical provenance as unknown. Reject
unsafe schemas with actionable errors; never execute or fetch URLs from a payload.

Proposed count rules: `total` is unique events; evidence-source counts distinguish
audio/visual/manual; channel counts may overlap for a `both` event and therefore need
not sum to total. Category/subcategory counts use the current classification registry
plus an explicit unclassified bucket. Record registry version. Do not infer detector
coverage from event categories or a `scan_audio` checkbox alone.

Partial scans and older scans with unknown provenance are allowed with explicit
coverage/unknown labels; do not imply complete analysis or fabricate historical models.
Bounded free-text event notes/comments are allowed and must appear in the sharing
preview; exclude complete transcripts/debug output and render notes as untrusted text.
Imported third-party EDLs do not become AI scans. Model-confidence values from different
detectors are not interchangeable. Human edits can have unknown confidence rather
than fabricated model certainty. Credits detection keeps its distinct scan mode and
must not imply objectionable-content coverage.

All event times refer to the declared source file's playback timeline, with explicit
time units and origin. Validate against that source duration, not an uncertain target's
runtime. Import preserves source bounds; user-approved mapping belongs to the saved
review. A donated derived filter set uses its declared target timeline and records the
transform as provenance, so another importer does not apply it a second time.

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

CSS-38 implements the catalog subset in numbered SQLite revision 0009, with verified
artifact v1/v2 repository inputs and explicit caller-supplied limits. Additional
contributor-asserted identifiers associate releases; only explicit verified-source
or operator evidence may add a protected global alias. Older published parents,
immutable depth/edges and bounded counts prevent cycles. Owned operation linkage
supports recovery without reassigning exact bytes or object keys. This is internal
persistence, not an enabled upload or lookup route. See the
[schema/API/measurement/backup contract](community-scan-service-tasks/decision-evidence/38-catalog-schema-contract.md).

Local imported scans can retain the existing `origin='imported'` with separate community
provenance instead of conflating remote authorship with local scan origin. Migrations
add the required tables/contracts; no direct service-to-local-database row copying.

Audit representation gaps before finalizing the artifact-to-local adapter: existing
finding confidence is non-nullable, provider identity is a single string, and coverage
is not a full outcome model. Migrate schema/API/rendering to represent unknown confidence
and richer identity/coverage honestly; do not fill missing values with fabricated
certainty. Keep established legacy values and annotate their known provenance.

## 6. Publication, import, and failure recovery

Publication now uses one SQLite transaction. Preserve the versioned lifecycle names
`reserved → object_stored → published`, or `failed/expired`; later
operator `quarantined`. Legacy `withdrawn` values remain historical schema state,
not an author endpoint. Both `reserved` and `object_stored` are internal transitions
within one transaction for new uploads. Historical rows remain unchanged.

Signed POST `/v1/contributions` and owned signed GET
`/v1/publication-operations/{id}` bind active key/member authority to original
v1/v2 gzip, manifest and signature bytes. Explicit limits remain required and
production routes remain disabled.

CSS-63 requires exact signed `sharing_terms: community-reuse-1` for new and pending
publications. The signature authenticates the author's permission assertion; it does
not establish legal ownership. CSS-46 requires local rights-to-share/exact-preview
approval. Referenced originals are checked from their immutable digest-bound BLOBs
in the bounded lineage read: compatible terms, original revision/author and matching
V2 source attribution are required. Unknown/other terms are not upgraded. Completed
historical retries and generic signature verification retain original claims.
HTTP 400 permission/attribution refusals are terminal local jobs, retaining evidence:
`sharing_permission_required`, `source_permission_unknown`,
`source_permission_incompatible`, `source_attribution_mismatch`.

1. Validate size/schema/signature and authorize the author. Account for
   authenticated request attempts separately, including rejected uploads.
2. In one SQLite batch, insert the owned operation, nonce, catalog records and all three
   immutable BLOBs, update passive payload-byte observations, recheck active authority,
   publish and save the completed result. Failure leaves no new publication records
   or byte-count changes; no background publication recovery is needed.
3. Retry the same operation with the original bytes. Identical content is idempotent;
   changed bytes are rejected. Completed retries, including after a lost success
   response, return the saved result without another payload or byte-count increase.
   Older pending records may complete only under the current permission/lineage policy;
   an incompatible intent is retained rather than silently rebuilt or relicensed.
4. Readers require published state; download services read exact SQLite bytes after
   current authorization and moderation checks. CSS-42 owns read integrity, CSS-64
   owns approved cleanup/retention, and CSS-67/68 own Docker backup/recovery verification.
   CSS-60/61 retain legacy D1 archive evidence; CSS-40 is retired.

Migration 0010 and the [historical single-store decision](community-scan-service-tasks/d1-storage-scope.md)
supersede earlier cross-store CSS-38/39 evidence; the [Docker runtime](community-service-docker.md)
now supplies the SQLite adapter. Existing wire
formats, signatures and client retry contracts are unchanged.

Prefer a single bounded upload request initially. Benchmark full validation, digest,
decompression and signature verification under the target Node/Docker CPU, memory
and concurrency limits. If realistic scans exceed approved capacity, return for a
scope/limits decision. Do not skip validation, silently enlarge caps or buy capacity.

For download, authenticate and check status before fetching/caching bytes. Verify
digest, detached author signature/key binding, schema, limits, and compatibility locally.
Stage then commit a local scan plus provenance atomically; `(service, revision, digest)`
provides idempotent reuse across target associations. Cache successful artifacts locally.
Remote payloads cannot specify local paths, database IDs, commands, or export targets.

Return a service-authenticated download receipt bound to contributor/revision/digest.
October 3 CSS-42 implements local-only signed
`GET /v1/contributions/{revisionId}/artifact` as an exact original-buffer JSON/base64
bundle, without HTTP content encoding and with a distinct personalized transport
digest. Current authority/moderation checks run even on immutable-byte cache
hits. The user approved separate service Ed25519 no-expiry receipts with stable
contributor/revision/digest identity and retained trusted service key history.
The [CSS-42 contract](community-scan-service-tasks/decision-evidence/42-artifact-download-contract.md)
freezes byte framing, DTO/error/key-history rules, secret/backup inventory and
additive unapplied 0011 daily read/write telemetry. No download increments popularity.
October 3 [CSS-44 local import](community-scan-service-tasks/44-verified-local-import.md)
adds signed `GET /v1/receipt-keys` under the existing read gates. The user approved
configured HTTPS service-origin trust, fresh discovery before each import and no
stale fallback or independent pinning. Exact original evidence and verification
history commit atomically under additive local `0006_community_evidence`, with
explicit target associations, safe review/export attribution and original-event
derived lineage. Receipt staging is a separate post-commit transaction; a failure
does not undo import, and retries use the original saved receipt. Full backups retain
that evidence; settings backups exclude it. No new local HTTP/UI or acknowledgment
feature was implemented by CSS-44; CSS-50/51 own their integration.
After successful local import, acknowledge it through a signed idempotent mutation.
Count one eligible receipt and update summary counters transactionally. A dishonest
client can acknowledge without using the scan; receipts reduce casual inflation but
do not prove playback or human satisfaction. Retry failed acknowledgments independently.
October 4 [CSS-51](community-scan-service-tasks/51-import-receipts.md) implements
approved unique contributor/revision counting, excluding author self-imports,
under numbered SQLite 0012. Signed `POST /v1/import-receipts` verifies
the original envelope against configured service public history and commits
nonce, owned result, immutable fact and counter atomically. Equivalent valid
issuances reconcile using stable receipt identity despite changed issue time/key.
The legitimate local client sends only after commit; independent recovery/retry
retains original bytes/operation identity. Counts are statistics, not trust points;
voting is separately approved in CSS-52 and revision-only ranking in CSS-53.

October 4 [CSS-52](community-scan-service-tasks/52-votes-service.md) adds signed
`POST /v1/contributions/{revisionId}/vote` with `{version:1,vote}` and a unique
`Idempotency-Key` per intent, plus signed `GET` on the same path for own current
vote/eligibility and `GET /v1/contributions/{revisionId}/votes` for public totals.
All responses are `private, no-store`; no voter histories are exposed. Mutation
returns `{version:1,state:"completed",result:{version:1,operationId,revisionId,vote}}`.
An old retry returns its original result, not the current vote, and never reapplies
it. Current reads are authoritative for present state. One mutable vote requires
an exact-revision counted import, active requester and current membership; self
votes fail. Upload restrictions do not block voting. Suspension/nonmembership
blocks access without changing stored votes/totals. No reason field is accepted.
Numbered SQLite 0013 stores neutral explicitly and atomically changes aggregates;
nonce, completed operation and vote commit together. Independent bounded
reconciliation preserves import counts. Explicit `VOTE_LIMITS` is required;
production, tiers, local vote client/UI and reports are not implemented by CSS-52.

October 4 [CSS-53](community-scan-service-tasks/53-ranking-policy.md) supplies
`ranking:{policyVersion:"community-ranking-wilson-1",qualityScore,voteCount,
uploaderBoost:0,score}` in candidate DTOs. The approved Wilson lower bound uses
z=1.96 and one positive/negative prior vote, rounded to nine decimals. Actual
neutral votes are excluded; score equals qualityScore. No author boost, age decay
or usage-admission change. Order is video band, audio band, descending score, ASCII revision
ID. No weaker-compatible band can win through popularity; new/derived/revised
scans use their own votes only. Import/upload volume does not affect quality.

The score is calculated from freshly read counters on every metadata cache hit,
not signed artifact data or persistent rank state. Policy version participates in
cache/cursor identity. `community-search-2` includes a hash of bounded current
ranked IDs/vote counts; changed votes or visibility require restarting with no
cursor (`invalid_cursor`), while new inserts retain the original revision ceiling.
Python independently verifies non-null scores; Python/TypeScript DTOs accept
legacy null rankings. Author boosts and reputation tiers are explicitly deferred.
CSS-54 implements local UI sorting/display and protected persisted vote transport;
no new service activation or configuration is added. Private current votes never
enter result caches. Exact saved retries cannot replace newer hosted state;
fresh signed own/aggregate reads establish current feedback. Report submission
stays separate and unavailable pending CSS-56 approved fields/API. Full local
backups retain exact intents; no new schema/secret or signed evidence change.

Revision-specific votes avoid inheriting praise for replaced content. Group revisions
under their contribution lineage for uploader statistics so repeated edits do not
multiply upload credit. Identical retransmissions are idempotent; cross-uploader exact
copies retain attribution but should not multiply reputation or independent agreement.

## 7. API shape and operating rules

Proposed service routes, all versioned under `/v1`:

| Route | Responsibility |
| --- | --- |
| `GET /capabilities` | Supported schemas, match/fingerprint versions, bounded limits; no private data |
| Hosted Discord start/callback and enrollment completion routes | Bound OAuth flow, membership verification, single-use handoff bound to the installation's public key |
| `POST /enrollment/challenge`, `POST /enrollment` | Discord-bound signing-key proof and idempotent registration; never anonymous fallback |
| `GET /identity`, `PATCH /identity` | Own public profile and validated display name |
| `POST /identity/keys/rotate`, `POST /identity/keys/revoke` | Authenticated key lifecycle; rotation initiated only by the user's UI action, with immediate old-key retirement |
| `POST /availability/batch` | Bounded media lookup returning counts/bands, not complete findings |
| `POST /matches` | Paginated candidate summaries for a specific target file |
| `POST /contributions` | Idempotent validated publication |
| `GET /operations/{id}` | Retry/recovery state for an authorized operation |
| `GET /contributions/{id}`, `GET /contributions/{id}/artifact` | Summary and authorized immutable payload with receipt |
| `POST /contributions/{id}/imports` | Idempotent acknowledgment of local import |
| `POST /contributions/{revisionId}/vote`, `GET /contributions/{revisionId}/vote` | CSS-52 signed retry-safe vote mutation and own current vote/eligibility |
| `GET /contributions/{revisionId}/votes` | CSS-52 signed public aggregate read; no individual voter history |
| `POST /contributions/{revisionId}/reports` | CSS-56 signed private import-eligible structured report, independently retry-safe; no owner withdrawal route |

October 3 CSS-41 implements local-only signed `POST /v1/availability/batch` and
`POST /v1/matches`, with explicit `LOOKUP_LIMITS`, per-channel counts, null
missing-identity outcomes, honest truncated lower bounds, bounded compatibility
keyset cursors and fresh authority/moderation checks on metadata cache hits.
DTOs, error/offline fixtures and local latency/cost evidence are recorded in the
[CSS-41 handoff](community-scan-service-tasks/decision-evidence/41-catalog-lookup-contract.md).
CSS-53 extends quality ordering/cursors and the local/browser DTOs; production
limits, deployment and visible feedback/sorting UI remain separate.

Operator actions use separate authorization; no special contributor name grants admin.
The user requested a small hosted dashboard alongside CLI tooling. Add a separate
`/admin/v1` contract for moderation, identities, budgets, audit, and backup status;
reuse the same application services from the dashboard and CLI. Local Editarr endpoints
adapt contributor operations to existing local auth, source resolution, and reviews.

Confirmed D17: Cloudflare Access protects the operator UI and admin API only. Validate
its signed token, issuer, audience, expiry, and allowed operator identity in the Node service;
a header containing an email is not proof. Refuse alternate hosts/direct untrusted
origin access, and keep legacy Worker/preview deployments disabled. Include CSRF/origin checks for operator
mutations and keep contributor signing credentials incapable of admin access.
[Cloudflare Access JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)

Serve dashboard static assets through the same deployment with routing that preserves
authentication checks on protected paths. The Node handler serves bundled assets
only after Access verification; no unauthenticated static server or asset/SPA fallback
may bypass protection or swallow an admin API error. Keep the equivalent Worker-first
routing in the regression harness.

The dashboard must never receive database files, signing secrets or deployment credentials.
CSS-55 implements this boundary locally: configured `ACCESS_ISSUER`,
`ACCESS_AUDIENCE`, `OPERATOR_ORIGIN` and exact verified `OPERATOR_SUBJECTS`;
no default allowlist or alternate origin. `GET /admin/v1/session` returns only
`{version:1,subject}` behind Access JWT verification. Every unsafe admin method
also requires exact Origin and `X-Community-Operator: 1`, plus same-origin browser
fetch context when supplied. No CORS grant or CLI exception exists. Assets are
protected before serving, with no SPA/API fallback and private/no-store responses.
The separate operator build never imports the local Editarr bootstrap/session API.
Read the [CSS-55 middleware/build/configuration handoff](community-scan-service-tasks/55-operator-access.md).
CSS-59 implements explicitly approved human cloudflared Access CLI authentication
locally/mock: `Cf-Access-Token` is forwarded by Access as the existing verified
assertion; exact human allowlisting and mutation headers remain unchanged. Private
UUID/body journals support exact retry and bounded list pages support explicit
cursor resumption, not a consistent backup. No machine/deployment/contributor
authority or CSRF exception is introduced. Live Access/session setup remains
independently gated. See the [CSS-59 commands and adapter handoff](community-scan-service-tasks/59-operator-cli.md). October 4
CSS-56 implements the approved local report/moderation policy behind this boundary:
reports require an acknowledged exact-revision import, current member/active key,
non-self author and spam/inaccurate/privacy/rights/other reason, optional private
plain-text note <=1,000 characters. Signed report mutation accepts
`{version:1,reason,note?}` and returns a saved completed operation with
`{version:1,operationId,reportId,revisionId,state:"open"}`. One open report per
contributor/revision; report counts never automatically quarantine content.

Operator `POST /admin/v1/moderation` accepts
`{version:1,actionId,command,targetId,revisionId?,expectedRevision,reason}`.
Commands are quarantine/restore/suspend/unsuspend/exclude_vote/restore_vote/
resolve_report; revisionId is supplied only for votes. Every valid attempt records
verified subject, resource, reason and applied/conflict/denied outcome. Successful
state changes and immutable audit insertion commit atomically; exact actor-bound
retries return their historical result. Stale resource revisions return 409.
Protected `GET /admin/v1/moderation/{kind}/{targetId}` (vote adds /{revisionId})
returns snapshot state/version and at most 20 audit entries. Open report queue
`GET /admin/v1/reports?after={reportId}` returns at most 20 rows and nextCursor.
Text remains escaped plain text, never trusted HTML; no individual library history,
Discord identifier, deployment secret or raw artifact enters these read DTOs.

Suspension hides authored revisions and blocks contributor community access;
unsuspension never changes keys or overrides quarantine. Explicit vote exclusions
preserve source rows and historical authorship while recalculating affected totals;
restoration requires eligible counted non-self imports and current service state.
Suspension/nonmembership alone continue to preserve vote totals. A catalog epoch
invalidates cached visibility and old cursors across isolates; current authority
and author-state fences still run on cached reads. Local downloaded evidence remains.
`REPORT_LIMITS` and `MODERATION_LIMITS` require explicit positive bounded JSON,
without production defaults. Numbered SQLite migration 0014 stores report/audit/exclusion/
version/epoch/admission/usage evidence and must be included in future full backups.
See the [CSS-56 DTO, limits and recovery handoff](community-scan-service-tasks/56-moderation-service.md).
Report UI/local adapter, hosted backup drills and deployment remain downstream.

Use explicit offline maintenance for community SQLite backups; do not build or expose
an entire service backup inside an ordinary API handler. Audit operator subject, reason, resource, and outcome;
operator audit data is separate from public contributor profiles.

Begin feasibility testing with 50 media items per availability request and bounded
response pages; this is a benchmark input, not an approved production limit. Query
distinct indexed identities in bounded statements below SQL parameter/query limits;
avoid N+1 requests. Return per-item outcomes, stable cursors, and deterministic sorting.

Cache by work/release identity, fingerprint/schema/matching version, and relevant policy
version. Use shorter negative-result lifetimes, refresh-on-demand, and bounded backoff
with jitter and `Retry-After`. Optional whole-library background checking is a separate
opt-in scope decision; a user clicking Find Filters need not disclose an entire library.

Use machine-readable errors for enrollment, service failures, clock skew, revoked key,
unsupported schema, invalid signature, incompatible media, conflict, permission refusal, and
temporary storage failure. Log request IDs and safe reason codes, never private keys,
authorization material, complete library queries, absolute paths, or raw artifacts.

Expose public contribution attribution and aggregate statistics, not individual users'
download/vote histories. Responses containing the current identity's vote or import
state are private. October 8 G-RETENTION selects community-retain-first-1: signed
evidence/lineage, historical public keys, durable operations, compact import
deduplication receipts, votes, reports and audits are retained indefinitely.
Existing transient authority/nonce/membership/name-event/broker-observation expiry
and log rotation stay unchanged; no new service-backup pruning or schedule. Library
lookup requests must not become a permanent library inventory. Retained receipts
and audit growth consume SQLite/disk/WAL space; payload bytes are not total disk
usage. CSS-68 remains canceled, not capacity certification.

Any worker cache must run authorization/moderation checks before serving protected
content, with epoch invalidation and final visibility fences on operator quarantine
or contributor suspension. Moderation never revokes reuse permission or deletes
already downloaded evidence. An unauthenticated artifact URL would bypass the
cost/authentication policy and is not part of this design. Do not conflate CDN hits,
HTTP transfers, download receipts, and reputation.

Cache immutable artifact bytes separately from identity-specific response data. Issue
fresh contributor-bound receipts after authorization; never serve another install's
receipt or private response headers from a shared cached response. Preserve digest semantics
across cache compression/transport behavior and verify them in integration tests.
