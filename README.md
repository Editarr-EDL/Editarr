# Editarr

[Editarr](https://editarr.org) is a self-hosted companion to [Jellyfin](https://jellyfin.org) that helps
you skip or mute objectionable content in your movies and shows. It scans a
video's audio and picture with AI models that run on your own hardware, suggests
time ranges to filter, and lets you review and refine them. It then publishes
[Simple EDL](https://kodi.wiki/view/Edit_decision_list) or [Movie Content Filter (MCF)](https://github.com/delight-im/MovieContentFilter) sidecar files that Jellyfin can serve
to clients—such as via the [jellyfin-edl](https://github.com/Editarr-EDL/jellyfin-edl)
plugin—to use during playback. Editarr never modifies your media files.

## Features

- **Jellyfin sign-in.** Everyone signs in with their Jellyfin account, by
  password or Quick Connect. Each person sees only the libraries Jellyfin lets
  them see.
- **Local AI scans.** Audio scans transcribe speech and flag profanity, and the
  optional **Qwen (multilingual)** filter finds profanity and blasphemy in any
  language. Visual scans flag unsafe imagery. **Credits** scans find where the
  closing credits start. Scans run in a queue with progress and cancellation,
  and resume after a restart.
- **Reviews.** Combine scan results into named saved reviews. Preview each cue,
  choose what to filter, add your own mute or skip filters, and adjust cue
  times. Filters use the MCF categories, subcategories, and severities.
- **Filter preferences.** Each account sets which categories and severities it
  filters by default to help speed up the review process. The owner account can require
  a minimum level of filtering for other accounts.
- **Import and export.** Import existing EDL, MCF, or VideoSkip files. Download
  `.skp` skip files or publish `.edl` and `.mcf` sidecars next to the media.
- **Several users.** Owner, admin, and user roles. Reviews and exports are
  private to each person, and each account has its own API key.
- **Community sharing (optional).** Share scans and reviews with the Editarr
  community, and use **Find Filters** in an item's **Community** tab to reuse
  filters others have made.
- **API.** Every feature is available through a documented REST API.

## Requirements

- A Jellyfin server and a Jellyfin administrator account
- Read access to your media from the Editarr host, plus write access to publish
  sidecar files

## Install and run

The native macOS app is available for Apple Silicon on macOS 26.2 or later.
Download the disk image, drag Editarr to Applications, and follow the local
signing steps in the release notes before opening it. [GitHub releases](https://github.com/Editarr-EDL/Editarr/releases)

### Build from source for development

You can build a Python wheel from a checkout with Python 3.11 or newer,
SQLite 3.35 or newer, [uv](https://docs.astral.sh/uv/), Node.js, and pnpm:

```bash
git clone https://github.com/Editarr-EDL/Editarr.git
cd Editarr
uv run python scripts/build_release.py --wheel
uv venv /opt/editarr/venv --python 3.11
uv pip install --python /opt/editarr/venv/bin/python dist/editarr-*.whl
/opt/editarr/venv/bin/editarr
```

Open `http://localhost:3348`. See the [release build guide](docs/release-build.md)
for details.

### Apple Speech transcription

On Apple Silicon with macOS 26 or later, choose **Apple Speech** under
**New Scan > Transcriber**. Speech recognition runs on the Mac. On first use,
macOS may download the speech assets for the selected audio track's language
if the account starting the scan has permission to download models.
Editarr uses US English for untagged tracks. Set the server environment variable
`EDITARR_APPLE_SPEECH_LOCALE` to a locale such as `es-ES` to override the track language.
Source and wheel installations need Xcode or Command Line Tools with the macOS 26 SDK
or later to compile the helper on first use. The macOS app includes the helper.

### Network access

The commands below apply to source and wheel installations. The macOS app
starts from its menu bar and uses the same deployment settings in Editarr.

- **This computer only:** run `editarr` (it listens on `127.0.0.1:3348`).
- **Your home network:** run `editarr --host 0.0.0.0` and open
  `http://<server-ip>:3348`. Traffic is not encrypted, so use this only on a
  trusted network.
- **The internet:** run Editarr on `127.0.0.1` behind an HTTPS reverse proxy
  and set its public address:

  ```bash
  EDITARR_BASE_URL=https://editarr.example.com editarr
  ```

  You can also set the Base URL later in **Settings > General**. See the
  [Nginx and Caddy examples](docs/examples/reverse-proxy/README.md).

To use a different port, start it with `editarr --port <port>`, or save a port
under **Settings > General**. Run `editarr --help` for all options and `editarr paths` to see where Editarr
stores its data, logs, and model cache.

## Getting started

1. **Connect Jellyfin.** On first launch, enter your Jellyfin server address and
   sign in as a Jellyfin administrator. That account becomes the Editarr owner.
2. **Map paths (if needed).** If Jellyfin and Editarr see your media at different
   paths, add a path mapping under **Settings > General** and test it.
3. **Scan.** Open a movie or episode from **Jellyfin Libraries** and choose
   **New Scan**. Pick audio, visual, and/or credits scans. The first scan may
   download model weights. Manage them under **Settings > Models > Storage**.
4. **Review.** Start a review and include the finished scans. Use **Preview**
   to check filter timing in the draft. Add manual filters or use **Apply Filter
   Defaults** to apply your preferences, then save the review.
5. **Export.** Publish EDL or MCF beside your media, or choose **VideoSkip (.skp)**
   in the saved review’s **Publish** step to download a skip file.
6. **Watch.** Install the [jellyfin-edl](https://github.com/Editarr-EDL/jellyfin-edl)
   plugin on your Jellyfin server. A compatible player can then request the user's
   own exported filters, or the default filters when they have none.

You can also skip scanning and create filters by hand, or **Import** an existing
EDL, MCF, or VideoSkip file. [Editarr MCF 1.1.1 specification](docs/mcf-1.1.1.md) ·
[VideoSkip import, export, and local seed datasets](docs/videoskip-integration.md).

## Users and roles

Anyone with a Jellyfin account can sign in. Their Editarr account is created the
first time they do, or an admin can add Jellyfin users ahead of time from
**Settings > Users**. Jellyfin administrators start as Editarr admins.

| Role | Can |
| --- | --- |
| Owner | Everything, including connections, backups, and enforced filter policy |
| Admin | Manage users and their media access, publish default exports |
| User | Browse allowed media, scan, keep private reviews, publish personal exports |

When Jellyfin disables or deletes a user, Editarr disables that account too. See
[Run Editarr for several users](docs/multi-user-guide.md) for permissions, filter
policy, and the Jellyfin plugin.

## API

Create a personal API key under **My Profile** (click your avatar in the
sidebar) and send it in the `X-Api-Key` header. The key has the same permissions
as your account. Browse the endpoints at `/docs` on your installation, and see
the [API contract](docs/api-contract.md) for details.

## Backups

Admins and the owner can create full backups under **Settings > General >
Backup and restore**, including scheduled backups. A new installation can
restore a backup from its setup screen. The `editarr backup` command does the
same from the command line. See the [multi-user guide](docs/multi-user-guide.md#back-up-and-restore).

### Portable backups from the CLI

With Editarr stopped, export a full backup to a private destination. Add
`--encrypt` to protect the archive with a passphrase.

```sh
editarr --data-dir /path/to/editarr-data backup export /path/to/private/editarr.backup --encrypt
```

Inspect and restore the archive using an empty data directory. Editarr verifies
the archive and asks for confirmation before activation.

```sh
editarr --data-dir /path/to/empty-data backup inspect /path/to/private/editarr.backup
editarr --data-dir /path/to/empty-data backup restore /path/to/private/editarr.backup
```

For an interrupted restore, stop the application and run
`editarr --data-dir /path/to/restore-target backup recover`.

## Learn more

- [Run Editarr for several users](docs/multi-user-guide.md)
- [Community sharing](docs/community-user-guide.md)
- [Reverse proxy examples](docs/examples/reverse-proxy/README.md)
- [Model sources and licenses](docs/model-artifact-notices.md)
- [Model development and evaluation](docs/model-development.md)

## Development

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 20+, and Corepack.

```bash
uv sync --extra dev    # add --extra audio_macos for MLX Whisper transcription
corepack enable
pnpm --dir web install
uv run ./scripts/dev.sh dev
```

This starts the web UI at `http://localhost:5173` with a live-reloading API
behind it.

Run the checks:

```bash
uv run ruff check scanner tests
uv run pytest -q tests          # runs in parallel; add -n 0 to run serially
pnpm --dir web build
pnpm --dir web test
pnpm --dir web test:e2e
```

Database schema changes need a versioned, data-preserving migration with upgrade
tests. See [AGENTS.md](AGENTS.md).

Editarr is licensed under the [GNU General Public License v3.0](LICENSE).
