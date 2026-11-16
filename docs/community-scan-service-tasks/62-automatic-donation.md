# CSS-62 — Add explicit opt-in automatic donation

Status: **DONE by explicit deferral, October 4, 2026**. The user selected
"Defer automatic sharing" for CSS-62. Manual sharing remains the only implemented
donation flow; no automatic preparation, completion hook, scheduler or upload was
added. The implementation brief below is retained for a separately authorized
future task, not evidence of implemented automation.

- Prerequisites: [CSS-21](21-local-community-state.md), [CSS-37](37-donation-artifact-builder.md), [CSS-45](45-local-publication-outbox.md), [CSS-46](46-manual-sharing-ui.md)
- Decision gates: [G-LICENSE](decisions.md#g-license), [G-CONTENT](decisions.md#g-content)
- Parent sections: main D07; implementation §3/7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Automatic sharing is optional and distinct from enabling community access. A restore must not unexpectedly publish a library or old queued material.

## Recorded scope decision

On October 4, 2026, the user explicitly chose to defer automatic sharing. The same
answers selected prepare-only with individual preview approval before upload, and
holding pending intents with explicit approval to resume. Those are constraints
for reconsideration, not permission to implement automatic preparation now. Any
future implementation needs renewed scope approval and must not infer opt-in from
connection state or CSS-46's manual `sharing_enabled`/consent record. No backfill,
automatic retry or library-wide publication is authorized.

## Starting points

scanner/community/ donation scheduling; settings repository/UI; scan completion hooks.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add a clearly scoped opt-in for approved contribution types/settings, with disclosure and recorded consent policy version.
2. Create durable intents only for eligible future completions under the chosen policy; reuse exact preview/build/upload pipeline.
3. Pause on revoked eligibility/terms changes; after restore require reconciliation and explicit approval before resumed sharing.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Default remains off: no automatic feature was added or enabled by this task.
- [x] No backfill/library-wide publication was implemented or authorized.
- [x] Automatic completion/restart/disable behavior is explicitly deferred; no new
  automatic intents exist. Existing manual jobs retain CSS-45/46 behavior.

## Verification

- Event/outbox tests for duplicate completion, version/policy change, offline/restore and disable.
- Browser tests verify meaningful opt-in and visible status.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Optional feature has an explicit user decision to defer; no automation is claimed.
- [x] Applicable decision gates record the deferral; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) allow explicit optional-feature deferral.
- [x] Update this status and the [master checklist](README.md) as DONE by deferral, not implemented.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide consent schema, trigger eligibility and restore-safe scheduling behavior to CSS-66/67.

## Handoff summary for the next task

DONE by explicit deferral, October 4, 2026, GitHub Copilot. Clean starting worktree;
no commit. Updated this brief, the master checklist and G-LICENSE only. No runtime,
API, schema, migration, fixture, setting or consent record was changed. No existing
database, server, external service or deployment was touched.

G-LICENSE records the user's explicit "Defer automatic sharing" answer. The existing
G-CONTENT and manual `community-reuse-1` rules remain unchanged. Future automation
must use separate consent; it cannot reinterpret manual sharing approval as opt-in.
Prepare-only and explicit-resumption answers are recorded above without implementing
either feature.

Verification: `node --input-type=module` with assertions for all three changed
documents passed: **123 local links**, balanced code fences and matching
deferral/checklist status. `git diff --check` passed. Application, event/outbox and
browser tests were not run: no runtime or UI changed, and no automatic behavior is
claimed. Automatic scheduling remains absent.

CSS-66/67 must describe manual sharing only and treat automatic donation as explicitly
deferred, not as an implemented integration dependency. There is no new consent
schema or trigger eligibility. CSS-45/46's explicit manual jobs and restore safeguards
remain authoritative. Reopening CSS-62 requires renewed scope approval.

Next task: CSS-63. Read [its brief](63-sharing-policy-withdrawal.md), G-LICENSE and
the CSS-39/46/56 handoffs; inspect standing-permission enforcement in the hosted
publication validator. Preserve attribution and operator moderation; do not introduce
an author-withdrawal flow or license picker.

Direct dependent tasks: [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md).

