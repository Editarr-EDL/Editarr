# Run Editarr for several users

This guide is for the operator of an Editarr installation that more than one
person uses. It covers the upgrade from a single-user release, accounts and
roles, personal API keys, filter policy, reviews and exports, the Jellyfin
plugin, backups, and what to do when the owner cannot sign in. The
[permission matrix](multi-user-permissions.md) lists the role defaults.

## Know the roles

Every request comes from one signed-in account or one personal API key. No
client address, reverse proxy, or deployment setting skips sign-in. Every
account comes from Jellyfin, and each person signs in with a Jellyfin password
or Quick Connect. Editarr has no local passwords.

- The `owner` has full access. Exactly one account is the owner. It signs in
  with Jellyfin, and Jellyfin sync cannot demote or disable it.
- An `admin` manages ordinary users, their media access, and most operations.
  An admin cannot manage another admin or the owner.
- A `user` browses the media it may see, starts scans, and keeps private
  reviews and personal exports.

Eight powers belong to the owner alone. No role default or individual override
can grant them. They are ownership transfer, managing the owner account, editing
role defaults, granting roles or permission overrides, revealing another
account's API key, changing connections or deployment settings, downloading
backups, and managing enforced filter policy.

Role defaults apply live. When the owner changes a role default under
**Settings > Users > Role permission defaults**, every account of that role
without an override for that capability follows the change immediately, including its
open sessions and its API key.

## Upgrade a single-user installation

The upgrade keeps every scan, review, export record, and community identity and
assigns them to the owner. It leaves the EDL and MCF files beside your media
unchanged, but the updated Jellyfin plugin reads only the new layout. Follow
these steps in order, so that players keep receiving EDL files throughout.

1. Stop Editarr. Press Ctrl+C in the terminal that runs `editarr`, or stop the
   service or container that starts it. Stop every Editarr process that uses this
   data directory or media root. Editarr is stopped when its address, for example
   `http://localhost:3348/`, refuses connections. Leave it stopped until step 5.

   Then install the new release's `editarr` command, but do not start it. Step 2
   needs a current build with the `backup` command. Install it without starting
   the server or opening the database.

   If you installed Editarr from a wheel, install the new wheel into a new virtual
   environment so you can still run the old version if recovery is needed.
   Create the new environment with the Python of the old one, for example with pip:

	```bash
	/opt/editarr/venv/bin/python -m venv /opt/editarr/venv-new
	/opt/editarr/venv-new/bin/pip install /absolute/path/editarr-<version>-py3-none-any.whl
	```

   Or with uv:

	```bash
	uv venv --python /opt/editarr/venv/bin/python /opt/editarr/venv-new
	uv pip install --python /opt/editarr/venv-new/bin/python /absolute/path/editarr-<version>-py3-none-any.whl
	```

   In that example, step 2 runs `/opt/editarr/venv-new/bin/editarr`. If you run
   Editarr from a source checkout, check out the new release's commit and run
   `uv sync` with the same `--extra` options you used before, because a plain
   `uv sync` removes the packages of every extra it is not given. Step 2 then runs
   `uv run editarr` from the checkout. Keep the application stopped through
   the backup and sidecar copy steps.

   Check that the new command is active before step 2. Run the `editarr` that
   step 2 runs, for example:

	```bash
	/opt/editarr/venv-new/bin/editarr backup --help
	/opt/editarr/venv-new/bin/editarr admin --help
	```

   Both commands must succeed, and the second must list `write-export`. No earlier
   release has that command. Neither command opens the database. If either check
   fails, the old release is still installed, so repeat the installation before
   you continue.
