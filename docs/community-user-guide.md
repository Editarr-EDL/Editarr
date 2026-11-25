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

Community filters have their own **Community** tab on the item page, left of
**Scans**. Select the physical media version and choose **Find Filters** in that
tab. It searches the community service for versions of this file (the first
search uses Theatrical); use the version picker to search a different version, or
choose **Other** and an optional name. Accounts without community access see an
explanation instead of the controls.

Finding local scans is separate: the **Scans** tab's **Recover Scans** matches
scans of this item's other versions and other local files and associates the ones
you choose. It is enabled when this item's other versions, its previous versions,
pending imports or linked scans have scans, or when another file of the same movie or
episode has a scan you can see; otherwise it is disabled with an explanation.
That check is quick and doesn't read your files, so a search can still come back
with "No recoverable scans matched this file."

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
**Preview alignment**, then **Approve alignment**. A set can't be included in a
review until its alignment is approved.

## Browse the community filters

The **Community** tab lists each linked set's filters with their time, category,
the word or phrase of a language filter, severity, action, confidence and
contributor count. It is for browsing, voting and
checking for updates; you enable, disable and adjust filters in the saved reviews
that include them. Notification links from **Filter updates** open this tab.

- Filters with a **Locally verified** check icon (hover or focus it for the
  tooltip) overlap a finding of the same category from a completed scan of this
  version.
- A filter is **Low Confidence** when its negative signals outweigh its support
  (attestations plus upvotes). Negatives come from downvotes, disputes, and other
  contributors' shared submissions that scanned that channel (audio or video) over
  that range without finding the filter; by default each of those counts as a
  quarter of a downvote. Your local scans never count toward anything unless you
  share them.
- **Minimum confidence** and **Show Low Confidence filters** decide which filters
  start enabled when a review includes the set or brings in new filters with
  Refresh: filters below your threshold, and Low Confidence filters unless you show
  them, are still added to the review but start disabled. The list here hides them
  the same way. Both preferences belong to your account; changing them later
  doesn't change reviews that already include the set.
- **Hide downvoted filters** (off by default) removes filters you voted down from
  the list. It only hides them. A filter you vote down while it is on stays listed
  until you change a list setting or reopen the set. The setting belongs to your
  account.
- Each filter has a vote button that shows your vote at a glance: a dashed
  **Vote ▾** when you haven't voted, a green 👍 or a red 👎 when you have. Select
  it (or press the down arrow on it) to open 👍 **Thumbs up**, 👎 **Thumbs down**
  and ‒ **No vote**, which clears your vote; arrow keys move between them and
  Escape closes the menu. Votes are sent right away and require a current
  community connection and your downloaded copy of the set; you cannot vote on
  your own filters. A vote that could not be sent stays pending with
  **Retry vote**, which resends the same saved intent. You can also vote from a
  saved review (below).

## Use community filters in a saved review

Linking a set never changes a saved review. In the **Review** tab, the Filters
toolbar's **Sources** button opens **Included scans**, which lists each scan and a
**Community ·** entry for each set linked to this version. Check it and save the
review to add **all** of the set's filters to the review's
results, next to the scan filters. Each shows its community source, confidence,
contributor count and version; filters below your minimum confidence, and Low
Confidence filters unless you show them, start disabled.

From then on community filters are ordinary review filters. A community language
filter's label is the word or phrase the community flagged. Enable or disable them,
edit their times, category, severity, action or label, preview or delete them
(**Edit** and **Delete** are in each row's **⋯** menu) exactly like scan filters; an **Edited** label marks the ones you changed yourself.
Turning filters on or off, one at a time or a whole group at once, is your personal
choice for this review: it never counts as an edit and is never shared. Changes
made by **Apply Filter Defaults** don't count as your edits either. Once you edit a
community filter it stays marked Edited, even if you later set it back to its
original values, and sharing the review sends it as your verdict while it is
enabled. Your instance's filter
policy applies to them like any other filter, and EDL and MCF exports, published
sidecars and filtered subtitles use them the same way.

Later changes to the set don't touch the review. **Check for filter updates** (or
the daily check) downloads the newest filters for the set; the **Sources** button
then shows a dot, and the set's entry shows **Community filters changed since
included** with **Refresh community filters**. Refreshing and saving brings the changes in as a new review revision, so
earlier exports show as out of date:

- filters you edited keep your changes, even when the community merged them into
  another filter;
- filters you didn't edit take the new community values, including a new word or
  phrase as their label (and including ones only Apply Filter Defaults changed),
  and stay on or off as you left them;
- new filters are added, enabled or disabled by your current minimum confidence and
  Low Confidence setting;
- filters the community no longer serves stay in the review marked **Removed from
  community**, disabled unless you had edited them;
- filters you deleted from the review stay deleted;
- a filter that was left out because it lay outside your file is added once the
  community moves it inside.

Reviews made before this version keep their community filters. Filters you adjusted
in the Community tab then become edits of those reviews' filters. A filter you
rejected appears disabled, and one you confirmed below your threshold stays
enabled; neither is marked Edited, since turning a filter on or off isn't an edit.
Decisions on sets no review included no longer apply.

### Vote from a review

Each community filter in the review's results has the same vote button as the
Community tab, beside **Preview**, so you can vote after previewing or aligning
it. Scan and manual filters have none. Votes are sent right away to the set the
filter came from: they don't change the review, aren't part of **Save Changes**,
and show in the Community tab too. A filter whose community ID was merged into
another votes on the filter that continues it. The button is shown unavailable,
with the reason in its tooltip, when your account can't vote, when the set the
review included was deleted, replaced or belongs to another account, or when the
community no longer serves the filter (it still shows the vote you last gave it).
If your votes couldn't be loaded, use **Retry loading votes** on the set's entry
under **Sources**.

Unchecking the entry and saving removes the set and its filters from the review. A
set that still needs alignment can't be included or refreshed until you approve its
alignment. If the set is later deleted or replaced, the review keeps its filters but
can't refresh them. Duplicating a review copies its community filters.

A review can contain only community filters: with no scans included, it still
exports and publishes EDL and MCF files beside the media file. A review needs at
least one scan or community filter to export.

Filtered subtitles follow one rule for every filter: an enabled language mute on
the audio channel removes the word or phrase in its label. A community language
mute's label is the community's word or phrase, so it filters subtitles like a
scan filter; one without a word doesn't change subtitles unless you give it a
label, exactly like any other filter without a word.

## Share your filters

Use **Share** on a completed scan or saved review. Choose the version your file
represents with the version picker (Theatrical, another listed version, or
**Other** with an optional name); no preview is prepared until a version is
chosen. The work needs a TMDB or TVDB ID, plus season and episode for TV.

The preview shows the exact public payload and an approximate summary such as
"≈4 new, ≈27 confirmations, 3 adjustments". The numbers are estimates
because the service makes the final match. Your cues and coverage are shared;
coverage tells the service which channels (audio or video) and time ranges your
scan analyzed, so community filters there that your scan did not find count
against them. A language cue shares the word or phrase in its label (up to 64
characters), without saying who flagged it. When you share a review, the enabled
community filters you edited are shared as your verdicts on them, confirming each
with your times, category, severity, action and word or phrase (an adjustment when
you changed them). Disabled community filters are never shared, whether or not you
edited them: turning a filter off is personal and doesn't dispute it. To tell the
community a filter is wrong, vote it down. Community filters you didn't edit,
filters from another community version than the set you share against, and ones
the community no longer serves aren't shared either. Private file paths, credentials and alternate-track
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

## Daily update checks

Editarr checks your linked versions for newer master-set revisions at most once a
day when you open the web app. The sidebar's **Filter updates** entry shows
unread notifications for changed, merged or unavailable versions and links to each
item's Community tab; use **Mark all as read** to clear them. On an item page,
**Check for filter updates** checks that set immediately without using the daily
allowance.

A newer revision is downloaded and shown in the Community tab at once; there is
nothing to review there. Saved reviews keep their filters until you choose
**Refresh community filters** in the review (see above). A set aligned to your
file keeps its alignment for new revisions.

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
