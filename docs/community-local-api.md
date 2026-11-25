# Local community API: master filter sets

The Editarr web app uses these routes to find, apply, vote on, share and update
community master filter sets. The Community tab only browses a linked set: filters
are enabled, disabled and adjusted in the saved reviews that include them, as normal
review filters. The hosted protocol behind them is the
[community service contract](community-scan-service-contracts.md). The design
decisions are in [ADR 0002](adr/0002-community-master-filter-sets.md).
Connection, enrollment, profile and key routes are unchanged; see
[Local community API](api-contract.md#local-community-api).

## 1. Conventions

These follow the existing `/api/community/*` routes:

- **Authentication:** every route requires a forms session or an API key.
  - Unsafe methods, and every `POST` (including read-only `POST`s), require
    the same-origin check and `X-CSRF-Token`.
  - Responses are `Cache-Control: no-store`.
- **Field names:** request bodies are strict snake_case, and extra fields
  return a 422 validation list. Responses are camelCase and carry
  `"version": 1`.
- **Empty responses:** `POST /api/community/sync/notifications/read` returns
  `204 No Content`. Every other successful route returns a JSON body.
- **Errors:**

  | Status | Body | Meaning |
  | --- | --- | --- |
  | 409 | `{code, retryable}` | Nonretryable conflict |
  | 503 | `{code, retryable}` | Retryable failure, or the community service isn't configured |
  | 403 | `capability_denied` | Missing capability |
  | 404 | — | Unknown links, or links owned by another account, so existence isn't revealed |
  | 422 | Existing validation list | Invalid request, including a `link_id` or `filter_id` path segment that isn't 1–128 of `A–Z a–z 0–9 _ . -` |
  | 429 | `{code: "rate_limited", retryable: true, retryAfter}` and `Retry-After` | A repeated manual Find or update check inside its cooldown (§2, §8) |

  A network or membership failure never becomes an empty result. A 409 or
  503 body may add the contract details `reason` (for `vote_ineligible`) and
  `mergedInto` (for `version_merged`) next to `code` and `retryable`.
  Requests without `confirmed: true` where one is required return
  `409 confirmation_required`.
- **Ownership:** master links, votes, preferences and sync notifications all
  belong to the **current account** (`owner_id`).
  They are signed with that account's contributor identity. Media access
  rules apply to every item.
- **Capabilities** (existing IDs):

  | Capability | Routes |
  | --- | --- |
  | `community.search` | find, aliases, master view and lists, active filters, `GET` preferences, including or refreshing review snapshots (§3) |
  | `community.download` | download and apply, alignment, `PATCH` preferences, check, sync and notifications |
  | `community.feedback.submit` | votes, reports |
  | `community.publish.own_review`, `community.publish.scan_evidence`, `community.publish.other_review` | share |

  A missing capability is `403 capability_denied` before any link is looked
  up or any confirmation is checked. Each link route also needs media access,
  with the route's capability, to one catalog location of the link's local
  version; otherwise it is `404`.

- **Targets:** a target is the existing
  `{ "source": "jellyfin", "item_id": "…", "media_source_id": "…"|null }`. The
  backend resolves the local `media_versions.id`, the fingerprints, provider
  IDs, title and year, and the runtime itself. The browser never supplies
  keys, hashes or paths.

## 2. Find

### `POST /api/community/find`

Request:

```json
{ "target": { "source": "jellyfin", "item_id": "abc", "media_source_id": null },
  "label": { "alias_id": "theatrical" }, "refresh": false }
```

- `label` is optional. It is `{alias_id}`, `{text}`, or null (Theatrical).
- `refresh` is accepted for compatibility. Find never reuses an earlier
  result: every call measures the keys and queries the service again.
- The backend computes the available keys using the existing fingerprint
  budgets, then calls `POST /v1/match`.
- The target needs media access with `community.search`; a hidden or unknown
  item is `404`. Find records the target's local version
  (`localVersionId`) from its sampled fingerprint.

Response:

```json
{ "version": 1, "findKey": "<opaque local guard>", "localVersionId": "mv_…",
  "keysUsed": ["md5_quick", "sha256_sampled", "tmdb", "title_year"],
  "results": [ {
    "communityVersionId": "v_12ab", "label": "Theatrical", "aliasId": "theatrical", "status": "candidate",
    "unconfirmed": true, "tier": "4", "matchPercent": 30, "runtimeAgreement": "same_cut",
    "filterCount": 34, "contributorCount": 5, "trust": 0.41, "revision": 12,
    "alignmentRequired": true, "linkedLinkId": null } ],
  "error": null }
```

- `unconfirmed` is `status == "candidate"`. The UI shows the "Unconfirmed"
  label and the ADR tooltip text.
- `alignmentRequired` is true when `tier` isn't `1`–`3`.
- `linkedLinkId` is the current account's existing link for this local
  version and community version, if one exists.
- **Cooldown:** an account may Find the same target with the same `label` at
  most once per 10 seconds. A repeat inside the window returns `429
  rate_limited` with `retryAfter` (whole seconds) and never reaches the
  service, even when the earlier call failed. Another label or target isn't
  limited.
- `findKey` binds the result to the account, target, file generation and match
  response. It expires after 30 minutes. Another account's key is
  `find_expired`.

### `GET /api/community/aliases`

Returns the hosted alias list for the version picker:

```json
{ "version": 1, "choices": [
    { "kind": "alias", "aliasId": "theatrical", "label": "Theatrical" },
    { "kind": "alias", "aliasId": "directors_cut", "label": "Director's Cut" },
    { "kind": "alias", "aliasId": "extended", "label": "Extended Edition" },
    { "kind": "alias", "aliasId": "unrated", "label": "Unrated" },
    { "kind": "other", "label": "Other" } ] }
```

Theatrical is always first and Other is always last.

## 3. Download and apply

### `POST /api/community/masters`

Request:

```json
{ "target": {…}, "find_key": "…", "community_version_id": "v_12ab", "confirmed": true }
```

The backend runs these steps:

1. Revalidate `find_key` and the target file generation.
2. Download `GET /v1/versions/{id}/master`, then verify the snapshot
   signature and receipt (contract §7.3).
3. Store the snapshot and receipt.
4. Create the link or replace its version. Each account has one link per
   local version and service.
5. Create a filter row for each snapshot filter.
   - **Tiers 1–3:** set `alignmentState: "not_required"`. The filters can be
     included in reviews at once.
   - **Other tiers:** set `alignmentState: "required"`. The filters can't be
     included in a review until alignment is approved (§4).

Response:

```json
{ "version": 1, "link": <MasterLink> }
```

Errors:

| Status | Code |
| --- | --- |
| 409 | `find_expired`, `target_changed`, `version_merged` (also returns `mergedInto`), `snapshot_invalid`, `replace_required`, `confirmation_required` |
| 503 | `service_unavailable` (or another retryable transport code) |

The account has one unreplaced link per local version and service.
Downloading another community version for a linked local version without
`replace: true` returns `409 {"code":"replace_required","retryable":false}`
and changes nothing. To confirm the switch, send `confirmed: true` with
`replace: true`: the old link becomes `linkState: "replaced"` and keeps its
rows as history. Downloading the version already linked stores and applies a
newer revision instead (§8).

### `GET /api/community/masters?item_id=…&media_source_id=…`

Lists the current account's unreplaced links for the item version
(`media_source_id` omitted means the item's default version):

```json
{ "version": 1, "links": [<MasterLinkSummary>] }
```

An item the account can't reach is `404`; an item with no local version yet
returns `links: []`. `MasterLinkSummary` and `MasterLink` are the same `link`
object shown below.

### `GET /api/community/masters/{link_id}`

Returns the master view:

```json
{ "version": 1, "link": {
    "linkId": "…", "localVersionId": "mv_…", "communityVersionId": "v_12ab", "label": "Theatrical",
    "status": "confirmed", "unconfirmed": false, "tier": "2", "matchPercent": 80,
    "aliasId": "theatrical", "appliedRevision": 13, "latestRevision": 13, "alignmentState": "not_required",
    "alignment": null, "alignmentRevision": null, "linkState": "active", "mergedInto": null },
  "preferences": { "confidenceThreshold": 0.0, "showLowConfidence": false, "hideDownvoted": false },
  "filters": [ {
    "filterId": "f_9c1d…", "mergedIds": ["f_77aa…"], "category": "violence",
    "master": { "startMs": 600000, "endMs": 605050, "subcategory": "punching", "severity": "high", "action": "skip",
                "expression": null,
                "spread": { "startMs": 300, "endMs": 350 }, "confidence": 0.393857102, "contributorCount": 4,
                "support": { "attestations": 4, "upvotes": 1 }, "negative": { "downvotes": 1, "disputes": 0, "coverageNegatives": 1 },
                "lowConfidence": false, "present": true },
    "locallyVerified": true, "myVote": "up", "active": true } ],
  "retiredVotes": { "f_41b0…": "down" } }
```

Field rules:

- `link.label` is the version's label, or `"Other"` for an unlabeled
  candidate. `link.alignment` is the approved `{offsetMs, scale, anchors}`
  transform or null, and `alignmentRevision` the revision it was approved for.
- `link.linkState` is `active`, `merged` (with `mergedInto`, the surviving
  community version), `unavailable` (the service no longer serves the
  version), or `replaced` (history after a confirmed replace). A merged or
  unavailable link keeps its local rows; the web app shows the state and offers
  Find again. `appliedRevision` is the revision the rows show, which is always
  `latestRevision` once a revision is stored (§8).
- `filters` lists the filters the latest snapshot serves. The view is read-only:
  there are no local filter decisions (ADR 0002 U1).
- `master` holds the filter's master values, mapped to the local timeline
  through `alignment` when one is present. `expression` is a language filter's
  merged word or phrase (contract §6.7), or null; the list shows it beside the
  severity and action.
- `mergedIds` lists the earlier filter IDs this filter continues (the latest
  snapshot's `mergedIds`), or `[]`. A saved review's community filter still keyed
  by one of them (its snapshot predates the merge) reads its `myVote` from, and
  votes through, the filter that lists it.
- `myVote` is the account's acknowledged vote on the filter (`up`, `down` or
  null); the web app shows it on the filter's vote button here and in reviews.
- `retiredVotes` maps each filter the link no longer serves (its local row is kept
  with `present: false`) to the account's last acknowledged vote on it; filters
  without a vote are left out. Reviews still holding such a filter show that vote
  on its unavailable vote button.
- `locallyVerified` drives the "Locally verified" badge. It is true when the
  filter overlaps a finding of the same top-level category from a completed
  local model scan of this local version (after alignment; never while the
  link awaits alignment).
- `active` tells whether the filter **starts enabled** when a review includes
  or refreshes the link: the alignment state isn't `required`, the filter
  isn't Low Confidence unless `showLowConfidence` is set, and its confidence is
  at least `confidenceThreshold`. Filters that don't start enabled are still
  added to the review, disabled (§3 review community filters).
- `active` is computed with the account's **saved** preferences. While the
  user changes the threshold or the Low Confidence toggle and the change
  hasn't been saved yet, the client recomputes it with the same rule for
  display.
- Rows the master no longer serves are left out.

### `GET /api/community/masters/{link_id}/active-filters`

The link's filters that start enabled now (`active` in the master view), with
their master values on the local timeline, and the same filters merged by the
existing Simple EDL range merging (no imported scan is generated).
`?clipped=false` returns them unclipped. Bounds are
clipped to `[0, duration]` (the duration comes from a completed scan of the
local version, when one exists), and a filter wholly outside the file is left
out, so no range is empty or negative:

```json
{ "version": 1, "linkId": "…", "localVersionId": "mv_…",
  "filters": [ { "linkId": "…", "filterId": "f_9c1d…", "category": "violence", "startMs": 600100,
                 "endMs": 605050, "subcategory": "punching", "severity": "medium", "action": "skip",
                 "confidence": 0.39 } ],
  "edl": "## Editarr Simple EDL\n## punching: Community filter\n600.100 605.050 3" }
```

### Saved-review community filters

A saved review includes a master set by storing a **snapshot** of the link's served
filters (master values on the local timeline, after approved alignment) and adding
**every** served filter to the review as a normal review filter (ADR 0002 U2, U4).
From then on a community filter is enabled, disabled and adjusted exactly like a scan
filter, and goes through the same policy, EDL and MCF export, published sidecar,
retained export snapshot, filtered-subtitle and sharing paths. Only its provenance
differs. Later link changes never change a saved review until the user refreshes.

`GET /api/reviews/{review_id}` (and every response that returns a `SavedReview`)
lists the snapshots:

```json
"community_sources": [ {
  "source_id": "…", "link_id": "…"|null, "community_version_id": "v_12ab", "label": "Theatrical",
  "tier": "2", "match_percent": 80, "master_revision": 12, "alignment": null|{…},
  "captured_at": "2026-11-24T21:00:00", "state": "current"|"changed"|"alignment_required"|"link_unavailable",
  "link_state": "active"|"merged"|"unavailable"|"replaced"|null, "filter_count": 34 } ]
```

and each community filter in `filters` and `base_filters` is a `ReviewFilter` with
`source_scan_id: null`, `source_finding_id: null`, `community_source_id` (the
snapshot) and `community_filter_id` (the master filter), `community_edited` (§ below)
and read-only provenance:

```json
"community": { "source_id": "…", "filter_id": "f_9c1d…", "label": "Theatrical", "master_revision": 12,
  "confidence": 0.39, "contributor_count": 4, "low_confidence": false,
  "removed": false, "locally_verified": true }
```

`locally_verified` is true when the review filter's own bounds overlap a finding of
the same top-level category from a completed local model scan of the review's local
versions or among its scans (U7); it works for snapshots whose link is gone.

- **Values.** A master mute becomes an `audio` filter with `mcf_channel: "audio"`;
  a skip becomes a `visual` filter with `mcf_channel: "both"`. `label` is the
  master `expression` (a language filter's word or phrase), or empty without one;
  `details` and `group_key` start empty; `confidence` is the master confidence. A filter whose
  subcategory doesn't belong to its category arrives unclassified (needs
  classification, like any review filter). Bounds are clipped to the runtime known
  when the filter is added (the shortest completed scan of the review, else of its
  local versions); a filter wholly outside the file isn't added.
- **Starts enabled** (U2). The owner's confidence threshold and Show Low Confidence
  decide which filters start enabled when a set is included or new filters arrive
  on Refresh: a filter below the threshold, or Low Confidence while Show Low
  Confidence is off, arrives disabled. Changing the preferences later changes no
  review.
- **`community_edited`** is true once the user directly changed the filter's times,
  classification, severity, action or, for a language filter, label (its expression). The web app sets it on
  those edits. Turning a filter on or off, alone or by group, is a personal choice
  of the review and never counts; nor do bulk changes such as Apply Filter Defaults.
  Clients send it back on save; once set it never resets, and the server ignores it
  on scan filters. Refresh and sharing use it.
- **Policy.** The review owner's filter policy applies to community filters like
  any other: an enforced rule can require and enable one, and enforced severities
  replace its severity, as for hand-added filters.
- **Saving.** `PUT /api/reviews/{review_id}` sends community filters back in
  `filters` like any other filter (the read-only `community` object may be echoed
  and is ignored). Each must name an included snapshot and one of its filters, at
  most once; its provenance can't change. A filter left out is deleted from the
  review, like a scan filter. Filters of a snapshot removed from
  `community_memberships` are removed with it.
- **`community_memberships`** (at most 32): `{"source_id"}` keeps a snapshot,
  `{"source_id", "refresh": true}` refreshes it, and `{"link_id"}` includes a link
  (adding its filters). Omitted or null keeps the stored snapshots; snapshots left
  out are removed. Adding, refreshing or removing one is a content change: it
  advances `revision`, `content_revision`, `edl_content_revision` and
  `mcf_content_revision`, so published exports report themselves outdated.
  `POST /api/library-items/{item_id}/reviews` accepts `{"link_id"}` entries only.
- **Refresh** (U3) takes a new snapshot of the link and merges it into the
  submitted filters by master filter ID, following `mergedIds` (a merged filter
  continues an edited review filter first, then any other, then one the user
  deleted):
  - an edited filter (`community_edited`) keeps the user's values, including its
    label;
  - an unedited filter takes the new master values, including the expression as
    its label, even when bulk defaults had changed them, and keeps the enabled
    state the review gave it (one returning after removal starts as a new one);
  - a new served filter is added, enabled or disabled as above;
  - a filter the master no longer serves stays in the review with
    `community.removed: true`, disabled unless the user had edited it;
  - a filter the user deleted from the review stays deleted;
  - a filter that was left out because it lay outside the file is added once the
    master moves it inside.
- **`state`** compares the snapshot with the link now: `changed` when the link's
  served master values differ (confidence, counts and preferences don't count;
  the review then shows "Refresh community filters"), `alignment_required` when
  the link awaits alignment, and `link_unavailable` when the link was deleted, is
  `replaced` or belongs to another account than the review owner. The review keeps
  its filters in every state.
- **Permissions.** Community filter edits need the same filter and cue edit
  permissions as scan filters. A new or refreshed snapshot needs `community.search`,
  the review evidence-edit permission, and the caller to be the review owner taking
  a snapshot of their own unreplaced link to a local version of the review's
  target; a link awaiting alignment is refused. These are `422 invalid_membership`
  errors (or `403 capability_denied`).
- **Subtitles** (U5) use one rule for every filter: an enabled audio-channel
  language mute with a word or phrase (its label) redacts that expression. A
  community language filter's label is its master expression, so it redacts like
  a scan filter; one without an expression redacts nothing unless the user gives
  it a label, exactly like any wordless filter. There is no community-specific
  subtitle alignment or summary.
- **Exports.** A review needs at least one scan **or** one community filter to
  export. A community-only review (no scans) exports, downloads and publishes EDL
  and MCF sidecars beside the review target's own file, which must still match the
  review's bound Editarr version; its publications list no `exported_scans`, and
  their retained export snapshot carries the community filters in `base_filters`
  for policy regeneration. Its runtime comes from the shortest completed scan of
  its local versions, or else from probing its media file. Retained snapshots from
  before 0040 keep regenerating with their stored community filters.
- **Previews** of a community filter render the review target's file directly,
  as for an unsaved draft filter.
- **Votes.** Each community filter in the review's results shows the account's
  vote and votes through the snapshot's `link_id` with §6, immediately; a vote is
  not part of the review and never changes its revision. The web app reads the
  votes from the master view (`GET /api/community/masters/{link_id}`), matching
  `community_filter_id` to `filterId` or to a filter's `mergedIds`. A snapshot
  without a live link (`link_id` null or `state: "link_unavailable"`), a filter the
  link no longer serves (showing its `retiredVotes` entry), a link the caller can't
  read, or an account without `community.feedback.submit` shows the vote
  unavailable. A failed read other than `404` offers **Retry loading votes** on the
  snapshot's Included scans entry (the Review tab's Sources popover).
- Duplicating a review copies every snapshot with its filters; the link reference
  is kept only when the duplicate's owner owns the link. Deleting an account's
  master state keeps snapshots and filters and clears their link; transferring it,
  or replacing a link with another version, points snapshots at the destination's
  live link without changing them.

## 4. Alignment for fuzzy tiers

These routes reuse the version-reuse verification UI and its clip preview.
A master set has no source transcript, so alignment uses user-placed anchors.

### `POST /api/community/masters/{link_id}/alignment/preview`

Request:

```json
{ "anchors": [ { "filter_id": "f_…", "local_start_ms": 601300 } ], "offset_ms": null, "audio_track_index": 1 }
```

- Send exactly one of `anchors` (1–50) or `offset_ms`; the other is null
  or omitted. Both, neither, an empty list, more than 50 anchors or an
  unknown `filter_id` is a 422 validation list.
- An anchor places a served filter's master start (`startMs`) at
  `local_start_ms`.
- With at least 3 anchors spanning 300 s or more, the preview fits an
  offset and drift (the median of pairwise slopes, then the median offset).
  Otherwise it fits only the median offset. A drift beyond ±10% returns
  `409 alignment_out_of_range`.
- The transform must keep every row on the local file: a row that would end at
  or before 0, or (when a completed scan of the local version records its
  duration) start at or after the file's end, returns
  `409 alignment_out_of_range`. Rows that only partly leave the file are kept
  and clipped where they are used. Approval checks this again.
- The transform maps master to local time:
  `local = master × scale + offsetMs`.
- `audio_track_index` is validated but not used by the backend; the client
  uses it for its clip previews.
- A link that needs no alignment (tiers 1–3) returns
  `409 alignment_not_required`. Nothing is stored by a preview.

Response:

```json
{ "version": 1, "token": "…", "alignment": { "offsetMs": 1300, "scale": 1.0, "anchors": 3 },
  "rows": [ { "filterId": "…", "alignedStartMs": …, "alignedEndMs": … } ] }
```

`rows` lists every served filter in aligned start order. There is no clip token: clips play through the existing
review draft-filter preview of the target file at `alignedStartMs` to
`alignedEndMs`.

### `POST /api/community/masters/{link_id}/alignment/approve`

Request: `{ "token": "…", "for_revision": 13, "confirmed": true }`.

- Approval stores the transform on the link and sets
  `alignmentState: "approved"`.
- The link's filters can then be included in reviews; later revisions keep
  using the stored transform. `for_revision` must be the link's latest
  revision (`409 update_changed` otherwise).
- The token is single-use and expires after 30 minutes. Each account keeps
  at most 32 live previews; a newer preview evicts that account's oldest. An unknown, used,
  expired or another account's token, a `for_revision` other than the
  previewed revision, or rows or revision that changed since the preview,
  returns `409 alignment_preview_changed`.
- The response is `{ "version": 1, "link": <MasterLink> }`.

## 5. Preferences

Local filter decisions are retired (ADR 0002 U1): the Community tab has no
Confirm, Reject, Reset or per-filter edits, and
`PATCH /api/community/masters/{link_id}/filters/{filter_id}` no longer exists.
Decisions stored by earlier builds (`community_master_filters.state` and
`local_*`) stay in the database unused; they affect neither the master view,
reviews nor sharing.

### `PATCH /api/community/preferences`

Request (every field optional):

```json
{ "confidence_threshold": 0.0, "show_low_confidence": false, "hide_downvoted": false }
```

- `confidence_threshold` ranges from 0 to 1.
- `confidence_threshold` and `show_low_confidence` decide which community filters
  start enabled when a review includes or refreshes a master set (§3); they also
  decide which rows the Community tab lists.
- `hide_downvoted` ("Hide downvoted filters") hides downvoted rows from
  the web app's master filter list only. It never changes `active`.
- The response is the saved preferences object
  (`{ "version": 1, "confidenceThreshold": …, "showLowConfidence": …, "hideDownvoted": … }`),
  which the master view also returns as `preferences`.
- These preferences belong to the account. `GET` returns the same object.
- The defaults are `0.0`, `false` and `false`.

## 6. Votes and reports

### `POST /api/community/masters/{link_id}/filters/{filter_id}/vote`

Request:

```json
{ "vote": "up" | "down" | "none", "operation_id": "vote-…", "confirmed": true }
```

The backend runs these steps:

1. Persist the intent in the operation outbox.
2. Send `POST /v1/filters/{id}/votes` with the receipt for the link's
   current snapshot.
3. Set `myVote`.

Response:

```json
{ "version": 1, "state": "completed", "result": {…hosted result…} }
```

| Error | Meaning |
| --- | --- |
| `409 vote_ineligible` | The body's `reason` is `self_vote` or `receipt_required` (the filter isn't in the link's latest receipt) |
| `409 vote_pending`, `operation_conflict`, `receipt_owner_mismatch` | Another open vote on the filter, a reused `operation_id` with another vote, or a link received through account transfer |
| `404` | Unknown link, filter or vote intent |
| `503` | Retryable. The pending intent is kept |

`operation_id` matches `vote-[A-Za-z0-9._~-]{1,123}`.

`POST …/vote/retry` accepts `{operation_id, confirmed: true}` and resends
the saved bytes only.

The web app votes from the Community tab's filter list and from each community
filter in a saved review (§3). Both read the current vote from the master
view's `myVote`; `vote: "none"` clears it.

### `POST /api/community/masters/{link_id}/reports`

Request:

```json
{ "target": { "type": "filter"|"version", "id": "…" }, "reason": "inaccurate", "note": "…", "confirmed": true }
```

This proxies `POST /v1/reports` and returns
`{ "version": 1, "state": "completed", "result": {…hosted result…} }`.

- `reason` is `spam`, `inaccurate`, `privacy`, `rights` or `other`. `note` is
  optional, at most 1,000 characters, and a blank note is not sent.
- A filter target must be in the link's latest receipt
  (`409 vote_ineligible`); a version target must be the link's version
  (`409 report_ineligible`).

## 7. Sharing

Sharing reuses the
[manual sharing](api-contract.md#manual-sharing) dialog and its preview,
consent, discard, approval and publication job routes. Under this contract,
the preview builds a `community-artifact-3` submission. There is one Share
action; master-set sharing isn't a separate flow.

### `POST /api/community/sharing/sources/{kind}/{source_id}/preview`

Request:

```json
{ "name": "…", "identity_confirmed": true, "media": null, "audio_source_scan_id": null,
  "link_id": "…"|null,
  "version_choice": { "choice": "alias", "alias_id": "theatrical" } | { "choice": "other", "text": "IMAX"|null } }
```

- The request is the existing manual-sharing draft (`name`,
  `identity_confirmed`, `media`, `audio_source_scan_id`) plus `link_id` and
  `version_choice`.
- `version_choice` is required. The UI lists the aliases from
  `GET /api/community/aliases` (Theatrical first) and Other, which reveals
  an optional text field. No preview is requested until a version is chosen.
- `link_id` selects the master set to project against. It defaults to the
  account's link for the source's local version. A link that isn't the
  account's live link for that local version and service returns
  `409 link_unavailable`, whether or not it exists.
- The work needs a TMDB or TVDB ID, plus season and episode for TV.
  Otherwise the preview returns `409 work_identity_required`.

The projection builds the entries as follows:

| Source | Entry |
| --- | --- |
| Scan cue, or a review's scan filter | A `support` entry. When the local alignment places it on a served master filter (one the review doesn't already attest to), `proposedFilterId` is set. A language cue carries its label as `expression` when the label is a valid expression (contract §5.2) |
| Enabled review community filter the user edited | A `support` entry for its master filter (`proposedFilterId`), with the user's bounds, subcategory, severity, action and, for a language filter, its label as `expression`; `origin: "downloaded"`, `manualReview: true`, and `locallyVerified` when a local model finding of the same category overlaps it |
| Disabled review community filter | Not shared, whether or not the user edited it: turning a filter off is personal and is never a dispute |
| Untouched review community filter (`community_edited` unset) | Not shared |
| Community filter of a snapshot of another community version than the submission's link, or no longer served by the master | Not shared |
| Local scan coverage | `coverage`: each completed audio stage covers every audio category, and each completed visual stage every video category except `commercial_content` and `dispensable_scenes`, for its analyzed ranges, whichever model ran. Saved reviews send none |

"Edited" is the review filter's `community_edited` (§3). Editarr never sends
`dispute` entries (the protocol keeps the kind for compatibility); a 👎 vote (§6)
is the only negative a user casts. The Community tab's retired local decisions are
never shared. In the estimates, confirmations count proposed cues and supported
community filters, and adjustments the supported ones whose values the user
changed.

The response:

The response keeps the manual-sharing preview fields used for consent and
review (`previewId`, `expiresAt`, `terms` and `preview`), and adds the
master-set summary:

```json
{ "version": 1, "previewId": "…", "expiresAt": 1795554548,
  "terms": { "version": "…", "text": "…" }, "preview": { … },
  "estimates": { "approximate": true, "newFilters": 4, "confirmations": 27, "adjustments": 3 },
  "versionChoice": { "choice": "alias", "aliasId": "theatrical", "label": "Theatrical" },
  "work": { "kind": "movie", "tmdb": "603", "tvdb": null, "title": "The Matrix", "year": 1999 },
  "entryCount": 35, "warnings": [] }
```

- The UI renders the estimates as "≈4 new, ≈27 confirmations, 3
  adjustments".
- The estimates are approximate because the server makes the final match
  using N, which clients never receive.

### `POST /api/community/sharing/previews/{preview_id}/approve`

The request is unchanged: `{confirmed: true}`, and it accepts the displayed
terms as before. Unused previews are still discarded with
`DELETE /api/community/sharing/previews/{preview_id}`. Approval queues an
outbox operation of kind `submission`. The existing publication routes report its
status, and `result` is the hosted `POST /v1/submissions` result.

## 8. Daily sync and notifications

### `POST /api/community/sync/run`

The web app calls this on page load. The request is `{}`.

- **Not due:** if the account's last successful run was less than 24 hours
  ago, or a run is in progress, the route returns the status with
  `ran: false`.
- **Due:** otherwise, the backend sends the account's active and unavailable
  links in batches of 500 under one `runId`. A link whose local version the
  account can no longer reach (media access, §1) is skipped: it isn't sent to
  the service and gets no notification.
  1. It records each changed version's newer revision on its link as a
     pending download, after all revision batches.
  2. It downloads the pending snapshots, applies each to its link (§9) and
     records a `changed` notification for each link once its snapshot is
     stored. Reviews that include the link then show "Refresh community
     filters".
  3. `GET …/master` is limited per contributor and hour
     (`versionChecksPerHour`). When a download fails (for example
     `429 rate_limited`), the run still succeeds and the remaining downloads
     stay pending. Later calls to this route (page loads) download them once
     the service's `Retry-After`, or one hour, has passed. This doesn't start
     a new run or use the daily sync allowance.

A merged or unavailable version also gets a notification. Its link is
marked `merged` (with `mergedInto`) or `unavailable`.

The route needs an enrolled identity (`409 not_connected` otherwise). A
failed run returns its error and is due again after the service's
`Retry-After`, or one hour.

### `GET /api/community/sync`

```json
{ "version": 1, "ran": false, "state": "idle"|"running"|"failed", "lastRunAt": 1795553648,
  "nextDueAt": 1795640048, "lastError": null, "unreadCount": 2, "pendingDownloads": 0,
  "notifications": [ { "notificationId": "…", "linkId": "…", "libraryId": "movies", "itemId": "abc", "mediaSourceId": null,
    "title": "The Matrix", "kind": "changed"|"merged"|"unavailable", "fromRevision": 12, "toRevision": 13,
    "createdAt": 1795553648, "read": false } ] }
```

- `pendingDownloads` counts active links whose newer revision the sync found
  but hasn't downloaded yet.
- At most 100 notifications are returned, newest first. `GET` works before
  enrollment once the service is configured.
- Times are Unix seconds. `fromRevision` and `toRevision` are null when not
  applicable (`toRevision` for `merged` and `unavailable`).
- A notification whose local version the account can no longer reach is
  left out and not counted in `unreadCount`.
- Each notification carries the Jellyfin `libraryId`, `itemId` and
  `mediaSourceId` of the notified local version, so the web app links
  directly to that version's item page on its Community tab
  (`#/library/{libraryId}/item/{itemId}/source/{mediaSourceId}/community`, without
  `/source/…` for the default version).
- While `state` is `running`, the web app re-reads `GET /api/community/sync`
  until it settles, then refreshes open item pages.

### `POST /api/community/sync/notifications/read`

Request: `{ "notification_ids": ["…"] }`, with at most 100 IDs, or
`{"all": true}`; sending both or neither is a 422. Only the account's own
notifications change. The response is `204 No Content`.

### `POST /api/community/masters/{link_id}/check`

This is the item page's **Check for filter updates** button. The request is
`{}`.

- The backend calls `GET /v1/versions/{id}/master?knownRevision=<latest>`.
- If a newer revision exists, it stores the verified snapshot and applies it
  to the link (§9). Reviews keep their own snapshots until refreshed (§3).

Response:

```json
{ "version": 1, "changed": true, "latestRevision": 13 }
```

This check doesn't use the daily sync allowance. An account may check the
same link at most once per 10 seconds; a repeat returns `429 rate_limited`
with `retryAfter` and doesn't reach the service. A merged or unavailable
version marks the link and returns `409 version_merged` (with `mergedInto`)
or `409 version_unavailable` (`version_not_found` when the service no longer
knows it).

## 9. Following newer revisions (ADR 0002 U3)

A link always shows its latest verified snapshot; there is no update review.
Storing a newer revision (Check for filter updates, daily sync, or downloading the
linked version again) applies it at once:

- Each served filter updates the row with its `filterId`, or a new row is added.
- A served filter whose `mergedIds` names rows the snapshot no longer serves
  continues the lowest of them (the row is re-keyed to the merged ID, keeping its
  vote); the others leave the set.
- Rows the snapshot doesn't serve leave the set (`present: false`) and keep their
  history.
- A fuzzy link keeps its approved alignment for the new revision.
- `appliedRevision` becomes `latestRevision`, and `locallyVerified` is
  recomputed.

Reviews bring the changes in with Refresh (§3), which keeps the user's review
edits.

The retired routes `GET /api/community/masters/{link_id}/update`,
`POST …/update/decisions` and `POST …/update/accept-all` are removed, and links
no longer report `pendingUpdate`. Undecided update rows and dismissals stored by
earlier builds stay in `community_master_updates` and `community_master_dismissals`
unused; a link that an earlier build left behind its latest revision catches up
from its stored latest snapshot on its next check or sync.

## 10. Local database (migration `0037_community_master_sets`)

These rules come from AGENTS.md:

- The migration runs only after the existing verified, recoverable backup
  check passes.
- It is versioned and preserves data.
- Its downgrade refuses to discard master-set data.

**Upgrade tests** cover these starting points:

- the `baseline-v1` schema;
- `0013_community_identities`;
- `0036_sampled_media_identity`, with populated `community_imports`,
  `community_import_targets`, and `community_operations` rows of each kind
  and state.

The tests assert that the imported scans and findings are unchanged after
the upgrade.

### 10.1 New tables

| Table | Columns (PK **bold**) | Keys and constraints |
| --- | --- | --- |
| `community_master_links` | **id** (uuid), owner_id → users, media_version_id → media_versions, service_url, contributor_id, community_version_id, community_work_id, label, alias_id, version_status, tier, match_percent, applied_revision, latest_revision, alignment_state (`not_required`\|`required`\|`approved`), alignment_json, alignment_revision, link_state (`active`\|`merged`\|`unavailable`\|`replaced`), merged_into, pending_revision (newer revision awaiting download, §8), created_at, updated_at | Unique on `(owner_id, media_version_id, service_url)` where `link_state != 'replaced'`. Index on `(owner_id, community_version_id)` |
| `community_master_snapshots` | **id**, link_id → links (CASCADE), revision, snapshot_digest, snapshot_bytes (exact BLOB), signature_json, service_key_id, receipt_json, receipt_id, downloaded_at | Unique on `(link_id, revision)`. Only the baseline revisions still in use and the latest revision are retained |
| `community_master_filters` | **link_id**, **filter_id**, category, baseline_json (master values of the revision last applied), baseline_revision, info_json (confidence, counts, spread), local_start_ms, local_end_ms, local_subcategory, local_severity, local_action, state (`pending`\|`verified`\|`adjusted`\|`rejected`\|`low_confidence`), locally_verified, local_touched, my_vote (`up`\|`down`\|NULL), present (bool), updated_at | Checks: valid state, `local_start_ms < local_end_ms`. The `local_*`, `state` and `local_touched` decision columns are retired and unused (§5) |
| `community_master_updates` | **link_id**, **filter_id**, to_revision, change (`new`\|`changed`\|`removed`\|`low_confidence`), changed_fields_json, new_master_json, merged_into, values_digest | Retired (§9): no longer written or read; earlier rows are kept |
| `community_master_dismissals` | **link_id**, **filter_id**, values_digest, dismissed_at | Retired (§9): no longer written or read; earlier rows are kept |
| `community_master_preferences` | **owner_id**, confidence_threshold (0–1), show_low_confidence, hide_downvoted (0039) | — |
| `community_sync_state` | **owner_id**, **service_url**, contributor_id, state, run_id, last_started_at, last_success_at, next_due_at, last_error_code, downloads_retry_at (pending downloads wait until then, §8) | — |
| `community_sync_notifications` | **id**, owner_id, link_id → links (CASCADE), kind, from_revision, to_revision, created_at, read_at | Unique on `(owner_id, link_id, kind, to_revision)`. Index on `(owner_id, read_at)` |

`community_operations` is rebuilt with SQLite batch mode:

- `ck_community_operation_kind` becomes
  `kind IN ('publish', 'submission', 'filter_vote')`.
- New nullable columns: `community_version_id`, `filter_id`, `receipt_id`.
  The exact vote JSON stays in `receipt`, and the vote value in `vote`.

### 10.2 Retiring revision-link data (data-preserving)

| Existing data | Migration action |
| --- | --- |
| `community_imports`, `community_import_targets` | Renamed to `retired_community_imports` and `retired_community_import_targets`. All columns and BLOBs are unchanged, and the `scans` FK cascade is kept. Only backup, export, audit, scan provenance and the source terms and lineage of a shared imported scan read them |
| `scans` and `scan_findings` for imported scans | Unchanged. They stay plain local imported scans, so reviews, exports and published files are unaffected |
| `community_operations` rows of kind `receipt` or `vote` | Copied verbatim into `retired_community_operations` (same columns plus `retired_at` and `retired_reason = 'revision_protocol_removed'`), then deleted from `community_operations` |
| `publish` operations | Rows in `pending` or `submitted` become `failed`, and their `community_publications.error_code` becomes `protocol_retired`. `acknowledged` rows are kept as history. All exact bytes are retained |

Portable backups include all the new and retired tables.

### 10.3 Retired capability grants (migration `0038_retire_receipt_capabilities`)

The revision protocol's `community.receipts.recover.own` and
`community.receipts.recover.other` capabilities are removed from the catalog.
The migration deletes their `role_capability_defaults` and
`user_capability_overrides` rows only; the grant loader already ignored them,
so no access decision changes. The verified pre-upgrade backup keeps the removed
rows, and the upgrade test recovers them from it.

### 10.4 Review community snapshots (migration `0039_review_community_sources`)

| Table | Columns (PK **bold**) | Keys and constraints |
| --- | --- | --- |
| `review_community_sources` | **review_id** → saved_reviews (CASCADE), **id**, link_id → community_master_links (nullable), position, community_version_id, label, tier, match_percent, master_revision, alignment_json, filters_json, filters_digest, captured_at | Index on `link_id`. Checks: `position >= 0`, `master_revision >= 1` |

The migration also adds `community_master_preferences.hide_downvoted`
(default false). It changes no existing row, runs only after the verified,
recoverable backup, and its downgrade refuses while any snapshot or hide preference
exists. The upgrade test starts from `0038_retire_receipt_capabilities` with stored
preferences and recovers the pre-upgrade database from the backup; the upgrade
matrix covers every earlier revision.

The first published 0039 named the column `hide_rejected_downvoted`. A database at
0039 with that column passes the schema check (only that one name may differ), and
0040 renames the column to `hide_downvoted`, keeping each value; a test upgrades
from that shape and recovers it from the backup.

### 10.5 Community filters as review filters (migration `0040_review_community_filters`)

`review_filters` is rebuilt (SQLite batch mode) so a review filter comes from a scan
or from an included community snapshot:

| Change | Detail |
| --- | --- |
| `source_scan_id` | Becomes nullable |
| `community_source_id`, `community_filter_id` | New nullable columns. `(review_id, community_source_id)` → `review_community_sources(review_id, id)` with `ON DELETE CASCADE`; unique on `(review_id, community_source_id, community_filter_id)` |
| `community_edited` | New nullable boolean: the user edited the community filter directly |
| `ck_review_filters_provenance` | Exactly one of `source_scan_id` or `community_source_id`; `community_filter_id` with `community_source_id`; no `source_finding_id` on a community filter |

Every filter stored in an existing snapshot's `filters_json` becomes a review
filter after the review's existing filters (whose positions and values don't
change), enabled as exports used it, so exports keep their content: a mute becomes
an audio mute (`mcf_channel` `audio`), a skip a skip of both channels. A filter
whose subcategory doesn't belong to its category, which exports already left out,
becomes an unclassified, disabled filter. A negative start is clipped to 0; a filter
with no remaining range stays only in the snapshot.