2. With every Editarr server still stopped, use the new release's `editarr` to
   take a full backup of the old data directory to a directory that only the
   account that runs Editarr uses:

	```bash
	editarr --data-dir /absolute/data/dir backup export /absolute/backups/before-upgrade.backup
	```

   The new command reads every supported single-user schema, including the
   earliest release's, which predates schema versioning. `migration inspect`
   reports that schema as `unversioned-v1`. The export reads a private copy of
   the database and never upgrades it, so the data directory keeps its old
   schema until step 5. Until then you can switch back to the old environment, or
   check out the old commit and run `uv sync` with the same options.
   The backup keeps the schema it was taken from. The new release restores only
   backups made at its own schema, so it cannot restore this one; keep it as an
   offline copy. To go back after step 5, use `migration recover-export` as
   described after these steps.

   `backup export` creates the parent directory if needed and removes group and
   other access from it (mode 0700), because a plain archive holds every
   credential and key. Never point it at a shared system directory such as
   `/private`, `/tmp`, or `/var`. Step 5 also copies the database into
   `migration-snapshots/` in the data directory and checks the copy's integrity
   and checksum before it changes anything.

3. Back up the sidecar files. The Editarr backup excludes the EDL and MCF files
   beside your media. This command archives every one of them. It follows
   symbolic links to folders and files. Replace `/media` with your media root
   here and in the copy script in step 4.

	```bash
	bash -s -- /media /absolute/backups/sidecars-before-upgrade.tar.gz <<'EOF'
	set -o pipefail
	if find -L "$1" -type l | grep .; then
		echo 'failed: the links above are broken' >&2
		exit 1
	fi
	find -L "$1" -type f \( -name '*.edl' -o -name '*.mcf' \) -print0 |
		tar --null -czhf "$2" --files-from=- && echo 'sidecar backup complete'
	EOF
	```

   The archive is complete only when the command prints
   `sidecar backup complete`. Otherwise the command printed each broken link, such
   as a link to a disk that is not mounted, or `find` or `tar` printed a folder or
   file it could not read. Mount the disk, delete a link that nothing uses, or fix
   the permission, and run the command again. One message is an exception. If
   the only error is `File system loop detected`, a link points to a folder that
   contains it, and the archive already holds those files through the folder
   itself.

