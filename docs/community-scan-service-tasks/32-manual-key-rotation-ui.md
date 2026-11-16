# CSS-32 — Add user-triggered key rotation and recovery of interrupted attempts

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE** (October 1, 2026), local/mock scope. Live deployment and
Discord-assisted lost-key recovery remain separate tasks.

- Prerequisites: [CSS-14](14-local-key-storage.md), [CSS-29](29-local-service-client.md), [CSS-30](30-connection-settings.md), [CSS-31](31-key-rotation-service.md)
- Decision gates: None beyond the shared rules.
- Parent sections: main D18; contracts §1; implementation §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Only the explicit Rotate key action creates a replacement. Startup/upgrade/restore/Discord login must never initiate it.

## Starting points

scanner/security/ private pending key lifecycle; scanner/integrations/community/;
web/src/settings/community/; backup status integration. Read the implemented
[CSS-31 exact proof/reconciliation contract](decision-evidence/31-key-lifecycle-contract.md)
before designing the durable pending journal or transport.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Persist new private key and operation ID safely before submission; serialize against snapshots and other identity changes.
2. Add confirmation explaining that copies of this installation's old key lose access while other separately registered installations retain access; submit via old/new proof and reconcile an interrupted existing operation.
3. Activate local replacement only with verified service outcome and prompt for a fresh backup; encryption remains optional and off by default.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] No hidden scheduler, CLI, operator, restore or startup rotation trigger exists.
- [x] Lost response/restart reuses the same replacement and operation rather than generating another key.
- [x] Old credentials cease working immediately at service activation; pending/failed/success UI is accurate.

## Verification

- Browser/API tests for explicit action and cancellation; fault-inject before send, after commit and during local persistence.
- Verify backup captures one consistent generation and retired-key restore remains retired.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide manual rotation flow, pending-state recovery steps and fresh-backup reminder behavior to CSS-33/66.

## Handoff summary for the next task

DONE / October 1, 2026 / GitHub Copilot / uncommitted working-tree changes.
Confirmed D18/D21/G-KEYS: explicit UI action, independent installation keys,
immediate hosted retirement, preserved account/history and optional encryption.
No new product policy, migration, automatic trigger or recovery bypass.

### Implementation and contracts

- [Private journal](../../scanner/security/community_key_rotation.py) persists one
  candidate, operation UUID, service audience and exact dual-proof body atomically
  before send. [Proof validation](../../scanner/security/community_rotation_proofs.py)
  matches CSS-31's independent vector. Candidates never change on reload/retry.
- [Identity store](../../scanner/security/community_identity.py) accepts optional
  version-1 `rotation`/`key_file` fields in `community-identity-1`. Preparation stores
  candidate private bytes in the owner-private manifest, avoiding an unrecorded-file
  crash window. Activation writes the inactive existing slot then atomically selects
  it with the new public identity, completed result and cached profile key ID.
  Candidate private bytes leave the manifest on completion. The inactive old private
  slot may remain until the next rotation but is excluded from completed backups.
  Legacy manifests remain readable; the old staging API refuses journaled generations.
- [Rotation transport](../../scanner/integrations/community/rotation.py) holds a
  nonblocking interprocess `rotation.lock` and maintenance worker lease across
  send/probe/activation/projection. Exclusive backup capture cannot split that operation.
  [Client state](../../scanner/security/community_client_state.py) blocks competing
  enrollment/rename writes while a replacement is pending. Both locks are excluded
  from archives. No new file containing persistent private material or SQL schema.
- [Client](../../scanner/integrations/community/client.py) and
  [repository](../../scanner/persistence/repository/repository_community.py) repair
  a completed rotation's interrupted SQLite projection only for the matching
  service/contributor/installation and verified old key. Sharing consent is preserved;
  unrelated identity mismatches remain errors. Contributor identity never changes.
- [Protected API](../../scanner/http/community_api.py) adds
  `POST /api/community/keys/rotate`, `rotation-status`, and `rotation-retry`.
  All require an actual administrator session and same-origin/CSRF even with bypass.
  New send/retry require `{ "confirmed": true }`; status never sends a rotation.
  Public status adds nullable `pendingRotation` (operation/new fingerprint/state)
  and `rotationCompleted` (operation/current fingerprint/commit milliseconds).
  A noncommitted probe adds `rotationStatus: "not_committed"`.
- [Settings controls](../../web/src/settings/community/community-rotation.tsx) and
  [state](../../web/src/settings/community/community-state.ts) use typed local API
  commands. Rotate key requires verified access and no competing pending intent.
  Confirmation discloses immediate access loss for old-key copies, sibling-installation
  independence, preserved account history and fresh backup. Cancel/Escape sends nothing.
  No candidate private bytes or detached proofs enter the browser.

