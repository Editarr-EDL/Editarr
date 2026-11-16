# CSS-33 — Implement the approved additional-installation and lost-key flow

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** (October 1, 2026), local/mock scope. Live setup/deployment remains separate.

- Prerequisites: [CSS-28](28-enrollment-profile.md), [CSS-29](29-local-service-client.md), [CSS-30](30-connection-settings.md), [CSS-31](31-key-rotation-service.md), [CSS-32](32-manual-key-rotation-ui.md)
- Decision gates: [G-KEYS](decisions.md#g-keys), [G-RECOVERY](decisions.md#g-recovery)
- Parent sections: Discord §4; contracts §1.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

The user selected separate installation keys and explicit Discord-assisted lost-key recovery. A username or possession of an enrollment URL is never sufficient recovery proof. Recovery targets one existing installation; registering a new installation is a separate operation.

## Starting points

scanner/community/; community-service/src/identity/; community settings UI.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Support enrolling another installation with a separate key under the same Discord contributor, preserving account identity and reputation.
2. For lost-key recovery, require fresh Discord authorization bound to the contributor, selected installation, new public key and one-use recovery intent. Require explicit local UI confirmation; immediately retire only the replaced installation key.
3. Preserve backup recovery and interrupted-operation reconciliation. Lost Discord access has no approved transfer bypass; show actionable supported recovery limits rather than inventing one.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Contributor ID, reputation and bans survive every supported path.
- [x] Ordinary login never silently rotates keys; unsupported recovery asks for the prescribed proof rather than opening anonymous enrollment.
- [x] Old backups/other installations behave exactly as disclosed by the selected retirement scope.

## Verification

- Test reinstall, two installations, all-key loss, compromised key, lost Discord access, duplicate recovery and interrupted response.
- Verify UI confirmation and immediate retirement with the same adversarial tests as normal rotation.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Document exact ownership proofs, affected-key scope and user recovery runbook for CSS-66/67.

## Handoff summary for the next task

DONE / October 1, 2026 / GitHub Copilot / uncommitted working-tree changes.
Hosted and local portions were implemented and verified together; no partial subtask
is deferred. G-KEYS/D21 and G-RECOVERY/D24 remain unchanged.

The [exact protocol and recovery runbook](decision-evidence/33-key-recovery-contract.md)
documents fresh ownership proof, affected-key scope, failures, private journal,
backup compatibility and user recovery limits for CSS-66/67. The
[shared fixture](../../tests/fixtures/community/key-recovery-v1.json) freezes
routes and request/envelope/result fields, independently checked by local and
Worker tests. CSS-12 signatures and existing confirmation domain remain unchanged.

### Implementation

- [Hosted transaction](../../community-service/src/identity/recovery.ts) and
  [handler](../../community-service/src/http/recovery.ts) require fresh
  contributor/installation/old-key/candidate/operation-bound Discord authority,
  backend-held confirmation secret and candidate signature. Atomic grant,
  retirement, activation, operation and nonce commit preserve contributor
  records/history. Normal enrollment rejects recovery grants. Worker native
  admission, passive observations, continuing membership and nonlocal guard remain.
- Separate broker recovery start/hosted disclosure plus candidate status and
  active-owned operation lookup reconcile lost replies and expired/fresh grants.
  Unused authorizations expire in bounded cleanup; committed evidence survives.
  No schema changes, existing-store migration, backup application or reset occurred.
- [Private journal](../../scanner/security/community_key_recovery.py) and
  [local workflow](../../scanner/integrations/community/recovery.py) atomically
  retain one candidate, operation, secret and exact body. Recovery-only metadata
  reads permit a missing old private key without relaxing normal signing or SQLite
  identity checks. Worker lease/interprocess lock serialize sends against backups.
  Verified pointer activation and recoverable projection retain contributor and
  sharing consent; profile refresh remains signed/current, not cached authority.
- Administrator-session/CSRF routes expose start, complete, local status, check
  and confirmed retry. The explicit authentication-policy inventory includes all.
  [Recovery settings](../../web/src/settings/community/community-recovery.tsx)
  require separate prepare/final approval, show passive saved state and fresh
  authorization with the same candidate, preserve failures, and prompt for a fresh
  optional-encryption backup. No private seed/proof/secret enters the browser.
- Full/settings plain/encrypted backup inventory preserves pending/completed
  recovery, including genuinely missing old keys. Restore neither activates nor
  sends. Completed exports contain only the selected active private slot plus
  completed journal. New secret type is `community-recovery-proof`; older builds
  reject unsupported new archives instead of discarding the intent.

### Verification

- `.venv/bin/python -m pytest -q` over the community identity/client/state/contract/
  API/status/transport/repository/signature/rotation/recovery tests and auth,
  portable-backup, restore and activation tests: final scoped run **224 passed**.
  Initial route-inventory failure was repaired; focused auth/API rerun 45 passed.
  Final recovery/contract rerun 14 passed; backup generation suite 21 passed.
  This is not a claim about the entire backend suite.
- Changed Python Ruff checks and editor diagnostics passed. Local persistence
  fault, incorrect archived secret allowlist, test signature timestamp, import
  style and JSX defects were repaired and the corresponding checks passed.
- `pnpm --dir community-service test`: 318 tests/43 files passed;
  `run typecheck` and `run build` passed (Wrangler dry-run only).
  Final shared-contract HTTP rerun: six passed. Focused hosted recovery: 20 passed.
- `pnpm --dir web exec vitest run --maxWorkers=1`: 171 passed/23 files;
  `pnpm --dir web build`: TypeScript and production build passed.
- `pnpm --dir web exec playwright test e2e/community-recovery.spec.ts
  e2e/community-rotation.spec.ts e2e/community-settings.spec.ts`: eight passed.
  Axe/focus/Escape/overflow and privacy checks at 320px/1440px pass. Inspected
  `web/test-results/css33-confirm-320.png` and `css33-completed-1440.png`.
- Node validated all 120 local links, Markdown fences and shared fixture JSON
  in the five edited documentation files.
- Final changed-file Ruff, editor diagnostics and `git diff --check` passed.
  Frontend-only Vite preview is `http://127.0.0.1:5177/`; no backend was started.

All execution used temporary synthetic identities/stores and mocked provider/browser
responses. No real Discord login, Cloudflare setup/deployment, existing database,
user identity/media/model or schema migration was touched. Live isolates/replication,
native Windows ACL/locking, non-Chromium and packaged-runtime UI remain unverified.
Retained metadata/matching backup identifies a lost installation; a clean reinstall
registers separately under the same contributor. Lost Discord transfer is unsupported.
No remaining CSS-33 scoped work; CSS-25 remain partial downstream tasks.

Next exact action: resume [CSS-34](34-media-identity.md), already IN PROGRESS.
Read its existing typed-normalizer handoff and collect sanitized real upstream
response evidence before extending its Other Files confirmation UI. Do not infer
episode namespaces or implement CSS-35 fingerprint/tolerance choices.

Direct dependent tasks: [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md).
