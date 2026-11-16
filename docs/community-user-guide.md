# Community filters and portable identity

The community service is optional. Local scans, manual filters, saved reviews and
EDL/MCF export remain available without it. The local Python application and the
[separate community Docker service](community-service-docker.md) have different
administrators, credentials and backup procedures. Local verification does not
mean the public service is active.

## Connect and retain your identity

Sign in as the local administrator and open **Settings > Connections**. Review
the community service reference and approve the connection before starting Discord
authorization. Return to Editarr and confirm the connection. A displayed saved
profile is not proof of current access; use the explicit access check when needed.
Never paste a Discord credential, signing seed or operator token into a form or chat.

Membership alone is required; no approval role is currently required. The service
rechecks membership on use after 900 seconds. A cached positive check can delay
removal enforcement by up to that interval. Expired evidence plus a provider outage
pauses access, rather than treating the user as a member. Verified rejoin restores
access unless another restriction, such as operator suspension, remains.

Installations of the same Discord account share the contributor identity and history
and reputation, but separately registered installations have distinct keys. Reinstall,
rename and rotation do not reset those records. Restoring a portable identity retains
its logical installation ID and key; it does not register a new installation.

## Share an exact contribution

Use **Share** on a completed scan or saved review. Confirm its media identity,
source, coverage and selected channels. Preview the exact public payload, including free-text notes,
original authors and changes, before confirming rights and standing reuse permission.
Private file paths, credentials and alternate-track inventories are excluded.

AI audio contributions describe only the actual analyzed track. A mixed review can
require choosing one track and excluding other-track audio; this does not edit the
saved review. Historical or partial evidence keeps its unknown/coverage labels.
Do not promote it to a verified match by editing a description.

Uploads retain their approved bytes and operation identity. Check a pending result
before retrying; explicit retry sends the saved intent, not a newly created donation.
Cancellation after submission can be uncertain, and a committed publication remains
published. Standing permission permits retained public notes, attribution and change
history. There is no author withdrawal command; operators can moderate visibility.
Community sharing requires explicit approval for each contribution.

## Find, import and review

Select the physical media version and, when applicable, its audio track. Open
**Find Filters** and explicitly request community results. Local discovery stays
available if the community lookup fails. No lookup or download implies enrollment,
automatic selection, review edits or sidecar export.

Compare video and audio compatibility separately. Compatibility is ordered before
revision quality; the quality score does not prove a matching physical cut. Review
required, uncertain and unknown-track results need explicit channel approval. Stream
timing conflicts remain channel-specific; the duration tolerance does not authorize
automatic cue retiming.

Select the desired channels and download. Each queued item has its own outcome;
completed imports survive another item's failure or cancellation. Reopening reconciles
committed local results. Repeated import reuses verified evidence while preserving
original signed bytes and attribution. Create or open a saved review, inspect cues
and save changes before explicitly exporting EDL or MCF.

Use **Check feedback** for current private eligibility and vote state. Voting requires
an acknowledged import of that exact non-self revision and current membership.
Pending votes retain their exact intent; retry does not overwrite a newer vote with
a historical outcome. Download/import volume is not uploader trust. Reports have a
service API, but the local report-submission control is not implemented.

## Backup, transfer, rotation and recovery

Open **Settings > General > Backup and restore**. Full backups include local work;
settings/identity backups intentionally omit it. Both preserve their selected
credentials and portable signing identity. Plain export is the default and needs no
passphrase. Encryption is optional; failed encryption never falls back to plaintext.
Protect every archive and keep encrypted-archive passphrases independently.

On a new installation choose **Restore from backup** before creating an administrator.
Upload the archive, enter a passphrase only for encrypted input, inspect the preview
and confirm replacements. If administrator credentials are included, sign in with
those restored credentials. Create an administrator only when they are absent.
Restore can pause work for reconciliation; it neither rotates keys nor starts sharing.
For interrupted activation, stop Editarr and follow the
[offline backup recovery commands](../README.md#portable-backups-from-the-cli).

For a deliberate move, select the identity replacement explicitly, retain a recoverable
backup and retire the old running copy. Two restored copies share one logical key;
they are not independently registered installations. Do not run both as if they were.

Use the connection settings' explicit rotation/recovery actions. Normal startup,
login, restore and upgrade never rotate keys. Rotation immediately retires the old
key; historical public keys remain available to verify existing signatures. Lost-key
recovery requires fresh authorization of the same Discord account and confirmation,
and does not replace sibling installations' keys. Reconcile an interrupted action
before starting another, then make a fresh backup.

No recovery can recreate a missing private key from its public key, recover an
unknown archive passphrase, silently change Discord ownership, adopt an unknown
database schema or certify old security state as current. Preserve uncertain journals
and seek operator review rather than resetting the database.

## Compatibility and failures

The developer Python package requires Python 3.11 or newer. Wheel encryption
on POSIX uses age and Expect. The native app bundles its encryption helper. Windows
encrypted scheduling fails closed because protected-file ACL validation is unavailable.
Local scheduling is disabled until configured and never backs up the Docker service.

Use the [Docker upgrade procedure](community-service-docker.md#persistence-and-recovery)
with a verified pre-upgrade snapshot. Startup does not upgrade the database.
Artifact v1/v2 verification retains exact gzip, manifest and signature bytes. Strict
clients and service DTOs need coordinated releases; unknown versions/fields fail closed.
The About endpoint may report community/artifact identities as unknown; it is not a
capability-negotiation endpoint. No additional compatibility grace window is selected.

For unavailable access, distinguish absent configuration, expired membership evidence,
service outage and operator restrictions. Retry only the recorded uncertain
intent after resolving the cause. Invalid signatures, changed targets and incompatible
parent permission require correction/review, not bypass. A service outage is not an
empty result or evidence that a submitted operation failed. Protect screenshots and
support logs from tokens, private paths, passwords and Discord identities.

Operator moderation, maintenance snapshots and restore are documented in the
[service guide](../community-service/README.md) and
[Docker runbook](community-service-docker.md). Local administrator credentials do not
grant operator Access authority.
