# Catalog access (MU-20)

Catalog, artwork, candidate, and audio-track routes decide media before they
count, show, or serve it. The contract is in
[Catalog authorization](../../../../docs/multi-user-contracts.md#catalog-authorization).

## Deterministic HTTP

`tests/test_catalog_authorization_http.py` and
`tests/test_catalog_authorization_regressions.py` drive the real catalog routers
through the access middleware (fixture `mu20-catalog-1` over
`mu11-media-access-4`). The Jellyfin catalog client and MU-16's
evidence adapter are replaced and record their calls; that substitution
is not live evidence.

```bash
PYTHONPATH=$PWD EDITARR_DATA_DIR=<scratch>/data EDITARR_LOG_DIR=<scratch>/logs \
  .venv/bin/python -m pytest -q tests/test_catalog_authorization_http.py \
  tests/test_catalog_authorization_regressions.py
```

It covers lists, totals, facets, and a page boundary between hidden and visible
items; refused libraries, items, children, and artwork after the owner cached
them; candidates and associations that a client path cannot widen; scope
and sync changes seen by an existing session; tracks named without a path; and
item decisions reused inside one sync window.

The regressions compare hidden and missing IDs by status, body, and headers;
refuse raw paths before any file inspection; remove stored candidate errors
without path permission; and warm an allowed item before a revoking sync and
an item-read outage. The last case must remain 404. A fresh item read that
completes after the request's clock sample must still allow reachable media.
Artwork tests require `private, no-store`, Cookie/Authorization Vary headers,
and different URLs and ETags for different principals.

For browser-cache verification, fetch restricted Jellyfin and Arr artwork as
the owner in Chromium, change to a restricted user's cookie in that same
context, and fetch the exact URLs again without disabling the browser cache.
Both requests must return 404, matching fresh URLs. Exercise desktop and mobile
viewports. This cache check can use the recorded provider boundary above; it
does not certify a live Arr server. The server checks each image request even
when its principal query value came from another account.

## Live Jellyfin

`scripts/catalog_live.py run` launches media_access_live.py's disposable
pinned Jellyfin 12.0.0 with synthetic media, links user 4 to Casey (Family and
Home only) from her verified snapshot, and reads the catalog over HTTP as the
owner and as Casey with the real catalog client and evidence. It always
removes the container and volumes.

```bash
PYTHONPATH=$PWD EDITARR_DATA_DIR=<scratch>/data EDITARR_LOG_DIR=<scratch>/logs \
  .venv/bin/python .github/skills/verify-editarr/scripts/catalog_live.py run
```

Pass means Casey sees only Family and Home without locations, the same Family
total as the owner without paths, and 404 `not_found` for the Movies listing,
a Movies item, and its artwork. Evidence goes to
`web/test-results/verify-editarr/catalog-<run>/` (`instance.json`, the doctor
records, `results.json` or `failure.json`, `cleanup.json`, `redaction.json`).
Exit codes are 0 pass, 1 failure, and 2 blocked (Docker or the pinned image
missing). If a run is interrupted, `media_access_live.py cleanup EVIDENCE`
removes its labelled container and volumes.