**Earlier Community-tab decisions.** A 0039 snapshot held the link's active filters
with the owner's decisions applied. For a snapshot of a link, the migration rewrites
its entries to the link's **master** values of the snapshot's revision (the kept
verified snapshot of that revision, else the link's rows), aligned as the snapshot
was, with the owner's threshold and Low Confidence preference deciding each
`default_selected`. Against those defaults:

- a stored filter the owner had adjusted becomes an enabled review filter with the
  adjusted values and `community_edited` set;
- a stored filter the owner had enabled despite the threshold stays enabled and is
  unedited, since turning a filter on or off is never an edit;
- a filter the owner had rejected (left out of the snapshot) becomes a disabled,
  unedited review filter, so it is simply not shared;
- the master's other filters the snapshot left out are added disabled and unedited;
- untouched stored filters are unedited.

A snapshot without a link keeps its stored filters as their own defaults. The
digest is recomputed in the new form, so a snapshot whose link hasn't changed reads
`current`. Decisions on links that no review includes no longer apply anywhere; the
`community_master_filters` decision columns, update rows and dismissals are kept
unused. No other row changes.

**Development databases from the earlier 0040.** Builds of this branch before the
owner's W4 decision (ADR 0002) shipped a 0040 that counted turning a filter on or off
as an edit and made rejected decisions edited. 0040 was changed in place because it
was never released, so no released database is affected. A development database
already upgraded with that earlier 0040 isn't upgraded again; restore it from the
pre-0040 backup the adoption path wrote before upgrading, and upgrade again.

The migration runs only after the verified, recoverable backup, and its downgrade
refuses while any community review filter exists. The upgrade tests start from
`0039_review_community_sources` with several reviews and snapshots (linked and
detached, with adjusted, rejected, below-threshold and confirmed decisions, and a
scan filter), check the new review filters, unchanged scan filters and the
snapshots' `current` state, and recover the pre-upgrade database from the backup;
the upgrade matrix covers every earlier revision.

## Open questions

1. **Per-account versus shared master links.** The suggested default is
   per-account, matching personal reviews and per-contributor receipts.
   Several accounts on one install then each download their own copy.
