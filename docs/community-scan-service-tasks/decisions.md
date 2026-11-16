# Decision gates and user questions

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Current deployment decision, October 5, 2026: **Docker on a Mac mini, Node + SQLite**,
including exact signed artifact BLOBs. The user requested this refactor after the
self-hosting/security discussion. See the [runtime and recovery contract](../community-service-docker.md).
It supersedes the [October 3 D1 deployment scope](d1-storage-scope.md), not data,
auth or retention policy; usage admission is retired. No existing database transfer or public launch is approved.

Status: **Unresolved gates are not permission to choose defaults.**

The user said: “Don't make any major decisions yourself. If there's anything left
that's ambiguous or unclear, ask.” This registry identifies exactly what to ask and
which code depends on it. Confirmed D01–D25 remain in the [main plan](../community-scan-service-plan.md).

Answers received during preparation are recorded below. The membership recheck method
is deferred to CSS-70's technical comparison; a recommendation is not accepted by silence.
License was deferred during preparation; G-LICENSE now records the October 3 standing
reuse decision. Launch remains deferred; do not infer production activation.

## Recording an answer

Append date, the user's exact answer or faithful summary, and affected task IDs to
the decision log. Update the corresponding parent-plan section and briefs. A design
gate blocks dependent behavior/acceptance. Configuration/launch gates may permit pure
mechanisms and fake-data tests, but cannot be hidden by a production default.
A task may finish an approved mechanism with a remaining deployment-only gate only
when its handoff explicitly states the unverified operational step.

**October 4 CSS-50 contract answer:** The user answered "Yes" to exposing the
existing root `contributionId` in authenticated matches and updating hosted queries,
strict local/frontend DTOs and fixtures. It is distinct from release/revision IDs;
downloads revalidate the authenticated root rather than trusting a browser assertion.
This resolves the missing catalog/import dependency, not G-LIMITS, reputation policy
or launch activation. See the [CSS-50 handoff](50-find-filters-downloads.md).

## G-KEYS

**Question asked:** one shared signing key restored across installations, or separate
keys per installation under one Discord contributor?
**Status:** Confirmed September 26: separate keys per installation; revoke/rotate each
independently. Contributor reputation and votes remain shared (D21).
**Why:** Determines D1 active-key constraints and the scope of rotation/revocation.
**Applies to:** CSS-23, CSS-28, CSS-31, CSS-33; this gate is resolved.
**Already fixed:** Contributor reputation/votes are shared by Discord account;
keys stay portable and rotation is manual with immediate retirement of affected keys.

**October 1 CSS-28 answers:** User approved a backend-held confirmation secret
bound before OAuth to the installation/key, with local administrator confirmation
before the backend signs completion. The hosted page retains explicit browser
confirmation. Local administrator credentials are never sent to the Worker.
CSS-28 defines/enforces the hosted contract; CSS-29/30 implement local secret
storage, administrator/CSRF authorization and UI confirmation.
User approved bounded same-grant/installation/active-key signed retry reconciliation,
without duplicating operations or replacing keys. The existing ten-minute grant
expiry bounds retries; retired/revoked keys cannot reconcile.

**October 1 profile answer:** "Use 3 word names. Names need to be unique in
current usage." The user also requested reflecting updated names client side and
subsequently chose
case-insensitive normalized uniqueness. Curated three-word generated names and
custom names use the proposed trimmed/NFC 3-40 Unicode code-point bounds and reject
control/format characters. Current-name keys use NFC, Unicode lowercase, then NFC;
this is not full Unicode confusable matching. The October 7 local-service decision
removes hourly name-change allowances. Identical-name updates and idempotent
retries do not change the profile revision. Old names become available immediately.
Every successful enrollment/identity/rename response returns the current name and
monotonic `profileRevision`; CSS-29/30 must replace cached labels and refresh signed
identity on connection/profile views. No name serves as an identity or authority key.
These explicit answers supersede the earlier nonunique-name proposal in contracts.

## G-RECOVERY

**Question asked:** permit fresh Discord authorization plus explicit UI recovery to
replace lost keys immediately, or require an encrypted key backup?
**Status:** Confirmed September 26: allow explicit Discord-assisted recovery (D24).
Fresh authorization plus UI confirmation replaces the affected installation's key and
retires the lost key immediately; other installations' keys remain active.
**Applies to:** CSS-33; this gate is resolved. Lost Discord access/account transfer
has no approved bypass; present that case as unsupported rather than inventing one.
**Do not infer:** Normal login or creating another installation authorizes replacement.

## G-MEMBERSHIP

