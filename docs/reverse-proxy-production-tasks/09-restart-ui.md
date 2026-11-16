# RP-09 — Add Restart Editarr button and reconnect flow

Dependencies: RP-07, RP-08. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

The user explicitly requested **Restart Editarr** at the top right of General
Settings. Save and Restart are separate. Restart must work without pending changes,
apply only saved values, and avoid misleading success while the proxy is unavailable.

## Read and expected touch points

Read RP-07's component, web/src/App.tsx General heading, api.ts, auth-gate.tsx,
styles.css, and RP-08's real restart DTOs/capabilities. Preserve existing safe hash
return handling and the single authentication gate.

## Implementation steps

1. Add the exact button label at the top right of the General page heading, visible
   only in the applicable settings context. Wire it to the protected restart helper.
2. Show only saved changes will apply; do not implicitly submit dirty deployment,
   account, or mapping forms. Follow RP-00's approved dirty-form handling.
3. Implement request-pending, restarting, reconnecting, and failure/unsupported states.
   Disable duplicate clicks while a restart is pending and report rejection normally.
4. After acknowledgment, use the server's resolved next-start destination and the General
   hash. Do not synthesize it from the upstream Port or a dirty input.
5. Distinguish same-origin readiness from cross-origin navigation. Do not enable
   broad CORS or leak CSRF/session data to a new origin to poll readiness. A new
   origin can require navigation/sign-in; provide a usable link and truthful state.
6. Avoid mistaking the old still-running server for successful restart. Use RP-08's
   completion/generation contract, including explicit failed-change/recovery outcome.
   An advanced generation on the old snapshot does not mean new settings applied.
   Bound retries and leave a retry/open-target path
   when the proxy has not yet been updated or startup failed.
7. Let the normal auth gate re-establish session status on return, especially after
   cookie path/origin changes. No credentials or tokens in URLs/storage.
8. Keep the action usable without pending settings and accessible at narrow widths.

## Localhost requirement update

Support permanent localhost reconnect without a Base URL or setup-URL prompt.
Local Port changes update the server-provided effective URL; proxy upstream Port
changes must leave an explicitly configured public destination unchanged.

## Acceptance criteria

- [x] Exact button label and top-right General placement match the request.
- [x] A click invokes real restart once; it does not reload the page as a substitute.
- [x] Unsaved edits are not persisted or used as the reconnect target.
- [x] Same-origin and changed-origin/prefix flows have accurate progress/failure behavior.
- [x] Changed upstream Port never replaces public proxy port in reconnect URLs.
- [x] Session expiry/new-origin sign-in uses existing auth flow without exposing protected UI.
- [x] No infinite polling, cross-origin credential leakage, or duplicate restart requests.
- [x] Unsupported mode and proxy/bind failure states give an actionable recovery path.

## Verification

Component-test button placement, dirty state, no-pending restart, duplicate clicks,
endpoint failures, timeouts, old-server responses, CLI override, and new-origin
navigation. Run affected frontend tests and pnpm build. RP-13 must later exercise
this button against RP-08's actual process restart, not an intercepted fake endpoint.

## Definition of done

Shared DoD applies. The UI fully implements RP-08's restart lifecycle with accessible,
truthful states. Record reconnect decisions and test coverage for production verification.

## Handoff

Status: DONE

Audit September 4, 2026: Reviewed the heading/dirty-state integration, protected API
helpers, generation/outcome checks, same-origin polling, cross-origin links, and auth
gate transitions against RP-00/RP-07/RP-08. Fixed an unbounded-network gap: each POST
and target/recovery GET now has a five-second deadline covering response headers and
body parsing. Requests and poll delays are cancelled on unmount; late responses
cannot navigate or update this flow. A stalled target still allows the old endpoint
to report recovery, and stalled recovery requests consume the existing bounded retry
budget. A missing POST acknowledgment now explicitly says the restart may already
be happening, disables repeat requests, and offers an explicit General Settings
reload without inventing a reconnect target. Cross-origin status observation reports
when its bounded checks stop. Credentials, CSRF attachment, and deferred auth-gate
navigation retain the existing boundaries; no new public/server contract is needed.

Audit verification: `pnpm exec vitest run src/restart-editarr.test.tsx src/api.test.ts
src/deployment-settings.test.tsx src/auth-settings.test.tsx src/App.test.tsx
src/auth-gate.test.tsx` passed 97 tests. New regressions cover a stalled target with
successful recovery, stalled target JSON and recovery transport exhaustion, ambiguous
POST acknowledgment with late response/duplicate suppression, and unmount during
POST, GET, and poll delay. `pnpm build` and `git diff --check` passed. Audit changes
are confined to `web/src/restart-editarr.tsx`, its tests, `web/src/api.ts`, and this
handoff. No live instance or user data was touched. No remaining RP-09 blocker found;
real production-browser verification remains RP-13's responsibility.

Last completed substep: Ran the full frontend suite and a production TypeScript/Vite
build after covering the controller lifecycle, changed-prefix recovery, cross-origin
handoff, deferred reauthentication, and StrictMode behavior.

Files changed: `web/src/restart-editarr.tsx`, `web/src/restart-editarr.test.tsx`,
`web/src/api.ts`, `web/src/api.test.ts`, `web/src/App.tsx`,
`web/src/deployment-settings.tsx`, `web/src/deployment-settings.test.tsx`,
`web/src/styles.css`, this task, and the task-pack checklist.

Contract/API changes: The frontend now exposes typed `fetchRestartCapability` and
`requestEditarrRestart` helpers through the existing credential/CSRF boundary. Restart
polling may defer the shared authentication-state broadcast so a new-prefix 401 can
navigate to the server-provided target before the normal auth gate remounts. The General
Settings header owns the exact Restart Editarr action and aggregates deployment,
administrator/security, and mapping draft state without submitting any form. After a
202, same-origin targets are polled only to `generation >= target_generation` plus
`outcome: applied`. A distinct current-origin channel can report target-generation
`recovered` or `failed` when a changed prefix/origin is unavailable; it can never prove
that a different target was applied. Cross-origin targets are never polled and receive
no credentials; the UI presents the exact server URL while bounded current-origin
polling watches only for recovery/failure.

Verification:

- `pnpm exec vitest run src/restart-editarr.test.tsx src/api.test.ts
  src/deployment-settings.test.tsx` — 29 tests passed.
- `pnpm test` — 9 files and 113 tests passed.
- `pnpm build` — TypeScript and Vite production build passed.
- `pnpm exec vitest run src/restart-editarr.test.tsx src/api.test.ts
  src/deployment-settings.test.tsx src/auth-settings.test.tsx src/App.test.tsx
  src/auth-gate.test.tsx` — 6 files and 90 tests passed after the final build fix.
- `git diff --check` — passed.

Acceptance unmet: None. RP-13 still owns browser layout and real-process button
verification through the production proxy fixtures.

Approvals: No new product/architecture choice or database change was needed.

Known limitations: A cross-origin successful restart cannot be verified by credentialed
browser polling under the approved security model. The server-provided target remains
available for navigation, while recovery on the previous origin is reported when it is
observable.

Next exact action: RP-10 can build the reproducible release frontend/package artifact;
RP-13 later exercises this exact button against the real RP-08 controller.