### Recovery runbook and fresh backup

Reload shows the saved operation/new fingerprint without sending or probing and
suppresses ordinary old-key profile refresh/name editing. **Check rotation** signs
the exact retained body/operation with the replacement. `completed` activates
locally only with the exact expected outcome. `not_committed` is a read snapshot,
never cancellation or permission to discard. **Retry saved rotation** requires
confirmation, probes first and resends the original old-key signed body only if
not committed. Outer timestamps/nonces change; candidate/operation/body/proofs do not.
The actual CSS-31 routes return `{version:1,state,result}` envelopes, validated here.

Outage, membership failure, inactive replacement, conflict, malformed outcome and
local persistence failure retain the journal. There is no post-send discard,
guessed activation, second candidate or retired-key access exception. Bounded
CSS-29 retries may encounter immediate old-key denial after an uncertain commit;
the retained replacement can still reconcile. Activation faults leave old-current
plus pending or new-current plus completed evidence. Explicit check/status repairs
the same generation and database projection. If the replacement is itself retired/
revoked, CSS-33 owns fresh Discord recovery; do not delete identity files.

Success retains public completion metadata and prompts for a fresh identity backup
on later visits, honestly stating backup freshness is not verified. The existing
Settings > General link supports both kinds; encryption remains optional/unchecked.
No passphrase or invented backup timestamp is persisted. Both full/settings exports
retain a consistent active slot plus pending candidate/journal in plain/encrypted
modes. [Restore inspection](../../scanner/backup/restore.py) follows the selected slot;
restore never sends or activates a key. Restored old backups keep their retired key
and hosted authorization denies it. No CLI/operator/startup/restore/scheduler trigger.

### Evidence and limitations

- `.venv/bin/python -m pytest -q` with
  `tests/test_community_{rotation,rotation_backup,rotation_contract,identity,client,client_state,client_contract,api,local_status,transport,repository,request_signatures}.py`,
  `tests/test_auth_policy.py`, `tests/test_portable_backup.py`,
  `tests/test_backup_restore.py`, and `tests/test_backup_activation.py`: 194 passed.
  Covers lost response/restart, cancellation before send, exact not-committed retry,
  key/manifest/projection faults, overlapping probes, second rotations, foreign/
  malformed results, failure retention, independent proof vectors, public DTOs,
  12 generation/kind/encryption round trips and retired restore denial.
- Final legacy staging guard/auth formatting cleanup rerun: 68 focused identity,
  rotation, API and auth tests passed. Touched Python Ruff checks pass.
- `pnpm --dir web exec vitest run --maxWorkers=1`: 167 passed across 22 files.
  `pnpm --dir web build`: TypeScript and production build passed.
- `pnpm --dir web exec playwright test e2e/community-rotation.spec.ts
  e2e/community-settings.spec.ts`: six Chromium tests passed at 320px/1440px,
  including confirmation/Escape focus, passive reload, saved retry, public-only
  DOM/storage and unchecked encryption. Axe/overflow checks pass; inspected
  `web/test-results/css32-{confirm,pending,completed}-{320,1440}.png` (ignored output).
- Editor diagnostics and `git diff --check` passed. Hosted envelope parsing,
  an unavailable icon import, the legacy active-slot restore check and lint defects
  were repaired and focused checks passed. No known scoped failure remains.
- After final status wording, 21 focused frontend tests passed. Node checked all
  122 local links and Markdown fences in the four edited documentation files.
  Application call-site search found candidate preparation only in the confirmed
  local rotation handler, not startup, restore, CLI, scheduler or operator code.
  Frontend-only Vite preview: `http://127.0.0.1:5176/`; no backend was started.

All stores/keys/provider responses are isolated and synthetic. No live Discord,
Cloudflare resource, user database/identity, model, deployment or existing-schema
migration was touched. Hosted immediate retirement/sibling isolation relies on
CSS-31's verified authoritative D1 contract, not a claimed live-isolate measurement.
Native Windows locking/ACLs, non-Chromium and packaged-runtime UI remain unverified.
No remaining CSS-32 work; CSS-25 downstream scope is still partial.

Next exact action: CSS-33. Read its brief, G-RECOVERY/G-KEYS and CSS-28/31/32 handoffs;
extend explicit fresh-Discord contributor/installation/candidate-bound lost-key
recovery without weakening normal enrollment, manual rotation or restored-key
retirement. Reuse journal/publication/confirmation conventions, but never pretend
the old-key proof in normal rotation can recover an actually lost private key.

Direct dependent tasks: [CSS-33](33-identity-recovery.md), [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md).
