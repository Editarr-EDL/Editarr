# External setup prerequisites

These are operational tasks, separate from local coding. They require the user's
chosen configuration and authorization for concrete external changes. Prepare the
configuration/checklist first; do not create resources or publish invitations as a
side effect of writing code.

October 5 direction: deploy [Node 24 + SQLite in Docker on a Mac mini](../community-service-docker.md).
Cloudflare remains the Tunnel/Access ingress provider, not the compute/database host.
The October 6 local review passed runtime/build/crash/backup checks; it did not
perform any of the live setup or target-Mac recovery checks below.

## OPS-01 — Create the Editarr Discord community and application

Status: IN PROGRESS, September 30, 2026. Server and Developer application
created; callback implemented locally, but redirect registration, deployment
and live membership checks remain pending.
Gates: G-ADMISSION, G-MEMBERSHIP and
G-OPERATIONS in [decisions](decisions.md).

### Context and work

The user created the Discord server (guild ID `1554889314549243986`) and
Developer application (application/client ID `1554924079486607490`). Invites
are currently public; preserve the option to close them later. The `owner` and
`moderator` roles handle human moderation and support for now. Membership alone
currently admits service access; a future `contributor` role requirement for
uploads is an open option, not an active authorization rule. The chosen hosted
origin is `https://community.editarr.org` (`PUBLIC_ORIGIN` once production is
configured); no tunnel route, DNS record or OAuth redirect has been
created by this choice. Confirm ownership and support contact expectations.
The exact registered redirect is
`https://community.editarr.org/v1/discord/callback`; add it under OAuth2
Redirects without a wildcard or local Editarr origin. The authorization
request uses `identify guilds.members.read`. Registering this URL does not
deploy or activate the Docker service. Bot rechecks were selected October 1 and implemented locally
in CSS-27. Installation and live verification have not been performed.

After explicit authorization, configure the application's guild installation
with scope `bot`, permissions=0, in guild `1554889314549243986`. Do not enable
administrator/moderation permissions, a Gateway or privileged bulk-member intent
to compensate for a failed lookup. Provision `DISCORD_BOT_TOKEN` through the
protected host secret file mounted read-only into the container, never through chat
or tracked files. Verify UID 1000 readability without printing secret contents.
Verify `GET /api/v10/guilds/{guild}/members/{user}` for a dedicated member,
absent user (404/code 10007), rejoined user and inaccessible guild/bot removal.
The latter is unavailable, not evidence of absence. Record response status and
rate-limit behavior without headers containing credentials or private payloads.
Test leave/rejoin with 900-second positive/negative evidence, expired outage
denial and operator suspension. Token rotation must invalidate old cache authority.
The documented single-member endpoint names no extra intent/permission; only
these live checks can establish that the minimal installation works here.

CSS-26's local/mock implementation is DONE, not OPS-01 acceptance. CSS-24A
supplies locally tested IP60/shared300 limits per 60 seconds, partial application
passive observations and default-disabled runtime configuration. OPS-02 must review production
process-local limits and trusted tunnel-IP assumptions; do not promote local
test environments or APP_ENV to expose the service. CSS-28/29 enrollment and CSS-27
membership are implemented locally, not live acceptance.

Keep client/bot secrets in the protected host secret file, separate from the database. Record
public IDs and callback URLs separately. Do not request broad unrelated scopes or
publish an invitation without instruction.

### Acceptance and definition of done

- [ ] Server/application exist with the approved owner, invitation and admission policy.
- [ ] Test/production identities/callbacks are distinct as applicable.
- [ ] Required membership checks succeed for a member and fail for a nonmember using
      dedicated test identities; rate-limit/error behavior is recorded.
- [ ] Real HTTPS browser start/confirmation/Discord callback/enrollment completion
      succeeds through the tunnel with its cookie and backend-confirmation binding;
      denial, expired state, replay and provider outage fail safely.
- [ ] Leave/rejoin, expired membership outage, suspension and bot-token rotation
      behave as documented; synthetic provider responses are not live evidence.
- [ ] No secret appears in a repository, task record or screenshot supplied as evidence.
- [ ] Configuration and test evidence handed to CSS-26/27/28; actual live setup status
      is reflected in the task pack.

### Required handoff

```text
Status / date:
Approved decisions and exact external actions:
Public server/application IDs, callback URLs and configuration names:
Where secrets are managed (names/locations only, no values):
Member/nonmember checks and results:
Remaining setup / limitations:
Next exact action for CSS-26/27/28:
```

## OPS-02 — Configure Docker ingress and operator access

Status: NOT STARTED. Gates: G-LIMITS, G-OPERATIONS, G-SERVICE-BACKUP.

Selected hostname: `community.editarr.org` on `editarr.org`. Cloudflare Tunnel
routing, DNS/HTTPS verification and Docker service activation
remain pending; do not create a placeholder A record or expose local bindings.

### Context and work

The user already uses Cloudflare; check applicable Tunnel/Access eligibility and
constraints before proposing external changes. Prepare the selected Mac/Docker
engine, image, loopback port, local data/backup volumes, protected host configuration
and secrets, tunnel route, hostname and Access allowlist. Review existing local
evidence and CSS-69's open-gate matrix; CSS-68 is canceled, not capacity certification.
Do not provision Workers/D1/R2 resources.

