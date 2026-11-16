# CSS-30 — Build Discord connection and identity settings UI

Status: **DONE** (October 1, 2026). Implemented and verified locally with mocked
hosted/Discord flows. Live provider setup/deployment remains the operator checklist.

- Prerequisites: [CSS-20](20-backup-settings-ui.md), [CSS-28](28-enrollment-profile.md), [CSS-29](29-local-service-client.md)
- Decision gates: None beyond the shared rules.
- Parent sections: main §5; Discord §3.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Ordinary enrollment must ship with a usable backup path supporting optional encryption, off by default. Generated/custom names remain independent of Discord display names.

## Starting points

web/src/settings/community/; web/src/api/; local safe status endpoints.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Build enable/connect/status/profile screens with hosted sign-in launch, connection confirmation, retry/cancel and enrollment-required states.
2. Show contributor ID/key fingerprint/backup status and shared contributor identity without exposing Discord IDs or secrets.
3. Handle existing identity, missing key, revoked membership and provider outage distinctly; link to backup UI and recovery only when implemented.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Connection cannot silently replace a key or toggle automatic donation.
- [x] Keyboard/mobile flow works, progress survives refresh, and secret material never appears in DOM/browser storage.
- [x] Disabled community service leaves local workflows usable.

## Verification

- Component/API tests for all enrollment states plus one mocked end-to-end Discord handoff browser test.
- Run axe/focus/responsive checks and local auth enforcement tests.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide UI state model and connect/profile affordances for CSS-32/33/46; verify backup access before exposing enrollment.

## Handoff summary for the next task

### Outcome and module map

DONE / October 1, 2026 / GitHub Copilot. Working-tree changes; no commit requested.
Settings > Connections now includes an unframed Community scans section. Service
configuration remains the CSS-29 administrator deployment configuration; connecting
is explicit and never enables donation or creates/replaces a signing key.

- [Panel](../../web/src/settings/community/community-settings.tsx): hosted launch,
  trusted public reference input, explicit local confirmation modal, resume/retry/cancel,
  public installation/key state, and backup navigation.
- [State hook](../../web/src/settings/community/community-state.ts): local-first reload,
  separate current-access verification, safe failure copy and serialized actions.
- [Profile editor](../../web/src/settings/community/community-profile.tsx): current
  name/revision, full and short contributor IDs, shared contributor identity,
  validation and exact durable rename retry/discard. Names render as text.
- [Integration](../../web/src/settings/settings-view.tsx),
  [styles](../../web/src/styles/community.css), and
  [API wrappers](../../web/src/api/api-community.ts) preserve existing Settings/backup routing.
- [Local API](../../scanner/http/community_api.py),
  [client](../../scanner/integrations/community/client.py), and
  [auth inventory](../../scanner/security/auth_policy.py) add the protected local status
  read and public identity projection. No schema/migration changes.
- Hosted [page](../../community-service/src/integrations/discord/pages.ts) and
  [callback](../../community-service/src/integrations/discord/oauth.ts) expose only the
  verified public state reference, preserving no-store/referrer/CSP protections.

### State and downstream affordances

`GET /api/community/connection/status` is network-free, no-store and requires an
actual administrator session even under local/external bypass. Its safe DTO adds
`serviceUrl` and `identity` (`installationId`, `keyId`, `lastKnownKeyState`). It
retains cached profile, pending enrollment and exact pending rename without secrets.
When connected, the panel subsequently uses signed `GET /connection` to verify
current access and refresh account changes made by another installation.

States: loading; unconfigured; unconnected/enrollment-required; awaiting hosted
authorization; public-reference review; explicit confirmation; durable pending
confirmation after uncertain response; currently verified connected; cached profile
with access paused; identity unavailable/recovery required. Missing/corrupt/pending
keys, inactive keys, wrong service, membership revocation and provider/network
outage have distinct safe notices. Identity failures block new enrollment and never
offer a guessed recovery or rotation action. Cached profile name editing remains
disabled until signed refresh succeeds. Reload never automatically completes an
enrollment or sends a rename.

