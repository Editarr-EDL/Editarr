# ADR 0001: Editarr owns scan state; Jellyfin owns media identity

## Status

Accepted.

For community matching, the local-only identity rule below is superseded by
[the work, version and match-key contract](../community-scan-service-contracts.md#3-works-versions-aliases-and-match-keys)
(see also [ADR 0002](0002-community-master-filter-sets.md)).
Jellyfin server/item IDs remain local catalog keys, not public work IDs. This
ADR continues to describe ownership of local scans and exports.

## Context

Editarr must browse a Jellyfin catalog without giving browser or native
clients access to Jellyfin credentials. A movie or episode can be scanned
more than once, and each editing must retain independent findings and EDL
selections.

## Decision

- A `Library` and `MediaItem` are normalized from Jellyfin. A media identity is
  the configured Editarr Jellyfin server plus Jellyfin item ID.
- `MediaItem` retains only Editarr-owned state, including the scan currently
  associated with its sidecar EDL. Jellyfin remains authoritative for artwork
  and descriptive metadata.
- A `Scan` is immutable in identity and has a never-reused, incrementing
  `scan_id` within its media item. Multiple scans for the same item may be
  queued, but the scanner processes queue jobs serially.
- `ScanFinding` records are owned by one scan. Selection updates use a revision
  number so stale clients receive a conflict instead of overwriting work.
- `QueueItem` provides the serial queue order and transient progress for an
  active scan. `PathMapping` translates only server-side Jellyfin paths to the
  scanner host.
- `EdlExport` is a completed, atomic sidecar write associated with one scan.
  The media item points to that scan only after the write succeeds.

## Consequences

Clients call Editarr APIs only; they never call Jellyfin directly or receive
the Jellyfin key. Scan records are the review/export boundary, allowing newer
scans to coexist with an EDL exported from an older one.
