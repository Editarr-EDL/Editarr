# Retired cloud-accounting proposal

Status: **RETIRED**, October 7, 2026. Earlier numerical cloud-admission targets
are superseded by the local Docker decision and are not current requirements.
No provider reservation, billing verification or usage-admission work is required.

The October 7, 2026 local Docker decision supersedes usage-admission policy.
There are no user daily/hourly allowances, IP/shared buckets, aggregate payload
caps or recorded-usage pauses. Usage and byte observations are passive. Keep
signatures, membership, replay/idempotency, Access/CSRF, immutable evidence,
duplicate-report protection and per-operation parsing/query/page/cache bounds.
Provider-side cooldowns and Docker/physical hardware constraints still apply.


Historical schema/accounting rows remain only for preserving upgrades and old
archive recovery. See [the current decision](../decisions.md#g-limits),
[retired CSS-24](../24-budget-ledger.md) and
[the Docker runbook](../../community-service-docker.md).