Hosted authorization opens a no-referrer/no-opener browser link so the broker can
set its HttpOnly cookie. The user supplies the public 64-hex reference or a trusted
same-service confirm/callback URL. Duplicate state parameters, wrong origins/paths,
credentials and fragments are rejected. Trusted URLs immediately become the bare
reference, dropping provider codes. The local dialog shows installation/fingerprint/
reference, traps/restores focus, and sends only state plus explicit approval to the
CSRF-protected backend. The backend retains and sends the secret privately.

Name changes use one UUID and exact intended name through response loss and reload;
pending names are read-only until retry or explicit discard. Discard clears only
the local intent, not a hosted mutation. Refresh verifies current state before
editing again. CSS-32/33 should extend these feature modules using the durable
identity/journal APIs and explicit approval pattern, not put key logic in the UI.
CSS-46 should use verified current access, retain shared contributor identity, and keep
donation consent independent of connection.

Backup access is visible in all states, including disabled/missing identity, and
links to Settings > General. Browser tests navigate it from connected state and
verify `Encrypt backup` remains unchecked; the existing full/settings and encrypted/
plain browser tests also pass. No backup-history endpoint exists, so the panel
honestly says backup status is not verified. No forced backup/encryption policy added.

### Evidence and limitations

- `pnpm --dir web exec vitest run --maxWorkers=1`: final run 164 passed across
  22 files; the focused lifecycle run passed all 15 panel tests.
- `pnpm --dir web exec vitest run src/settings/community/community-settings.test.tsx
  src/api/api-community.test.ts`: 16 passed before those two additions.
- `pnpm --dir web exec playwright test`: all 67 Chromium tests passed. New mocked
  hosted/local journeys at 320px/1440px cover approval, focus/Escape restoration,
  lost-response reload, exact rename ID retry, escaped names, secret-free DOM/storage,
  disabled-service local catalog access and backup navigation. Axe/overflow checks
  pass in confirmation, connected and disabled states. Screenshots were inspected
  at `web/test-results/css30-{confirm,connected}-{320,1440}.png` (ignored output).
- `.venv/bin/python -m pytest -q tests/test_community_local_status.py
  tests/test_community_api.py tests/test_community_client.py tests/test_auth_policy.py`:
  38 passed on temporary databases, including local/external bypass rejection.
- `pnpm --dir community-service exec vitest run test/discord-handoff-page.test.ts
  test/discord-oauth.test.ts test/enrollment-profiles.test.ts`: 27 passed.
- `pnpm --dir web build`, hosted `tsc --noEmit`, editor diagnostics and touched
  Python Ruff checks passed. Initial mocked redirect/ambiguous Jellyfin status
  selectors and missing test newline were repaired and rerun successfully.
- 129 edited-document local links/fences and `git diff --check` passed. A frontend-only
  Vite server was left at `http://127.0.0.1:5175/`; it does not start a backend or
  certify a configured/live community service.

No live Discord/Cloudflare calls, production deployment, existing database migration,
user-data/reset, backup creation or key rotation occurred. Current service deployment,
provider credentials, live membership verification and non-Chromium/native packaging
are not certified by these mocked tests. Recovery/rotation, actual contribution
sharing and donation licensing remain their owning tasks. No remaining CSS-30 work.

Next: CSS-31. Read its brief, CSS-28's active-key/operation handoff, CSS-25's partial
shared-auth handoff and G-KEYS before extending hosted atomic rotation/revocation.
Resolve any prerequisite scope/decision gaps there; do not start CSS-32 before its
service rotation dependency is complete.

Direct dependent tasks: [CSS-32](32-manual-key-rotation-ui.md), [CSS-33](33-identity-recovery.md), [CSS-46](46-manual-sharing-ui.md), [CSS-66](66-integration-and-abuse.md), [CSS-67](67-packaging-and-user-docs.md).
