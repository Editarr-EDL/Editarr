# Discord enrollment and signing keys

Discord OAuth2 and membership in the configured Editarr Discord server establish
eligibility for finding, downloading, sharing and voting on community filters. Local scans
and saved filters work independently of Discord. An installation signs community
API requests and contributions with its Ed25519 key.

## Connect an installation

1. In **Settings > Connections**, a local administrator starts the community
   connection. Editarr loads or creates the durable installation key and binds a
   short-lived enrollment to its public key and a private confirmation secret.
2. The hosted service starts Discord's OAuth2 authorization-code flow with
   `identify` and `guilds.members.read`. The callback validates browser state and
   verifies both the Discord account and membership in the configured server.
3. Return to Editarr with the public enrollment reference and confirm locally.
   The local backend signs the completion request and supplies the original
   confirmation secret. The hosted service validates the key, binding, expiry
   and current membership before registration.
4. Editarr stores the contributor, installation and service information with
   the private key. Attribution uses the Editarr display name. The service
   keeps the Discord identity private.

For the callback URL, bot credential and tunnel boundary, see
[Docker configuration](community-service-docker.md#configure-before-activation).
For request and artifact signatures, see
[community service contracts](community-scan-service-contracts.md).

## Membership and key recovery

The service uses the configured Discord bot to refresh individual membership
when the previous result expires after 15 minutes. A failed refresh pauses
community access until the service can verify membership again. Rejoining the
server restores eligibility after verification if the contributor is not
suspended.

Connecting a second installation with the same Discord account registers a
separate key under the same contributor. Restoring an identity backup preserves
the original installation and key. Keep backups private because they contain
signing credentials.

Use **Settings > Connections** to rotate a key. The request proves possession
of both keys and atomically retires the old key. If a response is lost, Editarr
reconciles the same operation with the saved replacement key. If you lose the
key, use the separate recovery flow with fresh Discord authorization and explicit
local approval. Recovery replaces the key for that installation and preserves all
other installations under the same contributor. After a successful rotation or
recovery, create a new identity backup.
