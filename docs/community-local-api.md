# Local community API: master filter sets

The Editarr web app uses these routes to find, apply, vote on, share and update
community master filter sets. The hosted protocol behind them is the
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
- **Empty responses:** `POST /api/community/sync/notifications/read` and
  `POST /api/community/masters/{link_id}/update/decisions` return
  `204 No Content`. Every other successful route returns a JSON body.
- **Errors:**

  | Status | Body | Meaning |
  | --- | --- | --- |
  | 409 | `{code, retryable}` | Nonretryable conflict |
  | 503 | `{code, retryable}` | Retryable failure, or the community service isn't configured |
  | 403 | `capability_denied` | Missing capability |
  | 404 | — | Unknown links, or links owned by another account, so existence isn't revealed |
  | 422 | Existing validation list | Invalid request |

  A network or membership failure never becomes an empty result. A 409 or
  503 body may add the contract details `reason` (for `vote_ineligible`) and
  `mergedInto` (for `version_merged`) next to `code` and `retryable`.
  Requests without `confirmed: true` where one is required return
  `409 confirmation_required`.
- **Ownership:** master links, local filter state, votes, preferences and
  sync notifications all belong to the **current account** (`owner_id`).
  They are signed with that account's contributor identity. Media access
  rules apply to every item.
- **Capabilities** (existing IDs):

  | Capability | Routes |
  | --- | --- |
  | `community.search` | find, aliases, master view and lists, active filters, `GET` preferences |
  | `community.download` | download and apply, alignment, filter decisions, `PATCH` preferences, check, update review, sync and notifications |
  | `community.feedback.submit` | votes, reports |

  A missing capability is `403 capability_denied` before any link is looked
  up. Each link route also needs media access, with the route's capability, to
  one catalog location of the link's local version; otherwise it is `404`.
  | `community.publish.own_review`, `community.publish.scan_evidence`, `community.publish.other_review` | share |

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
   - **Tiers 1–3:** set `alignmentState: "not_required"`. Untouched rows
     become `pending` and active, unless they are Low Confidence.
   - **Other tiers:** set `alignmentState: "required"`. No row is active
     until alignment is approved (§4).

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
rows as history. Downloading the version already linked stores a newer
revision for update review instead.

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
    "aliasId": "theatrical", "appliedRevision": 12, "latestRevision": 13, "alignmentState": "not_required",
    "alignment": null, "alignmentRevision": null,
    "pendingUpdate": { "new": 3, "changed": 2, "removed": 1, "nowLowConfidence": 0 },
    "linkState": "active", "mergedInto": null },
  "preferences": { "confidenceThreshold": 0.0, "showLowConfidence": false },
  "filters": [ {
    "filterId": "f_9c1d…", "category": "violence",
    "master": { "startMs": 600000, "endMs": 605050, "subcategory": "punching", "severity": "high", "action": "skip",
                "spread": { "startMs": 300, "endMs": 350 }, "confidence": 0.393857102, "contributorCount": 4,
                "support": { "attestations": 4, "upvotes": 1 }, "negative": { "downvotes": 1, "disputes": 0, "coverageNegatives": 1 },
                "lowConfidence": false, "present": true },
    "local": { "startMs": 600100, "endMs": 605050, "subcategory": "punching", "severity": "medium", "action": "skip" },
    "effective": { "startMs": 600100, "endMs": 605050, "subcategory": "punching", "severity": "medium", "action": "skip" },
    "state": "adjusted", "touched": true, "locallyVerified": true, "myVote": "up", "active": true } ] }
