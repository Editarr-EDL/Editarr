# RP-07 — Build deployment settings UI

Dependencies: RP-04, RP-06. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

Add Base URL and listening Port controls to General Settings. Existing General
Settings contains security and media path mappings; preserve both. Users must
understand saved pending changes and the independent public versus upstream ports.

## Read and expected touch points

Read web/src/App.tsx SettingsView, styles.css, api.ts, auth-settings.test.tsx,
App.test.tsx, and the RP-06 API contract. Prefer a focused settings component to
adding another large responsibility to App.tsx. Restart behavior belongs to RP-09.

## Implementation steps

1. Add typed API helpers through the established apiRequest wrapper and a deployment
   section under General Settings. Load the real active/next-start response.
2. Provide labeled Base URL and Port inputs, derived path/origin displays, source/
   override information, and current versus pending values. Never offer independent
   prefix editing or change trusted proxies into editable UI settings.
3. Track edits per field; ensure saving Port does not freeze Base URL seed state.
   Support explicit Base URL save semantics agreed in RP-00, including saving the
   currently displayed seed and saving a no-prefix URL.
4. Save validated edits through RP-06 and display server errors inline. Do not
   optimistically change global API base, auth origin, or current browser URL.
5. Explain that saved deployment changes take effect on restart and that external
   proxy changes are managed separately. Show when --port masks a saved Port.
6. Keep dirty values across recoverable errors, avoid duplicate submissions, and
   retain existing shared 401/423 auth-gate behavior.
7. Provide an integration point at the top-right General heading for RP-09's restart
   action; do not add a fake reload button or an independent restart implementation.

## Localhost requirement update

Base URL is optional. Display effective localhost address separately without a
required-field error or setup-URL prompt. Do not persist it on unrelated saves.
Port changes update implicit local URLs but not explicitly configured public URLs.

## Acceptance criteria

- [x] Base URL/Port appear on General Settings with accessible labels and keyboard use.
- [x] Derived path shows / for URLs with no path and handles nested prefixes.
- [x] Saving Port alone preserves environment Base URL seed semantics.
- [x] Saved values and currently active values remain visibly distinct until restart.
- [x] Changing upstream Port does not rewrite public Base URL.
- [x] Invalid input and server errors are clear; no silent form or global-state reset.
- [x] Existing account/security/path-mapping behavior remains functional.
- [x] Layout works at desktop and narrow widths without horizontal overflow.

## Verification

Add component tests using RP-06 DTO fixtures for seeds, saved root, pending values,
CLI override, invalid URL/Port, save failures, and auth expiration. Run affected
auth-settings/App/api tests and pnpm build. Browser layout verification is completed
in RP-13; record any local visual check honestly.

## Definition of done

Shared DoD applies. The UI edits real deployment settings with correct per-field
semantics and active/pending display. RP-09 receives the heading integration point,
dirty-state behavior, and saved reconnect data contract.

## Handoff

Status: DONE

Audit September 4, 2026: In implicit-local mode, the optional Base URL input remains
blank and the derived localhost URL appears only in active/next-start details. The
explicit displayed-seed checkbox is shown only for an environment seed. Added a
component regression proving a port-only save retains the blank input and updates
the read-only effective local address. Six affected frontend suites passed 90 tests;
TypeScript/Vite production build passed.

Last completed substep: Added the Deployment section to General Settings, including
local Base URL/Port drafts, sparse protected API saves, active-versus-next-start
snapshots, explicit override/source disclosures, and RP-09's inert heading slot.

Files changed: `web/src/deployment-settings.tsx`,
`web/src/deployment-settings.test.tsx`, `web/src/api.ts`, `web/src/api.test.ts`,
`web/src/App.tsx`, `web/src/styles.css`, and `web/src/auth-settings.test.tsx`.

Contract/API changes: Consumes RP-06's `DeploymentConfiguration` with typed
`fetchDeploymentConfiguration` and `saveDeploymentConfiguration` helpers. The form
sends only fields changed by the operator; a blank changed field sends `null`. A Port
save never includes Base URL unless the operator changed it or explicitly selected
the displayed-seed override checkbox. The component never changes runtime bootstrap,
browser location, or active configuration. The empty
`data-restart-editarr-slot` at the General heading is an integration point only.

Verification:

- `pnpm exec vitest run src/deployment-settings.test.tsx src/api.test.ts` — 18 tests passed.
- `pnpm exec vitest run src/deployment-settings.test.tsx src/api.test.ts src/auth-settings.test.tsx src/App.test.tsx src/auth-gate.test.tsx && pnpm build` — 79 tests passed; production build passed.
- `git diff --check` — passed.

Acceptance unmet: None. Browser layout and production/restart end-to-end checks remain
intentionally assigned to RP-13; no manual browser visual check was performed here.

Approvals: No new decisions or migrations. Implements the approved RP-00/RP-04/RP-06
contracts.

RP-09 integration: Place the real Restart Editarr action in the General heading's
`data-restart-editarr-slot` (or replace that empty slot). It must not submit this
component's local form or infer a reconnect target from unsaved inputs; use its own
saved server response and the actual RP-08 restart endpoint.

Next exact action: RP-09 implements the restart/reconnect UI after RP-08 is complete.