After authorization, create only the agreed test/production resources, mount secrets,
protect dashboard and admin API including alternate hosts, and validate that artifact
reads require current authorization. No public launch, paid subscription/upgrade, production
migration or invitation publication is implied by a coding task.

CSS-55's [local implementation and evidence](55-operator-access.md) are complete,
not operational acceptance. Before live operator setup:

- Obtain the approved account/domain, Access application and human operator list.
      Protect `/admin` and every `/admin/` path, including `/admin/v1/*` and static
      assets, without applying contributor authentication or an Access bypass rule.
- Configure `OPERATOR_ORIGIN` as the approved canonical HTTPS origin,
      `ACCESS_ISSUER` as the exact HTTPS team domain, `ACCESS_AUDIENCE` as this
      application's AUD, and `OPERATOR_SUBJECTS` as an exact verified Access-subject
      JSON array. These are private deployment configuration where appropriate;
      never paste JWTs, cookies, deployment tokens or operator identity inventories
      into chat, tracked examples or screenshots. Email headers do not authorize.
- Serve only the image's bundled operator assets through the authenticated Node
      handler. Do not publish them through an unauthenticated static server or SPA
      fallback. The local Editarr build is not an operator artifact; no alternate
      provider preview/deployment path is supported.
- Route the approved HTTPS hostname through the tunnel to the loopback-only published
      container port. Preserve the external Host and verify scheme/authority handling
      with real requests. Confirm LAN/internet clients cannot reach the origin directly
      or inject trusted `CF-Connecting-IP`/`CF-Worker` metadata through ingress.
      Do not rely on the hostname or a trustedTunnel flag alone as isolation proof.
      Do not blanket-protect signed contributor routes with operator Access login.
- Verify allowed/denied subjects, malformed/expired/wrong-audience JWTs, real
      key rotation/session revocation behavior, every asset/API denial path, missing
      config, alternate/direct hosts and hostile mutation origins. Current local tests
      do not establish live session lifecycle, JWKS rotation or tunnel isolation.
- Verify CSS-59's approved human cloudflared application-token flow against the
      same Access application/subject allowlist and mutation guards. Protect the
      cloudflared local token cache; test expiration/re-login/revocation and current
      binding-cookie compatibility without silently relaxing Access settings.
      No contributor/service/deployment token or CSRF exception is supported.

Docker is the only supported deployment. Activation is separate: stage disabled,
validate complete protected configuration and signer/public-ring agreement, and
obtain explicit permission before enabling both runtime/tunnel trust and the database
protected configuration. Verify `/health` versus `/ready` and disabled API behavior. This checklist
does not authorize activation or reuse of fixture limits/secrets in production.

Coordinate target-Mac/FileVault/Docker/tunnel cold-boot and reconnect testing with
CSS-67. CSS-68's independent recovery drill is canceled; CSS-69 records the
snapshot/configuration/key-history recovery risk and explicit launch disposition.
Do not infer cold-boot readiness from container restart or off-device safety
from a named backup volume on the same Mac. No backup schedule or retention is selected.

CSS-42 adds a separate service-only `DOWNLOAD_RECEIPT_PRIVATE_KEY` secret and
public `DOWNLOAD_RECEIPT_KEYS` verification/revocation history, plus explicit
`DOWNLOAD_LIMITS` configuration. Provision only after operational authorization;
never use fixture seeds. Protect the active secret separately from SQLite and preserve
historical public keys for no-expiry receipts. Include additive 0011 download
observations in full database backups, with verified isolated restore before any
existing-store upgrade. See the [receipt/secret inventory](decision-evidence/42-artifact-download-contract.md).

### Acceptance and definition of done

- [ ] Chosen account, plan, resources, passive activity and hostname are recorded.
- [ ] Contributor routes and operator routes have their separate tested authorization.
- [ ] Alternate hosts/asset fallbacks cannot bypass Access; artifact reads remain authenticated.
- [ ] Real ingress preserves HTTPS authority/Host and trusted client metadata; direct
      LAN/public origin access is refused and contributor endpoints do not require Access.
- [ ] Allowed/denied operator browser and CLI sessions, expiration/re-login/revocation,
      signing-key/JWKS rotation and CSRF are verified without weakening policy.
- [ ] Image/configuration/mounts and disabled/ready/activation states are recorded;
      target-Mac cold boot and independent recovery dispositions are linked from CSS-67/69.
- [ ] Test restore cannot reach production callbacks, clients or databases.
- [ ] CSS-55/66/67/69 receive configuration and measured setup evidence, with open provider
      or deployment constraints explicitly marked.

### Required handoff

```text
Status / date:
Approved configuration and external action scope:
Environment/resource/config names (no secrets):
Tunnel/Access account checks and passive application telemetry (not provider billing):
Access/routing/artifact-authorization verification:
Image/architecture, Mac/Docker/tunnel startup and readiness evidence:
Backup destination/isolation and CSS-69 unverified recovery disposition:
Outstanding decisions, rollback steps, next exact task:
```