4. While Editarr is still stopped, copy the legacy sidecars into the new layout
   with the script in
   [Copy legacy sidecars into the new layout](#copy-legacy-sidecars-into-the-new-layout).
   Keep Jellyfin and the old plugin running. The old plugin keeps serving the
   adjacent `Movie.edl`, which stays on disk.
5. Start the new release, or run `editarr --data-dir /absolute/data/dir migration upgrade`
   offline. For a wheel installation, change the service or command that started the
   old release to run the new environment's `editarr`, for example
   `/opt/editarr/venv-new/bin/editarr`. Before it changes the database, Editarr takes a recoverable snapshot
   and records it in `migration-upgrade.json`. An unknown or newer schema, or an
   unfinished upgrade journal, stops startup. Editarr never resets or silently
   stamps a database.
6. Sign in again. The upgrade from a released single-user version signs out
   every browser session. If the database had an administrator, that account is
   now the owner, with its API key and saved reviews. That account has no
   Jellyfin link, so migration `0030_remove_passwords` disables it, and nobody
   can sign in as that owner. To keep that owner, recover the pre-upgrade
   snapshot into a separate data directory and use a compatible previous
   version to link the owner to Jellyfin before upgrading. If the database
   had work but no administrator, Editarr asks you to connect Jellyfin and
   create the owner before anything else runs. The existing API key stops
   working until then. Setup assigns the existing work and that key to the owner.
   The old plugin does not see files that Editarr publishes into the new folders,
   so new publications reach players only after the next step.
7. Install the plugin build from the same release and restart Jellyfin.
   From then on, players receive the new-layout files, and nothing reads the
   adjacent legacy files.
8. Delete the legacy files after you confirm playback. **Manage exports** on a
   catalog item lists them.

If you install the new plugin before step 4, every player stops receiving EDL
files at once. No file is lost, and each item's EDL returns as soon as its file
is in the new layout. If you keep the old plugin after step 6, players keep the
legacy files, and new publications stay invisible until you install the new
plugin.

If the upgrade fails, leave the original files in place and export the prior
generation to a new data directory:

```bash
editarr --data-dir /absolute/data/dir migration recover-export /absolute/recovery/dir/scanner.db
editarr --data-dir /absolute/recovery/dir migration upgrade
```

Inspect the recovered copy before pointing the installation at it. Run
`editarr --data-dir /absolute/recovery/dir migration inspect` to check its
schema and recovery state.

## Copy legacy sidecars into the new layout

Before multi-user support, Editarr wrote `Movie.edl` and `Movie.mcf` beside the
video. The updated Jellyfin plugin never reads those files. New exports use
folders beside the video. For `/media/Movies/Movie/Movie.mkv`:

| Scope | EDL | MCF |
| --- | --- | --- |
| Personal | `edl/<id>/Movie.user-<id>.edl` | `mcf/<id>/Movie.user-<id>.mcf` |
| Item default | `edl/Movie.edl` | `mcf/Movie.mcf` |
| Legacy, never read | `Movie.edl` | `Movie.mcf` |

`<id>` is the Jellyfin user ID (32 lowercase hexadecimal digits) for an account
linked to Jellyfin, or `local-` plus 32 hexadecimal digits for an account without
a Jellyfin link.

Copy the files with the script below during step 4 of the upgrade, while
Editarr is stopped. Run it as the account that runs Editarr, which must own the
media folders or be able to write to them. Never run it as root or with `sudo`.
Each `edl/` or `mcf/` folder that the script creates belongs to the account that
runs it. If that is root, Editarr cannot write inside it later. Every personal
export and item default publication for that item then fails with the message
`Unable to update the export` (code `export_write_failed`), because the system
refuses the write with `Permission denied`.

Run the script with bash and pass your absolute media root in place of `/media`:

```bash
bash -s -- /media <<'EOF'
root=$1 status=0
has_video() {
	local file
	for file in "$1/$2".*; do
		case $file in *.edl | *.mcf) ;; *) [ -f "$file" ] && return 0 ;; esac
	done
	return 1
}
while IFS= read -r -d '' src; do
	if [[ $src != /* ]]; then
		printf 'failed: find printed an error for a folder under %q\n' "$root"
		status=1
		continue
	fi
	if [ ! -e "$src" ]; then
		printf 'not copied, broken link: %q\n' "$src"
		status=1
		continue
	fi
	dir=${src%/*} name=${src##*/}
	fmt=${name##*.} stem=${name%.*} parent=${dir##*/} up=${dir%/*}
	if [[ $parent =~ ^(local-)?[0-9a-f]{32}$ && ${up##*/} == "$fmt" ]]; then
		continue
	fi
	if [[ $parent == "$fmt" ]] && has_video "$up" "$stem"; then
		has_video "$dir" "$stem" || continue
		printf 'not copied, it is both a legacy file and an item default: %q\n' "$src"
		status=1
		continue
	fi
	if ! has_video "$dir" "$stem"; then
		printf 'not copied, no video with its name beside it: %q\n' "$src"
		status=1
		continue
	fi
	dest=$dir/$fmt/$name
	if ! err=$(mkdir -p -- "$dir/$fmt" 2>&1); then
		printf 'failed, cannot create the folder %q: %s\n' "$dir/$fmt" "${err##*: }"
		status=1
	elif ! err=$( (set -C; : >"$dest") 2>&1); then
		if [ ! -e "$dest" ] && [ ! -L "$dest" ]; then
			printf 'failed, cannot create %q: %s\n' "$dest" "${err##*: }"
			status=1
		elif cmp -s -- "$src" "$dest"; then
			printf 'already copied: %q\n' "$dest"
		else
			printf 'not copied, another file exists: %q\n' "$dest"
			status=1
		fi
	elif err=$(cp -p -- "$src" "$dest" 2>&1) && cmp -s -- "$src" "$dest"; then
		printf 'copied: %q\n' "$dest"
	else
		rm -f -- "$dest"
		printf 'failed, nothing copied: %q%s\n' "$src" "${err:+: ${err##*: }}"
		status=1
	fi
done < <(find -L "$root" \( -type l -o -type f \( -name '*.edl' -o -name '*.mcf' \) \) -print0 ||
	printf 'find failed\0')
exit "$status"
EOF
```

The script prints one line for each legacy file. It exits with status 0 only
when every legacy file is in the new layout. Otherwise it exits with status 1,
and each line that starts with `not copied` or `failed` names a file that still
needs your attention.

- It follows symbolic links to the media root, to folders, and to files, so it
  copies a file under `Shows -> /mnt/disk2/Shows` into the real folder on that
  disk. When two links reach one folder, it copies each file once and reports
  the second path as `already copied`.
- It never replaces a file. With `set -C`, bash creates each item default with
  an exclusive create, which fails if any file already has that name, even one
  that appeared a moment earlier. The script then reports that file and leaves it
  unchanged.
- It compares every copy with its original and removes a copy that differs.
- A legacy file needs a video with the same name in its own folder, such as
  `Movie.edl` beside `Movie.mkv`. The script treats `edl/Movie.edl` as an existing
  item default only when `Movie.mkv` is in the folder above it, so a media folder
  named `edl` or `mcf` is copied like any other. Files in a personal folder,
  `edl/<id>/`, are skipped.
- A rerun prints `already copied` for identical item defaults and copies the
  rest.

The copy and every later Editarr export write into the media folders. If the
media is mounted read-only, for example as a container volume with `:ro`, the
script prints `Read-only file system`, and Editarr cannot publish there either.
Mount the media read-write for the account that runs Editarr, then run the
script again.

A line that starts with `not copied` or `failed` repeats on every run until you
resolve it:

- `not copied, another file exists` means the item default differs from the
  legacy file. Players get the item default. If it is the version you want,
  delete the legacy file, because step 3 archived it. Otherwise delete the item
  default and run the script again.
- `not copied, no video with its name beside it` means the video was renamed,
  moved, or deleted. Rename or move the legacy file to sit beside its video
  with the same name, or delete it if the video is gone.
- `not copied, it is both a legacy file and an item default` names a file in a
  media folder called `edl` or `mcf` that has a video with its name beside it and
  another in the folder above. The script cannot tell which video it belongs
  to, so the line stays and the run exits 1. If it belongs to the video above, it
  is already that video's item default, and the line needs no action. If it
  belongs to the video beside it, copy it by hand into the `edl/` or `mcf/`
  folder beside that video.
- `not copied, broken link` names a link whose target is missing. Mount the disk
  it points to and run the script again, or delete the link if nothing uses it.
- `failed, cannot create` ends with the reason. `Permission denied` means the
  account cannot write to that folder. `Read-only file system` is covered above.
  `File name too long` means the folder path is too long for `edl/` or `mcf/`.
  Shorten the folder or file name.
- `failed: find printed an error` follows the message from `find`. Fix the
  permission it names and run the script again. If the only message is
  `File system loop detected`, a link points to a folder that contains it, and
  the script already reached those files through the folder itself.

To check ownership afterwards, run this as the same account. It prints every
`edl/` or `mcf/` folder that another account owns:

```bash
find -L /media -type d \( -name edl -o -name mcf \) ! -user "$(id -un)" -print
```

Change the owner of each folder it prints to the account that runs Editarr, for
example with `chown -R`, which needs root.

The plugin serves a copied file, but Editarr does not track it. A later Editarr
publication of that item default refuses with `export_file_collision` until you
delete the copy.

For an item default that Editarr tracks, publish it again from a saved review
after step 6. Delete the copied item default first. Open the review, choose
**Export**, pick **Item default (shared fallback)** under **Destination**, and
publish EDL or MCF. Editarr then lists the file under **Published files**.
Editarr has no bulk republish, so this works one item at a time.

**Manage exports** on a catalog item lists the remaining legacy files. Admins
and the owner may delete them there.

## Add people

Open **Settings > Users**. Admins and the owner see the directory.

- A Jellyfin user's first sign-in, by password or Quick Connect, creates that
  person's account.
- **Import Jellyfin users** creates accounts for selected or all Jellyfin users
  before they sign in.

Editarr has no local accounts, registration, or temporary passwords.

Jellyfin administrators start as Editarr admins. Editarr rechecks linked users at
sign-in, every five minutes, and on a manual sync. If Jellyfin disables or
deletes a user, Editarr disables the linked account and ends its sessions and
API access. During a Jellyfin outage, linked users keep their last verified
access for 24 hours. After that they keep profile access but lose media access
until Jellyfin answers again. An Editarr restart during an outage ends that
grace early.

To delete an account, choose to retain, transfer, or permanently delete its
reviews and exports after reviewing the impact. Disabling an account keeps all
of its data. Deleting an account never retracts its community publications.

### Re-enable an account after the upgrade

Migration `0030_remove_passwords` disables every account that has no Jellyfin
link. **Users** shows such an account as **Disabled: no Jellyfin link**. Its
reviews, exports, and API key stay. Its old username becomes its display name
unless it already had one, so a Jellyfin user can use that name. To enable it again:

1. Open the account in **Settings > Users**.
2. Choose **Link to Jellyfin**.
3. Enter the person's **Jellyfin username** and **Jellyfin password**, then
   your own Jellyfin password, and choose **Review impact**.
4. Check the preview. Its **Status** row shows that the account becomes active
   and that its existing API key works again.
5. Choose **Confirm link**.

The person then signs in with Jellyfin and sees their old reviews. Any API key
the account had before the upgrade works again as soon as the link enables it. Linking
moves personal exports to the Jellyfin ID after the new files are written. Editarr
refuses to enable such an account without a link. An account that an admin
disabled stays disabled after linking. Editarr has no unlink action.

## Manage personal API keys

Each account has at most one API key. It carries that account's current
permissions and never expires. Find it under **My Profile** (open your avatar
at the top of the sidebar), in the **API Key** section. Showing, creating, or
replacing the key asks for your Jellyfin password.

The owner may reveal or replace any account's key from that account's details.
An admin may revoke an ordinary user's key but cannot reveal it.

A key cannot perform sensitive actions. Key reveal and rotation, permission
changes, account linking, account deletion, and ownership transfer need an
interactive browser session and the account's Jellyfin password. A key
gets `403 interactive_session_required` for them.

## Set filter policy

Settings > Filters has two tabs. **My filters** holds each person's own
filters. **Server defaults** is for admins and sets the defaults everyone starts
from. A tab bar appears only for accounts that can use both.

Each user has a filter mode. **Keep me in sync** follows the server defaults,
and anything the person changes stays theirs. **Use my own copy** takes a
one-time copy of the defaults that later server changes don't touch. Users pick
their mode on My filters unless an admin or the owner assigns one. While an
account has required filters, its mode is managed for it and the choice is shown
but disabled. On Server defaults, **For new accounts** sets the mode new
accounts start in. It doesn't change existing accounts.

Both tabs show severity as a heat map of every category. Click a category to
change its level. On My filters an outlined chip is one you changed, a lock marks
one an admin requires, and **Reset to server default** puts back the server's
level. Selection rules are checked top to bottom. **Select** marks matching
filters to cut, **Keep** leaves them in, and Select wins when both match. On My
filters you can add your own rules, edit or turn off a server rule, and use
**Use server's rule** to drop your change to it.

The owner may enforce individual selection rules and subcategory severities for
an account. Enforcement sets a minimum. A user may filter more than the owner
requires, never less. Enforced policy applies to review editing, imports, and
exports, and Editarr keeps the underlying review content unchanged.

When enforced policy changes, Editarr regenerates the affected personal exports.
The old file keeps serving until the new one is written. If a file cannot be
regenerated, Editarr withdraws it and records why, and the plugin falls back to
the item default if one exists. Item defaults are outside this guarantee.

## Work with reviews and exports

Saved reviews are private to their creator, admins, and the owner. Completed
scans are shared evidence that everyone with access to the media may reuse.
Ordinary users cannot share a review with each other.

Users publish, download, and delete their own personal EDL and MCF files.
Admins may also publish item defaults, publish another user's review to that
user or to themselves, and delete other users' personal files. Publishing a
different review over an existing personal file asks for confirmation. Deleting
a review asks whether to keep or remove each published file you may see.

Ordinary users upload EDL or MCF files. Only admins and the owner import from a
path on the Editarr host.

## Install the Jellyfin plugin

The public [jellyfin-edl](https://github.com/Editarr-EDL/jellyfin-edl) plugin
serves each signed-in Jellyfin user that user's personal file, or the item
default when the user has none. A server API key gets only the default. An
invalid or unreadable personal file is an error, not a fallback.

Install [jellyfin-edl v1.0.0](https://github.com/Editarr-EDL/jellyfin-edl/releases/tag/v1.0.0)
for Jellyfin 12.0.0. For an existing installation, follow the order in
[Upgrade a single-user installation](#upgrade-a-single-user-installation).
Both applications use the `editarr-export-layout-1` export layout. Shared
compatibility cases are in
[`tests/fixtures/multi-user/export-layout-v1/`](../tests/fixtures/multi-user/export-layout-v1/).
Older plugin builds that only read the legacy adjacent EDL cannot read personal
exports or item defaults in the new layout.

## Back up and restore

Admins create full backups under **Settings > General > Backup and restore**.
Only the owner downloads them, because a plain archive holds every account's
credentials and keys. Only full backups exist. A schedule saved by an older
version for settings-only backups now makes full backups.

Restore works only into an empty installation. Choose **Restore from backup** on
the first-run screen of a new installation, or run `editarr backup restore` with
an empty data directory. A configured installation has no restore controls.
To recover a broken installation, stop Editarr, start it with a new empty data
directory, and restore there. A restore signs out every browser session, keeps
every API key, and keeps the new installation's base URL and listen port. The
[CLI backup guide](../README.md#portable-backups-from-the-cli) lists the commands.

## When the owner cannot sign in

Editarr has no lockout recovery. The owner signs in only with Jellyfin, and the
`editarr admin reset-password` and `admin rename` commands are gone. Link the
owner to Jellyfin before upgrading. If the upgrade disabled an owner without a
Jellyfin link, no backup helps, because this release restores only backups made
at its own schema. Reinstall, or recover the pre-upgrade snapshot with the
previous compatible release. Export the pre-upgrade copy to a separate location:

```bash
editarr --data-dir /absolute/data/dir migration recover-export /absolute/recovery/dir/scanner.db
```

Restoring a backup helps when Editarr's data is lost. It does not help when the
Jellyfin server is gone. A restored owner stays linked to the old server, and
only the owner can change the Jellyfin server. If Jellyfin is down for a while,
wait for it to return. If the Jellyfin server is permanently gone, nobody can
sign in to Editarr. Reinstall Editarr with a new, empty data directory.

## Known limitations

- Queue IDs are one global sequence, and scan numbers count per media item. Gaps
  in the numbers a user sees can reveal how many jobs other users created. The
  jobs' content, owners, titles, paths, progress, errors, and queue positions
  stay hidden.
- A failed export regeneration has no retry button. Publish the review again.
- Audit history credits the owner for work done by an upgrade, a restore, or an
  owner-run CLI command.
- Deleting a legacy sidecar whose path no longer resolves answers 500 for that
  item.
- A missing poster answers 404 without a `not_found` code.
- On Windows and on Linux Arm32, ppc64le, s390x, and FreeBSD, the plugin checks
  an export file and then opens it. Someone with write access inside the library
  folder can swap in a symbolic link between the two steps.
- Editarr was verified on macOS arm64 and Linux containers. Native Windows, CIFS,
  SMB, and FAT media storage, and a live community service, are not verified.
- Excluded from this release: notifications, translations, per-user quotas,
  review sharing between ordinary users, multiple or scoped API keys, automatic
  account merges, unlinking, a personal list of active sessions, and changes to
  the community service's operator dashboard.
