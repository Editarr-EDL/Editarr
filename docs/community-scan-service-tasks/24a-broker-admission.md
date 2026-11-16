# CSS-24A - Trusted ingress and passive broker observations

Status: **DONE** in local/mock scope; current contract updated October 7, 2026.
Live Tunnel/Discord setup and target-machine capacity remain separate release gates.

- Prerequisites: [CSS-22](22-worker-scaffold.md), [CSS-23](23-d1-identity-schema.md).
- Dependent task: [CSS-26](26-discord-oauth.md).
- Runtime: [Node/SQLite Docker](../community-service-docker.md).

The October 7, 2026 local Docker decision supersedes usage-admission policy.
There are no user daily/hourly allowances, IP/shared buckets, aggregate payload
caps or recorded-usage pauses. Usage and byte observations are passive. Keep
signatures, membership, replay/idempotency, Access/CSRF, immutable evidence,
duplicate-report protection and per-operation parsing/query/page/cache bounds.
Provider-side cooldowns and Docker/physical hardware constraints still apply.


## Current Scope

- [x] Trusted ingress metadata is required before broker database/provider work.
- [x] Missing/invalid actor metadata, forwarding aliases and Worker subrequests fail closed.
- [x] Explicit disabled runtime configuration refuses contributor/operator routes.
- [x] Anonymous request/write observations are privacy-preserving and unknown-aware.
- [x] Missing telemetry never hides a committed redirect or verified grant.
- [x] Bounded expiry cleanup preserves durable identity and operation evidence.

## Implementation and Handoff

The runtime supplies the protected Tunnel actor adapter. Admission requires a
valid trusted actor and available handler configuration, without source hashing
into request buckets or a database accounting-policy query. A direct client cannot
establish trust through forwarding headers. Tunnel listener isolation is required;
`CF-Connecting-IP` alone is not cryptographic authentication.

`serviceUsage` reads anonymous broker counters and contributor observations for
display only. Historical request/write labels are not provider billing. SQLite
reports logical changes including triggers; scanned reads remain unknown. No
enabled/paused/headroom fields are returned. Observation failures preserve the
handler outcome and may add `Community-Usage-Status: unavailable`.

Migration 0006 records anonymous daily observations without IPs, OAuth identity,
Discord accounts, tokens or secrets. Bounded hourly cleanup expires transient
browser/grant/nonce state and old anonymous detail, not durable operations or
security facts. Full SQLite backups retain counters and schema state. Applying
numbered upgrades to an existing database requires a verified recoverable backup;
startup does not migrate, initialize or stamp a store.

The September 30 implementation evidence described the prior provider runtime.
It is not evidence of live setup or the current Docker deployment. Current
regressions verify repeated trusted requests, unknown/high observations,
telemetry failures, membership/provider failures, replay and preserving upgrades.
Use the repository test commands; record actual results for future changes.

Next action: CSS-26 consumes the trusted admission adapter. Authenticated handlers
record passive observations while preserving current authority and atomic replay
state. CSS-25 owns the independent route-auth audit; CSS-68 owns capacity/recovery.
