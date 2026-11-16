# CSS-24 - Retired accounting-admission work

Status: **RETIRED**, October 7, 2026. No implementation or downstream dependency
is required. The local Docker service no longer needs usage-based admission.

The October 7, 2026 local Docker decision supersedes usage-admission policy.
There are no user daily/hourly allowances, IP/shared buckets, aggregate payload
caps or recorded-usage pauses. Usage and byte observations are passive. Keep
signatures, membership, replay/idempotency, Access/CSRF, immutable evidence,
duplicate-report protection and per-operation parsing/query/page/cache bounds.
Provider-side cooldowns and Docker/physical hardware constraints still apply.


Passive observations remain in the existing usage modules and operator views;
they do not reserve spending, reject requests or require provider verification.
CSS-25 still owns the independent current-route authentication audit. CSS-68
owns target-hardware capacity and independent recovery, not user allowances.

Historical migrations are immutable compatibility records. Migrations 0015 and
0016 remove enforcement without deleting prior data. Existing-store upgrades
require explicit offline maintenance and an independently verified backup.
See the [Docker runbook](../community-service-docker.md) and
[current decision](decisions.md#g-limits). Do not restore this task as an active
prerequisite or mark the retired implementation DONE.
