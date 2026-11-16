# Continuing Discord membership comparison

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Prepared October 1, 2026. No real Discord account, bot installation, token,
provider request or external resource was used. The comparison itself selects no
method; the user subsequently chose the bot plan October 1. CSS-27 implements
bot REST locally, pending OPS-01 minimal-permission live verification.

## Same access policy

Both choices recheck on use after 15 minutes, never extend expired positive
results during an outage, distinguish verified absence from provider failure,
and restore access after verified rejoin unless Editarr has suspended the
contributor. Local Editarr use remains available. Roles and rules-screening
completion do not control community eligibility. No Gateway is required.

## Alternatives

| Concern | Single-member bot REST | Retained user OAuth |
| --- | --- | --- |
| Request | GET /guilds/{guild.id}/members/{user.id}, Bot authorization | GET /users/@me/guilds/{guild.id}/member, Bearer authorization |
| Setup | Install application's bot in the selected guild; provision bot secret | Keep existing identify + guilds.members.read code flow; retain tokens |
| Permission boundary | Single-member docs name no additional permission or privileged intent; bulk List Guild Members explicitly requires GUILD_MEMBERS | Endpoint explicitly requires guilds.members.read |
| Proposed minimal setup | Guild installation with bot scope, permissions=0; no administrator, moderation, Gateway or bulk listing | Exact hosted callback unchanged; no guilds.join, email or broad guild listing |
| New secrets | One DISCORD_BOT_TOKEN Worker secret | Per-contributor access/refresh tokens plus a separate encryption-key Worker secret |
| Existing secrets | OAuth client secret still needed for initial broker login | OAuth client secret needed for login and refresh |
| D1 | Membership state, checked time, refresh lease and provider cooldown; existing private Discord linkage | Same membership/lease/cooldown plus encrypted tokens, expiry, scopes, key version and rotation metadata |
| Reauthorization | Not needed solely because initial user OAuth tokens expire | Required after revoked/invalid refresh grant or lost encryption key |
| Failure blast radius | Bot removal, invalid token or permissions affect all stale contributors | Individual revoked grants affect that contributor; client/encryption secret faults affect many |
| Maintenance | Bot token rotation and least-privilege installation | Authenticated encryption, token refresh races/rotation, deletion, revocation, backup key custody and migration |

The bot endpoint's absence of a documented privileged-intent/permission
requirement is not a live guarantee. OPS-01 must verify a permissions=0 bot
can read a known member, reject an absent user and distinguish inaccessible
guild/bot removal from absence. Do not enable broad permissions to mask a
failed test. Neither a public guild widget nor approximate member counts can
substitute for an authenticated lookup.

For OAuth, use returned expires_in rather than assuming Discord's example
604800-second lifetime. Refresh with form-encoded grant_type=refresh_token;
atomically persist the returned token pair. A lost response or competing
refresh must not silently reuse stale credentials. Token revocation invalidates
the authorization's access and refresh tokens, not merely the supplied token.
The current CSS-26 broker discards tokens: OAuth selection requires an additive
encrypted-token design, backup inventory and consent/reauthorization integration.
No access/refresh token may appear in clients, logs, task records or plaintext D1.

## Request and storage estimates

Assume N contributors continuously active for a full day, checks perfectly
coalesced per contributor across installations/isolates, no failures and a
900-second window. That gives 96N membership requests/day and average N/900
requests/second for either method. Inactive contributors generate no checks.

| Active contributors | Membership calls/day | Mean calls/second |
| --- | --- | --- |
| 50 | 4,800 | 0.056 |
| 500 | 48,000 | 0.556 |
| 5,000 | 480,000 | 5.556 |

These are Discord subrequests, not separate incoming Worker requests. They
consume Worker invocation duration/subrequest allowance and D1 coordination
work. A lease claim plus result update is about two application mutations per
refresh, before indexes, rate-limit metadata and telemetry; actual rows_written
and CPU must be measured, not inferred from this estimate. Bursts synchronized
at expiry can exceed route limits even with small daily averages. Discord's
documented 50 requests/second bot global limit is not the guild route allowance.
Parse bucket/reset headers and Retry-After/retry_after; do not hardcode provider
rates. A 429 must delay further calls at the affected shared/global scope while
stale users remain denied; do not retry-loop inside the request.

If an attempt fails and a later on-use retry succeeds, it adds one membership
call per affected contributor beyond the ideal model. A shared outage cooldown
and D1 refresh leases must prevent one failed call per incoming request or
per installation. Cooldown is an operational retry mechanism, not extra access
freshness. A negative cache can delay rejoin detection until its bounded
recheck; successful verification must automatically restore eligibility.

OAuth adds refresh calls and token updates. Illustratively, if the actual
token response allows seven days, continuously active users add roughly N/7
token calls per day averaged over that period; synchronized expiry is bursty.
This is an estimate based on a documentation example, not a lifetime promise.
Both store O(N) membership rows; OAuth additionally stores O(N) ciphertext
token pairs, nonces/tags, expiry and encryption-key-version metadata. Exact
byte/storage costs depend on chosen encoding and returned token lengths; no
sample token length is treated as a production cap.

## Failure examples and rollout

Check at 12:00 permits an otherwise active contributor before 12:15. At
12:15 a failed refresh yields unavailable, not eligible or nonmember. A valid
unknown-member response yields ineligible. Rejoin followed by a successful
bounded refresh restores membership access, never clears an operator suspension.
The bot strategy must distinguish Unknown Member from Unknown Guild/Missing
Access; a bare 404 cannot safely prove nonmembership. OAuth 401/invalid refresh
requires reauthorization, not a claim that the user left the guild.

Bot setup: use existing application and chosen guild, keep public installation
off if suitable, authorize bot scope with zero requested permissions, securely
provision DISCORD_BOT_TOKEN and test dedicated member/nonmember/removal cases.
No bot token is requested through chat. Initial login remains CSS-26's fixed
identify + guilds.members.read callback. Back up membership data as part of
D1; keep bot/client secrets outside snapshots and restore into isolated test
environments only. Bot credential changes invalidate old cache authority.

OAuth setup: define and review authenticated token encryption/key rotation,
add numbered migrations and upgrade tests, securely provision encryption key,
retain authorized grants server-side, test refresh/revocation/restore/lost-response
cases, and implement explicit reauthorization. Backups must inventory ciphertext
and separately managed key custody; restored tokens can be stale or revoked.
Never fetch real credentials or install the bot as an incidental coding action.

## Sources and recommendation

Official sources checked October 1, 2026:

- [Get Guild Member and privileged bulk listing](https://docs.discord.com/developers/resources/guild#get-guild-member).
- [Get Current User Guild Member](https://docs.discord.com/developers/resources/user#get-current-user-guild-member).
- [OAuth scopes, bot installation, refresh and revocation](https://docs.discord.com/developers/topics/oauth2).
- [Dynamic buckets, global limits and retry rules](https://docs.discord.com/developers/topics/rate-limits).

Recommendation: single-member bot REST, conditional on minimal-permission live
verification before rollout. It fits the single-guild Worker footprint and
avoids retaining user authorization credentials. It adds one operator secret
and guild installation, not an always-on process. Retained OAuth is viable if
avoiding a guild bot is more important than token custody and reauthorization
complexity. Neither method changes the approved membership policy.

Exact selection question: implement single-member bot REST checks with one
Worker bot secret and no retained user tokens, or encrypted retained OAuth
access/refresh tokens with refresh/revocation/reauthorization support?