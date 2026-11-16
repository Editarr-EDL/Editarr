# Reverse proxy and production serving implementation plan

Status: **Implemented and release-verified September 5, 2026.** Production serving,
UI-editable port/Base URL, URL-derived base path, saved-setting precedence,
configuration-file persistence, CLI port override, redirect/trust strategy, packaging,
and parent-controlled restart are delivered as approved. Base URL is optional; the
permanent localhost default is supported. `EDITARR_AUTH_PUBLIC_ORIGIN` was removed with
no compatibility path, and configured Base URL origin/prefix transitions apply the
approved forms-session policy. This document remains the approval and design record;
the [task pack](reverse-proxy-production-tasks/README.md) contains the implementation
and verification handoffs. Any future architecture change still requires approval.

Multi-user support later removed the local-address and external-authentication
bypasses and the single-administrator model. Every client now signs in with its own
account or API key; see [Run Editarr for several users](multi-user-guide.md). This
plan is kept as a historical record.

Release evidence: the fresh current-worktree `editarr-0.1.0` wheel
(`4ae7a2237d245303a2a332dedab156839552d3973a982b00388242c2cfff0c05`) and sdist
(`3b2b53fda9bc2aaeedfa76a3253230f07373e405a522846a5e69d86790b43ee8`) were built
offline, the wheel was installed without dependency resolution in a fresh temporary
environment, and the real Nginx production Chromium flow passed. The broader final
suite recorded 441 backend tests, 121 frontend unit tests, 30 mocked browser tests, a
production build, and real Nginx root/nested plus Caddy root TLS flows. See RP-13 for
the exact commands, artifact environment, test-owned fixture boundaries, and limitations.

Coding task breakdown: [Reverse proxy and production serving task pack](reverse-proxy-production-tasks/README.md).
The pack contains dependency-ordered assignments, shared background, approval gates,
acceptance criteria, verification steps, and definitions of done for individual
coding subagents. Task progress is tracked there; this document remains the parent
design and approval record.

## Objective

Support a complete production Editarr deployment at either a dedicated hostname
(`https://editarr.example.com/`) or a path on a shared hostname
(`https://example.com/editarr/`). Cover frontend serving, authentication, assets,
API URLs, redirects, proxy trust, packaging, configuration visibility, and tests.
Also support permanent standalone localhost operation with no configured Base URL,
no proxy, and no manual HTTPS/cookie configuration.

## Original findings (resolved by RP-01 through RP-13)

- The API did not serve the built web application; releases now package and serve the
  validated frontend with the API while retaining `--api-only` for development.
- The Python wheel had no frontend output; the release build stages validated Vite
  output under the installed package and rejects incomplete production artifacts.
- Frontend requests, icons, and generated artwork links assumed root; runtime bootstrap
  and application URL helpers now carry the active prefix exactly once.
- Literal authorization paths required an explicit public-static inventory; only the
  production shell/assets/icons are public while docs/data remain protected.
- Public origin needed to become Base-URL-derived without trusting request headers; the
  resolver now does this while the trusted-peer address policy remains independent.
- Production and development launch paths now keep Uvicorn proxy-header rewriting
  disabled; Vite preserves the matching path/peer topology explicitly.
- Framework redirects are now rewritten as prefix-aware origin-relative locations.
- Existing Playwright tests start Vite's development server. They do not validate
  the packaged production server behind a TLS proxy.

## Decision record

### D1: Production serving model

Confirmed: Editarr serves its built frontend and API from one HTTP-service child
and port. A standalone parent controller manages that child's restart lifecycle.
An external reverse proxy handles public TLS. Bundle frontend assets in the release
artifact, keep Vite for development, and retain an explicit API-only mode.
The Editarr listening port must be editable and persisted through Settings UI.
This is the upstream service port, not the reverse proxy's public HTTPS port.

Make only the frontend shell and its intended static assets
public so setup/login can load; application data and API documentation retain their
existing authentication requirements.

### D2: Base URL, inferred path, and operator configuration

