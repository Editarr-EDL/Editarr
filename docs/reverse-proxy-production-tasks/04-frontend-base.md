# RP-04 — Make frontend URLs runtime-configurable

Dependencies: RP-00. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

One frontend build must work at any configured Base URL prefix. web/src/api.ts
hardcodes /api both for requests and CSRF matching, and index/login/setup/app icons
use root paths. React navigation is hash-based; do not replace the router.

## Read and expected touch points

Read web/src/api.ts and api.test.ts, main.tsx, auth-gate.tsx, auth-login.tsx,
auth-setup.tsx, App.tsx, index.html, vite.config.ts, and existing auth component tests.
Create a small shared runtime URL helper according to RP-00's bootstrap contract.

## Implementation steps

1. Implement validated runtime bootstrap consumption and shared helpers for API
   and static URLs. Initialize before the first authentication request/imported
   module computes a URL. Use RP-00's explicit dev/test bootstrap, not silent
   production fallback after a malformed/missing bootstrap.
2. Configure the build/template so the first script, stylesheet, and icon URLs work
   under the deployment prefix. Setting only the API constant or Vite base is insufficient.
   RP-05 will inject/serve the actual runtime configuration; fixture it here.
3. Update all API helpers and unsafe-request classification. Handle string, URL,
   and Request inputs consistently. Bootstrap login/setup exceptions must be
   relative to the configured API root.
4. Only attach CSRF to same-origin application API requests, with path-boundary
   matching: /editarr/api2 is not /editarr/api. Keep credentials same-origin.
5. Update shell/login/setup icons and other application-owned URLs. Consume already
   prefixed backend URLs without adding the prefix twice. Preserve external URLs.
6. Preserve safe internal hashes and setup/login return destinations; base prefix
   belongs before #. Clear auth state as before on the established errors.
7. Extend relevant tests using root and nested bootstrap fixtures and reset shared
   module state between cases. Leave General Settings and restart UI to later tasks.

## Acceptance criteria

- [x] Same source build works with root and nested runtime bootstrap values.
- [x] Initial HTML assets as well as subsequent API calls use the active prefix.
- [x] No double-prefixing of artwork or other URLs returned by the API.
- [x] CSRF is attached to intended unsafe API calls and excluded from external,
      bootstrap, safe-method, and similarly named unrelated paths.
- [x] Login/setup hash destinations and existing auth-gate behavior are unchanged.
- [x] Bootstrap contains no secrets and invalid production bootstrap fails clearly.

## Verification

Run pnpm test -- src/api.test.ts plus the affected auth/App tests and new URL helper
tests, then pnpm build. Inspect emitted HTML/assets against the agreed runtime
bootstrap design. These checks do not replace RP-13's real production-browser test.

## Definition of done

Shared DoD applies. Runtime helper/bootstrap interface is documented in the handoff;
production server RP-05 can provide it without frontend rebuilds. Relevant component
tests and the TypeScript/Vite build pass.

## Handoff

Status: DONE
Audit September 4, 2026: Runtime bootstrap path validation now matches the user's
  approved ASCII-only path-segment syntax; malformed percent/non-ASCII/reserved and
  traversal paths fail clearly. The six affected frontend suites passed 90 tests,
  and the TypeScript/Vite production build passed.
Last completed substep: Added the validated runtime bootstrap consumer, made all
  frontend API/static URLs prefix-aware, and made the built entry document use
  relative initial asset paths.
Files changed: web/src/runtime-urls.ts; web/src/runtime-urls.test.ts;
  web/src/vite-env.d.ts; web/src/api.ts; web/src/api.test.ts; web/src/App.tsx;
  web/src/auth-login.tsx; web/src/auth-setup.tsx; web/src/main.tsx;
  web/src/test/setup.ts; web/index.html; web/vite.config.ts;
  docs/reverse-proxy-production-tasks/04-frontend-base.md;
  docs/reverse-proxy-production-tasks/README.md (RP-04 checklist only).
Contract/API changes: `runtime-urls.ts` consumes exactly one inert
  `#editarr-bootstrap[type="application/json"]` object with no fields other than
  `{ version: 1, basePath, apiBasePath }`; `basePath` must be canonical and
  `apiBasePath` must exactly equal `${basePath}/api` (or `/api` at root). It exposes
  `initializeRuntimeUrls()`, `runtimeApiBasePath()`, `applicationAssetUrl()`, and
  `applicationUrl()`. Missing/malformed bootstrap and the development fixture in a
  production build fail before React starts and display a clear alert. All API helpers
  resolve `runtimeApiBasePath()` when called. CSRF is sent only for same-origin unsafe
  requests within that API path boundary, excluding setup/login. `applicationUrl()`
  leaves external URLs and already-prefixed backend application URLs unchanged while
  adding the active prefix once to an unprefixed same-origin application URL.
Verification commands and results: In `web/`, `pnpm test` -> 7 files, 88 tests
  passed; `pnpm build` -> passed (TypeScript and Vite v6.4.3); inspected final
  `dist/index.html`: bootstrap precedes module script and all module, stylesheet, and
  icon references use `./...`; `git diff --check` -> passed.
Acceptance criteria still unmet: None.
Approvals received or outstanding: RP-00's exact bootstrap schema and explicit
  production-vs-development fixture policy were implemented. No new approval is
  needed; no database, deployment configuration, process, or unrelated change was made.
Known failures/limitations: This task deliberately does not serve production assets,
  inject active bootstrap data, expose deployment settings, or alter restart UI.
  `dist/` remains ignored build output and is not packaged.
Next exact action: RP-05 must serve canonical `/prefix/` entry HTML and replace the
  source fixture `<script id="editarr-bootstrap" type="application/json"
  data-editarr-bootstrap="development">…</script>` with a safely serialized,
  attribute-free active-snapshot object exactly shaped as
  `{ "version": 1, "basePath": "/prefix", "apiBasePath": "/prefix/api" }`
  before the module script. It must not leave the fixture marker in production, and
  must preserve the built `./assets/...`, `./favicon...`, and stylesheet references.
  RP-07 must add deployment helpers through `apiRequest`/`runtimeApiBasePath()` and
  must not mutate the current bootstrap/global API base when saving pending settings.
  RP-11 owns a prefixed Vite development topology: it must supply an explicit matching
  non-production bootstrap and matching API proxy path (or refuse an incompatible
  saved public configuration); `base: "./"` and the root development/test fixture are
  already established here.
