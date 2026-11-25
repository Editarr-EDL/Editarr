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

## How community filters are organized

The community keeps one **master filter set** per version of a work. A work is a
movie (TMDB or TVDB ID) or a TV episode (series ID plus season and episode). A
version is a release or cut such as Theatrical, Director's Cut, Extended Edition
or Unrated; Theatrical also means "no particular version". Every contributor's
shared filters are merged into that version's master set, so contributions
reinforce one set instead of competing. Each filter carries its own confidence
and contributor count. Attribution is a count only, without names.

## Find community filters

Select the physical media version on the item page and open **Find Filters**.
The dialog lists matching local scans first. Its **Community versions** section
searches only when you choose **Find community versions**; local discovery stays
available if the search fails. The first search uses Theatrical. Use the version
picker to search a different version, or choose **Other** and an optional name.

Each result card shows the version name, a **Match %** and the filter and
contributor counts. Match % reflects the strongest evidence that links your file
to that version:

| Evidence | Match % |
| --- | --- |
| Full-file hash | 100% |
| Sampled or quick hash | 80% |
| Audio or video stream fingerprint | 65% |
| TMDB/TVDB ID and version, runtime within tolerance | 30% |
| Title, year and version, runtime within tolerance | 15% |
| ID or title match with runtime outside tolerance | 5% |

An **Unconfirmed** label marks a candidate version. Its filters come from
contributors whose files could only be loosely matched (by ID, title or runtime).
It becomes confirmed once several contributors with matching files submit for it,
or after moderator review. Hover over or focus the label to see this explanation.
Treat unconfirmed versions with extra care.

## Apply and align a master set

Choose **Use these filters** on a result card to download the service-signed
master set and link it to this media version. Each account has one linked version
per media version; choosing another version asks you to confirm the replacement.
Nothing is applied to a saved review or exported automatically.

Results at 65% or higher apply immediately. Lower matches show **Needs alignment
with your file before it applies**: open the set's **Align before applying** step,
add anchors that say where filters start on your file, previewing their clips (or
enter an offset), choose
**Preview alignment**, then **Approve alignment**. No filter from that set is active
until alignment is approved.

## Review the community filters

The item page's **Community filters** section lists the linked set's filters with
their time, category, severity, action, confidence and contributor count.

- **Confirm** or **Reject** each filter. Your decisions are kept locally and are
  never overwritten by updates; **Reset** returns a filter to the master values.
- Filters with a **Locally verified** check icon (hover or focus it for the
  tooltip) are ones you confirmed or adjusted, or that overlap a local model
  finding of the same category.
- A filter is **Low Confidence** when its negative signals outweigh its support
  (attestations plus upvotes). Negatives come from downvotes, disputes, and other
  contributors' shared submissions that scanned that channel (audio or video) over
  that range without finding the filter; each counts as one downvote. Your local
  scans never count toward anything unless you share them. Untouched
  Low Confidence filters are not active unless you turn on **Show Low Confidence
  filters**. **Minimum confidence** hides other untouched filters below your
  threshold. Both preferences belong to your account; filters you touched ignore
  them.
- Use **Thumbs up** or **Thumbs down** to vote on an individual filter. Votes
  require a current community connection and your downloaded copy of the set; you
  cannot vote on your own filters. A vote that could not be sent stays pending with
  **Retry vote**, which resends the same saved intent.

## Share your filters

Use **Share** on a completed scan or saved review. Choose the version your file
represents with the version picker (Theatrical, another listed version, or
**Other** with an optional name); no preview is prepared until a version is
chosen. The work needs a TMDB or TVDB ID, plus season and episode for TV.

The preview shows the exact public payload and an approximate summary such as
"≈4 new, ≈27 confirmations, 3 adjustments, 1 dispute". The numbers are estimates
because the service makes the final match. Your cues and coverage are shared;
coverage tells the service which channels (audio or video) and time ranges your
scan analyzed, so community filters there that your scan did not find count
against them;
community filters you confirmed or adjusted count as confirmations or
adjustments, and filters you rejected are shared as disputes. Untouched downloaded
filters are not re-shared. Private file paths, credentials and alternate-track
inventories are excluded. Confirm rights and standing reuse permission before
approving.

AI audio contributions describe only the actual analyzed track. A mixed review can
require choosing one track and excluding other-track audio; this does not edit the
saved review.

Approved contributions keep their exact bytes and operation identity. Check a
pending result before retrying; retry resends the saved intent, not a new
contribution. Cancellation after submission can be uncertain, and a committed
contribution remains published. There is no author withdrawal command; operators
can moderate visibility.

## Daily update checks and update review

Editarr checks your linked versions for newer master-set revisions at most once a
day when you open the web app. The sidebar's **Filter updates** entry shows
unread notifications for changed, merged or unavailable versions and links to each
item page; use **Mark all as read** to clear them. On an item page,
**Check for filter updates** checks that set immediately without using the daily
allowance.

A newer revision is downloaded but never applied on its own. Choose **Review** to
see the changes grouped as **New**, **Changed**, **Removed** and **Now Low
Confidence**, with the changed fields for each filter. For each row choose
**Accept**, **Keep mine** or **Dismiss**, or use a group's **Accept all**. Accept all
skips filters you edited, so decide those one at a time; they are never silently
overwritten. Sets that needed alignment must have their
alignment approved for the new revision before updates can be accepted.
Confidence-only changes apply without review.

## Earlier community imports

Scans imported under the earlier per-scan protocol remain ordinary local scans,
with their original attribution, and can still be used in reviews and exports.
They no longer support community votes, receipt recovery or re-download from a
saved review's repair options; use **Recover the review** or delete it instead.

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
The service accepts only master-set submissions; earlier artifact v1/v2 imports stay
local. Strict clients and service DTOs need coordinated releases; unknown
versions/fields fail closed.
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