Confirmed: Provide an optional editable, persisted Base URL in Settings and infer the active
base path from its URL path component. Do not require a separately entered prefix.
The default path is `/` when no path is entered, whether the URL ends in `/` or has
no trailing slash. A non-root path becomes the prefix, including nested segments.
Parse the URL structurally; the slashes in `https://` are not path separators.
Confirmed September 4, 2026 during the completed-task audit: restrict path segments
to ASCII letters, digits, hyphen, period, underscore, and tilde for this release.
Reject `.` and `..` segments, percent escapes, reserved characters, and non-ASCII
characters. Root, nested segments, and trailing-slash normalization remain supported.
The user approved this restriction with “ASCII segments are fine for now.”

| Entered Base URL | Active base path |
| --- | --- |
| `https://example.com` | `/` |
| `https://example.com/` | `/` |
| `https://example.com/editarr` | `/editarr` |
| `https://example.com/editarr/` | `/editarr` |
| `https://example.com/media/editarr` | `/media/editarr` |

Use one canonical prefix internally, excluding its trailing slash except for `/`,
and construct child URLs with exactly one separating slash. Queries and fragments
are not path prefixes; validation must clearly reject inappropriate Base URL input.

Retain an environment variable to pre-populate Base URL until the user changes it.
The earlier provisional `EDITARR_URL_BASE` name described a path-only setting;
`EDITARR_BASE_URL` is now the proposed name for the full URL. Precedence remains
explicitly saved setting, then environment value, then fallback. Once explicitly
saved, subsequent environment changes must not replace it. Saving a URL without a
prefix explicitly selects `/` and overrides an environment URL containing a prefix.
Saving unrelated settings must not accidentally freeze the Base URL fallback.

Confirmed correction to RP-00: Absence of a saved Base URL and environment seed is
valid. Start the ordinary application at `http://localhost:<effective listening port>/`
with prefix `/`; the existing default listening port is 8000 and default bind remains
loopback. This is a permanent supported operating mode, not a setup-only exemption.
Setup, login, sessions, scans, Settings, and restart must work without the user
providing a Base URL, TLS certificate, or insecure-cookie environment switch.

Construct the local origin from the explicit localhost default and resolved port,
not request Host or forwarded headers. Keep the distinction between an absent
configured Base URL and the derived local effective URL so changing the listening
port updates local origin/reconnect targets without accidentally persisting a URL.
In configured proxy mode, changing the listening port still must not rewrite the
explicit public Base URL. Invalid explicit URLs remain validation errors; they do
not silently select local mode.

Adapt HTTP-cookie handling for the built-in loopback mode while preserving forms
authentication, source checks, CSRF, HttpOnly, and SameSite. Retain secure-cookie
behavior for public HTTPS deployments. This local default does not authorize a
broader non-loopback/LAN HTTP exception; RP-00 must distinguish that case explicitly.

Confirmed: Also derive the authentication public origin from
the effective Base URL's scheme, host, and optional port. For example,
`https://example.com/editarr` yields origin `https://example.com` and path `/editarr`.
This makes Base URL the single source for public address configuration. Confirmed:
remove `EDITARR_AUTH_PUBLIC_ORIGIN` without a compatibility release, deprecated alias,
or fallback. This is initial-release development with no existing users. Use
`EDITARR_BASE_URL` as the optional full-URL environment seed. Precedence is saved
Base URL, then `EDITARR_BASE_URL`, then the permanent localhost default. Update
code, scripts, tests, and documentation together; do not retain the obsolete value
as a second origin input or let it prevent local-default startup.
The public URL's port does not set Editarr's separate upstream listening port.
Trusted proxies retain their existing environment-controlled behavior.

