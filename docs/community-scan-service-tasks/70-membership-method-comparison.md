# CSS-70 — Compare bot and OAuth membership rechecks before selection

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE**, October 1, 2026. Official-source comparison written and
selection question presented; user subsequently selected single-member bot REST.

- Prerequisites: [CSS-00](00-repository-preflight.md).
- Decision gates: None to prepare the comparison; the resulting choice gates [CSS-27](27-membership-eligibility.md).
- Parent sections: Discord §4; main D22; G-MEMBERSHIP.
- Read first: [execution guide](README.md), [shared context](context.md), [decision registry](decisions.md#g-membership), and [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The user explicitly requested a technical comparison before choosing the membership recheck method. Freshness (15 minutes on use), outage denial after expiry, membership-only admission and automatic rejoin eligibility are already fixed.

## Starting points

docs/community-scan-service-tasks/decision-evidence/; existing Discord contract fixtures; small isolated prototype only if useful.

## Work within this task

1. Compare single-member bot REST checks against user OAuth membership checks using current primary Discord documentation; distinguish single-member lookup from privileged bulk listing and Gateway events.
2. Document application/bot setup, exact scopes/permissions, secret retention, token refresh/revocation, rate limits, maintenance burden, user reauthorization and 15-minute coalesced request cost.
3. Prepare concrete D1/Worker secret/config changes and failure/rejoin examples for each choice. Identify what still needs verification in a real test server; do not create one or retain real tokens incidentally.
4. Present the evidence and recommendation to the user, ask which method to implement, and record the answer if supplied. Implement neither strategy as production behavior in this task.

## Acceptance criteria

- [x] Report compares the same approved access policy for both alternatives and names all stored secrets/data.
- [x] Published facts link to current official sources; estimates and untested endpoint assumptions are labeled.
- [x] Report includes minimal setup steps and an exact selection question; CSS-27 remains gated until the user chooses.

## Verification

- Review documented endpoints/scopes and calculate request/storage overhead for representative active-member counts, retries and provider outages.
- If a prototype is used, run it against fake responses and record limitations; live permission tests require OPS-01 authorization.

## Definition of done

- [x] Every acceptance criterion is met and the comparison is written to disk.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] The user has been asked to select a method; an unanswered choice remains open.
- [x] Update task/index status and write the required handoff summary below.
- [x] Provide comparison/report path, dated official sources, assumptions, cost model and explicit unanswered/answered method choice for CSS-27. Completing this report does not choose the method.

## Handoff summary for the next task

October 1 comparison: [report](decision-evidence/70-membership-comparison.md)
compares single-member bot REST with retained OAuth under identical 15-minute
policy, naming secrets, setup, refresh/revocation, D1/backup changes and modeled
50/500/5000-active-contributor costs. Official Discord guild/user/OAuth/rate-limit
sources were fetched; no prototype or live resource was used. Recommendation
is minimal-permission bot REST, subject to OPS-01 live verification. Exact
selection question is in the report and was presented to the user October 1;
selection was initially skipped, then the user chose the bot plan. Request model verified with
Node arithmetic: 96 checks/day per continuously active contributor; 50/500/5000
contributors imply 4800/48000/480000 calls/day and 0.056/0.556/5.556 mean calls
per second. These are estimates, not endpoint benchmarks. `git diff --check`
passed; no executable implementation changed and no application tests or live
permission checks were needed or claimed. Remaining in CSS-70: none.
CSS-27 now implements the selected provider and bounded membership state machine
against fake responses. OPS-01 must verify minimal installation permissions live.
CSS-25's existing eligibility hook is the integration entry point; its broader
unfinished mutation workflows must not be claimed complete by this task.

```text
Status / date / implementer:
Report and prototype files:
Official sources checked and dates:
Compared approaches / secret inventory / request-cost assumptions:
Tests or calculations and results:
Known limitations and required live verification:
User method decision, or exact pending question:
Next action for CSS-27:
```

Direct dependent task: [CSS-27](27-membership-eligibility.md).