```

Field rules:

- `link.label` is the version's label, or `"Other"` for an unlabeled
  candidate. `link.alignment` is the approved `{offsetMs, scale, anchors}`
  transform or null, and `alignmentRevision` the revision it was approved for.
- `link.linkState` is `active`, `merged` (with `mergedInto`, the surviving
  community version), `unavailable` (the service no longer serves the
  version), or `replaced` (history after a confirmed replace). A merged or
  unavailable link keeps its local rows and decisions; the web app shows the
  state and offers Find again. `pendingUpdate` is always present, with zero
  counts when nothing is pending.
- `master` holds the row's baseline master values, mapped to the local
  timeline through `alignment` when one is present.
- `local` holds the user's overrides, or null.
- `effective` is `local`, or else `master`.
- `state` is one of these:

  | State | Meaning |
  | --- | --- |
  | `pending` | Untouched and active |
  | `verified` | The user confirmed it |
  | `adjusted` | The user edited its timing or classification |
  | `rejected` | The user rejected it, so it is shared as a dispute |
  | `low_confidence` | Untouched, and the master is Low Confidence |

- `locallyVerified` drives the "Locally verified" badge. It is true for
  `verified` and `adjusted` rows, and for rows that overlap a local model
  finding of the same category on this local version.
- A row is `active` when **all** of these hold:
  - the alignment state isn't `required`;
  - the state is `pending`, `verified`, or `adjusted` (or
    `low_confidence` when `showLowConfidence` is set);
  - **untouched rows only:** the row isn't Low Confidence, unless
    `showLowConfidence` is set, and its confidence is at least
    `confidenceThreshold`.
- With `showLowConfidence` set, untouched `low_confidence` rows that meet the
  threshold are active.
- Touched rows ignore the threshold.
- `active` is computed with the account's **saved** preferences. While the
  user changes the threshold or the Low Confidence toggle and the change
  hasn't been saved yet, the client recomputes `active` with the same rules
  for display. Once the displayed preferences equal the saved ones, the client
  uses the server's `active` flag.
- Rows with `present: false` are only included when the user kept them
  (§5).

### `GET /api/community/masters/{link_id}/active-filters`

The read path that saved reviews and EDL export use for a master set as a
filter source next to scans (no imported scan is generated). It returns the
link's `active` rows with their `effective` values on the local timeline, and
the same filters merged by the existing Simple EDL range merging:

```json
{ "version": 1, "linkId": "…", "localVersionId": "mv_…",
  "filters": [ { "linkId": "…", "filterId": "f_9c1d…", "category": "violence", "startMs": 600100,
                 "endMs": 605050, "subcategory": "punching", "severity": "medium", "action": "skip" } ],
  "edl": "## Editarr Simple EDL\n## punching: Community filter\n600.100 605.050 3" }
```

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
- An anchor places a filter's master start (`startMs` of the row's baseline,
  or of a new filter in a pending update) at `local_start_ms`.
- With at least 3 anchors spanning 300 s or more, the preview fits an
  offset and drift (the median of pairwise slopes, then the median offset).
  Otherwise it fits only the median offset. A drift beyond ±10% returns
  `409 alignment_out_of_range`.
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

`rows` lists every row in the local set, and new filters of a pending update,
in aligned start order. There is no clip token: clips play through the existing
review draft-filter preview of the target file at `alignedStartMs` to
`alignedEndMs`.

### `POST /api/community/masters/{link_id}/alignment/approve`

Request: `{ "token": "…", "for_revision": 13, "confirmed": true }`.

- Approval stores the transform on the link and sets
  `alignmentState: "approved"`.
- For the first import, it activates the rows.
- For an update (§9), it enables **Accept** for that revision's rows.
- The token is single-use and expires after 30 minutes. An unknown, used,
  expired or another account's token, a `for_revision` other than the
  previewed revision, or rows or revision that changed since the preview,
  returns `409 alignment_preview_changed`.
- The response is `{ "version": 1, "link": <MasterLink> }`.

## 5. Local filter decisions

### `PATCH /api/community/masters/{link_id}/filters/{filter_id}`

Request:

```json
{ "decision": "verify" | "reject" | "adjust" | "reset",
  "start_ms": 600100, "end_ms": 605050, "subcategory": "punching", "severity": "medium", "action": "skip",
  "expected_state": "pending" }