The same release/frontend build must use the path inferred from the effective
Base URL, without rebuilding per deployment. This does not propose simultaneous
root and prefixed copies of the application. Sonarr's
[settings documentation](https://github.com/Servarr/Wiki/blob/master/sonarr/settings.md)
describes a configurable URL Base, and Radarr's
[frontend template](https://github.com/Radarr/Radarr/blob/develop/frontend/src/index.ejs)
uses a URL-base placeholder in its frontend bootstrap configuration. This is
consistent with shipping one build and configuring its deployment path.

### D3: Proxy path contract

Confirmed: Require proxies to preserve the configured prefix when
forwarding requests. For `/editarr`, the upstream receives `/editarr/api/...`.
Editarr mounts the application consistently under that prefix. Requests outside
the configured mount do not expose a second copy of the API.

Prefix-stripping proxies are outside this implementation scope. Do not infer a
mount from arbitrary forwarding headers.

For the browser request `https://example.com/editarr/api/scans`:

| Contract | Request received by Editarr |
| --- | --- |
| Preserve prefix (approved) | `http://127.0.0.1:8000/editarr/api/scans` |
| Strip prefix (outside scope) | `http://127.0.0.1:8000/api/scans` |

Both can present the same public address. Stripping requires Editarr to distinguish
the externally visible prefix from its internal route paths, including generated
links, cookies, and redirects. Preserving keeps both paths consistent and makes
direct upstream access use the configured prefix too. Neither changes TLS or
client-address forwarding. The port above is illustrative and will be configurable.

With the approved inferred-path behavior, the implementation is:

1. Resolve the saved/environment Base URL and derive its active path at startup.
2. Mount the UI and API under that path; the browser uses that path for assets,
   requests, cookies, and redirects.
3. Configure the external proxy to forward that path unchanged to Editarr's
   configured listening port. For `https://example.com/media/editarr`, both browser
   and upstream request paths are `/media/editarr/api/scans`.
4. For a Base URL without a prefix, both request paths are `/api/scans`.

Editarr's Settings cannot automatically change a separately managed proxy. The
deployment guide must show which proxy route to update when Base URL changes.
Deriving the prefix from Base URL works with either proxy contract; support for
stripping is outside this approved scope.

### D4: Redirect and proxy trust strategy

Confirmed: Keep original client-peer information intact and keep Editarr's
existing trusted-proxy resolver. Use origin-relative canonical redirects that
include the configured base path. Use the configured public origin whenever an
absolute public URL is required. Do not enable generic Uvicorn rewriting.

### D5: Production packaging scope

Confirmed for now: Include a reproducible release build with bundled frontend assets,
native installation/run instructions, and Nginx and Caddy examples. TLS remains
at the external proxy. Docker/Compose is not included in the current approved scope.

Future requirement, recorded only: Offer builds of native executables for Windows,
macOS, and Linux. Do not expand this into a detailed implementation plan yet. No
executable bundler, target architectures, signing/notarization, installers, or
platform-specific model dependency strategy is selected in this plan.

### D6: Settings persistence and application lifecycle

Confirmed: Use a small deployment configuration file in the data directory for
UI-saved port/Base URL, separate from the database. This lets startup
resolve the listening port before starting the web server and provides an offline
recovery path. Use atomic writes and distinguish absent fields from explicit values.
The derived prefix does not need an independently editable persisted setting.

If an approved approach requires a database schema change, follow AGENTS.md: obtain
explicit confirmation to skip migrations and wipe the development database before
any wipe. No such wipe is authorized by this plan.

Confirmed: Save port/Base URL changes as pending until an
operator restarts Editarr, with current and next-start values shown in Settings.
Do not interrupt an active scan or automatically restart as a side effect of Save.
The explicit Restart Editarr button described in D7 applies saved changes. Live
port rebinding and automatic restart on Save are outside scope.

Confirmed for now: Saved UI port wins during normal startup, default remains 8000,
and an explicitly supplied `--port` is a temporary recovery override shown in the
UI. An argparse default must not mask the saved value. No base-path CLI override
is proposed; the approved saved-setting-over-environment rule remains mandatory.

Development clarification approved September 4, 2026: General Settings next-start
comparisons in Vite development use the Vite browser address and the same development
topology as the active service. An unchanged configuration therefore needs no restart.
Keep saved/environment Base URL precedence and explicit CLI port policy; never persist
an implicit Vite browser URL. Incompatible configured URLs remain an actionable error
with API-only or isolated-development-data guidance.

### D7: Restart Editarr from General Settings

Confirmed: Add a button labeled **Restart Editarr** at the **top right of the
General Settings page**, aligned with the page heading. It provides an explicit
way to restart the service and apply saved pending deployment settings, and is
also available when no settings change is pending.

Implementation requirements:

- Restart must reload persisted deployment settings and restart the actual service,
  including rebinding a changed port. Reloading the browser page is insufficient.
- Keep Save and Restart distinct. Do not silently persist unsaved form edits when
  restarting; show that only saved settings will be applied.
- Protect the restart operation with the existing authentication policy and CSRF
  checks. Use a state-changing request and prevent duplicate restart scheduling.
- Acknowledge the request before shutting down the serving process. Show restart
  progress, prevent repeated clicks, and give a clear reconnect/failure state.
- Reconnect using the effective public Base URL after restart, preserving the
  General Settings destination. Account for changed origin, prefix, expired login,
  proxy reconfiguration, and browser restrictions on cross-origin polling.
- Never substitute Editarr's upstream listening port for the public proxy port
  when constructing reconnect links.
- Preserve the existing lifecycle contract: API shutdown stops scheduling while
  dedicated scan workers survive, and startup reconciles existing workers. Test
  that UI restart does not cancel scans or launch duplicate work. Any necessary
  change to this worker policy requires approval before implementation.
- Provide a real restart path for the supported standalone `editarr` launch;
  do not assume an external service manager will automatically restart it.

Confirmed restart architecture: standalone `editarr` runs a parent controller with
one HTTP-service child at a time. The child completes its restart acknowledgment
before the controller gracefully stops it and launches a fresh child using newly
resolved settings. Preserve launch arguments, including an explicit CLI port
override, and keep dedicated scan workers independent of HTTP-child termination.
If a new listener fails to bind, attempt recovery on the last working listener and
configuration, preserve saved changes as pending, and report failure rather than
claiming the requested settings were applied. If recovery also fails, report it
with manual recovery instructions. Direct Uvicorn, Vite/reload, and other launches
without this controller truthfully report in-app restart as unsupported.

### D8: Forms reauthentication after a URL-scope transition

Confirmed: changing the configured Base URL's canonical origin or prefix requires
forms reauthentication. Persist a non-secret transition cutoff in the deployment
configuration and reject older forms sessions without wiping the database or
deleting session records. Activate the cutoff when the changed origin/prefix takes
effect, not when pending Settings are saved. Keep the cutoff persistent and
monotonic so later restarts or configuration reversions cannot revive invalidated
sessions. Clear obsolete/root and active cookie paths as applicable; cookies on a
different host cannot be cleared by the new host and remain subject to expiration
and the server's transition policy.

Listening-port-only changes retain sessions. In implicit-local mode the derived
localhost origin follows the selected port; that alone is not a configured Base URL
scope change. Changing the explicit Base URL's public port does change its origin
and therefore requires forms reauthentication. Coordinate cutoff activation and
failed-start recovery so an unapplied pending URL never invalidates active sessions.
Existing external-auth and verified-local bypass policies remain unchanged.

## Approved implementation sequence (completed)

### 1. Record the approved deployment contract

- Implement against approved D1–D8 and the permanent-localhost requirement. The
  origin-setting removal, parent controller, and session transition are approved;
  do not request those approvals again. Obtain approval only for additional major
  architectural choices not covered by these decisions.
- Define root and prefixed public URL examples, upstream request paths, defaults,
  restart behavior, and API-only startup behavior.
- Define accepted base-path syntax and canonicalization, cookie scope, and the
  operator procedure when moving an existing installation to a different prefix.
- Inventory all application-generated URLs and public/protected route classes.

### 2. Add deployment configuration and common URL handling

- Implement approved persistence and resolve the listening port before server bind.
- Implement Base URL saved-setting/environment/fallback precedence, preserving
  explicitly chosen root URLs and field-level override state across restarts.
- Include an unsaved localhost fallback derived from the selected listening port;
  absent Base URL must not prevent startup or normal browser authentication.
- Parse Base URL and derive its path; implement the approved public-origin contract
  and remove the old origin environment-variable path without compatibility logic.
- Validate ports and Base URLs, report invalid persisted settings clearly, and
  provide the approved offline recovery procedure for unreachable configurations.
- Keep active routing/cookie configuration consistent for the process lifetime;
  show saved changes awaiting restart separately under the approved lifecycle.
- Validate the full HTTP(S) Base URL and its parsed path; reject credentials,
  queries, fragments, traversal, and ambiguous encoded separators. Do not reject
  full URLs as the earlier path-only proposal did.
- Keep origin comparisons separate from path comparisons after URL parsing.
- Provide shared backend URL construction and frontend runtime configuration.
  Bootstrap configuration must contain only explicitly public deployment values.
- Ensure JavaScript, CSS, icons, and API helpers use the same configured base.
- Document how old path-wide cookies are cleared during a move to a prefix, and
  test set/delete behavior at the new cookie path. Document that cookie paths do
  not isolate mutually untrusted applications sharing one browser origin.

### 3. Implement production frontend serving

- Build and package the web application with the service according to D1 and D5.
- Resolve asset paths independently of the process working directory.
- Serve the public HTML shell and explicit static asset locations before the API
  authentication gate; keep API and documentation protection intact.
- Preserve hash navigation and setup/login return destinations.
- Keep API errors and missing assets from falling through to an HTML response.
- Limit file serving to packaged assets, including traversal and symlink checks.
- Use appropriate cache policies: hashed assets can be long-lived; entry HTML and
  deployment bootstrap configuration must update promptly across releases.
- Fail production startup with an actionable message if required assets are absent;
  support the explicitly selected API-only development workflow.

### 4. Implement base-path routing throughout the application

- Mount routes using the approved proxy contract and normalize the path seen by
  authentication policy without broadening its public-route classification.
- Update frontend API URL matching and bootstrap-authentication exemptions so
  CSRF token attachment still works under a prefix.
- Update all backend-generated artwork URLs and any other returned application
  links; verify media previews and downloads.
- Make API docs/OpenAPI paths and server metadata respect the mount.
- Scope session cookies and cookie deletion consistently to the approved base.
- Implement D8's persistent transition cutoff at activation and verify forms
  reauthentication without data/session wipes, including port-only exceptions.

### 5. Fix redirects and align startup behavior

- Implement the approved canonical redirect strategy for both application entry
  paths and API trailing-slash handling, preserving queries and HTTP methods.
- Prevent redirects to internal hostnames, incorrect schemes, or untrusted hosts.
- Align CLI, development script, tests, and production launch instructions around
  the same original-peer preservation policy.
- Preserve existing strict forwarding-header validation and throttle behavior.

### 5a. Implement the explicit service restart lifecycle

- Implement D7's authenticated restart operation and route classification.
- Reload saved Base URL/port configuration on restart while preserving an explicit
  CLI port override and displaying its effect on the next-start port accurately.
- Coordinate response completion, scheduler shutdown, process restart, and startup
  recovery without overlapping server instances or duplicating scan workers.
- Surface startup/bind failures with useful diagnostics and recovery instructions.
- Verify the actual restart path through the packaged standalone service.

### 6. Surface effective configuration and document deployment

- Add an authenticated deployment-settings response and General settings display
  for the approved operator-facing fields, including restart guidance. Port and
  Base URL are editable; the inferred path is read-only. Display the effective
  public origin according to D2's approved contract; trusted proxies are read-only.
- Protect deployment-setting writes with existing authentication and CSRF policy.
- Place Restart Editarr at the top right of General Settings and implement its
  pending, restarting, reconnecting, and failure states according to D7.
- Explain that a changed upstream port may require updating proxy configuration;
  public HTTPS port 443 and the public origin do not necessarily change with it.
- Explain how a changed base path affects proxy routing and browser access, and
  provide current versus next-start values and a usable recovery procedure.
- Provide complete root-hostname and subpath examples for the approved proxies,
  covering TLS termination, client headers, cookies, and upstream paths.
- Explain the distinction between proxy transport and external authentication.
- Document installation/build, upgrades, asset packaging, data/key persistence,
  missing-asset failures, and changing the public origin or prefix.
- Keep future native executable builds recorded as deferred scope, without choosing
  a packaging tool or designing the cross-platform release pipeline now.

### 7. Verify the complete production path

- Backend tests: base-path validation, route classification, anonymous static
  access, protected APIs/docs, traversal rejection, redirects, cookie deletion,
  malformed/spoofed forwarding chains, local bypass, and client throttling.
- Settings tests: environment pre-population, saved-setting precedence, explicit
  root override, unrelated saves, persistence across restarts, active versus pending
  values, port selection/recovery, and authentication/CSRF on settings writes.
- URL parsing tests: URLs with no path/no trailing slash, explicit `/`, nested
  paths, trailing slash normalization, public ports distinct from the listening
  port, and invalid/ambiguous URLs. Cover the approved origin fallback contract.
- Frontend tests: runtime base handling, CSRF classification, auth bootstrap,
  application navigation, artwork, previews, and static URLs.
- Real browser integration: serve the production build through a local TLS proxy
  at both `/` and `/editarr/`, using an isolated test database and test keys.
- Exercise first-run setup, login/logout, authenticated mutations, hash deep links,
  representative artwork/media responses, and rejection of unauthorized requests.
- Verify the release artifact works outside the repository without Node/Vite at
  runtime; verify documented proxy configurations against the actual service.
- Exercise changing the port and base path in Settings, restarting through the
  approved workflow, updating proxy routing, and reconnecting at the new location.
- Exercise permanent localhost operation with no Base URL or cookie-security flags,
  including setup/login, mutations, scans, and restart. Verify saved/CLI port changes
  update implicit local origin/reconnect URLs without persisting a Base URL or
  changing configured-proxy behavior.
- Verify the Restart Editarr button's location, unsaved-edit handling, duplicate
  click prevention, reconnect target, and restart-with-no-pending-changes behavior.
- Test restart authorization/CSRF rejection, acknowledgments before shutdown, port
  rebinding, CLI override behavior, session revalidation, and bind/startup failures.
- Exercise restart during active and queued scans; verify worker continuity and
  absence of duplicated scheduling or unexpected scan cancellation.
- Run existing lint, backend, frontend, build, and relevant browser regression
  checks after the targeted deployment checks pass.

## Completion criteria (met; see RP-13/RP-14 evidence)

- One approved production installation procedure serves both UI and API.
- Editarr starts and runs permanently at localhost without a configured Base URL,
  reverse proxy, TLS certificate, or manual cookie-security flag.
- Port and Base URL can be changed in Settings and survive restarts.
- Saved Base URL takes precedence over the environment, including an explicit
  URL with no prefix; environment values only supply an unsaved fallback.
- Active base path is inferred from Base URL and defaults to `/` when its path
  is absent or `/`. There is no independently editable prefix to conflict with it.
- The same frontend artifact works at either the root or the single inferred
  prefix, without rebuilding for each deployment.
- No asset, API, or generated application URL escapes that prefix.
- HTTPS proxying produces correct cookies and redirects while preserving client
  identity validation, CSRF behavior, and login throttling.
- Setup/login pages load anonymously; protected data remains protected.
- Deployment settings and proxy examples match the tested runtime behavior.
- Restart Editarr appears at the top right of General Settings, restarts the actual
  standalone service, applies saved settings, and provides clear reconnect feedback.
- Restart preserves the existing active-worker and queue-recovery guarantees.
- No development database is wiped without separate explicit authorization.