**Questions asked:** bot REST rechecks versus retained encrypted OAuth refresh tokens
(or comparison first); 15-minute or 1-hour freshness with access paused when an expired
check cannot refresh (or estimates first).
**Status:** D22 confirms recheck on use after 15 minutes; pause all community access
if the expired check cannot refresh. Local use remains available. The user requested
a technical comparison first: [CSS-70](70-membership-method-comparison.md) prepares it
and asks for the method selection before CSS-27 implements either approach.
**Rejoin answer:** Restore access automatically after successful membership verification,
provided Editarr operators have not suspended the contributor.
**Method gate:** Resolved October 1 after CSS-70 comparison.
**October 1 evidence:** [CSS-70 comparison](decision-evidence/70-membership-comparison.md)
is complete with current official sources, secret inventory and request estimates.
The user initially skipped selection, then explicitly answered "Okay, then let's
go with the bot plan." Single-member bot REST is selected: one Worker bot secret,
no retained user OAuth tokens, Gateway process or bulk member listing. CSS-27
implements local/mock checks; minimal-permission live verification remains OPS-01.
**Evidence to prepare:** endpoint/permission requirements, provider token retention,
request cost, maximum removal delay, outage examples. No always-on Gateway is assumed.
Discord documents a single-member endpoint separately from the privileged member-list
endpoint: [Get Guild Member](https://docs.discord.com/developers/resources/guild#get-guild-member).
Validate the chosen strategy with the configured app before rollout.

## G-ADMISSION

**Question asked:** membership alone, completed rules screening, or an approval role?
**Status:** Confirmed September 26: membership alone, with no additional service
screening or approval-role gate. This eligibility decision is resolved.
**September 30 setup:** Discord guild `1554889314549243986` exists. Invitations
are currently public, with controlled invitations possible later. The `owner`
and `moderator` roles handle moderation and support for now. The user wants
to leave open a future `contributor` role requirement for uploads only; this
does not change today's membership-only eligibility or authorize enforcing a
role gate yet. Application setup, exact invite publication, and any future
upload-role policy remain separate choices.
**Applies to:** CSS-27 and production community setup; do not add an extra role rule now.

## G-ENCODING

**Current storage answer, October 3, 2026:** JSON remains selected. Keep summaries
as indexed columns plus uncompressed JSON text; keep exact gzip artifact bytes,
original manifests and detached signatures as D1 BLOBs. This supersedes the earlier
R2 destination without changing encoding or signed-byte contracts.

**Question after CSS-10:** which measured JSON/Protobuf and compression option should
be used for artifacts and D1 serialized summaries?
**Status:** Resolved September 26, 2026. After reviewing the CSS-10 benchmark
and the distinction between D1 summaries and R2 bodies, the user chose to
keep JSON. Store serialized D1 summaries as uncompressed JSON text alongside
separate indexed searchable columns; store immutable private R2 artifact
bodies as gzip-compressed JSON. Protobuf remains experimental only.
**Applies to:** CSS-11/13 and the production artifact format. This selection
does not approve the test-only 64 KiB compressed / 512 KiB expanded / 2,500
event bound, a signing byte profile, or a production CPU claim. Exact-byte
manifest/signature binding and validated size limits remain CSS-11/13 work;
G-LIMITS and the free-plan CPU/scope choice remain open.
**Evidence:** [CSS-10 benchmark](decision-evidence/10-encoding-benchmark.md)
compares bytes, local CPU/memory, queryability, schema evolution and tooling.

## G-CONTENT

**Question asked:** permit partial and older unknown-provenance scans with clear labels,
older complete only, or complete recorded-provenance scans only?
**Status:** D23 confirms allowing both partial scans and older unknown-provenance scans
with explicit coverage/unknown labels. This part is resolved.
**Notes answer:** Allow bounded free-text event notes/comments shown in the sharing
preview. Treat them as untrusted public text, include only explicitly previewed notes,
and do not include complete transcripts/internal debug output.
**Applies to:** CSS-11/37/39/46/62; this content-policy gate is resolved. Exact resource
limits remain under G-LIMITS and license consent under G-LICENSE.
AI/manual/derived types and technical source attribution are already confirmed.

**October 3 CSS-37 answer:** The user explicitly selected **Approve Artifact V2**
for the donation builder, preserving strict v1 support and the existing detached
signature format. `community-artifact-2` / `community-summary-2` add portable
matching/audio evidence, immutable local source snapshots, revisions and
already-applied retiming. `community-manifest-1` supports both artifact versions
and binds the actual version; `community-artifact-signature-1` is unchanged.
See the [CSS-37 contract](decision-evidence/37-donation-artifact-contract.md).
This is not approval of production limits, a sharing license, publication routes,
database migrations, deployment or access to personal media.

## G-MATCHING

**Questions after representative media tests:** accept the sampled SHA-256 layout,
runtime tolerances, episode/audio conflict rules and explicit compatibility examples?
**Status:** Typed IDs and compatibility-first bands are confirmed. On October 2,
2026, the user approved the three CSS-35 benchmark layouts on synthetic temporary
files only, then selected nine evenly spaced 1 MiB samples with size/offset/length
binding, fixed big-endian framing, overlap/adjacency merging and optional separate
full SHA-256. The user explicitly acknowledged representative media/storage
measurements remain unavailable. See [CSS-35 evidence](decision-evidence/35-file-fingerprint-contract.md).
**Compatibility confirmed October 2, 2026 by user:** Inclusive fixed 2.0-second
file-probe duration tolerance, with no percentage allowance or promotion from
rounded catalog runtime. The user approved the conservative ladder `exact_file`,
`strong_fingerprint`, `release_match`, `possible_cut`, `title_only`,
`incompatible`; known work/edition/episode/span/timing conflicts override
digest/name evidence. Audio language/role conflicts block audio, not otherwise
compatible video. Unknown selected-track metadata requires audio review.
Missing/unlinked work identity requires confirmation; alternative numbering
without a common scheme stays uncertain. All candidates start unselected;
exact/strong eligibility is for a later explicit selection action, never automatic
download/apply. This is approved policy, not measured real-media accuracy.

**Stream/track scope confirmed October 2, 2026 by user:** Add separate sampled
compressed-video and compressed-audio fingerprints alongside the unchanged
CSS-35 container protocol. Unchanged streams/remux normalization are the initial
scope; re-encode-tolerant perceptual matching is deferred. Stream sampling,
packet/codec normalization and timing alignment require measured proposals and
explicit format approval; the file-byte nine-position layout is not silently
adopted for demuxed streams. See [CSS-35A](35a-audio-track-provenance.md),
[CSS-35B](35b-stream-fingerprints.md), [CSS-35C](35c-audio-track-selection-ui.md).

The user explicitly selected these rules in follow-up questions:

- Preselect the single available audio track or an unambiguous flagged default;
	otherwise require selection. Show the selection before starting. Never silently
	substitute a missing track or choose between ambiguous default flags.
- Remember selection only for that physical file/version, revalidating file state
	and stream metadata; no global language preference or cross-file stream index.
- Retain audio language and description/title as metadata identifiers. Description
	differences alone do not block strong matching; language/role conflicts still
	apply and labels alone never prove audio content equality.
- An AI audio scan analyzes one selected track. Its immutable actual analyzed
	track is the only individual audio track used for community submission; do not
	upload the file's alternate audio-track inventory or relabel old findings.
- A derived review with multiple source audio tracks requires choosing one for
	the contribution and excluding other-track audio findings, visibly in preview.
	Preserve video findings and the saved review; no automatic splitting/mutation.
- Historical/imported audio with unknown analyzed-track identity may still be
	shared with explicit unknown-track labeling and required review. Do not guess
	a track, fabricate a stream fingerprint or promote unknown audio to strong.

**Stream format approved October 2, 2026 by user:** The generated-only bounded
experiment envelope was explicitly approved, followed by the measured three
250 ms timeline-window, codec-aware AAC-LC/H.264-AVCC v1 proposal with distinct
audio/video SHA-256 domains and fixed big-endian configuration/packet framing.
Local indexes/container size/timing are excluded from digest bytes; measured
timing remains separate. Unsupported/missing indexes, cancellation/mutation and
budget exhaustion fail without a full-stream fallback. Delayed/re-encoded cases
do not establish matching, alignment or perceptual support. Read the
[approved contract and synthetic measurement limits](decision-evidence/35-track-contract.md).
Actual-read/seek/packet/payload limits are enforced; elapsed checks are cooperative,
not a preemptive OS-read/native-demux or hard CPU timeout.

**CSS-36 timing answer, October 2, 2026:** User selected **Approve Exact Timing**
after the explicit proposal in this session. Strong independent stream matching
requires equal supported stream samples and identical known playback-relative
start and duration, compared as exact rational seconds. Subtract the known
container playback origin; equivalent timebases are not conflicts. Add no
alignment tolerance, offset/stretch inference or automatic retiming. Missing
timing stays review-required; known stream timing differences block only that
channel. The existing inclusive 2.0-second measured file-duration rule remains.
Audio additionally requires the candidate's actual analyzed-track provenance,
the target's selected or analyzed track, and known language/role. Unknown or merely
requested candidate audio cannot become strong through labels or file/video hashes.
Descriptions are supporting metadata, not equality proof or conflicts. Different
sampled streams remain uncertain, not inherently incompatible; equal container
bytes can contain different selected tracks. All results remain unselected.

**Blocks:** CSS-36's stream compatibility/timing gate is resolved. Representative
media/storage accuracy remains unverified; no fuzzy match becomes automatic
permission to apply filters. See the [completed policy handoff](decision-evidence/36-compatibility-core.md).

**Other Files confirmed October 2, 2026 by user:** Community uploads from Other
Files require a TMDB or TVDB URL/ID identifying the associated movie, series or
episode. Normalize supported URLs to typed provider IDs; episodes may use a
typed series association plus explicit episode numbering. The user further
confirmed that IMDb is not a supported provider: use only TMDB/TVDB throughout
normalized identity and manual input. The contributor's explicit association
is sufficient: do not require proof that the local file belongs to that work,
provider-account ownership or another identity-verification mechanism. Association
is user-asserted, not independently verified; trust is reputation-based and
recipients can compare and confirm against their own media before applying it.
This does not require community metadata for ordinary local scanning, or remove
signature/authentication, artifact safety, release compatibility or recipient
confirmation checks. Applies to CSS-37/46 upload preparation and sharing UI;
CSS-34's follow-up removes IMDb from typed identity and manual input; supported
URL parsing remains CSS-37/46 follow-up work.

## G-BACKUP

**Confirmed September 27:** encryption is optional and off by default for both full
and settings/identity-only backups, including keys/credentials. Plain exports/restores
need no passphrase; encrypted mode requires one. This supersedes mandatory encryption.
**Confirmed September 27 by user:** Approves the CSS-15 backup suggestions after
the opt-in change: use the shared bounded tar/manifest base (plain by default),
with age v1 for optional passphrase encryption, subject to safe integration and
native packaging proof. Owner-only local pre-upgrade snapshots remain separate.
The user selected 3 generations, 3 days and 5 GiB total for automatic
pre-upgrade snapshot retention. Preserve the last verified pre-migration
generation even when it exceeds age/space/count limits; if a new recoverable
snapshot cannot be created within available storage, stop the upgrade rather
than delete the only verified recovery copy.
**Clarified September 27 by user:** Support Raspberry Pi 3 restore, in place of
the earlier 2 GB/2-core target; there is no physical Pi to benchmark. The Pi 3
Model B has 1 GB RAM and a 1.2 GHz quad-core CPU. Retain the earlier 10-second
passphrase-unlock goal as a target, not a verified performance promise.
**Confirmed September 27 by user:** Use age's default scrypt logN=18 for new
encrypted backups and reject restore work factors above 18 before key derivation.
This supersedes the proposed logN=15 Pi 3 profile. The scrypt array is 256 MiB;
actual Pi 3 unlock time and available application memory remain unverified. Do
not claim the 10-second goal is met without a Pi-class test.
**Confirmed September 27 by user:** Start with 256 regular-file archive members,
1 MiB manifest, 256 UTF-8 bytes per member path, 64 GiB for the SQLite database
member, 64 MiB for each other member (with the manifest's stricter cap), and
80 GiB total member content. These
are restore-validation ceilings, not limits on scans, reviews or titles; apply
them to both plain and encrypted backups. Reject duplicates, unexpected names,
links, special files and trailing data; stream into isolated staging and check
available space before activation. A backup exceeding a limit must fail clearly,
not discard data or silently downgrade encryption.
**Status:** [CSS-15 four-mode synthetic evaluation](decision-evidence/15-backup-container.md)
records plain and encrypted streams and their threat limits. The experimental
fixture limits are not the selected production ceilings. G-BACKUP format and
policy choices for CSS-16/17 are resolved; safe production parsing/passphrase
integration, actual Pi timing and native release checks remain unproved.
**Blocks:** No remaining G-BACKUP design choice blocks CSS-16/17. Their own
implementation and release validation remain outstanding.
Plain backups retain the same selected secret/identity inventory; no automatic redaction
or encryption requirement based on contents. Both modes require bounded validation and
atomic restore. A saved export passphrase is not authorized, nor is plaintext fallback
after an explicitly requested encryption operation fails.

## G-SETUP-RESTORE

**Confirmed:** The user requested a Restore from backup option on the initial setup
screen. CSS-71 reuses the existing full/settings restore workflow, with plain and
encrypted archives, before ordinary administrator setup is completed.
**Question asked:** restore the administrator credentials included in the backup
(create an administrator only if none are available), always create a new
administrator, or let the user choose during restore?
**User answer:** Use backup credentials; create an administrator only if none are included.
After activation, require a fresh sign-in with the restored credentials, or complete
administrator creation when they are absent. Malformed credential records fail
validation rather than silently becoming a credential-reset path.
**Status:** Resolved.
**Blocks:** No remaining credential policy decision blocks CSS-71.
Existing Settings restore choices are unchanged. Do not create an anonymous bypass
to general backup or application APIs to support the setup entry point.

## G-LIMITS

**Status:** Usage-admission work retired October 7, 2026. The user requested
removing usage allowances because the service runs locally in Docker, not in the cloud.

The October 7, 2026 local Docker decision supersedes usage-admission policy.
There are no user daily/hourly allowances, IP/shared buckets, aggregate payload
caps or recorded-usage pauses. Usage and byte observations are passive. Keep
signatures, membership, replay/idempotency, Access/CSRF, immutable evidence,
duplicate-report protection and per-operation parsing/query/page/cache bounds.
Provider-side cooldowns and Docker/physical hardware constraints still apply.


its cloud-accounting proposal are retired, with no active dependency
edges. CSS-24A retains trusted ingress and passive observations. CSS-68's
capacity/recovery audit is canceled by the October 7 user answer under G-LAUNCH;
do not implement it or reintroduce user allowances. Per-operation artifact, body,
query, page, cache and lineage validation remains required. Production hardware
capacity, ingress setup, recovery objectives and explicit launch authorization
are independent open gates, not reasons to add an admission policy.

Historical migrations retain old accounting rows and labels for data-preserving
upgrades and archive compatibility. Migrations 0015/0016 retire enforcement,
including name/report allowances; report history is preserved. No existing
database or public service is modified or activated by this decision.

## G-DOWNLOAD-RECEIPTS

**Status:** Resolved October 3, 2026 during CSS-42. The required service-authenticated
receipt had no existing byte/signing helper. The user explicitly selected
"Approve the proposed Ed25519 receipt": a separate service key, domain-separated
contributor/revision/digest-bound stateless receipt with no expiry and retained
trusted public-key history. Issuance identities remain stable across retries and
service-key changes; each download receives its own contributor-bound issuance.
Downloads do not count imports or votes. CSS-51 owns later signed idempotent
acknowledgment; G-REPUTATION is not resolved by this format choice.
**Applies to:** CSS-42/44/51/60/61. Only synthetic keys/local tests were authorized;
live secrets, production limits and deployment remain separate.
See the [receipt contract and inventory](decision-evidence/42-artifact-download-contract.md).

**October 3 CSS-44 trust-source answer:** The user selected "Fetch from service is
fine. We're not looking for super high integrity." The configured HTTPS origin
authenticates a dedicated receipt public-key-history response; no independently
pinned keyring or additional root-signing hierarchy is required. Refresh before
each import and fail closed when refresh fails; do not establish trust from the
artifact download's receipt or fall back to stale key status. Preserve historical
verification evidence with imported bytes. Ordinary signed membership/service gates
apply to the key-history read. Retired keys remain historically valid; revoked
keys fail. No deployment, live provisioning or counting policy is approved here.

## G-REPUTATION

**Question before CSS-51–54:** accept unique-successful-import counting, vote eligibility,
a proposed formula/prior/capped uploader boost, contributor tier thresholds and any decay?
**Status:** CSS-51 counting, CSS-52 voting and CSS-53 revision-only ranking
resolved October 4. Author boosts and author reputation tiers explicitly
deferred by the user.
**October 4 CSS-51 answer:** User approved one acknowledged, committed local import
per contributor and immutable revision across installations, restores, retries and
channel selections. Author self-imports add zero displayed popularity; two other
contributors add two, and a new revision has its own count. Retain durable deduplication
facts pending CSS-64 retention policy. Counts are statistics, never trust points.
Implement local/mock mechanisms with explicit test limits and production disabled.
**October 4 CSS-52 answer:** User approved voting only after an acknowledged
successful import of that exact immutable revision, current Discord membership
and no self-votes. Upload-restricted contributors may vote; suspended contributors
and nonmembers cannot read their private vote or change it. Retain existing votes
and totals during access loss; verified return does not remove or recreate them.
No reason codes for now; like/dislike/neutral only, with abuse reports separate.
One mutable contributor/revision vote spans installations, credential rotation,
restores and retries; new revisions and derivatives inherit no votes. This does
not select tier definitions, voting-specific rate caps or a ranking formula.
**October 4 CSS-53 answer:** User approved Wilson lower-bound revision quality,
z=1.96 with one positive and one negative prior vote, strictly after video and
audio compatibility bands. Actual neutral votes contribute no rating evidence;
equal scores use immutable ASCII revision IDs. Worked scores approved: 0/0
0.0945, 1/0 0.2077, 10/0 0.6461, 100/0 0.9465, 0/10 0.0149, 5/5 0.2538.
No age decay; retained votes follow CSS-52 access-loss policy and authorized
reversals recalculate quality. After requesting an explanation of author boosts
and reputation tiers, user answered "Yes" to the revision-only approach: zero author
boost and unrestricted local usage, with both optional mechanisms deferred. Upload/import
volume never enters quality. `community-ranking-wilson-1` rounds scores to nine
decimal places for stable ordering; production activation remains separate.
**Blocks:** No remaining formula gate for CSS-53's approved local/mock scope.
Future author boosts/tier thresholds and qualifying independent evidence
values require new explicit approval; they cannot be inferred from this decision.

**October 4 CSS-54 implementation:** Applies the existing CSS-51/52/53 answers
unchanged: private current reads, persisted exact like/dislike/neutral retries and
compatibility-first quality/count displays. No new policy selection. Report
submission/fields remain explicitly deferred to CSS-56; the UI command is disabled,
not a reason picker or a dislike-to-report conversion. No author trust or tiers.

## G-LICENSE

**Question when ready for public contributions:** which sharing license, consent rules,
owner withdrawal rights, derivative treatment and retained attribution should apply?
**Status:** Resolved October 3, 2026 for CSS-46. The user requested a free/open
community with no per-contribution license choice/approval or author revocation,
and explicitly chose to retain prose under standing community reuse terms.
Contributions, including public event notes, may be copied, shared and modified;
preserve original attribution and identify derivatives/changes. Require rights-to-share
and exact-preview confirmation for each donation, not a claim of ownership over media.
Imported community evidence must retain its original terms and attribution; unknown
or incompatible permissions cannot silently become new reuse permission.
There is no author withdrawal beyond operator moderation. This supersedes the
previous license-picker/deferred-policy requirement; no Creative Commons license
or noncommercial restriction was selected. The versioned standing rule is a service
reuse permission, not a representation that all contributed prose is uncopyrightable.
**Blocks:** No remaining product-policy gate blocks CSS-46's manual flow. Production
limits, deployment and live publication still require their separate approvals.
CSS-63 owns service-wide enforcement/moderation policy, not a new author revocation UI.

**October 4 CSS-63 evidence:** New and pending publications require signed
`community-reuse-1`, compatible exact parent evidence and original source author
attribution. This is an authenticated rights assertion, not legal ownership proof.
Completed historical retries and generic historical verification preserve their
original claims without relicensing. Operator quarantine/suspension affects discovery
and downloads only; signed bytes, attribution and local downloaded evidence remain.
No author withdrawal, destructive retention, new license or schema was introduced.
See the [CSS-63 local/mock verification and state machine](63-sharing-policy-withdrawal.md).

**October 4 CSS-62 answer:** The user explicitly selected "Defer automatic sharing".
CSS-62 is DONE by optional-feature deferral, not implemented automation. Manual
sharing remains unchanged; connection and manual consent do not authorize automatic
preparation, scheduling, retries, backfill or uploads. In the same answers the user
selected prepare-only with individual preview approval before upload, and holding
pending intents until explicit resumption. Preserve those constraints if the feature
is reconsidered, but require renewed scope approval before implementing it. CSS-66/67
must treat automatic donation as deferred and verify/document the manual flow only.

## G-OPERATIONS

**October 7 CSS-67 user answer:** Record unavailable native/Intel target and
target-Mac cold-boot/FileVault/Docker/tunnel checks as **open release gates**.
No reboot, live tunnel change, native/platform certification, scope exclusion or
activation is authorized. Local packaging evidence and exact gate owners are in
the [CSS-67 matrix](decision-evidence/67-packaging-matrix.md). The user also approved
repairing its confirmed Docker build-context allowlist flaw; no new policy followed.

**October 5 direction / October 6 planning follow-through:** Docker packaging does
not authorize external setup or activation. OPS-01/02 own real Discord callback/bot
checks and Tunnel/Access configuration, exact Host/IP isolation, browser/CLI session
revocation and JWKS behavior. CSS-67 owns supported image architectures and target-Mac
FileVault/Docker/tunnel cold boot. Existing hostname choices remain recorded in
[operator setup](operator-setup.md); private values, provider actions, operating
constraints and public activation still require explicit authorization.

**Questions before live setup:** account/project/domain, operator allowlist, Access
configuration, administrative CLI authentication and report-handling expectations?
**Status:** Hosting/Access/dashboard chosen; deployment values and operating policy open.
**Blocks:** Live CSS-55/59/68 setup; local mocked code need not wait for account creation.
Ask for only necessary configuration through appropriate secret channels; never paste
credentials into task handoffs. Creating a Discord server/application remains a to-do.

**October 4 CSS-55 evidence:** Local/mock Access verification, exact signed-subject
allowlist, Worker-first protected assets and separate operator frontend are complete.
No live configuration was selected: `OPERATOR_ORIGIN`, `ACCESS_ISSUER`,
`ACCESS_AUDIENCE` and `OPERATOR_SUBJECTS` have no production defaults. The chosen
hosting/Access/dashboard mechanism permits this work; it does not resolve operator
identities, Access policy, CLI authentication, report/moderation policy or deployment.
See the [CSS-55 handoff](55-operator-access.md); OPS-02 remains NOT STARTED.

**October 4 CSS-56 user answers:** Reports require an acknowledged import of the
exact revision, current active membership/key and no self-report. Approved proposed
fields are revision, spam/inaccurate/privacy/rights/other reason and optional
plain-text note up to 1,000 characters; one open report per contributor/revision,
private to operators, idempotent retry, no automatic quarantine. Limits require
explicit configuration, with production still disabled. Operators may quarantine/
restore individual revisions and suspend/unsuspend contributors. Suspension hides
all authored revisions and blocks contributor reads/mutations; restoration cannot
override another restriction. Every action requires an expected resource revision
and nonempty operator reason. Abusive votes use explicit per-vote exclusion and
restoration while retaining source rows and audit evidence; suspension alone never
changes vote totals. Restoration checks counted non-self import and current service
eligibility; authorship, keys, imports, artifact bytes and local downloads survive.
Report resolution records operator handling without an automatic content action.
No retention, live setup, CLI exception, launch or owner withdrawal is approved.

**October 4 CSS-58 user answer:** The user selected **Defer separate pause controls**
when asked whether to defer separate enrollment/publishing pauses or prepare an
audited policy proposal. CSS-58 completes already-approved moderation and operator
report review only. No per-feature pause service/schema/control or direct runtime-configuration
write is authorized; the protected runtime configuration remains explicit; usage-pressure admission is retired. This
explicit deferral also constrains CSS-59 commands until the user reopens the policy.
CLI authentication, live Access and deployment remain separate unresolved gates.
See the [CSS-58 workflows and evidence](58-operator-dashboard-actions.md).

**October 4 CSS-59 user answer:** The user selected **Human Cloudflare Access
login**. The CLI retrieves the application token through cloudflared and uses the
existing exact human subject allowlist, permissions and audit identity. It sends
the existing mutation headers without a CSRF exception. No machine/service-token
authority, deployment credential or contributor credential is approved. Tokens
must not enter command arguments, output, repository files or action checkpoints.
Live Access/domain/session configuration and deployment remain OPS-02 gates.

## G-SERVICE-BACKUP

**October 7 CSS-67 user answer:** Select **fresh deployment; no legacy import**.
Existing D1 stores/CSS-60/61 archive import is not applicable to this selected scope.
Do not invent a transfer or stamp an old schema. Discovering actual legacy data later
requires a newly approved source/export, destination, downtime and cutover procedure.
Disposable local snapshot/upgrade/restore tests do not authorize existing-data changes
or certify independent/off-device recovery. CSS-68 is canceled; CSS-69 records
the launch disposition of this unverified risk.

**Current Docker boundary, October 5:** Use explicit offline maintenance and verified
private SQLite snapshots, with the API unable to mount backups and maintenance unable
to read service secrets or listen. Snapshot files are plaintext; configuration and
signing secrets/history need separate protection. No schedule, pruning, paid/off-site
destination or public restore is selected. CSS-68's drill is canceled. Any later
independent/off-device drill needs renewed authorization plus destination,
protection/key custody, downtime and recovery-objective
decisions. Preserve original volumes and stale security/operation history before
separately approved activation. CSS-67 owns any required, explicitly approved legacy
D1/archive conversion; unknown schemas are never adopted or stamped.

The October 4 answers and DONE evidence below describe legacy D1 tooling, not
completion of Docker independent recovery or a requirement to use provider export
for the new runtime.

**Question before CSS-60:** independent backup destination, encryption/secret protection,
schedule/retention, and acceptable publication freeze versus watermark complexity?
**October 4 CSS-60 user answers:** Select the full provider D1 export with its
fixed polling bookmark. After reviewing Cloudflare's documentation, explicitly
accept its temporary database-wide query lock; this is not a nonblocking backup.
Do not add application freeze controls. Approve a separate operator-local,
protected provider credential with minimum applicable permissions, never sent to
the Worker/dashboard or persisted in checkpoints. Existing admin authority stays
human Access. Approve an explicitly selected private directory outside the
checkout and Cloudflare, age recipient encryption with the decryption key held
separately, and protected local secret inputs in a separately encrypted bundle.
Machine-local copies are labeled as such; off-device replication is an operator
responsibility. Manual invocation only, no schedule or automatic deletion,
paid storage, real credential use, deployment or existing-database migration.
Local/mock verification only. Completion requires isolated restore validation,
not merely a successful provider export. Provider cancellation must never resume
by silently starting a new snapshot generation.
**Status:** Policy selected; [CSS-60](60-hosted-backup.md) is DONE locally/mock.
Its full SQL/tar/age format, fixed-bookmark protocol and isolated validation are
implemented. Local actual Wrangler export and encrypted size evidence exist;
provider D1 billing, live credentials/locks/download hosts, matched deployment
generation and off-device replication remain unverified. No paid service is selected.
**Remaining gates:** Live provider setup/export and CSS-61 hosted restore/cutover
require separate authorization and acceptance; mock completion grants neither.
**October 4 CSS-61 user answer:** Approve local/mock restore into a new private
directory, including matched encrypted archive validation, derived counter
reconciliation, safe resume and a disabled-environment report. No live Cloudflare
resources, existing-database writes, callbacks, deployment or production cutover.
Live hosted acceptance remains separate; restoration never activates the service.
**CSS-61 outcome:** DONE locally/mock. Matched archive decryption, isolated known
schema/signed-byte validation, source-preserving counter reconciliation, private
resume and usage-provenance drill passed. No live resource or cutover acceptance.

## G-RETENTION

**Question before CSS-64:** accept the proposed retention matrix for receipts/dedup facts,
votes, reports/audit, OAuth state, historical keys and local/hosted backups?
**October 8 user answer:** Select "Reopen CSS-64 retention". This authorizes
review/policy selection and subsequent approved implementation, not a guessed
retention matrix, durable-data deletion, backup pruning, existing-database changes
or live activation. CSS-68 stays canceled; CSS-69's local-only pilot scope is unchanged.
**October 8 policy answer:** Select **Approve retain-first**. Keep exact signed
payloads/revisions/lineage, identity/public-key history, durable operations/import
deduplication/votes/reports/audits indefinitely. Preserve existing transient
expiry/observation cleanup and Docker log rotation; unused expired browser recovery
authority is transient, but committed recovery proof and pending operations survive.
No new service backup pruning/schedule; CSS-65's local-app successful-backup count
policy stays unchanged. Implement/test only with disposable data, not existing
databases, credentials, resources or activation. See the [approved matrix](64-retention-cleanup.md#approved-retention-matrix).
**Status:** G-RETENTION resolved for retain-first; CSS-64 DONE locally/disposable
October 8. The October 6 deferral is historical. Age-based durable deletion needs
renewed explicit approval and preserving migration/recovery evidence.
**Blocks:** Destructive pruning/public privacy policy. Never delete proof needed for
historical signatures or uniqueness needed to prevent reputation duplication.
Existing hourly transient cleanup is the approved deletion scope; immutable
SQLite guards and backup policy remain unchanged. CSS-64 adds explicit policy,
read-only preview and durable-state/retry/timer evidence, not a deletion migration.

**CSS-64 outcome:** 570 shared tests, supported schema/read-only/recovery tests and
fresh Node 24 ARM64 packaged dry-run/sharing/cleanup/restart checks passed. Exact
signed BLOBs, keys/lineage, pending/committed operations, receipts/votes and report/
moderation inventories survived repeated cleanup and replay. Payload reclamation
is zero; measured source main/WAL/payload sizes are synthetic, not hardware capacity
or provider billing. No existing database, service backup schedule, credentials,
runtime/public ingress or CSS-68 cancellation changed; see the [handoff](64-retention-cleanup.md#october-8-completion).

## G-SCHEDULING

**Question after backup UX is reviewable:** include unattended local backup scheduling,
and if so, which schedule/destination and encryption choice? Only encrypted scheduling
needs an approved encryption key/secret-store policy; plain scheduling needs no passphrase.
**Status:** Selected October 6, 2026 for CSS-65 local-app implementation. User chose
configurable intervals in hours, one catch-up after downtime and retry at the next
interval after failure. Require an existing writable destination and a configurable
number of retained successful backups, deleting oldest scheduler-owned files only
after a new verified success. No manual-backup pruning. Interval and retained count
must be explicitly configured, with no assumed values. Full/settings content
choices remain available; scheduling defaults disabled and requires administrator
configuration/enablement. Support plain archives and encrypted archives using a
separately provisioned owner-only passphrase file, read per run and excluded from
backup contents; unavailable credentials fail closed, never downgrade. No saved
manual-export passphrase, OS wakeup/daemon, live activation or existing-database
change was approved. Verification is disposable/local only.
**Outcome:** CSS-65 is DONE locally: protected settings/API, private durable attempts,
30-second application runner, verified no-overwrite exports and owned-file retention.
205 affected backend tests, 297 frontend tests, 12 responsive browser journeys and
build/style checks passed. POSIX credential permissions are enforced; Windows ACL
validation is not implemented and encrypted scheduling fails closed there. Schedule
state and credentials are excluded from portable backups. No actual schedule enabled.
**Blocks:** CSS-65 scheduling code only. It is not required to complete manual backups.
This is the local-app scheduler. A community-service Docker backup schedule needs
separate G-SERVICE-BACKUP approval, including offline maintenance coordination.

## G-LAUNCH

**October 7 CSS-68 user answer:** "This task doesn't apply in the local Docker
scenario on Mac mini. Cancel this task and move on to the next one."
CSS-68 is CANCELED / RETIRED, has no active dependency edges, and is not a required
benchmark or recovery implementation task. Proceed with CSS-69 readiness review.
Cancellation is an explicit scope exclusion, not capacity/recovery certification
or approval to launch, activate configuration, alter retention or expose ingress.
CSS-69 must record unverified risks and obtain the remaining launch decisions.

**Question when the user is ready:** which subset should be the first release, and what
remains long-term work? What compatibility support window and rollout scope are approved?
The full plan is now reviewable; this choice does not require all coding tasks to finish.
Pilot evidence subsequently verifies readiness for the selected subset.
**October 7 CSS-69 user answers:** Select **Local-only Docker pilot**. Separately
authorize **disabled disposable staging**: build a current ARM64 image, use a unique
project with synthetic configuration and new disposable volumes, verify liveness
200/readiness and APIs 503, then remove only rehearsal resources. No existing
database, real secrets, provider traffic, runtime activation, public ingress or
CSS-68 work. Public scope/support window and future live/platform/recovery gates
remain unselected/unverified; local staging does not certify them.

**Status:** CSS-69 DONE for selected local-only pilot. Current schema-16 ARM64
image built and recorded, disabled Compose staging/image replacement passed,
temporary resources removed. Public rollout and protected runtime/ingress
activation remain unauthorized; future live/platform/recovery gates stay open.
**Blocks:** CSS-69 release decision/public rollout, not independent coding tasks.
There is no implied v1 subset, public deployment, paid upgrade or production migration.
CSS-69 must explicitly disposition Node API parity (CSS-66), live Discord/Tunnel/Access
(OPS-01/02), image/architecture/Mac cold boot and conditional legacy import (CSS-67),
and the CSS-68 cancellation with remaining unverified capacity/recovery risks.
Local review/test success is not
approval to enable runtime configuration, public ingress.

## Decision log

October 5, 2026: User requested "Then refactor to be a docker image to run on a Mac mini."
D01/D11/D16 now select Node + SQLite in Docker, preserving signed API, exact BLOBs,
identity, moderation and retention policy. No existing data transfer or
public activation was approved.

October 6, 2026: User requested that the plan and coding tasks cover the remaining
review steps and reflect the Docker direction. CSS-66/67/68/69 and OPS-01/02 now own
the Node API, live provider/ingress, image/host lifecycle, capacity, independent
recovery and conditional legacy-cutover checks. This documentation update selects
no new limits, retention, backup schedule/destination or launch scope.

October 3, 2026: During CSS-42 the user approved the proposed separate service
Ed25519 download receipt, stateless and without expiry, with stable contributor/
revision/digest identity and retained independently trusted public-key history.
Only synthetic keys/local tests would run; downloads do not change popularity.
See G-DOWNLOAD-RECEIPTS and the CSS-42 handoff. No counting formula, retention,
live key provisioning, existing-store migration or deployment was approved.

October 3, 2026: During CSS-37 the user approved artifact v2 for donation
preparation, with v1 compatibility and unchanged detached signatures. This
resolves the representation gap for local source lineage, portable analyzed-track
metadata and explicit retiming; all unrelated license, limits and launch gates
remain as recorded. See G-CONTENT above and the CSS-37 handoff.

October 2, 2026: The user confirmed Other Files community uploads require a
TMDB/TVDB URL or ID, with explicit user association sufficient and no additional
proof of media identity. Reputation and recipient comparison/confirmation remain
the trust basis. See G-MATCHING above; URL entry and upload-specific eligibility
are follow-up work, not claims about the completed CSS-34 implementation.
The user also confirmed TMDB/TVDB-only identity throughout the application;
IMDb is not an alternative provider. Normalization ignores upstream IMDb fields,
manual input rejects them, and legacy stored bytes are preserved without exposing
IMDb as a supported association.

September 26, 2026, user answers received while preparing the task pack:

- G-KEYS: separate keys per installation; revoke/rotate independently (D21).
- G-RECOVERY: explicit Discord-assisted replacement of a lost key (D24).
- G-MEMBERSHIP freshness: recheck on use after 15 minutes; pause if expired refresh fails (D22).
- G-CONTENT eligibility: allow partial and unknown-provenance legacy scans with labels (D23).
- G-ADMISSION: membership alone (clarifies D19).
- G-MEMBERSHIP method: technical comparison first (CSS-70), then ask for selection.
- G-MEMBERSHIP rejoin: restore access after successful verification unless suspended.
- G-CONTENT notes: allow bounded free-text notes shown in the sharing preview.

All direct questions asked while preparing this pack now have answers. The recheck
method remains deliberately undecided pending CSS-70. Other deferred/measurement-dependent
choices remain open exactly as described above.

September 27, 2026: D06 revised at the user's request. Encryption is optional and off
by default for full and settings/identity-only backups. CSS-15's existing age experiment
is retained as evidence for the optional encrypted path, not an approved required dependency.

September 27, 2026: The user approved G-BACKUP's initial archive ceilings: 256
regular-file members, 1 MiB manifest, 256 UTF-8 bytes per member path, 64 GiB
SQLite member, 64 MiB per other member and 80 GiB aggregate member data. CSS-15
can hand off the selected policy to CSS-16/17; Pi 3 performance and native
packaging remain unverified.