```

- `adjust` requires at least one value field; other decisions take none.
  Values are validated against the taxonomy: `subcategory` must belong to the
  row's top-level `category`, `severity` is `low`, `medium` or `high`, and
  `action` is `skip` or `mute`. The resulting effective bounds must satisfy
  `0 ≤ start_ms < end_ms`. Violations are a 422 validation list.
- `expected_state` is optional.
- `verify`, `reject` and `adjust` set `touched = true`.
- `reset` clears the local values, sets `state` back to
  `pending`/`low_confidence`, and clears `touched` unless the user has voted.
- A stale `expected_state` returns `409 filter_state_changed`.
- The response is the updated filter object with `"version": 1`. An unknown
  filter is `404`.

### `PATCH /api/community/preferences`

Request:

```json
{ "confidence_threshold": 0.0, "show_low_confidence": false }
```

- `confidence_threshold` ranges from 0 to 1.
- The response is the saved preferences object
  (`{ "version": 1, "confidenceThreshold": …, "showLowConfidence": … }`).
- These preferences belong to the account. `GET` returns the same object.
- The defaults are `0.0` and `false`.

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
3. Set `myVote`, and `touched = true` unless the vote is `none`.

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
| Review or scan cue | A `support` entry. When the local alignment places it on a master filter, `proposedFilterId` is set |
| `rejected` row | A `dispute` entry |
| `verified` or `adjusted` row not covered by a cue | A `support` entry with `origin: "downloaded"` and `locallyVerified: true`, carrying the effective values |
| Untouched downloaded row | Not shared |
| Local scan coverage | `coverage` |

The response:

The response keeps the manual-sharing preview fields used for consent and
review (`previewId`, `expiresAt`, `terms` and `preview`), and adds the
master-set summary:

```json
{ "version": 1, "previewId": "…", "expiresAt": 1795554548,
  "terms": { "version": "…", "text": "…" }, "preview": { … },
  "estimates": { "approximate": true, "newFilters": 4, "confirmations": 27, "adjustments": 3, "disputes": 1 },
  "versionChoice": { "choice": "alias", "aliasId": "theatrical", "label": "Theatrical" },
  "work": { "kind": "movie", "tmdb": "603", "tvdb": null, "title": "The Matrix", "year": 1999 },
  "entryCount": 35, "warnings": [] }
```

- The UI renders the estimates as "≈4 new, ≈27 confirmations, 3
  adjustments, 1 dispute".
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
- **Due:** otherwise, the backend sends all of the account's links in
  batches of 500 under one `runId`.
  1. For each changed version, it downloads the new snapshot.
  2. It builds the update rows (§9).
  3. It records a notification.

A merged or unavailable version also gets a notification. Its link is
marked `merged` (with `mergedInto`) or `unavailable`.

The route needs an enrolled identity (`409 not_connected` otherwise). A
failed run returns its error and is due again after the service's
`Retry-After`, or one hour.

### `GET /api/community/sync`

```json
{ "version": 1, "ran": false, "state": "idle"|"running"|"failed", "lastRunAt": 1795553648,
  "nextDueAt": 1795640048, "lastError": null, "unreadCount": 2,
  "notifications": [ { "notificationId": "…", "linkId": "…", "libraryId": "movies", "itemId": "abc", "mediaSourceId": null,
    "title": "The Matrix", "kind": "changed"|"merged"|"unavailable", "fromRevision": 12, "toRevision": 13,
    "createdAt": 1795553648, "read": false } ] }
```

- At most 100 notifications are returned, newest first. `GET` works before
  enrollment once the service is configured.
- Times are Unix seconds. `fromRevision` and `toRevision` are null when not
  applicable (`toRevision` for `merged` and `unavailable`).
- A notification whose local version the account can no longer reach is
  left out and not counted in `unreadCount`.
- Each notification carries the Jellyfin `libraryId`, `itemId` and
  `mediaSourceId` of the notified local version, so the web app links
  directly to that version's item page
  (`#/library/{libraryId}/item/{itemId}/source/{mediaSourceId}`).
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
- If a newer revision exists, it stores the snapshot and builds the update
  rows. It never applies them.

Response:

```json
{ "version": 1, "changed": true, "latestRevision": 13,
  "pendingUpdate": { "new": 3, "changed": 2, "removed": 1, "nowLowConfidence": 0 } }
```

This check doesn't use the daily sync allowance. A merged or unavailable
version marks the link and returns `409 version_merged` (with `mergedInto`)
or `409 version_unavailable` (`version_not_found` when the service no longer
knows it).

## 9. Update review (D20)

**Baselines:** each filter row keeps the master values it last accepted, which
form its baseline. A new snapshot is compared row by row with those
baselines, using `filterId`.

**Merged IDs:**

