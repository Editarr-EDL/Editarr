# Community scan service: Discord enrollment and signing keys

Status: **Discord sign-in, community membership, and retaining installation signing
keys confirmed September 26, 2026 (D19/D20).**
Separate installation keys, 15-minute membership freshness, membership-only admission,
and explicit Discord-assisted lost-key recovery are also confirmed (D21/D22/D24).
This supplements the [main plan](community-scan-service-plan.md). Recommendations
below are proposals, not authorization to deploy or create the Discord community.

October 5 deployment update: the hosted endpoint is now
[Node 24 + SQLite in Docker on a Mac mini](community-service-docker.md), reached
through Cloudflare Tunnel. Access remains separate operator authentication.
[OPS-01/02](community-scan-service-tasks/operator-setup.md) own real callback/cookie,
bot membership and Tunnel/Access tests; CSS-66 owns mocked Node HTTP integration.

## 1. Confirmed direction

Require Discord OAuth2 sign-in and membership in the Editarr Discord server for
community enrollment. This replaces unrestricted anonymous key enrollment. Lookup,
download, publication, and feedback remain authenticated under D10. Local scanning,
existing local filters, and local playback workflows do not depend on Discord.

Keep a stable Editarr contributor ID, with a private unique mapping from Discord's
user ID. Repeated sign-in, new installations, restored copies, and new credentials
must not reset reputation, moderation state or vote eligibility.
Public attribution still uses the chosen generated/custom Editarr display name.
Do not expose the Discord ID in public artifacts or match accounts by display name.
Each installation registers its own key under the shared contributor. Rotation or
revocation targets that installation only. Backup restore retains the installation
ID/key; two restored copies are copies of one installation, not extra reputation.

Discord membership is an admission control, not proof of one physical machine or
one human. Alternate Discord accounts remain possible. Community moderation and current membership still apply; recorded usage does not restrict users.

## 2. Confirmed signing design

The user chose to retain Ed25519 keys and signatures for the additional proof and
attribution. Discord establishes the external account and membership; the installation
signs everyday API requests and contribution artifacts. Interactive Discord sign-in
is not required for every lookup or background donation, but service authorization
still checks key state, contributor state, and membership freshness policy.

Keep the private key in the local backend's durable private directory and include it
in portable backups; backup encryption is optional and off by default under D06.
The service stores the public key and its contributor binding. Verify request
signatures, signed digests, replay controls, and current authorization before protected
activity. TLS, idempotency, payload validation and unique vote/import rules
remain required as specified in the [contracts](community-scan-service-contracts.md).

Detached artifact signatures allow recipients to verify exact bytes against the
registered uploader key, including after download. Attribution to an Editarr contributor
still depends on the trusted key-to-contributor binding; a signature alone does not
prove a real-world identity, accurate findings, or truthful model/settings claims.
Preserve the original signature/provenance when importing and sign derived contributions
with the editing contributor's key while retaining source attribution.

D18 remains unchanged: only a manual action in Editarr's UI rotates the key, and the
old key retires immediately on activation of its replacement. Keep historical public
keys for verifying prior artifacts; they no longer authorize API requests. Contributor
ID and reputation survive rotation. Fresh Discord authorization plus explicit UI
recovery may replace a lost installation key and immediately retire it, leaving other
installations unaffected. Ordinary Discord sign-in or provider-token refresh
must not silently rotate an installation key. Service-issued bearer tokens do not
replace signing keys in the selected design.

## 3. Hosted authorization flow

1. An authenticated local administrator starts community connection in Editarr.
   The backend loads or creates its durable key atomically. Create a bounded enrollment
   transaction and bind it to that installation's public key.
2. Open a hosted enrollment page. The Node service uses Discord's authorization-code flow;
   its registered callback and client secret remain on the hosted service.
3. Request `identify` and `guilds.members.read`. Fetch the authenticated user and their
   membership in the configured Editarr server. No email, message access, automatic
   server joining, or list of all servers is needed for this check.
4. Validate one-use OAuth state bound to the browser session and enrollment transaction.
   Verify membership server-side, then resolve the unique Discord/contributor link.
