# ADR 0002: Community sharing uses one master filter set per work version

## Status

Accepted. Supersedes the revision-based catalog, artifact v1/v2, revision
download, revision vote and revision ranking parts of the
[community service contracts](../community-scan-service-contracts.md). Identity,
enrollment, request authentication and Ed25519 manifest/envelope signing are
unchanged.

## Context

Community sharing published each scan as an immutable signed revision. Users
searched for revisions, ranked them by compatibility band and a Wilson score
over revision votes, and imported one whole revision as a local scan. Several
people covering the same release produced competing revisions instead of
reinforcing each other. A filter's quality could not be judged on its own,
and a downloaded scan could not be refreshed when others improved it.

The community service has no users yet. The local Editarr database does have
users and must keep their data.

## Decision

**Scope**

- The community service database may be wiped, and its migrations are rebased
  to a new baseline. The owner explicitly authorized this reset for the
  community service only (D1). Old clients aren't supported. The v1/v2 upload,
  search, download and revision-vote endpoints are removed (D2).
- Every contributor's signed raw submission is retained. The master set can
  always be re-derived from those submissions (D3).
- The local database gets a versioned, data-preserving migration.
  Community-imported scans stay as plain imported scans. Revision-link
  metadata is archived after a verified backup.

**Works and versions**

- A **work** is a TMDB/TVDB ID, plus season and episode for TV. A **version**
  is a release or cut of that work, and each version has one master set (D4).
- A submission requires a TMDB or TVDB ID, plus season and episode for TV. A
  query may use any subset of keys (D11).
- Version names have aliases: a curated seed list (Theatrical, which also
  means "no version"; Director's Cut; Extended Edition; Unrated), extended by
  contributor suggestions that moderators approve. Admins can merge versions
  (D12).
- Version acceptance (D13):
  1. A new label under a work creates a confirmed version.
  2. An existing label that matches only on fuzzy tiers creates a candidate.
  3. An existing label that matches on tiers 1–3 attaches to that version.
  4. "Other" or no version creates a candidate.

  A candidate is promoted automatically when at least 3 independent
  contributors submit tier 1–3 evidence for it, or when a moderator confirms
  it.

**Matching**

- Matching uses a tiered ladder that is shown as a Match % (D10):

  | Tier | Evidence | Match % |
  |---|---|---|
  | 1 | Full-file SHA-256 | 100% |
  | 2 | Sampled SHA-256 or MD5 quick hash | 80% |
  | 3 | Audio or video stream fingerprint | 65% |
  | 4 | TMDB/TVDB and alias, with runtime in tolerance | 30% |
  | 5 | Title, year and alias, with runtime in tolerance | 15% |
  | — | Tier 4 or 5 identity with runtime outside tolerance | 5% |

- A runtime within ±2 s is the same cut, and one within ±30 s is a possible
  cut. Hash and stream tiers ignore runtime.

**Merging**

- The client proposes how its cues correspond to master filters. These
  proposals are hints only. The server fits its own offset and linear drift,
  then merges deterministically from the stored submissions (D5).
- Two cues are the same filter when they share a top-level category and both
  their start and end are within ±N s after alignment. N defaults to 0.5 s and
  is set only in the operator configuration (D6).
- The subcategory follows a plurality of attestations, and ties keep the
  current value.
- Bounds are the median start and end, and their spread is exposed (D7).
- Severity is a weighted vote with low = 1, medium = 1 and high = 3. Ties go to
  the more severe value (D8).

**Signals**

- Attestations, receipt-bound per-filter 👍/👎 votes, disputes and weak
  coverage negatives feed a Wilson lower bound (z = 1.96, with one positive
  and one negative prior) (D9, D14, D17).
- A filter is flagged **Low Confidence** when negatives exceed
  attestations + upvotes.
- One contributor counts once per filter. A re-shared downloaded filter counts
  only if it was locally verified (D15).
- Attribution is a count only, with no names (D16).
- Moderators can quarantine filters, attestations, contributors or versions.
  Each action recomputes from the raw submissions (D18).

**Download and updates**

- Clients download the whole service-signed master set for a version (D19).
- A filter is served only if its confidence is at least 0.05. That floor is
  set in the operator configuration.
- Each version has a monotonic `revision`. Clients bulk-check revisions at
  most once a day and can check one item on demand.
- Updates never silently overwrite anything the user has touched. Changes
  are shown in a review diff (D20).
- Fuzzy matches (tier 4 and below) must be aligned locally before they apply.
  A "Locally verified" badge marks filters the user confirmed or that overlap
  a local finding (D21).
- Local adjustments become attestations on the next share (D22).

## Consequences

- Contributions reinforce one shared set, and each filter carries its own
  confidence and contributor count.
- The service carries a deterministic merge engine. It needs golden and
  order-independence tests, and every change recomputes the affected version.
- Votes and moderation actions change served content, so they advance the
  revision. A daily sync can therefore report changes that only affect
  confidence, and the client applies those silently.
- Accepting the first submission at face value lets an early bad submission
  own a label. This is mitigated by allowing several versions with the same
  label, ranked by trust.
- Count-based confidence rewards Sybil accounts. This is mitigated by
  Discord-bound identity, receipt-bound votes, per-contributor dedupe, the Low
  Confidence rule and moderation recompute.
- The local client gains master-set, update-review and sync state, and a
  migration that archives old revision links without deleting them.
- The precise protocol is in
  [community scan service contracts](../community-scan-service-contracts.md),
  and the local routes are in the [local community API](../community-local-api.md).