- A new filter whose `mergedIds` contains a row's ID re-keys that row.
- When several rows merge into one filter, the touched row, or else the
  lowest ID, re-keys.
- The other merged rows appear under Removed with `mergedInto`.

**Material fields:** `startMs`, `endMs`, `subcategory`, `severity`, `action`,
`lowConfidence` and presence. `confidence`, the counts and `spread` are
informational and refresh immediately with no review.

| Change | Group | Untouched row | Touched row |
| --- | --- | --- | --- |
| Filter not in baseline | `new` | Accept or Dismiss | — |
| Material field differs | `changed` | Accept or Dismiss, shown as old → new | "You edited this": shows mine, old master and new master. Keep mine, Accept or Dismiss. Mine stays in effect until the user decides |
| Filter absent | `removed` | Accept (deactivate) or Dismiss | "You kept this": Keep mine, Accept or Dismiss. Stays in effect unless the user accepts |
| `lowConfidence` false → true | `nowLowConfidence` | Accept (row becomes `low_confidence`) or Dismiss | Same, and mine is kept |

**Decisions:**

| Decision | Effect |
| --- | --- |
| `accept` | Adopts the new master values as the row's baseline, and resets the local overrides for the changed fields |
| `keep_mine` | Keeps the current effective values, updates the baseline to the new master values, and sets `touched` |
| `dismiss` | Hides the row until the master values change again. Stored as a dismissal digest; the effective values don't change |

The **dismissal digest** is the SHA-256 of the compact JSON
`{"startMs","endMs","subcategory","severity","action","lowConfidence","present"}`
taken from the new master values.

### `GET /api/community/masters/{link_id}/update`

```json
{ "version": 1, "fromRevision": 12, "toRevision": 13, "alignmentRequired": true, "alignmentApproved": false,
  "groups": { "new": [<Row>], "changed": [<Row>], "removed": [<Row>], "nowLowConfidence": [<Row>] } }
```

A `<Row>` looks like this:

```json
{ "rowId": "…", "filterId": "…", "category": "violence", "touched": false, "changedFields": ["startMs","severity"],
  "mine": {…}|null, "oldMaster": {…}|null, "newMaster": {…}|null, "mergedInto": null,
  "allowed": ["accept","dismiss"], "canAccept": false }
```

- `canAccept` is false for every row with bounds while the link is fuzzy
  (tier 4 and below) until alignment is approved for `toRevision` (§4).
  `removed` rows can always be accepted.
- `mine`, `oldMaster` and `newMaster` are on the **local timeline**: the
  master values are mapped through the link's stored alignment, the same
  transform the master view uses. Until alignment is approved for
  `toRevision`, that is the previously approved transform.
  - `mine` is the row's current effective values (`startMs`, `endMs`,
    `subcategory`, `severity`, `action`) for a touched row, or null.
  - `oldMaster` is the baseline master values (those fields plus
    `lowConfidence` and `present`), or null for a new filter.
  - `newMaster` adds the informational fields (`confidence`,
    `contributorCount`, `support`, `negative`, `spread`), and is null for a
    removed filter.
- `rowId` is the filter ID.
- The client prefills the alignment step from the stored transform.

### `POST /api/community/masters/{link_id}/update/decisions`

Request:

```json
{ "to_revision": 13, "decisions": [ { "row_id": "…", "decision": "accept"|"keep_mine"|"dismiss" } ] }
```

- At most 500 decisions per request. The request is atomic.
- A decision outside the row's `allowed` list, Accept while `canAccept` is
  false, or two decisions for one row return `409 decision_not_allowed`.
- A row that was already decided returns `409 update_changed`.
- A stale `to_revision` returns `409 update_changed`.
- The response is `204 No Content`; the client re-reads the update.

### `POST /api/community/masters/{link_id}/update/accept-all`

Request: `{ "to_revision": 13, "group": "new"|"changed"|"removed"|"nowLowConfidence" }`.

- This accepts every **untouched** row in the group whose `canAccept` is
  true.
- Touched rows must be decided one at a time.
- The response is the remaining update.