5. Complete enrollment only for the initiating installation. Use a short-lived,
   single-use handoff bound to the registered enrollment public key and require proof
   of possession; the public transaction ID alone cannot claim the result. Show an explicit
   connection confirmation so an attacker cannot trick a member into authorizing an
  unrelated installation. October 1 CSS-28 binds a backend-held confirmation
  secret hash before OAuth and requires the secret in signed completion after
  local administrator confirmation. The public transaction ID is insufficient.
  The hosted contract and fixture are in [service contracts](community-scan-service-contracts.md#1-boundaries-and-identity-lifecycle);
  local confirmation/secret persistence and UI implementation remain CSS-29/30.
6. Complete key proof-of-possession and registration, then persist contributor/service
   identity alongside the existing local key and supported backup metadata.

Use Discord's user API rather than trusting callback parameters or an unverified user
ID supplied by the local client. Discord provides the scoped current-user membership
endpoint; an installed bot is unnecessary for this initial OAuth check.
[Discord OAuth2](https://docs.discord.com/developers/topics/oauth2),
[current-user membership](https://docs.discord.com/developers/resources/user#get-current-user-guild-member).

The local installation signs Editarr service requests with its registered key.
Discord access/refresh tokens serve Discord API access and must not become public
scan metadata or a shared app secret. The Discord application client secret never
ships in the local Editarr app. Keep service secrets in protected host files mounted
read-only into Docker, not image layers. Bound enrollment state lifetime/polling and
count it in the preserved passive observations.

## 4. Continued membership and recovery

On use, recheck membership when 15 minutes have elapsed since successful verification.
If that expired check cannot refresh, pause community reads/writes while preserving
local use. Known ineligibility denies protected requests immediately; do not claim
instant Discord-ban propagation while a prior positive check is still fresh. Membership
alone is required, with no extra rules-screening or operator-role gate.
After a user rejoins, successful membership verification restores service access unless
Editarr operators have separately suspended the contributor; no forced reconnect is required.

The user selected single-member bot REST on October 1 after
[CSS-70](community-scan-service-tasks/70-membership-method-comparison.md).
CSS-27 implements it locally; minimal-permission live verification remains OPS-01.
The compared alternatives were:

- **OAuth rechecks:** retain provider access/refresh material encrypted on the hosted
  service, refresh when necessary, and make bounded membership checks. Requires
  provider-secret storage and reauthorization when the grant is no longer usable.
- **Server bot membership checks:** evaluate scoped bot REST checks against the one
  configured community. This adds bot setup and credential management; do not assume
  an always-running Gateway process is required or approved. Verify endpoint access,
  permission/intent requirements, and rate limits before selecting it.

A one-time membership check alone is insufficient for ongoing membership gating.
Do not call Discord on every artifact request without budgeting and rate-limit tests.
Expired eligibility must not silently become anonymous authorization. Keep provider
outages distinct from verified nonmembership, and preserve completed local work.

Lost-key recovery requires fresh Discord authorization bound to the affected installation
and replacement key, then explicit confirmation in Editarr's UI. Retire the lost key
immediately while preserving that installation ID, contributor and reputation; other
installations' keys remain active. Never turn a normal login into recovery.
Losing Discord access is a separate case; no operator identity-transfer bypass has
been approved. Retain service moderation state across leave/rejoin or credential reset.

## 5. Prerequisite to-do list

- [x] Create the Editarr Discord server/community; guild ID `1554889314549243986`.
- [ ] Configure the server ID in the hosted service; record an invite URL only if
  the user chooses to publish one.
- [x] Invitations currently public; `owner` and `moderator` roles handle human
  moderation/support. Controlled invitations remain possible later. Membership
  alone currently admits service access; a `contributor` role gate for uploads
  remains a future option, not a current requirement.
- [x] Create the Discord Developer application; application/client ID
  `1554924079486607490`.
- [ ] Register the exact hosted OAuth redirect URI; configure protected Docker
  secret mounts and separate test/production credentials as applicable.
- [x] Resolve D20: retain Ed25519 request and artifact signatures alongside Discord.
- [x] Select separate installation keys and explicit Discord-assisted lost-key recovery.
- [x] Select 15-minute rechecks on use and pause access when expired checks cannot refresh.
- [x] Restore access automatically after successful rejoin verification unless suspended.
- [x] Compare recheck methods in CSS-70 and obtain the October 1 bot REST choice;
  CSS-27/33 implement membership and explicit recovery locally. Live checks remain open.
- [ ] Describe the private Discord linkage and any provider-token retention in the
  service privacy notice; include provider secrets in hosted backup/recovery design.
- [ ] Test denied consent, nonmembers, removal/ban, lost/revoked credentials, provider
  outages, repeated enrollment, hostile handoffs, and account-level identity preservation.
- [ ] Verify real HTTPS Host/cookie/callback handling through the tunnel and separate
  operator browser/CLI Access sessions without blanket login on contributor routes.

Cloudflare Access continues to protect operators separately. Discord contributor
membership does not grant operator authority. Creating this server/application and
deploying the integration are implementation tasks, not actions performed by this plan.
