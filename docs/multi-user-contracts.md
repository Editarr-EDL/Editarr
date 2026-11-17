# Multi-user contracts

See the [account guide](multi-user-guide.md) for workflows, the
[permission matrix](multi-user-permissions.md) for role defaults, and the
[API contract](api-contract.md) for routes, response shapes and errors.

## Versions

| Contract | Version | Pin |
| --- | --- | --- |
| Capability inventory | `editarr-capabilities-9` | `contract_digest()` `a3c9b242cf4785d533e79843e3e77688c412430759f27924fc489174c245a9d4` |
| Route/action inventory | `editarr-route-actions-23` | 216 registered method and path pairs |
| Export layout and fixtures | `editarr-export-layout-1` | manifest.json SHA-256 c3063858e3ecf2f8982c66a1140f52ab7d806e737ff5c7854b413cdecccf987e |
| Jellyfin user fixtures | `editarr-jellyfin-users-1` | manifest.json SHA-256 c4fd21e52fa957d1e5794b4de53305ea35b095a53a673ca2a32e7b7c02d9af29 |

A contract change bumps its version and its pinned test. The pins live in
`tests/test_multi_user_capabilities.py`, `tests/test_multi_user_export_layout.py`,
and `tests/test_jellyfin_user_adapter.py`.

## Capability and route evaluation

The [access package](../scanner/security/access/) defines capability IDs,
target rules, owner powers, authentication levels and the route inventory.
Capability IDs are persisted in permission overrides and audit records. Each
capability declares default grants, target type, authentication and any
nondelegable owner power. The evaluator combines live role defaults,
account-specific overrides and target protections. A route with own/other
capabilities checks the variant that matches the resolved resource. Operations
on another administrator or the owner have additional account protections.

The route inventory maps every registered method and path to public,
any-principal or capability-gated access. Setup restore routes are public only
while owner setup is required. The inventory checker rejects missing or stale
route rules. A capability can require fresh interactive authentication even
when the caller otherwise has access. Fresh authentication verifies the linked
Jellyfin password during the protected request. Personal API keys follow the
account's current permissions and cannot satisfy an interactive challenge.

## Accounts, sessions and provider snapshots

Jellyfin authenticates every account. First sign-in uses the verified
Jellyfin identity and initializes the Editarr account. Role overrides on
existing accounts follow the current Editarr owner policy. Browser sessions
belong to one account. Requests read the current permission revision, and
durable work rechecks authority under the original actor before proceeding.

The [Jellyfin account package](../scanner/security/jellyfin_accounts/)
manages verified account snapshots, synchronization and access transitions.
A cached provider snapshot does not turn unverified media into an allowed item.
Media decisions use verified provider identity, safe catalog data and
account/media scope. A hidden item normally returns `not_found` so its
existence is not disclosed. Revocation or an account change can invalidate
in-flight reads, browser state and queued work.

Account linking requires fresh proof and binds consent to the specific
Jellyfin identity and local account state. The account disposition flow
previews retain, transfer or permanent deletion, then binds confirmation to
the authorized scope and current impact. Transfer preserves original authorship.
File operations check publication ownership and identity before modifying files.
Interrupted operations use durable recovery state. Review the
[account linking code](../scanner/security/account_links/) and
[API flow](api-contract.md#users-and-profiles) for request and conflict behavior.

## Scans, reviews and review policy

Completed scan findings remain immutable. An authorized user can reuse
accessible scan evidence to create a private saved review. Reviews retain
original author and current owner separately. The current review owner's
enforced policy governs reads, edits and export preparation. Account policy
changes apply live. Personal non-enforced defaults are starting choices for
new work.

Review-local evidence retains source bindings and can survive removal of
original scan records. A missing required finding or source binding fails
closed during export or publication preparation. Effective review output
carries its saved revision, policy revision and source provenance. A review
transfer recalculates policy under the new owner. The
[review API](api-contract.md#named-review-api) defines explicit repair actions.

## Export layout

Editarr and the [Jellyfin plugin](https://github.com/Editarr-EDL/jellyfin-edl)
implement `editarr-export-layout-1`. The path and export-ID rules live in
[`scanner/formats/export_layout.py`](../scanner/formats/export_layout.py).
The [shared fixture manifest](../tests/fixtures/multi-user/export-layout-v1/)
pins compatibility cases for both implementations.

A personal publication belongs to an export target account. An item-default
publication is available to authorized Jellyfin users without a personal file.
The export provenance records format, target, actor, source review and revision,
original author, and policy owner and revision. Exports use the review owner's
enforced policy. Policy changes regenerate personal publications whose policy
owner changed policy. Item defaults require deliberate republishing.

The plugin resolves the selected media source and authorized Jellyfin account
before choosing an export. Signed-in users get their personal publication or
the item default if no personal file exists. Server API keys see only defaults.
Invalid or unreadable personal files produce an error. Review deletion offers
keep or remove choices for visible publications. Recovery checks file identity
before deleting or moving files and preserves operator-owned data.
See [canonical review exports](api-contract.md#canonical-review-exports).

## Backups and audit history

Full portable backups contain account credentials, API keys, community
identities and application records. The owner downloads them. Restore runs
through onboarding or the offline CLI into an empty installation and
invalidates browser sessions. The configured deployment address and listen
port of the destination apply after restore. Check the supported schema and
use the [CLI backup guide](../README.md#portable-backups-from-the-cli).

Audit records carry actor, credential, operation, outcome, target and resource
scope. Immutable scalar details exclude secrets. Reads apply current account
and media visibility. A permission revision change invalidates cached visibility
checks. Audit retention is 90 days. Community actions bind the initiating
account to that account's installation and contributor identity. Transferring
a local review does not transfer the original community identity or remote
contribution ownership.