When no undecided rows remain, the link's `appliedRevision` becomes
`toRevision`.

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
| `community_master_links` | **id** (uuid), owner_id → users, media_version_id → media_versions, service_url, contributor_id, community_version_id, community_work_id, label, alias_id, version_status, tier, match_percent, applied_revision, latest_revision, alignment_state (`not_required`\|`required`\|`approved`), alignment_json, alignment_revision, link_state (`active`\|`merged`\|`unavailable`\|`replaced`), merged_into, created_at, updated_at | Unique on `(owner_id, media_version_id, service_url)` where `link_state != 'replaced'`. Index on `(owner_id, community_version_id)` |
| `community_master_snapshots` | **id**, link_id → links (CASCADE), revision, snapshot_digest, snapshot_bytes (exact BLOB), signature_json, service_key_id, receipt_json, receipt_id, downloaded_at | Unique on `(link_id, revision)`. Only the baseline revisions still in use and the latest revision are retained |
| `community_master_filters` | **link_id**, **filter_id**, category, baseline_json (master values at baseline), baseline_revision, info_json (confidence, counts, spread, as of the latest revision), local_start_ms, local_end_ms, local_subcategory, local_severity, local_action, state (`pending`\|`verified`\|`adjusted`\|`rejected`\|`low_confidence`), locally_verified, local_touched, my_vote (`up`\|`down`\|NULL), present (bool), updated_at | Checks: valid state, `local_start_ms < local_end_ms` |
| `community_master_updates` | **link_id**, **filter_id**, to_revision, change (`new`\|`changed`\|`removed`\|`low_confidence`), changed_fields_json, new_master_json, merged_into, values_digest | Rebuilt for each newly stored latest snapshot. A row is deleted when it is decided |
| `community_master_dismissals` | **link_id**, **filter_id**, values_digest, dismissed_at | — |
| `community_master_preferences` | **owner_id**, confidence_threshold (0–1), show_low_confidence | — |
| `community_sync_state` | **owner_id**, **service_url**, contributor_id, state, run_id, last_started_at, last_success_at, next_due_at, last_error_code | — |
| `community_sync_notifications` | **id**, owner_id, link_id → links (CASCADE), kind, from_revision, to_revision, created_at, read_at | Unique on `(owner_id, link_id, kind, to_revision)`. Index on `(owner_id, read_at)` |

`community_operations` is rebuilt with SQLite batch mode:

- `ck_community_operation_kind` becomes
  `kind IN ('publish', 'submission', 'filter_vote')`.
- New nullable columns: `community_version_id`, `filter_id`, `receipt_id`.
  The exact vote JSON stays in `receipt`, and the vote value in `vote`.

### 10.2 Retiring revision-link data (data-preserving)

| Existing data | Migration action |
| --- | --- |
| `community_imports`, `community_import_targets` | Renamed to `retired_community_imports` and `retired_community_import_targets`. All columns and BLOBs are unchanged, and the `scans` FK cascade is kept. No code reads them except backup, export and audit |
| `scans` and `scan_findings` for imported scans | Unchanged. They stay plain local imported scans, so reviews, exports and published files are unaffected |
| `community_operations` rows of kind `receipt` or `vote` | Copied verbatim into `retired_community_operations` (same columns plus `retired_at` and `retired_reason = 'revision_protocol_removed'`), then deleted from `community_operations` |
| `publish` operations | Rows in `pending` or `submitted` become `failed`, and their `community_publications.error_code` becomes `protocol_retired`. `acknowledged` rows are kept as history. All exact bytes are retained |

Portable backups include all the new and retired tables.

## Open questions

1. **How reviews and EDL exports consume master sets.** The default is
   implemented as a read path: master sets stay a filter source next to scans,
   and no derived imported scan is generated.
   `community_master_sources.active_master_filters` returns an account's
   active effective filters for a local version (after local decisions, the
   saved threshold and Low Confidence preference, and alignment), and
   `as_findings` feeds them to the existing EDL range merging, which
   `GET /api/community/masters/{link_id}/active-filters` exposes. **Still
   open:** adding a link as a member of a saved review, so the review's
   filter linking, export revision, policy freshness and published sidecars
   include it.
2. **Per-account versus shared master links.** The suggested default is
   per-account, matching personal reviews and per-contributor receipts.
   Several accounts on one install then each download their own copy.
